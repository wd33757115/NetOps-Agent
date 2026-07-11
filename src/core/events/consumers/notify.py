# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""NotifyConsumer：站内通知（Skill / Workflow）。"""

from __future__ import annotations

from src.core.events.consumers.base import StreamConsumer
from src.core.events.domain_event import DomainEvent
from src.core.events.streams import GROUP_NOTIFY, STREAM_SKILL_EXECUTION, STREAM_WORKFLOW
from src.core.workflows.artifacts import collect_download_links, notification_download_payload
from src.core.workflows.repository import create_notification


class NotifyConsumer(StreamConsumer):
    name = "notify"
    group = GROUP_NOTIFY
    streams = [STREAM_SKILL_EXECUTION, STREAM_WORKFLOW]

    def handle(self, event: DomainEvent) -> None:
        payload = event.payload or {}
        user_id = payload.get("user_id")
        if not user_id:
            return

        if event.event_type == "skill.executed":
            artifacts = payload.get("artifacts") or {}
            links = collect_download_links(artifacts=artifacts)
            if not links and not payload.get("message"):
                return
            ticket = payload.get("ticket_id") or ""
            skill = payload.get("skill_name") or "Skill"
            body = str(payload.get("message") or "执行成功")
            if links:
                body = body.rstrip() + "\n\n" + "\n".join(
                    f"下载 {lnk['label']}: {lnk['url']}" for lnk in links
                )
            create_notification(
                user_id=str(user_id),
                title=f"Skill 完成 — {skill}" + (f" ({ticket})" if ticket else ""),
                body=body,
                thread_id=payload.get("thread_id"),
                payload=notification_download_payload(artifacts=artifacts),
                level="success",
                idempotency_key=event.idempotency_key,
            )
            return

        if event.event_type == "skill.failed":
            ticket = payload.get("ticket_id") or ""
            skill = payload.get("skill_name") or "Skill"
            create_notification(
                user_id=str(user_id),
                title=f"Skill 失败 — {skill}" + (f" ({ticket})" if ticket else ""),
                body=str(payload.get("message") or "执行失败"),
                thread_id=payload.get("thread_id"),
                level="error",
                idempotency_key=event.idempotency_key,
            )
            return

        if event.event_type == "workflow.step.completed" and payload.get("notify_user"):
            run_id = payload.get("run_id") or event.correlation_id
            label = str(payload.get("step_label") or payload.get("step_name") or "步骤").replace("_", " ")
            ticket = payload.get("ticket_id") or run_id[:8]
            result = payload.get("result") if isinstance(payload.get("result"), dict) else None
            create_notification(
                user_id=str(user_id),
                title=f"Workflow 步骤完成 — {label} ({ticket})",
                body=str((result or {}).get("message") or payload.get("skill_name") or ""),
                workflow_run_id=run_id,
                thread_id=payload.get("thread_id"),
                payload=notification_download_payload(result=result),
                level="info",
                idempotency_key=event.idempotency_key,
            )
            return

        if event.event_type == "workflow.completed":
            run_id = payload.get("run_id") or event.correlation_id
            create_notification(
                user_id=str(user_id),
                title=str(payload.get("title") or "Workflow 已完成"),
                body=str(payload.get("body") or "流程已完成。"),
                workflow_run_id=run_id,
                thread_id=payload.get("thread_id"),
                payload=notification_download_payload(artifacts=payload.get("artifacts")),
                level=str(payload.get("level") or "success"),
                idempotency_key=event.idempotency_key,
            )
            return

        if event.event_type == "workflow.failed":
            if payload.get("notify_on_failure") is False:
                return
            run_id = payload.get("run_id") or event.correlation_id
            ticket = payload.get("ticket_id") or run_id[:8]
            create_notification(
                user_id=str(user_id),
                title=f"Workflow 失败 ({ticket})",
                body=str(payload.get("error") or payload.get("message") or "流程失败"),
                workflow_run_id=run_id,
                thread_id=payload.get("thread_id"),
                level="error",
                idempotency_key=event.idempotency_key,
            )
