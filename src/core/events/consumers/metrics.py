# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""MetricsConsumer：Skill / Workflow 指标。"""

from __future__ import annotations

from src.common.metrics import increment_counter, observe_histogram
from src.core.events.consumers.base import StreamConsumer
from src.core.events.domain_event import DomainEvent
from src.core.events.streams import GROUP_METRICS, STREAM_SKILL_EXECUTION, STREAM_WORKFLOW


class MetricsConsumer(StreamConsumer):
    name = "metrics"
    group = GROUP_METRICS
    streams = [STREAM_SKILL_EXECUTION, STREAM_WORKFLOW]

    def handle(self, event: DomainEvent) -> None:
        payload = event.payload or {}
        if event.event_type.startswith("skill."):
            skill = str(payload.get("skill_name") or "unknown")
            status = str(payload.get("status") or "unknown")
            increment_counter(
                "skill_execution_event_total",
                tags={"event_type": event.event_type, "skill": skill, "status": status},
            )
            duration = payload.get("duration_ms")
            if duration is not None:
                observe_histogram(
                    "skill_execution_duration_ms",
                    float(duration),
                    tags={"skill": skill, "status": status},
                )
            return

        if event.event_type.startswith("workflow."):
            increment_counter(
                "workflow_event_total",
                tags={"event_type": event.event_type},
            )
