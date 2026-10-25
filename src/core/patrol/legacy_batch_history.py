# SPDX-License-Identifier: Apache-2.0

"""Database-backed compatibility reads for patrol batches created before manifests."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import case, func, or_

from src.core.patrol.batch_manifest import ACTIVE_STATUSES, _aggregate_batch_status, _iso
from src.infrastructure.db.models import PatrolBatchRecord, WorkflowRun
from src.infrastructure.db.postgres import get_db_session


class LegacyPatrolBatchHistoryRepository:
    """Read legacy batch history without loading an arbitrary number of runs."""

    def list_batches_window(
        self,
        *,
        limit: int,
        include_archived: bool = False,
        search: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        window_limit = max(1, int(limit))
        with get_db_session() as db:
            batch_id = WorkflowRun.context["patrol_batch_id"].as_string()
            archived = func.coalesce(
                WorkflowRun.context["patrol_batch_archived"].as_boolean(), False
            )
            active_count = func.sum(case((WorkflowRun.status.in_(ACTIVE_STATUSES), 1), else_=0))
            failed_count = func.sum(case((WorkflowRun.status == "failed", 1), else_=0))
            cancelled_count = func.sum(case((WorkflowRun.status == "cancelled", 1), else_=0))
            completed_count = func.sum(case((WorkflowRun.status == "completed", 1), else_=0))
            archived_count = func.sum(case((archived.is_(True), 1), else_=0))
            run_count = func.count(WorkflowRun.id)

            query = (
                db.query(
                    batch_id.label("patrol_batch_id"),
                    func.min(WorkflowRun.created_at).label("created_at"),
                    run_count.label("run_count"),
                    active_count.label("active_count"),
                    failed_count.label("failed_count"),
                    cancelled_count.label("cancelled_count"),
                    completed_count.label("completed_count"),
                    archived_count.label("archived_count"),
                )
                .filter(
                    WorkflowRun.template_name == "patrol-change-event",
                    WorkflowRun.source == "device-management",
                    batch_id.isnot(None),
                    batch_id != "",
                    ~db.query(PatrolBatchRecord.patrol_batch_id)
                    .filter(PatrolBatchRecord.patrol_batch_id == batch_id)
                    .exists(),
                )
                .group_by(batch_id)
            )
            if search and search.strip():
                pattern = f"%{search.strip()}%"
                matching_batch_ids = (
                    db.query(batch_id)
                    .filter(
                        WorkflowRun.template_name == "patrol-change-event",
                        WorkflowRun.source == "device-management",
                        batch_id.isnot(None),
                        or_(
                            batch_id.ilike(pattern),
                            WorkflowRun.ticket_id.ilike(pattern),
                            WorkflowRun.context["device_id"].as_string().ilike(pattern),
                            WorkflowRun.context["device_name"].as_string().ilike(pattern),
                            WorkflowRun.context["management_ip"].as_string().ilike(pattern),
                            WorkflowRun.context["ip"].as_string().ilike(pattern),
                        ),
                    )
                    .distinct()
                )
                query = query.filter(batch_id.in_(matching_batch_ids))
            if not include_archived:
                query = query.having(archived_count < run_count)

            normalized_status = str(status or "").strip().lower()
            if normalized_status == "active":
                query = query.having(active_count > 0)
            elif normalized_status == "failed":
                query = query.having(active_count == 0, failed_count > 0)
            elif normalized_status == "cancelled":
                query = query.having(
                    active_count == 0,
                    failed_count == 0,
                    cancelled_count > 0,
                )
            elif normalized_status == "completed":
                query = query.having(completed_count == run_count)
            elif normalized_status == "pending":
                query = query.having(
                    active_count == 0,
                    failed_count == 0,
                    cancelled_count == 0,
                    completed_count < run_count,
                )

            grouped = query.subquery()
            total = int(db.query(func.count()).select_from(grouped).scalar() or 0)
            rows = (
                db.query(grouped)
                .order_by(
                    grouped.c.created_at.desc(),
                    grouped.c.patrol_batch_id.desc(),
                )
                .limit(window_limit)
                .all()
            )
            selected_ids = [str(row.patrol_batch_id) for row in rows]
            selected_runs = (
                db.query(WorkflowRun)
                .filter(
                    WorkflowRun.template_name == "patrol-change-event",
                    WorkflowRun.source == "device-management",
                    batch_id.in_(selected_ids),
                )
                .order_by(WorkflowRun.created_at.asc(), WorkflowRun.id.asc())
                .all()
                if selected_ids
                else []
            )

            runs_by_batch: dict[str, list[Any]] = {}
            for run in selected_runs:
                context = dict(run.context or {})
                runs_by_batch.setdefault(str(context.get("patrol_batch_id") or ""), []).append(run)

            items = [
                self._serialize_batch(
                    str(row.patrol_batch_id),
                    runs_by_batch[str(row.patrol_batch_id)],
                )
                for row in rows
            ]
            return {"items": items, "total": total}

    @staticmethod
    def _serialize_batch(patrol_batch_id: str, runs: list[Any]) -> dict[str, Any]:
        statuses = Counter(str(run.status or "unknown") for run in runs)
        contexts = [dict(run.context or {}) for run in runs]
        sample_devices: list[str] = []
        for context in contexts:
            name = str(context.get("device_name") or context.get("device_id") or "")
            if name and name not in sample_devices and len(sample_devices) < 5:
                sample_devices.append(name)
        completed_values = [run.completed_at for run in runs if run.completed_at]
        archived_contexts = [ctx for ctx in contexts if ctx.get("patrol_batch_archived")]
        archived_at_values = [
            str(ctx.get("patrol_batch_archived_at"))
            for ctx in archived_contexts
            if ctx.get("patrol_batch_archived_at")
        ]
        first = runs[0]
        return {
            "patrol_batch_id": patrol_batch_id,
            "workflow_name": first.template_name,
            "source": first.source,
            "ticket_id": next((run.ticket_id for run in runs if run.ticket_id), None),
            "total": len(runs),
            "status_counts": dict(statuses),
            "failed_count": int(statuses.get("failed") or 0),
            "running_count": sum(statuses.get(item, 0) for item in ACTIVE_STATUSES),
            "completed_count": int(statuses.get("completed") or 0),
            "pending_count": int(statuses.get("pending") or 0)
            + int(statuses.get("queued") or 0),
            "cancelled_count": int(statuses.get("cancelled") or 0),
            "aggregate_status": _aggregate_batch_status(statuses, launched_count=len(runs)),
            "created_at": _iso(min(run.created_at for run in runs if run.created_at)),
            "completed_at": _iso(max(completed_values)) if completed_values else None,
            "sample_devices": sample_devices,
            "manifest_available": False,
            "archived": len(archived_contexts) == len(runs),
            "archived_count": len(archived_contexts),
            "archived_at": max(archived_at_values) if archived_at_values else None,
            "archive_reason": next(
                (
                    ctx.get("patrol_batch_archive_reason")
                    for ctx in contexts
                    if ctx.get("patrol_batch_archive_reason")
                ),
                None,
            ),
            "archived_by": next(
                (
                    ctx.get("patrol_batch_archived_by")
                    for ctx in contexts
                    if ctx.get("patrol_batch_archived_by")
                ),
                None,
            ),
            "keep_until": next(
                (
                    ctx.get("patrol_batch_keep_until")
                    for ctx in contexts
                    if ctx.get("patrol_batch_keep_until")
                ),
                None,
            ),
        }
