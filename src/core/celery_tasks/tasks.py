# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import os
import sys
from datetime import datetime
from typing import Any

from src.core.celery_tasks.celery_app import celery

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, BASE_DIR)


from src.common.config import get_settings
from src.core.backup.runner import DeviceBackupRunner, DeviceBackupRunRequest
from src.core.logging import get_logger
from src.core.patrol.repository import PatrolRepository
from src.core.patrol.runner import (
    DevicePatrolRunner,
    DevicePatrolRunRequest,
    PatrolPlatformError,
)
from src.core.patrol.runner import (
    persist_patrol_snapshots as _persist_patrol_snapshots,  # noqa: F401
)

settings = get_settings()
log = get_logger(__name__)


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
    retries = int(getattr(request, "retries", 0) or 0)
    delivery_info = dict(getattr(request, "delivery_info", None) or {})
    if not delivery_info.get("redelivered"):
        return {"action": "execute", "attempt_number": retries, "run_id": None}
    task_id = str(getattr(request, "id", "") or "")
    if not task_id:
        return {"action": "execute", "attempt_number": max(1, retries), "run_id": None}
    return PatrolRepository().prepare_redelivery_attempt(
        f"patrol-{task_id}",
        worker_id=getattr(request, "hostname", None),
        mode=mode,
    ).to_dict()


def _patrol_attempt_number(request: Any) -> int:
    """Compatibility helper for callers that only need the allocated number."""
    retries = int(getattr(request, "retries", 0) or 0)
    delivery_info = dict(getattr(request, "delivery_info", None) or {})
    if not delivery_info.get("redelivered"):
        return retries
    task_id = str(getattr(request, "id", "") or "")
    if not task_id:
        return max(1, retries)
    decision = PatrolRepository().prepare_redelivery_attempt(f"patrol-{task_id}")
    if hasattr(decision, "attempt_number"):
        return int(decision.attempt_number)
    return int(decision)


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
        if redelivery["action"] in {"already_completed", "reuse_running"}:
            return {
                "success": True,
                "status": redelivery["action"],
                "snapshot_run_id": redelivery.get("run_id"),
                "redelivery_skipped": True,
                "reason": redelivery.get("reason"),
            }
        result = DevicePatrolRunner().run(
            DevicePatrolRunRequest(
                filter_params=filter_params,
                ticket_id=ticket_id,
                save_baseline=save_baseline,
                mode=mode,
                execution_plan=execution_plan,
                execution_plan_id=execution_plan_id,
                task_id=getattr(self.request, "id", None),
                attempt_number=int(redelivery.get("attempt_number") or 0),
                worker_id=getattr(self.request, "hostname", None),
            )
        )
        snapshot_run_id = result.get("snapshot_run_id")
        if snapshot_run_id:
            PatrolRepository().mark_celery_task_completed(str(snapshot_run_id))
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
