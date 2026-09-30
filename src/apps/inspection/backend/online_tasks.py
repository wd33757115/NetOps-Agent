"""Fixed online inspection: collect, normalize, detect, assess, then request a report."""

from datetime import datetime, timedelta, timezone

from src.core.celery_tasks.celery_app import celery
from src.core.check_items.run_finalizer import CheckItemRunFinalizer
from src.core.patrol.handlers import (
    BuildEventsHandler,
    DetectChangesHandler,
    ExecutePatrolCommand,
    ExecutePatrolHandler,
    NormalizePatrolHandler,
)
from src.core.patrol.offline_jobs import _json_safe
from src.infrastructure.db.models import PatrolBatchDeviceRecord, PatrolBatchRecord, PatrolRun
from src.infrastructure.db.postgres import get_db_session

from .access import authorize_batch, maintenance
from .access import execution_lock as app_execution_lock
from .online_execution import (
    EXECUTION_KEY,
    InvalidOnlinePlanError,
    OnlinePatrolBusyError,
    OnlinePatrolExecution,
    OnlinePatrolFencedError,
    OnlinePatrolStoppedError,
    _cancelled,
    execution_lock,
    execution_state,
)
from .reports import request_terminal_report

TASK = "netops.inspection.execute_online"
FAILURE_STATUS = {
    "collection": "collection_failed",
    "normalization": "normalization_failed",
    "changes": "comparison_failed",
    "events": "assessment_failed",
}


def _success(result, phase):
    if not isinstance(result, dict) or not result.get("success"):
        raise ValueError(f"{phase}: {str(result)[:2000]}")
    return _json_safe(result)


def _settle(execution, *, phase, cancelled=False, reason_code=None):
    return CheckItemRunFinalizer().fail_device(
        patrol_batch_id=execution["patrol_batch_id"],
        device_id=execution["device_id"],
        reason_code="patrol_batch_cancelled" if cancelled else reason_code or f"{phase}_failed",
        status="cancelled" if cancelled else FAILURE_STATUS.get(phase, "assessment_failed"),
        evidence_refs=(execution["run_id"], execution["execution_plan_id"]),
    )


@celery.task(
    name=TASK,
    bind=True,
    max_retries=0,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=3540,
    time_limit=3600,
)
def execute_online(self, patrol_batch_id, execution_plan_id):
    task_id = str(self.request.id or "")
    try:
        with app_execution_lock(), execution_lock(task_id):
            repository = OnlinePatrolExecution()
            try:
                execution = repository.claim(
                    patrol_batch_id, execution_plan_id, task_id, self.request.hostname
                )
            except InvalidOnlinePlanError as exc:
                rejected = repository.fail_admission(
                    patrol_batch_id, execution_plan_id, task_id, exc
                )
                if rejected:
                    _settle(rejected, phase="collection", reason_code="invalid_frozen_plan")
                    request_terminal_report(patrol_batch_id)
                return {"success": False, "status": "failed", "error": str(exc)}
            if not execution:
                return {"success": False, "ignored": True}
            return run_online(execution, repository, worker_id=self.request.hostname)
    except OnlinePatrolBusyError:
        return {"success": False, "ignored": True, "reason": "execution_busy"}


def run_online(execution, repository, *, worker_id=None):
    results, phase, error = {}, "collection", None
    try:
        actor = authorize_batch(execution["patrol_batch_id"])
        repository.checkpoint(execution, phase, results)
        if execution.get("replay"):
            from src.core.check_items.current_authority_replay import (
                AttestedOfflineCurrentReplayService,
            )

            collected = _success(
                AttestedOfflineCurrentReplayService().execute(
                    execution_plan=execution["execution_plan"],
                    execution_plan_id=execution["execution_plan_id"],
                    snapshot_run_id=execution["run_id"],
                    owner_token=execution["owner_token"],
                ),
                phase,
            )
        else:
            collected = _success(
                ExecutePatrolHandler().handle(
                    ExecutePatrolCommand(
                        execution_plan=execution["execution_plan"],
                        execution_plan_id=execution["execution_plan_id"],
                        ticket_id=execution["ticket_id"],
                        save_baseline=execution["save_baseline"],
                        task_id=execution["task_id"],
                        worker_id=worker_id,
                        owner_token=execution["owner_token"],
                        defer_completion=True,
                        actor_id=str(getattr(actor, "user_id", "system")),
                        source_app="inspection",
                    )
                ),
                phase,
            )
        if collected.get("snapshot_run_id") != execution["run_id"]:
            raise ValueError("采集结果与已领取的巡检记录不一致")
        manifest = dict(collected.get("runtime_asset_manifest") or {})
        if manifest.get("manifest_kind") != "check_item_runtime":
            raise ValueError("采集结果缺少冻结检查项运行包")
        results[phase] = collected
        phase = "normalization"
        repository.checkpoint(execution, phase, results)
        results[phase] = _success(
            NormalizePatrolHandler().handle(
                {
                    "run_id": execution["run_id"],
                    "update_current_state": True,
                }
            ),
            phase,
        )
        phase = "changes"
        repository.checkpoint(execution, phase, results)
        results[phase] = _success(
            DetectChangesHandler().handle(
                {
                    "current_run_id": execution["run_id"],
                    "device_id": execution["device_id"],
                    "use_state_observations": True,
                    "persist": True,
                    "runtime_asset_manifest": manifest,
                }
            ),
            phase,
        )
        phase = "events"
        repository.checkpoint(execution, phase, results)
        results[phase] = _success(
            BuildEventsHandler().handle(
                {
                    "run_id": execution["run_id"],
                    "device_id": execution["device_id"],
                    "patrol_batch_id": execution["patrol_batch_id"],
                    "runtime_asset_manifest": manifest,
                    "persist": True,
                    "publish": False,
                }
            ),
            phase,
        )
        _success(results[phase].get("check_item_assessment"), "检查项结论")
        repository.checkpoint(execution, "completed", results)
    except OnlinePatrolFencedError:
        return {"success": False, "ignored": True, "reason": "ownership_lost"}
    except OnlinePatrolStoppedError as exc:
        error = str(exc)
        _settle(execution, phase=phase, cancelled=True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:2000]
        # A failure becomes a terminal check-item conclusion, never a healthy result.
        try:
            repository.checkpoint(execution, phase, results)
        except OnlinePatrolFencedError:
            return {"success": False, "ignored": True, "reason": "ownership_lost"}
        except OnlinePatrolStoppedError:
            _settle(execution, phase=phase, cancelled=True)
        else:
            _settle(execution, phase=phase)
    status = repository.finish(execution, results=results, error=error, phase=phase)
    request_terminal_report(execution["patrol_batch_id"], run_id=execution["run_id"])
    return {
        "success": status == "completed",
        "status": status,
        "snapshot_run_id": execution["run_id"],
        "error": error,
    }


@celery.task(name="netops.inspection.recover_online")
@maintenance
def recover_online():
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=65)
    with get_db_session() as db:
        missing_run = (
            ~db.query(PatrolRun.id)
            .filter(
                PatrolRun.id == "patrol-" + PatrolBatchDeviceRecord.celery_task_id,
            )
            .exists()
        )
        queued = [
            (target.patrol_batch_id, target.execution_plan_id, target.celery_task_id)
            for target in db.query(PatrolBatchDeviceRecord)
            .join(
                PatrolBatchRecord,
                PatrolBatchRecord.patrol_batch_id == PatrolBatchDeviceRecord.patrol_batch_id,
            )
            .filter(
                PatrolBatchRecord.launch_mode == "direct",
                PatrolBatchRecord.patrol_mode == "production",
                PatrolBatchRecord.workflow_name.in_(
                    ["patrol-change-event", "patrol-attested-offline-current"]
                ),
                PatrolBatchRecord.archived.is_(False),
                PatrolBatchRecord.dispatch_status.notin_(["cancelled", "revoked"]),
                PatrolBatchDeviceRecord.dispatch_status == "dispatched",
                PatrolBatchDeviceRecord.celery_task_id.is_not(None),
                PatrolBatchDeviceRecord.updated_at
                < datetime.now(timezone.utc) - timedelta(minutes=2),
                missing_run,
            )
            .order_by(PatrolBatchDeviceRecord.updated_at)
            .limit(100)
            .all()
        ]
        rows = (
            db.query(PatrolRun)
            .filter(
                PatrolRun.updated_at < cutoff,
                PatrolRun.metadata_json[EXECUTION_KEY]["status"].as_string() == "running",
            )
            .order_by(PatrolRun.updated_at)
            .limit(100)
            .all()
        )
        candidates = [row.to_dict() for row in rows]
    interrupted = []
    for row in candidates:
        state = dict((row.get("metadata_json") or {}).get(EXECUTION_KEY) or {})
        task_id = state.get("task_id")
        if not task_id:
            continue
        try:
            with app_execution_lock(), execution_lock(task_id):
                with get_db_session() as db:
                    current = db.get(PatrolRun, row["id"])
                    if execution_state(current).get("status") != "running":
                        continue
                    metadata = dict(current.metadata_json or {})
                    target = (
                        db.query(PatrolBatchDeviceRecord)
                        .filter_by(
                            execution_plan_id=metadata["execution_plan_id"],
                            patrol_batch_id=metadata["patrol_batch_id"],
                        )
                        .one()
                    )
                    batch = db.get(PatrolBatchRecord, metadata["patrol_batch_id"])
                    cancelled = _cancelled(batch, target, db)
                    execution = {
                        "run_id": current.id,
                        "owner_token": current.owner_token,
                        "patrol_batch_id": batch.patrol_batch_id,
                        "device_id": target.device_id,
                        "execution_plan_id": target.execution_plan_id,
                    }
                    state = execution_state(current)
                _settle(execution, phase=state.get("phase"), cancelled=cancelled)
                OnlinePatrolExecution().finish(
                    execution,
                    results=state.get("steps") or {},
                    error="Worker 执行中断，已保留证据，请检查后重新发起巡检",
                )
                request_terminal_report(execution["patrol_batch_id"], run_id=execution["run_id"])
                interrupted.append(execution["run_id"])
        except OnlinePatrolBusyError:
            continue
    dispatched, errors = 0, []
    for batch_id, plan_id, task_id in queued:
        try:
            execute_online.apply_async(
                kwargs={"patrol_batch_id": batch_id, "execution_plan_id": plan_id},
                task_id=task_id,
                queue="netops.patrol",
            )
            dispatched += 1
        except Exception as exc:
            errors.append({"task_id": task_id, "error": str(exc)[:500]})
    return {"interrupted": interrupted, "queued": dispatched, "dispatch_errors": errors}
