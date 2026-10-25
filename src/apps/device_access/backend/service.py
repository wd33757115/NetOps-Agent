"""Saved-session SSH: human terminals; diagnosis logins live in diagnosis.py."""

import json
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.client import authorize_worker
from src.platform.device_access import DeviceAccessService
from src.platform.device_access.contracts import ConnectionProfile, DeviceAccessError, Target
from src.platform.device_access.drivers.paramiko_driver import ParamikoDriver
from src.platform.device_access.governance import write_event
from src.platform.device_access.security import CredentialStore
from src.platform.kernel.migrations import business_engine

from . import saved
from .host_keys import HostKeyStore
from .models import DOMAIN, Operation, now

APP_ID = "device-access"
TICKET_PREFIX = "netops:device-access:terminal-ticket:"
checkpoint = ContextVar("device_access_checkpoint", default=lambda: None)


@contextmanager
def sessions():
    with Session(business_engine()) as session, session.begin():
        yield session


def access(
    ref, host, port, username, password, *, driver_kind="paramiko", platform=None,
    auto_trust=False, device_name=None,
):
    """One device, one login. Host keys come from this App's store."""
    trust = HostKeyStore()
    if driver_kind == "netmiko":
        from src.platform.device_access.drivers.netmiko_driver import NetmikoDriver

        driver = NetmikoDriver(
            platform, known_hosts_provider=lambda target, port: trust.known_hosts(target.host, port)
        )
    elif driver_kind == "paramiko":
        driver = ParamikoDriver(
            host_key_verifier=lambda target, port, key: trust.verify(
                target.host, port, key, auto_trust=auto_trust
            )
        )
    else:
        raise DeviceAccessError("invalid_request", "未知 SSH 驱动")
    profile = ConnectionProfile(
        ref, username, password, port=port, max_output_bytes=256 * 1024, platform=platform
    )
    target = Target(ref, host, management_domain=DOMAIN, display_name=device_name)
    return DeviceAccessService(
        target_resolver=lambda _: target, profile_resolver=lambda _: profile, driver=driver
    )


def session_access(owner, session_id, **kwargs):
    name, host, port = saved.target(owner, session_id)
    username, password = saved.login(owner, session_id)
    return access(session_id, host, port, username, password, device_name=name, **kwargs)


def _owned_operation(session, owner, operation_id):
    row = session.get(Operation, operation_id)
    if not row or row.owner_id != str(owner):
        raise HTTPException(404, "设备访问记录不存在")
    return row


def read(owner, operation_id):
    with sessions() as session:
        row = _owned_operation(session, owner, operation_id)
        created = (
            row.created_at.replace(tzinfo=timezone.utc)
            if row.created_at.tzinfo is None
            else row.created_at
        )
        limit = 1080 if row.profile_id in {"human-terminal", "human-batch"} else 180
        if row.status == "running" and (now() - created).total_seconds() > limit:
            row.status, row.finished_at = "waiting_for_input", now()
            row.result = {
                **row.result,
                "failure_code": "execution_interrupted",
                "notice": "执行未正常结束；本请求不会自动重试，请人工核对后重新发起。",
            }
        return {
            "id": row.id,
            "session_id": row.session_id,
            "host": row.host,
            "profile_id": row.profile_id,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
            "result": row.result,
        }


def _begin(owner, request_id, session_id, host, profile_id):
    with sessions() as session:
        existing = session.scalar(
            select(Operation).where(Operation.owner_id == owner, Operation.request_id == request_id)
        )
        if existing:
            if existing.session_id != session_id or existing.profile_id != profile_id:
                raise HTTPException(409, "请求编号已用于其他操作")
            return existing.id, False
        row = Operation(
            id=uuid4().hex,
            owner_id=owner,
            session_id=session_id,
            host=host,
            request_id=request_id,
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


def _guard(owner, message):
    def guard():
        checkpoint.get()()
        if not authorize_worker(owner, APP_ID).can_execute_skills():
            raise DeviceAccessError("not_authorized", message)
        return True

    return guard


def ticket(owner, body):
    """A 30-second single-use ticket; secrets stay in the backend."""
    if body.session_id is not None:
        device_name, host, port = saved.target(owner, body.session_id)
    else:
        host, port = body.host, body.port
        device_name = host
    ref = None
    if body.login is not None:
        username, password = body.login.username, body.login.password.get_secret_value()
        ref = CredentialStore().put(owner, DOMAIN, [host], username, password, ttl=60)
    operation_id, created = _begin(owner, body.request_id, body.session_id, host, "human-terminal")
    if not created:
        if ref:
            CredentialStore().revoke(ref)
        raise HTTPException(409, "该终端请求已经使用，请创建新请求")
    value = secrets.token_urlsafe(32)
    payload = {
        "owner": owner,
        "session_id": body.session_id,
        "host": host,
        "port": port,
        "device_name": device_name,
        "credential_ref": ref,
        # Saved to the session only after this login succeeds.
        "save": body.save,
        "operation_id": operation_id,
    }
    try:
        write_event(
            actor_id=owner, source_app=APP_ID, kind="terminal_requested",
            detail={
                "host": host, "port": port, "session_id": operation_id, "device_name": device_name
            },
        )
        CredentialStore().client.set(TICKET_PREFIX + value, json.dumps(payload), ex=30)
    except Exception:
        if ref:
            CredentialStore().revoke(ref)
        finish(operation_id, "waiting_for_input", {"failure_code": "ticket_unavailable"})
        raise DeviceAccessError("ticket_unavailable", "终端票据服务不可用") from None
    return {"ticket": value, "session_id": operation_id, "expires_in": 30}


def consume_ticket(value, operation_id):
    if not isinstance(value, str) or len(value) > 100:
        raise DeviceAccessError("not_authorized", "终端票据无效")
    raw = CredentialStore().client.getdel(TICKET_PREFIX + value)
    if not raw:
        raise DeviceAccessError("not_authorized", "终端票据已过期或已使用")
    payload = json.loads(raw)
    if payload["operation_id"] != operation_id:
        raise DeviceAccessError("not_authorized", "终端票据不匹配")
    return payload


def terminal_login(payload):
    """A one-time typed login is read once and revoked; otherwise the saved one."""
    ref = payload.get("credential_ref")
    if not ref:
        return saved.login(payload["owner"], payload.get("session_id"))
    store = CredentialStore()
    try:
        return store.get(ref, payload["owner"], DOMAIN, payload["host"])
    finally:
        store.revoke(ref)
