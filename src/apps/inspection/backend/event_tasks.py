"""Celery delivery task for the durable network-event outbox."""

from .access import maintenance
from src.core.celery_tasks.celery_app import celery
from src.core.patrol.event_builder import publish_event_outbox


@celery.task(name="publish_network_event_outbox")
@maintenance
def publish_network_event_outbox():
    message_ids = publish_event_outbox(limit=10)
    return {"published_message_ids": message_ids, "published_count": len(message_ids)}


@celery.task(name="reconcile_patrol_runs")
@maintenance
def reconcile_patrol_runs():
    from src.common.config import settings
    from src.core.patrol.change_event_repository import ChangeEventRepository
    from src.core.patrol.repository import (
        PatrolAnalysisAttemptRepository,
        PatrolRunRepositoryBackend,
    )
    from src.core.workflows.repository import reconcile_stale_patrol_workflow_runs

    runs = PatrolRunRepositoryBackend().reconcile_stale_runs(limit=200)
    analysis = PatrolAnalysisAttemptRepository().reconcile_stale_analysis_attempts(limit=200)
    workflows = reconcile_stale_patrol_workflow_runs(
        limit=200,
        queued_stale_seconds=settings.PATROL_WORKFLOW_QUEUED_STALE_SECONDS,
        running_stale_seconds=settings.PATROL_WORKFLOW_RUNNING_STALE_SECONDS,
    )
    decisions = ChangeEventRepository().purge_expired_change_decisions(limit=5000)
    return {
        **runs,
        "analysis_reconciled_count": analysis["reconciled_count"],
        "analysis_run_ids": analysis["run_ids"],
        "workflow_reconciled_count": workflows["reconciled_count"],
        "workflow_run_ids": workflows["run_ids"],
        "workflow_failure_counts": workflows["failure_counts"],
        "purged_change_decisions": decisions,
    }
