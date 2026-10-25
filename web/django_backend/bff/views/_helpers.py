# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import json

from django.http import HttpRequest

from ..roles import user_thread_prefix


_FORWARDED_CLIENT_HEADERS = {
    "HTTP_AUTHORIZATION": "Authorization",
    "CONTENT_TYPE": "Content-Type",
    "HTTP_ACCEPT": "Accept",
    "HTTP_IDEMPOTENCY_KEY": "Idempotency-Key",
    "HTTP_IF_MATCH": "If-Match",
    "HTTP_IF_NONE_MATCH": "If-None-Match",
    "HTTP_LAST_EVENT_ID": "Last-Event-ID",
    "HTTP_RANGE": "Range",
}


def _request_user_role(request: HttpRequest) -> str:
    role = getattr(request, "bff_user_role", None)
    if role:
        return str(role)
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        from ..roles import get_user_role

        return get_user_role(user)
    return "operator"


def _request_thread_prefix(request: HttpRequest, user) -> str:
    thread_prefix = getattr(request, "bff_user_thread_prefix", None)
    if thread_prefix:
        return str(thread_prefix)
    return user_thread_prefix(user)


def parse_json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def forward_client_headers(request: HttpRequest) -> dict:
    """将客户端可转发的请求头传递给 FastAPI，并注入 BFF 可信用户上下文。"""
    headers = {
        target: str(value)
        for source, target in _FORWARDED_CLIENT_HEADERS.items()
        if (value := request.META.get(source))
    }

    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        headers["X-User-Id"] = str(user.id)
        headers["X-User-Name"] = user.username
        headers["X-User-Role"] = _request_user_role(request)
        headers["X-User-Permissions"] = ",".join(getattr(request, "bff_user_permissions", []))
        headers["X-User-Thread-Prefix"] = _request_thread_prefix(request, user)
        session_id = getattr(request, "bff_session_id", "")
        if session_id:
            headers["X-Session-Id"] = session_id

    return headers


def inject_user_into_body(request: HttpRequest, data: dict) -> dict:
    """将 JWT 用户写入请求体（覆盖客户端伪造的 user_id）。"""
    payload = dict(data or {})
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        payload["user_id"] = str(user.id)
    return payload
