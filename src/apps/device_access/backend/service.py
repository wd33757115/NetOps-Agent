"""Human-created expiring connections and auditable bounded diagnostic calls."""

import json
import os
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.client import authorize_worker
from src.platform.device_access import AccessContext, AccessGrant, BatchRequest, DeviceAccessService
from src.platform.device_access.catalog import DIAGNOSTIC_PROFILES
from src.platform.device_access.contracts import ConnectionProfile, DeviceAccessError, Target
from src.platform.device_access.security import CredentialStore, authorize_address
from src.platform.kernel.migrations import business_engine

from .credentials import CredentialVault
from .models import Connection, Operation, now
from .vault_driver import VaultDriver

APP_ID = "device-access"
TICKET_PREFIX = "netops:device-access:terminal-ticket:"
checkpoint = ContextVar("device_access_checkpoint", default=lambda: None)


@contextmanager
def sessions():
    with Session(business_engine()) as session, session.begin():
        yield session


def _owned(session, model, row_id, owner):
    row = session.get(model, row_id)
    if not row or row.owner_id != owner:
        raise HTTPException(404, "设备访问记录不存在")
    return row


def connection(owner, connection_id, action="device.collect"):
    with sessions() as session:
        row = _owned(session, Connection, connection_id, owner)
        expiry = (
            row.expires_at.replace(tzinfo=timezone.utc)
            if row.expires_at.tzinfo is None
            else row.expires_at
        )
        if expiry <= now():
            raise DeviceAccessError("credential_expired", "连接授权已过期，请人工重新输入凭据")
        settings, ref = dict(row.settings), row.credential_ref
    authorize_address(owner, settings["domain"], settings["address"], action)
    if action == "device.terminal" and not settings["allow_terminal"]:
        raise DeviceAccessError("not_authorized", "此连接未授权人工终端")
    return settings, ref


def create_connection(owner, body):
    if any(profile not in DIAGNOSTIC_PROFILES for profile in body.profiles):
        raise HTTPException(422, "未知诊断命令集")
    authorize_address(owner, body.domain, body.address)
    if body.allow_terminal:
        authorize_address(owner, body.domain, body.address, "device.terminal")
    ref = ""
    if body.credential_mode == "vault" and body.password is None:
        if not CredentialVault().candidates(
            owner, body.domain, body.address, credential_set_id=body.credential_set_id
        ):
            raise DeviceAccessError("waiting_for_input", "没有适用凭据，请创建凭据集或提供临时凭据")
    if body.password is not None:
        ref = CredentialStore().put(
            owner, body.domain, [body.address], body.username, body.password.get_secret_value()
        )
    try:
        with sessions() as session:
            row = Connection(
                id=uuid4().hex,
                owner_id=owner,
                credential_ref=ref,
                settings=body.model_dump(exclude={"username", "password"}),
                expires_at=now() + timedelta(hours=1),
            )
            session.add(row)
            result = {
                "connection_id": row.id,
                "expires_at": row.expires_at.isoformat(),
                **row.settings,
            }
    except Exception:
        if ref:
            CredentialStore().revoke(ref)
        raise
    return result


def list_connections(owner):
    with sessions() as session:
        return [
            {"connection_id": row.id, "expires_at": row.expires_at.isoformat(), **row.settings}
            for row in session.scalars(
                select(Connection)
                .where(Connection.owner_id == owner, Connection.expires_at > now())
                .order_by(Connection.created_at.desc())
                .limit(100)
            )
        ]


def revoke(owner, connection_id):
    with sessions() as session:
        row = _owned(session, Connection, connection_id, owner)
        row.expires_at = now()
        ref = row.credential_ref
    if ref:
        CredentialStore().revoke(ref)
    return {"revoked": True}


def access(owner, connection_id, action="device.collect"):
    settings, ref = connection(owner, connection_id, action)
    driver = None
    if settings.get("credential_mode") == "vault":
        username, password = "credential-vault", None

        def guard():
            checkpoint.get()()
            user = authorize_worker(owner, APP_ID)
            if not user.can_execute_skills():
                raise DeviceAccessError("not_authorized", "账号不能执行设备访问")
            connection(owner, connection_id, action)
            return True

        driver = VaultDriver(
            CredentialVault(),
            owner,
            temporary_ref=ref or None,
            credential_set_id=settings.get("credential_set_id"),
            guard=guard,
        )
    else:
        username, password = CredentialStore().get(
            ref, owner, settings["domain"], settings["address"]
        )
    profile = ConnectionProfile(
        connection_id,
        username,
        password,
        port=settings["port"],
        known_hosts_file=os.getenv("DEVICE_ACCESS_KNOWN_HOSTS"),
        max_output_bytes=256 * 1024,
    )
    target = Target(connection_id, settings["address"], management_domain=settings["domain"])
    return DeviceAccessService(
        target_resolver=lambda _: target, profile_resolver=lambda _: profile, driver=driver
    )


def read(owner, operation_id):
    with sessions() as session:
        row = _owned(session, Operation, operation_id, owner)
        created = (
            row.created_at.replace(tzinfo=timezone.utc)
            if row.created_at.tzinfo is None
            else row.created_at
        )
        limit = 1080 if row.profile_id == "human-terminal" else 180
        if row.status == "running" and (now() - created).total_seconds() > limit:
            row.status, row.finished_at = "waiting_for_input", now()
            row.result = {
                **row.result,
                "failure_code": "execution_interrupted",
                "notice": "执行未正常结束；本请求不会自动重试，请人工核对后重新授权。",
            }
        return {
            "id": row.id,
            "connection_id": row.connection_id,
            "profile_id": row.profile_id,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
            "result": row.result,
        }


def _begin(owner, body, profile_id):
    with sessions() as session:
        existing = session.scalar(
            select(Operation).where(
                Operation.owner_id == owner, Operation.request_id == body.request_id
            )
        )
        if existing:
            if existing.connection_id != body.connection_id or existing.profile_id != profile_id:
                raise HTTPException(409, "请求编号已用于其他操作")
            return existing.id, False
        row = Operation(
            id=uuid4().hex,
            owner_id=owner,
            connection_id=body.connection_id,
            request_id=body.request_id,
            profile_id=profile_id,
            status="running",
            result={},
        )
        session.add(row)
        return row.id, True


def finish(operation_id, status, result):
    with sessions() as session:
        row = session.get(Operation, operation_id)
        if row and row.status == "running":
            row.status, row.result, row.finished_at = status, result, now()


def collect(owner, body):
    settings, _ = connection(owner, body.connection_id)
    if body.profile_id not in settings["profiles"] or body.profile_id not in DIAGNOSTIC_PROFILES:
        raise HTTPException(403, "此连接未授权该诊断命令集")
    operation_id, created = _begin(owner, body, body.profile_id)
    if not created:
        return read(owner, operation_id)
    outputs = []
    try:
        commands = DIAGNOSTIC_PROFILES[body.profile_id]

        def guard():
            checkpoint.get()()
            user = authorize_worker(owner, APP_ID)
            if not user.can_execute_skills():
                raise HTTPException(403, "账号不能执行设备采集")
            connection(owner, body.connection_id)
            return True

        guard()
        result = access(owner, body.connection_id).execute_batch(
            AccessContext(
                owner,
                APP_ID,
                (
                    AccessGrant(
                        body.connection_id, body.connection_id, frozenset(c.text for c in commands)
                    ),
                ),
                operation_id,
            ),
            BatchRequest(body.connection_id, body.connection_id, commands, budget_seconds=60),
            should_continue=guard,
            on_result=lambda item: outputs.append(asdict(item)),
        )
        finish(
            operation_id,
            result.execution_status,
            {
                "commands": outputs,
                "failure_code": result.failure_code,
                "observed_at": now().isoformat(),
                "kind": "live_observation",
            },
        )
    except Exception as exc:
        finish(
            operation_id,
            "waiting_for_input",
            {
                "commands": outputs,
                "failure_code": (
                    exc.failure_code
                    if isinstance(exc, DeviceAccessError)
                    else "execution_unavailable"
                ),
                "notice": "本操作不会自动重试。请人工检查连接、主机密钥或更新凭据后发起新的请求。",
            },
        )
    return read(owner, operation_id)


def ticket(owner, body):
    connection(owner, body.connection_id, "device.terminal")
    operation_id, created = _begin(owner, body, "human-terminal")
    if not created:
        raise HTTPException(409, "该终端请求已经使用，请创建新请求")
    value = secrets.token_urlsafe(32)
    try:
        store = CredentialStore()
        store.client.set(
            TICKET_PREFIX + value,
            json.dumps(
                {"owner": owner, "connection_id": body.connection_id, "operation_id": operation_id}
            ),
            ex=30,
        )
    except Exception:
        finish(operation_id, "waiting_for_input", {"failure_code": "ticket_unavailable"})
        raise DeviceAccessError("ticket_unavailable", "终端票据服务不可用") from None
    return {"ticket": value, "session_id": operation_id, "expires_in": 30}


def consume_ticket(value, session_id):
    if not isinstance(value, str) or len(value) > 100:
        raise DeviceAccessError("not_authorized", "终端票据无效")
    raw = CredentialStore().client.getdel(TICKET_PREFIX + value)
    if not raw:
        raise DeviceAccessError("not_authorized", "终端票据已过期或已使用")
    payload = json.loads(raw)
    if payload["operation_id"] != session_id:
        raise DeviceAccessError("not_authorized", "终端票据不匹配")
    return payload
