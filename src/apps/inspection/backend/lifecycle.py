"""Close queued inspection work after all executing workers have released the App lock."""

from datetime import datetime, timezone

from src.infrastructure.db.models import (
    OfflinePatrolJobRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolCurrentAuthorityRequestRecord,
    PatrolRun,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session

from .access import execution_lock
from .online_execution import EXECUTION_KEY, TERMINAL, execution_state

RETIRED = (
    "patrol-change-event",
    "patrol-attested-offline-current",
    "patrol-offline-current",
    "patrol-history-change-event",
)
ACTIVE = ("queued", "running", "previewing")


def drain():
    settle = []
    with execution_lock(exclusive=True):
        with get_db_session() as db:
            now = datetime.now(timezone.utc)
            for job in (
                db.query(OfflinePatrolJobRecord)
                .filter_by(purpose="offline_patrol")
                .with_for_update()
                .all()
            ):
                if job.status in ACTIVE:
                    job.status, job.error_message = (
                        "failed",
                        "巡检应用已停用，已保留证据，请检查后重新提交",
                    )
                    job.updated_at = job.completed_at = now
            for batch in db.query(PatrolBatchRecord).with_for_update().all():
                if (batch.selector_json or {}).get("rollout_stage_execution_id"):
                    continue
                targets = (
                    db.query(PatrolBatchDeviceRecord)
                    .filter_by(patrol_batch_id=batch.patrol_batch_id)
                    .with_for_update()
                    .all()
                )
                unfinished = batch.dispatch_status in {
                    "pending",
                    "dispatching",
                    "awaiting_current_plan",
                }
                for target in targets:
                    run = (
                        db.get(PatrolRun, "patrol-" + target.celery_task_id)
                        if target.celery_task_id
                        else None
                    )
                    state = execution_state(run)
                    active = (state and state.get("status") not in TERMINAL) or (
                        not state
                        and not target.workflow_run_id
                        and target.dispatch_status
                        in {"pending", "dispatching", "dispatched", "awaiting_current_plan"}
                    )
                    if active:
                        unfinished = True
                        target.dispatch_status, target.error_message = "cancelled", "巡检应用已停用"
                        if run and state:
                            run.metadata_json = {
                                **run.metadata_json,
                                EXECUTION_KEY: {
                                    **state,
                                    "status": "cancelled",
                                    "error": "巡检应用已停用",
                                    "completed_at": now.isoformat(),
                                },
                            }
                            run.owner_token = None
                        if batch.runtime_manifest_id:
                            settle.append((batch.patrol_batch_id, target.device_id))
                if unfinished:
                    batch.dispatch_status = "cancelled"
                if batch.report_status in {"queued", "generating"}:
                    batch.report_status = "failed"
                if batch.source_authority_request_id and unfinished:
                    request = db.get(
                        PatrolCurrentAuthorityRequestRecord, batch.source_authority_request_id
                    )
                    if request and request.status in {"approved", "dispatching"}:
                        request.status = "failed"
            for run in (
                db.query(WorkflowRun)
                .filter(WorkflowRun.template_name.in_(RETIRED), WorkflowRun.status.notin_(TERMINAL))
                .with_for_update()
                .all()
            ):
                run.status, run.error_message = "cancelled", "巡检已迁移到固定任务"
                db.query(WorkflowStepRecord).filter(
                    WorkflowStepRecord.run_id == run.id, WorkflowStepRecord.status.notin_(TERMINAL)
                ).update({"status": "cancelled"})
        from src.core.check_items.run_finalizer import CheckItemRunFinalizer

        for batch_id, device_id in settle:
            CheckItemRunFinalizer().fail_device(
                patrol_batch_id=batch_id,
                device_id=device_id,
                reason_code="app_disabled",
                status="cancelled",
                evidence_refs=(),
            )


def pending():
    with get_db_session() as db:
        count = (
            db.query(OfflinePatrolJobRecord)
            .filter(
                OfflinePatrolJobRecord.purpose == "offline_patrol",
                OfflinePatrolJobRecord.status.in_(ACTIVE),
            )
            .count()
        )
        count += (
            db.query(WorkflowRun)
            .filter(WorkflowRun.template_name.in_(RETIRED), WorkflowRun.status.notin_(TERMINAL))
            .count()
        )
        for batch in db.query(PatrolBatchRecord).all():
            if (batch.selector_json or {}).get("rollout_stage_execution_id"):
                continue
            if batch.dispatch_status in {
                "pending",
                "dispatching",
                "awaiting_current_plan",
            } or batch.report_status in {"queued", "generating"}:
                count += 1
            for target in (
                db.query(PatrolBatchDeviceRecord)
                .filter_by(patrol_batch_id=batch.patrol_batch_id)
                .all()
            ):
                run = (
                    db.get(PatrolRun, "patrol-" + target.celery_task_id)
                    if target.celery_task_id
                    else None
                )
                state = execution_state(run)
                if (state and state.get("status") not in TERMINAL) or (
                    not state
                    and not target.workflow_run_id
                    and target.dispatch_status
                    in {"pending", "dispatching", "dispatched", "awaiting_current_plan"}
                ):
                    count += 1
        return count
