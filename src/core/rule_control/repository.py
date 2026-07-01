"""PostgreSQL repository for LLM-assisted rule generation jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.core.rule_control.jobs import (
    RULE_GENERATION_JOB_TYPES,
    RULE_GENERATION_STATUSES,
    JOB_TRANSITIONS,
    RuleGenerationJobStateError,
)
from src.infrastructure.db.models import RuleGenerationJobRecord
from src.infrastructure.db.postgres import get_db_session


TERMINAL_RULE_GENERATION_STATUSES = {"active", "rejected", "deprecated"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RuleGenerationJobRepository:
    def create_job(
        self,
        *,
        job_type: str,
        source_ref: str | None = None,
        asset_ref: str | None = None,
        payload: dict[str, Any] | None = None,
        validation_report: dict[str, Any] | None = None,
        approval_required: bool = True,
        created_by: str | None = None,
        status: str = "draft",
    ) -> dict[str, Any]:
        if job_type not in RULE_GENERATION_JOB_TYPES:
            raise ValueError(f"unsupported rule generation job_type: {job_type}")
        if status not in RULE_GENERATION_STATUSES:
            raise ValueError(f"unsupported rule generation status: {status}")
        now = utcnow()
        row = RuleGenerationJobRecord(
            id=str(uuid.uuid4()),
            job_type=job_type,
            status=status,
            source_ref=source_ref,
            asset_ref=asset_ref,
            payload_json=dict(payload or {}),
            validation_report_json=validation_report,
            approval_required=bool(approval_required),
            created_by=created_by,
            created_at=now,
            updated_at=now,
            completed_at=now if status in TERMINAL_RULE_GENERATION_STATUSES else None,
        )
        with get_db_session() as db:
            db.add(row)
            db.flush()
            return row.to_dict()

    def list_jobs(
        self,
        *,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(RuleGenerationJobRecord)
            if job_type:
                query = query.filter(RuleGenerationJobRecord.job_type == job_type)
            if status:
                query = query.filter(RuleGenerationJobRecord.status == status)
            rows = (
                query.order_by(RuleGenerationJobRecord.updated_at.desc())
                .limit(max(1, min(int(limit), 1000)))
                .all()
            )
            return [row.to_dict() for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, job_id)
            return row.to_dict() if row else None

    def transition_job(
        self,
        job_id: str,
        *,
        status: str,
        validation_report: dict[str, Any] | None = None,
        asset_ref: str | None = None,
    ) -> dict[str, Any]:
        if status not in RULE_GENERATION_STATUSES:
            raise RuleGenerationJobStateError(f"unsupported target status: {status}")
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, job_id)
            if row is None:
                raise KeyError("rule generation job not found")
            allowed = JOB_TRANSITIONS.get(str(row.status), set())
            if status not in allowed:
                raise RuleGenerationJobStateError(
                    f"invalid rule generation transition: {row.status} -> {status}"
                )
            row.status = status
            row.updated_at = utcnow()
            if validation_report is not None:
                existing_report = dict(row.validation_report_json or {})
                if existing_report:
                    governance = dict(existing_report.get("governance") or {})
                    governance.update(validation_report)
                    existing_report["governance"] = governance
                    row.validation_report_json = existing_report
                else:
                    row.validation_report_json = validation_report
            if asset_ref is not None:
                row.asset_ref = asset_ref
            if status in TERMINAL_RULE_GENERATION_STATUSES:
                row.completed_at = row.updated_at
            db.flush()
            return row.to_dict()

    def attach_validation_report(
        self,
        job_id: str,
        *,
        validation_report: dict[str, Any],
    ) -> dict[str, Any]:
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, job_id)
            if row is None:
                raise KeyError("rule generation job not found")
            row.validation_report_json = validation_report
            row.updated_at = utcnow()
            db.flush()
            return row.to_dict()

    def approve_job(self, job_id: str, *, approved_by: str | None = None) -> dict[str, Any]:
        return self.transition_job(
            job_id,
            status="active",
            validation_report={"approved_by": approved_by} if approved_by else None,
        )

    def reject_job(
        self,
        job_id: str,
        *,
        rejected_by: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        report = {
            key: value
            for key, value in {
                "rejected_by": rejected_by,
                "reason": reason,
            }.items()
            if value
        }
        return self.transition_job(
            job_id,
            status="rejected",
            validation_report=report or None,
        )

    def deprecate_job(
        self,
        job_id: str,
        *,
        deprecated_by: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        report = {
            key: value
            for key, value in {
                "deprecated_by": deprecated_by,
                "reason": reason,
            }.items()
            if value
        }
        return self.transition_job(
            job_id,
            status="deprecated",
            validation_report=report or None,
        )
