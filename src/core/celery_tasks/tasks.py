# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from typing import Any

from src.core.celery_tasks.celery_app import celery

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, BASE_DIR)


from src.common.config import get_settings
from src.core.logging import get_logger
from src.infrastructure.storage.minio_client import get_minio_storage

settings = get_settings()
log = get_logger(__name__)


def _persist_patrol_snapshots(
    *,
    details: list[dict[str, Any]],
    snapshot_run_id: str,
    db_path: str | None = None,
    ticket_id: str | None = None,
    mode: str = "production",
) -> dict[str, Any]:
    """Import completed patrol reports into the snapshot store."""
    from src.core.patrol.raw_importer import import_raw_capture

    imported_commands = 0
    imported_devices = 0
    import_errors: list[str] = []
    for detail in details:
        output_file = detail.get("output_file")
        if not output_file or not os.path.isfile(str(output_file)):
            continue
        try:
            imported = import_raw_capture(
                file_path=str(output_file),
                mode=mode,
                run_id=snapshot_run_id,
                device_name=detail.get("device_name"),
                ip=detail.get("ip"),
                vendor=detail.get("vendor"),
                model=detail.get("model"),
                metadata={"ticket_id": ticket_id, "source": "device-patrol"},
            )
            imported_commands += int(imported.get("command_count") or 0)
            imported_devices += 1
        except Exception as exc:
            import_errors.append(f"{output_file}: {exc}")

    return {
        "snapshot_run_id": snapshot_run_id,
        "snapshot_storage": "postgresql",
        "legacy_db_path_ignored": db_path,
        "snapshot_devices": imported_devices,
        "snapshot_commands": imported_commands,
        "snapshot_errors": import_errors,
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
    filter_params: dict[str, Any],
    ticket_id: str | None = None,
    **kwargs
):
    """执行配置备份任务"""
    execution_start = datetime.now()

    log.info(
        "config_backup_task_started",
        filter_params=filter_params,
        ticket_id=ticket_id,
    )

    try:
        from src.core.device_ops.loader import import_netops_agent_tools

        netops = import_netops_agent_tools()
        ConfigBackupTool = netops.ConfigBackupTool
        DBManager = netops.DBManager
        DeviceFilter = netops.DeviceFilter

        db_manager = DBManager()
        backup_tool = ConfigBackupTool(db_manager)

        device_filter = DeviceFilter(**filter_params) if filter_params else DeviceFilter()

        import asyncio
        result = asyncio.run(backup_tool.backup_by_filter(device_filter))

        minio_client = get_minio_storage()
        download_url = None
        output_files = result.output_files or []

        if minio_client and minio_client.is_ready() and output_files:
            log.debug("config_backup_minio_upload_begin")

            if not ticket_id:
                ticket_id = f"BACKUP_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            with tempfile.TemporaryDirectory() as tmpdir:
                zip_path = os.path.join(tmpdir, f"config_backup_{ticket_id}.zip")
                output_dir = os.path.dirname(output_files[0]) if output_files else tmpdir
                shutil.make_archive(zip_path.replace(".zip", ""), "zip", output_dir)

                object_name = f"config_backup/{ticket_id}/{os.path.basename(zip_path)}"
                log.info("config_backup_minio_upload", object_name=object_name)

                with open(zip_path, "rb") as f:
                    upload_success = minio_client.upload_file(object_name, f)

                if upload_success:
                    download_url = minio_client.get_presigned_url(object_name, expires=3600*24*7)
                    log.info("config_backup_minio_uploaded", download_url=download_url)

        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        log.info(
            "config_backup_task_completed",
            ticket_id=ticket_id,
            total_devices=result.total_devices,
            success_devices=result.success_devices,
            failed_devices=result.failed_devices,
            duration_ms=execution_time_ms,
        )

        return {
            "status": "success",
            "action": "backup",
            "ticket_id": ticket_id,
            "download_url": download_url,
            "total_devices": result.total_devices,
            "success_devices": result.success_devices,
            "failed_devices": result.failed_devices,
            "output_files": result.output_files,
            "execution_time_ms": execution_time_ms,
            "message": result.message
        }

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


@celery.task(bind=True, max_retries=3, retry_backoff=2)
def execute_device_patrol_task(
    self,
    filter_params: dict[str, Any],
    ticket_id: str | None = None,
    save_baseline: bool = False,
    mode: str = "production",
    **kwargs
):
    """执行设备巡检任务"""
    execution_start = datetime.now()

    log.info(
        "device_patrol_task_started",
        filter_params=filter_params,
        ticket_id=ticket_id,
        save_baseline=save_baseline,
    )

    try:
        from src.core.device_ops.loader import import_netops_agent_tools
        from src.core.patrol.modes import normalize_patrol_mode

        mode = normalize_patrol_mode(mode)
        netops = import_netops_agent_tools()
        DBManager = netops.DBManager
        DeviceFilter = netops.DeviceFilter
        PatrolTool = netops.PatrolTool

        db_manager = DBManager()
        patrol_tool = PatrolTool(db_manager)

        device_filter = DeviceFilter(**filter_params) if filter_params else DeviceFilter()

        import asyncio
        result = asyncio.run(
            patrol_tool.patrol_by_filter(device_filter, save_baseline, mode=mode)
        )
        if not result.success:
            raise RuntimeError(result.message or "device patrol failed")

        snapshot_run_id = f"patrol-{getattr(self.request, 'id', None) or uuid.uuid4()}"
        snapshot_result = _persist_patrol_snapshots(
            details=result.details or [],
            snapshot_run_id=snapshot_run_id,
            ticket_id=ticket_id,
            mode=mode,
        )
        if snapshot_result["snapshot_errors"]:
            raise RuntimeError("; ".join(snapshot_result["snapshot_errors"]))

        minio_client = get_minio_storage()
        download_url = None
        output_files = result.output_files or []

        if minio_client and minio_client.is_ready() and output_files:
            log.debug("device_patrol_minio_upload_begin")

            if not ticket_id:
                ticket_id = f"PATROL_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            with tempfile.TemporaryDirectory() as tmpdir:
                zip_path = os.path.join(tmpdir, f"device_patrol_{ticket_id}.zip")
                output_dir = os.path.dirname(output_files[0]) if output_files else tmpdir
                shutil.make_archive(zip_path.replace(".zip", ""), "zip", output_dir)

                object_name = f"device_patrol/{ticket_id}/{os.path.basename(zip_path)}"
                log.info("device_patrol_minio_upload", object_name=object_name)

                with open(zip_path, "rb") as f:
                    upload_success = minio_client.upload_file(object_name, f)

                if upload_success:
                    download_url = minio_client.get_presigned_url(object_name, expires=3600*24*7)
                    log.info("device_patrol_minio_uploaded", download_url=download_url)

        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        log.info(
            "device_patrol_task_completed",
            ticket_id=ticket_id,
            save_baseline=save_baseline,
            total_devices=result.total_devices,
            success_devices=result.success_devices,
            failed_devices=result.failed_devices,
            snapshot_run_id=snapshot_run_id,
            snapshot_commands=snapshot_result["snapshot_commands"],
            duration_ms=execution_time_ms,
        )

        return {
            "status": "success",
            "action": "patrol",
            "ticket_id": ticket_id,
            "save_baseline": save_baseline,
            "mode": mode,
            "download_url": download_url,
            "total_devices": result.total_devices,
            "success_devices": result.success_devices,
            "failed_devices": result.failed_devices,
            "output_files": result.output_files,
            "execution_time_ms": execution_time_ms,
            "message": result.message,
            **snapshot_result,
        }

    except Exception as e:
        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        log.error(
            "device_patrol_task_failed",
            ticket_id=ticket_id,
            save_baseline=save_baseline,
            duration_ms=execution_time_ms,
            error=str(e),
            exc_info=e,
        )
        raise e


@celery.task(bind=True, max_retries=1)
def archive_skill_executions_task(self, before_days: int | None = None):
    """定期归档 netops_skill_executions 到 MinIO。"""
    from src.core.skills.archive import archive_skill_executions

    result = archive_skill_executions(before_days=before_days)
    log.info("archive_skill_executions_task_done", **{k: v for k, v in result.items() if k != "error"})
    return result
