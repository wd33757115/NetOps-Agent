"""Celery delivery task for the durable network-event outbox."""

from src.core.celery_tasks.celery_app import celery
from src.core.patrol.event_builder import publish_event_outbox


@celery.task(name="publish_network_event_outbox")
def publish_network_event_outbox():
    message_ids = publish_event_outbox(limit=10)
    return {"published_message_ids": message_ids, "published_count": len(message_ids)}


@celery.task(name="reconcile_patrol_runs")
def reconcile_patrol_runs():
    from src.core.patrol.change_event_repository import ChangeEventRepository
    from src.core.patrol.repository import PatrolRepository

    repository = PatrolRepository()
    runs = repository.reconcile_stale_runs(limit=200)
    analysis = repository.reconcile_stale_analysis_attempts(limit=200)
    decisions = ChangeEventRepository().purge_expired_change_decisions(limit=5000)
    return {
        **runs,
        "analysis_reconciled_count": analysis["reconciled_count"],
        "analysis_run_ids": analysis["run_ids"],
        "purged_change_decisions": decisions,
    }
