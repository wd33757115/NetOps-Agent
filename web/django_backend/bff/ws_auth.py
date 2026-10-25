# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""WebSocket JWT 鉴权（BFF）。"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from django.contrib.auth import get_user_model

from src.auth.token_store import is_auth_token_revoked
from src.auth.security import decode_access_token


def _header_from_scope(scope: dict, name: str) -> str:
    target = name.lower().encode()
    for key, value in scope.get("headers", []):
        if key.lower() == target:
            return value.decode("utf-8", errors="ignore")
    return ""


def extract_bearer_token_from_scope(scope: dict) -> str | None:
    query = parse_qs(scope.get("query_string", b"").decode("utf-8"))
    token = (query.get("token") or query.get("access_token") or [None])[0]
    if token:
        return token
    auth = _header_from_scope(scope, "authorization")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def authenticate_websocket_scope(scope: dict) -> tuple[Any | None, dict | None, str | None]:
    """
    校验 WebSocket 连接的 JWT。
    返回 (django_user, claims, error_message)
    """
    token = extract_bearer_token_from_scope(scope)
    if not token:
        return None, None, "missing token"

    try:
        claims = decode_access_token(token)
    except Exception:
        return None, None, "invalid token"

    jti = claims.get("jti")
    session_id = claims.get("session_id")
    if is_auth_token_revoked(jti, session_id):
        return None, None, "token revoked"

    user_id = claims.get("user_id")
    if user_id is None:
        return None, None, "invalid token payload"

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return None, None, "user not found"

    if not user.is_active:
        return None, None, "user inactive"

    return user, claims, None
