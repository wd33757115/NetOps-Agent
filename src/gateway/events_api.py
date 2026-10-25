# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""EventBus 运维 API（DLQ / Consumer Lag）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.auth.dependencies import require_role
from src.auth.models import CurrentUser
from src.core.events.bus import EventBus
from src.core.events.streams import GROUP_AUDIT, GROUP_METRICS, GROUP_NOTIFY, STREAM_SKILL_EXECUTION, STREAM_WORKFLOW
from src.core.events.worker import poll_event_consumers_once

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.get("/dlq")
def list_event_dlq(
    limit: int = 50,
    _user: CurrentUser = Depends(require_role(["admin"])),
):
    """列出死信队列最近事件（admin）。"""
    return {"items": EventBus.list_dlq_entries(limit=min(limit, 200))}


@router.get("/consumer-lag")
def get_consumer_lag(_user: CurrentUser = Depends(require_role(["admin"]))):
    """各 Consumer Group pending 概览。"""
    groups = [
        (STREAM_SKILL_EXECUTION, GROUP_AUDIT),
        (STREAM_SKILL_EXECUTION, GROUP_NOTIFY),
        (STREAM_SKILL_EXECUTION, GROUP_METRICS),
        (STREAM_WORKFLOW, GROUP_AUDIT),
        (STREAM_WORKFLOW, GROUP_NOTIFY),
        (STREAM_WORKFLOW, GROUP_METRICS),
    ]
    return {"groups": [EventBus.read_group_pending_info(stream, group) for stream, group in groups]}


@router.post("/poll")
def poll_consumers_once(_user: CurrentUser = Depends(require_role(["admin"]))):
    """手动触发一轮 Consumer 拉取（调试）。"""
    processed = poll_event_consumers_once()
    return {"processed": processed}
