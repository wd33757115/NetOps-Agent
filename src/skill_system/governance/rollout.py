# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill 版本治理与灰度发布（enabled_ratio）。"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from src.common.config import get_settings
from src.skill_system.catalog.repository import get_catalog_entry

logger = logging.getLogger(__name__)

ROLLOUT_DRAFT = "draft"
ROLLOUT_CANARY = "canary"
ROLLOUT_STABLE = "stable"
ROLLOUT_DEPRECATED = "deprecated"


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for segment in str(value).replace("-", ".").split("."):
        digits = "".join(ch for ch in segment if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or (0,))


def _platform_ok(entry: dict[str, Any]) -> bool:
    required = entry.get("min_platform_version")
    if not required:
        return True
    current = get_settings().PLATFORM_VERSION
    return _version_tuple(current) >= _version_tuple(str(required))


def in_rollout_cohort(user_id: str | None, skill_name: str, enabled_ratio_pct: int) -> bool:
    """确定性灰度：同一 user+skill 始终落在同一桶。"""
    if enabled_ratio_pct >= 100:
        return True
    if enabled_ratio_pct <= 0:
        return False
    if not user_id:
        return enabled_ratio_pct >= 50
    digest = hashlib.sha256(f"{user_id}:{skill_name}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < enabled_ratio_pct


def is_skill_available(entry: dict[str, Any] | None, *, user_id: str | None = None) -> tuple[bool, str]:
    """判断 Skill 是否可路由/可执行。"""
    if entry is None:
        return True, ""

    if not entry.get("enabled", True):
        return False, f"Skill `{entry.get('skill_name')}` 已禁用"
    if entry.get("deprecated"):
        return False, f"Skill `{entry.get('skill_name')}` 已废弃"
    if not _platform_ok(entry):
        return False, f"Skill `{entry.get('skill_name')}` 需要更高平台版本"

    status = str(entry.get("rollout_status") or ROLLOUT_STABLE).lower()
    if status == ROLLOUT_DRAFT:
        return False, f"Skill `{entry.get('skill_name')}` 处于 draft，未开放"
    if status == ROLLOUT_DEPRECATED:
        return False, f"Skill `{entry.get('skill_name')}` 已下线"

    ratio = int(entry.get("enabled_ratio") if entry.get("enabled_ratio") is not None else 100)
    if status == ROLLOUT_CANARY or ratio < 100:
        if not in_rollout_cohort(user_id, str(entry.get("skill_name")), ratio):
            return False, f"Skill `{entry.get('skill_name')}` 未命中灰度 ({ratio}%)"
    return True, ""


def is_skill_executable(skill_name: str, *, user_id: str | None = None) -> tuple[bool, str]:
    settings = get_settings()
    if not settings.SKILL_GOVERNANCE_ENABLED:
        return True, ""
    return is_skill_available(get_catalog_entry(skill_name), user_id=user_id)


def is_skill_routable(skill_name: str, *, user_id: str | None = None) -> bool:
    ok, _ = is_skill_executable(skill_name, user_id=user_id)
    return ok
