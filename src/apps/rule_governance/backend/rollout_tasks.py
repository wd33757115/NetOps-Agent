from typing import Any
from src.core.celery_tasks.celery_app import celery
from .access import maintenance


@celery.task(name="publish_rollout_outbox")
@maintenance
def publish_rollout_outbox_task() -> dict[str, Any]:
    from src.core.check_items.rollout_execution import RolloutOutboxRepository

    repository = RolloutOutboxRepository()
    rows = repository.claim_pending(limit=100)
    published = 0
    failed = 0
    for row in rows:
        try:
            execute_rollout_stage_task.apply_async(
                args=[str(row["aggregate_id"])],
                queue="app.rule-governance",
                task_id=f"rollout-{row['id']}",
            )
            repository.mark_published(
                str(row["id"]),
                claim_token=str(row["claim_token"]),
            )
            published += 1
        except Exception as exc:
            repository.release(
                str(row["id"]),
                claim_token=str(row["claim_token"]),
                error=str(exc),
            )
            failed += 1
    return {
        "success": failed == 0,
        "candidate_count": len(rows),
        "published_count": published,
        "failed_count": failed,
    }


@celery.task(
    bind=True,
    name="execute_rollout_stage",
    max_retries=3,
)
@maintenance
def execute_rollout_stage_task(
    self,
    stage_execution_id: str,
) -> dict[str, Any]:
    from src.core.check_items.rollout_execution import (
        RolloutStageExecutionService,
    )

    service = RolloutStageExecutionService()
    try:
        return service.execute(
            stage_execution_id,
            worker_owner=str(getattr(self.request, "hostname", None) or "unknown"),
            worker_task_id=str(getattr(self.request, "id", None) or "") or None,
        )
    except Exception as exc:
        if int(getattr(self.request, "retries", 0)) >= int(self.max_retries):
            service.pause_campaign_after_failure(
                stage_execution_id,
                reason=f"rollout execution retries exhausted: {exc}",
            )
            raise
        raise self.retry(
            exc=exc,
            countdown=10 * (2 ** int(getattr(self.request, "retries", 0))),
        )


@celery.task(name="reconcile_rollout_live_captures")
@maintenance
def reconcile_rollout_live_captures_task() -> dict[str, Any]:
    from src.core.check_items.rollout_execution import (
        RolloutStageExecutionService,
    )

    service = RolloutStageExecutionService()
    execution_ids = service.pending_live_capture_ids(limit=100)
    completed = 0
    pending = 0
    failed = 0
    for execution_id in execution_ids:
        try:
            result = service.reconcile_live_capture(execution_id)
        except Exception as exc:
            failed += 1
            service.pause_campaign_after_failure(
                execution_id,
                reason=f"live-capture reconciliation failed: {exc}",
            )
            continue
        status = str(result.get("status") or "")
        if status == "queued":
            completed += 1
        elif status == "failed":
            failed += 1
            service.pause_campaign_after_failure(
                execution_id,
                reason=(
                    f"live capture failed: {result.get('failure_code') or result.get('error')}"
                ),
            )
        else:
            pending += 1
    return {
        "success": failed == 0,
        "candidate_count": len(execution_ids),
        "completed_count": completed,
        "pending_count": pending,
        "failed_count": failed,
    }


@celery.task(name="netops.rule_governance.dispatch_capture")
@maintenance
def dispatch_capture(patrol_batch_id):
    from .access import authorize
    from src.infrastructure.db.models import PatrolBatchRecord, RolloutStageExecutionRecord
    from src.infrastructure.db.postgres import get_db_session
    from src.core.patrol.handlers import DispatchPatrolBatchHandler

    with get_db_session() as db:
        batch = db.get(PatrolBatchRecord, patrol_batch_id)
        stage_id = (batch.selector_json or {}).get("rollout_stage_execution_id") if batch else None
        stage = db.get(RolloutStageExecutionRecord, stage_id) if stage_id else None
        if stage is None or stage.status not in {"running", "capturing"}:
            return {"ignored": True}
        actor = (stage.selection_manifest_json or {}).get("execution_user_id")
    authorize(actor)
    return DispatchPatrolBatchHandler().handle(patrol_batch_id)


@celery.task(name="netops.rule_governance.recover_capture_dispatch")
@maintenance
def recover_capture_dispatch():
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
    from src.infrastructure.db.models import PatrolBatchRecord
    from src.infrastructure.db.postgres import get_db_session

    ids = PatrolBatchManifestRepository().pending_dispatch_batch_ids(limit=100)
    with get_db_session() as db:
        ids = [
            key
            for key in ids
            if (db.get(PatrolBatchRecord, key).selector_json or {}).get(
                "rollout_stage_execution_id"
            )
        ]
    for key in ids:
        dispatch_capture.apply_async(args=[key], queue="app.rule-governance")
    return {"queued": len(ids)}
