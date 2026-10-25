"""Import rule samples directly; no patrol analysis or Workflow dispatch."""

from datetime import datetime, timedelta, timezone

from src.core.celery_tasks.celery_app import celery
from src.core.patrol.offline_jobs import OfflinePatrolJobService
from src.infrastructure.db.models import OfflinePatrolJobRecord, OfflinePatrolPeriodRecord
from src.infrastructure.db.postgres import get_db_session

from .access import authorize, execution_lock, maintenance, require_running
from .source_preparation import prepare_source_samples

TASK = "netops.rule_governance.import_source"


@celery.task(name=TASK, bind=True, soft_time_limit=1740, time_limit=1800)
@maintenance
def import_source(self, job_id, execution_key):
    with get_db_session() as db:
        job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one()
        if (
            job.purpose != "rule_asset_source"
            or job.status != "queued"
            or job.execution_idempotency_key != execution_key
        ):
            return {"success": False, "ignored": True}
        owner = job.owner_id
        job.status = "running"
        periods = [
            p.role for p in db.query(OfflinePatrolPeriodRecord).filter_by(job_id=job_id).all()
        ]
    try:
        results = {}
        for role in periods:
            authorize(owner)
            results[role] = OfflinePatrolJobService().import_period(
                job_id, role, rule_source_execution_key=execution_key
            )
            if not results[role].get("success"):
                raise ValueError(f"样本周期 {role} 导入失败")
        authorize(owner)
        preparation = prepare_source_samples(job_id, execution_key)
        result = {
            "success": True,
            "preparation": preparation,
            "periods": results,
            "snapshot_count": sum(
                int(item.get("snapshot_count", len(item.get("snapshot_ids") or [])))
                for item in results.values()
            ),
            "new_snapshot_count": sum(
                int(item.get("new_snapshot_count") or 0) for item in results.values()
            ),
            "duplicate_snapshot_count": sum(
                int(item.get("duplicate_snapshot_count") or 0) for item in results.values()
            ),
        }
        error = None
    except Exception as exc:
        result, error = {"success": False}, str(exc)
    with get_db_session() as db:
        job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one()
        if job.status == "running" and job.execution_idempotency_key == execution_key:
            job.status = "failed" if error else "completed"
            job.result_json, job.error_message = result, error
            job.completed_at = datetime.now(timezone.utc)
            job.progress_json = {"phase": job.status, "percent": 100}
    return result


def dispatch(job_id, execution_key, task_id=None):
    # Queued rows survive a lost broker acknowledgement and are safe to redeliver.
    try:
        import_source.apply_async(args=[job_id, execution_key], task_id=task_id, queue="app.rule-governance")
    except Exception:
        return False
    return True


@celery.task(name="netops.rule_governance.recover_sources")
def recover_sources():
    # Do not settle an interrupted import while any package executor still holds
    # the shared lock (including capture/generator child processes).
    queued, interrupted = [], []
    with execution_lock(exclusive=True), get_db_session() as db:
        require_running()
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=35)
        for row in (
            db.query(OfflinePatrolJobRecord)
            .filter_by(purpose="rule_asset_source")
            .with_for_update()
            .all()
        ):
            if row.status == "queued":
                queued.append((row.id, row.execution_idempotency_key))
            elif (
                row.status in {"running", "previewing"}
                and row.updated_at.replace(tzinfo=timezone.utc) < cutoff
            ):
                row.status = "failed"
                row.error_message = "执行已中断，已保留导入证据，请检查后重新执行"
                interrupted.append(row.id)
    for job_id, key in queued:
        dispatch(job_id, key)
    return {"queued": len(queued), "interrupted": interrupted}


@celery.task(
    name="netops.rule_governance.preview_source", bind=True, soft_time_limit=1740, time_limit=1800
)
@maintenance
def preview_source(self, job_id):
    return OfflinePatrolJobService().run_preview(job_id, rule_source_task_id=self.request.id)
