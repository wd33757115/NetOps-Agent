# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill 执行记录归档（PG → MinIO JSONL.gz）。"""

from __future__ import annotations

import gzip
import io
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.common.config import get_settings
from src.infrastructure.db.postgres import get_db_session

logger = logging.getLogger(__name__)


def _serialize_row(row) -> dict[str, Any]:
    return {
        "execution_id": row.execution_id,
        "skill_name": row.skill_name,
        "skill_version": row.skill_version,
        "status": row.status,
        "message": row.message,
        "input_params": row.input_params,
        "output": row.output,
        "artifacts": row.artifacts,
        "exec_metadata": row.exec_metadata,
        "error_info": row.error_info,
        "context": row.context,
        "thread_id": row.thread_id,
        "message_id": row.message_id,
        "user_id": row.user_id,
        "ticket_id": row.ticket_id,
        "source": row.source,
        "executed_at": row.executed_at.isoformat() if row.executed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def archive_skill_executions(
    *,
    before_days: int | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """将早于 cutoff 的执行记录导出到 MinIO 并删除 PG 行。"""
    settings = get_settings()
    if not settings.SKILL_EXEC_ARCHIVE_ENABLED:
        return {"archived": 0, "skipped": True, "reason": "disabled"}

    days = before_days if before_days is not None else settings.SKILL_EXEC_ARCHIVE_AFTER_DAYS
    batch = batch_size if batch_size is not None else settings.SKILL_EXEC_ARCHIVE_BATCH_SIZE
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    from src.infrastructure.db.models import SkillExecutionArchiveLog, SkillExecutionRecord
    from src.infrastructure.storage.minio_client import get_minio_storage

    minio = get_minio_storage()
    if not minio or not minio.is_ready():
        return {"archived": 0, "error": "MinIO 不可用"}

    archived_total = 0
    archive_id = f"arch-{uuid.uuid4().hex[:12]}"
    period = cutoff.strftime("%Y-%m")
    object_key = f"archives/skill_executions/{period}/{archive_id}.jsonl.gz"

    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
        while True:
            with get_db_session() as session:
                rows = (
                    session.query(SkillExecutionRecord)
                    .filter(SkillExecutionRecord.executed_at < cutoff)
                    .order_by(SkillExecutionRecord.executed_at.asc())
                    .limit(batch)
                    .all()
                )
                if not rows:
                    break
                for row in rows:
                    line = json.dumps(_serialize_row(row), ensure_ascii=False, default=str)
                    gz.write((line + "\n").encode("utf-8"))
                ids = [r.execution_id for r in rows]
                session.query(SkillExecutionRecord).filter(
                    SkillExecutionRecord.execution_id.in_(ids)
                ).delete(synchronize_session=False)
                archived_total += len(ids)

    if archived_total == 0:
        return {"archived": 0, "cutoff": cutoff.isoformat()}

    buffer.seek(0)
    uploaded = minio.upload_file(object_key, buffer.read(), content_type="application/gzip")
    if not uploaded:
        logger.error("skill_executions_archive_upload_failed count=%s", archived_total)
        return {"archived": 0, "error": "MinIO 上传失败"}

    with get_db_session() as session:
        session.add(
            SkillExecutionArchiveLog(
                id=archive_id,
                object_key=object_key,
                record_count=archived_total,
                before_date=cutoff,
            )
        )

    logger.info(
        "skill_executions_archived count=%s object_key=%s cutoff=%s",
        archived_total,
        object_key,
        cutoff.isoformat(),
    )
    return {
        "archived": archived_total,
        "object_key": object_key,
        "cutoff": cutoff.isoformat(),
        "archive_id": archive_id,
    }
