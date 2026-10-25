# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Redis ExecutionWorkspace：按 thread_id + message_id 缓存 Skill 执行摘要。"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.auth.token_store import get_redis
from src.core.skills.result import SkillExecutionResult

logger = logging.getLogger(__name__)

WORKSPACE_TTL_SEC = 86400  # 24h


def _workspace_key(thread_id: str, message_id: str) -> str:
    return f"exec_ws:{thread_id}:{message_id}"


class ExecutionWorkspace:
    """一次 Chat 消息内的 Skill 链执行上下文（Redis Hash）。"""

    @classmethod
    def put(cls, thread_id: str | None, message_id: str | None, result: SkillExecutionResult) -> bool:
        if not thread_id or not message_id:
            return False
        client = get_redis()
        if not client:
            return False
        try:
            key = _workspace_key(thread_id, message_id)
            payload = {
                **result.to_summary(),
                "legacy": result.to_legacy_dict(),
            }
            client.hset(key, result.skill_name, json.dumps(payload, ensure_ascii=False, default=str))
            client.expire(key, WORKSPACE_TTL_SEC)
            return True
        except Exception as exc:
            logger.warning(
                "ExecutionWorkspace.put 失败 thread=%s message=%s skill=%s: %s",
                thread_id,
                message_id,
                result.skill_name,
                exc,
            )
            return False

    @classmethod
    def get_skill(cls, thread_id: str | None, message_id: str | None, skill_name: str) -> dict[str, Any] | None:
        if not thread_id or not message_id:
            return None
        client = get_redis()
        if not client:
            return None
        try:
            raw = client.hget(_workspace_key(thread_id, message_id), skill_name)
            if not raw:
                return None
            data = json.loads(raw)
            legacy = data.get("legacy")
            return legacy if isinstance(legacy, dict) else data
        except Exception as exc:
            logger.warning(
                "ExecutionWorkspace.get_skill 失败 thread=%s message=%s skill=%s: %s",
                thread_id,
                message_id,
                skill_name,
                exc,
            )
            return None

    @classmethod
    def list_skills(cls, thread_id: str | None, message_id: str | None) -> list[str]:
        if not thread_id or not message_id:
            return []
        client = get_redis()
        if not client:
            return []
        try:
            return list(client.hkeys(_workspace_key(thread_id, message_id)) or [])
        except Exception:
            return []
