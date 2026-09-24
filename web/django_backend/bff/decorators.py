# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import asyncio
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from .jwt_utils import attach_auth_context, token_is_revoked
from .response import bff_error
from .roles import get_user_role


def _auth_required() -> bool:
    return getattr(settings, "BFF_REQUIRE_AUTH", not settings.DEBUG)


def _format_auth_error(detail) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail:
        return str(detail[0])
    return str(detail)


def _has_bearer_token(request) -> bool:
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if auth_header.lower().startswith("bearer "):
        return True
    token = request.GET.get("token") or request.GET.get("access_token")
    if token:
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return True
    return False


def _authenticate_request(request, *, required: bool = True) -> tuple | None | JsonResponse:
    """解析 JWT；required=False 时无 token 可匿名通过，有 token 则必须有效。"""
    has_bearer = _has_bearer_token(request)
    if not has_bearer:
        if required:
            return bff_error("Authentication credentials were not provided.", 401)
        return None

    jwt_auth = JWTAuthentication()
    try:
        auth_result = jwt_auth.authenticate(request)
    except AuthenticationFailed as exc:
        return bff_error(_format_auth_error(exc.detail), 401)

    if not auth_result:
        if required:
            return bff_error("Authentication credentials were not provided.", 401)
        return None

    user, token = auth_result
    if token_is_revoked(token):
        return bff_error("Token has been revoked", 401)
    attach_auth_context(request, user, token)
    return user, token


def _request_role(request) -> str | None:
    role = getattr(request, "bff_user_role", None)
    if role:
        return str(role)
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        return get_user_role(user)
    return None


def require_role(*allowed_roles: str):
    """BFF 角色校验（需配合 @require_jwt）。"""

    allowed = {r.lower() for r in allowed_roles}

    def decorator(view_func):
        if asyncio.iscoroutinefunction(view_func):

            @wraps(view_func)
            async def async_wrapper(request, *args, **kwargs):
                user = getattr(request, "user", None)
                if not user or not getattr(user, "is_authenticated", False):
                    return bff_error("Authentication credentials were not provided.", 401)
                role = _request_role(request)
                if role not in allowed and not getattr(user, "is_superuser", False):
                    return bff_error(f"角色 {role} 无权访问", 403)
                return await view_func(request, *args, **kwargs)

            return async_wrapper

        @wraps(view_func)
        def sync_wrapper(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if not user or not getattr(user, "is_authenticated", False):
                return bff_error("Authentication credentials were not provided.", 401)
            role = _request_role(request)
            if role not in allowed and not getattr(user, "is_superuser", False):
                return bff_error(f"角色 {role} 无权访问", 403)
            return view_func(request, *args, **kwargs)

        return sync_wrapper

    return decorator


_READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_role_with_viewer_readonly(*write_roles: str):
    """角色校验：write_roles 拥有全部方法；viewer 只读（GET/HEAD/OPTIONS）。

    审计场景（领导看巡检报告、规则资产状态）用 viewer 角色即可，
    不再被迫发放 operator 权限。
    """

    allowed_write = {r.lower() for r in write_roles}

    def _check(request):
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return bff_error("Authentication credentials were not provided.", 401)
        if getattr(user, "is_superuser", False):
            return None
        role = _request_role(request)
        if role in allowed_write:
            return None
        if role == "viewer":
            if request.method in _READ_ONLY_METHODS:
                return None
            return bff_error("viewer 角色仅有只读权限", 403)
        return bff_error(f"角色 {role} 无权访问", 403)

    def decorator(view_func):
        if asyncio.iscoroutinefunction(view_func):

            @wraps(view_func)
            async def async_wrapper(request, *args, **kwargs):
                denied = _check(request)
                if denied is not None:
                    return denied
                return await view_func(request, *args, **kwargs)

            return async_wrapper

        @wraps(view_func)
        def sync_wrapper(request, *args, **kwargs):
            denied = _check(request)
            if denied is not None:
                return denied
            return view_func(request, *args, **kwargs)

        return sync_wrapper

    return decorator


def require_jwt(view_func=None, *, strict: bool | None = None):
    """BFF JWT 鉴权：兼容同步/异步视图。

    strict=True  始终要求有效 JWT（账户管理等敏感接口）
    strict=None  跟随 BFF_REQUIRE_AUTH；为 false 时仍会在有 Bearer 时解析 JWT
    """

    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(request, *args, **kwargs):
                required = strict if strict is not None else _auth_required()
                auth_result = _authenticate_request(request, required=required)
                if isinstance(auth_result, JsonResponse):
                    return auth_result
                return await fn(request, *args, **kwargs)

            return async_wrapper

        @wraps(fn)
        def sync_wrapper(request, *args, **kwargs):
            required = strict if strict is not None else _auth_required()
            auth_result = _authenticate_request(request, required=required)
            if isinstance(auth_result, JsonResponse):
                return auth_result
            return fn(request, *args, **kwargs)

        return sync_wrapper

    if view_func is not None:
        return decorator(view_func)
    return decorator


def require_app_grants_manager(view_func):
    """Only admin and netops-config may delegate App usage; not account roles."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        from .roles import can_manage_app_grants
        if not can_manage_app_grants(getattr(request, 'user', None)):
            return bff_error('需要 admin 或 netops-config 用户组的应用授权管理权限', 403)
        return view_func(request, *args, **kwargs)
    return wrapper
