"""App-owned address grants; explicit import, no runtime file fallback."""

import hashlib
import ipaddress
import json
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from src.platform.device_access.contracts import DeviceAccessError

from .models import AddressGrant, GrantAudit, now
from .schemas import Schema

Action = Literal["device.collect", "device.terminal"]


class GrantCreate(Schema):
    owner_id: str = Field(min_length=1, max_length=64, pattern=r"^[^\s\x00-\x1f\x7f]+$")
    domain: str = Field(min_length=1, max_length=64, pattern=r"^[^\s\x00-\x1f\x7f]+$")
    cidr: str
    action: Action

    @field_validator("cidr")
    @classmethod
    def network(cls, value):
        # Strict prevents silently broadening an IP with host bits to a subnet.
        return str(ipaddress.ip_network(value))


class GrantState(Schema):
    revision: int = Field(ge=1, strict=True)
    enabled: bool = Field(strict=True)


class LegacyGrant(Schema):
    domain: str = Field(min_length=1, max_length=64)
    cidrs: list[str] = Field(min_length=1, max_length=256)
    actions: list[Action] = Field(min_length=1, max_length=2)


class GrantImport(Schema):
    grants: dict[str, list[LegacyGrant]]

    def entries(self):
        entries = {}
        for owner, grants in self.grants.items():
            for grant in grants:
                for cidr in grant.cidrs:
                    for action in grant.actions:
                        body = GrantCreate(
                            owner_id=owner, domain=grant.domain, cidr=cidr, action=action
                        )
                        entries[identifier(body)] = body
                        if len(entries) > 1000:
                            raise ValueError("too many grants")
        return entries


def identifier(body):
    return hashlib.sha256(
        json.dumps(body.model_dump(), sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def metadata(row):
    return {
        name: getattr(row, name)
        for name in ("id", "owner_id", "domain", "cidr", "action", "enabled", "revision")
    }


class GrantStore:
    def __init__(self, *, sessions=None):
        if sessions is None:
            from .service import sessions
        self.sessions = sessions

    @staticmethod
    def audit(session, actor, operation, grant):
        session.add(
            GrantAudit(
                id=uuid4().hex,
                actor_id=str(actor),
                operation=operation,
                grant_id=grant["id"],
                payload=grant,
                created_at=now(),
            )
        )

    def list(self, owner=None):
        with self.sessions() as session:
            query = select(AddressGrant).order_by(AddressGrant.owner_id, AddressGrant.id)
            if owner is not None:
                query = query.where(AddressGrant.owner_id == str(owner))
            return [metadata(row) for row in session.scalars(query)]

    def create(self, actor, body):
        key = identifier(body)
        with self.sessions() as session:
            if session.get(AddressGrant, key):
                raise HTTPException(409, "授权已存在，请刷新后修改启用状态")
            row = AddressGrant(id=key, **body.model_dump(), enabled=True, revision=1)
            session.add(row)
            result = metadata(row)
            self.audit(session, actor, "create", result)
            return result

    def set_state(self, actor, key, body):
        with self.sessions() as session:
            changed = session.execute(
                update(AddressGrant)
                .where(
                    AddressGrant.id == key,
                    AddressGrant.revision == body.revision,
                )
                .values(enabled=body.enabled, revision=body.revision + 1)
            )
            if changed.rowcount != 1:
                raise HTTPException(409, "授权不存在或已变化，请刷新后重试")
            result = metadata(session.get(AddressGrant, key))
            self.audit(session, actor, "enable" if body.enabled else "revoke", result)
            return result

    def import_file(self, actor, body):
        try:
            entries = body.entries()
        except ValueError:
            raise HTTPException(422, "文件包含无效授权或超过 1000 条") from None
        added, skipped = 0, 0
        with self.sessions() as session:
            for key, entry in entries.items():
                if session.get(AddressGrant, key) is not None:
                    # Re-import must not resurrect a revoked grant.
                    skipped += 1
                    continue
                row = AddressGrant(id=key, **entry.model_dump(), enabled=True, revision=1)
                session.add(row)
                self.audit(session, actor, "import", metadata(row))
                added += 1
        return {"added": added, "skipped": skipped}

    def authorize(self, owner, domain, address, action="device.collect"):
        try:
            ip = ipaddress.ip_address(address)
            with self.sessions() as session:
                cidrs = session.scalars(
                    select(AddressGrant.cidr).where(
                        AddressGrant.owner_id == str(owner),
                        AddressGrant.domain == domain,
                        AddressGrant.action == action,
                        AddressGrant.enabled.is_(True),
                    )
                )
                if any(ip in ipaddress.ip_network(cidr) for cidr in cidrs):
                    return
        except (ValueError, SQLAlchemyError):
            raise DeviceAccessError("not_authorized", "设备授权校验不可用") from None
        # Name what did not match so the user can fix the domain or ask for the right grant.
        kind = {"device.collect": "采集", "device.terminal": "终端"}.get(action, action)
        raise DeviceAccessError(
            "not_authorized", f"设备 {address} 不在当前用户于管理域“{domain}”的{kind}授权范围内"
        )


def authorize_address(owner, domain, address, action="device.collect"):
    """Public App service used by topology, vault, trust and device access."""
    return GrantStore().authorize(owner, domain, address, action)
