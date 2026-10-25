"""Explicit check-item conclusions that never equate execution with health."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.core.check_items.assessment_policy import (
    build_assessment_basis,
    validate_assessment_basis,
)
from src.infrastructure.db.models import CheckAssessmentRecord, CheckExecutionRecord
from src.infrastructure.db.postgres import get_db_session

_COMPARISON_STATUSES = {
    "comparable",
    "first_observation",
    "baseline_missing",
    "baseline_stale",
    "schema_incompatible",
}


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class CheckAssessmentInput:
    execution_id: str
    patrol_batch_id: str
    device_id: str
    check_item_version_id: str
    execution_status: str
    assessment_mode: str
    comparison_status: str
    observation_count: int
    observed_entity_count: int
    evaluated_entity_count: int
    expected_entity_count: int | None = None
    threshold_unknown_count: int = 0
    partial_threshold_count: int = 0
    current_finding_ids: tuple[str, ...] = ()
    unresolved_risk_case_ids: tuple[str, ...] = ()
    rule_assets_ready: bool = False
    evidence_qualification_id: str | None = None
    coverage_decision_id: str | None = None
    source_identity_status: str = "unknown"
    output_quality_status: str = "unknown"
    freshness_status: str = "unknown"
    empty_result_classification: str = "not_evaluated"
    coverage_status: str = "unknown"
    coverage_basis: str = "unknown"
    blocking_reason_codes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    assessment_rule_bundle_digest: str | None = None
    runtime_bundle_version_id: str | None = None
    assessment_policy_snapshot_digest: str | None = None
    assessment_basis: dict[str, Any] = field(default_factory=dict)
    comparison_context_digest: str | None = None
    assessed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class CheckAssessmentDecision:
    id: str
    idempotency_key: str
    assessment_status: str
    completeness_status: str
    comparison_status: str
    blocking_reason_codes: tuple[str, ...]
    evidence_digest: str
    values: dict[str, Any]


class CheckAssessmentService:
    """Form a deterministic conclusion from terminal execution facts."""

    @staticmethod
    def assess(data: CheckAssessmentInput) -> CheckAssessmentDecision:
        if data.comparison_status not in _COMPARISON_STATUSES:
            raise ValueError(f"unsupported comparison status: {data.comparison_status}")
        if data.assessment_mode not in {"health", "change", "evidence"}:
            raise ValueError(f"unsupported assessment mode: {data.assessment_mode}")
        blockers = list(data.blocking_reason_codes)

        def block(code: str) -> None:
            if code not in blockers:
                blockers.append(code)

        supplied_basis = dict(data.assessment_basis or {})
        basis_valid = bool(supplied_basis) and validate_assessment_basis(
            supplied_basis
        )
        if supplied_basis and not basis_valid:
            block("assessment_basis_invalid")
        assessment_basis = (
            supplied_basis
            if basis_valid
            else build_assessment_basis(
                entries=(),
                policy_snapshot_digest=(
                    data.assessment_policy_snapshot_digest
                ),
                runtime_bundle_version_id=data.runtime_bundle_version_id,
                runtime_bundle_digest=data.assessment_rule_bundle_digest,
            )
        )
        basis_summary = dict(assessment_basis.get("summary") or {})
        strict_basis = bool(data.assessment_policy_snapshot_digest)
        if (
            strict_basis
            and str(assessment_basis.get("policy_snapshot_digest") or "")
            != str(data.assessment_policy_snapshot_digest or "")
        ):
            block("assessment_basis_policy_snapshot_mismatch")
        if strict_basis:
            for entry in assessment_basis.get("entries") or []:
                if not isinstance(entry, dict):
                    block("assessment_basis_invalid")
                    continue
                reason_code = str(
                    dict(entry.get("policy_resolution") or {}).get(
                        "reason_code"
                    )
                    or ""
                )
                if reason_code:
                    block(reason_code)

        approved_empty = (
            data.output_quality_status == "empty"
            and data.empty_result_classification == "expected_empty"
        )
        approved_not_applicable = (
            data.empty_result_classification == "not_applicable"
            and data.output_quality_status == "empty"
        )
        if (
            strict_basis
            and data.execution_status == "completed"
            and data.assessment_mode == "health"
            and not approved_empty
            and not approved_not_applicable
            and (
                not basis_valid
                or int(basis_summary.get("entry_count") or 0) <= 0
            )
        ):
            block("assessment_basis_missing")

        if data.execution_status == "not_applicable":
            status = "not_applicable"
            completeness = "complete"
            risk_verification_status = "not_applicable"
        elif data.execution_status != "completed":
            block(f"execution_{data.execution_status}")
            status = "unable_to_determine"
            completeness = "insufficient"
            risk_verification_status = (
                "not_reverified"
                if data.unresolved_risk_case_ids
                else "unknown"
            )
        elif data.assessment_mode in {"change", "evidence"}:
            status = "informational"
            completeness = "complete"
            if blockers:
                status = "unable_to_determine"
                completeness = "partial"
            risk_verification_status = "not_applicable"
        else:
            if not data.evidence_qualification_id:
                block("evidence_qualification_missing")
            if data.source_identity_status != "confirmed":
                block(f"source_identity_{data.source_identity_status}")
            if (
                data.output_quality_status != "complete"
                and not approved_empty
                and not approved_not_applicable
            ):
                block(f"output_quality_{data.output_quality_status}")
            if data.freshness_status != "fresh":
                block(f"freshness_{data.freshness_status}")
            if not approved_not_applicable:
                if not data.coverage_decision_id:
                    block("coverage_decision_missing")
                if data.coverage_status != "complete":
                    block(f"coverage_{data.coverage_status}")
                if data.observation_count <= 0 and not approved_empty:
                    block("no_observations")
                if data.observed_entity_count <= 0 and not approved_empty:
                    block("no_observed_entities")
                if not data.rule_assets_ready:
                    block("current_state_rules_not_ready")
                if data.evaluated_entity_count <= 0 and not approved_empty:
                    block("no_entities_evaluated")
                if (
                    data.expected_entity_count is not None
                    and data.observed_entity_count
                    < data.expected_entity_count
                ):
                    block("entity_coverage_incomplete")
                if data.threshold_unknown_count > 0:
                    block("threshold_unknown")
                if data.partial_threshold_count > 0:
                    block("threshold_coverage_partial")

            current_evidence_confirmed = (
                bool(data.evidence_qualification_id)
                and data.source_identity_status == "confirmed"
                and (
                    data.output_quality_status == "complete"
                    or approved_empty
                )
                and data.freshness_status == "fresh"
            )
            has_current_risk = bool(data.current_finding_ids) and (
                current_evidence_confirmed
            )
            if approved_not_applicable and not blockers:
                status = "not_applicable"
                completeness = "complete"
                risk_verification_status = "not_applicable"
            elif has_current_risk:
                status = "at_risk"
                completeness = "partial" if blockers else "complete"
                risk_verification_status = "confirmed_current"
            elif blockers:
                status = "unable_to_determine"
                completeness = (
                    "insufficient"
                    if data.observation_count <= 0
                    or data.observed_entity_count <= 0
                    or data.evaluated_entity_count <= 0
                    else "partial"
                )
                risk_verification_status = (
                    "not_reverified"
                    if data.unresolved_risk_case_ids
                    else "unknown"
                )
            else:
                status = "healthy"
                completeness = "complete"
                risk_verification_status = (
                    "recovery_pending"
                    if data.unresolved_risk_case_ids
                    else "no_current_risk"
                )

        evidence_payload = {
            "execution_id": data.execution_id,
            "execution_status": data.execution_status,
            "assessment_mode": data.assessment_mode,
            "comparison_status": data.comparison_status,
            "observation_count": data.observation_count,
            "expected_entity_count": data.expected_entity_count,
            "observed_entity_count": data.observed_entity_count,
            "evaluated_entity_count": data.evaluated_entity_count,
            "threshold_unknown_count": data.threshold_unknown_count,
            "partial_threshold_count": data.partial_threshold_count,
            "current_finding_ids": sorted(set(data.current_finding_ids)),
            "unresolved_risk_case_ids": sorted(
                set(data.unresolved_risk_case_ids)
            ),
            "evidence_qualification_id": data.evidence_qualification_id,
            "coverage_decision_id": data.coverage_decision_id,
            "source_identity_status": data.source_identity_status,
            "output_quality_status": data.output_quality_status,
            "freshness_status": data.freshness_status,
            "empty_result_classification": data.empty_result_classification,
            "coverage_status": data.coverage_status,
            "coverage_basis": data.coverage_basis,
            "risk_verification_status": risk_verification_status,
            "blocking_reason_codes": blockers,
            "evidence_refs": sorted(set(data.evidence_refs)),
            "assessment_rule_bundle_digest": data.assessment_rule_bundle_digest,
            "runtime_bundle_version_id": data.runtime_bundle_version_id,
            "assessment_policy_snapshot_digest": (
                data.assessment_policy_snapshot_digest
            ),
            "assessment_basis_digest": assessment_basis["digest"],
            "comparison_context_digest": data.comparison_context_digest,
        }
        evidence_digest = _digest(evidence_payload)
        idempotency_key = _digest({"assessment_for_execution": data.execution_id})
        assessment_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/check-assessment/{data.execution_id}/{evidence_digest}",
            )
        )
        values = {
            "id": assessment_id,
            "idempotency_key": idempotency_key,
            "patrol_batch_id": data.patrol_batch_id,
            "device_id": data.device_id,
            "check_item_version_id": data.check_item_version_id,
            "execution_id": data.execution_id,
            "assessment_status": status,
            "completeness_status": completeness,
            "comparison_status": data.comparison_status,
            "expected_entity_count": data.expected_entity_count,
            "observed_entity_count": data.observed_entity_count,
            "evaluated_entity_count": data.evaluated_entity_count,
            "threshold_unknown_count": data.threshold_unknown_count,
            "current_finding_ids_json": sorted(set(data.current_finding_ids)),
            "risk_case_ids_json": sorted(set(data.unresolved_risk_case_ids)),
            "evidence_qualification_id": data.evidence_qualification_id,
            "coverage_decision_id": data.coverage_decision_id,
            "source_identity_status": data.source_identity_status,
            "output_quality_status": data.output_quality_status,
            "freshness_status": data.freshness_status,
            "empty_result_classification": data.empty_result_classification,
            "coverage_status": data.coverage_status,
            "coverage_basis": data.coverage_basis,
            "risk_verification_status": risk_verification_status,
            "blocking_reason_codes_json": blockers,
            "assessment_rule_bundle_digest": data.assessment_rule_bundle_digest,
            "assessment_policy_snapshot_digest": (
                data.assessment_policy_snapshot_digest
            ),
            "assessment_basis_json": assessment_basis,
            "assessment_basis_digest": assessment_basis["digest"],
            "comparison_context_digest": data.comparison_context_digest,
            "evidence_digest": evidence_digest,
            "assessed_at": data.assessed_at,
            "created_at": datetime.now(timezone.utc),
        }
        return CheckAssessmentDecision(
            id=assessment_id,
            idempotency_key=idempotency_key,
            assessment_status=status,
            completeness_status=completeness,
            comparison_status=data.comparison_status,
            blocking_reason_codes=tuple(blockers),
            evidence_digest=evidence_digest,
            values=values,
        )


class CheckAssessmentRepository:
    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def save(self, decision: CheckAssessmentDecision) -> dict[str, Any]:
        with self._session_scope() as db:
            execution = db.get(
                CheckExecutionRecord,
                decision.values["execution_id"],
            )
            if execution is None:
                raise ValueError("check execution not found")
            existing = (
                db.query(CheckAssessmentRecord)
                .filter(CheckAssessmentRecord.execution_id == execution.id)
                .first()
            )
            if existing is not None:
                if str(existing.evidence_digest) != decision.evidence_digest:
                    raise ValueError(
                        "check execution already has a different immutable assessment"
                    )
                return existing.to_dict()
            row = CheckAssessmentRecord(**decision.values)
            db.add(row)
            db.flush()
            execution.assessment_id = row.id
            execution.updated_at = datetime.now(timezone.utc)
            db.flush()
            return row.to_dict()
