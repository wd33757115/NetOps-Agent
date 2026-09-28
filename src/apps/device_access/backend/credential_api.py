"""Metadata-only credential endpoints with redacted validation errors."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from src.auth.models import CurrentUser
from src.platform.authorization.client import require_app
from src.platform.device_access.contracts import DeviceAccessError

from .credential_schemas import CredentialCreate, CredentialDisabled, CredentialRotate
from .credentials import CredentialVault


class CredentialRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def redacted(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Pydantic/FastAPI's default errors include the original invalid input.
                raise HTTPException(422, "凭据参数无效，请检查名称、作用域和密码长度") from None
            except DeviceAccessError as exc:
                raise HTTPException(422, {"code": exc.failure_code, "message": str(exc)}) from None

        return redacted


router = APIRouter(prefix="/credential-sets", route_class=CredentialRoute)
actor = require_app("device-access")


def maintainer(user: CurrentUser = Depends(actor)):
    if not user.can_execute_skills():
        raise HTTPException(403, "当前账号不能维护凭据集")
    return user


@router.get("")
def list_credentials(user: CurrentUser = Depends(actor)):
    return CredentialVault().list(user.user_id)


@router.post("", status_code=201)
def create_credentials(body: CredentialCreate, user: CurrentUser = Depends(maintainer)):
    return CredentialVault().create(user.user_id, body)


@router.patch("/{identifier}")
def disable_credentials(
    identifier: str, body: CredentialDisabled, user: CurrentUser = Depends(maintainer)
):
    return CredentialVault().disable(user.user_id, identifier, body.disabled)


@router.post("/{identifier}/rotate")
def rotate_credentials(
    identifier: str, body: CredentialRotate, user: CurrentUser = Depends(maintainer)
):
    return CredentialVault().rotate(user.user_id, identifier, body)
