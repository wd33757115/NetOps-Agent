# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill Catalog 持久化。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from src.infrastructure.db.postgres import get_db_session
from src.skill_system.metadata import SkillMetadata

logger = logging.getLogger(__name__)


def prune_missing_file_entries() -> int:
    """Drop obsolete local discovery entries, retaining execution history and external Skills."""
    from src.infrastructure.db.models import SkillCatalogRecord

    root = Path(__file__).resolve().parents[2] / 'skills'
    with get_db_session() as session:
        removed = 0
        for row in session.query(SkillCatalogRecord).all():
            directory = Path(row.skill_path).resolve() if row.skill_path else None
            if directory and directory.parent == root and not (directory / 'SKILL.md').is_file():
                session.delete(row)
                removed += 1
        return removed


def _catalog_text(meta: SkillMetadata) -> str:
    triggers = " ".join(meta.triggers or [])
    tags = " ".join(meta.tags or [])
    return f"{meta.name}: {meta.description} {tags} {triggers}".strip()


def compute_content_hash(meta: SkillMetadata) -> str:
    raw = f"{meta.version}|{_catalog_text(meta)}|{meta.domain}|{meta.category}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upsert_catalog_entry(meta: SkillMetadata) -> bool:
    try:
        from src.infrastructure.db.models import SkillCatalogRecord

        record = SkillCatalogRecord(
            skill_name=meta.name,
            version=meta.version,
            description=meta.description,
            category=meta.category,
            domain=meta.domain or meta.category or "default",
            tags=list(meta.tags or []),
            triggers=list(meta.triggers or []),
            enabled=bool(meta.enabled),
            hidden=bool(meta.hidden),
            deprecated=bool(meta.deprecated),
            min_permission_level=str(meta.min_permission_level or "user").lower(),
            celery_queue=meta.celery_queue,
            skill_path=meta.skill_path,
            content_hash=compute_content_hash(meta),
            updated_at=datetime.now(timezone.utc),
        )
        if meta.rollout_status is not None:
            record.rollout_status = str(meta.rollout_status).lower()
        if meta.enabled_ratio is not None:
            record.enabled_ratio = max(0, min(100, int(float(meta.enabled_ratio) * 100 if meta.enabled_ratio <= 1 else meta.enabled_ratio)))
        if meta.min_platform_version is not None:
            record.min_platform_version = meta.min_platform_version
        with get_db_session() as session:
            existing = session.get(SkillCatalogRecord, meta.name)
            if existing:
                for field in (
                    "version", "description", "category", "domain", "tags", "triggers",
                    "enabled", "hidden", "deprecated", "min_permission_level",
                    "celery_queue", "skill_path", "content_hash", "updated_at",
                ):
                    setattr(existing, field, getattr(record, field))
                if meta.rollout_status is not None:
                    existing.rollout_status = record.rollout_status
                if meta.enabled_ratio is not None:
                    existing.enabled_ratio = record.enabled_ratio
                if meta.min_platform_version is not None:
                    existing.min_platform_version = record.min_platform_version
                if existing.content_hash != record.content_hash:
                    existing.embedding_vector = None
                    existing.indexed_at = None
            else:
                session.add(record)
        return True
    except Exception as exc:
        logger.warning("upsert_catalog_entry failed skill=%s: %s", meta.name, exc)
        return False


def list_catalog_entries(*, enabled_only: bool = True) -> list[dict[str, Any]]:
    try:
        from src.infrastructure.db.models import SkillCatalogRecord

        with get_db_session() as session:
            q = session.query(SkillCatalogRecord)
            if enabled_only:
                q = q.filter(
                    SkillCatalogRecord.enabled.is_(True),
                    SkillCatalogRecord.deprecated.is_(False),
                    SkillCatalogRecord.hidden.is_(False),
                )
            rows = q.all()
            for row in rows:
                session.expunge(row)
            return [_row_to_dict(row) for row in rows]
    except Exception as exc:
        logger.warning("list_catalog_entries failed: %s", exc)
        return []


def get_catalog_entry(skill_name: str) -> dict[str, Any] | None:
    try:
        from src.infrastructure.db.models import SkillCatalogRecord

        with get_db_session() as session:
            row = session.get(SkillCatalogRecord, skill_name)
            if not row:
                return None
            session.expunge(row)
            return _row_to_dict(row)
    except Exception as exc:
        logger.warning("get_catalog_entry failed skill=%s: %s", skill_name, exc)
        return None


def list_entries_needing_embedding() -> list[dict[str, Any]]:
    try:
        from src.infrastructure.db.models import SkillCatalogRecord

        with get_db_session() as session:
            rows = (
                session.query(SkillCatalogRecord)
                .filter(
                    SkillCatalogRecord.enabled.is_(True),
                    SkillCatalogRecord.deprecated.is_(False),
                    SkillCatalogRecord.embedding_vector.is_(None),
                )
                .all()
            )
            for row in rows:
                session.expunge(row)
            return [_row_to_dict(row) for row in rows]
    except Exception as exc:
        logger.warning("list_entries_needing_embedding failed: %s", exc)
        return []


def save_embedding(skill_name: str, *, model: str, vector: list[float]) -> bool:
    try:
        from src.infrastructure.db.models import SkillCatalogRecord

        with get_db_session() as session:
            row = session.get(SkillCatalogRecord, skill_name)
            if not row:
                return False
            row.embedding_model = model
            row.embedding_vector = vector
            row.indexed_at = datetime.now(timezone.utc)
        return True
    except Exception as exc:
        logger.warning("save_embedding failed skill=%s: %s", skill_name, exc)
        return False


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "skill_name": row.skill_name,
        "version": row.version,
        "description": row.description,
        "category": row.category,
        "domain": row.domain,
        "tags": row.tags or [],
        "triggers": row.triggers or [],
        "enabled": row.enabled,
        "hidden": row.hidden,
        "deprecated": row.deprecated,
        "min_permission_level": row.min_permission_level,
        "celery_queue": row.celery_queue,
        "skill_path": row.skill_path,
        "content_hash": row.content_hash,
        "embedding_model": row.embedding_model,
        "embedding_vector": row.embedding_vector,
        "indexed_at": row.indexed_at.isoformat() if row.indexed_at else None,
        "rollout_status": row.rollout_status,
        "enabled_ratio": row.enabled_ratio,
        "min_platform_version": row.min_platform_version,
    }


def update_catalog_rollout(
    skill_name: str,
    *,
    rollout_status: str | None = None,
    enabled_ratio: int | None = None,
    min_platform_version: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    try:
        from src.infrastructure.db.models import SkillCatalogRecord

        with get_db_session() as session:
            row = session.get(SkillCatalogRecord, skill_name)
            if not row:
                return None
            if rollout_status is not None:
                row.rollout_status = rollout_status.lower()
            if enabled_ratio is not None:
                row.enabled_ratio = max(0, min(100, int(enabled_ratio)))
            if min_platform_version is not None:
                row.min_platform_version = min_platform_version or None
            if enabled is not None:
                row.enabled = enabled
            row.updated_at = datetime.now(timezone.utc)
            payload = _row_to_dict(row)
        return payload
    except Exception as exc:
        logger.warning("update_catalog_rollout failed skill=%s: %s", skill_name, exc)
        return None
