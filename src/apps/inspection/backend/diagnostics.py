"""Read-only diagnosis contract shared by HTTP and Agent; no Workflow joins or collection."""

from datetime import datetime
from ipaddress import ip_address

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import or_, select

from src.infrastructure.db.models import CommandSnapshot, NetworkEventRecord, PatrolRun
from src.infrastructure.db.postgres import get_db_session
from src.platform.inventory.facts import search_devices
from src.platform.tools.registry import Tool


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(10, ge=1, le=20)
    offset: int = Field(0, ge=0, le=10000)


class Devices(Page):
    query: str = Field("", max_length=256)


class Scope(Page):
    device_id: str | None = Field(None, max_length=256)
    run_id: str | None = Field(None, max_length=128)
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.since and self.until and self.since > self.until:
            raise ValueError("since must not exceed until")
        return self


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(min_length=1, max_length=64)
    offset: int = Field(0, ge=0, le=1000000)
    limit: int = Field(2000, ge=1, le=4000)


class SnapshotScope(Scope):
    host: str | None = Field(None, max_length=64)

    @field_validator("host")
    @classmethod
    def literal_address(cls, value):
        return str(ip_address(value)) if value is not None else None


def iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def page(session, statement, body):
    rows = session.execute(statement.offset(body.offset).limit(body.limit + 1)).mappings().all()
    return {
        "items": [{key: iso(value) for key, value in row.items()} for row in rows[: body.limit]],
        "next_offset": body.offset + body.limit if len(rows) > body.limit else None,
    }


def window(statement, column, body):
    if body.since:
        statement = statement.where(column >= body.since)
    if body.until:
        statement = statement.where(column <= body.until)
    return statement


def devices(body):
    result = search_devices(body.query, limit=body.limit, offset=body.offset)
    result["items"] = [{k: iso(v) for k, v in row.items()} for row in result["items"]]
    return result


def runs(body):
    fields = (
        "id",
        "mode",
        "source",
        "status",
        "collection_status",
        "parser_readiness_status",
        "fact_readiness_status",
        "analysis_status",
        "baseline_eligible",
        "run_purpose",
        "started_at",
        "completed_at",
        "device_count",
        "command_count",
    )
    statement = select(*(getattr(PatrolRun, key) for key in fields))
    if body.device_id:
        statement = statement.where(
            select(CommandSnapshot.id)
            .where(
                CommandSnapshot.run_id == PatrolRun.id,
                or_(
                    CommandSnapshot.device_id == body.device_id,
                    CommandSnapshot.inventory_device_id == body.device_id,
                ),
            )
            .exists()
        )
    if body.run_id:
        statement = statement.where(PatrolRun.id == body.run_id)
    statement = window(statement, PatrolRun.started_at, body)
    with get_db_session() as session:
        result = page(session, statement.order_by(PatrolRun.started_at.desc(), PatrolRun.id), body)
    result["meaning"] = "采集、解析、事实与分析阶段分别判读；历史/离线记录不能代表当前健康状态"
    return result


def events(body):
    fields = (
        "id",
        "device_id",
        "run_id",
        "event_type",
        "severity",
        "entity_type",
        "entity_key",
        "status",
        "first_seen_at",
        "last_seen_at",
        "occurrence_count",
        "source_change_id",
    )
    statement = select(*(getattr(NetworkEventRecord, key) for key in fields))
    if body.device_id:
        statement = statement.where(NetworkEventRecord.device_id == body.device_id)
    if body.run_id:
        statement = statement.where(NetworkEventRecord.run_id == body.run_id)
    statement = window(statement, NetworkEventRecord.last_seen_at, body)
    with get_db_session() as session:
        result = page(
            session,
            statement.order_by(NetworkEventRecord.last_seen_at.desc(), NetworkEventRecord.id),
            body,
        )
    result["meaning"] = "持久事件记录；空结果不证明无故障，事件相关性不证明根因"
    return result


def snapshots(body):
    fields = (
        "id",
        "run_id",
        "inventory_device_id",
        "device_id",
        "device_name",
        "management_ip",
        "command",
        "observed_at",
        "parse_status",
        "output_status",
        "raw_text_hash",
        "snapshot_kind",
    )
    statement = select(*(getattr(CommandSnapshot, key) for key in fields))
    if body.device_id:
        statement = statement.where(
            or_(
                CommandSnapshot.device_id == body.device_id,
                CommandSnapshot.inventory_device_id == body.device_id,
            )
        )
    if body.run_id:
        statement = statement.where(CommandSnapshot.run_id == body.run_id)
    if getattr(body, "host", None):
        statement = statement.where(CommandSnapshot.management_ip == body.host)
    statement = window(statement, CommandSnapshot.observed_at, body)
    with get_db_session() as session:
        return page(
            session,
            statement.order_by(CommandSnapshot.observed_at.desc(), CommandSnapshot.id),
            body,
        )


def read_snapshot(body):
    with get_db_session() as session:
        row = session.get(CommandSnapshot, body.snapshot_id)
        if row is None:
            raise HTTPException(404, "采集证据不存在")
        text = row.raw_text or ""
        if body.offset > len(text):
            raise HTTPException(422, "分页位置超出证据范围")
        end = min(len(text), body.offset + body.limit)
        return {
            "snapshot_id": row.id,
            "run_id": row.run_id,
            "device_id": row.device_id,
            "command": row.command,
            "observed_at": iso(row.observed_at),
            "parse_status": row.parse_status,
            "output_status": row.output_status,
            "source_sha256": row.raw_text_hash,
            "offset": body.offset,
            "end_offset": end,
            "text": text[body.offset : end],
            "next_offset": end if end < len(text) else None,
            "meaning": "observed_at 那一刻采集的历史原文，不是设备现在的配置或状态；"
            "现在是否已变化要与现场对比；缺失或解析失败不能当作正常",
        }


CONTRACTS = [
    (
        "search_devices",
        "按名称、IP或设备ID查找资产；返回登记信息及最近连接检查时间",
        Devices,
        devices,
    ),
    (
        "list_runs",
        "分页查询持久巡检运行，可按设备、运行ID和起止时间过滤；返回各阶段质量状态",
        Scope,
        runs,
    ),
    (
        "list_events",
        "查询设备事件及首次/最近出现时间；相同时间窗便于关联，空结果不等于健康",
        Scope,
        events,
    ),
    (
        "list_snapshots",
        "按资产设备、精确管理IP或巡检运行查询命令证据目录，取得 snapshot_id 后按需读取",
        SnapshotScope,
        snapshots,
    ),
    (
        "read_snapshot",
        "按 snapshot_id 分页读取采集当时的历史原文、来源摘要、采集时间和解析质量；"
        "不是设备现在的状态",
        Snapshot,
        read_snapshot,
    ),
]
TOOLS = [
    Tool(name, description, model, lambda user, body, fn=fn: fn(body), evidence=True)
    for name, description, model, fn in CONTRACTS
]
router = APIRouter()


def endpoint(model, fn):
    def query(body):
        return fn(body)

    query.__annotations__["body"] = model
    return query


for name, description, model, fn in CONTRACTS:
    router.add_api_route(
        "/" + name,
        endpoint(model, fn),
        methods=["POST"],
        summary=description,
        name="diagnosis_" + name,
    )
