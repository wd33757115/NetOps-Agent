from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError

from src.auth.models import CurrentUser
from src.platform.authorization.client import require_app
from src.platform.device_access.contracts import DeviceAccessError

from . import saved, service
from .host_keys import HostKeyStore
from .schemas import BatchLogin, HostKeyAccept, Login, SessionCreate, SessionUpdate, Ticket
from .sessions import read_audit


class RedactedRoute(APIRoute):
    """Validation errors echo the request body, which may hold a password."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def redacted(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "参数无效，请检查地址、端口、用户名和密码") from None
            except DeviceAccessError as exc:
                raise HTTPException(422, {"code": exc.failure_code, "message": str(exc)}) from None
            except IntegrityError:
                raise HTTPException(409, "记录已变化，请刷新后重试") from None

        return redacted


router = APIRouter(tags=["SSH terminal"], route_class=RedactedRoute)
actor = require_app("device-access")


def operator(user: CurrentUser = Depends(actor)):
    if not user.can_execute_skills():
        raise HTTPException(403, "当前账号不能使用 SSH 终端")
    return user


@router.get("/sessions")
def list_sessions(user: CurrentUser = Depends(actor)):
    return saved.list_sessions(user.user_id)


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreate, user: CurrentUser = Depends(operator)):
    return saved.create(user.user_id, body)


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: SessionUpdate, user: CurrentUser = Depends(operator)):
    return saved.update(user.user_id, session_id, body)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, user: CurrentUser = Depends(operator)):
    return saved.delete(user.user_id, session_id)


@router.post("/sessions/login")
def batch_login(body: BatchLogin, user: CurrentUser = Depends(operator)):
    return saved.set_login(user.user_id, body)


@router.get("/default-login")
def read_default(user: CurrentUser = Depends(actor)):
    return saved.read_default(user.user_id)


@router.put("/default-login")
def save_default(body: Login, user: CurrentUser = Depends(operator)):
    return saved.save_default(user.user_id, body)


@router.get("/host-keys/{host}/{port}")
def host_key(host: str, port: int, user: CurrentUser = Depends(operator)):
    return HostKeyStore().get(host, port)


@router.post("/host-keys/accept")
def accept_host_key(body: HostKeyAccept, user: CurrentUser = Depends(operator)):
    return HostKeyStore().accept(user.user_id, body.host, body.port, body.fingerprint)


@router.post("/terminal-tickets")
def ticket(body: Ticket, user: CurrentUser = Depends(operator)):
    return service.ticket(user.user_id, body)


@router.get("/operations/{operation_id}")
def read(operation_id: str, user: CurrentUser = Depends(actor)):
    return service.read(user.user_id, operation_id)


@router.get("/operations/{operation_id}/audit")
def operation_audit(operation_id: str, user: CurrentUser = Depends(actor)):
    return read_audit(user.user_id, operation_id)
