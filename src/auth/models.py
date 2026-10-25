# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""认证与 RBAC 数据模型（Pydantic）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CurrentUser(BaseModel):
    """FastAPI 请求上下文中的当前用户。"""

    user_id: str
    username: str
    role: str = Field(default="operator", description="admin | operator | viewer")
    permissions: list[str] = Field(default_factory=list)
    thread_prefix: str | None = None
    session_id: str | None = None

    def skill_permission_level(self) -> str:
        from src.auth.rbac import role_to_permission_level

        return role_to_permission_level(self.role)

    def can_execute_skills(self) -> bool:
        return self.role in {"admin", "operator"}

    def can_configure_netops(self) -> bool:
        from src.auth.netops_access import NETOPS_CONFIGURE
        return self.is_admin() or NETOPS_CONFIGURE in self.permissions

    def is_admin(self) -> bool:
        return self.role == "admin"

    def can_view_trace_detail(self) -> bool:
        """admin/operator 可见完整执行步骤；viewer 仅摘要。"""
        return self.role in {"admin", "operator"}
