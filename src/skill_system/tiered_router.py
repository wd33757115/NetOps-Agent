# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill 分级路由（L1 触发词 → L2 域/权限 → L3 Catalog 语义 → L4 LLM Judge）。"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.common.config import get_settings
from src.skill_system.catalog.service import SkillCatalogService
from src.skill_system.router import SkillMatch

logger = logging.getLogger(__name__)

_PERMISSION_RANK = {
    "guest": 0,
    "user": 1,
    "operator": 2,
    "power_user": 3,
    "admin": 4,
}


def permission_rank(role: str | None) -> int:
    return _PERMISSION_RANK.get((role or "user").lower(), 1)


def catalog_allowed_skills(user_role: str | None = None, user_id: str | None = None) -> set[str] | None:
    """L2：按 Catalog enabled + RBAC + 灰度 过滤；Catalog 不可用时返回 None（不限制）。"""
    settings = get_settings()
    if not settings.SKILL_CATALOG_ENABLED:
        return None
    from src.skill_system.catalog.repository import list_catalog_entries
    from src.skill_system.governance.rollout import is_skill_available

    rows = list_catalog_entries(enabled_only=True)
    if not rows:
        return None
    user_rank = permission_rank(user_role)
    allowed: set[str] = set()
    for row in rows:
        required = permission_rank(str(row.get("min_permission_level") or "user"))
        if user_rank < required:
            continue
        ok, _ = is_skill_available(row, user_id=user_id)
        if ok:
            allowed.add(row["skill_name"])
    return allowed


def l2_expand_candidates(query: str, allowed: set[str] | None) -> set[str]:
    """L2：query 中出现 domain/tag/category 关键词时扩展候选集。"""
    from src.skill_system.catalog.repository import list_catalog_entries

    settings = get_settings()
    if not settings.SKILL_CATALOG_USE_TIERED_ROUTING:
        return allowed or set()

    q = query.lower()
    tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", q))
    expanded: set[str] = set(allowed or [])
    for row in list_catalog_entries(enabled_only=True):
        name = row["skill_name"]
        if allowed is not None and name not in allowed:
            continue
        domain = str(row.get("domain") or "").lower()
        category = str(row.get("category") or "").lower()
        if domain and domain in q:
            expanded.add(name)
        if category and category in q:
            expanded.add(name)
        for tag in row.get("tags") or []:
            if str(tag).lower() in tokens or str(tag).lower() in q:
                expanded.add(name)
    return expanded


def catalog_semantic_match(
    query: str,
    top_k: int,
    *,
    allowed_skills: set[str] | None = None,
) -> list[SkillMatch]:
    """L3：仅对 Catalog 预计算向量做语义检索（不对全量 Skill 实时 encode）。"""
    settings = get_settings()
    results = SkillCatalogService.semantic_search(
        query,
        top_k=top_k,
        allowed_skills=allowed_skills,
        min_score=settings.SKILL_CATALOG_SEMANTIC_MIN_SCORE,
    )
    return [
        SkillMatch(
            skill_name=skill_name,
            confidence=float(score),
            match_type="semantic",
            reason=f"Catalog 语义相似度: {score:.2f}",
        )
        for skill_name, score in results
    ]


def filter_matches_by_allowed(
    matches: list[SkillMatch],
    allowed: set[str] | None,
) -> list[SkillMatch]:
    if allowed is None:
        return matches
    return [m for m in matches if m.skill_name in allowed]
