"""Inspection dispatch, recovery, retention and final report tasks."""

import uuid
from datetime import datetime
from typing import Any

from src.common.config import get_settings
from src.core.celery_tasks.celery_app import celery
from src.core.logging import get_logger
from src.core.patrol.handlers import DispatchPatrolBatchHandler
from src.observability.deprecations import observe_legacy_path

from .access import authorize_batch, maintenance

settings = get_settings()
log = get_logger(__name__)


@celery.task(name="cleanup_expired_patrol_outputs")
@maintenance
def cleanup_expired_patrol_outputs_task() -> dict[str, Any]:
    from src.core.execution.artifacts import cleanup_expired_output_captures
    from src.core.ingestion.adapters.cli import OUTPUT_ROOT

    return {
        "success": True,
        **cleanup_expired_output_captures(
            OUTPUT_ROOT,
            retention_hours=settings.PATROL_LOCAL_OUTPUT_RETENTION_HOURS,
        ),
    }


@celery.task(name="dispatch_patrol_batch", max_retries=3, retry_backoff=5)
def dispatch_patrol_batch_task(patrol_batch_id: str) -> dict[str, Any]:
    from .access import inspection_batch

    if not inspection_batch(patrol_batch_id):
        from src.apps.rule_governance.backend.rollout_tasks import dispatch_capture

        return dispatch_capture.run(patrol_batch_id)
    return _dispatch_inspection(patrol_batch_id)


@maintenance
def _dispatch_inspection(patrol_batch_id: str) -> dict[str, Any]:
    from fastapi import HTTPException

    try:
        authorize_batch(patrol_batch_id)
    except HTTPException as exc:
        if exc.status_code not in {401, 403, 404}:
            raise
        from src.infrastructure.db.models import PatrolBatchDeviceRecord, PatrolBatchRecord
        from src.infrastructure.db.postgres import get_db_session

        with get_db_session() as db:
            batch = (
                db.query(PatrolBatchRecord)
                .filter_by(patrol_batch_id=patrol_batch_id)
                .with_for_update()
                .first()
            )
            if batch and batch.dispatch_status in {"pending", "dispatching"}:
                batch.dispatch_status, batch.dispatch_error = "failed", str(exc.detail)
                for row in (
                    db.query(PatrolBatchDeviceRecord)
                    .filter_by(patrol_batch_id=patrol_batch_id)
                    .all()
                ):
                    if row.dispatch_status in {"pending", "dispatching"} and not row.celery_task_id:
                        row.dispatch_status, row.failure_code, row.error_message = (
                            "failed",
                            "app_access_revoked",
                            str(exc.detail),
                        )
        return {"success": False, "error": str(exc.detail)}
    return DispatchPatrolBatchHandler().handle(patrol_batch_id)


@celery.task(name="reconcile_patrol_batch_dispatches")
@maintenance
def reconcile_patrol_batch_dispatches_task() -> dict[str, Any]:
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    from .launch import recover_incomplete_submissions

    recover_incomplete_submissions()
    batch_ids = PatrolBatchManifestRepository().pending_dispatch_batch_ids(limit=100)
    from .access import inspection_batch

    batch_ids = [key for key in batch_ids if inspection_batch(key)]
    dispatched = 0
    for patrol_batch_id in batch_ids:
        dispatch_patrol_batch_task.apply_async(
            args=[patrol_batch_id],
            queue="netops.patrol",
            task_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"patrol-batch-dispatch|{patrol_batch_id}|{datetime.utcnow():%Y%m%d%H%M}",
                )
            ),
        )
        dispatched += 1
    return {"success": True, "candidate_count": len(batch_ids), "dispatched_count": dispatched}


@celery.task(bind=True, name="reconcile_current_authority_dispatches")
@maintenance
def reconcile_current_authority_dispatches_task(self) -> dict[str, Any]:
    """Freeze approved offline-current requests before normal batch dispatch."""

    from src.core.check_items.current_authority import (
        PatrolCurrentAuthorityService,
    )

    discovery_service = PatrolCurrentAuthorityService()
    request_ids = discovery_service.pending_dispatch_request_ids(limit=100)
    if not request_ids:
        return {
            "success": True,
            "candidate_count": 0,
            "prepared_count": 0,
            "deferred_count": 0,
            "failed_count": 0,
            "failures": [],
        }

    from src.core.patrol.production_readiness import (
        PatrolProductionReadinessService,
    )

    delivery_info = dict(getattr(self.request, "delivery_info", None) or {})
    executing_queue = str(
        delivery_info.get("routing_key") or delivery_info.get("exchange") or ""
    ).strip()
    trusted_worker_queues = [executing_queue] if executing_queue == "netops.patrol" else []
    readiness = PatrolProductionReadinessService().evaluate(
        workflow_name="patrol-attested-offline-current",
        launch_mode="direct",
        executing_worker_queues=trusted_worker_queues,
    )
    if not bool(readiness.get("can_launch")):
        blocker_codes = [
            str(item.get("failure_code") or item.get("id") or "")
            for item in readiness.get("blockers") or []
            if isinstance(item, dict)
        ]
        log.warning(
            "current_authority_dispatches_deferred",
            candidate_count=len(request_ids),
            blocker_codes=blocker_codes,
        )
        return {
            "success": True,
            "candidate_count": len(request_ids),
            "prepared_count": 0,
            "deferred_count": len(request_ids),
            "failed_count": 0,
            "failures": [],
            "readiness_status": str(readiness.get("status") or "blocked"),
            "blocker_codes": blocker_codes,
        }
    service = PatrolCurrentAuthorityService(
        production_readiness=lambda: readiness,
    )
    prepared = 0
    failed = 0
    failures: list[dict[str, str]] = []
    for request_id in request_ids:
        try:
            from .access import authorize_authority_request

            authorize_authority_request(request_id)
            service.prepare_dispatch(request_id)
            prepared += 1
        except Exception as exc:
            failed += 1
            failures.append(
                {
                    "request_id": request_id,
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                }
            )
            log.warning(
                "current_authority_dispatch_prepare_failed",
                request_id=request_id,
                error=str(exc),
            )
    return {
        "success": failed == 0,
        "candidate_count": len(request_ids),
        "prepared_count": prepared,
        "deferred_count": 0,
        "failed_count": failed,
        "failures": failures,
    }


@celery.task(name="reconcile_patrol_batch_reports")
@maintenance
def reconcile_patrol_batch_reports_task() -> dict[str, Any]:
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    repository = PatrolBatchManifestRepository()
    batch_ids = repository.terminal_report_candidate_ids(limit=100)
    from .access import inspection_batch

    batch_ids = [key for key in batch_ids if inspection_batch(key)]
    requested = 0
    failed = 0
    for patrol_batch_id in batch_ids:
        if not repository.request_terminal_report(patrol_batch_id):
            continue
        try:
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
        except Exception as exc:
            repository.mark_report_status(
                patrol_batch_id,
                "not_requested",
                error=str(exc),
            )
            failed += 1
            log.warning(
                "patrol_batch_report_reconciliation_dispatch_failed",
                patrol_batch_id=patrol_batch_id,
                error=str(exc),
            )
            continue
        requested += 1
    return {
        "success": failed == 0,
        "candidate_count": len(batch_ids),
        "requested_count": requested,
        "failed_count": failed,
    }


@celery.task(
    name="materialize_patrol_batch_report",
    autoretry_for=(Exception,),
    max_retries=2,
    retry_backoff=5,
)
@maintenance
def materialize_patrol_batch_report_task(
    patrol_batch_id: str,
    generated_by: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    from fastapi import HTTPException

    from src.core.check_items import CheckItemReportMaterializer
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
    from src.core.patrol.patrol_report_service import PatrolReportService
    from src.core.workflows.patrol_batch_projection import (
        MaterializePatrolBatchReportHandler,
    )
    from src.core.workflows.patrol_batch_service import build_patrol_batch_summary
    from src.infrastructure.db.models import PatrolBatchRecord
    from src.infrastructure.db.postgres import get_db_session
    from src.infrastructure.storage.minio_client import get_minio_storage

    try:
        authorize_batch(patrol_batch_id)
    except HTTPException as exc:
        if exc.status_code not in {401, 403, 404}:
            raise
        PatrolBatchManifestRepository().mark_report_status(
            patrol_batch_id, "failed", error=str(exc.detail), request_id=request_id
        )
        return {"success": False, "patrol_batch_id": patrol_batch_id, "error": str(exc.detail)}
    repository = PatrolBatchManifestRepository()
    runs = repository.workflow_runs(patrol_batch_id)
    if runs is None:
        return {"success": False, "patrol_batch_id": patrol_batch_id, "reason": "not_found"}
    claimed = repository.mark_report_status(
        patrol_batch_id,
        "generating",
        request_id=request_id,
    )
    if request_id and not claimed:
        return {
            "success": False,
            "patrol_batch_id": patrol_batch_id,
            "request_id": request_id,
            "reason": "stale_report_request",
        }
    try:
        with get_db_session() as db:
            batch = db.get(PatrolBatchRecord, patrol_batch_id)
            uses_check_item_runtime = bool(batch is not None and batch.runtime_manifest_id)
        if uses_check_item_runtime:
            result = CheckItemReportMaterializer(
                object_storage=get_minio_storage(),
            ).materialize(
                patrol_batch_id,
                generated_by=(generated_by or "celery:materialize_patrol_batch_report"),
            )
            repository.mark_report_status(
                patrol_batch_id,
                "completed",
                request_id=request_id,
            )
            return {
                **result,
                "run_count": len(runs),
                "report_mode": "check_item_manifest",
                "request_id": request_id,
            }
        observe_legacy_path(
            "legacy-patrol-report-projection",
            "materialize_legacy_report",
            outcome="write",
        )
        markdown = MaterializePatrolBatchReportHandler(
            summary_builder=build_patrol_batch_summary,
            renderer=PatrolReportService.render_markdown,
            object_storage=get_minio_storage(),
        ).handle(patrol_batch_id, runs)
    except Exception as exc:
        repository.mark_report_status(
            patrol_batch_id,
            "failed",
            error=str(exc),
            request_id=request_id,
        )
        raise
    repository.mark_report_status(
        patrol_batch_id,
        "completed",
        request_id=request_id,
    )
    return {
        "success": True,
        "patrol_batch_id": patrol_batch_id,
        "request_id": request_id,
        "run_count": len(runs),
        "report_bytes": len(markdown.encode("utf-8")),
        "report_mode": "legacy_projection",
    }
