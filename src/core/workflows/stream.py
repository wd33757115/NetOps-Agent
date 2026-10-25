# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 运行 SSE 流（Timeline 轮询 + 终态检测）。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from src.core.workflows.events import get_workflow_timeline
from src.core.workflows.repository import get_workflow_run

logger = logging.getLogger(__name__)


async def stream_workflow_events(run_id: str) -> AsyncIterator[str]:
    """SSE：推送 timeline 增量事件，Run 终态后结束。"""
    run = get_workflow_run(run_id)
    if not run:
        yield f"event: error\ndata: {json.dumps({'message': 'Workflow 不存在'}, ensure_ascii=False)}\n\n"
        return

    seen = 0
    while True:
        events = get_workflow_timeline(run_id)
        for event in events[seen:]:
            yield f"event: timeline\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            seen += 1

        refreshed = get_workflow_run(run_id)
        if refreshed and refreshed.status in ("completed", "failed"):
            yield f"event: done\ndata: {json.dumps({'status': refreshed.status}, ensure_ascii=False)}\n\n"
            break

        await asyncio.sleep(0.8)
