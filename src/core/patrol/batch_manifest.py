# SPDX-License-Identifier: Apache-2.0

"""Durable patrol batch manifests and scalable device-level reads."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_

from src.infrastructure.db.models import (
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    WorkflowRun,
)
from src.infrastructure.db.postgres import get_db_session

ACTIVE_STATUSES = {
    "pending",
    "queued",
    "running",
    "in_progress",
    "collecting",
    "dispatching",
}
CONNECTION_FAILURE_MARKERS = (
    "auth",
    "credential",
    "connect",
    "login",
    "password",
    "ssh",
    "timeout",
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _connection_status(*, selection_status: str, run: Any | None) -> str:
    if selection_status == "skipped":
        return "not_attempted"
    if run is None or str(run.status or "") in ACTIVE_STATUSES:
        return "pending"
    if str(run.status or "") == "completed":
        return "succeeded"
    error = str(getattr(run, "error_message", "") or "").lower()
    if any(marker in error for marker in CONNECTION_FAILURE_MARKERS):
        return "failed"
    return "unknown"


def _aggregate_batch_status(
    statuses: Counter[str], *, launched_count: int
) -> str:
    if any(statuses.get(status, 0) for status in ACTIVE_STATUSES):
        return "active"
    if statuses.get("failed", 0):
        return "failed"
    if statuses.get("cancelled", 0):
        return "cancelled"
    if launched_count > 0 and statuses.get("completed", 0) == launched_count:
        return "completed"
    return "pending"


class PatrolBatchManifestRepository:
    def existing_batch_ids(self, patrol_batch_ids: set[str]) -> set[str]:
        if not patrol_batch_ids:
            return set()
        with get_db_session() as db:
            return {
                str(value)
                for (value,) in db.query(PatrolBatchRecord.patrol_batch_id)
                .filter(PatrolBatchRecord.patrol_batch_id.in_(patrol_batch_ids))
                .all()
            }

    def workflow_runs(self, patrol_batch_id: str) -> list[Any] | None:
        with get_db_session() as db:
            if db.get(PatrolBatchRecord, patrol_batch_id) is None:
                return None
            run_ids = [
                value
                for (value,) in db.query(PatrolBatchDeviceRecord.workflow_run_id)
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id == patrol_batch_id,
                    PatrolBatchDeviceRecord.workflow_run_id.is_not(None),
                )
                .all()
                if value
            ]
            if not run_ids:
                return []
            runs = db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids)).all()
            for run in runs:
                db.expunge(run)
            return runs

    def record_launch(
        self,
        *,
        result: dict[str, Any],
        ready_rows: list[dict[str, Any]],
        skipped_errors: list[dict[str, Any]],
        target_snapshot: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        batch_id = str(result["patrol_batch_id"])
        launched_by_device = {
            str(item.get("device_id") or ""): item
            for item in (result.get("runs") or result.get("tasks") or [])
        }
        targets: dict[str, dict[str, Any]] = {}
        for row in ready_rows:
            device_id = str(row.get("device_id") or "")
            if not device_id:
                continue
            launched = launched_by_device.get(device_id, {})
            targets[device_id] = {
                "device_id": device_id,
                "device_name": row.get("device_name"),
                "management_ip": row.get("ip"),
                "selection_status": "launched",
                "preflight_status": "ready",
                "failure_code": None,
                "error_message": None,
                "workflow_run_id": launched.get("workflow_run_id"),
            }
        for error in skipped_errors:
            device_id = str(error.get("device_id") or "")
            if not device_id:
                continue
            connection_issues = error.get("connection_issues") or []
            first_issue = connection_issues[0] if connection_issues else {}
            targets[device_id] = {
                "device_id": device_id,
                "device_name": error.get("device_name"),
                "management_ip": error.get("ip"),
                "selection_status": "skipped",
                "preflight_status": "failed",
                "failure_code": error.get("failure_code") or first_issue.get("failure_code"),
                "error_message": error.get("error") or first_issue.get("message"),
                "workflow_run_id": None,
            }

        with get_db_session() as db:
            db.add(
                PatrolBatchRecord(
                    patrol_batch_id=batch_id,
                    workflow_name=str(result.get("workflow_name") or "patrol-change-event"),
                    launch_mode=str(result.get("launch_mode") or "workflow"),
                    patrol_mode=str(result.get("mode") or "production"),
                    ticket_id=result.get("ticket_id"),
                    target_type=str(target_snapshot.get("target_type") or "filters"),
                    target_label=str(target_snapshot.get("target_label") or "当前筛选条件"),
                    selector_json=dict(target_snapshot.get("selector") or {}),
                    target_count=int(result.get("target_count") or len(targets)),
                    launched_count=int(result.get("launched_count") or 0),
                    skipped_count=int(result.get("skipped_count") or 0),
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            for target in targets.values():
                db.add(
                    PatrolBatchDeviceRecord(
                        id=str(uuid.uuid4()),
                        patrol_batch_id=batch_id,
                        created_at=now,
                        updated_at=now,
                        **target,
                    )
                )

    def list_batches(
        self, *, limit: int = 20, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        return self.list_batches_page(
            limit=limit,
            offset=0,
            include_archived=include_archived,
        )["items"]

    def list_batches_page(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
        include_archived: bool = False,
        search: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        page_limit = max(1, int(limit))
        page_offset = max(0, offset)
        with get_db_session() as db:
            query = db.query(PatrolBatchRecord)
            if not include_archived:
                query = query.filter(PatrolBatchRecord.archived.is_(False))
            if search and search.strip():
                pattern = f"%{search.strip()}%"
                query = query.filter(
                    or_(
                        PatrolBatchRecord.patrol_batch_id.ilike(pattern),
                        PatrolBatchRecord.target_label.ilike(pattern),
                        PatrolBatchRecord.ticket_id.ilike(pattern),
                        db.query(PatrolBatchDeviceRecord.id)
                        .filter(
                            PatrolBatchDeviceRecord.patrol_batch_id
                            == PatrolBatchRecord.patrol_batch_id,
                            or_(
                                PatrolBatchDeviceRecord.device_id.ilike(pattern),
                                PatrolBatchDeviceRecord.device_name.ilike(pattern),
                                PatrolBatchDeviceRecord.management_ip.ilike(pattern),
                            ),
                        )
                        .exists(),
                    )
                )
            normalized_status = str(status or "").strip().lower()
            if normalized_status:
                run_for_target = db.query(WorkflowRun.id).filter(
                    WorkflowRun.id == PatrolBatchDeviceRecord.workflow_run_id,
                )
                active_exists = db.query(PatrolBatchDeviceRecord.id).filter(
                    PatrolBatchDeviceRecord.patrol_batch_id
                    == PatrolBatchRecord.patrol_batch_id,
                    run_for_target.filter(WorkflowRun.status.in_(ACTIVE_STATUSES)).exists(),
                ).exists()
                failed_exists = db.query(PatrolBatchDeviceRecord.id).filter(
                    PatrolBatchDeviceRecord.patrol_batch_id
                    == PatrolBatchRecord.patrol_batch_id,
                    run_for_target.filter(WorkflowRun.status == "failed").exists(),
                ).exists()
                cancelled_exists = db.query(PatrolBatchDeviceRecord.id).filter(
                    PatrolBatchDeviceRecord.patrol_batch_id
                    == PatrolBatchRecord.patrol_batch_id,
                    run_for_target.filter(WorkflowRun.status == "cancelled").exists(),
                ).exists()
                launched_target_count = (
                    db.query(func.count(PatrolBatchDeviceRecord.id))
                    .filter(
                        PatrolBatchDeviceRecord.patrol_batch_id
                        == PatrolBatchRecord.patrol_batch_id,
                        PatrolBatchDeviceRecord.selection_status != "skipped",
                    )
                    .correlate(PatrolBatchRecord)
                    .scalar_subquery()
                )
                incomplete_exists = db.query(PatrolBatchDeviceRecord.id).filter(
                    PatrolBatchDeviceRecord.patrol_batch_id
                    == PatrolBatchRecord.patrol_batch_id,
                    PatrolBatchDeviceRecord.selection_status != "skipped",
                    or_(
                        PatrolBatchDeviceRecord.workflow_run_id.is_(None),
                        ~run_for_target.filter(WorkflowRun.status == "completed").exists(),
                    ),
                ).exists()
                completed = and_(
                    PatrolBatchRecord.launched_count > 0,
                    launched_target_count == PatrolBatchRecord.launched_count,
                    ~incomplete_exists,
                )
                aggregate_filters = {
                    "active": active_exists,
                    "failed": and_(~active_exists, failed_exists),
                    "cancelled": and_(~active_exists, ~failed_exists, cancelled_exists),
                    "completed": completed,
                    "pending": and_(
                        ~active_exists,
                        ~failed_exists,
                        ~cancelled_exists,
                        ~completed,
                    ),
                }
                if normalized_status in aggregate_filters:
                    query = query.filter(aggregate_filters[normalized_status])
            total = query.count()
            batches = (
                query.order_by(
                    PatrolBatchRecord.created_at.desc(),
                    PatrolBatchRecord.patrol_batch_id.desc(),
                )
                .offset(page_offset)
                .limit(page_limit)
                .all()
            )
            if not batches:
                return {
                    "items": [],
                    "total": total,
                    "limit": page_limit,
                    "offset": page_offset,
                }
            batch_ids = [row.patrol_batch_id for row in batches]
            targets = (
                db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.patrol_batch_id.in_(batch_ids))
                .all()
            )
            run_ids = [row.workflow_run_id for row in targets if row.workflow_run_id]
            runs = (
                db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids)).all() if run_ids else []
            )
            runs_by_id = {str(row.id): row for row in runs}

            targets_by_batch: dict[str, list[Any]] = {}
            for row in targets:
                targets_by_batch.setdefault(str(row.patrol_batch_id), []).append(row)

            result: list[dict[str, Any]] = []
            for batch in batches:
                batch_targets = targets_by_batch.get(str(batch.patrol_batch_id), [])
                run_statuses = Counter(
                    str(runs_by_id[str(row.workflow_run_id)].status or "unknown")
                    for row in batch_targets
                    if row.workflow_run_id and str(row.workflow_run_id) in runs_by_id
                )
                connection_statuses = Counter(
                    _connection_status(
                        selection_status=str(row.selection_status),
                        run=runs_by_id.get(str(row.workflow_run_id or "")),
                    )
                    for row in batch_targets
                )
                completed_at_values = [
                    run.completed_at
                    for run in runs
                    if run.completed_at
                    and str(run.id)
                    in {str(row.workflow_run_id) for row in batch_targets if row.workflow_run_id}
                ]
                result.append(
                    {
                        "patrol_batch_id": batch.patrol_batch_id,
                        "workflow_name": batch.workflow_name,
                        "source": "device-management",
                        "ticket_id": batch.ticket_id,
                        "target_type": batch.target_type,
                        "target_label": batch.target_label,
                        "selector": batch.selector_json or {},
                        "target_count": batch.target_count,
                        "launched_count": batch.launched_count,
                        "skipped_count": batch.skipped_count,
                        "total": batch.launched_count,
                        "status_counts": dict(run_statuses),
                        "failed_count": int(run_statuses.get("failed") or 0),
                        "running_count": sum(run_statuses.get(item, 0) for item in ACTIVE_STATUSES),
                        "completed_count": int(run_statuses.get("completed") or 0),
                        "pending_count": int(run_statuses.get("pending") or 0)
                        + int(run_statuses.get("queued") or 0),
                        "cancelled_count": int(run_statuses.get("cancelled") or 0),
                        "aggregate_status": _aggregate_batch_status(
                            run_statuses,
                            launched_count=int(batch.launched_count or 0),
                        ),
                        "connection_succeeded_count": int(
                            connection_statuses.get("succeeded") or 0
                        ),
                        "connection_failed_count": int(connection_statuses.get("failed") or 0),
                        "connection_pending_count": int(connection_statuses.get("pending") or 0),
                        "created_at": _iso(batch.created_at),
                        "completed_at": _iso(max(completed_at_values))
                        if completed_at_values
                        else None,
                        "sample_devices": [],
                        "manifest_available": True,
                        "archived": bool(batch.archived),
                        "archived_at": _iso(batch.archived_at),
                        "archive_reason": batch.archive_reason,
                        "archived_by": batch.archived_by,
                        "keep_until": _iso(batch.keep_until),
                    }
                )
            return {
                "items": result,
                "total": total,
                "limit": page_limit,
                "offset": page_offset,
            }

    def set_governance(
        self,
        patrol_batch_id: str,
        *,
        archived: bool,
        actor: str | None,
        reason: str | None,
        keep_until: datetime | None,
    ) -> bool:
        with get_db_session() as db:
            row = db.get(PatrolBatchRecord, patrol_batch_id)
            if row is None:
                return False
            row.archived = archived
            row.archived_at = datetime.now(timezone.utc) if archived else None
            row.archived_by = actor if archived else None
            row.archive_reason = reason
            row.keep_until = keep_until
            row.updated_at = datetime.now(timezone.utc)
            return True

    def list_devices(
        self,
        patrol_batch_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        selection_status: str | None = None,
    ) -> dict[str, Any] | None:
        with get_db_session() as db:
            batch = db.get(PatrolBatchRecord, patrol_batch_id)
            if batch is None:
                return None
            query = db.query(PatrolBatchDeviceRecord).filter(
                PatrolBatchDeviceRecord.patrol_batch_id == patrol_batch_id
            )
            if selection_status:
                query = query.filter(PatrolBatchDeviceRecord.selection_status == selection_status)
            if search:
                pattern = f"%{search.strip()}%"
                query = query.filter(
                    or_(
                        PatrolBatchDeviceRecord.device_id.ilike(pattern),
                        PatrolBatchDeviceRecord.device_name.ilike(pattern),
                        PatrolBatchDeviceRecord.management_ip.ilike(pattern),
                    )
                )
            total = query.count()
            rows = (
                query.order_by(
                    PatrolBatchDeviceRecord.selection_status.desc(),
                    PatrolBatchDeviceRecord.device_name.asc(),
                )
                .offset(max(0, offset))
                .limit(max(1, min(limit, 200)))
                .all()
            )
            run_ids = [row.workflow_run_id for row in rows if row.workflow_run_id]
            runs = (
                db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids)).all() if run_ids else []
            )
            runs_by_id = {str(row.id): row for row in runs}
            items = []
            for row in rows:
                run = runs_by_id.get(str(row.workflow_run_id or ""))
                items.append(
                    {
                        "device_id": row.device_id,
                        "device_name": row.device_name,
                        "management_ip": row.management_ip,
                        "selection_status": row.selection_status,
                        "preflight_status": row.preflight_status,
                        "connection_status": _connection_status(
                            selection_status=str(row.selection_status), run=run
                        ),
                        "workflow_run_id": row.workflow_run_id,
                        "workflow_status": str(run.status or "unknown") if run else None,
                        "failure_code": row.failure_code,
                        "error_message": row.error_message or (run.error_message if run else None),
                        "created_at": _iso(row.created_at),
                        "completed_at": _iso(run.completed_at) if run else None,
                    }
                )
            return {
                "patrol_batch_id": patrol_batch_id,
                "target_type": batch.target_type,
                "target_label": batch.target_label,
                "selector": batch.selector_json or {},
                "target_count": batch.target_count,
                "launched_count": batch.launched_count,
                "skipped_count": batch.skipped_count,
                "total": total,
                "limit": max(1, min(limit, 200)),
                "offset": max(0, offset),
                "items": items,
            }
