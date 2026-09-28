# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill Catalog 同步与批量 Embedding 索引。"""

from __future__ import annotations

import logging
from typing import Any

from src.common.config import get_settings
from src.infrastructure.embedding.embedder import encode_batch, encode_text
from src.skill_system.catalog.repository import (
    compute_content_hash,
    list_catalog_entries,
    list_entries_needing_embedding,
    save_embedding,
    prune_missing_file_entries,
    upsert_catalog_entry,
)
from src.skill_system.metadata import SkillMetadata

logger = logging.getLogger(__name__)


def _catalog_embed_text(record: dict[str, Any]) -> str:
    tags = " ".join(record.get("tags") or [])
    triggers = " ".join(record.get("triggers") or [])
    return f"{record['skill_name']}: {record.get('description', '')} {tags} {triggers}".strip()


class SkillCatalogService:
    """Skill Catalog 同步 / 索引 / 内存检索。"""

    _memory_index: dict[str, dict[str, Any]] = {}

    @classmethod
    def sync_from_metadata(cls, skills: list[SkillMetadata]) -> dict[str, int]:
        synced = 0
        for meta in skills:
            if upsert_catalog_entry(meta):
                synced += 1
        removed = prune_missing_file_entries()
        cls.refresh_memory_index()
        return {"synced": synced, "total": len(skills), "removed": removed}

    @classmethod
    def index_embeddings(cls, *, force: bool = False) -> dict[str, int]:
        settings = get_settings()
        if force:
            entries = list_catalog_entries(enabled_only=True)
        else:
            entries = list_entries_needing_embedding()
        if not entries:
            cls.refresh_memory_index()
            return {"indexed": 0, "skipped": 0}

        texts = [_catalog_embed_text(e) for e in entries]
        vectors = encode_batch(texts)
        indexed = 0
        for entry, vector in zip(entries, vectors):
            if not vector:
                continue
            if save_embedding(
                entry["skill_name"],
                model=settings.EMBEDDING_MODEL,
                vector=vector,
            ):
                indexed += 1
        cls.refresh_memory_index()
        logger.info("skill_catalog_indexed count=%s force=%s", indexed, force)
        return {"indexed": indexed, "pending": max(len(entries) - indexed, 0)}

    @classmethod
    def refresh_memory_index(cls) -> None:
        cls._memory_index = {
            row["skill_name"]: row
            for row in list_catalog_entries(enabled_only=True)
            if row.get("embedding_vector")
        }

    @classmethod
    def semantic_search(
        cls,
        query: str,
        *,
        top_k: int = 5,
        allowed_skills: set[str] | None = None,
        min_score: float | None = None,
    ) -> list[tuple[str, float]]:
        if not cls._memory_index:
            cls.refresh_memory_index()
        if not cls._memory_index:
            return []

        settings = get_settings()
        min_score = min_score if min_score is not None else settings.SKILL_CATALOG_SEMANTIC_MIN_SCORE
        query_vec = encode_text(query)
        if not query_vec:
            return []

        from src.infrastructure.embedding.embedder import cosine_similarity

        scores: list[tuple[str, float]] = []
        for skill_name, record in cls._memory_index.items():
            if allowed_skills is not None and skill_name not in allowed_skills:
                continue
            vec = record.get("embedding_vector") or []
            sim = cosine_similarity(query_vec, vec)
            if sim >= min_score:
                scores.append((skill_name, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    @classmethod
    def get_stats(cls) -> dict[str, Any]:
        all_rows = list_catalog_entries(enabled_only=False)
        with_vec = sum(1 for r in all_rows if r.get("embedding_vector"))
        return {
            "total": len(all_rows),
            "enabled": sum(1 for r in all_rows if r.get("enabled")),
            "indexed": with_vec,
            "memory_cached": len(cls._memory_index),
        }


def sync_and_index(skills: list[SkillMetadata], *, index: bool = True) -> dict[str, Any]:
    stats = SkillCatalogService.sync_from_metadata(skills)
    if index and get_settings().SKILL_CATALOG_ENABLED:
        stats.update(SkillCatalogService.index_embeddings())
    return stats
