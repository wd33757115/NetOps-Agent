from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from src.auth.models import CurrentUser
from src.platform.authorization.client import require_app
from src.platform.device_access.contracts import DeviceAccessError

from . import service
from .credential_api import CredentialRoute
from .credential_api import router as credential_router
from .grant_api import router as grant_router
from .host_key_api import router as host_key_router
from .schemas import Collect, ConnectionRequest, Ticket
from .sessions import read_audit

router = APIRouter(tags=["Device access"], route_class=CredentialRoute)
router.include_router(credential_router)
router.include_router(host_key_router)
router.include_router(grant_router)
actor = require_app("device-access")


def call(user, fn, *args):
    if not user.can_execute_skills():
        raise HTTPException(403, "当前账号不能执行设备访问")
    try:
        return fn(user.user_id, *args)
    except DeviceAccessError as exc:
        raise HTTPException(422, {"code": exc.failure_code, "message": str(exc)}) from None
    except IntegrityError:
        raise HTTPException(409, "请求已执行，请查询原操作记录") from None


@router.get("/connections")
def connections(user: CurrentUser = Depends(actor)):
    return service.list_connections(user.user_id)


@router.post("/connections", status_code=201)
def create(body: ConnectionRequest, user: CurrentUser = Depends(actor)):
    return call(user, service.create_connection, body)


@router.delete("/connections/{connection_id}")
def revoke(connection_id: str, user: CurrentUser = Depends(actor)):
    return call(user, service.revoke, connection_id)


@router.post("/collect")
def collect(body: Collect, user: CurrentUser = Depends(actor)):
    return call(user, service.collect, body)


@router.get("/operations/{operation_id}")
def read(operation_id: str, user: CurrentUser = Depends(actor)):
    return service.read(user.user_id, operation_id)


@router.post("/terminal-tickets")
def ticket(body: Ticket, user: CurrentUser = Depends(actor)):
    return call(user, service.ticket, body)


@router.get("/sessions/{session_id}/audit")
def session_audit(session_id: str, user: CurrentUser = Depends(actor)):
    return read_audit(user.user_id, session_id)
