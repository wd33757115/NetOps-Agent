"""Read actual PatrolRun facts for direct batches, without synthetic Workflow rows."""

from collections import Counter
from datetime import datetime

from src.infrastructure.db.models import PatrolBatchDeviceRecord, PatrolBatchRecord, PatrolRun
from src.infrastructure.db.postgres import get_db_session

from .online_execution import TERMINAL, execution_state

PHASE_LABELS = {
    "collection": "命令采集与解析",
    "normalization": "状态归一化",
    "changes": "变化检测",
    "events": "事件与检查项判定",
}


def execution_steps(state):
    """Display persisted business progress; a returned result can still fail its gate."""
    result = []
    for phase, label in PHASE_LABELS.items():
        current = state.get("phase") == phase
        if current and state.get("status") in {"failed", "cancelled"}:
            status = state["status"]
        elif phase in (state.get("steps") or {}):
            status = "completed"
        elif current and state.get("status") == "running":
            status = "running"
        else:
            status = "not_started"
        result.append(
            {
                "phase": phase,
                "label": label,
                "status": status,
                "error": state.get("error") if current else None,
            }
        )
    return result


def batch_execution_rows(db, batch_id):
    """None denotes a historical/other executor, [] an empty fixed online batch."""
    batch = db.get(PatrolBatchRecord, batch_id)
    if (
        batch is None
        or getattr(batch, "launch_mode", None) != "direct"
        or batch.patrol_mode != "production"
        or batch.workflow_name not in {"patrol-change-event", "patrol-attested-offline-current"}
        or not batch.runtime_manifest_id
    ):
        return None
    targets = (
        db.query(PatrolBatchDeviceRecord)
        .filter(PatrolBatchDeviceRecord.patrol_batch_id == batch_id)
        .order_by(PatrolBatchDeviceRecord.created_at, PatrolBatchDeviceRecord.id)
        .all()
    )
    states = states_for_targets(db, targets)
    return [
        {
            **states[target.id],
            "run_id": states[target.id]["snapshot_run_id"],
            "execution_kind": "inspection",
            "device_id": target.device_id,
            "device_name": target.device_name or target.device_id,
            "created_at": target.created_at,
            "ticket_id": batch.ticket_id,
            "error": states[target.id]["error"] or target.error_message,
        }
        for target in targets
        if target.id in states
    ]


def device_execution_runs(db, device_id, *, batch_id=None, limit=10):
    query = (
        db.query(PatrolRun)
        .join(
            PatrolBatchDeviceRecord,
            PatrolRun.id == "patrol-" + PatrolBatchDeviceRecord.celery_task_id,
        )
        .filter(
            PatrolBatchDeviceRecord.device_id == device_id,
            PatrolRun.metadata_json["inspection_execution"]["status"].as_string().is_not(None),
        )
    )
    if batch_id:
        query = query.filter(PatrolBatchDeviceRecord.patrol_batch_id == batch_id)
    return query.order_by(PatrolRun.started_at.desc()).limit(limit).all()


def read_batch_execution_rows(batch_id):
    with get_db_session() as db:
        return batch_execution_rows(db, batch_id)


def pipeline_summary(rows):
    """Reuse the display contract without inventing Workflow steps or stage timings."""
    progress = [(row, execution_steps(row)) for row in rows]
    stages = []
    for index, (phase, label) in enumerate(PHASE_LABELS.items() if rows else []):
        counts = Counter(steps[index]["status"] for _row, steps in progress)
        completed, failed = counts["completed"], counts["failed"]
        active, cancelled = counts["running"], counts["cancelled"]
        status = (
            "running"
            if active
            else ("degraded" if completed else "failed")
            if failed
            else "cancelled"
            if cancelled
            else "completed"
            if rows and completed == len(rows)
            else "pending"
        )
        stages.append(
            {
                "key": phase,
                "step_index": index,
                "step_name": phase,
                "label": label,
                "description": label,
                "status": status,
                "total": len(rows),
                "status_counts": dict(counts),
                "completed_count": completed,
                "failed_count": failed,
                "active_count": active,
                "cancelled_count": cancelled,
                "pending_count": len(rows) - completed - failed - active - cancelled,
                "completion_rate": completed / len(rows) if rows else 0.0,
                "started_at": None,
                "completed_at": None,
                "duration": {
                    "sample_count": 0,
                    "average_seconds": None,
                    "p95_seconds": None,
                    "max_seconds": None,
                },
                "error_examples": [
                    {
                        "device_id": row["device_id"],
                        "device_name": row["device_name"],
                        "message": step[index]["error"],
                    }
                    for row, step in progress
                    if step[index]["error"]
                ][:5],
            }
        )
    return {
        "total_devices": len(rows),
        "stage_count": len(stages),
        "stages": stages,
        "bottleneck_stage_key": None,
        "bottleneck_stage_label": None,
    }


def states_for_targets(db, targets):
    task_ids = {str(target.celery_task_id) for target in targets if target.celery_task_id}
    if not task_ids:
        return {}
    runs = (
        db.query(PatrolRun)
        .filter(PatrolRun.id.in_([f"patrol-{value}" for value in task_ids]))
        .all()
    )
    by_id = {row.id: row for row in runs}
    result = {}
    for target in targets:
        if not target.celery_task_id:
            continue
        row = by_id.get(f"patrol-{target.celery_task_id}")
        state = execution_state(row)
        # Keep legacy collection-only jobs separate from the new inspection flow.
        if row is not None and not state:
            continue
        completed_at = state.get("completed_at")
        result[target.id] = {
            "task_id": target.celery_task_id,
            "snapshot_run_id": row.id if row else None,
            "status": state.get("status")
            or (
                target.dispatch_status
                if target.dispatch_status in {"cancelled", "failed"}
                else "queued"
            ),
            "phase": state.get("phase") or "queued",
            "steps": state.get("steps") or {},
            "error": state.get("error"),
            "completed_at": datetime.fromisoformat(completed_at) if completed_at else None,
            "connection_status": (
                "succeeded"
                if row and row.collection_status in {"completed", "partial"}
                else "failed"
                if row and row.collection_status == "failed"
                else "pending"
            ),
        }
    return result


def all_targets_terminal(db, targets):
    states = states_for_targets(db, targets)
    for target in targets:
        state = states.get(target.id)
        if state and state["snapshot_run_id"]:
            if state["status"] not in TERMINAL:
                return False
        elif target.dispatch_status not in {"failed", "cancelled", "skipped"}:
            return False
    return True
