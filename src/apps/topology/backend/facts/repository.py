"""Server-only shared facts service with live scope checks and immutable evidence."""

import hashlib
import ipaddress
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Integer, String, func, select
from sqlalchemy.exc import IntegrityError

from .. import storage
from ..models import Network, Run, now
from ..repository import sessions as default_sessions
from .models import RECORD_MODELS, Device, FactSet

Capability = Literal[
    "version",
    "inventory",
    "lldp_local",
    "lldp",
    "interfaces",
    "ip",
    "arp",
    "mac",
    "routes",
    "vrfs",
    "vlan",
    "trunk",
    "switchport",
    "ospf_interfaces",
    "ospf_process",
    "lag",
    "irf",
    "mlag",
    "vxlan",
    "evpn",
]


class FactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: Capability
    observed_at: datetime
    status: Literal["complete", "partial", "failed", "unsupported"]
    source: Literal["discovery", "diagnosis", "refresh", "offline"]
    run_id: str | None = Field(default=None, max_length=32)
    platform_profile: str = Field(min_length=1, max_length=128)
    parser_ref: str = Field(min_length=1, max_length=512)
    evidence_ref: str = Field(min_length=1, max_length=512)
    records: list[dict] = Field(default_factory=list, max_length=100000)
    summary: dict = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observation time must include timezone")
        return value.astimezone(timezone.utc)


def values(row):
    result = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    # SQLite returns naive timestamps; database values are stored in UTC.
    return {
        key: (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()
        if isinstance(value, datetime)
        else value
        for key, value in result.items()
    }


def validate_records(body):
    model = RECORD_MODELS.get(body.capability)
    if body.status in {"failed", "unsupported"} and (body.records or body.summary):
        raise ValueError("failed capabilities cannot carry facts")
    if not model and body.records:
        raise ValueError("this capability uses summary facts")
    for record in body.records:
        for name in ("next_hops", "remote_mgmt_addresses"):
            if name in record and not isinstance(record[name], list):
                raise ValueError("invalid list fact field")
        columns = {
            c.name: c
            for c in model.__table__.columns
            if c.name not in {"id", "device_uid", "fact_set_id"}
        }
        if record.keys() - columns.keys():
            raise ValueError("unknown or reserved fact field")
        for name, column in columns.items():
            value = record.get(name)
            if value is None:
                if not column.nullable:
                    raise ValueError("required fact field missing")
            elif isinstance(column.type, Integer):
                if type(value) is not int or value < 0 or value > 2147483647:
                    raise ValueError("invalid numeric fact field")
            elif isinstance(column.type, String):
                if not isinstance(value, str) or (
                    column.type.length and len(value) > column.type.length
                ):
                    raise ValueError("invalid text fact field")
    # No NaN/non-JSON objects in either backend, including JSON summary capabilities.
    json.dumps([body.records, body.summary], allow_nan=False)


class FactRepository:
    def __init__(self, actor, *, sessions=None, evidence_reader=None):
        # Actor is authenticated by the App gateway/worker, never supplied by model tools.
        self.actor = actor
        self.sessions = sessions or default_sessions
        self.evidence_reader = evidence_reader or storage.get

    def _writer(self):
        if not self.actor.can_execute_skills():
            raise HTTPException(403, "当前账号不能提交采集事实")

    def _device(self, session, domain, uid):
        row = session.get(Device, uid)
        if row is None or row.domain != domain:
            raise HTTPException(404, "设备事实不存在")
        return row

    def create_device(self, domain, addresses, *, hostname=None):
        """Create a weak managed identity; P3 resolves/merges strong identities."""
        self._writer()
        if not isinstance(domain, str) or not domain or len(domain) > 64:
            raise ValueError("invalid domain")
        addresses = list(dict.fromkeys(str(ipaddress.ip_address(ip)) for ip in addresses))
        if not addresses or len(addresses) > 64:
            raise ValueError("managed device requires bounded management addresses")
        if hostname is not None and (not isinstance(hostname, str) or len(hostname) > 256):
            raise ValueError("invalid hostname")
        with self.sessions() as session:
            existing = session.scalar(
                select(Device).where(Device.domain == domain, Device.seed_address == addresses[0])
            )
            if existing is not None:
                return values(self._device(session, domain, existing.device_uid))
            row = Device(
                device_uid=uuid4().hex,
                domain=domain,
                seed_address=addresses[0],
                kind="managed",
                hostname=hostname,
                serials=[],
                chassis_macs=[],
                mgmt_addresses=addresses,
                role="unknown",
                identity_confidence="weak",
                created_by=self.actor.user_id,
                first_seen=now(),
                last_seen=now(),
            )
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(Device).where(
                        Device.domain == domain, Device.seed_address == addresses[0]
                    )
                )
                if existing is None:
                    raise
                return values(self._device(session, domain, existing.device_uid))
            return values(row)

    def get_device(self, domain, uid):
        with self.sessions() as session:
            return values(self._device(session, domain, uid))

    def save_evidence(self, domain, uid, outputs):
        self._writer()
        self.get_device(domain, uid)
        return storage.put(
            "facts", uid, uuid4().hex, {"domain": domain, "device_uid": uid, "outputs": outputs}
        )

    def _evidence(self, domain, uid, reference):
        if not reference.startswith(f"apps/topology/facts/{uid}/"):
            raise ValueError("evidence must belong to this device")
        evidence = self.evidence_reader(reference)
        if evidence.get("domain") != domain or evidence.get("device_uid") != uid:
            raise ValueError("evidence scope mismatch")
        digest = hashlib.sha256(
            json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        if reference.rsplit("/", 1)[-1] != digest + ".json":
            raise ValueError("evidence checksum mismatch")
        return digest

    def append(self, domain, uid, body: FactInput):
        self._writer()
        validate_records(body)
        # Check permission before reading potentially sensitive evidence.
        self.get_device(domain, uid)
        digest = self._evidence(domain, uid, body.evidence_ref)
        with self.sessions() as session:
            device = self._device(session, domain, uid)
            if body.run_id:
                run = session.get(Run, body.run_id)
                network = session.get(Network, run.network_id) if run else None
                if (
                    not run
                    or run.owner_id != self.actor.user_id
                    or not network
                    or network.domain != domain
                ):
                    raise HTTPException(404, "来源发现任务不存在")
            row = FactSet(
                id=uuid4().hex,
                device_uid=uid,
                **body.model_dump(exclude={"records"}),
                received_at=now(),
                evidence_sha256=digest,
                record_count=len(body.records),
                collected_by=self.actor.user_id,
            )
            session.add(row)
            session.flush()
            model = RECORD_MODELS.get(body.capability)
            for record in body.records:
                session.add(model(id=uuid4().hex, device_uid=uid, fact_set_id=row.id, **record))
            previous = device.last_seen
            previous = (
                previous.replace(tzinfo=timezone.utc) if previous.tzinfo is None else previous
            )
            device.last_seen = max(previous, body.observed_at)
            session.flush()
            return values(row)

    def _read(self, session, row):
        model = RECORD_MODELS.get(row.capability)
        records = (
            list(
                session.scalars(select(model).where(model.fact_set_id == row.id).order_by(model.id))
            )
            if model
            else []
        )
        return {"fact_set": values(row), "records": [values(record) for record in records]}

    def read_set(self, domain, uid, identifier):
        with self.sessions() as session:
            self._device(session, domain, uid)
            row = session.get(FactSet, identifier)
            if row is None or row.device_uid != uid:
                raise HTTPException(404, "事实集不存在")
            return self._read(session, row)

    def current(self, domain, uid, capability):
        with self.sessions() as session:
            self._device(session, domain, uid)
            ordering = (FactSet.observed_at.desc(), FactSet.received_at.desc(), FactSet.id.desc())
            ranked = (
                select(
                    FactSet.id,
                    func.row_number()
                    .over(partition_by=(FactSet.status == "complete"), order_by=ordering)
                    .label("rank"),
                )
                .where(FactSet.device_uid == uid, FactSet.capability == capability)
                .subquery()
            )
            # Both candidates are chosen in one statement, without a concurrent-read gap.
            rows = list(
                session.scalars(
                    select(FactSet)
                    .join(ranked, ranked.c.id == FactSet.id)
                    .where(ranked.c.rank == 1)
                    .order_by(*ordering)
                )
            )
            latest = rows[0] if rows else None
            complete = next((row for row in rows if row.status == "complete"), None)
            return {
                "current": self._read(session, complete) if complete else None,
                "latest_attempt": values(latest) if latest else None,
            }
