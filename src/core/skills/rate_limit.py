# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill 执行限流（Redis 固定窗口）。"""

from __future__ import annotations

import logging
import time

from src.auth.token_store import get_redis
from src.common.config import get_settings

logger = logging.getLogger(__name__)


def _minute_bucket() -> str:
    return str(int(time.time()) // 60)


def check_skill_rate_limit(user_id: str | None, skill_name: str) -> tuple[bool, str]:
    """
    检查用户级 + Skill 全局级限流。
    返回 (allowed, message)。
    """
    settings = get_settings()
    if not settings.SKILL_RATE_LIMIT_ENABLED:
        return True, ""

    client = get_redis()
    if not client:
        return True, ""

    bucket = _minute_bucket()
    try:
        if user_id and settings.SKILL_RATE_LIMIT_PER_USER > 0:
            key = f"rate:skill_exec:user:{user_id}:{bucket}"
            count = client.incr(key)
            if count == 1:
                client.expire(key, 120)
            if count > settings.SKILL_RATE_LIMIT_PER_USER:
                return False, f"用户 Skill 执行频率超限（{settings.SKILL_RATE_LIMIT_PER_USER}/分钟）"

        if settings.SKILL_RATE_LIMIT_PER_SKILL > 0:
            key = f"rate:skill_exec:skill:{skill_name}:{bucket}"
            count = client.incr(key)
            if count == 1:
                client.expire(key, 120)
            if count > settings.SKILL_RATE_LIMIT_PER_SKILL:
                return False, f"Skill `{skill_name}` 全局频率超限（{settings.SKILL_RATE_LIMIT_PER_SKILL}/分钟）"
    except Exception as exc:
        logger.debug("rate_limit_check_degraded: %s", exc)
        return True, ""

    return True, ""
