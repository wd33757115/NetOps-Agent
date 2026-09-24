"""Single-period and historical inspection, using the existing durable job record."""

from datetime import datetime, timedelta, timezone

from src.core.celery_tasks.celery_app import celery
from src.core.patrol.handlers import (
    BuildEventsHandler,
    DetectChangesHandler,
    NormalizePatrolHandler,
)
from src.core.patrol.offline_job_lock import OfflineJobBusyError, job_execution_lock
from src.core.patrol.offline_jobs import OfflinePatrolJobService, _json_safe
from src.core.patrol.reporting import render_run_report
from src.core.patrol.repository import _compact_analysis_result
from src.infrastructure.db.models import OfflinePatrolJobRecord
from src.infrastructure.db.postgres import get_db_session
from src.infrastructure.storage.minio_client import get_minio_storage

from .access import authorize, maintenance
from .access import execution_lock as app_execution_lock

TASK = "netops.inspection.execute_offline"


class ExecutionStoppedError(ValueError):
    pass


def _owned(job, execution_key, task_id):
    return (
        job is not None
        and job.purpose == "offline_patrol"
        and job.workflow_run_id is None
        and job.execution_idempotency_key == execution_key
        and job.celery_task_id == task_id
    )


def _checkpoint(job_id, execution_key, task_id, phase, results, percent):
    with get_db_session() as db:
        job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one()
        if not _owned(job, execution_key, task_id) or job.status != "running":
            raise ExecutionStoppedError("离线巡检已取消或执行凭据已失效")
        job.progress_json = {"phase": phase, "percent": percent}
        job.result_json = _json_safe({"steps": results})
        job.updated_at = datetime.now(timezone.utc)


def _require_success(result, phase):
    if not isinstance(result, dict) or not result.get("success"):
        detail = (
            result.get("error") or result.get("message") or result
            if isinstance(result, dict)
            else result
        )
        raise ValueError(f"{phase}: {detail}")
    # Full observations, changes and events already live in their evidence tables.
    # Do not retain or rewrite those payloads at every task checkpoint.
    return _compact_analysis_result(result)


def _store_report(job_id, task_id, report):
    storage = get_minio_storage()
    name = f"patrol-report-{report['run_id']}.md"
    key = f"inspection/offline/{job_id}/{task_id}/{name}"
    if not storage.upload_file(
        key, report["markdown"].encode("utf-8"), "text/markdown; charset=utf-8"
    ):
        raise ValueError("离线巡检报告保存失败，分析结果已保留")
    return {
        "report": {
            "object_key": key,
            "filename": name,
            "download_url": (
                f"/api/apps/inspection/patrol/offline-jobs/{job_id}/artifacts/saved-report"
            ),
        }
    }


@celery.task(
    name=TASK,
    bind=True,
    max_retries=0,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=1740,
    time_limit=1800,
)
def execute_offline(self, job_id, execution_key):
    task_id = str(self.request.id or "")
    try:
        with app_execution_lock(), job_execution_lock(job_id):
            return _execute(job_id, execution_key, task_id)
    except OfflineJobBusyError:
        return {"success": False, "ignored": True, "reason": "execution_busy"}


def _execute(job_id, execution_key, task_id):
    with get_db_session() as db:
        job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one_or_none()
        if not task_id or not _owned(job, execution_key, task_id) or job.status != "queued":
            return {"success": False, "ignored": True}
        owner_id = job.owner_id
        job_type, publish_events = job.job_type, bool(job.publish_events)
        job.status = "running"
        job.updated_at = datetime.now(timezone.utc)
    service = OfflinePatrolJobService()
    results, artifacts, summary = {}, {}, {}
    error = None
    try:
        authorize(owner_id)
        _checkpoint(job_id, execution_key, task_id, "quality_gate", results, 1)
        if job_type not in {"single_patrol", "history_compare"}:
            raise ValueError("不支持的离线巡检类型")
        results["quality_gate"] = _require_success(
            service.execution_quality_gate(job_id), "质量门禁"
        )

        def import_and_normalize(role, percent):
            phase = f"{role}_ingest"
            _checkpoint(job_id, execution_key, task_id, phase, results, percent)
            result = _require_success(
                service.import_period(
                    job_id,
                    role,
                    patrol_execution_key=execution_key,
                    patrol_task_id=task_id,
                ),
                phase,
            )
            results[phase] = result
            run_id = str(result.get("run_id") or "")
            if not run_id:
                raise ValueError(f"{phase}: 导入未返回巡检记录")
            phase = f"{role}_normalization"
            _checkpoint(job_id, execution_key, task_id, phase, results, percent + 10)
            results[phase] = _require_success(
                NormalizePatrolHandler().handle(
                    {
                        "run_id": run_id,
                        "update_current_state": False,
                        "include_observations": False,
                    }
                ),
                phase,
            )
            return run_id

        previous_run_id = (
            import_and_normalize("baseline", 10) if job_type == "history_compare" else None
        )
        current_run_id = import_and_normalize("current", 40)
        _checkpoint(job_id, execution_key, task_id, "change_detection", results, 65)
        change_params = {
            "current_run_id": current_run_id,
            "use_state_observations": True,
            "persist": True,
        }
        if previous_run_id:
            change_params.update(previous_run_id=previous_run_id, allow_partial_normalization=True)
        results["change_detection"] = _require_success(
            DetectChangesHandler().handle(change_params), "变化检测"
        )
        _checkpoint(job_id, execution_key, task_id, "event_building", results, 80)
        results["event_building"] = _require_success(
            BuildEventsHandler().handle(
                {
                    "run_id": current_run_id,
                    "persist": True,
                    "publish": publish_events,
                }
            ),
            "事件构建",
        )
        _checkpoint(job_id, execution_key, task_id, "report_generation", results, 90)
        report = _require_success(
            render_run_report(run_id=current_run_id, output_format="markdown"), "报告生成"
        )
        # Content is stored as an artifact; retain counts/quality in the job record.
        summary = {
            key: value
            for key, value in report.items()
            if key not in {"markdown", "content", "output_path"}
        }
        results["report_generation"] = summary
        _checkpoint(job_id, execution_key, task_id, "report_storage", results, 95)
        artifacts = _store_report(job_id, task_id, report)
        _checkpoint(job_id, execution_key, task_id, "finishing", results, 99)
    except ExecutionStoppedError:
        return {"success": False, "ignored": True, "reason": "execution_stopped"}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:2000]

    with get_db_session() as db:
        job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one()
        if not _owned(job, execution_key, task_id) or job.status != "running":
            return {"success": False, "ignored": True, "reason": "execution_stopped"}
        job.status = "failed" if error else "completed"
        job.error_message = error
        job.result_json = _json_safe({**summary, "success": not error, "steps": results})
        job.artifacts_json = artifacts
        job.progress_json = {
            "phase": job.status,
            "percent": 100,
            "failed_phase": (job.progress_json or {}).get("phase") if error else None,
        }
        job.completed_at = job.updated_at = datetime.now(timezone.utc)
    return {"success": not error, "job_id": job_id, "error": error}


def dispatch(job_id, execution_key, task_id):
    try:
        execute_offline.apply_async(
            args=[job_id, execution_key], task_id=task_id, queue="netops.patrol"
        )
    except Exception:
        # The queued row is authoritative; Beat can redeliver a lost broker ACK.
        return False
    return True


@celery.task(name="netops.inspection.recover_offline")
@maintenance
def recover_offline():
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=35)
    with get_db_session() as db:
        ids = [
            row.id
            for row in db.query(OfflinePatrolJobRecord)
            .filter(
                OfflinePatrolJobRecord.purpose == "offline_patrol",
                OfflinePatrolJobRecord.workflow_run_id.is_(None),
                OfflinePatrolJobRecord.status.in_(["queued", "running", "previewing"]),
            )
            .order_by(OfflinePatrolJobRecord.updated_at)
            .limit(200)
            .all()
        ]
    queued, interrupted = [], []
    for job_id in ids:
        try:
            with job_execution_lock(job_id), get_db_session() as db:
                job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one()
                if job.status == "previewing" and job.celery_task_id:
                    if job.updated_at.replace(tzinfo=timezone.utc) < datetime.now(
                        timezone.utc
                    ) - timedelta(minutes=2):
                        from .preview_tasks import preview_offline_patrol_job

                        preview_offline_patrol_job.apply_async(
                            args=[job.id], task_id=job.celery_task_id, queue="netops.patrol"
                        )
                    continue
                if (
                    job.workflow_run_id
                    or not job.celery_task_id
                    or not job.execution_idempotency_key
                ):
                    continue
                if job.status == "queued":
                    queued.append((job.id, job.execution_idempotency_key, job.celery_task_id))
                elif (
                    job.status == "running" and job.updated_at.replace(tzinfo=timezone.utc) < cutoff
                ):
                    job.status = "failed"
                    job.error_message = "执行已中断，已保留分析证据，请检查后重新执行"
                    job.completed_at = job.updated_at = datetime.now(timezone.utc)
                    job.progress_json = {**(job.progress_json or {}), "phase": "interrupted"}
                    interrupted.append(job.id)
        except OfflineJobBusyError:
            continue
    for values in queued:
        dispatch(*values)
    return {"queued": len(queued), "interrupted": interrupted}
