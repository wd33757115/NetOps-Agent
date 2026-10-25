# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""AuditConsumer：Skill / Workflow 执行审计。"""

from __future__ import annotations

from src.core.events.consumers.base import StreamConsumer
from src.core.events.domain_event import DomainEvent
from src.core.events.streams import GROUP_AUDIT, STREAM_SKILL_EXECUTION, STREAM_WORKFLOW
from src.gateway.audit_service import write_audit_log


class AuditConsumer(StreamConsumer):
    name = "audit"
    group = GROUP_AUDIT
    streams = [STREAM_SKILL_EXECUTION, STREAM_WORKFLOW]

    def handle(self, event: DomainEvent) -> None:
        payload = event.payload or {}
        user_id = payload.get("user_id")
        if event.event_type.startswith("skill."):
            write_audit_log(
                action="skill_execute",
                user_id=str(user_id) if user_id else None,
                resource_type="skill",
                resource_id=payload.get("execution_id") or payload.get("skill_name"),
                detail={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "skill_name": payload.get("skill_name"),
                    "status": payload.get("status"),
                    "ticket_id": payload.get("ticket_id"),
                    "source": event.source,
                    "correlation_id": event.correlation_id,
                    "summary": payload.get("summary"),
                },
                status="success" if event.event_type == "skill.executed" else "failed",
                idempotency_key=event.idempotency_key,
                raise_on_error=True,
            )
            return

        if event.event_type.startswith("workflow."):
            write_audit_log(
                action="workflow_event",
                user_id=str(user_id) if user_id else None,
                resource_type="workflow_run",
                resource_id=payload.get("run_id") or event.correlation_id,
                detail={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "ticket_id": payload.get("ticket_id"),
                    "step_name": payload.get("step_name"),
                    "skill_name": payload.get("skill_name"),
                },
                status="failed" if "failed" in event.event_type else "success",
                idempotency_key=event.idempotency_key,
                raise_on_error=True,
            )
