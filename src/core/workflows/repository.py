# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 持久化。"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.db.models import (
    Notification,
    PatrolBatchDeviceRecord,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session

TERMINAL_WORKFLOW_STATUSES = {"completed", "failed", "cancelled"}
QUEUED_WORKFLOW_STATUSES = {"pending", "queued", "dispatching"}
RUNNING_WORKFLOW_STATUSES = {"running", "in_progress", "collecting"}
_FAILURE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _now():
    return datetime.now(timezone.utc)


def create_workflow_run(
    *,
    template_name: str,
    context: dict[str, Any],
    ticket_id: str | None = None,
    source: str = "chat",
    user_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
) -> str:
    run_id = str(run_id or uuid.uuid4())
    with get_db_session() as db:
        if db.get(WorkflowRun, run_id) is not None:
            return run_id
        run = WorkflowRun(
            id=run_id,
            template_name=template_name,
            ticket_id=ticket_id,
            source=source,
            user_id=user_id,
            thread_id=thread_id,
            status="pending",
            context=context,
            current_step_index=0,
        )
        db.add(run)
        db.flush()
    return run_id


def create_workflow_steps(run_id: str, steps: list[dict[str, Any]]) -> list[WorkflowStepRecord]:
    records: list[WorkflowStepRecord] = []
    with get_db_session() as db:
        existing = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.run_id == run_id)
            .order_by(WorkflowStepRecord.step_index)
            .all()
        )
        if existing:
            return existing
        for idx, step in enumerate(steps):
            rec = WorkflowStepRecord(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"workflow-step|{run_id}|{idx}")),
                run_id=run_id,
                step_index=idx,
                step_name=step["name"],
                skill_name=step["skill_name"],
                status="pending",
            )
            db.add(rec)
            records.append(rec)
        db.flush()
    return records


def get_workflow_run(run_id: str) -> WorkflowRun | None:
    with get_db_session() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if run:
            db.expunge(run)
        return run


def list_workflow_steps(run_id: str) -> list[WorkflowStepRecord]:
    with get_db_session() as db:
        steps = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.run_id == run_id)
            .order_by(WorkflowStepRecord.step_index)
            .all()
        )
        for s in steps:
            db.expunge(s)
        return steps


def update_run_context(run_id: str, context: dict[str, Any]) -> None:
    with get_db_session() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if run:
            run.context = context
            run.updated_at = _now()


def list_child_runs(parent_run_id: str) -> list[WorkflowRun]:
    """列出子 Workflow Run（subworkflow 嵌套）。"""
    with get_db_session() as db:
        candidates = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.source == "subworkflow")
            .order_by(WorkflowRun.created_at.asc())
            .all()
        )
        runs = [r for r in candidates if (r.context or {}).get("parent_run_id") == parent_run_id]
        for r in runs:
            db.expunge(r)
        return runs


def update_run_status(
    run_id: str,
    status: str,
    *,
    error: str | None = None,
    failure_code: str | None = None,
) -> bool:
    with get_db_session() as db:
        run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == run_id)
            .with_for_update()
            .first()
        )
        if not run:
            return False
        if str(run.status or "") in TERMINAL_WORKFLOW_STATUSES:
            return False
        run.status = status
        run.updated_at = _now()
        if error:
            run.error_message = error
        elif status == "completed":
            run.error_message = None
        if status == "failed":
            raw_failure_code = str(failure_code or "").strip()
            stable_failure_code = (
                raw_failure_code
                if _FAILURE_CODE_RE.fullmatch(raw_failure_code)
                else "workflow_failed"
            )
            run.context = {
                **dict(run.context or {}),
                "failure_code": stable_failure_code,
            }
            targets = (
                db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.workflow_run_id == run_id)
                .all()
            )
            for target in targets:
                target.failure_code = stable_failure_code
                target.error_message = error
                target.updated_at = run.updated_at
        if status in TERMINAL_WORKFLOW_STATUSES:
            run.completed_at = _now()
        return True


def update_run_step_index(
    run_id: str,
    step_index: int,
    *,
    status: str = "running",
) -> bool:
    with get_db_session() as db:
        run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == run_id)
            .with_for_update()
            .first()
        )
        if not run or str(run.status or "") in TERMINAL_WORKFLOW_STATUSES:
            return False
        run.current_step_index = step_index
        run.status = status
        run.updated_at = _now()
        return True


def reconcile_stale_patrol_workflow_runs(
    *,
    queued_stale_seconds: int,
    running_stale_seconds: int,
    limit: int = 100,
) -> dict[str, Any]:
    """Fail orphaned formal-patrol workflows and preserve a retryable reason."""

    now = _now()
    queued_cutoff = now - timedelta(seconds=max(60, int(queued_stale_seconds)))
    running_cutoff = now - timedelta(seconds=max(60, int(running_stale_seconds)))
    reconciled: list[str] = []
    failure_counts: dict[str, int] = {}

    with get_db_session() as db:
        patrol_run_ids = (
            db.query(PatrolBatchDeviceRecord.workflow_run_id)
            .filter(PatrolBatchDeviceRecord.workflow_run_id.is_not(None))
            .distinct()
        )
        rows = (
            db.query(WorkflowRun)
            .filter(
                WorkflowRun.id.in_(patrol_run_ids),
                WorkflowRun.updated_at.is_not(None),
                (
                    WorkflowRun.status.in_(QUEUED_WORKFLOW_STATUSES)
                    & (WorkflowRun.updated_at < queued_cutoff)
                )
                | (
                    WorkflowRun.status.in_(RUNNING_WORKFLOW_STATUSES)
                    & (WorkflowRun.updated_at < running_cutoff)
                ),
            )
            .order_by(WorkflowRun.updated_at, WorkflowRun.id)
            .limit(max(1, min(int(limit), 1000)))
            .with_for_update(skip_locked=True)
            .all()
        )
        for run in rows:
            previous_status = str(run.status or "unknown")
            queued_timeout = previous_status in QUEUED_WORKFLOW_STATUSES
            failure_code = (
                "workflow_queue_timeout"
                if queued_timeout
                else "workflow_heartbeat_expired"
            )
            threshold_seconds = (
                max(60, int(queued_stale_seconds))
                if queued_timeout
                else max(60, int(running_stale_seconds))
            )
            message = (
                f"Workflow remained {previous_status} without progress for more than "
                f"{threshold_seconds} seconds and was reconciled as failed"
            )
            previous_updated_at = run.updated_at
            run.status = "failed"
            run.error_message = message
            run.completed_at = now
            run.updated_at = now
            run.context = {
                **dict(run.context or {}),
                "failure_code": failure_code,
                "retryable": True,
                "reconciled_at": now.isoformat(),
                "stale_previous_status": previous_status,
                "stale_last_updated_at": (
                    previous_updated_at.isoformat()
                    if hasattr(previous_updated_at, "isoformat")
                    else str(previous_updated_at or "")
                ),
            }

            active_steps = (
                db.query(WorkflowStepRecord)
                .filter(
                    WorkflowStepRecord.run_id == run.id,
                    WorkflowStepRecord.status.in_(["queued", "running"]),
                )
                .all()
            )
            for step in active_steps:
                step.status = "failed"
                step.error_message = message
                step.completed_at = now

            targets = (
                db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.workflow_run_id == run.id)
                .all()
            )
            for target in targets:
                target.failure_code = failure_code
                target.error_message = message
                target.updated_at = now

            reconciled.append(str(run.id))
            failure_counts[failure_code] = failure_counts.get(failure_code, 0) + 1

    return {
        "reconciled_count": len(reconciled),
        "run_ids": reconciled,
        "failure_counts": failure_counts,
    }


def mark_step_queued(step_id: str, celery_task_id: str) -> bool:
    with get_db_session() as db:
        run_id = (
            db.query(WorkflowStepRecord.run_id)
            .filter(WorkflowStepRecord.id == step_id)
            .scalar()
        )
        if not run_id:
            return False
        run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == run_id)
            .with_for_update()
            .first()
        )
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            return False
        step = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.id == step_id)
            .with_for_update()
            .first()
        )
        if not step or str(step.status or "pending") not in {"pending", "queued"}:
            return False
        step.status = "queued"
        step.celery_task_id = celery_task_id
        return True


def mark_step_running(step_id: str, celery_task_id: str) -> bool:
    with get_db_session() as db:
        run_id = (
            db.query(WorkflowStepRecord.run_id)
            .filter(WorkflowStepRecord.id == step_id)
            .scalar()
        )
        if not run_id:
            return False
        run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == run_id)
            .with_for_update()
            .first()
        )
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            return False
        step = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.id == step_id)
            .with_for_update()
            .first()
        )
        if not step:
            return False
        if str(step.status or "pending") not in {"pending", "queued"}:
            return False
        step.status = "running"
        step.celery_task_id = celery_task_id
        step.started_at = _now()
        run.status = "running"
        run.updated_at = _now()
        return True


def mark_step_completed(step_id: str, result: dict[str, Any]) -> bool:
    with get_db_session() as db:
        run_id = (
            db.query(WorkflowStepRecord.run_id)
            .filter(WorkflowStepRecord.id == step_id)
            .scalar()
        )
        if not run_id:
            return False
        run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == run_id)
            .with_for_update()
            .first()
        )
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            return False
        step = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.id == step_id)
            .with_for_update()
            .first()
        )
        if not step or str(step.status or "pending") not in {
            "pending",
            "queued",
            "running",
        }:
            return False
        step.status = "completed" if result.get("success") else "failed"
        step.result = result
        step.output_artifacts = result.get("artifacts")
        step.error_message = result.get("error")
        step.completed_at = _now()
        return True


def mark_step_failed(step_id: str, error: str) -> bool:
    with get_db_session() as db:
        run_id = (
            db.query(WorkflowStepRecord.run_id)
            .filter(WorkflowStepRecord.id == step_id)
            .scalar()
        )
        if not run_id:
            return False
        run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == run_id)
            .with_for_update()
            .first()
        )
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            return False
        step = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.id == step_id)
            .with_for_update()
            .first()
        )
        if not step or str(step.status or "pending") in TERMINAL_WORKFLOW_STATUSES:
            return False
        step.status = "failed"
        step.error_message = error
        step.completed_at = _now()
        return True


def cancel_workflow_run(
    run_id: str,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """Atomically cancel a run and fence every unfinished step."""

    now = _now()
    with get_db_session() as db:
        run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == run_id)
            .with_for_update()
            .first()
        )
        if run is None:
            return {
                "found": False,
                "changed": False,
                "run_id": run_id,
                "status": "missing",
                "task_ids": [],
                "patrol_batch_ids": [],
                "device_ids": [],
            }

        previous_status = str(run.status or "pending")
        if previous_status in {"completed", "failed"}:
            return {
                "found": True,
                "changed": False,
                "cancellable": False,
                "run_id": run_id,
                "previous_status": previous_status,
                "status": previous_status,
                "task_ids": [],
                "patrol_batch_ids": [],
                "device_ids": [],
            }

        steps = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.run_id == run_id)
            .order_by(WorkflowStepRecord.step_index)
            .with_for_update()
            .all()
        )
        task_ids = sorted(
            {
                str(step.celery_task_id)
                for step in steps
                if step.celery_task_id
                and str(step.status or "pending") not in TERMINAL_WORKFLOW_STATUSES
            }
        )
        targets = (
            db.query(PatrolBatchDeviceRecord)
            .filter(PatrolBatchDeviceRecord.workflow_run_id == run_id)
            .with_for_update()
            .all()
        )
        patrol_batch_ids = sorted({str(row.patrol_batch_id) for row in targets})
        device_ids = sorted({str(row.device_id) for row in targets})

        if previous_status == "cancelled":
            return {
                "found": True,
                "changed": False,
                "cancellable": True,
                "idempotent": True,
                "run_id": run_id,
                "previous_status": previous_status,
                "status": "cancelled",
                "task_ids": task_ids,
                "patrol_batch_ids": patrol_batch_ids,
                "device_ids": device_ids,
            }

        message = f"Cancelled by {actor}: {reason}"
        run.status = "cancelled"
        run.error_message = message
        run.completed_at = now
        run.updated_at = now
        run.context = {
            **dict(run.context or {}),
            "failure_code": "workflow_cancelled",
            "retryable": True,
            "cancelled_at": now.isoformat(),
            "cancelled_by": actor,
            "cancellation_reason": reason,
        }
        for step in steps:
            if str(step.status or "pending") in TERMINAL_WORKFLOW_STATUSES:
                continue
            step.status = "cancelled"
            step.error_message = message
            step.completed_at = now
        for target in targets:
            target.dispatch_status = "cancelled"
            target.failure_code = "workflow_cancelled"
            target.error_message = message
            target.dispatch_claim_token = None
            target.dispatch_lease_until = None
            target.updated_at = now

        return {
            "found": True,
            "changed": True,
            "cancellable": True,
            "idempotent": False,
            "run_id": run_id,
            "previous_status": previous_status,
            "status": "cancelled",
            "cancelled_at": now.isoformat(),
            "cancelled_by": actor,
            "reason": reason,
            "task_ids": task_ids,
            "patrol_batch_ids": patrol_batch_ids,
            "device_ids": device_ids,
        }


def create_notification(
    *,
    user_id: str,
    title: str,
    body: str = "",
    payload: dict[str, Any] | None = None,
    workflow_run_id: str | None = None,
    thread_id: str | None = None,
    level: str = "info",
    idempotency_key: str | None = None,
) -> str:
    nid = (
        str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))
        if idempotency_key
        else str(uuid.uuid4())
    )
    with get_db_session() as db:
        values = {
            "id": nid,
            "idempotency_key": idempotency_key,
            "user_id": str(user_id),
            "title": title,
            "body": body,
            "level": level,
            "payload": payload,
            "workflow_run_id": workflow_run_id,
            "thread_id": thread_id,
        }
        if idempotency_key:
            db.execute(
                pg_insert(Notification)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
        else:
            db.add(Notification(**values))
    return nid


def list_notifications(
    user_id: str, *, unread_only: bool = False, limit: int = 50
) -> list[Notification]:
    with get_db_session() as db:
        q = db.query(Notification).filter(Notification.user_id == str(user_id))
        if unread_only:
            q = q.filter(Notification.read_at.is_(None))
        items = q.order_by(Notification.created_at.desc()).limit(limit).all()
        for n in items:
            db.expunge(n)
        return items


def mark_notification_read(notification_id: str, user_id: str) -> bool:
    with get_db_session() as db:
        n = (
            db.query(Notification)
            .filter(Notification.id == notification_id, Notification.user_id == str(user_id))
            .first()
        )
        if not n:
            return False
        n.read_at = _now()
        return True


def count_unread_notifications(user_id: str) -> int:
    with get_db_session() as db:
        return (
            db.query(Notification)
            .filter(Notification.user_id == str(user_id), Notification.read_at.is_(None))
            .count()
        )


def clear_notifications(user_id: str) -> int:
    """删除用户全部站内通知，返回清除条数。"""
    with get_db_session() as db:
        return (
            db.query(Notification)
            .filter(Notification.user_id == str(user_id))
            .delete(synchronize_session=False)
        )


def list_workflow_runs(
    *,
    limit: int = 50,
    template_name: str | None = None,
    ticket_id: str | None = None,
    source: str | None = None,
    status: str | None = None,
    device_id: str | None = None,
    patrol_batch_id: str | None = None,
) -> list[WorkflowRun]:
    """列出最近的 Workflow 运行实例。"""
    with get_db_session() as db:
        q = db.query(WorkflowRun).order_by(WorkflowRun.created_at.desc())
        if template_name:
            q = q.filter(WorkflowRun.template_name == template_name)
        if ticket_id:
            q = q.filter(WorkflowRun.ticket_id == ticket_id)
        if source:
            q = q.filter(WorkflowRun.source == source)
        if status:
            q = q.filter(WorkflowRun.status == status)
        if device_id:
            q = q.filter(WorkflowRun.context["device_id"].as_string() == str(device_id))
        if patrol_batch_id:
            q = q.filter(
                WorkflowRun.context["patrol_batch_id"].as_string() == str(patrol_batch_id)
            )
        runs = q.limit(max(1, min(limit, 200))).all()
        for r in runs:
            db.expunge(r)
        return runs
