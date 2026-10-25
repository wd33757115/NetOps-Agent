# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 插件元数据与版本仓库。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from sqlalchemy.exc import OperationalError

from src.infrastructure.db.models import WorkflowMarketTemplate, WorkflowPluginMetadata, WorkflowPluginVersion
from src.infrastructure.db.postgres import get_db_session

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"draft", "review", "published", "archived"})

T = TypeVar("T")


def _read_or_fallback(read_fn: Callable[[], T], fallback: T) -> T:
    """只读元数据：数据库不可用时返回 fallback，便于 Code-First / 无 PG 环境。"""
    try:
        return read_fn()
    except OperationalError as exc:
        logger.debug("Workflow 元数据读失败（数据库不可用）: %s", exc)
        return fallback


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_plugin_metadata(name: str) -> WorkflowPluginMetadata | None:
    def _read() -> WorkflowPluginMetadata | None:
        with get_db_session() as db:
            row = db.query(WorkflowPluginMetadata).filter(WorkflowPluginMetadata.name == name).first()
            if row:
                db.expunge(row)
            return row

    return _read_or_fallback(_read, None)


def list_plugin_metadata() -> list[WorkflowPluginMetadata]:
    def _read() -> list[WorkflowPluginMetadata]:
        with get_db_session() as db:
            rows = db.query(WorkflowPluginMetadata).order_by(WorkflowPluginMetadata.updated_at.desc()).all()
            for row in rows:
                db.expunge(row)
            return rows

    return _read_or_fallback(_read, [])


def metadata_to_dict(row: WorkflowPluginMetadata | None, *, default_status: str = "published") -> dict[str, Any]:
    if not row:
        return {"status": default_status, "current_version": 0}
    return {
        "status": row.status,
        "category": row.category,
        "description": row.description,
        "plugin_path": row.plugin_path,
        "current_version": row.current_version,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "published_at": row.published_at.isoformat() if row.published_at else None,
    }


def upsert_plugin_metadata(
    name: str,
    *,
    category: str = "itsm",
    description: str | None = None,
    plugin_path: str | None = None,
    status: str | None = None,
    user_id: str | None = None,
) -> WorkflowPluginMetadata:
    if status and status not in VALID_STATUSES:
        raise ValueError(f"非法状态: {status}")

    with get_db_session() as db:
        row = db.query(WorkflowPluginMetadata).filter(WorkflowPluginMetadata.name == name).first()
        if not row:
            row = WorkflowPluginMetadata(
                name=name,
                category=category,
                description=description or "",
                plugin_path=plugin_path,
                status=status or "draft",
                created_by=user_id,
                updated_by=user_id,
            )
            db.add(row)
        else:
            row.category = category or row.category
            if description is not None:
                row.description = description
            if plugin_path:
                row.plugin_path = plugin_path
            if status:
                row.status = status
            row.updated_by = user_id
            row.updated_at = _now()
        db.flush()
        db.expunge(row)
        return row


def transition_plugin_status(
    name: str,
    new_status: str,
    *,
    user_id: str | None = None,
) -> WorkflowPluginMetadata:
    if new_status not in VALID_STATUSES:
        raise ValueError(f"非法状态: {new_status}")

    with get_db_session() as db:
        row = db.query(WorkflowPluginMetadata).filter(WorkflowPluginMetadata.name == name).first()
        if not row:
            raise LookupError(f"插件元数据不存在: {name}")
        row.status = new_status
        row.updated_by = user_id
        row.updated_at = _now()
        if new_status == "published":
            row.published_at = _now()
        db.flush()
        db.expunge(row)
        return row


def create_version_snapshot(
    plugin_name: str,
    files: dict[str, str],
    *,
    user_id: str | None = None,
    change_summary: str | None = None,
    status: str = "published",
) -> WorkflowPluginVersion:
    with get_db_session() as db:
        meta = db.query(WorkflowPluginMetadata).filter(WorkflowPluginMetadata.name == plugin_name).first()
        if not meta:
            meta = WorkflowPluginMetadata(name=plugin_name, status="draft", created_by=user_id)
            db.add(meta)
            db.flush()

        next_version = (meta.current_version or 0) + 1
        meta.current_version = next_version
        meta.updated_at = _now()
        meta.updated_by = user_id

        version = WorkflowPluginVersion(
            id=str(uuid.uuid4()),
            plugin_name=plugin_name,
            version=next_version,
            files=dict(files),
            status=status,
            change_summary=change_summary,
            created_by=user_id,
        )
        db.add(version)
        db.flush()
        db.expunge(version)
        return version


def list_plugin_versions(plugin_name: str, *, limit: int = 50) -> list[WorkflowPluginVersion]:
    def _read() -> list[WorkflowPluginVersion]:
        with get_db_session() as db:
            rows = (
                db.query(WorkflowPluginVersion)
                .filter(WorkflowPluginVersion.plugin_name == plugin_name)
                .order_by(WorkflowPluginVersion.version.desc())
                .limit(limit)
                .all()
            )
            for row in rows:
                db.expunge(row)
            return rows

    return _read_or_fallback(_read, [])


def get_plugin_version(plugin_name: str, version: int) -> WorkflowPluginVersion | None:
    def _read() -> WorkflowPluginVersion | None:
        with get_db_session() as db:
            row = (
                db.query(WorkflowPluginVersion)
                .filter(
                    WorkflowPluginVersion.plugin_name == plugin_name,
                    WorkflowPluginVersion.version == version,
                )
                .first()
            )
            if row:
                db.expunge(row)
            return row

    return _read_or_fallback(_read, None)


def is_plugin_chat_active(name: str) -> bool:
    """聊天触发是否应对该插件生效。"""
    meta = get_plugin_metadata(name)
    if meta is None:
        return True
    return meta.status == "published"


def version_to_dict(v: WorkflowPluginVersion, *, include_files: bool = False) -> dict[str, Any]:
    data = {
        "id": v.id,
        "plugin_name": v.plugin_name,
        "version": v.version,
        "status": v.status,
        "change_summary": v.change_summary,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }
    if include_files:
        data["files"] = v.files
    else:
        data["file_keys"] = list((v.files or {}).keys())
    return data


def delete_plugin_metadata(plugin_name: str) -> None:
    """删除插件元数据、版本快照及关联市场模板。"""
    try:
        with get_db_session() as db:
            db.query(WorkflowPluginVersion).filter(
                WorkflowPluginVersion.plugin_name == plugin_name
            ).delete(synchronize_session=False)
            db.query(WorkflowMarketTemplate).filter(
                WorkflowMarketTemplate.source_plugin_name == plugin_name
            ).delete(synchronize_session=False)
            db.query(WorkflowPluginMetadata).filter(
                WorkflowPluginMetadata.name == plugin_name
            ).delete(synchronize_session=False)
    except OperationalError as exc:
        logger.debug("Workflow 元数据删除失败（数据库不可用）: %s", exc)
