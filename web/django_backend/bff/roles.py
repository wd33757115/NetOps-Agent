# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Django 用户角色（Group 映射）。"""

from __future__ import annotations

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"

ROLE_PRIORITY = {
    ROLE_ADMIN: 3,
    ROLE_OPERATOR: 2,
    ROLE_VIEWER: 1,
}

ALL_ROLES = (ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)
APP_GRANTS_MANAGE = "apps.manage"
SYSTEM_GROUPS = ("admin", "netops-config")


def get_user_role(user) -> str:
    """从 Django Group 解析角色，优先级 admin > operator > viewer。"""
    if not user or not getattr(user, "is_authenticated", False):
        return ROLE_OPERATOR
    if user.is_superuser:
        return ROLE_ADMIN
    group_names = {g.name.lower() for g in user.groups.all()}
    if "admin" in group_names:
        return ROLE_ADMIN
    if "operator" in group_names:
        return ROLE_OPERATOR
    if "viewer" in group_names:
        return ROLE_VIEWER
    return ROLE_OPERATOR


def user_thread_prefix(user) -> str:
    return f"user-{user.id}"


def assign_user_role(user, role: str) -> None:
    """将用户绑定到单一角色 Group。"""
    from django.contrib.auth.models import Group

    role = (role or ROLE_OPERATOR).lower()
    if role not in ALL_ROLES:
        raise ValueError(f"invalid role: {role}")
    for group_name in SYSTEM_GROUPS:
        Group.objects.get_or_create(name=group_name)
    user.groups.remove(*user.groups.filter(name__in=ALL_ROLES))
    if role == ROLE_ADMIN:
        user.groups.add(Group.objects.get(name=ROLE_ADMIN))
    user.is_staff = role == ROLE_ADMIN
    user.is_superuser = role == ROLE_ADMIN


def count_active_admins(*, exclude_user_id: int | None = None) -> int:
    """统计当前启用的 admin 账号数量。"""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    qs = User.objects.filter(is_active=True)
    if exclude_user_id is not None:
        qs = qs.exclude(pk=exclude_user_id)
    return sum(1 for user in qs if get_user_role(user) == ROLE_ADMIN)


def get_user_permissions(user) -> list[str]:
    from src.auth.netops_access import NETOPS_CONFIGURE, NETOPS_CONFIG_GROUP
    if not user or not getattr(user, "is_authenticated", False):
        return []
    if get_user_role(user) == ROLE_ADMIN:
        return [NETOPS_CONFIGURE, APP_GRANTS_MANAGE]
    if user.groups.filter(name=NETOPS_CONFIG_GROUP).exists():
        return [APP_GRANTS_MANAGE]
    return []


def assign_netops_configuration(user, enabled: bool) -> None:
    from django.contrib.auth.models import Group
    from src.auth.netops_access import NETOPS_CONFIG_GROUP
    group, _ = Group.objects.get_or_create(name=NETOPS_CONFIG_GROUP)
    if enabled:
        user.groups.add(group)
    else:
        user.groups.remove(group)


def can_manage_app_grants(user):
    return bool(user and user.is_authenticated and user.is_active and APP_GRANTS_MANAGE in get_user_permissions(user))
