"""Governed production feedback and remediation work items.

Feedback is evidence about a frozen production result. It never mutates that
result and never starts model generation. Confirmed feedback creates a work
item that must enter an existing governed candidate workflow.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from src.infrastructure.db.models import (
    AssessmentPolicyVersionRecord,
    AssetGapRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    OfflinePatrolJobRecord,
    ProductionFeedbackRecord,
    ProductionFeedbackWorkItemRecord,
    RiskCaseRecord,
    RiskOccurrenceRecord,
)
from src.infrastructure.db.postgres import get_db_session

FEEDBACK_TYPES = {
    "false_positive",
    "missed_detection",
    "wrong_severity",
    "wrong_scope",
    "expected_behavior",
    "evidence_quality_issue",
}
TRIAGE_OUTCOMES = {"confirmed", "not_confirmed", "inconclusive"}
WORK_ITEM_KINDS = {"rule_sample", "assessment_policy", "data_quality"}
_EVENT_OR_RISK_REQUIRED = {
    "false_positive",
    "wrong_severity",
    "wrong_scope",
    "expected_behavior",
}
_RULE_FEEDBACK_TYPES = {
    "false_positive",
    "missed_detection",
    "wrong_severity",
    "wrong_scope",
    "expected_behavior",
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProductionFeedbackConflictError(ValueError):
    """The requested feedback transition conflicts with frozen history."""


class ProductionFeedbackService:
    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def submit(
        self,
        *,
        execution_id: str,
        assessment_id: str,
        feedback_type: str,
        statement: str,
        reported_by: str,
        idempotency_key: str,
        event_occurrence_id: str | None = None,
        risk_occurrence_id: str | None = None,
    ) -> dict[str, Any]:
        kind = str(feedback_type).strip().lower()
        rationale = str(statement).strip()
        actor = str(reported_by).strip()
        idem = str(idempotency_key).strip()
        if kind not in FEEDBACK_TYPES:
            raise ValueError("unsupported production feedback type")
        if len(rationale) < 3:
            raise ValueError("feedback statement must contain at least 3 characters")
        if not actor:
            raise ValueError("feedback reporter is required")
        if len(idem) < 8:
            raise ValueError("feedback idempotency key must contain at least 8 characters")
        if kind in _EVENT_OR_RISK_REQUIRED and not (
            event_occurrence_id or risk_occurrence_id
        ):
            raise ValueError(
                f"{kind} feedback requires an event occurrence or risk occurrence"
            )

        request = {
            "execution_id": str(execution_id),
            "assessment_id": str(assessment_id),
            "feedback_type": kind,
            "statement": rationale,
            "event_occurrence_id": str(event_occurrence_id or "") or None,
            "risk_occurrence_id": str(risk_occurrence_id or "") or None,
            "reported_by": actor,
        }
        request_digest = _digest(request)
        now = _now()
        with self._session_scope() as db:
            existing = (
                db.query(ProductionFeedbackRecord)
                .filter(ProductionFeedbackRecord.idempotency_key == idem)
                .with_for_update()
                .one_or_none()
            )
            if existing is not None:
                if str(existing.request_digest) != request_digest:
                    raise ProductionFeedbackConflictError(
                        "idempotency key was already used for different feedback"
                    )
                return self._serialize(db, existing)

            execution = db.get(CheckExecutionRecord, str(execution_id))
            assessment = db.get(CheckAssessmentRecord, str(assessment_id))
            if execution is None:
                raise ValueError("check execution not found")
            if assessment is None:
                raise ValueError("check assessment not found")
            self._require_assessment_binding(execution, assessment)
            if not execution.runtime_bundle_version_id or not execution.runtime_bundle_digest:
                raise ProductionFeedbackConflictError(
                    "production feedback requires a frozen RuntimeBundle version and digest"
                )

            event_occurrence, event = self._load_event_context(
                db,
                event_occurrence_id=event_occurrence_id,
                execution=execution,
            )
            risk_occurrence, risk_case = self._load_risk_context(
                db,
                risk_occurrence_id=risk_occurrence_id,
                execution=execution,
            )
            source_context = {
                "schema_version": "production-feedback-source/v1",
                "execution_id": str(execution.id),
                "assessment_id": str(assessment.id),
                "patrol_batch_id": str(execution.patrol_batch_id),
                "device_id": str(execution.device_id),
                "check_item_version_id": str(execution.check_item_version_id),
                "runtime_bundle_id": execution.runtime_bundle_id,
                "runtime_bundle_version_id": execution.runtime_bundle_version_id,
                "runtime_bundle_digest": execution.runtime_bundle_digest,
                "evidence_digest": str(assessment.evidence_digest),
                "assessment_basis_digest": assessment.assessment_basis_digest,
                "event_occurrence": self._event_ref(event_occurrence, event),
                "risk_occurrence": self._risk_ref(risk_occurrence, risk_case),
            }
            feedback_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"netops/production-feedback/{idem}")
            )
            record = ProductionFeedbackRecord(
                id=feedback_id,
                idempotency_key=idem,
                request_digest=request_digest,
                feedback_type=kind,
                status="submitted",
                execution_id=str(execution.id),
                assessment_id=str(assessment.id),
                event_occurrence_id=(
                    str(event_occurrence.id) if event_occurrence is not None else None
                ),
                risk_occurrence_id=(
                    str(risk_occurrence.id) if risk_occurrence is not None else None
                ),
                risk_episode_id=(
                    str(risk_occurrence.risk_episode_id)
                    if risk_occurrence is not None
                    else None
                ),
                patrol_batch_id=str(execution.patrol_batch_id),
                device_id=str(execution.device_id),
                check_item_version_id=str(execution.check_item_version_id),
                runtime_bundle_id=execution.runtime_bundle_id,
                runtime_bundle_version_id=execution.runtime_bundle_version_id,
                runtime_bundle_digest=execution.runtime_bundle_digest,
                evidence_digest=str(assessment.evidence_digest),
                assessment_basis_digest=assessment.assessment_basis_digest,
                source_context_json=source_context,
                statement=rationale,
                reported_by=actor,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.flush()
            return self._serialize(db, record)

    def triage(
        self,
        feedback_id: str,
        *,
        outcome: str,
        reason: str,
        triaged_by: str,
        idempotency_key: str,
        work_item_kind: str | None = None,
    ) -> dict[str, Any]:
        decision = str(outcome).strip().lower()
        actor = str(triaged_by).strip()
        rationale = str(reason).strip()
        idem = str(idempotency_key).strip()
        item_kind = str(work_item_kind or "").strip().lower() or None
        if decision not in TRIAGE_OUTCOMES:
            raise ValueError("unsupported production feedback triage outcome")
        if len(rationale) < 3:
            raise ValueError("triage reason must contain at least 3 characters")
        if not actor:
            raise ValueError("feedback triager is required")
        if len(idem) < 8:
            raise ValueError("triage idempotency key must contain at least 8 characters")
        if decision == "confirmed" and item_kind not in WORK_ITEM_KINDS:
            raise ValueError("confirmed feedback requires a valid work item kind")
        if decision != "confirmed" and item_kind is not None:
            raise ValueError("only confirmed feedback can create a work item")

        request_digest = _digest(
            {
                "feedback_id": str(feedback_id),
                "outcome": decision,
                "reason": rationale,
                "triaged_by": actor,
                "work_item_kind": item_kind,
            }
        )
        now = _now()
        with self._session_scope() as db:
            record = (
                db.query(ProductionFeedbackRecord)
                .filter(ProductionFeedbackRecord.id == str(feedback_id))
                .with_for_update()
                .one_or_none()
            )
            if record is None:
                raise ValueError("production feedback not found")
            if record.triage_idempotency_key == idem:
                if str(record.triage_request_digest) != request_digest:
                    raise ProductionFeedbackConflictError(
                        "triage idempotency key was already used for a different decision"
                    )
                return self._serialize(db, record)
            if record.status != "submitted":
                raise ProductionFeedbackConflictError(
                    "only submitted feedback can be triaged"
                )
            if actor == str(record.reported_by):
                raise ProductionFeedbackConflictError(
                    "feedback reporter cannot triage the same feedback"
                )
            self._validate_work_item_kind(str(record.feedback_type), decision, item_kind)

            record.triage_outcome = decision
            record.triaged_by = actor
            record.triage_reason = rationale
            record.triage_idempotency_key = idem
            record.triage_request_digest = request_digest
            record.triaged_at = now
            record.updated_at = now
            if decision == "confirmed":
                action_payload = {
                    "schema_version": "production-feedback-action/v1",
                    "feedback_id": str(record.id),
                    "feedback_type": str(record.feedback_type),
                    "work_item_kind": item_kind,
                    "source_context_digest": _digest(record.source_context_json),
                    "next_action": self._next_action(str(item_kind)),
                    "model_generation_allowed": False,
                    "historical_mutation_allowed": False,
                }
                work_item_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/production-feedback-work-item/{record.id}",
                    )
                )
                work_item = ProductionFeedbackWorkItemRecord(
                    id=work_item_id,
                    feedback_id=str(record.id),
                    work_item_kind=str(item_kind),
                    status="open",
                    action_payload_json=action_payload,
                    action_digest=_digest(action_payload),
                    created_by=actor,
                    created_at=now,
                    updated_at=now,
                )
                db.add(work_item)
                record.work_item_id = work_item_id
                record.status = "work_item_created"
            elif decision == "not_confirmed":
                record.status = "rejected"
            else:
                record.status = "triaged"
            db.flush()
            return self._serialize(db, record)

    def link_candidate(
        self,
        work_item_id: str,
        *,
        candidate_type: str,
        candidate_id: str,
        linked_by: str,
    ) -> dict[str, Any]:
        kind = str(candidate_type).strip().lower()
        candidate = str(candidate_id).strip()
        actor = str(linked_by).strip()
        if not candidate or not actor:
            raise ValueError("candidate id and linking actor are required")
        with self._session_scope() as db:
            work_item = (
                db.query(ProductionFeedbackWorkItemRecord)
                .filter(ProductionFeedbackWorkItemRecord.id == str(work_item_id))
                .with_for_update()
                .one_or_none()
            )
            if work_item is None:
                raise ValueError("production feedback work item not found")
            if work_item.status not in {"open", "candidate_linked"}:
                raise ProductionFeedbackConflictError(
                    "only an open feedback work item can link a candidate"
                )
            candidate_ref = self._validate_candidate_ref(
                db,
                work_item_kind=str(work_item.work_item_kind),
                candidate_type=kind,
                candidate_id=candidate,
            )
            candidate_digest = _digest(candidate_ref)
            if work_item.candidate_ref_json:
                if str(work_item.candidate_ref_digest) != candidate_digest:
                    raise ProductionFeedbackConflictError(
                        "work item is already linked to a different candidate"
                    )
                return self._serialize_work_item(work_item)
            work_item.candidate_ref_json = candidate_ref
            work_item.candidate_ref_digest = candidate_digest
            work_item.linked_by = actor
            work_item.linked_at = _now()
            work_item.updated_at = work_item.linked_at
            work_item.status = "candidate_linked"
            db.flush()
            return self._serialize_work_item(work_item)

    def list_feedback(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
        feedback_type: str | None = None,
        runtime_bundle_version_id: str | None = None,
        patrol_batch_id: str | None = None,
    ) -> dict[str, Any]:
        current_page = max(1, int(page))
        limit = min(100, max(1, int(page_size)))
        offset = (current_page - 1) * limit
        with self._session_scope() as db:
            query = db.query(ProductionFeedbackRecord)
            if status:
                query = query.filter(ProductionFeedbackRecord.status == str(status))
            if feedback_type:
                query = query.filter(
                    ProductionFeedbackRecord.feedback_type == str(feedback_type)
                )
            if runtime_bundle_version_id:
                query = query.filter(
                    ProductionFeedbackRecord.runtime_bundle_version_id
                    == str(runtime_bundle_version_id)
                )
            if patrol_batch_id:
                query = query.filter(
                    ProductionFeedbackRecord.patrol_batch_id == str(patrol_batch_id)
                )
            total = query.count()
            rows = (
                query.order_by(
                    ProductionFeedbackRecord.created_at.desc(),
                    ProductionFeedbackRecord.id.desc(),
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            return {
                "items": [self._serialize(db, row) for row in rows],
                "total": total,
                "page": current_page,
                "page_size": limit,
            }

    def get(self, feedback_id: str) -> dict[str, Any]:
        with self._session_scope() as db:
            row = db.get(ProductionFeedbackRecord, str(feedback_id))
            if row is None:
                raise ValueError("production feedback not found")
            return self._serialize(db, row)

    def context_for_execution(self, execution_id: str) -> dict[str, Any]:
        """Return selectable event/risk facts already bound to one execution."""

        with self._session_scope() as db:
            execution = db.get(CheckExecutionRecord, str(execution_id))
            if execution is None:
                raise ValueError("check execution not found")
            assessment = (
                db.get(CheckAssessmentRecord, str(execution.assessment_id))
                if execution.assessment_id
                else db.query(CheckAssessmentRecord)
                .filter(CheckAssessmentRecord.execution_id == str(execution.id))
                .one_or_none()
            )
            events = (
                db.query(NetworkEventOccurrenceRecord, NetworkEventRecord)
                .join(
                    NetworkEventRecord,
                    NetworkEventRecord.id == NetworkEventOccurrenceRecord.event_id,
                )
                .filter(NetworkEventRecord.check_execution_id == str(execution.id))
                .order_by(
                    NetworkEventOccurrenceRecord.observed_at.desc(),
                    NetworkEventOccurrenceRecord.id.desc(),
                )
                .limit(200)
                .all()
            )
            risk_cases = (
                db.query(RiskCaseRecord)
                .filter(
                    RiskCaseRecord.last_verification_execution_id
                    == str(execution.id)
                )
                .all()
            )
            risk_case_ids = [str(row.id) for row in risk_cases]
            risk_by_id = {str(row.id): row for row in risk_cases}
            risks = (
                db.query(RiskOccurrenceRecord)
                .filter(RiskOccurrenceRecord.risk_case_id.in_(risk_case_ids))
                .order_by(
                    RiskOccurrenceRecord.observed_at.desc(),
                    RiskOccurrenceRecord.id.desc(),
                )
                .limit(200)
                .all()
                if risk_case_ids
                else []
            )
            return {
                "execution_id": str(execution.id),
                "assessment_id": str(assessment.id) if assessment else None,
                "patrol_batch_id": str(execution.patrol_batch_id),
                "device_id": str(execution.device_id),
                "check_item_version_id": str(execution.check_item_version_id),
                "runtime_bundle_version_id": execution.runtime_bundle_version_id,
                "runtime_bundle_digest": execution.runtime_bundle_digest,
                "evidence_digest": assessment.evidence_digest if assessment else None,
                "feedback_ready": bool(
                    assessment
                    and execution.runtime_bundle_version_id
                    and execution.runtime_bundle_digest
                ),
                "events": [
                    self._event_ref(occurrence, event)
                    for occurrence, event in events
                ],
                "risks": [
                    self._risk_ref(row, risk_by_id.get(str(row.risk_case_id)))
                    for row in risks
                    if risk_by_id.get(str(row.risk_case_id)) is not None
                ],
            }

    @staticmethod
    def _require_assessment_binding(
        execution: CheckExecutionRecord,
        assessment: CheckAssessmentRecord,
    ) -> None:
        if str(assessment.execution_id) != str(execution.id):
            raise ProductionFeedbackConflictError(
                "assessment does not belong to the selected execution"
            )
        for field in ("patrol_batch_id", "device_id", "check_item_version_id"):
            if str(getattr(assessment, field)) != str(getattr(execution, field)):
                raise ProductionFeedbackConflictError(
                    f"assessment and execution {field} do not match"
                )
        if execution.assessment_id and str(execution.assessment_id) != str(assessment.id):
            raise ProductionFeedbackConflictError(
                "execution points to a different assessment"
            )

    @staticmethod
    def _load_event_context(
        db: Any,
        *,
        event_occurrence_id: str | None,
        execution: CheckExecutionRecord,
    ) -> tuple[NetworkEventOccurrenceRecord | None, NetworkEventRecord | None]:
        if not event_occurrence_id:
            return None, None
        occurrence = db.get(NetworkEventOccurrenceRecord, str(event_occurrence_id))
        if occurrence is None:
            raise ValueError("network event occurrence not found")
        event = db.get(NetworkEventRecord, str(occurrence.event_id))
        if event is None:
            raise ValueError("network event not found")
        if str(event.check_execution_id or "") != str(execution.id):
            raise ProductionFeedbackConflictError(
                "event occurrence does not belong to the selected execution"
            )
        return occurrence, event

    @staticmethod
    def _load_risk_context(
        db: Any,
        *,
        risk_occurrence_id: str | None,
        execution: CheckExecutionRecord,
    ) -> tuple[RiskOccurrenceRecord | None, RiskCaseRecord | None]:
        if not risk_occurrence_id:
            return None, None
        occurrence = db.get(RiskOccurrenceRecord, str(risk_occurrence_id))
        if occurrence is None:
            raise ValueError("risk occurrence not found")
        risk_case = db.get(RiskCaseRecord, str(occurrence.risk_case_id))
        if risk_case is None:
            raise ValueError("risk case not found")
        if str(risk_case.last_verification_execution_id or "") != str(execution.id):
            raise ProductionFeedbackConflictError(
                "risk occurrence does not belong to the selected execution"
            )
        if occurrence.patrol_batch_id and str(occurrence.patrol_batch_id) != str(
            execution.patrol_batch_id
        ):
            raise ProductionFeedbackConflictError(
                "risk occurrence and execution patrol batch do not match"
            )
        return occurrence, risk_case

    @staticmethod
    def _event_ref(
        occurrence: NetworkEventOccurrenceRecord | None,
        event: NetworkEventRecord | None,
    ) -> dict[str, Any] | None:
        if occurrence is None or event is None:
            return None
        return {
            "occurrence_id": str(occurrence.id),
            "event_id": str(event.id),
            "event_type": str(event.event_type),
            "severity": str(occurrence.severity),
            "occurrence_kind": str(occurrence.occurrence_kind),
            "observed_at": occurrence.observed_at,
        }

    @staticmethod
    def _risk_ref(
        occurrence: RiskOccurrenceRecord | None,
        risk_case: RiskCaseRecord | None,
    ) -> dict[str, Any] | None:
        if occurrence is None or risk_case is None:
            return None
        return {
            "occurrence_id": str(occurrence.id),
            "risk_case_id": str(risk_case.id),
            "risk_episode_id": str(occurrence.risk_episode_id),
            "occurrence_kind": str(occurrence.occurrence_kind),
            "status_after": str(occurrence.status_after),
            "observed_at": occurrence.observed_at,
        }

    @staticmethod
    def _validate_work_item_kind(
        feedback_type: str,
        outcome: str,
        work_item_kind: str | None,
    ) -> None:
        if outcome != "confirmed":
            return
        if feedback_type == "evidence_quality_issue" and work_item_kind != "data_quality":
            raise ValueError("evidence quality feedback must create a data quality work item")
        if feedback_type in _RULE_FEEDBACK_TYPES and work_item_kind == "data_quality":
            raise ValueError("rule feedback must create a rule sample or assessment policy work item")

    @staticmethod
    def _next_action(work_item_kind: str) -> str:
        return {
            "rule_sample": "import_sanitized_rule_asset_source",
            "assessment_policy": "create_assessment_policy_revision",
            "data_quality": "investigate_asset_gap_or_source_quality",
        }[work_item_kind]

    @staticmethod
    def _validate_candidate_ref(
        db: Any,
        *,
        work_item_kind: str,
        candidate_type: str,
        candidate_id: str,
    ) -> dict[str, Any]:
        expected = {
            "rule_sample": "offline_job",
            "assessment_policy": "assessment_policy_version",
            "data_quality": "asset_gap",
        }[work_item_kind]
        if candidate_type != expected:
            raise ValueError(
                f"{work_item_kind} work item requires candidate type {expected}"
            )
        if candidate_type == "offline_job":
            row = db.get(OfflinePatrolJobRecord, candidate_id)
            if row is None:
                raise ValueError("offline rule asset source job not found")
            if str(row.purpose) != "rule_asset_source":
                raise ProductionFeedbackConflictError(
                    "feedback samples must use the rule asset source workflow"
                )
            return {
                "type": candidate_type,
                "id": str(row.id),
                "status": str(row.status),
                "purpose": str(row.purpose),
            }
        if candidate_type == "assessment_policy_version":
            row = db.get(AssessmentPolicyVersionRecord, candidate_id)
            if row is None:
                raise ValueError("assessment policy version not found")
            if str(row.status) in {"approved", "superseded", "revoked"}:
                raise ProductionFeedbackConflictError(
                    "feedback must link to a non-active assessment policy candidate"
                )
            return {
                "type": candidate_type,
                "id": str(row.id),
                "status": str(row.status),
                "digest": str(row.digest),
            }
        row = db.get(AssetGapRecord, candidate_id)
        if row is None:
            raise ValueError("asset gap not found")
        if str(row.status) not in {"open", "in_progress"}:
            raise ProductionFeedbackConflictError(
                "feedback must link to an open data quality task"
            )
        return {
            "type": candidate_type,
            "id": str(row.id),
            "status": str(row.status),
            "gap_type": str(row.gap_type),
        }

    @staticmethod
    def _serialize_work_item(row: ProductionFeedbackWorkItemRecord) -> dict[str, Any]:
        return row.to_dict()

    def _serialize(self, db: Any, row: ProductionFeedbackRecord) -> dict[str, Any]:
        result = row.to_dict()
        work_item = (
            db.get(ProductionFeedbackWorkItemRecord, str(row.work_item_id))
            if row.work_item_id
            else None
        )
        result["work_item"] = self._serialize_work_item(work_item) if work_item else None
        result["historical_facts_mutated"] = False
        result["model_generation_started"] = False
        return result


def aggregate_feedback_metrics(
    feedback_rows: list[Mapping[str, Any]],
    *,
    candidate_event_count: int,
    minimum_rate_sample: int = 20,
) -> dict[str, Any]:
    """Derive rollout metrics only from independently confirmed feedback facts."""

    counts = {kind: 0 for kind in sorted(FEEDBACK_TYPES)}
    confirmed_count = 0
    for row in feedback_rows:
        if str(row.get("triage_outcome") or "") != "confirmed":
            continue
        feedback_type = str(row.get("feedback_type") or "")
        if feedback_type in counts:
            counts[feedback_type] += 1
            confirmed_count += 1
    denominator = max(0, int(candidate_event_count))
    observed_rate = counts["false_positive"] / denominator if denominator else None
    return {
        "confirmed_feedback_count": confirmed_count,
        "confirmed_false_positive_count": counts["false_positive"],
        "confirmed_missed_detection_count": counts["missed_detection"],
        "confirmed_wrong_severity_count": counts["wrong_severity"],
        "confirmed_wrong_scope_count": counts["wrong_scope"],
        "confirmed_expected_behavior_count": counts["expected_behavior"],
        "confirmed_evidence_quality_issue_count": counts["evidence_quality_issue"],
        "feedback_event_denominator": denominator,
        "confirmed_false_positive_rate": observed_rate,
        "feedback_rate_sample_sufficient": denominator >= int(minimum_rate_sample),
        "feedback_confidence": (
            "sufficient" if denominator >= int(minimum_rate_sample) else "insufficient"
        ),
    }


__all__ = [
    "FEEDBACK_TYPES",
    "TRIAGE_OUTCOMES",
    "WORK_ITEM_KINDS",
    "ProductionFeedbackConflictError",
    "ProductionFeedbackService",
    "aggregate_feedback_metrics",
]
