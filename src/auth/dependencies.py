# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""FastAPI 认证依赖。"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from src.auth.models import CurrentUser
from src.auth.rbac import require_roles
from src.auth.security import resolve_current_user
from src.gateway.bff_security import is_enforce_bff_origin_enabled


async def get_optional_user(request: Request) -> CurrentUser | None:
    user = resolve_current_user(request.headers)
    if user:
        request.state.current_user = user
    return user


async def get_current_user(request: Request) -> CurrentUser:
    user = resolve_current_user(request.headers)
    if user:
        request.state.current_user = user
        return user
    if is_enforce_bff_origin_enabled():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或 BFF 未注入用户上下文",
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录，请先通过 Django BFF 登录",
    )


def require_role(allowed_roles: list[str]):
    """工厂：限制角色访问。"""

    async def _dependency(request: Request) -> CurrentUser:
        user = await get_current_user(request)
        if not require_roles(user.role, allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"角色 {user.role} 无权访问此资源",
            )
        return user

    return _dependency


async def require_netops_access(request: Request) -> CurrentUser:
    from src.auth.netops_access import READ_METHODS, requires_netops_configuration
    user = await get_current_user(request)
    if requires_netops_configuration(request.method, request.url.path):
        if not user.can_configure_netops():
            raise HTTPException(status_code=403, detail="需要设备与规则配置权限，请联系管理员授权")
    elif request.method not in READ_METHODS and not user.can_execute_skills():
        raise HTTPException(status_code=403, detail="当前角色仅可查看巡检")
    return user
