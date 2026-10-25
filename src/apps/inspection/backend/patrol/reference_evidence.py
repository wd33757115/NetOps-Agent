"""Read one result's CLI evidence without evaluating or rendering the whole report."""

from sqlalchemy import or_
from sqlalchemy.orm import load_only

from src.core.patrol.command_splitter import canonicalize_command
from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkChangeRecord,
    NetworkEventOccurrenceRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    PatrolAnalysisAttemptRecord,
    StateObservationRecord,
)
from src.infrastructure.db.postgres import get_db_session
from src.platform.evidence.offline_jobs import OfflinePatrolJobService

from .report_drafting import digest
from .report_evidence import build_excerpts


def reference_evidence(job_id, reference, *, owner_id=None, is_admin=False):
    OfflinePatrolJobService().check_access(
        job_id, owner_id=owner_id, is_admin=is_admin, purpose="offline_patrol"
    )
    with get_db_session() as db:
        job = (
            db.query(OfflinePatrolJobRecord)
            .options(
                load_only(
                    OfflinePatrolJobRecord.id,
                    OfflinePatrolJobRecord.status,
                    OfflinePatrolJobRecord.attempt_number,
                )
            )
            .filter_by(id=job_id)
            .one()
        )
        if job.status != "completed":
            raise ValueError("任务完成后才能查看原文对比")
        periods = (
            db.query(
                OfflinePatrolPeriodRecord.role,
                OfflinePatrolPeriodRecord.patrol_run_id,
            )
            .filter(OfflinePatrolPeriodRecord.job_id == job_id)
            .all()
        )
        runs = dict(periods)
        run_id = runs.get("current") or runs.get("sample")
        if not run_id:
            raise ValueError("任务尚未产生巡检证据")
        attempt = (
            db.query(PatrolAnalysisAttemptRecord)
            .options(
                load_only(
                    PatrolAnalysisAttemptRecord.id,
                    PatrolAnalysisAttemptRecord.scope_json,
                )
            )
            .filter_by(run_id=run_id, stage="event_building", status="completed")
            .filter(
                PatrolAnalysisAttemptRecord.completed_at.is_not(None),
            )
            .order_by(PatrolAnalysisAttemptRecord.completed_at.desc())
            .first()
        )
        scope = (attempt.scope_json or {}) if attempt else {}
        changes = db.query(NetworkChangeRecord).filter_by(run_id=run_id)
        if attempt:
            changes = changes.filter_by(
                analysis_revision=scope.get("analysis_revision") or "original"
            )
        change = changes.filter_by(id=reference).one_or_none()
        if change is None:
            # Event identifiers are resolved only inside this job's current run.
            occurrences = (
                db.query(NetworkEventOccurrenceRecord)
                .filter(
                    NetworkEventOccurrenceRecord.run_id == run_id,
                    or_(
                        NetworkEventOccurrenceRecord.id == reference,
                        NetworkEventOccurrenceRecord.event_id == reference,
                    ),
                )
                .limit(2)
                .all()
            )
            ids = {row.source_change_id for row in occurrences}
            if len(ids) == 1 and None not in ids:
                change = changes.filter(NetworkChangeRecord.id == next(iter(ids))).one_or_none()
        if change and "change_ids" in scope and change.id not in (scope.get("change_ids") or []):
            raise KeyError("证据不属于本次分析")
        observation = None
        if change is None:
            observation = (
                db.query(StateObservationRecord)
                .filter_by(id=reference, run_id=run_id)
                .one_or_none()
            )
            if observation is None:
                raise KeyError("未找到属于本任务的证据，请刷新巡检结果")
        record = change or observation
        item = {
            "device_id": record.device_id,
            "command": record.canonical_command,
            "entity_key": record.entity_key,
            "entity_type": record.entity_type,
            "field": change.field if change else observation.field_name,
            "change_id": change.id if change else None,
            "finding_id": observation.id if observation else None,
        }
        snapshot_query = db.query(CommandSnapshot).options(
            load_only(
                CommandSnapshot.id,
                CommandSnapshot.device_id,
                CommandSnapshot.run_id,
                CommandSnapshot.canonical_command,
                CommandSnapshot.raw_text,
                CommandSnapshot.observed_at,
            )
        )
        for side, snapshot_id, expected_run in (
            ("current", change.current_snapshot_id if change else observation.snapshot_id, run_id),
            ("baseline", change.previous_snapshot_id if change else None, runs.get("baseline")),
        ):
            snapshot = (
                snapshot_query.filter_by(id=snapshot_id).one_or_none() if snapshot_id else None
            )
            if snapshot and (
                snapshot.run_id != expected_run
                or snapshot.device_id != record.device_id
                or canonicalize_command(snapshot.canonical_command)
                != canonicalize_command(record.canonical_command)
            ):
                raise ValueError("原文引用与本任务的设备、命令或周期不一致，请核对证据")
            if not snapshot_id and expected_run:
                # Added/removed objects can lack an observation on one side, even
                # though the exact command's baseline output was collected.
                candidates = (
                    snapshot_query.filter_by(
                        run_id=expected_run,
                        device_id=record.device_id,
                        canonical_command=record.canonical_command,
                    )
                    .limit(2)
                    .all()
                )
                snapshot = candidates[0] if len(candidates) == 1 else None
            item[side] = {
                "snapshot_id": snapshot.id if snapshot else None,
                "observed_at": snapshot.observed_at.isoformat()
                if snapshot and snapshot.observed_at
                else None,
                "raw_text": snapshot.raw_text if snapshot else None,
            }
        unstructured = bool(
            change
            and (
                change.field == "raw_text_hash"
                or change.observation_type == "raw.command.hash"
                or "unstructured_diff" in (change.dimensions_json or {})
            )
        )
        report = {
            "unstructured_comparison" if unstructured else "structured_event_evidence": {
                "items": [item]
            }
        }
        source_digest = digest(
            {
                "job": job_id,
                "attempt": job.attempt_number,
                "analysis": attempt.id if attempt else None,
                "reference": reference,
                "evidence": item,
            }
        )
    entries = build_excerpts(report, include_unmatched_changes=True)
    for entry in entries:
        entry["lookup_reference"] = reference
    return {"source_digest": source_digest, "items": entries}
