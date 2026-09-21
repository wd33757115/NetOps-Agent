# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import json
import sys
import uuid
from pathlib import Path

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework_simplejwt.tokens import RefreshToken

# 允许 BFF 引用 src/auth
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.auth.token_store import (  # noqa: E402
    LOGIN_BLOCK_MESSAGE,
    blacklist_token,
    check_login_blocked,
    clear_login_failures,
    record_login_failure,
    revoke_session,
)

from .audit import log_auth_event
from .app_access import available_apps
from .jwt_utils import attach_auth_context, token_is_revoked
from .middleware import get_client_ip
from .response import bff_error, bff_success
from .roles import get_user_permissions, get_user_role, user_thread_prefix


def get_tokens_for_user(user, request=None):
    refresh = RefreshToken.for_user(user)
    role = get_user_role(user)
    thread_prefix = user_thread_prefix(user)
    session_id = f"sess-{uuid.uuid4().hex[:16]}"

    refresh["role"] = role
    refresh["thread_id"] = thread_prefix
    refresh["username"] = user.username
    refresh["session_id"] = session_id

    access = refresh.access_token
    access["role"] = role
    access["thread_id"] = thread_prefix
    access["username"] = user.username
    access["session_id"] = session_id

    log_auth_event(
        "login",
        user,
        request,
        detail={"role": role, "session_id": session_id, "thread_prefix": thread_prefix},
    )

    return {
        "refresh": str(refresh),
        "access": str(access),
        "role": role,
        "thread_id": thread_prefix,
        "session_id": session_id,
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": role,
            "permissions": get_user_permissions(user),
            "app_ids": [app["id"] for app in available_apps(user)],
        },
    }


@csrf_exempt
def bff_login(request):
    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return bff_error("Invalid JSON", 400)

    username = (body.get("username") or "").strip()
    password = body.get("password")

    if not username or not password:
        return bff_error("username and password are required", 400)

    ip = get_client_ip(request)
    blocked, retry_after = check_login_blocked(ip, username)
    if blocked:
        return bff_error(
            LOGIN_BLOCK_MESSAGE,
            429,
            data={"retry_after_seconds": retry_after},
        )

    user = authenticate(username=username, password=password)
    if user is None:
        record_login_failure(ip, username)
        log_auth_event("login", None, request, status="failure", detail={"username": username})
        return bff_error("Invalid credentials", 401)

    if not user.is_active:
        log_auth_event("login", user, request, status="failure", detail={"reason": "inactive"})
        return bff_error("Account disabled", 403)

    clear_login_failures(ip, username)
    return bff_success(get_tokens_for_user(user, request))


@csrf_exempt
def bff_refresh(request):
    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return bff_error("Invalid JSON", 400)

    refresh_token = body.get("refresh")
    if not refresh_token:
        return bff_error("refresh token is required", 400)

    try:
        refresh = RefreshToken(refresh_token)
        if token_is_revoked(refresh):
            return bff_error("Refresh token has been revoked", 401)

        user_id = refresh.get("user_id")
        User = get_user_model()
        user = User.objects.get(pk=user_id)

        # Refresh Token 轮换：吊销旧 refresh，签发新对
        blacklist_token(refresh_token, "refresh")
        return bff_success(get_tokens_for_user(user, request))
    except Exception:
        return bff_error("Invalid or expired refresh token", 401)


@csrf_exempt
def bff_logout(request):
    """登出：吊销 access/refresh/session（Redis 黑名单）。"""
    from .decorators import require_jwt

    @require_jwt(strict=True)
    def _logout(req):
        auth_header = req.META.get("HTTP_AUTHORIZATION", "")
        access_token = auth_header.split(" ", 1)[1].strip() if auth_header.lower().startswith("bearer ") else ""
        refresh_token = None
        try:
            body = json.loads(req.body.decode("utf-8") or "{}")
            refresh_token = body.get("refresh")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        if access_token:
            blacklist_token(access_token, "access")
        if refresh_token:
            blacklist_token(refresh_token, "refresh")

        session_id = getattr(req, "bff_session_id", "") or ""
        if not session_id and getattr(req, "auth", None):
            try:
                session_id = str(req.auth.get("session_id") or "")
            except Exception:
                session_id = ""
        if session_id:
            revoke_session(session_id)

        log_auth_event("logout", getattr(req, "user", None), req)
        return bff_success({"message": "logged out"})

    return _logout(request)


@csrf_exempt
def bff_me(request):
    from .decorators import require_jwt

    @require_jwt(strict=True)
    def _me(req):
        user = req.user
        if not getattr(user, "is_authenticated", False):
            return bff_error("Authentication credentials were not provided.", 401)
        return bff_success(
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "role": get_user_role(user),
                "permissions": get_user_permissions(user),
            "app_ids": [app["id"] for app in available_apps(user)],
                "thread_id": user_thread_prefix(user),
            }
        )

    return _me(request)


@csrf_exempt
def bff_change_password(request):
    """已登录用户修改密码。"""
    from .decorators import require_jwt

    @require_jwt(strict=True)
    def _change(req):
        try:
            body = json.loads(req.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return bff_error("Invalid JSON", 400)

        old_password = body.get("old_password")
        new_password = body.get("new_password")
        if not old_password or not new_password:
            return bff_error("old_password and new_password are required", 400)

        user = req.user
        if not user.check_password(old_password):
            return bff_error("原密码不正确", 400)

        try:
            validate_password(new_password, user)
        except ValidationError as exc:
            return bff_error("; ".join(exc.messages), 400)

        user.set_password(new_password)
        user.save(update_fields=["password"])
        log_auth_event("change_password", user, req)
        return bff_success({"message": "password updated, please login again"})

    return _change(request)
