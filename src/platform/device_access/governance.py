"""Platform SSH policy and durable audit. Never depends on a business App."""

import hashlib
import re
import time
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

import regex
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from src.infrastructure.db.models import AuditLogRecord, SSHPolicyRecord
from src.infrastructure.db.postgres import get_db_session

from .contracts import DeviceAccessError

MAX_PATTERNS = 100
MAX_PATTERN_LENGTH = 512
MAX_COMMAND_LENGTH = 4096
MATCH_SECONDS = 0.05
MAX_OUTPUT_BYTES = 256 * 1024
_operation = ContextVar("ssh_operation", default=None)
_command = ContextVar("ssh_command", default=None)
_SECRET_KEY = re.compile(
    r"(?i)^(?:password|passwd|secret|secret_ciphertext|token|access_token|refresh_token|"
    r"credential_ref|private_key|authorization|api[_-]?key)$"
)
def redact(value, secrets=()):
    """Hide only the platform's own credentials: fields named as secrets and the
    literal secrets a session logged in with.

    Device output is never rewritten by keyword. Masking every line that mentioned
    password/cipher/authentication altered collected raw data before it was stored
    (owner's decision, 2026-10-02: collection stays raw). Control characters are
    still dropped: PostgreSQL cannot store NUL, which Comware sends before a prompt.
    """
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SECRET_KEY.search(str(key)) else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [redact(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]", "", value)


def _policy_view(row):
    return {
        "patterns": list(row.patterns),
        "version": row.version,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat(),
    }


def validate_patterns(patterns):
    if not isinstance(patterns, list) or len(patterns) > MAX_PATTERNS:
        raise ValueError("白名单最多 100 条正则表达式")
    for pattern in patterns:
        if not isinstance(pattern, str) or not 0 < len(pattern) <= MAX_PATTERN_LENGTH:
            raise ValueError("每条正则须为 1–512 个字符")
        try:
            regex.compile(pattern)
        except regex.error:
            raise ValueError("白名单正则表达式无效") from None
    return patterns


def get_policy():
    try:
        with get_db_session() as session:
            row = session.get(SSHPolicyRecord, 1)
            if row is None:
                raise RuntimeError("SSH policy migration missing")
            validate_patterns(row.patterns)
            return _policy_view(row)
    except Exception:
        raise DeviceAccessError("policy_unavailable", "SSH 白名单不可用，未发送命令") from None


def write_event(*, actor_id, source_app, kind, detail=None, status="success", session=None):
    from .session_logs import is_connection_event, sync_log

    identifier = "audit-" + uuid4().hex
    payload = redact(dict(detail or {}))
    payload["source_app"] = str(source_app)[:128]
    row = AuditLogRecord(
        id=identifier,
        user_id=str(actor_id) if actor_id is not None else None,
        action="ssh." + str(kind)[:59],
        resource_type="ssh",
        resource_id=str(payload.get("session_id") or payload.get("target_ref") or "")[:128],
        detail=payload,
        status=str(status)[:32],
        created_at=datetime.now(timezone.utc),
    )
    try:
        if session is not None:
            session.add(row)
            session.flush()
        else:
            with get_db_session() as target:
                target.add(row)
                target.flush()
    except Exception:
        raise DeviceAccessError("audit_unavailable", "SSH 审计写入失败，操作已停止") from None
    if is_connection_event(kind, payload):
        session_id = payload["session_id"]
        if session is None:
            sync_log(session_id)
        else:
            # The caller's transaction stays authoritative; a projection failure
            # after commit must not roll back or erase its durable audit rows.
            pending = session.info.setdefault("ssh_log_sessions", set())
            pending.add(session_id)
            if not session.info.get("ssh_log_hooks"):
                session.info["ssh_log_hooks"] = True

                def committed(target):
                    target.info["ssh_logs_committed"] = True

                def rolled_back(target):
                    target.info.pop("ssh_log_sessions", None)
                    target.info.pop("ssh_logs_committed", None)

                def ended(target, transaction):
                    if transaction.parent is None and target.info.pop("ssh_logs_committed", False):
                        identifiers = target.info.pop("ssh_log_sessions", set())
                        for identifier in identifiers:
                            sync_log(identifier)

                event.listen(session, "after_commit", committed)
                event.listen(session, "after_rollback", rolled_back)
                event.listen(session, "after_transaction_end", ended)
    return identifier


def save_policy(patterns, actor_id, expected_version=None, *, expected_revision=None):
    validate_patterns(patterns)
    expected = expected_version if expected_version is not None else expected_revision
    try:
        return _save_policy(patterns, actor_id, expected)
    except SQLAlchemyError:
        raise DeviceAccessError("policy_unavailable", "SSH 白名单保存失败") from None


def _save_policy(patterns, actor_id, expected):
    with get_db_session() as session:
        row = session.scalar(
            select(SSHPolicyRecord).where(SSHPolicyRecord.id == 1).with_for_update()
        )
        if row is None:
            raise DeviceAccessError("policy_unavailable", "SSH 白名单未初始化")
        if expected is not None and expected != row.version:
            raise DeviceAccessError("policy_conflict", "白名单已变化，请刷新后重试")
        previous = _policy_view(row)
        row.patterns, row.version = list(patterns), row.version + 1
        row.updated_by, row.updated_at = str(actor_id), datetime.now(timezone.utc)
        result = _policy_view(row)
        write_event(
            actor_id=actor_id,
            source_app="platform",
            kind="policy_changed",
            detail={"before": previous, "after": result},
            session=session,
        )
        return result


def query_audit(
    *,
    actor_id=None,
    session_id=None,
    task_id=None,
    host=None,
    kind=None,
    status=None,
    limit=50,
    offset=0,
    management_only=False,
):
    limit, offset = max(1, min(100, int(limit))), max(0, int(offset))
    conditions = [AuditLogRecord.resource_type == "ssh"]
    if management_only:
        from .session_logs import connection_condition

        conditions.append(~connection_condition())
    if actor_id is not None:
        conditions.append(AuditLogRecord.user_id == str(actor_id))
    if kind:
        conditions.append(AuditLogRecord.action == "ssh." + kind.removeprefix("ssh."))
    if status:
        conditions.append(AuditLogRecord.status == status)
    for key, value in (("session_id", session_id), ("task_id", task_id), ("host", host)):
        if value:
            conditions.append(AuditLogRecord.detail[key].as_string() == str(value))
    with get_db_session() as session:
        total = session.scalar(select(func.count()).select_from(AuditLogRecord).where(*conditions))
        created_at = AuditLogRecord.created_at
        if session.get_bind().dialect.name == "postgresql":
            # The shared audit column stores database-local timestamps without
            # a zone. Interpret them using the same zone used when writing.
            created_at = created_at.op("AT TIME ZONE")(func.current_setting("TimeZone"))
        rows = session.execute(
            select(AuditLogRecord, created_at.label("audit_created_at"))
            .where(*conditions)
            .order_by(
                AuditLogRecord.created_at.desc(),
                AuditLogRecord.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        items = [
            {
                "id": row.id,
                "actor_id": row.user_id,
                "user_id": row.user_id,
                "kind": row.action.removeprefix("ssh."),
                "action": row.action,
                "status": row.status,
                "created_at": (
                    audit_created_at.replace(tzinfo=timezone.utc)
                    if audit_created_at.tzinfo is None else audit_created_at
                ).isoformat(),
                "detail": row.detail,
            }
            for row, audit_created_at in rows
        ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def current_operation():
    return _operation.get()


class SSHOperation:
    def __init__(
        self,
        actor_id,
        source_app,
        target_ref,
        host,
        port=22,
        task_id=None,
        secrets=(),
        session_id=None,
        device_name=None,
    ):
        self.actor_id, self.source_app = str(actor_id), str(source_app)
        self.target_ref, self.host, self.port = str(target_ref), str(host), port
        self.task_id, self.session_id = task_id, session_id or uuid4().hex
        self.secrets = tuple(secret for secret in secrets if secret)
        self.device_name = str(device_name or host)
        self.connection_started = False

    @contextmanager
    def activate(self):
        token = _operation.set(self)
        try:
            yield self
        finally:
            _operation.reset(token)

    def redact(self, value):
        return redact(value, self.secrets)

    def event(self, kind, *, status="success", **detail):
        if kind in {"connecting", "opening"}:
            self.connection_started = True
        return write_event(
            actor_id=self.actor_id,
            source_app=self.source_app,
            kind=kind,
            status=status,
            detail=self.redact(
                {
                    "session_id": self.session_id,
                    "target_ref": self.target_ref,
                    "host": self.host,
                    "port": self.port,
                    "task_id": self.task_id,
                    "device_name": self.device_name,
                    **detail,
                }
            ),
        )

    def check_command(self, command, *, phase="command"):
        valid = isinstance(command, str) and 0 < len(command) <= MAX_COMMAND_LENGTH
        valid = (
            valid
            and bool(command.strip())
            and not any(
                unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in command
            )
        )
        if not valid:
            self.event(
                "command_denied",
                status="denied",
                command=str(command)[:4096],
                phase=phase,
                failure_code="invalid_command",
            )
            raise DeviceAccessError("invalid_command", "命令必须是无控制字符的完整单行")
        policy = get_policy()
        deadline = time.monotonic() + MATCH_SECONDS
        try:
            allowed = False
            for pattern in policy["patterns"]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                if regex.fullmatch(pattern, command, timeout=remaining):
                    allowed = True
                    break
        except (TimeoutError, regex.error):
            self.event(
                "command_denied",
                status="denied",
                command=command,
                phase=phase,
                policy_version=policy["version"],
                failure_code="policy_match_failed",
            )
            raise DeviceAccessError("policy_match_failed", "白名单匹配未完成，未发送命令") from None
        if not allowed:
            self.event(
                "command_denied",
                status="denied",
                command=command,
                phase=phase,
                policy_version=policy["version"],
                failure_code="policy_denied",
            )
            raise DeviceAccessError("policy_denied", "命令未匹配管理员 SSH 白名单")
        return policy["version"]

    def before_command(self, command, *, phase="command", command_id=None):
        version = self.check_command(command, phase=phase)
        ticket = {
            "command_id": command_id or uuid4().hex,
            "command": command,
            "phase": phase,
            "policy_version": version,
        }
        self.event("command_requested", **ticket)
        return ticket

    def after_command(self, ticket, *, output="", status="succeeded", failure_code=None, **detail):
        cleaned = self.redact(str(output or ""))
        raw = cleaned.encode("utf-8")
        return self.event(
            "command_finished",
            status=status,
            **ticket,
            **detail,
            output=raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore"),
            output_bytes=len(raw),
            output_truncated=len(raw) > MAX_OUTPUT_BYTES,
            output_sha256=hashlib.sha256(raw).hexdigest(),
            failure_code=failure_code,
        )

    def execute(self, command, callback, *, phase="command", command_id=None):
        from .errors import failure_code

        active = _command.get()
        if active and active[0] is self and active[1]["command"] == command:
            self.check_command(command, phase=active[1]["phase"])
            return callback()
        ticket = self.before_command(command, phase=phase, command_id=command_id)
        token = _command.set((self, ticket))
        try:
            output = callback()
        except Exception as exc:
            self.after_command(
                ticket,
                output=getattr(exc, "output", ""),
                status="failed",
                failure_code=failure_code(exc),
            )
            raise
        else:
            self.after_command(ticket, output=output)
            return output
        finally:
            _command.reset(token)


def operation_for(context, target, profile, *, session_id=None):
    current = current_operation()
    if (
        current is not None
        and current.host == target.host
        and current.port == profile.port
        and not current.connection_started
        and (session_id is None or session_id == current.session_id)
    ):
        return current
    return SSHOperation(
        context.actor_id,
        context.source_app,
        target.target_ref,
        target.host,
        profile.port,
        task_id=context.task_id,
        secrets=(profile.password,),
        session_id=session_id,
        device_name=target.display_name,
    )
