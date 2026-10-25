"""Server-only session entry point and owned audit reads. No HTTP live handles."""

from fastapi import HTTPException
from sqlalchemy import select

from src.platform.authorization.client import authorize_worker
from src.platform.device_access import AccessContext, AccessGrant
from src.platform.device_access.contracts import DeviceAccessError

from . import service
from .models import SessionAudit


def write_audit(event):
    with service.sessions() as session:
        session.add(
            SessionAudit(
                id=event["id"],
                owner_id=event["owner_id"],
                session_id=event["session_id"],
                target_ref=event["target_ref"],
                event_kind=event["kind"],
                payload=event,
            )
        )
    return "device-access:audit:" + event["id"]


def read_audit(owner, session_id):
    with service.sessions() as session:
        records = list(
            session.scalars(
                select(SessionAudit)
                .where(
                    SessionAudit.owner_id == str(owner),
                    SessionAudit.session_id == session_id,
                )
                .order_by(SessionAudit.created_at, SessionAudit.id)
            )
        )
        if not records:
            raise HTTPException(404, "会话审计记录不存在")
        return [row.payload for row in records]


def open_session(
    owner,
    session_id,
    *,
    platform,
    purpose,
    budget=None,
    should_continue=None,
    required_vrf=None,
    driver_kind="paramiko",
):
    """Caller supplies an identified platform, never a model-controlled platform hint.

    P4's worker must keep the handle in its own process and close all handles at
    diagnosis completion. Only its opaque ID is exposed through model tools.
    """

    def guard():
        service.checkpoint.get()()
        user = authorize_worker(owner, service.APP_ID)
        if not user.can_execute_skills():
            raise DeviceAccessError("not_authorized", "账号不能执行诊断会话")
        return should_continue() if should_continue is not None else True

    if guard() is False:
        raise DeviceAccessError("cancelled", "诊断会话已取消")
    access = (
        service.session_access(owner, session_id)
        if driver_kind == "paramiko"
        else service.session_access(owner, session_id, driver_kind=driver_kind, platform=platform)
    )
    return access.open_session(
        AccessContext(
            owner,
            service.APP_ID,
            (AccessGrant(session_id, session_id, action="device.session"),),
        ),
        session_id,
        platform=platform,
        purpose=purpose,
        budget=budget,
        audit=write_audit,
        should_continue=guard,
        required_vrf=required_vrf,
    )
