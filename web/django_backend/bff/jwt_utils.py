# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""BFF JWT 鉴权辅助（黑名单校验）。"""

from __future__ import annotations

from src.auth.token_store import is_auth_token_revoked


def token_is_revoked(token) -> bool:
    if token is None:
        return False
    try:
        jti = token.get("jti")
        session_id = token.get("session_id")
        return is_auth_token_revoked(jti, session_id)
    except Exception:
        return False


def attach_auth_context(request, user, token) -> None:
    """同步绑定当前数据库角色及权限；线程前缀可复用 JWT，避免异步视图查库。"""
    from .roles import get_user_role, get_user_permissions, user_thread_prefix

    request.user = user
    request.auth = token
    try:
        request.bff_session_id = str(token.get("session_id") or "")
    except Exception:
        request.bff_session_id = ""

    thread_prefix = None
    try:
        thread_prefix = token.get("thread_id")
    except Exception:
        pass

    # 在同步鉴权阶段解析角色（此处可安全访问 ORM）
    request.bff_user_role = get_user_role(user)
    request.bff_user_permissions = get_user_permissions(user)
    request.bff_user_thread_prefix = str(thread_prefix or user_thread_prefix(user))
