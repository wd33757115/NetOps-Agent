"""Administrator-managed device scope; users can read their own grants."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from src.auth.models import CurrentUser

from .credential_api import actor
from .grants import GrantCreate, GrantImport, GrantState, GrantStore

router = APIRouter(prefix="/grants")


def admin(user: CurrentUser = Depends(actor)):
    if not user.is_admin():
        raise HTTPException(403, "仅管理员可以维护设备访问范围")
    return user


def mutate(fn, *args):
    try:
        return fn(*args)
    except IntegrityError:
        raise HTTPException(409, "授权已变化，请刷新后重试") from None


@router.get("")
def list_grants(user: CurrentUser = Depends(actor)):
    return GrantStore().list(None if user.is_admin() else user.user_id)


@router.post("", status_code=201)
def create(body: GrantCreate, user: CurrentUser = Depends(admin)):
    return mutate(GrantStore().create, user.user_id, body)


@router.post("/import")
def import_grants(body: GrantImport, user: CurrentUser = Depends(admin)):
    return mutate(GrantStore().import_file, user.user_id, body)


@router.patch("/{identifier}")
def set_state(identifier: str, body: GrantState, user: CurrentUser = Depends(admin)):
    return mutate(GrantStore().set_state, user.user_id, identifier, body)
