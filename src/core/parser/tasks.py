"""Celery coordination tasks for parser jobs and durable outbox delivery."""

from __future__ import annotations

import os
import uuid

from src.common.config import get_settings
from src.core.celery_tasks.celery_app import celery
from src.core.events.bus import EventBus
from src.core.events.domain_event import DomainEvent
from src.core.events.streams import STREAM_PARSER
from src.core.parser.outbox_repository import ParserOutboxRepository
from src.core.parser.repository import (
    ParserJobRepository,
    ParserSampleRepository,
    ParserTemplateRepository,
)
from src.core.rule_control import RuleAssetTemplateActivationService


def dispatch_ready_parser_jobs(
    *,
    job_id: str | None = None,
    limit: int | None = None,
    repository: ParserJobRepository | None = None,
    celery_app=None,
    allow_production: bool = False,
) -> dict:
    repository = repository or ParserJobRepository()
    celery_app = celery_app or celery
    batch_limit = limit or int(os.getenv("PARSER_DISPATCH_BATCH_SIZE", "1"))
    dispatched: list[str] = []
    failed: list[dict[str, str]] = []
    for job in repository.list_dispatchable_jobs(limit=batch_limit, job_id=job_id):
        if job.get("source_mode") == "production" and not (
            get_settings().PARSER_LLM_GENERATION_ENABLED or allow_production
        ):
            continue
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
                queue="netops.ai",
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
    repository = ParserJobRepository()
    if not repository.get_auto_dispatch_enabled():
        return {"dispatched": [], "failed": [], "auto_dispatch_enabled": False}
    return {
        **dispatch_ready_parser_jobs(repository=repository),
        "auto_dispatch_enabled": True,
    }


@celery.task(name="reconcile_parser_jobs")
def reconcile_parser_jobs():
    return {"failed": ParserJobRepository().fail_stale_jobs()}


@celery.task(name="promote_parser_gap_ledger")
def promote_parser_gap_ledger():
    from src.core.parser.gap_ledger import ParserGapLedgerService

    return ParserGapLedgerService().promote_to_draft_jobs()


@celery.task(name="sanitize_parser_samples")
def sanitize_parser_samples(job_id: str | None = None, limit: int = 100):
    samples = ParserSampleRepository().sanitize_pending_samples(job_id=job_id, limit=limit)
    return {
        "processed_count": len(samples),
        "succeeded_count": sum(bool(item.get("success")) for item in samples),
        "failed_count": sum(not bool(item.get("success")) for item in samples),
        "samples": samples,
    }


@celery.task(name="publish_parser_outbox")
def publish_parser_outbox():
    parser_repository = ParserTemplateRepository()
    outbox_repository = ParserOutboxRepository()
    published: list[str] = []
    rule_asset_seeds: list[dict] = []
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
            if (
                row.event_type == "parser.template.activated"
                and get_settings().RULE_ASSET_AUTO_SEED_ENABLED
            ):
                try:
                    template_id = str((row.payload_json or {}).get("template_id") or "")
                    if template_id:
                        seed_result = RuleAssetTemplateActivationService(
                            parser_repository=parser_repository,
                        ).seed_from_template(template_id)
                        if seed_result.get("reason") in {
                            "parser_template_not_active",
                            "parser_template_not_found",
                        }:
                            seed_result = {
                                **seed_result,
                                "skipped": True,
                                "terminal": True,
                            }
                        rule_asset_seeds.append(seed_result)
                        if not seed_result.get("success") and not seed_result.get("terminal"):
                            raise RuntimeError(
                                str(seed_result.get("reason") or "rule asset seed failed")
                            )
                        job_id = str((seed_result.get("job") or {}).get("id") or "")
                        if job_id:
                            try:
                                auto_context = RuleAssetTemplateActivationService(
                                    parser_repository=parser_repository,
                                ).auto_build_context(job_id)
                                context_ready = bool(auto_context.get("ready"))
                                seed_result["auto_context"] = {
                                    "success": True,
                                    "context_id": auto_context.get("context_id"),
                                    "ready": context_ready,
                                    "quality": auto_context.get("quality"),
                                }
                                if (
                                    context_ready
                                    and get_settings().RULE_ASSET_AUTO_GENERATE_ENABLED
                                ):
                                    generation = RuleAssetTemplateActivationService(
                                        parser_repository=parser_repository,
                                    ).start_generation_workflow(
                                        job_id,
                                        context_id=str(auto_context["context_id"]),
                                        actor="parser-template-activation",
                                    )
                                    seed_result["auto_generation"] = generation
                            except Exception as auto_exc:
                                seed_result["auto_context"] = {
                                    "success": False,
                                    "error": str(auto_exc),
                                }
                except Exception as exc:
                    failure = {
                        "success": False,
                        "created": False,
                        "event_id": row.id,
                        "reason": "rule_asset_seed_failed",
                        "error": str(exc),
                    }
                    if not rule_asset_seeds or rule_asset_seeds[-1].get("success"):
                        rule_asset_seeds.append(failure)
                    outbox_repository.release(
                        row.id,
                        claim_token=claim_token,
                        error=str(exc),
                    )
                    continue
            outbox_repository.mark_published(row.id, claim_token=claim_token)
            published.append(row.id)
        else:
            outbox_repository.release(
                row.id,
                claim_token=claim_token,
                error="event_bus_publish_returned_none",
            )
    return {"published": published, "rule_asset_seeds": rule_asset_seeds}
