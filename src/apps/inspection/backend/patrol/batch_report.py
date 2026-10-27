"""An online patrol batch, described by the fact model of an offline patrol job.

Online and offline patrols end in one result page and one Word template. The
offline side builds its facts from the job's periods; this builds the same
model from the batch's device runs, and marks it online so the shared text says
how the data was collected.
"""

from __future__ import annotations

from typing import Any

BATCH_PREFIX = "patrol-batch-"


def is_batch(identifier: Any) -> bool:
    return str(identifier or "").startswith(BATCH_PREFIX)


def _capture_window(batch_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    from src.infrastructure.db.models import PatrolBatchRecord
    from src.infrastructure.db.postgres import get_db_session

    with get_db_session() as db:
        batch = db.get(PatrolBatchRecord, batch_id)
        started = (batch.dispatch_started_at or batch.created_at) if batch is not None else None
        ended = (batch.dispatch_completed_at or batch.report_completed_at) if batch is not None else None
    finished = [str(run.get("completed_at")) for run in summary.get("runs") or [] if run.get("completed_at")]
    return {
        "started_at": started.isoformat() if started is not None else None,
        "completed_at": max(finished) if finished else (ended.isoformat() if ended is not None else None),
    }


def _build(batch_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from src.apps.inspection.backend import batch_api
    from src.core.patrol.leadership_report import build_leadership_report

    runs = batch_api._list_patrol_batch_runs(batch_id)
    if not runs and not batch_api._patrol_batch_manifest_exists(batch_id):
        raise KeyError(batch_id)
    summary = batch_api._build_patrol_batch_summary(batch_id, runs)
    summary["collection_mode"] = "online"
    summary["capture_window"] = _capture_window(batch_id, summary)
    report = build_leadership_report(batch_id, summary, issuer="网络运维中心", recipient="项目建设单位")
    return report, summary


def batch_report_context(batch_id: str, *, owner_id: Any = None, is_admin: bool = False) -> tuple[dict, dict]:
    """``(report, source)`` for an online batch, as ``report_context`` returns them for an offline job."""

    report, summary = _build(batch_id)
    if (report.get("assessment") or {}).get("code") == "in_progress":
        raise ValueError("巡检完成后才能生成报告")
    return report, summary


def batch_result_summary(batch_id: str) -> dict[str, Any]:
    """The model the result page shows; a running batch is shown as running, not refused."""

    return _build(batch_id)[0]


def batch_comparison_evidence(batch_id: str, change_id: str) -> dict[str, Any]:
    """Baseline and current CLI of one change found in the batch's runs."""

    from src.apps.inspection.backend import batch_api
    from src.core.patrol.change_event_repository import ChangeEventRepository
    from src.platform.evidence.offline_jobs import OfflinePatrolJobService

    summary = batch_api._build_patrol_batch_summary(batch_id, batch_api._list_patrol_batch_runs(batch_id))
    run_ids = sorted({str(run.get("snapshot_run_id") or run.get("run_id") or "") for run in summary.get("runs") or []} - {""})
    repository = ChangeEventRepository()
    for run_id in run_ids:
        for change in repository.list_changes(run_id=run_id):
            if str(change.get("change_id") or change.get("id") or "") == change_id:
                return OfflinePatrolJobService._comparison_evidence_for_change(change)
    raise KeyError(f"change not found: {change_id}")


__all__ = ["batch_comparison_evidence", "batch_report_context", "batch_result_summary", "is_batch"]
