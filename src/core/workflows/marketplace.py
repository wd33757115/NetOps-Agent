# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 模板市场。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.core.workflows.manager import generate_from_collab_template, list_collab_templates
from src.core.workflows.metadata_repo import get_plugin_metadata
from src.core.workflows.versioning import export_plugin_bundle
from src.infrastructure.db.models import WorkflowMarketTemplate
from src.infrastructure.db.postgres import get_db_session

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_builtin_market_templates() -> int:
    """将内置模板写入市场（幂等）。"""
    created = 0
    builtin = list_collab_templates()
    with get_db_session() as db:
        # Built-in templates follow the code: one the code no longer ships leaves the market.
        db.query(WorkflowMarketTemplate).filter(
            WorkflowMarketTemplate.created_by == "system",
            WorkflowMarketTemplate.id.notin_([tpl["id"] for tpl in builtin]),
        ).delete(synchronize_session=False)
        for tpl in builtin:
            existing = (
                db.query(WorkflowMarketTemplate)
                .filter(WorkflowMarketTemplate.id == tpl["id"])
                .first()
            )
            if existing:
                continue
            files = generate_from_collab_template(tpl["id"]) or {}
            if not files:
                continue
            row = WorkflowMarketTemplate(
                id=tpl["id"],
                title=tpl["title"],
                description=tpl["description"],
                category=tpl.get("category") or "itsm",
                tags=["builtin"],
                files=files,
                featured=True,
                is_public=True,
                created_by="system",
            )
            db.add(row)
            created += 1
        db.flush()
    if created:
        logger.info("已初始化 %s 个内置市场模板", created)
    return created


def list_market_templates(*, category: str | None = None, featured_only: bool = False) -> list[dict[str, Any]]:
    ensure_builtin_market_templates()
    with get_db_session() as db:
        q = db.query(WorkflowMarketTemplate).filter(WorkflowMarketTemplate.is_public.is_(True))
        if category:
            q = q.filter(WorkflowMarketTemplate.category == category)
        if featured_only:
            q = q.filter(WorkflowMarketTemplate.featured.is_(True))
        rows = q.order_by(WorkflowMarketTemplate.featured.desc(), WorkflowMarketTemplate.use_count.desc()).all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "description": r.description,
                "category": r.category,
                "tags": r.tags or [],
                "source_plugin_name": r.source_plugin_name,
                "featured": r.featured,
                "use_count": r.use_count,
                "created_by": r.created_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "file_keys": list((r.files or {}).keys()),
            }
            for r in rows
        ]


def get_market_template(template_id: str) -> dict[str, Any] | None:
    ensure_builtin_market_templates()
    with get_db_session() as db:
        row = db.query(WorkflowMarketTemplate).filter(WorkflowMarketTemplate.id == template_id).first()
        if not row:
            return None
        return {
            "id": row.id,
            "title": row.title,
            "description": row.description,
            "category": row.category,
            "tags": row.tags or [],
            "files": row.files or {},
            "source_plugin_name": row.source_plugin_name,
            "featured": row.featured,
            "use_count": row.use_count,
        }


def increment_market_use(template_id: str) -> None:
    with get_db_session() as db:
        row = db.query(WorkflowMarketTemplate).filter(WorkflowMarketTemplate.id == template_id).first()
        if row:
            row.use_count = (row.use_count or 0) + 1


def publish_plugin_to_market(
    plugin_name: str,
    *,
    title: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """将已发布插件加入模板市场。"""
    meta = get_plugin_metadata(plugin_name)
    if meta and meta.status != "published":
        return {"success": False, "message": "仅 published 状态的插件可发布到市场"}

    bundle = export_plugin_bundle(plugin_name)
    template_id = f"market-{plugin_name}"

    with get_db_session() as db:
        row = db.query(WorkflowMarketTemplate).filter(WorkflowMarketTemplate.id == template_id).first()
        if row:
            row.title = title or plugin_name
            row.description = (meta.description if meta else "") or ""
            row.files = bundle["files"]
            row.source_plugin_name = plugin_name
        else:
            row = WorkflowMarketTemplate(
                id=template_id,
                title=title or plugin_name,
                description=meta.description if meta else "",
                category=meta.category if meta else "itsm",
                tags=["user-published"],
                files=bundle["files"],
                source_plugin_name=plugin_name,
                featured=False,
                is_public=True,
                created_by=user_id,
            )
            db.add(row)
        db.flush()

    return {"success": True, "template_id": template_id, "message": "已发布到模板市场"}
