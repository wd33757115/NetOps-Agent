"""Celery coordination tasks for parser jobs and durable outbox delivery."""

from __future__ import annotations

from src.apps.rule_governance.backend.access import maintenance

import os
import uuid

from src.apps.rule_governance.backend.parser.outbox_repository import ParserOutboxRepository
from src.apps.rule_governance.backend.parser.repository import (
    ParserJobRepository,
    ParserJobStateError,
    ParserSampleRepository,
    ParserTemplateRepository,
)
from src.apps.rule_governance.backend.parser.worker import (
    PARSER_EXECUTION_TASK,
    execute_parser_delivery,
)
from src.common.config import get_settings
from src.core.celery_tasks.celery_app import celery
from src.core.events.bus import EventBus
from src.core.events.domain_event import DomainEvent
from src.core.events.streams import STREAM_PARSER


@celery.task(bind=True, name=PARSER_EXECUTION_TASK, queue="app.rule-governance",
             soft_time_limit=get_settings().PARSER_GENERATION_TASK_SOFT_TIME_LIMIT_SECONDS,
             time_limit=get_settings().PARSER_GENERATION_TASK_TIME_LIMIT_SECONDS)
def execute_parser_task(self, job_id: str, operation: str = "generate", publish: bool = True,
                        dry_run: bool = False, suggestion_task_id: str | None = None):
    return execute_parser_delivery(task_id=str(self.request.id or ""), params={
        "job_id": job_id, "operation": operation, "publish": publish,
        "dry_run": dry_run, "suggestion_task_id": suggestion_task_id,
    })


def dispatch_ready_parser_jobs(
    *,
    job_id: str | None = None,
    limit: int | None = None,
    repository: ParserJobRepository | None = None,
    celery_app=None,
    actor: str | None = None,
    actor_user_id: str | None = None,
) -> dict:
    repository = repository or ParserJobRepository()
    celery_app = celery_app or celery
    settings = get_settings()
    batch_limit = limit or int(os.getenv("PARSER_DISPATCH_BATCH_SIZE", "1"))
    dispatched: list[str] = []
    failed: list[dict[str, str]] = []
    for job in repository.list_dispatchable_jobs(limit=batch_limit, job_id=job_id):
        task_id = str(uuid.uuid4())
        identity = {"actor": actor, "actor_user_id": actor_user_id} if actor is not None or actor_user_id is not None else {}
        try:
            repository.mark_job_dispatched(job["id"], task_id, **identity)
        except ParserJobStateError as exc:
            # Another dispatcher may have claimed the same ready row.
            failed.append({"job_id": job["id"], "error": str(exc)})
            continue
        try:
            celery_app.send_task(
                PARSER_EXECUTION_TASK,
                kwargs={
                    "job_id": job["id"],
                    "publish": True,
                },
                queue="app.rule-governance",
                task_id=task_id,
                soft_time_limit=int(
                    settings.PARSER_GENERATION_TASK_SOFT_TIME_LIMIT_SECONDS
                ),
                time_limit=int(settings.PARSER_GENERATION_TASK_TIME_LIMIT_SECONDS),
            )
            dispatched.append(job["id"])
        except Exception as exc:
            try:
                repository.transition_job_status(
                    job["id"], "failed", str(exc), failure_code="dispatch_failed",
                    expected_status="pending", expected_celery_task_id=task_id,
                )
            except ParserJobStateError:
                # An uncertain broker acknowledgement must not fail an already
                # running task or a later replacement execution.
                pass
            failed.append({"job_id": job["id"], "error": str(exc)})
    return {"dispatched": dispatched, "failed": failed}


@celery.task(name="dispatch_parser_jobs")
@maintenance
def dispatch_parser_jobs():
    repository = ParserJobRepository()
    if not repository.get_auto_dispatch_enabled():
        return {"dispatched": [], "failed": [], "auto_dispatch_enabled": False}
    return {
        **dispatch_ready_parser_jobs(repository=repository),
        "auto_dispatch_enabled": True,
    }


@celery.task(name="reconcile_parser_jobs")
@maintenance
def reconcile_parser_jobs():
    repository = ParserJobRepository()
    return {
        "finalized": repository.finalize_durable_successes(),
        "failed": repository.fail_stale_jobs(),
    }


@celery.task(name="promote_parser_gap_ledger")
@maintenance
def promote_parser_gap_ledger():
    from src.apps.rule_governance.backend.parser.gap_ledger import ParserGapLedgerService

    return ParserGapLedgerService().promote_to_draft_jobs()


@celery.task(name="sanitize_parser_samples")
@maintenance
def sanitize_parser_samples(job_id: str | None = None, limit: int = 100):
    samples = ParserSampleRepository().sanitize_pending_samples(job_id=job_id, limit=limit)
    return {
        "processed_count": len(samples),
        "succeeded_count": sum(bool(item.get("success")) for item in samples),
        "failed_count": sum(not bool(item.get("success")) for item in samples),
        "samples": samples,
    }


@celery.task(name="publish_parser_outbox")
@maintenance
def publish_parser_outbox():
    parser_repository = ParserTemplateRepository()
    outbox_repository = ParserOutboxRepository()
    published: list[str] = []
    for row in outbox_repository.claim_pending():
        claim_token = str(row.claim_token or "")
        try:
            message_id = EventBus.publish(
                STREAM_PARSER,
                DomainEvent(
                    event_id=str(row.id),
                    event_type=row.event_type,
                    source="parser-control-plane",
                    correlation_id=row.aggregate_id,
                    payload=dict(row.payload_json or {}),
                ),
            )
        except Exception as exc:
            outbox_repository.release(row.id, claim_token=claim_token, error=str(exc))
            continue
        if message_id:
            outbox_repository.mark_published(row.id, claim_token=claim_token)
            published.append(row.id)
        else:
            outbox_repository.release(
                row.id,
                claim_token=claim_token,
                error="event_bus_publish_returned_none",
            )
    return {"published": published}
