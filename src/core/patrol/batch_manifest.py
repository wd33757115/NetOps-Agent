# SPDX-License-Identifier: Apache-2.0

"""Durable patrol batch manifests and scalable device-level reads."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_

from src.core.check_items.report_contract import (
    SHADOW_VALIDATION,
    normalize_report_purpose,
    report_purpose_is_consistent_with_source,
)
from src.infrastructure.db.models import (
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRuntimeManifestRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolReportMaterializationRequestRecord,
    PatrolRun,
    WorkflowRun,
    WorkflowStepRecord,
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

_COLLECTION_FAILURE_CODES = {
    "command_timeout",
    "command_failed",
    "multiple_collection_failures",
    "connection_failed",
    "credential_missing",
    "device_not_found",
    "device_patrol_failed",
    "snapshot_persistence_failed",
    "collection_evidence_missing",
    "concurrency_lease_timeout",
    "device_fencing_lost",
}
_PARSER_FAILURE_CODES = {
    "template_missing",
    "parse_failed",
    "identity_unknown",
    "production_parser_not_ready",
    "parser_runtime_failed",
    "parser_contract_mismatch",
}


def _check_execution_failure_status(
    *,
    failure_code: str,
    skill_name: str | None,
) -> str:
    """Map a failed Workflow to the layer that actually lost evidence."""

    code = str(failure_code or "workflow_failed").strip().lower()
    skill = str(skill_name or "").strip().lower()
    if code in _COLLECTION_FAILURE_CODES or code.startswith(
        ("command_", "collection_", "credential_", "connection_")
    ):
        return "collection_failed"
    if code in _PARSER_FAILURE_CODES or code.startswith("parser_"):
        return "parse_failed"
    if code.startswith(("normalization_", "state_normalizer_")):
        return "normalization_failed"
    if code.startswith(("comparison_", "change_detection_")):
        return "comparison_failed"
    return {
        "device-patrol": "collection_failed",
        "state-normalizer": "normalization_failed",
        "change-detector": "comparison_failed",
        "event-builder": "assessment_failed",
    }.get(skill, "assessment_failed")


def _snapshot_run_id_from_steps(
    steps: list[WorkflowStepRecord],
) -> str | None:
    for step in steps:
        if str(step.skill_name or "") != "device-patrol":
            continue
        result = dict(step.result or {})
        run_id = str(result.get("snapshot_run_id") or "").strip()
        if run_id:
            return run_id
    return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _report_request_payload(
    request: PatrolReportMaterializationRequestRecord,
) -> dict[str, Any]:
    return {
        "request_id": str(request.id),
        "patrol_batch_id": str(request.patrol_batch_id),
        "idempotency_key": str(request.idempotency_key),
        "task_id": str(request.task_id),
        "status": str(request.status),
        "requested_by": str(request.requested_by),
        "requested_at": _iso(request.requested_at),
        "completed_at": _iso(request.completed_at),
        "error": request.error,
        "report_manifest_id": request.report_manifest_id,
        "report_manifest_digest": request.report_manifest_digest,
    }


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
    statuses: Counter[str],
    *,
    launched_count: int,
    dispatch_status: str | None = None,
) -> str:
    if any(statuses.get(status, 0) for status in ACTIVE_STATUSES):
        return "active"
    if dispatch_status == "cancelled":
        return "cancelled"
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

    def has_runtime_manifest(self, patrol_batch_id: str) -> bool:
        """Return whether this batch uses the check-item runtime."""

        with get_db_session() as db:
            row = db.get(PatrolBatchRecord, patrol_batch_id)
            return bool(row and row.runtime_manifest_id)

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

    def create_pending_batch(
        self,
        *,
        patrol_batch_id: str,
        workflow_name: str,
        launch_mode: str,
        patrol_mode: str,
        report_purpose: str,
        save_baseline: bool,
        ticket_id: str | None,
        target_count: int,
        planned_rows: list[dict[str, Any]],
        skipped_errors: list[dict[str, Any]],
        target_snapshot: dict[str, Any],
    ) -> None:
        """Persist the complete dispatch intent before any work is launched."""

        now = datetime.now(timezone.utc)
        target_type = str(
            target_snapshot.get("target_type") or "filters"
        )
        governed_report_purpose = normalize_report_purpose(
            report_purpose
        )
        if not report_purpose_is_consistent_with_source(
            report_purpose=governed_report_purpose,
            patrol_mode=patrol_mode,
            target_type=target_type,
        ):
            raise ValueError(
                "report purpose is inconsistent with the patrol source"
            )
        targets: dict[str, dict[str, Any]] = {}
        for row in planned_rows:
            device_id = str(row.get("device_id") or "")
            if not device_id:
                continue
            targets[device_id] = {
                "device_id": device_id,
                "device_name": row.get("device_name"),
                "management_ip": row.get("ip"),
                "selection_status": "selected",
                "preflight_status": "ready",
                "failure_code": None,
                "error_message": None,
                "execution_plan_id": row.get("execution_plan_id"),
                "dispatch_status": "pending",
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
                "execution_plan_id": None,
                "dispatch_status": "skipped",
            }

        with get_db_session() as db:
            if db.get(PatrolBatchRecord, patrol_batch_id) is not None:
                return
            db.add(
                PatrolBatchRecord(
                    patrol_batch_id=patrol_batch_id,
                    workflow_name=workflow_name,
                    launch_mode=launch_mode,
                    patrol_mode=patrol_mode,
                    report_purpose=governed_report_purpose,
                    save_baseline=save_baseline,
                    ticket_id=ticket_id,
                    target_type=target_type,
                    target_label=str(target_snapshot.get("target_label") or "current filters"),
                    selector_json=dict(target_snapshot.get("selector") or {}),
                    target_count=int(target_count),
                    launched_count=0,
                    skipped_count=len(skipped_errors),
                    dispatch_status="pending",
                    dispatch_requested_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            for target in targets.values():
                db.add(
                    PatrolBatchDeviceRecord(
                        id=str(uuid.uuid4()),
                        patrol_batch_id=patrol_batch_id,
                        dispatch_attempts=0,
                        created_at=now,
                        updated_at=now,
                        **target,
                    )
                )

    def note_dispatch_enqueue_error(self, patrol_batch_id: str, error: str) -> None:
        with get_db_session() as db:
            row = db.get(PatrolBatchRecord, patrol_batch_id)
            if row is None or row.dispatch_status == "cancelled":
                return
            row.dispatch_status = "pending"
            row.dispatch_error = str(error)[:4000]
            row.updated_at = datetime.now(timezone.utc)

    def mark_plan_freeze_failed(
        self,
        patrol_batch_id: str,
        error: str,
    ) -> None:
        """Make a pre-dispatch planning failure terminal and visible."""

        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            row = db.get(PatrolBatchRecord, patrol_batch_id)
            if row is None or row.dispatch_status == "cancelled":
                return
            row.dispatch_status = "failed"
            row.dispatch_error = str(error)[:4000]
            row.dispatch_completed_at = now
            row.updated_at = now

    def claim_dispatch_targets(
        self,
        patrol_batch_id: str,
        *,
        limit: int = 100,
        lease_seconds: int = 300,
        max_attempts: int = 3,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        claim_token = uuid.uuid4().hex
        with get_db_session() as db:
            batch = (
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == patrol_batch_id)
                .with_for_update()
                .first()
            )
            if batch is None or batch.archived or batch.dispatch_status in {"completed", "partial", "cancelled", "revoked", "failed"}:
                return []
            if (batch.patrol_mode == "production" and batch.launch_mode == "direct"
                    and not batch.runtime_manifest_id):
                return []
            rows = (
                db.query(PatrolBatchDeviceRecord)
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id == patrol_batch_id,
                    PatrolBatchDeviceRecord.selection_status != "skipped",
                    PatrolBatchDeviceRecord.workflow_run_id.is_(None),
                    PatrolBatchDeviceRecord.celery_task_id.is_(None),
                    PatrolBatchDeviceRecord.dispatch_attempts < max_attempts,
                    or_(
                        PatrolBatchDeviceRecord.dispatch_status.in_(["pending", "failed"]),
                        and_(
                            PatrolBatchDeviceRecord.dispatch_status == "dispatching",
                            PatrolBatchDeviceRecord.dispatch_lease_until < now,
                        ),
                    ),
                )
                .order_by(PatrolBatchDeviceRecord.created_at, PatrolBatchDeviceRecord.id)
                .limit(max(1, min(int(limit), 500)))
                .with_for_update(skip_locked=True)
                .all()
            )
            if not rows:
                return []
            batch.dispatch_status = "dispatching"
            batch.dispatch_started_at = batch.dispatch_started_at or now
            batch.dispatch_error = None
            batch.updated_at = now
            result: list[dict[str, Any]] = []
            for row in rows:
                row.dispatch_status = "dispatching"
                row.dispatch_attempts = int(row.dispatch_attempts or 0) + 1
                row.dispatch_claim_token = claim_token
                row.dispatch_lease_until = now + timedelta(seconds=max(30, lease_seconds))
                row.updated_at = now
                result.append(
                    {
                        "target_id": row.id,
                        "claim_token": claim_token,
                        "patrol_batch_id": patrol_batch_id,
                        "device_id": row.device_id,
                        "device_name": row.device_name,
                        "execution_plan_id": row.execution_plan_id,
                        "workflow_name": batch.workflow_name,
                        "launch_mode": batch.launch_mode,
                        "mode": batch.patrol_mode,
                        "report_purpose": batch.report_purpose,
                        "save_baseline": bool(batch.save_baseline),
                        "ticket_id": batch.ticket_id,
                        "target_snapshot": {
                            "target_type": batch.target_type,
                            "target_label": batch.target_label,
                            "selector": dict(batch.selector_json or {}),
                        },
                    }
                )
            return result

    def mark_target_dispatched(
        self,
        target_id: str,
        *,
        claim_token: str,
        workflow_run_id: str | None = None,
        celery_task_id: str | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            patrol_batch_id = (
                db.query(PatrolBatchDeviceRecord.patrol_batch_id)
                .filter(PatrolBatchDeviceRecord.id == target_id)
                .scalar()
            )
            if not patrol_batch_id:
                return False
            batch = (
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == patrol_batch_id)
                .with_for_update()
                .first()
            )
            if batch is None or batch.dispatch_status == "cancelled":
                return False
            row = (
                db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.id == target_id)
                .with_for_update()
                .first()
            )
            if row is None or row.dispatch_claim_token != claim_token:
                return False
            if row.dispatch_status == "cancelled":
                return False
            if row.workflow_run_id or row.celery_task_id:
                return True
            row.workflow_run_id = workflow_run_id
            row.celery_task_id = celery_task_id
            row.selection_status = "launched"
            row.dispatch_status = "dispatched"
            row.dispatched_at = now
            row.dispatch_claim_token = None
            row.dispatch_lease_until = None
            row.error_message = None
            row.updated_at = now
            batch.launched_count = (
                db.query(func.count(PatrolBatchDeviceRecord.id))
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id == row.patrol_batch_id,
                    or_(
                        PatrolBatchDeviceRecord.workflow_run_id.is_not(None),
                        PatrolBatchDeviceRecord.celery_task_id.is_not(None),
                        PatrolBatchDeviceRecord.id == row.id,
                    ),
                )
                .scalar()
                or 0
            )
            batch.updated_at = now
            return True

    def mark_target_dispatch_failed(
        self,
        target_id: str,
        *,
        claim_token: str,
        failure_code: str,
        error: str,
    ) -> bool:
        with get_db_session() as db:
            patrol_batch_id = (
                db.query(PatrolBatchDeviceRecord.patrol_batch_id)
                .filter(PatrolBatchDeviceRecord.id == target_id)
                .scalar()
            )
            if not patrol_batch_id:
                return False
            batch = (
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == patrol_batch_id)
                .with_for_update()
                .first()
            )
            if batch is None or batch.dispatch_status == "cancelled":
                return False
            row = (
                db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.id == target_id)
                .with_for_update()
                .first()
            )
            if row is None or row.dispatch_claim_token != claim_token:
                return False
            if row.dispatch_status == "cancelled":
                return False
            row.dispatch_status = "failed"
            row.failure_code = failure_code
            row.error_message = str(error)[:4000]
            row.dispatch_claim_token = None
            row.dispatch_lease_until = None
            row.updated_at = datetime.now(timezone.utc)
            return True

    def cancel_dispatch(
        self,
        patrol_batch_id: str,
        *,
        actor: str,
        reason: str,
    ) -> dict[str, Any] | None:
        """Freeze dispatch and mark every unfinished target as cancelled."""

        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            batch = (
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == patrol_batch_id)
                .with_for_update()
                .first()
            )
            if batch is None:
                return None
            if batch.archived:
                return {
                    "found": True,
                    "changed": False,
                    "cancellable": False,
                    "blocked_reason": "archived",
                    "patrol_batch_id": patrol_batch_id,
                    "dispatch_status": str(batch.dispatch_status),
                }

            targets = (
                db.query(PatrolBatchDeviceRecord)
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id == patrol_batch_id,
                    PatrolBatchDeviceRecord.selection_status != "skipped",
                )
                .order_by(PatrolBatchDeviceRecord.created_at, PatrolBatchDeviceRecord.id)
                .with_for_update()
                .all()
            )
            run_ids = [str(row.workflow_run_id) for row in targets if row.workflow_run_id]
            runs = (
                db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids)).all()
                if run_ids
                else []
            )
            run_statuses = {str(run.id): str(run.status or "pending") for run in runs}
            from src.apps.inspection.backend.online_reads import states_for_targets

            direct_states = states_for_targets(db, targets)

            unfinished: list[PatrolBatchDeviceRecord] = []
            for target in targets:
                if direct_states.get(target.id, {}).get("status") in {"completed", "failed"}:
                    continue
                if target.workflow_run_id:
                    status = run_statuses.get(str(target.workflow_run_id), "missing")
                    if status not in {"completed", "failed"}:
                        unfinished.append(target)
                    continue
                if str(target.dispatch_status or "pending") != "cancelled":
                    unfinished.append(target)

            already_cancelled = str(batch.dispatch_status or "") == "cancelled"
            if not unfinished and not already_cancelled:
                return {
                    "found": True,
                    "changed": False,
                    "cancellable": False,
                    "patrol_batch_id": patrol_batch_id,
                    "dispatch_status": str(batch.dispatch_status),
                    "workflow_run_ids": [],
                    "celery_task_ids": [],
                    "device_ids": [],
                }

            message = f"Cancelled by {actor}: {reason}"[:4000]
            previous_status = str(batch.dispatch_status or "pending")
            batch.dispatch_status = "cancelled"
            batch.dispatch_error = message
            batch.dispatch_completed_at = batch.dispatch_completed_at or now
            batch.updated_at = now

            workflow_run_ids: set[str] = set()
            celery_task_ids: set[str] = set()
            device_ids: set[str] = set()
            for target in unfinished:
                target.dispatch_status = "cancelled"
                target.failure_code = "patrol_batch_cancelled"
                target.error_message = message
                target.dispatch_claim_token = None
                target.dispatch_lease_until = None
                target.updated_at = now
                device_ids.add(str(target.device_id))
                if target.workflow_run_id:
                    workflow_run_ids.add(str(target.workflow_run_id))
                if target.celery_task_id:
                    celery_task_ids.add(str(target.celery_task_id))

            return {
                "found": True,
                "changed": not already_cancelled,
                "cancellable": True,
                "idempotent": already_cancelled,
                "patrol_batch_id": patrol_batch_id,
                "previous_status": previous_status,
                "dispatch_status": "cancelled",
                "cancelled_at": now.isoformat(),
                "cancelled_by": actor,
                "reason": reason,
                "runtime_manifest_id": batch.runtime_manifest_id,
                "workflow_run_ids": sorted(workflow_run_ids),
                "celery_task_ids": sorted(celery_task_ids),
                "device_ids": sorted(device_ids),
            }

    def finalize_dispatch(self, patrol_batch_id: str, *, max_attempts: int = 3) -> str:
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            batch = (
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == patrol_batch_id)
                .with_for_update()
                .first()
            )
            if batch is None:
                return "not_found"
            if batch.dispatch_status in {"cancelled", "revoked"}:
                return str(batch.dispatch_status)
            pending = (
                db.query(func.count(PatrolBatchDeviceRecord.id))
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id == patrol_batch_id,
                    PatrolBatchDeviceRecord.selection_status != "skipped",
                    PatrolBatchDeviceRecord.workflow_run_id.is_(None),
                    PatrolBatchDeviceRecord.celery_task_id.is_(None),
                    PatrolBatchDeviceRecord.dispatch_attempts < max_attempts,
                )
                .scalar()
                or 0
            )
            exhausted = (
                db.query(func.count(PatrolBatchDeviceRecord.id))
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id == patrol_batch_id,
                    PatrolBatchDeviceRecord.selection_status != "skipped",
                    PatrolBatchDeviceRecord.workflow_run_id.is_(None),
                    PatrolBatchDeviceRecord.celery_task_id.is_(None),
                    PatrolBatchDeviceRecord.dispatch_attempts >= max_attempts,
                )
                .scalar()
                or 0
            )
            batch.dispatch_status = (
                "pending" if pending else "partial" if exhausted else "completed"
            )
            if not pending:
                batch.dispatch_completed_at = now
            batch.updated_at = now
            return str(batch.dispatch_status)

    def pending_dispatch_batch_ids(self, *, limit: int = 100) -> list[str]:
        with get_db_session() as db:
            return [
                str(value)
                for (value,) in db.query(PatrolBatchRecord.patrol_batch_id)
                .filter(PatrolBatchRecord.dispatch_status.in_(["pending", "dispatching"]))
                .order_by(PatrolBatchRecord.dispatch_requested_at, PatrolBatchRecord.created_at)
                .limit(max(1, min(int(limit), 500)))
                .all()
            ]

    def request_terminal_report(
        self,
        patrol_batch_id: str,
        *,
        allow_regenerate: bool = False,
        idempotency_key: str | None = None,
        task_id: str | None = None,
        requested_by: str | None = None,
    ) -> bool:
        """Atomically request one final report after every launched run is terminal."""

        if idempotency_key and (not task_id or not requested_by):
            raise ValueError(
                "task_id and requested_by are required with idempotency_key"
            )
        self._reconcile_failed_check_item_workflows(patrol_batch_id)
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            batch = (
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == patrol_batch_id)
                .with_for_update()
                .first()
            )
            if batch is None:
                return False
            if idempotency_key:
                existing_request = (
                    db.query(PatrolReportMaterializationRequestRecord.id)
                    .filter(
                        PatrolReportMaterializationRequestRecord.patrol_batch_id
                        == patrol_batch_id,
                        PatrolReportMaterializationRequestRecord.idempotency_key
                        == idempotency_key,
                    )
                    .first()
                )
                if existing_request is not None:
                    return False
            if batch.report_status in {"queued", "generating"}:
                return False
            if batch.report_status == "completed" and not allow_regenerate:
                return False
            targets = (
                db.query(PatrolBatchDeviceRecord)
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id == patrol_batch_id,
                    PatrolBatchDeviceRecord.selection_status != "skipped",
                )
                .all()
            )
            if batch.runtime_manifest_id:
                if batch.launch_mode == "direct":
                    from src.apps.inspection.backend.online_reads import all_targets_terminal

                    if not all_targets_terminal(db, targets):
                        return False
                runtime_manifest = db.get(
                    CheckItemRuntimeManifestRecord,
                    batch.runtime_manifest_id,
                )
                if (
                    runtime_manifest is None
                    or str(runtime_manifest.digest)
                    != str(batch.runtime_manifest_digest or "")
                ):
                    return False
                payload = dict(runtime_manifest.manifest_json or {})
                expected_pairs = {
                    (
                        str(device_id),
                        str(check_item_version_id),
                    )
                    for device_id, version_ids in dict(
                        payload.get("device_check_item_versions") or {}
                    ).items()
                    for check_item_version_id in version_ids or []
                }
                executions = (
                    db.query(CheckExecutionRecord)
                    .filter(
                        CheckExecutionRecord.patrol_batch_id
                        == patrol_batch_id
                    )
                    .all()
                )
                actual_pairs = {
                    (
                        str(item.device_id),
                        str(item.check_item_version_id),
                    )
                    for item in executions
                }
                if not expected_pairs or actual_pairs != expected_pairs:
                    return False
                terminal_statuses = {
                    "completed",
                    "not_applicable",
                    "unsupported",
                    "blocked_asset_missing",
                    "collection_failed",
                    "parse_failed",
                    "normalization_failed",
                    "comparison_failed",
                    "assessment_failed",
                    "cancelled",
                }
                if any(
                    str(item.execution_status) not in terminal_statuses
                    for item in executions
                ):
                    return False
                assessment_execution_ids = {
                    str(value)
                    for (value,) in db.query(
                        CheckAssessmentRecord.execution_id
                    )
                    .filter(
                        CheckAssessmentRecord.patrol_batch_id
                        == patrol_batch_id
                    )
                    .all()
                }
                if {
                    str(item.id) for item in executions
                } != assessment_execution_ids:
                    return False
                workflow_run_ids = [
                    str(target.workflow_run_id)
                    for target in targets
                    if target.workflow_run_id
                ]
                if batch.launch_mode == "workflow":
                    if any(
                        not target.workflow_run_id
                        and str(target.dispatch_status or "")
                        not in {"failed", "cancelled"}
                        for target in targets
                    ):
                        return False
                    terminal_count = (
                        db.query(func.count(WorkflowRun.id))
                        .filter(
                            WorkflowRun.id.in_(workflow_run_ids),
                            WorkflowRun.status.in_(
                                ["completed", "failed", "cancelled"]
                            ),
                        )
                        .scalar()
                        or 0
                    )
                    if terminal_count != len(workflow_run_ids):
                        return False
            else:
                if not targets or any(
                    not target.workflow_run_id
                    and str(target.dispatch_status or "")
                    not in {"failed", "cancelled"}
                    for target in targets
                ):
                    return False
                run_ids = [
                    str(target.workflow_run_id)
                    for target in targets
                    if target.workflow_run_id
                ]
                terminal_count = (
                    db.query(func.count(WorkflowRun.id))
                    .filter(
                        WorkflowRun.id.in_(run_ids),
                        WorkflowRun.status.in_(
                            ["completed", "failed", "cancelled"]
                        ),
                    )
                    .scalar()
                    or 0
                )
                if terminal_count != len(run_ids):
                    return False
            batch.report_status = "queued"
            batch.report_requested_at = now
            batch.report_completed_at = None
            batch.report_error = None
            batch.updated_at = now
            if idempotency_key:
                request_id = (
                    "report-request-"
                    + uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{patrol_batch_id}|{idempotency_key}",
                    ).hex
                )
                db.add(
                    PatrolReportMaterializationRequestRecord(
                        id=request_id,
                        patrol_batch_id=patrol_batch_id,
                        idempotency_key=idempotency_key,
                        task_id=str(task_id),
                        status="queued",
                        requested_by=str(requested_by),
                        requested_at=now,
                        updated_at=now,
                    )
                )
                batch.report_request_id = request_id
            return True

    def report_generation_state(
        self,
        patrol_batch_id: str,
    ) -> dict[str, Any] | None:
        """Return the durable report worker state without loading report facts."""

        with get_db_session() as db:
            batch = db.get(PatrolBatchRecord, patrol_batch_id)
            if batch is None:
                return None
            state = {
                "patrol_batch_id": str(batch.patrol_batch_id),
                "status": str(batch.report_status or "not_requested"),
                "requested_at": _iso(batch.report_requested_at),
                "completed_at": _iso(batch.report_completed_at),
                "error": batch.report_error,
                "report_manifest_id": batch.report_manifest_id,
                "report_manifest_digest": batch.report_manifest_digest,
            }
            if batch.report_request_id:
                request = db.get(
                    PatrolReportMaterializationRequestRecord,
                    batch.report_request_id,
                )
                if request is not None:
                    state.update(
                        {
                            "status": str(request.status),
                            "requested_at": _iso(request.requested_at),
                            "completed_at": _iso(request.completed_at),
                            "error": request.error,
                            "request_id": str(request.id),
                            "idempotency_key": str(
                                request.idempotency_key
                            ),
                            "task_id": str(request.task_id),
                            "requested_by": str(request.requested_by),
                            "report_manifest_id": (
                                request.report_manifest_id
                                or batch.report_manifest_id
                            ),
                            "report_manifest_digest": (
                                request.report_manifest_digest
                                or batch.report_manifest_digest
                            ),
                        }
                    )
            return state

    def report_request_state(
        self,
        patrol_batch_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        """Return the immutable result for one API idempotency key."""

        with get_db_session() as db:
            request = (
                db.query(PatrolReportMaterializationRequestRecord)
                .filter(
                    PatrolReportMaterializationRequestRecord.patrol_batch_id
                    == patrol_batch_id,
                    PatrolReportMaterializationRequestRecord.idempotency_key
                    == idempotency_key,
                )
                .first()
            )
            return _report_request_payload(request) if request else None

    def terminal_report_candidate_ids(self, *, limit: int = 100) -> list[str]:
        with get_db_session() as db:
            return [
                str(value)
                for (value,) in db.query(PatrolBatchRecord.patrol_batch_id)
                .filter(
                    PatrolBatchRecord.report_status == "not_requested",
                    PatrolBatchRecord.dispatch_status.in_(
                        ["completed", "partial", "cancelled"]
                    ),
                    or_(
                        PatrolBatchRecord.launch_mode == "workflow",
                        PatrolBatchRecord.runtime_manifest_id.is_not(None),
                    ),
                )
                .order_by(
                    PatrolBatchRecord.created_at,
                    PatrolBatchRecord.patrol_batch_id,
                )
                .limit(max(1, min(int(limit), 500)))
                .all()
            ]

    @staticmethod
    def _reconcile_failed_check_item_workflows(
        patrol_batch_id: str,
    ) -> None:
        with get_db_session() as db:
            batch = db.get(PatrolBatchRecord, patrol_batch_id)
            if batch is None or not batch.runtime_manifest_id:
                return
            targets = (
                db.query(PatrolBatchDeviceRecord)
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id
                    == patrol_batch_id,
                    PatrolBatchDeviceRecord.selection_status != "skipped",
                )
                .all()
            )
            run_ids = {
                str(target.workflow_run_id)
                for target in targets
                if target.workflow_run_id
            }
            runs = (
                db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids)).all()
                if run_ids
                else []
            )
            run_by_id = {str(item.id): item for item in runs}
            step_rows = (
                db.query(WorkflowStepRecord)
                .filter(WorkflowStepRecord.run_id.in_(run_ids))
                .order_by(
                    WorkflowStepRecord.run_id,
                    WorkflowStepRecord.step_index,
                )
                .all()
                if run_ids
                else []
            )
            steps_by_run: dict[str, list[WorkflowStepRecord]] = {}
            for item in step_rows:
                steps_by_run.setdefault(str(item.run_id), []).append(item)
            failures: list[dict[str, Any]] = []
            for target in targets:
                workflow_run_id = str(target.workflow_run_id or "")
                run = run_by_id.get(workflow_run_id)
                run_status = str(run.status or "") if run is not None else ""
                dispatch_status = str(target.dispatch_status or "")
                evidence_refs = (workflow_run_id,) if workflow_run_id else ()
                if dispatch_status == "cancelled" or run_status == "cancelled":
                    failures.append(
                        {
                            "device_id": str(target.device_id),
                            "status": "cancelled",
                            "reason_code": (
                                "workflow_cancelled"
                                if workflow_run_id
                                else "patrol_batch_cancelled"
                            ),
                            "evidence_refs": evidence_refs,
                        }
                    )
                elif run_status == "failed":
                    run_context = (
                        dict(run.context or {}) if run is not None else {}
                    )
                    failure_code = str(
                        target.failure_code
                        or run_context.get("failure_code")
                        or "workflow_failed"
                    )
                    run_steps = steps_by_run.get(workflow_run_id, [])
                    failed_step = next(
                        (
                            item
                            for item in reversed(run_steps)
                            if str(item.status or "") == "failed"
                        ),
                        None,
                    )
                    if failed_step is None and run is not None:
                        failed_step = next(
                            (
                                item
                                for item in run_steps
                                if int(item.step_index or 0)
                                == int(run.current_step_index or 0)
                            ),
                            None,
                        )
                    failure = {
                        "device_id": str(target.device_id),
                        "status": _check_execution_failure_status(
                                failure_code=failure_code,
                                skill_name=(
                                    str(failed_step.skill_name)
                                    if failed_step is not None
                                    else None
                                ),
                            ),
                        "reason_code": failure_code,
                        "evidence_refs": evidence_refs,
                    }
                    snapshot_run_id = _snapshot_run_id_from_steps(run_steps)
                    if snapshot_run_id:
                        failure["run_id"] = snapshot_run_id
                    failures.append(failure)
                elif not workflow_run_id and dispatch_status == "failed":
                    failures.append(
                        {
                            "device_id": str(target.device_id),
                            "status": "collection_failed",
                            "reason_code": str(
                                target.failure_code or "dispatch_failed"
                            ),
                            "evidence_refs": (),
                        }
                    )
        if not failures:
            return
        from src.core.check_items import CheckItemRunFinalizer

        finalizer = CheckItemRunFinalizer()
        finalizer.fail_devices(
            patrol_batch_id=patrol_batch_id,
            failures=failures,
        )

    def mark_report_status(
        self,
        patrol_batch_id: str,
        status: str,
        *,
        error: str | None = None,
        request_id: str | None = None,
    ) -> bool:
        """Update report state and reject stale workers by request identity."""

        with get_db_session() as db:
            batch = db.get(PatrolBatchRecord, patrol_batch_id)
            if batch is None:
                return False
            now = datetime.now(timezone.utc)
            request = None
            if request_id:
                request = db.get(
                    PatrolReportMaterializationRequestRecord,
                    request_id,
                )
                if (
                    request is None
                    or str(request.patrol_batch_id) != patrol_batch_id
                ):
                    return False
                if str(batch.report_request_id or "") != request_id:
                    return False
                if (
                    str(request.status) == "completed"
                    and status != "completed"
                ):
                    return False
                request.status = status
                request.completed_at = (
                    now if status in {"completed", "failed"} else None
                )
                request.error = str(error)[:4000] if error else None
                request.updated_at = now
            batch.report_status = status
            batch.report_completed_at = now if status == "completed" else None
            if error:
                batch.report_error = str(error)[:4000]
            elif status == "completed":
                batch.report_error = None
            batch.updated_at = now
            if request is not None:
                request.report_manifest_id = batch.report_manifest_id
                request.report_manifest_digest = batch.report_manifest_digest
            return True

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
                "execution_plan_id": launched.get("execution_plan_id"),
                "celery_task_id": launched.get("celery_task_id"),
                "dispatch_status": "dispatched",
                "dispatched_at": now,
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
                "execution_plan_id": None,
                "celery_task_id": None,
                "dispatch_status": "skipped",
                "dispatched_at": None,
            }

        target_type = str(
            target_snapshot.get("target_type") or "filters"
        )
        governed_report_purpose = normalize_report_purpose(
            result.get("report_purpose"),
            default=SHADOW_VALIDATION,
        )
        patrol_mode = str(result.get("mode") or "production")
        if not report_purpose_is_consistent_with_source(
            report_purpose=governed_report_purpose,
            patrol_mode=patrol_mode,
            target_type=target_type,
        ):
            raise ValueError(
                "report purpose is inconsistent with the patrol source"
            )

        with get_db_session() as db:
            db.add(
                PatrolBatchRecord(
                    patrol_batch_id=batch_id,
                    workflow_name=str(result.get("workflow_name") or "patrol-change-event"),
                    launch_mode=str(result.get("launch_mode") or "workflow"),
                    patrol_mode=patrol_mode,
                    report_purpose=governed_report_purpose,
                    ticket_id=result.get("ticket_id"),
                    target_type=target_type,
                    target_label=str(target_snapshot.get("target_label") or "当前筛选条件"),
                    selector_json=dict(target_snapshot.get("selector") or {}),
                    target_count=int(result.get("target_count") or len(targets)),
                    launched_count=int(result.get("launched_count") or 0),
                    skipped_count=int(result.get("skipped_count") or 0),
                    save_baseline=bool(result.get("save_baseline")),
                    dispatch_status="completed",
                    dispatch_requested_at=now,
                    dispatch_started_at=now,
                    dispatch_completed_at=now,
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
                direct_run = db.query(PatrolRun.id).filter(
                    PatrolRun.id == "patrol-" + PatrolBatchDeviceRecord.celery_task_id,
                )

                def has_status(values):
                    return or_(
                        run_for_target.filter(WorkflowRun.status.in_(values)).exists(),
                        direct_run.filter(
                            PatrolRun.metadata_json["inspection_execution"]["status"].as_string().in_(values)
                        ).exists(),
                        and_(
                            PatrolBatchDeviceRecord.workflow_run_id.is_(None),
                            PatrolBatchDeviceRecord.celery_task_id.is_not(None),
                            ~direct_run.exists(),
                            PatrolBatchDeviceRecord.dispatch_status.in_(
                                [value for value in values if value in {"failed", "cancelled"}]
                                + (["dispatched"] if "queued" in values else [])
                            ),
                        ),
                    )

                active_exists = db.query(PatrolBatchDeviceRecord.id).filter(
                    PatrolBatchDeviceRecord.patrol_batch_id
                    == PatrolBatchRecord.patrol_batch_id,
                    has_status(list(ACTIVE_STATUSES)),
                ).exists()
                failed_exists = db.query(PatrolBatchDeviceRecord.id).filter(
                    PatrolBatchDeviceRecord.patrol_batch_id
                    == PatrolBatchRecord.patrol_batch_id,
                    has_status(["failed"]),
                ).exists()
                cancelled_exists = db.query(PatrolBatchDeviceRecord.id).filter(
                    PatrolBatchDeviceRecord.patrol_batch_id
                    == PatrolBatchRecord.patrol_batch_id,
                    has_status(["cancelled"]),
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
                    ~has_status(["completed"]),
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

            from src.apps.inspection.backend.online_reads import states_for_targets

            direct_states = states_for_targets(db, targets)
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
                run_statuses.update(
                    direct_states[row.id]["status"] for row in batch_targets if row.id in direct_states
                )
                connection_statuses = Counter(
                    direct_states[row.id]["connection_status"] if row.id in direct_states else _connection_status(
                        selection_status=str(row.selection_status),
                        run=runs_by_id.get(str(row.workflow_run_id or "")),
                    )
                    for row in batch_targets
                )
                dispatch_statuses = Counter(
                    str(row.dispatch_status or "pending") for row in batch_targets
                )
                completed_at_values = [
                    run.completed_at
                    for run in runs
                    if run.completed_at
                    and str(run.id)
                    in {str(row.workflow_run_id) for row in batch_targets if row.workflow_run_id}
                ]
                completed_at_values.extend(
                    direct_states[row.id]["completed_at"] for row in batch_targets
                    if row.id in direct_states and direct_states[row.id]["completed_at"]
                )
                result.append(
                    {
                        "patrol_batch_id": batch.patrol_batch_id,
                        "workflow_name": batch.workflow_name,
                        "report_purpose": batch.report_purpose,
                        "source": "device-management",
                        "ticket_id": batch.ticket_id,
                        "target_type": batch.target_type,
                        "target_label": batch.target_label,
                        "selector": batch.selector_json or {},
                        "target_count": batch.target_count,
                        "launched_count": batch.launched_count,
                        "skipped_count": batch.skipped_count,
                        "total": max(
                            0,
                            int(batch.target_count or 0) - int(batch.skipped_count or 0),
                        ),
                        "dispatch_status": batch.dispatch_status,
                        "dispatch_status_counts": dict(dispatch_statuses),
                        "dispatch_error": batch.dispatch_error,
                        "status_counts": dict(run_statuses),
                        "failed_count": int(run_statuses.get("failed") or 0),
                        "running_count": sum(run_statuses.get(item, 0) for item in ACTIVE_STATUSES),
                        "completed_count": int(run_statuses.get("completed") or 0),
                        "pending_count": int(run_statuses.get("pending") or 0)
                        + int(run_statuses.get("queued") or 0)
                        + int(dispatch_statuses.get("pending") or 0)
                        + int(dispatch_statuses.get("dispatching") or 0),
                        "cancelled_count": max(
                            int(run_statuses.get("cancelled") or 0),
                            int(dispatch_statuses.get("cancelled") or 0),
                        ),
                        "aggregate_status": _aggregate_batch_status(
                            run_statuses,
                            launched_count=int(batch.launched_count or 0),
                            dispatch_status=str(batch.dispatch_status or ""),
                        ),
                        "connection_succeeded_count": int(
                            connection_statuses.get("succeeded") or 0
                        ),
                        "connection_failed_count": int(connection_statuses.get("failed") or 0),
                        "connection_pending_count": int(connection_statuses.get("pending") or 0),
                        "connection_unknown_count": int(connection_statuses.get("unknown") or 0),
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
                        "report_status": batch.report_status,
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
            from src.apps.inspection.backend.online_reads import states_for_targets

            direct_states = states_for_targets(db, rows)
            items = []
            for row in rows:
                run = runs_by_id.get(str(row.workflow_run_id or ""))
                direct = direct_states.get(row.id) or {}
                items.append(
                    {
                        "device_id": row.device_id,
                        "device_name": row.device_name,
                        "management_ip": row.management_ip,
                        "selection_status": row.selection_status,
                        "preflight_status": row.preflight_status,
                        "connection_status": direct.get("connection_status") or _connection_status(
                            selection_status=str(row.selection_status), run=run
                        ),
                        "workflow_run_id": row.workflow_run_id,
                        "celery_task_id": row.celery_task_id,
                        "snapshot_run_id": direct.get("snapshot_run_id"),
                        "execution_status": direct.get("status") or (str(run.status) if run else None),
                        "execution_phase": direct.get("phase"),
                        "execution_plan_id": row.execution_plan_id,
                        "dispatch_status": row.dispatch_status,
                        "dispatch_attempts": int(row.dispatch_attempts or 0),
                        "workflow_status": str(run.status or "unknown") if run else None,
                        "failure_code": row.failure_code,
                        "error_message": row.error_message or direct.get("error") or (run.error_message if run else None),
                        "created_at": _iso(row.created_at),
                        "completed_at": _iso(direct.get("completed_at")) if direct else _iso(run.completed_at) if run else None,
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
                "dispatch_status": batch.dispatch_status,
                "total": total,
                "limit": max(1, min(limit, 200)),
                "offset": max(0, offset),
                "items": items,
            }
