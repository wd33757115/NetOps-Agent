# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill 执行结果持久化（PostgreSQL）。"""

from __future__ import annotations

import logging

from src.core.skills.result import SkillExecutionResult
from src.infrastructure.db.postgres import get_db_session

logger = logging.getLogger(__name__)


def save_skill_execution(result: SkillExecutionResult) -> bool:
    """写入 netops_skill_executions；失败时记录日志但不抛异常。"""
    try:
        from src.infrastructure.db.models import SkillExecutionRecord

        record = SkillExecutionRecord(
            execution_id=result.execution_id,
            skill_name=result.skill_name,
            skill_version=result.skill_version,
            status=result.status.value,
            message=result.message,
            input_params=result.metadata.get("input_params"),
            output=result.output or None,
            artifacts={k: v.model_dump() for k, v in result.artifacts.items()} or None,
            exec_metadata=result.metadata or None,
            error_info=result.error_info.model_dump() if result.error_info else None,
            context=result.context.model_dump(),
            thread_id=result.context.thread_id,
            message_id=result.context.message_id,
            user_id=result.context.user_id,
            ticket_id=result.context.ticket_id,
            source=result.context.source,
            executed_at=result.executed_at,
        )
        with get_db_session() as session:
            session.merge(record)
        return True
    except Exception as exc:
        logger.warning("保存 Skill 执行记录失败 execution_id=%s: %s", result.execution_id, exc)
        return False
