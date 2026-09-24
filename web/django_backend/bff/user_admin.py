# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Admin-only 账户管理 API。"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt

from .audit import log_auth_event
from .decorators import require_jwt, require_role
from .response import bff_error, bff_success
from .roles import get_user_permissions, assign_netops_configuration, ALL_ROLES, ROLE_ADMIN, ROLE_OPERATOR, assign_user_role, count_active_admins, get_user_role


def _serialize_user(user) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email or "",
        "role": get_user_role(user),
        "is_active": user.is_active,
        "permissions": get_user_permissions(user),
        "netops_configure": user.groups.filter(name="netops-config").exists(),
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "date_joined": user.date_joined.isoformat() if user.date_joined else None,
    }


def _parse_body(request) -> dict:
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
        return body if isinstance(body, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _would_remove_last_admin(user, *, new_role: str | None = None, deactivate: bool = False) -> bool:
    if get_user_role(user) != ROLE_ADMIN:
        return False
    if deactivate or (new_role and new_role.lower() != ROLE_ADMIN):
        return count_active_admins(exclude_user_id=user.id) == 0
    return False


def _can_delete_user(actor, target) -> str | None:
    """返回不可删除的原因；None 表示允许删除。"""
    if target.id == actor.id:
        return "不能删除当前登录账号"
    if target.username.lower() == "admin":
        return "系统保留账号 admin 不可删除"
    if get_user_role(target) == ROLE_ADMIN and count_active_admins(exclude_user_id=target.id) == 0:
        return "不能删除最后一个 admin 账号"
    return None


@csrf_exempt
@require_jwt(strict=True)
@require_role("admin")
def bff_users_list_or_create(request):
    User = get_user_model()

    if request.method == "GET":
        users = User.objects.all().order_by("id")
        return bff_success([_serialize_user(user) for user in users])

    if request.method != "POST":
        return bff_error("Method not allowed", 405)

    body = _parse_body(request)
    username = (body.get("username") or "").strip()
    password = body.get("password")
    role = (body.get("role") or ROLE_OPERATOR).lower()
    email = (body.get("email") or "").strip()

    if not username or not password:
        return bff_error("username and password are required", 400)
    if role not in ALL_ROLES:
        return bff_error(f"role must be one of: {', '.join(ALL_ROLES)}", 400)
    if User.objects.filter(username=username).exists():
        return bff_error("username already exists", 400)

    grant = body.get("netops_configure", False)
    if not isinstance(grant, bool) or (grant and role == "viewer"):
        return bff_error("配置授权必须为布尔值，且仅可授予 operator 或 admin", 400)
    user = User(username=username, email=email or f"{username}@local")
    try:
        validate_password(password, user)
    except ValidationError as exc:
        return bff_error("; ".join(exc.messages), 400)

    user.set_password(password)
    user.is_active = True
    user.save()
    assign_user_role(user, role)
    assign_netops_configuration(user, grant)
    user.save()

    log_auth_event(
        "user_create",
        request.user,
        request,
        detail={"target_user_id": user.id, "target_username": username, "role": role},
    )
    return bff_success(_serialize_user(user), status=201)


@csrf_exempt
@require_jwt(strict=True)
@require_role("admin")
@transaction.atomic
def bff_user_detail(request, user_id: int):
    User = get_user_model()
    if request.method in {"PATCH", "DELETE"}:
        list(User.objects.select_for_update().order_by("pk"))

    if request.method == "GET":
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return bff_error("user not found", 404)
        return bff_success(_serialize_user(user))

    if request.method == "DELETE":
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return bff_error("user not found", 404)

        reason = _can_delete_user(request.user, user)
        if reason:
            return bff_error(reason, 400)

        username = user.username
        user_id_deleted = user.id
        user.delete()
        log_auth_event(
            "user_delete",
            request.user,
            request,
            detail={"target_user_id": user_id_deleted, "target_username": username},
        )
        return bff_success({"message": "user deleted successfully"})

    if request.method != "PATCH":
        return bff_error("Method not allowed", 405)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return bff_error("user not found", 404)

    body = _parse_body(request)
    updates: list[str] = []
    # Reject invalid account-state changes before assign_user_role writes groups.
    # A rejected combined "change role + disable self" must not partially apply.
    if "is_active" in body:
        if type(body["is_active"]) is not bool:
            return bff_error("启用状态必须为布尔值", 400)
        if not body["is_active"] and user.id == request.user.id:
            return bff_error("不能禁用当前登录账号", 400)
        if _would_remove_last_admin(user, deactivate=not body["is_active"]):
            return bff_error("不能禁用最后一个 admin 账号", 400)
    grant = body.get("netops_configure")
    if "netops_configure" in body and (not isinstance(grant, bool) or (grant and body.get("role", get_user_role(user)) == "viewer")):
        return bff_error("配置授权必须为布尔值，且仅可授予 operator 或 admin", 400)

    if "email" in body:
        user.email = (body.get("email") or "").strip()
        updates.append("email")

    if "role" in body:
        role = (body.get("role") or "").lower()
        if role not in ALL_ROLES:
            return bff_error(f"role must be one of: {', '.join(ALL_ROLES)}", 400)
        if _would_remove_last_admin(user, new_role=role):
            return bff_error("不能移除最后一个 admin 账号", 400)
        assign_user_role(user, role)
        updates.append("role")

    if "is_active" in body:
        is_active = bool(body.get("is_active"))
        if not is_active and user.id == request.user.id:
            return bff_error("不能禁用当前登录账号", 400)
        if _would_remove_last_admin(user, deactivate=not is_active):
            return bff_error("不能禁用最后一个 admin 账号", 400)
        user.is_active = is_active
        updates.append("is_active")

    if "netops_configure" in body:
        assign_netops_configuration(user, grant)
        updates.append("netops_configure")
    if body.get("role") == "viewer":
        assign_netops_configuration(user, False)

    if not updates:
        return bff_error("no valid fields to update", 400)

    user.save()
    log_auth_event(
        "user_update",
        request.user,
        request,
        detail={"target_user_id": user.id, "fields": updates, "netops_configure": grant},
    )
    return bff_success(_serialize_user(user))


@csrf_exempt
@require_jwt(strict=True)
@require_role("admin")
def bff_user_reset_password(request, user_id: int):
    if request.method != "POST":
        return bff_error("Method not allowed", 405)

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return bff_error("user not found", 404)

    body = _parse_body(request)
    new_password = body.get("new_password") or body.get("password")
    if not new_password:
        return bff_error("new_password is required", 400)

    try:
        validate_password(new_password, user)
    except ValidationError as exc:
        return bff_error("; ".join(exc.messages), 400)

    user.set_password(new_password)
    user.save(update_fields=["password"])

    log_auth_event(
        "admin_reset_password",
        request.user,
        request,
        detail={"target_user_id": user.id, "target_username": user.username},
    )
    return bff_success({"message": "password reset successfully"})
