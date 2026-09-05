# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""FastAPI 侧 BFF 内部请求校验。"""

from __future__ import annotations

import os
from collections.abc import Mapping

# 允许不经 BFF 直连的路径（外部 ITSM 等集成场景，可通过环境变量关闭）
_ITSM_DIRECT_PATHS = {
    "/api/v1/itsm/webhook",
    "/api/v1/itsm/webhook/firewall-policy",
    "/api/v1/itsm/webhook/callback",
}


def is_enforce_bff_origin_enabled() -> bool:
    """是否启用「仅允许 Django BFF 转发」策略。"""
    value = os.getenv("ENFORCE_BFF_ORIGIN", "").strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False

    # 未显式配置时：DEBUG=false 默认开启，DEBUG=true 默认关闭（便于本地直连调试）
    debug = os.getenv("DEBUG", "true").strip().lower() in {"true", "1", "t", "yes"}
    return not debug


def allow_direct_itsm_access() -> bool:
    """是否允许 ITSM webhook 绕过 BFF 校验（默认关闭，统一走 Django）。"""
    return os.getenv("ALLOW_DIRECT_ITSM", "").strip().lower() in {"true", "1", "yes", "on"}


def _get_header(headers: Mapping[str, str], name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return ""


def is_trusted_bff_request(headers: Mapping[str, str]) -> bool:
    """校验是否来自 Django BFF 的内部转发。"""
    forwarded_from = _get_header(headers, "X-Forwarded-From")
    internal_request = _get_header(headers, "X-Internal-Request")
    return forwarded_from == "django-bff" and internal_request.lower() == "true"


def is_bff_bypass_path(path: str) -> bool:
    """在启用 BFF 校验时，是否允许该路径直连 FastAPI。"""
    if not allow_direct_itsm_access():
        return False
    if path in _ITSM_DIRECT_PATHS:
        return True
    # 插件化 Webhook：/api/v1/itsm/webhook/{route_key}
    return path.startswith("/api/v1/itsm/webhook/")


def reject_envelope(*, request_id: str | None = None) -> dict:
    from src.core.exceptions import ErrorCode, error_envelope

    return error_envelope(
        code=ErrorCode.BFF_ORIGIN_REQUIRED,
        message="Access only allowed via Django BFF",
        request_id=request_id,
    )
