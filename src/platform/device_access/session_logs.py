"""One private object-storage text log per SSH connection, projected from audit."""

import hashlib
import re
import unicodedata
from datetime import timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import aliased

from src.infrastructure.db.models import AuditLogRecord

from .contracts import DeviceAccessError

START_KINDS = frozenset({"connecting", "opening"})
CLOSE_KINDS = frozenset({"disconnected", "closed", "terminal_closed"})
CONNECTION_KINDS = (
    START_KINDS
    | CLOSE_KINDS
    | frozenset(
        {
            "connected",
            "opened",
            "connection_failed",
            "open_failed",
            "command_requested",
            "command_finished",
            "command_denied",
            "command_not_executed",
            "completion_requested",
            "completion_finished",
            "terminal_requested",
            "terminal_resized",
            "terminal_input_rejected",
            "batch_device_finished",
            "host_key_observed",
            "host_key_changed",
            "host_key_rejected",
            "host_key_accepted",
            "host_key_restored",
        }
    )
)
PREFIX = "platform/ssh-session-logs/"
BEIJING = timezone(timedelta(hours=8))


def is_connection_event(kind, detail):
    return kind in CONNECTION_KINDS and bool(detail.get("session_id"))


def connection_condition():
    start = aliased(AuditLogRecord)
    anchor = (
        select(start.id)
        .where(
            start.resource_type == "ssh",
            start.action.in_(["ssh." + kind for kind in START_KINDS]),
            start.detail["session_id"].as_string()
            == AuditLogRecord.detail["session_id"].as_string(),
            start.user_id == AuditLogRecord.user_id,
        )
        .exists()
    )
    return AuditLogRecord.action.in_(["ssh." + kind for kind in CONNECTION_KINDS]) & anchor


def _storage():
    # Keep importing the configured client inside the execution path. Tests
    # inject an in-memory store and never initialize a real MinIO connection.
    from src.infrastructure.storage.minio_client import get_minio_storage

    return get_minio_storage()


def _lock(session, identifier):
    if session.get_bind().dialect.name != "postgresql":
        raise DeviceAccessError("audit_unavailable", "SSH 会话日志需要数据库写锁")
    key = int.from_bytes(hashlib.sha256(identifier.encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _conditions(identifier=None, actor_id=None):
    result = [
        AuditLogRecord.resource_type == "ssh",
        AuditLogRecord.action.in_(["ssh." + kind for kind in CONNECTION_KINDS]),
        AuditLogRecord.detail["session_id"].as_string().is_not(None),
    ]
    if identifier is not None:
        result.append(AuditLogRecord.detail["session_id"].as_string() == identifier)
    if actor_id is not None:
        result.append(AuditLogRecord.user_id == str(actor_id))
    return result


def _timestamp(session):
    column = AuditLogRecord.created_at
    if session.get_bind().dialect.name == "postgresql":
        column = column.op("AT TIME ZONE")(func.current_setting("TimeZone"))
    return column


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _records(session, identifiers, actor_id=None, *, include_output=False):
    # List views fetch only metadata fields, never the command-output JSON.
    columns = [
        AuditLogRecord.id,
        AuditLogRecord.user_id,
        AuditLogRecord.action,
        AuditLogRecord.status,
        _timestamp(session).label("observed_at"),
    ]
    keys = ("session_id", "device_name", "host", "port", "source_app")
    if include_output:
        columns.append(AuditLogRecord.detail)
    else:
        columns.extend(AuditLogRecord.detail[key].as_string().label(key) for key in keys)
    query = (
        select(*columns)
        .where(
            *_conditions(actor_id=actor_id),
            AuditLogRecord.detail["session_id"].as_string().in_(identifiers),
        )
        .order_by(AuditLogRecord.created_at, AuditLogRecord.id)
    )
    records = []
    for values in session.execute(query):
        identifier, actor, action, status, observed_at, *tail = values
        detail = tail[0] if include_output else dict(zip(keys, tail))
        records.append(
            {
                "id": identifier,
                "actor_id": actor,
                "kind": action.removeprefix("ssh."),
                "status": status,
                "observed_at": _aware(observed_at),
                "detail": detail,
            }
        )
    return records


def _safe_name(value):
    value = "".join("_" if unicodedata.category(c).startswith("C") else c for c in str(value))
    value = re.sub(r'[<>:"/\\|?*]', "_", value).strip(" .")[:100]
    return value or "SSH"


def _metadata(records):
    start = next((item for item in records if item["kind"] in START_KINDS), None)
    if start is None:
        return None
    # IDs are server-generated per connection. Refuse any corrupt cross-owner
    # collision instead of combining two users' transcripts in one object.
    if any(item["actor_id"] != start["actor_id"] for item in records):
        raise DeviceAccessError("audit_unavailable", "SSH 会话记录归属冲突")
    detail = start["detail"]
    device_name = str(detail.get("device_name") or detail.get("host") or "SSH")
    began = start["observed_at"]
    ended = any(item["kind"] in CLOSE_KINDS for item in records)
    failed = any(item["status"] == "failed" for item in records)
    filename = f"{_safe_name(device_name)}_{began.astimezone(BEIJING):%Y-%m-%d_%H-%M-%S.%f}.log"
    return {
        "id": str(detail["session_id"]),
        "filename": filename,
        "device_name": device_name,
        "host": detail.get("host"),
        "port": int(detail.get("port") or 22),
        "source_app": detail.get("source_app"),
        "actor_id": start["actor_id"],
        "started_at": began.isoformat(),
        "updated_at": records[-1]["observed_at"].isoformat(),
        "status": "failed" if failed else "closed" if ended else "active",
    }


def _content(metadata, records):
    lines = [
        f"SSH 会话: {metadata['id']}",
        f"设备: {metadata['device_name']}",
        f"地址: {metadata['host']}:{metadata['port']}",
        f"操作者: {metadata['actor_id']}    来源: {metadata['source_app']}",
        "时间: Asia/Shanghai (UTC+08:00)",
        "内容由已脱敏审计生成，包含已记录的命令及回显。",
        "",
    ]
    requested = set()
    finished = {
        record["detail"].get("command_id")
        for record in records
        if record["kind"] == "command_finished" and record["detail"].get("command_id")
    }
    labels = {
        "connecting": "开始连接",
        "opening": "开始连接",
        "connected": "连接成功",
        "opened": "连接成功",
        "connection_failed": "连接失败",
        "open_failed": "连接失败",
        "disconnected": "连接结束",
        "closed": "连接结束",
        "terminal_closed": "终端结束",
        "command_denied": "命令已拒绝",
        "command_not_executed": "命令未执行",
        "completion_requested": "请求 Tab 补全（未执行命令）",
        "completion_finished": "Tab 补全结束（未执行命令）",
        "terminal_resized": "终端尺寸变化",
        "terminal_requested": "请求终端",
        "host_key_observed": "记录主机密钥",
        "host_key_changed": "主机密钥变化",
        "host_key_rejected": "主机密钥被拒绝",
        "batch_device_finished": "批量设备结束",
        "host_key_accepted": "主机密钥已接受",
        "host_key_restored": "主机密钥已恢复",
        "terminal_input_rejected": "终端输入被拒绝",
    }
    for record in records:
        detail, kind = record["detail"], record["kind"]
        stamp = record["observed_at"].astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S.%f")
        command, command_id = detail.get("command"), detail.get("command_id")
        if kind == "command_requested":
            if command_id:
                requested.add(command_id)
            phase = "初始化命令" if detail.get("phase") in {"setup", "initialization"} else "命令"
            lines.append(f"[{stamp}] {phase}: {command}")
            if not command_id or command_id not in finished:
                lines.append("[尚未记录完成结果，是否已发送及执行结果未知]")
        elif kind == "command_finished":
            if not command_id or command_id not in requested:
                lines.append(f"[{stamp}] 命令: {command}")
            status = {
                "succeeded": "成功", "success": "成功", "failed": "失败",
                "not_executed": "未执行", "denied": "已拒绝",
            }.get(record["status"], record["status"])
            lines.append(f"[{stamp}] 执行结果: {status}")
            if detail.get("output"):
                lines.append(str(detail["output"]))
            if detail.get("output_truncated"):
                lines.append("[输出已截断：仅包含审计保留的内容]")
            if detail.get("output_complete") is False:
                lines.append("[命令输出不完整]")
        else:
            lines.append(f"[{stamp}] {labels.get(kind, kind)}")
            if command:
                lines.append(f"命令: {command}")
            if kind == "completion_finished":
                if detail.get("completed_command") is not None:
                    lines.append(f"补全结果: {detail['completed_command']}")
                if detail.get("cleared"):
                    lines.append("远端草稿已清空")
                elif detail.get("sent") is False:
                    lines.append("未向设备发送补全草稿")
                else:
                    lines.append("远端草稿未确认清空")
        if detail.get("failure_code"):
            lines.append(f"原因: {detail['failure_code']}")
        lines.append("")
    return "\n".join(lines)


def _key(metadata):
    identifier = hashlib.sha256(metadata["id"].encode()).hexdigest()
    return f"{PREFIX}{identifier}/{metadata['filename']}"


def _upload(storage, metadata, records):
    content = _content(metadata, records)
    if not storage.upload_file(
        _key(metadata), content.encode("utf-8"), "text/plain; charset=utf-8"
    ):
        raise DeviceAccessError("audit_unavailable", "SSH 会话日志保存失败，操作已停止")
    return content


def sync_log(session_id):
    """Called after audit commit. A fresh locked snapshot prevents stale overwrites."""
    from .governance import get_db_session

    try:
        with get_db_session() as session:
            _lock(session, session_id)
            records = _records(session, [session_id], include_output=True)
            metadata = _metadata(records)
            if metadata is not None:
                _upload(_storage(), metadata, records)
    except Exception:
        raise DeviceAccessError("audit_unavailable", "SSH 会话日志保存失败，操作已停止") from None


def list_logs(*, actor_id=None, host=None, limit=50, offset=0):
    from .governance import get_db_session

    limit, offset = max(1, min(100, int(limit))), max(0, int(offset))
    sid = AuditLogRecord.detail["session_id"].as_string()
    conditions = _conditions(actor_id=actor_id) + [
        AuditLogRecord.action.in_(["ssh." + kind for kind in START_KINDS]),
    ]
    if host:
        conditions.append(AuditLogRecord.detail["host"].as_string() == host)
    roots = (
        select(sid.label("sid"), func.min(AuditLogRecord.created_at).label("started"))
        .where(
            *conditions,
        )
        .group_by(sid)
        .subquery()
    )
    with get_db_session() as session:
        total = session.scalar(select(func.count()).select_from(roots))
        identifiers = list(
            session.scalars(
                select(roots.c.sid)
                .order_by(roots.c.started.desc(), roots.c.sid.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        records = _records(session, identifiers, actor_id) if identifiers else []
        groups = {identifier: [] for identifier in identifiers}
        for record in records:
            groups[record["detail"]["session_id"]].append(record)
        items = [_metadata(groups[identifier]) for identifier in identifiers]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def read_log(session_id, *, actor_id=None):
    from .governance import get_db_session

    with get_db_session() as session:
        _lock(session, session_id)
        records = _records(session, [session_id], actor_id, include_output=True)
        metadata = _metadata(records)
        if metadata is None:
            raise DeviceAccessError("session_log_not_found", "SSH 会话记录不存在")
        try:
            storage = _storage()
            raw = storage.download_file(_key(metadata))
            expected = _content(metadata, records)
            if raw is None or raw != expected.encode("utf-8"):
                # Restore this authorized historical/stale projection only;
                # listing never scans or writes all historical objects.
                content = _upload(storage, metadata, records)
            else:
                content = raw.decode("utf-8")
        except Exception:
            raise DeviceAccessError("audit_unavailable", "SSH 会话日志读取失败") from None
    return {**metadata, "content": content}
