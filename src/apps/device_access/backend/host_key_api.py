"""Admin-only host trust maintenance; device users receive stable failure codes."""

import ipaddress
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator

from src.auth.models import CurrentUser

from .credential_api import CredentialRoute, actor
from .host_keys import HostKeyStore
from .schemas import Schema


class DomainPolicy(Schema):
    domain: str = Field(min_length=1, max_length=64, pattern=r"^[^\s\x00-\x1f\x7f]+$")
    policy: Literal["strict", "tofu_pinned"]


class KeyReference(Schema):
    domain: str = Field(min_length=1, max_length=64, pattern=r"^[^\s\x00-\x1f\x7f]+$")
    host: str
    port: int = Field(default=22, ge=1, le=65535, strict=True)
    revision: int = Field(ge=0, strict=True)

    @field_validator("host")
    @classmethod
    def ip(cls, value):
        return str(ipaddress.ip_address(value))


class KeyApproval(KeyReference):
    key_type: str = Field(min_length=1, max_length=64)
    public_key: str = Field(min_length=1, max_length=24000)
    confirm_fingerprint: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")


def admin(user: CurrentUser = Depends(actor)):
    if not user.is_admin():
        raise HTTPException(403, "仅管理员可以维护主机密钥信任")
    return user


router = APIRouter(prefix="/host-keys", route_class=CredentialRoute)


@router.get("")
def list_keys(user: CurrentUser = Depends(admin)):
    return {"keys": HostKeyStore().list(), "policies": HostKeyStore().policies()}


@router.put("/policy")
def policy(body: DomainPolicy, user: CurrentUser = Depends(admin)):
    return HostKeyStore().set_policy(user.user_id, body.domain, body.policy)


@router.post("/verify")
def verify(body: KeyApproval, user: CurrentUser = Depends(admin)):
    return HostKeyStore().approve(user.user_id, body)


@router.post("/revoke")
def revoke(body: KeyReference, user: CurrentUser = Depends(admin)):
    return HostKeyStore().revoke(user.user_id, body)
