# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import uuid
from datetime import datetime
from typing import Any

from src.core.celery_tasks.celery_app import celery
from src.apps.rule_governance.backend.access import capture_admission

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
                task_id=getattr(self.request, "id", None),
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
@capture_admission
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
                actor_id=str(kwargs.get("ssh_actor_id") or "system"),
                source_app=str(kwargs.get("ssh_source_app") or "inspection"),
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

from src.apps.rule_governance.backend.rollout_tasks import (
    publish_rollout_outbox_task as publish_rollout_outbox_task,
    execute_rollout_stage_task as execute_rollout_stage_task,
    reconcile_rollout_live_captures_task as reconcile_rollout_live_captures_task,
)

from src.apps.inspection.backend.tasks import (
    cleanup_expired_patrol_outputs_task,
    dispatch_patrol_batch_task,
    materialize_patrol_batch_report_task,
    reconcile_current_authority_dispatches_task,
    reconcile_patrol_batch_dispatches_task,
    reconcile_patrol_batch_reports_task,
)
