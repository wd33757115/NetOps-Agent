# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""BFF 审计（登录/登出等）。"""

from __future__ import annotations

import logging

logger = logging.getLogger("bff.audit")


def log_auth_event(action: str, user, request, *, status: str = "success", detail: dict | None = None) -> None:
    """记录认证事件；优先写入 FastAPI PostgreSQL 审计表。"""
    user_id = str(user.id) if user and getattr(user, "id", None) else None
    username = getattr(user, "username", None) if user else None
    ip = request.META.get("REMOTE_ADDR") if request else None
    payload = detail or {}
    logger.info("auth_event action=%s user=%s status=%s", action, username, status)
    try:
        from src.gateway.audit_service import write_audit_log

        write_audit_log(
            action=action,
            user_id=user_id,
            username=username,
            resource_type="auth",
            detail=payload,
            ip_address=ip,
            status=status,
        )
    except Exception as exc:
        logger.debug("audit write skipped: %s", exc)
