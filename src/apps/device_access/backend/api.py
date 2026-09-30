from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError

from src.auth.models import CurrentUser
from src.platform.authorization.client import require_app
from src.platform.device_access import governance, session_logs
from src.platform.device_access.contracts import DeviceAccessError

from . import batches, saved, service
from .host_keys import HostKeyStore
from .schemas import (
    BatchCommands,
    BatchLogin,
    CommandPolicy,
    HostKeyAccept,
    Login,
    SessionCreate,
    SessionUpdate,
    Ticket,
)
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
                status = 404 if exc.failure_code == "session_log_not_found" else 422
                raise HTTPException(
                    status, {"code": exc.failure_code, "message": str(exc)}
                ) from None
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


@router.get("/command-policy")
def command_policy(user: CurrentUser = Depends(actor)):
    return governance.get_policy()


@router.put("/command-policy")
def save_command_policy(body: CommandPolicy, user: CurrentUser = Depends(operator)):
    if not user.is_admin():
        raise HTTPException(403, "只有管理员可以修改全局命令白名单")
    try:
        return governance.save_policy(body.patterns, user.user_id, expected_revision=body.version)
    except ValueError:
        raise HTTPException(422, "白名单规则无效：最多 100 条，每条 1–512 字符的有效正则") from None


@router.get("/audit")
def audit_records(
    host: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    before: int = Query(0, ge=0),
    management_only: bool = False,
    user: CurrentUser = Depends(actor),
):
    result = governance.query_audit(
        actor_id=None if user.is_admin() else user.user_id,
        host=host,
        kind=kind,
        status=status,
        limit=limit,
        offset=before,
        management_only=management_only,
    )
    next_offset = before + len(result["items"])
    items = [
        {
            **item,
            "user_id": item.get("user_id", item.get("actor_id")),
            "action": item.get("action", "ssh." + item.get("kind", "")),
        }
        for item in result["items"]
    ]
    return {
        "items": items,
        "next_cursor": (str(next_offset) if next_offset < result["total"] else None),
    }


@router.get("/session-logs")
def list_session_logs(
    host: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    before: int = Query(0, ge=0),
    user: CurrentUser = Depends(actor),
):
    result = session_logs.list_logs(
        actor_id=None if user.is_admin() else user.user_id,
        host=host,
        limit=limit,
        offset=before,
    )
    next_offset = before + len(result["items"])
    return {
        "items": result["items"],
        "next_cursor": str(next_offset) if next_offset < result["total"] else None,
    }


@router.get("/session-logs/{session_id}")
def read_session_log(session_id: str, user: CurrentUser = Depends(actor)):
    return session_logs.read_log(
        session_id, actor_id=None if user.is_admin() else user.user_id
    )


@router.get("/session-logs/{session_id}/download")
def download_session_log(session_id: str, user: CurrentUser = Depends(actor)):
    record = read_session_log(session_id, user)
    filename = quote(record["filename"], safe="")
    return Response(
        record["content"],
        media_type="text/plain",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"ssh-session.log\"; filename*=UTF-8''{filename}"
            ),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/batches", status_code=202)
def start_batch(
    body: BatchCommands, background: BackgroundTasks, user: CurrentUser = Depends(operator)
):
    result, created, targets = batches.create(user.user_id, body)
    if created:
        background.add_task(batches.run, user.user_id, result["id"], targets, body.commands)
    return result


@router.get("/batches/{batch_id}")
def batch_result(batch_id: str, user: CurrentUser = Depends(actor)):
    return batches.read(user.user_id, batch_id)


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str, user: CurrentUser = Depends(operator)):
    return batches.cancel(user.user_id, batch_id)
