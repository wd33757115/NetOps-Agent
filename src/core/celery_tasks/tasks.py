# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import uuid
from datetime import datetime
from typing import Any

from src.core.celery_tasks.celery_app import celery

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, BASE_DIR)


from src.common.config import get_settings
from src.core.backup.runner import DeviceBackupRunner, DeviceBackupRunRequest
from src.core.logging import get_logger
from src.core.patrol.handlers import (
    DispatchPatrolBatchHandler,
    ExecutePatrolCommand,
    ExecutePatrolHandler,
)
from src.core.patrol.run_coordinator import PatrolRunCoordinator
from src.core.patrol.runner import PatrolPlatformError
from src.core.patrol.runner import (
    persist_patrol_snapshots as _persist_patrol_snapshots,  # noqa: F401
)
from src.observability.deprecations import observe_legacy_path

settings = get_settings()
log = get_logger(__name__)


@celery.task(
    bind=True,
    name="record_celery_beat_heartbeat",
    autoretry_for=(Exception,),
    max_retries=2,
    retry_backoff=2,
)
def record_celery_beat_heartbeat_task(self) -> dict[str, object]:
    from src.core.celery_tasks.beat_health import record_celery_beat_heartbeat

    return record_celery_beat_heartbeat(
        worker_id=str(getattr(self.request, "hostname", None) or "unknown"),
    )


@celery.task(name="cleanup_expired_patrol_outputs")
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


@celery.task(name="publish_rollout_outbox")
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
                queue="netops.patrol",
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
            worker_owner=str(
                getattr(self.request, "hostname", None) or "unknown"
            ),
            worker_task_id=str(
                getattr(self.request, "id", None) or ""
            )
            or None,
        )
    except Exception as exc:
        if int(getattr(self.request, "retries", 0)) >= int(
            self.max_retries
        ):
            service.pause_campaign_after_failure(
                stage_execution_id,
                reason=f"rollout execution retries exhausted: {exc}",
            )
            raise
        raise self.retry(
            exc=exc,
            countdown=10
            * (2 ** int(getattr(self.request, "retries", 0))),
        )


@celery.task(name="reconcile_rollout_live_captures")
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
                    "live capture failed: "
                    f"{result.get('failure_code') or result.get('error')}"
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


@celery.task(name="dispatch_patrol_batch", max_retries=3, retry_backoff=5)
def dispatch_patrol_batch_task(patrol_batch_id: str) -> dict[str, Any]:
    return DispatchPatrolBatchHandler().handle(patrol_batch_id)


@celery.task(name="reconcile_patrol_batch_dispatches")
def reconcile_patrol_batch_dispatches_task() -> dict[str, Any]:
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    batch_ids = PatrolBatchManifestRepository().pending_dispatch_batch_ids(limit=100)
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

    delivery_info = dict(
        getattr(self.request, "delivery_info", None) or {}
    )
    executing_queue = str(
        delivery_info.get("routing_key")
        or delivery_info.get("exchange")
        or ""
    ).strip()
    trusted_worker_queues = (
        [executing_queue]
        if executing_queue == "netops.patrol"
        else []
    )
    readiness = PatrolProductionReadinessService().evaluate(
        workflow_name="patrol-attested-offline-current",
        launch_mode="workflow",
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
            "readiness_status": str(
                readiness.get("status") or "blocked"
            ),
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
def reconcile_patrol_batch_reports_task() -> dict[str, Any]:
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    repository = PatrolBatchManifestRepository()
    batch_ids = repository.terminal_report_candidate_ids(limit=100)
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
def materialize_patrol_batch_report_task(
    patrol_batch_id: str,
    generated_by: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
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
            uses_check_item_runtime = bool(
                batch is not None and batch.runtime_manifest_id
            )
        if uses_check_item_runtime:
            result = CheckItemReportMaterializer(
                object_storage=get_minio_storage(),
            ).materialize(
                patrol_batch_id,
                generated_by=(
                    generated_by
                    or "celery:materialize_patrol_batch_report"
                ),
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


def _legacy_skill_execute(skill_name: str, **params: Any) -> dict[str, Any]:
    """兼容旧 Celery 任务：委托通用 Skill 执行器。"""
    from src.core.skills.executor import SkillExecutionError, execute_skill

    try:
        return execute_skill(skill_name, params)
    except SkillExecutionError as exc:
        return {"success": False, "message": str(exc), "error": str(exc)}


@celery.task(bind=True, max_retries=0)
def execute_firewall_policy_task(self, **kwargs):
    """[兼容] 委托 firewall-policy-generator；Workflow 新路径请用 execute_skill_task。"""
    return _legacy_skill_execute("firewall-policy-generator", **kwargs)


@celery.task(bind=True, max_retries=1)
def execute_itsm_change_ticket_task(self, **kwargs):
    """[兼容] 委托 itsm-change-ticket-writer；Workflow 新路径请用 execute_skill_task。"""
    return _legacy_skill_execute("itsm-change-ticket-writer", **kwargs)


@celery.task(bind=True, max_retries=3, retry_backoff=5)
def execute_itsm_callback_task(self, **kwargs):
    """[兼容] 委托 itsm-callback；Workflow 新路径请用 execute_skill_task。"""
    return _legacy_skill_execute("itsm-callback", **kwargs)


@celery.task(bind=True, max_retries=3, retry_backoff=2)
def execute_config_backup_task(
    self,
    filter_params: dict[str, Any] | None = None,
    ticket_id: str | None = None,
    execution_plan: dict[str, Any] | None = None,
    **kwargs,
):
    """执行配置备份任务"""
    execution_start = datetime.now()

    log.info(
        "config_backup_task_started",
        filter_params=filter_params,
        execution_plan=execution_plan,
        ticket_id=ticket_id,
    )

    try:
        result = DeviceBackupRunner().run(
            DeviceBackupRunRequest(
                filter_params=filter_params,
                ticket_id=ticket_id,
                execution_plan=execution_plan,
            )
        )
        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        log.info(
            "config_backup_task_completed",
            ticket_id=ticket_id,
            total_devices=result.get("total_devices"),
            success_devices=result.get("success_devices"),
            failed_devices=result.get("failed_devices"),
            duration_ms=execution_time_ms,
        )

        return result

    except Exception as e:
        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        log.error(
            "config_backup_task_failed",
            ticket_id=ticket_id,
            duration_ms=execution_time_ms,
            error=str(e),
            exc_info=e,
        )
        raise e


def _patrol_redelivery_decision(request: Any, *, mode: str = "production") -> dict[str, Any]:
    return PatrolRunCoordinator().prepare_delivery(request, mode=mode)


def _patrol_attempt_number(request: Any) -> int:
    """Compatibility helper for callers that only need the allocated number."""
    return PatrolRunCoordinator().attempt_number(request)


@celery.task(
    bind=True,
    max_retries=3,
    retry_backoff=2,
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_device_patrol_task(
    self,
    filter_params: dict[str, Any] | None = None,
    ticket_id: str | None = None,
    save_baseline: bool = False,
    mode: str = "production",
    execution_plan: dict[str, Any] | None = None,
    execution_plan_id: str | None = None,
    **kwargs,
):
    """执行设备巡检任务"""
    execution_start = datetime.now()

    log.info(
        "device_patrol_task_started",
        filter_params=filter_params,
        execution_plan=execution_plan,
        execution_plan_id=execution_plan_id,
        ticket_id=ticket_id,
        save_baseline=save_baseline,
    )

    try:
        redelivery = _patrol_redelivery_decision(self.request, mode=mode)
        if redelivery["action"] == "already_completed":
            return {
                "success": True,
                "status": redelivery["action"],
                "snapshot_run_id": redelivery.get("run_id"),
                "redelivery_skipped": True,
                "reason": redelivery.get("reason"),
            }
        result = ExecutePatrolHandler().handle(
            ExecutePatrolCommand(
                filter_params=filter_params,
                ticket_id=ticket_id,
                save_baseline=save_baseline,
                mode=mode,
                execution_plan=execution_plan,
                execution_plan_id=execution_plan_id,
                task_id=getattr(self.request, "id", None),
                attempt_number=int(redelivery.get("attempt_number") or 0),
                worker_id=getattr(self.request, "hostname", None),
                owner_token=str(redelivery.get("owner_token") or uuid.uuid4().hex),
            )
        )
        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        log.info(
            "device_patrol_task_completed",
            ticket_id=ticket_id,
            save_baseline=save_baseline,
            total_devices=result.get("total_devices"),
            success_devices=result.get("success_devices"),
            failed_devices=result.get("failed_devices"),
            snapshot_run_id=result.get("snapshot_run_id"),
            execution_plan_id=execution_plan_id,
            snapshot_commands=result.get("snapshot_commands"),
            duration_ms=execution_time_ms,
        )

        return result

    except PatrolPlatformError as e:
        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        log.error(
            "device_patrol_task_failed",
            ticket_id=ticket_id,
            execution_plan_id=execution_plan_id,
            save_baseline=save_baseline,
            duration_ms=execution_time_ms,
            error=str(e),
            exc_info=e,
        )
        countdown = min(60, 2 ** (int(getattr(self.request, "retries", 0)) + 1))
        raise self.retry(exc=e, countdown=countdown)
    except Exception as e:
        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        log.error(
            "device_patrol_task_failed_non_retryable",
            ticket_id=ticket_id,
            execution_plan_id=execution_plan_id,
            duration_ms=execution_time_ms,
            error=str(e),
            exc_info=e,
        )
        raise


@celery.task(bind=True, max_retries=1)
def archive_skill_executions_task(self, before_days: int | None = None):
    """定期归档 netops_skill_executions 到 MinIO。"""
    from src.core.skills.archive import archive_skill_executions

    result = archive_skill_executions(before_days=before_days)
    log.info(
        "archive_skill_executions_task_done",
        **{key: value for key, value in result.items() if key != "error"},
    )
    return result
