# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""ITSM Webhook: the ITSM system calls with a platform account (HTTP Basic).

The Workflow it starts belongs to that account, so its steps run under the account's App
grants and its notifications reach it. The password never travels past Django.
"""

import base64
import logging

from django.contrib.auth import authenticate
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..audit import log_auth_event
from ..auth import (
    LOGIN_BLOCK_MESSAGE,
    check_login_blocked,
    clear_login_failures,
    record_login_failure,
)
from ..middleware import get_client_ip
from ..proxy_client import DEFAULT_TIMEOUT, proxy_to_fastapi
from ..response import bff_error
from ..roles import ROLE_ADMIN, ROLE_OPERATOR, get_user_role
from ..sync_async import sync_bff_view
from ._helpers import forward_client_headers, parse_json_body

logger = logging.getLogger("bff.views.itsm")

REALM = 'Basic realm="NetOps ITSM Webhook", charset="UTF-8"'


def _challenge(message: str) -> JsonResponse:
    response = bff_error(message, 401)
    response["WWW-Authenticate"] = REALM
    return response


def _credentials(request: HttpRequest) -> tuple[str, str] | None:
    scheme, _, encoded = request.META.get("HTTP_AUTHORIZATION", "").partition(" ")
    if scheme.lower() != "basic" or not encoded.strip():
        return None
    try:
        username, separator, password = (
            base64.b64decode(encoded.strip(), validate=True).decode("utf-8").partition(":")
        )
    except (ValueError, UnicodeDecodeError):
        return None
    return (username.strip(), password) if separator and username.strip() and password else None


def authenticate_webhook(request: HttpRequest):
    """The account the request carries, or the response that refuses it."""
    credentials = _credentials(request)
    if credentials is None:
        return None, _challenge("需要平台账号的用户名和密码（HTTP Basic 认证）")
    username, password = credentials
    ip = get_client_ip(request)
    blocked, retry_after = check_login_blocked(ip, username)
    if blocked:
        return None, bff_error(LOGIN_BLOCK_MESSAGE, 429, data={"retry_after_seconds": retry_after})
    user = authenticate(request, username=username, password=password)
    if user is None:
        record_login_failure(ip, username)
        log_auth_event("itsm_webhook", None, request, status="failure",
                       detail={"username": username, "path": request.path})
        return None, _challenge("用户名或密码错误")
    if not user.is_active:
        return None, bff_error("账号已停用", 403)
    if get_user_role(user) not in {ROLE_ADMIN, ROLE_OPERATOR}:
        log_auth_event("itsm_webhook", user, request, status="failure",
                       detail={"reason": "role", "path": request.path})
        return None, bff_error("该账号不能启动 Workflow（需要管理员或运维人员角色）", 403)
    clear_login_failures(ip, username)
    log_auth_event("itsm_webhook", user, request, detail={"path": request.path})
    request.user = user
    return user, None


def _identity_headers(request: HttpRequest) -> dict:
    headers = forward_client_headers(request)
    # The Gateway trusts the forwarded user; the ITSM password stays here.
    headers.pop("Authorization", None)
    return headers


@sync_bff_view
async def _forward(request: HttpRequest, fastapi_path: str, headers: dict) -> JsonResponse:
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path=fastapi_path,
        request_id=request.bff_request_id,
        data=parse_json_body(request),
        timeout=DEFAULT_TIMEOUT,
        extra_headers=headers,
    )


def _authenticated(fastapi_path):
    def view(request: HttpRequest, **kwargs) -> JsonResponse:
        if request.method != "POST":
            return bff_error("Method not allowed", 405)
        _, denied = authenticate_webhook(request)
        if denied is not None:
            return denied
        # Role and permissions are read from the database here, before the async proxy.
        return _forward(request, fastapi_path.format(**kwargs), _identity_headers(request))

    return csrf_exempt(view)


proxy_itsm_webhook = _authenticated("/api/v1/itsm/webhook")
proxy_firewall_policy_webhook = _authenticated("/api/v1/itsm/webhook/firewall-policy")
# Routes come from each Workflow's ITSM.webhook.yaml (route_key).
proxy_plugin_webhook = _authenticated("/api/v1/itsm/webhook/{route_key}")


@csrf_exempt
@sync_bff_view
async def proxy_itsm_callback(request: HttpRequest) -> JsonResponse:
    data = parse_json_body(request)
    return await proxy_to_fastapi(
        method="POST",
        fastapi_path="/api/v1/itsm/webhook/callback",
        request_id=request.bff_request_id,
        data=data,
        timeout=DEFAULT_TIMEOUT,
        extra_headers=forward_client_headers(request),
    )
