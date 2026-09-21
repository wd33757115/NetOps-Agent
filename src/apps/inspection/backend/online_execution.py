"""Durable online inspection state on the existing PatrolRun and frozen plan."""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import blake2b

from sqlalchemy import text

from src.infrastructure.db.models import (
    CollectionExecutionPlanRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolCurrentAuthorityRequestRecord,
    PatrolRun,
    PatrolSourceQualificationRecord,
)
from src.infrastructure.db.postgres import get_db_session

EXECUTION_KEY = "inspection_execution"
TERMINAL = {"completed", "failed", "cancelled"}


class OnlinePatrolBusyError(ValueError):
    pass


class OnlinePatrolStoppedError(ValueError):
    pass


class OnlinePatrolFencedError(ValueError):
    pass


class InvalidOnlinePlanError(ValueError):
    pass


@contextmanager
def execution_lock(task_id):
    key = int.from_bytes(
        blake2b(f"online-inspection:{task_id}".encode(), digest_size=8).digest(), "big", signed=True
    )
    with get_db_session() as db:
        if db.get_bind().dialect.name == "postgresql":
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
                raise OnlinePatrolBusyError("巡检任务正在执行")
        yield


def execution_state(run):
    return dict((run.metadata_json or {}).get(EXECUTION_KEY) or {}) if run else {}


def _target(db, batch_id, plan_id):
    batch = db.query(PatrolBatchRecord).filter_by(patrol_batch_id=batch_id).with_for_update().one()
    target = (
        db.query(PatrolBatchDeviceRecord)
        .filter_by(
            patrol_batch_id=batch_id,
            execution_plan_id=plan_id,
        )
        .with_for_update()
        .one()
    )
    return batch, target


def _cancelled(batch, target, db):
    if batch.source_authority_request_id:
        request = db.get(PatrolCurrentAuthorityRequestRecord, batch.source_authority_request_id)
        qualification = (
            db.get(PatrolSourceQualificationRecord, request.approved_qualification_id)
            if request
            else None
        )
        if (
            request is None
            or request.status not in {"dispatching", "completed"}
            or qualification is None
            or qualification.status != "approved"
        ):
            return True
    return (
        batch.archived
        or batch.dispatch_status in {"cancelled", "revoked"}
        or target.dispatch_status == "cancelled"
    )


class OnlinePatrolExecution:
    """One task owns one frozen device plan; a duplicate never recollects it."""

    def claim(self, batch_id, plan_id, task_id, worker_id=None):
        if not task_id:
            raise ValueError("缺少巡检任务 ID")
        with get_db_session() as db:
            batch, target = _target(db, batch_id, plan_id)
            expected_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"patrol-task|{batch_id}|{target.device_id}")
            )
            if task_id != expected_id or target.celery_task_id not in {None, task_id}:
                raise ValueError("巡检任务与冻结批次不匹配")
            if _cancelled(batch, target, db) or target.dispatch_status not in {
                "dispatching",
                "dispatched",
            }:
                return None
            if (
                batch.launch_mode != "direct"
                or batch.patrol_mode != "production"
                or batch.workflow_name
                not in {"patrol-change-event", "patrol-attested-offline-current"}
                or not batch.runtime_manifest_id
            ):
                raise ValueError("固定在线巡检需要已冻结的生产检查项批次")
            plan = db.get(CollectionExecutionPlanRecord, plan_id)
            if (
                plan is None
                or plan.patrol_batch_id != batch_id
                or plan.device_id != target.device_id
                or plan.mode != "production"
                or plan.status == "failed"
            ):
                raise InvalidOnlinePlanError("巡检采集计划已失效")
            frozen = dict(plan.plan_json or {})
            if (
                frozen.get("runtime_manifest_id") != batch.runtime_manifest_id
                or frozen.get("runtime_manifest_digest") != batch.runtime_manifest_digest
            ):
                raise InvalidOnlinePlanError("巡检运行包与批次冻结摘要不匹配")
            run_id = f"patrol-{task_id}"
            row = db.query(PatrolRun).filter_by(id=run_id).with_for_update().one_or_none()
            if row is not None:
                # A worker crash is settled by recovery. Never reconnect to a
                # device merely because a broker redelivered the same message.
                return None
            now = datetime.now(timezone.utc)
            owner = uuid.uuid4().hex
            db.add(
                PatrolRun(
                    id=run_id,
                    logical_run_id=run_id,
                    attempt_number=0,
                    source="device-patrol",
                    mode="production",
                    status="reserved",
                    collection_status="running",
                    analysis_status="pending",
                    run_purpose="scheduled_patrol",
                    owner_token=owner,
                    owner_generation=1,
                    worker_id=worker_id,
                    heartbeat_at=now,
                    started_at=now,
                    metadata_json={
                        "patrol_batch_id": batch_id,
                        "execution_plan_id": plan_id,
                        EXECUTION_KEY: {
                            "status": "running",
                            "phase": "collection",
                            "steps": {},
                            "task_id": task_id,
                            "started_at": now.isoformat(),
                        },
                    },
                    created_at=now,
                    updated_at=now,
                )
            )
            return {
                "run_id": run_id,
                "owner_token": owner,
                "task_id": task_id,
                "patrol_batch_id": batch_id,
                "execution_plan_id": plan_id,
                "device_id": target.device_id,
                "execution_plan": frozen,
                "replay": batch.workflow_name == "patrol-attested-offline-current",
                "ticket_id": batch.ticket_id,
                "save_baseline": bool(batch.save_baseline),
            }

    def checkpoint(self, execution, phase, results):
        with get_db_session() as db:
            batch, target = _target(
                db, execution["patrol_batch_id"], execution["execution_plan_id"]
            )
            row = db.query(PatrolRun).filter_by(id=execution["run_id"]).with_for_update().one()
            state = execution_state(row)
            if row.owner_token != execution["owner_token"] or state.get("status") != "running":
                raise OnlinePatrolFencedError("巡检执行所有权已失效")
            if _cancelled(batch, target, db):
                raise OnlinePatrolStoppedError("巡检已取消")
            now = datetime.now(timezone.utc)
            row.metadata_json = {
                **(row.metadata_json or {}),
                EXECUTION_KEY: {
                    **state,
                    "phase": phase,
                    "steps": results,
                    "updated_at": now.isoformat(),
                },
            }
            row.updated_at = now

    def fail_admission(self, batch_id, plan_id, task_id, error):
        with get_db_session() as db:
            batch, target = _target(db, batch_id, plan_id)
            expected = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"patrol-task|{batch_id}|{target.device_id}")
            )
            if (
                task_id != expected
                or _cancelled(batch, target, db)
                or db.get(PatrolRun, f"patrol-{task_id}")
            ):
                return None
            target.dispatch_status = "failed"
            target.failure_code = "invalid_frozen_plan"
            target.error_message = str(error)[:2000]
            target.updated_at = datetime.now(timezone.utc)
            return {
                "run_id": f"patrol-{task_id}",
                "patrol_batch_id": batch_id,
                "device_id": target.device_id,
                "execution_plan_id": plan_id,
            }

    def finish(self, execution, *, results, error=None, phase=None):
        with get_db_session() as db:
            batch, target = _target(
                db, execution["patrol_batch_id"], execution["execution_plan_id"]
            )
            row = db.query(PatrolRun).filter_by(id=execution["run_id"]).with_for_update().one()
            state = execution_state(row)
            if row.owner_token != execution["owner_token"] or state.get("status") != "running":
                return False
            status = (
                "cancelled" if _cancelled(batch, target, db) else "failed" if error else "completed"
            )
            now = datetime.now(timezone.utc)
            row.metadata_json = {
                **(row.metadata_json or {}),
                "celery_task_completed": True,
                "celery_task_completed_at": now.isoformat(),
                EXECUTION_KEY: {
                    **state,
                    "status": status,
                    "phase": phase or state.get("phase"),
                    "steps": results,
                    "error": error,
                    "completed_at": now.isoformat(),
                },
            }
            if status != "completed":
                row.analysis_status = "failed"
                row.baseline_eligible = False
                if row.collection_status == "running":
                    row.status = row.collection_status = "failed"
            row.updated_at = now
            row.completed_at = row.completed_at or now
            row.lease_until = None
            db.flush()
            if batch.source_authority_request_id:
                from .online_reads import all_targets_terminal

                targets = (
                    db.query(PatrolBatchDeviceRecord)
                    .filter_by(
                        patrol_batch_id=batch.patrol_batch_id,
                    )
                    .all()
                )
                if all_targets_terminal(db, targets):
                    request = db.get(
                        PatrolCurrentAuthorityRequestRecord, batch.source_authority_request_id
                    )
                    if request and request.status == "dispatching":
                        request.status = "completed"
                        request.updated_at = now
            return status
