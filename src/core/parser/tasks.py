"""Celery coordination tasks for parser jobs and durable outbox delivery."""

from __future__ import annotations

import os
import uuid

from src.core.celery_tasks.celery_app import celery
from src.core.events.bus import EventBus
from src.core.events.domain_event import DomainEvent
from src.core.events.streams import STREAM_PARSER
from src.core.parser.repository import ParserRepository
from src.core.rule_control import RuleAssetTemplateActivationService


def dispatch_ready_parser_jobs(
    *,
    job_id: str | None = None,
    limit: int | None = None,
    repository: ParserRepository | None = None,
    celery_app=None,
) -> dict:
    repository = repository or ParserRepository()
    celery_app = celery_app or celery
    batch_limit = limit or int(os.getenv("PARSER_DISPATCH_BATCH_SIZE", "1"))
    dispatched: list[str] = []
    failed: list[dict[str, str]] = []
    for job in repository.list_dispatchable_jobs(limit=batch_limit, job_id=job_id):
        task_id = str(uuid.uuid4())
        repository.mark_job_dispatched(job["id"], task_id)
        try:
            celery_app.send_task(
                "execute_skill_task",
                args=["textfsm-generator"],
                kwargs={
                    "job_id": job["id"],
                    "publish": True,
                },
                queue="netops.default",
                task_id=task_id,
            )
            dispatched.append(job["id"])
        except Exception as exc:
            repository.transition_job_status(
                job["id"],
                "failed",
                str(exc),
                failure_code="dispatch_failed",
            )
            failed.append({"job_id": job["id"], "error": str(exc)})
    return {"dispatched": dispatched, "failed": failed}


@celery.task(name="dispatch_parser_jobs")
def dispatch_parser_jobs():
    repository = ParserRepository()
    if not repository.get_auto_dispatch_enabled():
        return {"dispatched": [], "failed": [], "auto_dispatch_enabled": False}
    return {
        **dispatch_ready_parser_jobs(repository=repository),
        "auto_dispatch_enabled": True,
    }


@celery.task(name="reconcile_parser_jobs")
def reconcile_parser_jobs():
    return {"failed": ParserRepository().fail_stale_jobs()}


@celery.task(name="publish_parser_outbox")
def publish_parser_outbox():
    repository = ParserRepository()
    published: list[str] = []
    rule_asset_seeds: list[dict] = []
    for row in repository.list_pending_outbox():
        message_id = EventBus.publish(
            STREAM_PARSER,
            DomainEvent(
                event_type=row.event_type,
                source="parser-control-plane",
                correlation_id=row.aggregate_id,
                payload=dict(row.payload_json or {}),
            ),
        )
        if message_id:
            if row.event_type == "parser.template.activated":
                try:
                    template_id = str((row.payload_json or {}).get("template_id") or "")
                    if template_id:
                        rule_asset_seeds.append(
                            RuleAssetTemplateActivationService(
                                parser_repository=repository,
                            ).seed_from_template(template_id)
                        )
                except Exception as exc:
                    rule_asset_seeds.append(
                        {
                            "success": False,
                            "created": False,
                            "event_id": row.id,
                            "reason": "rule_asset_seed_failed",
                            "error": str(exc),
                        }
                    )
            repository.mark_outbox_published(row.id)
            published.append(row.id)
    return {"published": published, "rule_asset_seeds": rule_asset_seeds}
