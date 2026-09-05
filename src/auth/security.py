# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""JWT 解码与 BFF 可信用户头解析。"""

from __future__ import annotations

import logging
from typing import Mapping

import jwt
from jwt.exceptions import InvalidTokenError

from src.auth.models import CurrentUser
from src.auth.token_store import is_auth_token_revoked
from src.auth.rbac import normalize_role
from src.common.config import get_settings
from src.gateway.bff_security import is_trusted_bff_request

logger = logging.getLogger(__name__)


def _header(headers: Mapping[str, str], name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return ""


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp"]},
    )


def user_from_jwt_payload(payload: dict) -> CurrentUser:
    user_id = payload.get("user_id") or payload.get("sub")
    if user_id is None:
        raise InvalidTokenError("missing user_id")
    return CurrentUser(
        user_id=str(user_id),
        username=str(payload.get("username") or payload.get("preferred_username") or user_id),
        role=normalize_role(payload.get("role")),
        thread_prefix=payload.get("thread_id"),
        session_id=payload.get("session_id"),
    )


def user_from_bff_headers(headers: Mapping[str, str]) -> CurrentUser | None:
    if not is_trusted_bff_request(headers):
        return None
    user_id = _header(headers, "X-User-Id")
    if not user_id:
        return None
    session_id = _header(headers, "X-Session-Id") or None
    if is_auth_token_revoked(None, session_id):
        return None
    return CurrentUser(
        user_id=user_id,
        username=_header(headers, "X-User-Name") or user_id,
        role=normalize_role(_header(headers, "X-User-Role") or "operator"),
        permissions=[p for p in _header(headers, "X-User-Permissions").split(",") if p],
        thread_prefix=_header(headers, "X-User-Thread-Prefix") or None,
        session_id=session_id,
    )


def resolve_current_user(headers: Mapping[str, str]) -> CurrentUser | None:
    """优先 BFF 注入头；否则尝试 Bearer JWT（开发直连 FastAPI）。"""
    bff_user = user_from_bff_headers(headers)
    if bff_user:
        return bff_user

    auth = _header(headers, "Authorization")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
        if is_auth_token_revoked(payload.get("jti"), payload.get("session_id")):
            logger.warning("JWT revoked")
            return None
        return user_from_jwt_payload(payload)
    except InvalidTokenError as exc:
        logger.warning("JWT decode failed: %s", exc)
        return None
