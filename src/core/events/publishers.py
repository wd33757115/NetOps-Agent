# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill / Workflow 领域事件发布辅助。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from src.core.events.bus import EventBus
from src.core.events.domain_event import DomainEvent
from src.core.events.streams import STREAM_SKILL_EXECUTION, STREAM_WORKFLOW
from src.core.skills.result import SkillExecutionResult, SkillStatus


def _stable_event_id(event_type: str, identity: dict[str, Any]) -> str:
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"domain-event-v1|{event_type}|{canonical}"))


def publish_skill_execution_event(result: SkillExecutionResult) -> str | None:
    if result.status == SkillStatus.ERROR:
        event_type = "skill.failed"
    elif result.status == SkillStatus.SUCCESS:
        event_type = "skill.executed"
    else:
        event_type = f"skill.{result.status.value}"

    ctx = result.context
    correlation = ctx.message_id or ctx.run_id or result.execution_id
    event = DomainEvent(
        event_id=_stable_event_id(
            event_type,
            {"execution_id": result.execution_id, "status": result.status.value},
        ),
        event_type=event_type,
        occurred_at=result.executed_at,
        source=ctx.source,
        correlation_id=correlation,
        trace_id=ctx.trace_id,
        payload={
            "execution_id": result.execution_id,
            "skill_name": result.skill_name,
            "skill_version": result.skill_version,
            "status": result.status.value,
            "message": result.message,
            "summary": result.to_summary(),
            "artifacts": {k: v.model_dump() for k, v in result.artifacts.items()},
            "output_keys": list(result.output.keys()),
            "user_id": ctx.user_id,
            "ticket_id": ctx.ticket_id,
            "thread_id": ctx.thread_id,
            "message_id": ctx.message_id,
            "run_id": ctx.run_id,
            "step_name": ctx.step_name,
            "duration_ms": result.metadata.get("duration_ms"),
        },
    )
    return EventBus.publish(STREAM_SKILL_EXECUTION, event)


def workflow_event_key(event_type: str, *, run_id: str, payload: dict[str, Any] | None = None) -> str:
    """The idempotency key consumers will see for this workflow event."""
    return _stable_event_id(event_type, {"run_id": run_id, **(payload or {})})


def publish_workflow_event(
    event_type: str,
    *,
    run_id: str,
    source: str = "workflow",
    trace_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str | None:
    event_payload = {"run_id": run_id, **(payload or {})}
    event = DomainEvent(
        event_id=_stable_event_id(event_type, event_payload),
        event_type=event_type,
        source=source,
        correlation_id=run_id,
        trace_id=trace_id,
        payload=event_payload,
    )
    return EventBus.publish(STREAM_WORKFLOW, event)
