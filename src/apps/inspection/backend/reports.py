"""Inspection report dispatch, shared with read-only legacy Workflow integration."""

import uuid

from src.core.logging import get_logger

log = get_logger(__name__)


def request_terminal_report(patrol_batch_id: str, *, run_id: str | None = None) -> bool:
    """Request a final report only after durable inspection facts are terminal."""
    try:
        from src.core.celery_tasks.tasks import materialize_patrol_batch_report_task
        from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

        repository = PatrolBatchManifestRepository()
        if not repository.request_terminal_report(patrol_batch_id):
            return False
        materialize_patrol_batch_report_task.apply_async(
            args=[patrol_batch_id],
            queue="netops.patrol",
            task_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"patrol-final-report|{patrol_batch_id}",
                )
            ),
        )
        return True
    except Exception as exc:
        try:
            from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

            PatrolBatchManifestRepository().mark_report_status(
                patrol_batch_id,
                "not_requested",
                error=str(exc),
            )
        except Exception:
            pass
        log.warning(
            "patrol_batch_report_materialization_dispatch_failed",
            run_id=run_id,
            patrol_batch_id=patrol_batch_id,
            error=str(exc),
        )
        return False
