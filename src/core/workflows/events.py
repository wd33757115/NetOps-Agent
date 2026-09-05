# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 进度事件（Redis Pub/Sub + Timeline，可选降级）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.auth.token_store import get_redis

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "workflow:progress:"
TIMELINE_PREFIX = "workflow:timeline:"
TIMELINE_TTL_SECONDS = 7 * 24 * 3600
TIMELINE_MAX_LEN = 500


def _channel(run_id: str) -> str:
    return f"{CHANNEL_PREFIX}{run_id}"


def _timeline_key(run_id: str) -> str:
    return f"{TIMELINE_PREFIX}{run_id}"


def append_timeline_event(run_id: str, payload: dict[str, Any]) -> None:
    """将事件追加到 Redis 时间线列表（供 Run Timeline API）。"""
    client = get_redis()
    if not client:
        return
    try:
        key = _timeline_key(run_id)
        client.rpush(key, json.dumps(payload, ensure_ascii=False))
        client.ltrim(key, -TIMELINE_MAX_LEN, -1)
        client.expire(key, TIMELINE_TTL_SECONDS)
    except Exception as exc:
        logger.debug("写入 workflow timeline 失败: %s", exc)


def get_workflow_timeline(run_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    client = get_redis()
    if not client:
        return []
    try:
        raw_items = client.lrange(_timeline_key(run_id), -limit, -1)
        events: list[dict[str, Any]] = []
        for item in raw_items or []:
            try:
                events.append(json.loads(item))
            except Exception:
                continue
        return events
    except Exception as exc:
        logger.warning("读取 workflow timeline 失败: %s", exc)
        return []


def publish_workflow_event(
    run_id: str,
    *,
    step_name: str | None = None,
    skill_name: str | None = None,
    status: str = "running",
    message: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {
        "run_id": run_id,
        "step_name": step_name,
        "skill_name": skill_name,
        "status": status,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    append_timeline_event(run_id, payload)
    client = get_redis()
    if not client:
        logger.debug("Redis 不可用，跳过 workflow 事件: %s", payload)
        return
    try:
        encoded = json.dumps(payload, ensure_ascii=False)
        client.publish(_channel(run_id), encoded)
        client.publish("workflow:progress:all", encoded)
    except Exception as exc:
        logger.warning("发布 workflow 事件失败: %s", exc)
