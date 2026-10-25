# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Django 角色 → Skill 权限级别映射。"""

from __future__ import annotations

ROLE_PERMISSION_MAP = {
    "admin": "admin",
    "operator": "power_user",
    "viewer": "guest",
}

# 需要 admin 的 FastAPI/BFF 路由前缀或操作
ADMIN_ONLY_PATH_PREFIXES = (
    "/api/v1/skills/reload",
    "/api/knowledge/reindex",
)


def role_to_permission_level(role: str) -> str:
    return ROLE_PERMISSION_MAP.get((role or "").lower(), "user")


def normalize_role(role: str | None) -> str:
    r = (role or "operator").lower()
    if r in ROLE_PERMISSION_MAP:
        return r
    return "operator"


def require_roles(user_role: str, allowed: list[str]) -> bool:
    return normalize_role(user_role) in {normalize_role(r) for r in allowed}
