"""Finalize check-item executions from frozen assets and immutable evidence."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from src.core.check_items.assessment import (
    CheckAssessmentInput,
    CheckAssessmentService,
)
from src.core.check_items.assessment_policy import (
    build_assessment_basis,
    validate_assessment_policy_snapshot,
)
from src.core.check_items.current_state import (
    CurrentStateEvaluationInput,
    CurrentStateEvaluationService,
    CurrentStateFindingRepository,
)
from src.core.check_items.evidence_quality import (
    CoverageDecisionInput,
    CoverageDecisionService,
    EvidenceQualificationInput,
    EvidenceQualificationService,
    EvidenceQualityRepository,
)
from src.core.check_items.risk import RiskCaseService
from src.core.check_items.runtime_bundle import CheckItemRuntimeBundleService
from src.core.check_items.text_comparison import (
    TextComparisonProfileRepository,
    compare_text,
)
from src.core.patrol.event_rules import govern_evaluation_scope
from src.infrastructure.db.models import (
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemVersionRecord,
    CommandSnapshot,
    ComparisonContextRecord,
    CoverageDecisionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    EvidenceQualificationRecord,
    NetworkChangeRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    RiskCaseRecord,
    StateObservationRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _comparison_status(row: ComparisonContextRecord | None) -> str:
    if row is None:
        return "baseline_missing"
    return str(
        (row.metadata_json or {}).get("comparison_status")
        or (
            "comparable"
            if row.baseline_run_id
            else "baseline_missing"
        )
    )


@dataclass(frozen=True, slots=True)
class StructuredExecutionResultInput:
    execution_id: str
    observations: tuple[dict[str, Any], ...]
    snapshot_facts: tuple[dict[str, Any], ...] = ()
    change_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    event_occurrence_ids: tuple[str, ...] = ()
    expected_entity_keys: tuple[Any, ...] = ()
    expected_entity_count: int | None = None
    coverage_denominator_ref: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    assessed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True, slots=True)
class TextExecutionResultInput:
    execution_id: str
    current_snapshot_id: str
    previous_snapshot_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    assessed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True, slots=True)
class EvidenceExecutionResultInput:
    execution_id: str
    evidence_refs: tuple[str, ...]
    assessed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True, slots=True)
class TerminalFailureInput:
    execution_id: str
    status: str
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()


class CheckItemExecutionService:
    """Create exactly one terminal Assessment for every planned execution."""

    _FAILURE_STATUSES = {
        "collection_failed",
        "parse_failed",
        "normalization_failed",
        "comparison_failed",
        "assessment_failed",
        "cancelled",
        "unsupported",
        "blocked_asset_missing",
    }
    _NON_EVALUABLE_COVERAGE_STATUSES = {
        "parse_failed",
        "normalization_failed",
    }

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def qualify_evidence(
        self,
        execution_id: str,
        *,
        snapshot_facts: Iterable[dict[str, Any]],
        evidence_refs: Iterable[str] = (),
        qualified_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist raw-evidence qualification without consulting Parser output."""
        snapshots = tuple(dict(item) for item in snapshot_facts)
        if not snapshots:
            raise ValueError("raw evidence qualification requires a snapshot")
        with self._session_scope() as db:
            execution = db.get(CheckExecutionRecord, execution_id)
            if execution is None:
                raise ValueError("check execution not found")
            existing = (
                db.get(
                    EvidenceQualificationRecord,
                    execution.evidence_qualification_id,
                )
                if execution.evidence_qualification_id
                else None
            )
            if existing is not None:
                return existing.to_dict()
            execution_values = execution.to_dict()

        execution = SimpleNamespace(**execution_values)
        bundle = self._frozen_bundle(execution)
        policy_snapshot = self._evidence_policy_snapshot(bundle)
        assessment_policy_snapshot = dict(
            getattr(execution, "assessment_policy_snapshot_json", None) or {}
        )
        if validate_assessment_policy_snapshot(assessment_policy_snapshot):
            policy_snapshot["applicability_decision"] = dict(
                assessment_policy_snapshot.get("applicability_decision") or {}
            )
        observed_ats = tuple(
            value
            for value in (
                self._as_datetime(item.get("observed_at"))
                for item in snapshots
            )
            if value is not None
        )
        at = _utc(qualified_at)
        decision = EvidenceQualificationService.qualify(
            EvidenceQualificationInput(
                execution_id=str(execution.id),
                patrol_batch_id=str(execution.patrol_batch_id),
                device_id=str(execution.device_id),
                check_item_version_id=str(
                    execution.check_item_version_id
                ),
                implementation_version_id=(
                    str(execution.implementation_version_id)
                    if execution.implementation_version_id
                    else None
                ),
                runtime_bundle_version_id=(
                    str(execution.runtime_bundle_version_id)
                    if execution.runtime_bundle_version_id
                    else None
                ),
                runtime_bundle_digest=(
                    str(execution.runtime_bundle_digest)
                    if execution.runtime_bundle_digest
                    else None
                ),
                snapshot_ids=tuple(
                    str(item.get("id") or "")
                    for item in snapshots
                    if item.get("id")
                ),
                source_device_ids=tuple(
                    str(
                        item.get("source_device_id")
                        or item.get("inventory_device_id")
                        or item.get("device_id")
                        or ""
                    )
                    for item in snapshots
                ),
                inventory_device_ids=tuple(
                    (
                        str(item.get("inventory_device_id"))
                        if item.get("inventory_device_id")
                        else None
                    )
                    for item in snapshots
                ),
                output_statuses=tuple(
                    str(item.get("output_status") or "unknown")
                    for item in snapshots
                ),
                output_reasons=tuple(
                    (
                        str(dict(item.get("metadata_json") or {}).get("output_reason"))
                        if dict(item.get("metadata_json") or {}).get("output_reason")
                        is not None
                        else None
                    )
                    for item in snapshots
                ),
                observed_ats=observed_ats,
                policy_snapshot=policy_snapshot,
                profile_version_id=(
                    dict(
                        policy_snapshot.get(
                            "output_quality_profile"
                        )
                        or {}
                    ).get("version_id")
                ),
                profile_digest=(
                    dict(
                        policy_snapshot.get(
                            "output_quality_profile"
                        )
                        or {}
                    ).get("digest")
                ),
                evidence_refs=tuple(
                    {
                        *(
                            str(item)
                            for item in evidence_refs
                            if str(item)
                        ),
                        *(
                            str(item.get("id") or "")
                            for item in snapshots
                            if item.get("id")
                        ),
                    }
                ),
                qualified_at=at,
            )
        )
        return EvidenceQualityRepository(
            self._session_scope
        ).save_qualification(decision)

    def fail_execution(
        self,
        execution_id: str,
        *,
        status: str,
        reason_codes: Iterable[str],
        evidence_refs: Iterable[str] = (),
        assessed_at: datetime | None = None,
    ) -> dict[str, Any]:
        if status not in self._FAILURE_STATUSES:
            raise ValueError(f"unsupported terminal failure status: {status}")
        supplied_reasons = tuple(
            str(item) for item in reason_codes if str(item)
        )
        supplied_evidence = tuple(
            str(item) for item in evidence_refs if str(item)
        )
        existing = self._existing_result(execution_id)
        if existing is not None:
            return existing
        at = _utc(assessed_at)
        self._ensure_failure_coverage(
            execution_id,
            status=status,
            reason_codes=supplied_reasons,
            evidence_refs=supplied_evidence,
            decided_at=at,
        )
        with self._session_scope() as db:
            execution, check_version, context = self._locked_context(
                db,
                execution_id,
            )
            reasons = tuple(
                dict.fromkeys(
                    [
                        *(str(item) for item in execution.reason_codes_json or []),
                        *supplied_reasons,
                    ]
                )
            )
            evidence = sorted(
                {
                    *(str(item) for item in execution.evidence_refs_json or []),
                    *supplied_evidence,
                    execution.id,
                }
            )
            execution.execution_status = status
            execution.reason_codes_json = list(reasons)
            execution.evidence_refs_json = evidence
            execution.completed_at = at
            execution.updated_at = at
            open_risks = self._open_risks(
                db,
                check_item_id=str(check_version.check_item_id),
                device_id=str(execution.device_id),
            )
            for risk in open_risks:
                risk.verification_status = "not_reverified"
                risk.updated_at = at
                RiskCaseService._append_occurrence(
                    db,
                    case=risk,
                    kind="unable_to_confirm",
                    observed_at=at,
                    status_after=str(risk.status),
                    patrol_batch_id=str(execution.patrol_batch_id),
                    reason=",".join(reasons),
                    evidence_refs=evidence,
                )
            decision = CheckAssessmentService.assess(
                CheckAssessmentInput(
                    execution_id=execution.id,
                    patrol_batch_id=execution.patrol_batch_id,
                    device_id=execution.device_id,
                    check_item_version_id=execution.check_item_version_id,
                    execution_status=status,
                    assessment_mode=check_version.assessment_mode,
                    comparison_status=_comparison_status(context),
                    observation_count=int(execution.observation_count or 0),
                    observed_entity_count=int(
                        execution.observed_entity_count or 0
                    ),
                    evaluated_entity_count=0,
                    unresolved_risk_case_ids=tuple(
                        str(item.id) for item in open_risks
                    ),
                    evidence_qualification_id=(
                        execution.evidence_qualification_id
                    ),
                    coverage_decision_id=execution.coverage_decision_id,
                    source_identity_status=str(
                        execution.source_identity_status or "unknown"
                    ),
                    output_quality_status=str(
                        execution.output_quality_status or "unknown"
                    ),
                    freshness_status=str(
                        execution.freshness_status or "unknown"
                    ),
                    empty_result_classification=str(
                        execution.empty_result_classification
                        or "not_evaluated"
                    ),
                    coverage_status=str(
                        execution.coverage_status or "unknown"
                    ),
                    coverage_basis=str(
                        execution.coverage_basis or "unknown"
                    ),
                    blocking_reason_codes=reasons,
                    evidence_refs=tuple(evidence),
                    assessment_rule_bundle_digest=(
                        execution.runtime_bundle_digest
                    ),
                    runtime_bundle_version_id=(
                        str(execution.runtime_bundle_version_id)
                        if execution.runtime_bundle_version_id
                        else None
                    ),
                    assessment_policy_snapshot_digest=(
                        str(execution.assessment_policy_snapshot_digest)
                        if execution.assessment_policy_snapshot_digest
                        else None
                    ),
                    comparison_context_digest=(
                        str(context.digest) if context else None
                    ),
                    assessed_at=at,
                )
            )
            db.add(CheckAssessmentRecord(**decision.values))
            execution.assessment_id = decision.id
            execution.risk_verification_status = decision.values[
                "risk_verification_status"
            ]
            db.flush()
            result = self._result_payload(
                execution,
                decision.values,
                risks=[item.to_dict() for item in open_risks],
            )
        return result

    def fail_executions_bulk(
        self,
        failures: Iterable[TerminalFailureInput],
        *,
        assessed_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Settle non-parser terminal failures in one database transaction."""

        items = tuple(failures)
        if not items:
            return []
        ids = [str(item.execution_id) for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("bulk terminal failures contain duplicate execution ids")
        for item in items:
            if item.status not in self._FAILURE_STATUSES:
                raise ValueError(
                    f"unsupported terminal failure status: {item.status}"
                )
            if item.status in self._NON_EVALUABLE_COVERAGE_STATUSES:
                raise ValueError(
                    "parse and normalization failures require individual "
                    "coverage reconciliation"
                )

        at = _utc(assessed_at)
        with self._session_scope() as db:
            rows = (
                db.query(CheckExecutionRecord, CheckItemVersionRecord)
                .join(
                    CheckItemVersionRecord,
                    CheckItemVersionRecord.id
                    == CheckExecutionRecord.check_item_version_id,
                )
                .filter(CheckExecutionRecord.id.in_(ids))
                .with_for_update()
                .all()
            )
            row_by_id = {
                str(execution.id): (execution, check_version)
                for execution, check_version in rows
            }
            missing = sorted(set(ids) - set(row_by_id))
            if missing:
                raise ValueError(
                    "one or more check executions were not found: "
                    + ",".join(missing)
                )
            context_ids = {
                str(execution.comparison_context_id)
                for execution, _check_version in row_by_id.values()
                if execution.comparison_context_id
            }
            contexts = (
                db.query(ComparisonContextRecord)
                .filter(ComparisonContextRecord.id.in_(context_ids))
                .all()
                if context_ids
                else []
            )
            context_by_id = {str(item.id): item for item in contexts}
            device_ids = {
                str(execution.device_id)
                for execution, _check_version in row_by_id.values()
            }
            check_item_ids = {
                str(check_version.check_item_id)
                for _execution, check_version in row_by_id.values()
            }
            risk_rows = (
                db.query(RiskCaseRecord)
                .filter(
                    RiskCaseRecord.device_id.in_(device_ids),
                    RiskCaseRecord.check_item_id.in_(check_item_ids),
                    RiskCaseRecord.status.in_(
                        ["open", "acknowledged", "recovering"]
                    ),
                )
                .order_by(RiskCaseRecord.first_seen_at, RiskCaseRecord.id)
                .all()
            )
            risks_by_pair: dict[tuple[str, str], list[RiskCaseRecord]] = {}
            for risk in risk_rows:
                risks_by_pair.setdefault(
                    (str(risk.device_id), str(risk.check_item_id)),
                    [],
                ).append(risk)

            results: list[dict[str, Any]] = []
            for item in items:
                execution, check_version = row_by_id[str(item.execution_id)]
                if execution.assessment_id:
                    assessment = db.get(
                        CheckAssessmentRecord,
                        execution.assessment_id,
                    )
                    if assessment is None:
                        raise ValueError(
                            "terminal execution has a missing Assessment"
                        )
                    results.append(
                        self._result_payload(execution, assessment.to_dict())
                    )
                    continue

                reasons = tuple(
                    dict.fromkeys(
                        [
                            *(
                                str(value)
                                for value in execution.reason_codes_json or []
                                if str(value)
                            ),
                            *(
                                str(value)
                                for value in item.reason_codes
                                if str(value)
                            ),
                        ]
                    )
                )
                evidence = sorted(
                    {
                        *(
                            str(value)
                            for value in execution.evidence_refs_json or []
                            if str(value)
                        ),
                        *(
                            str(value)
                            for value in item.evidence_refs
                            if str(value)
                        ),
                        str(execution.id),
                    }
                )
                execution.execution_status = item.status
                execution.reason_codes_json = list(reasons)
                execution.evidence_refs_json = evidence
                execution.completed_at = at
                execution.updated_at = at
                risks = risks_by_pair.get(
                    (
                        str(execution.device_id),
                        str(check_version.check_item_id),
                    ),
                    [],
                )
                for risk in risks:
                    risk.verification_status = "not_reverified"
                    risk.updated_at = at
                    RiskCaseService._append_occurrence(
                        db,
                        case=risk,
                        kind="unable_to_confirm",
                        observed_at=at,
                        status_after=str(risk.status),
                        patrol_batch_id=str(execution.patrol_batch_id),
                        reason=",".join(reasons),
                        evidence_refs=evidence,
                    )
                context = (
                    context_by_id.get(str(execution.comparison_context_id))
                    if execution.comparison_context_id
                    else None
                )
                decision = CheckAssessmentService.assess(
                    CheckAssessmentInput(
                        execution_id=execution.id,
                        patrol_batch_id=execution.patrol_batch_id,
                        device_id=execution.device_id,
                        check_item_version_id=execution.check_item_version_id,
                        execution_status=item.status,
                        assessment_mode=check_version.assessment_mode,
                        comparison_status=_comparison_status(context),
                        observation_count=int(execution.observation_count or 0),
                        observed_entity_count=int(
                            execution.observed_entity_count or 0
                        ),
                        evaluated_entity_count=0,
                        unresolved_risk_case_ids=tuple(
                            str(risk.id) for risk in risks
                        ),
                        evidence_qualification_id=(
                            execution.evidence_qualification_id
                        ),
                        coverage_decision_id=execution.coverage_decision_id,
                        source_identity_status=str(
                            execution.source_identity_status or "unknown"
                        ),
                        output_quality_status=str(
                            execution.output_quality_status or "unknown"
                        ),
                        freshness_status=str(
                            execution.freshness_status or "unknown"
                        ),
                        empty_result_classification=str(
                            execution.empty_result_classification
                            or "not_evaluated"
                        ),
                        coverage_status=str(
                            execution.coverage_status or "unknown"
                        ),
                        coverage_basis=str(
                            execution.coverage_basis or "unknown"
                        ),
                        blocking_reason_codes=reasons,
                        evidence_refs=tuple(evidence),
                        assessment_rule_bundle_digest=(
                            execution.runtime_bundle_digest
                        ),
                        runtime_bundle_version_id=(
                            str(execution.runtime_bundle_version_id)
                            if execution.runtime_bundle_version_id
                            else None
                        ),
                        assessment_policy_snapshot_digest=(
                            str(execution.assessment_policy_snapshot_digest)
                            if execution.assessment_policy_snapshot_digest
                            else None
                        ),
                        comparison_context_digest=(
                            str(context.digest) if context else None
                        ),
                        assessed_at=at,
                    )
                )
                db.add(CheckAssessmentRecord(**decision.values))
                execution.assessment_id = decision.id
                execution.risk_verification_status = decision.values[
                    "risk_verification_status"
                ]
                results.append(
                    self._result_payload(
                        execution,
                        decision.values,
                        risks=[risk.to_dict() for risk in risks],
                    )
                )
            db.flush()
            return results

    def _ensure_failure_coverage(
        self,
        execution_id: str,
        *,
        status: str,
        reason_codes: Iterable[str],
        evidence_refs: Iterable[str],
        decided_at: datetime,
    ) -> dict[str, Any] | None:
        if status not in self._NON_EVALUABLE_COVERAGE_STATUSES:
            return None
        with self._session_scope() as db:
            execution = db.get(CheckExecutionRecord, execution_id)
            if execution is None:
                raise ValueError("check execution not found")
            existing = (
                db.get(
                    CoverageDecisionRecord,
                    execution.coverage_decision_id,
                )
                if execution.coverage_decision_id
                else None
            )
            if existing is not None:
                return existing.to_dict()
            if not execution.evidence_qualification_id:
                return None
            execution_values = execution.to_dict()
            existing_reasons = tuple(
                str(item)
                for item in execution.reason_codes_json or []
                if str(item)
            )

        frozen_execution = SimpleNamespace(**execution_values)
        bundle = self._frozen_bundle(frozen_execution)
        policy_snapshot = self._evidence_policy_snapshot(bundle)
        qualification_id = str(
            frozen_execution.evidence_qualification_id
        )
        decision = CoverageDecisionService.decide(
            CoverageDecisionInput(
                execution_id=str(frozen_execution.id),
                patrol_batch_id=str(frozen_execution.patrol_batch_id),
                device_id=str(frozen_execution.device_id),
                check_item_version_id=str(
                    frozen_execution.check_item_version_id
                ),
                observed_entity_keys=(),
                evaluated_entity_count=0,
                policy_snapshot=dict(
                    policy_snapshot.get("coverage_policy") or {}
                ),
                evidence_refs=tuple(
                    {
                        qualification_id,
                        *(
                            str(item)
                            for item in evidence_refs
                            if str(item)
                        ),
                    }
                ),
                evaluable=False,
                evaluation_failure_reason_codes=tuple(
                    dict.fromkeys(
                        [
                            status,
                            *existing_reasons,
                            *(
                                str(item)
                                for item in reason_codes
                                if str(item)
                            ),
                        ]
                    )
                ),
                decided_at=decided_at,
            )
        )
        return EvidenceQualityRepository(
            self._session_scope
        ).save_coverage(decision)

    def finalize_structured(
        self,
        data: StructuredExecutionResultInput,
    ) -> dict[str, Any]:
        existing = self._existing_result(data.execution_id)
        if existing is not None:
            return existing
        at = _utc(data.assessed_at)
        qualification = self.qualify_evidence(
            data.execution_id,
            snapshot_facts=data.snapshot_facts,
            evidence_refs=data.evidence_refs,
            qualified_at=at,
        )
        with self._session_scope() as db:
            execution, check_version, context = self._locked_context(
                db,
                data.execution_id,
            )
            if str(check_version.result_mode) != "structured":
                raise ValueError("check execution is not structured")
            bundle = self._frozen_bundle(execution)
            assets = tuple(bundle["assets"])
            observations = tuple(
                self._validate_observation(
                    item,
                    execution=execution,
                    bundle=bundle,
                )
                for item in data.observations
            )
            baseline_observations = tuple(
                row.to_dict()
                for row in (
                    db.query(StateObservationRecord)
                    .filter(
                        StateObservationRecord.snapshot_id.in_(
                            [str(item) for item in context.baseline_snapshot_ids_json or []]
                        ),
                        StateObservationRecord.device_id == execution.device_id,
                    )
                    .all()
                    if context is not None and context.baseline_snapshot_ids_json
                    else []
                )
            )
            rules = self._load_current_state_rules(db, assets)
            check_item_id = str(check_version.check_item_id)
            evidence_policy_snapshot = self._evidence_policy_snapshot(bundle)
            assessment_policy_snapshot = dict(
                execution.assessment_policy_snapshot_json or {}
            )
            assessment_policy_snapshot_digest = (
                str(execution.assessment_policy_snapshot_digest or "")
                or str(assessment_policy_snapshot.get("digest") or "")
                or None
            )
            trusted_current_evidence = (
                qualification["source_identity_status"] == "confirmed"
                and qualification["output_quality_status"] == "complete"
                and qualification["freshness_status"] == "fresh"
            )
            finding_result = (
                CurrentStateEvaluationService.evaluate(
                    CurrentStateEvaluationInput(
                        patrol_batch_id=execution.patrol_batch_id,
                        check_execution_id=execution.id,
                        device_id=execution.device_id,
                        check_item_id=check_item_id,
                        check_item_version_id=execution.check_item_version_id,
                        implementation_version_id=(
                            execution.implementation_version_id
                        ),
                        runtime_bundle_version_id=str(
                            execution.runtime_bundle_version_id
                        ),
                        runtime_bundle_digest=str(
                            execution.runtime_bundle_digest
                        ),
                        observations=observations,
                        rules=rules,
                        runtime_assets=assets,
                        assessed_at=at,
                        assessment_policy_snapshot=(
                            assessment_policy_snapshot
                        ),
                        baseline_observations=baseline_observations,
                    )
                )
                if observations
                else None
            )
            evaluation_coverage = (
                dict(finding_result.coverage)
                if finding_result is not None
                else {
                    "evaluated_entity_count": 0,
                    "threshold_unknown_observation_count": 0,
                    "partial_threshold_observation_count": 0,
                    "assessment_policy_blocking_reason_codes": [],
                    "assessment_basis": build_assessment_basis(
                        entries=(),
                        policy_snapshot_digest=(
                            assessment_policy_snapshot_digest
                        ),
                        runtime_bundle_version_id=(
                            str(execution.runtime_bundle_version_id or "")
                            or None
                        ),
                        runtime_bundle_digest=(
                            str(execution.runtime_bundle_digest or "")
                            or None
                        ),
                    ),
                }
            )
            observed_entities = tuple(
                sorted(
                    {
                        (
                            str(item.get("entity_type") or ""),
                            str(item.get("entity_key") or ""),
                        )
                        for item in observations
                        if str(item.get("device_id") or "")
                        == execution.device_id
                    }
                )
            )
            coverage_policy = dict(
                evidence_policy_snapshot.get("coverage_policy") or {}
            )
            coverage_basis = str(
                coverage_policy.get("model") or "unknown"
            )
            denominator_ref = dict(data.coverage_denominator_ref or {})
            reference_basis = str(
                denominator_ref.get("basis")
                or denominator_ref.get("model")
                or ""
            )
            expected_entity_keys = (
                data.expected_entity_keys
                if coverage_basis
                in {"inventory_entity_set", "derived_entity_set"}
                and reference_basis == coverage_basis
                else ()
            )
            expected_entity_count = (
                data.expected_entity_count
                if coverage_basis == "command_reported_total"
                and reference_basis == coverage_basis
                else None
            )
            coverage_decision = CoverageDecisionService.decide(
                CoverageDecisionInput(
                    execution_id=execution.id,
                    patrol_batch_id=execution.patrol_batch_id,
                    device_id=execution.device_id,
                    check_item_version_id=execution.check_item_version_id,
                    observed_entity_keys=observed_entities,
                    evaluated_entity_count=int(
                        evaluation_coverage.get("evaluated_entity_count") or 0
                    ),
                    policy_snapshot=coverage_policy,
                    expected_entity_keys=expected_entity_keys,
                    expected_entity_count=expected_entity_count,
                    denominator_ref=denominator_ref,
                    evidence_refs=tuple(
                        {
                            str(qualification["id"]),
                            *(str(item) for item in data.evidence_refs if str(item)),
                        }
                    ),
                    decided_at=at,
                )
            )

        quality_repository = EvidenceQualityRepository(self._session_scope)
        coverage_record = quality_repository.save_coverage(coverage_decision)
        findings = (
            CurrentStateFindingRepository(self._session_scope).save(
                finding_result,
                check_execution_id=data.execution_id,
            )
            if finding_result is not None and trusted_current_evidence
            else []
        )
        risks = RiskCaseService(self._session_scope).observe_findings(
            check_item_id=check_item_id,
            finding_ids=[str(item["id"]) for item in findings],
        )

        with self._session_scope() as db:
            execution, check_version, context = self._locked_context(
                db,
                data.execution_id,
            )
            changes = self._bind_changes(
                db,
                ids=data.change_ids,
                execution=execution,
                check_item_id=str(check_version.check_item_id),
            )
            events = self._bind_events(
                db,
                ids=data.event_ids,
                execution=execution,
                check_item_id=str(check_version.check_item_id),
            )
            event_occurrences = self._bind_event_occurrences(
                db,
                ids=data.event_occurrence_ids,
                execution=execution,
                changes=changes,
            )
            open_risks = self._open_risks(
                db,
                check_item_id=str(check_version.check_item_id),
                device_id=str(execution.device_id),
            )
            observed_entities = set(observed_entities)
            coverage = evaluation_coverage
            evidence = sorted(
                {
                    *(str(item) for item in execution.evidence_refs_json or []),
                    *(str(item) for item in data.evidence_refs if str(item)),
                    *(str(item.get("id") or "") for item in data.observations),
                    *(str(item["id"]) for item in findings),
                    *(str(item["id"]) for item in risks),
                    *(str(item.id) for item in changes),
                    *(str(item.id) for item in events),
                    *(str(item.id) for item in event_occurrences),
                    str(qualification["id"]),
                    str(coverage_record["id"]),
                    execution.id,
                    str(execution.runtime_bundle_version_id),
                }
                - {""}
            )
            execution.execution_status = "completed"
            execution.reason_codes_json = []
            execution.observation_count = len(data.observations)
            execution.observed_entity_count = len(observed_entities)
            execution.change_count = len(changes)
            execution.event_count = (
                len(event_occurrences)
                if data.event_occurrence_ids
                else len(events)
            )
            execution.current_finding_count = len(findings)
            execution.evidence_refs_json = evidence
            execution.completed_at = at
            execution.updated_at = at
            decision = CheckAssessmentService.assess(
                CheckAssessmentInput(
                    execution_id=execution.id,
                    patrol_batch_id=execution.patrol_batch_id,
                    device_id=execution.device_id,
                    check_item_version_id=execution.check_item_version_id,
                    execution_status="completed",
                    assessment_mode=check_version.assessment_mode,
                    comparison_status=_comparison_status(context),
                    observation_count=len(data.observations),
                    expected_entity_count=coverage_record.get(
                        "expected_entity_count"
                    ),
                    observed_entity_count=len(observed_entities),
                    evaluated_entity_count=int(
                        coverage.get("evaluated_entity_count") or 0
                    ),
                    threshold_unknown_count=int(
                        coverage.get(
                            "threshold_unknown_observation_count"
                        )
                        or 0
                    ),
                    partial_threshold_count=int(
                        coverage.get(
                            "partial_threshold_observation_count"
                        )
                        or 0
                    ),
                    current_finding_ids=tuple(
                        str(item["id"]) for item in findings
                    ),
                    unresolved_risk_case_ids=tuple(
                        str(item.id) for item in open_risks
                    ),
                    evidence_qualification_id=str(qualification["id"]),
                    coverage_decision_id=str(coverage_record["id"]),
                    source_identity_status=str(
                        qualification["source_identity_status"]
                    ),
                    output_quality_status=str(
                        qualification["output_quality_status"]
                    ),
                    freshness_status=str(qualification["freshness_status"]),
                    empty_result_classification=str(
                        qualification["empty_result_classification"]
                    ),
                    coverage_status=str(coverage_record["coverage_status"]),
                    coverage_basis=str(coverage_record["coverage_basis"]),
                    rule_assets_ready=bool(
                        [
                            item
                            for item in assets
                            if item["asset_type"] == "current_state_rule"
                        ]
                    )
                    or check_version.assessment_mode != "health",
                    blocking_reason_codes=tuple(
                        str(item)
                        for item in coverage.get(
                            "assessment_policy_blocking_reason_codes"
                        )
                        or []
                        if str(item)
                    ),
                    evidence_refs=tuple(evidence),
                    assessment_rule_bundle_digest=(
                        execution.runtime_bundle_digest
                    ),
                    runtime_bundle_version_id=(
                        str(execution.runtime_bundle_version_id)
                        if execution.runtime_bundle_version_id
                        else None
                    ),
                    assessment_policy_snapshot_digest=(
                        assessment_policy_snapshot_digest
                    ),
                    assessment_basis=dict(
                        coverage.get("assessment_basis") or {}
                    ),
                    comparison_context_digest=(
                        str(context.digest) if context else None
                    ),
                    assessed_at=at,
                )
            )
            current_risk_ids = {
                str(item["id"]) for item in risks
            }
            for risk in open_risks:
                if str(risk.id) in current_risk_ids:
                    continue
                if decision.values["risk_verification_status"] in {
                    "recovery_pending",
                    "no_current_risk",
                }:
                    risk.verification_status = "recovery_pending"
                    risk.last_verified_at = at
                    risk.last_verification_execution_id = execution.id
                else:
                    risk.verification_status = "not_reverified"
                risk.updated_at = at
            db.add(CheckAssessmentRecord(**decision.values))
            execution.assessment_id = decision.id
            execution.risk_verification_status = decision.values[
                "risk_verification_status"
            ]
            db.flush()
            return self._result_payload(
                execution,
                decision.values,
                findings=findings,
                risks=[item.to_dict() for item in open_risks],
                changes=[item.to_dict() for item in changes],
                events=[item.to_dict() for item in events],
            )

    def finalize_text(
        self,
        data: TextExecutionResultInput,
    ) -> dict[str, Any]:
        existing = self._existing_result(data.execution_id)
        if existing is not None:
            return existing
        at = _utc(data.assessed_at)
        with self._session_scope() as db:
            execution, check_version, context = self._locked_context(
                db,
                data.execution_id,
            )
            if str(check_version.result_mode) not in {
                "normalized_diff",
                "hash_only",
            }:
                raise ValueError("check execution is not a text-comparison mode")
            bundle = self._frozen_bundle(execution)
            profile_id = str(
                bundle.get("comparison_profile_version_id") or ""
            )
            profile = TextComparisonProfileRepository(
                self._session_scope
            ).get_version(profile_id)
            if profile is None:
                raise ValueError("frozen text comparison profile is missing")
            current = db.get(CommandSnapshot, data.current_snapshot_id)
            current_device_id = (
                str(current.inventory_device_id or current.device_id)
                if current is not None
                else ""
            )
            if current is None or current_device_id != execution.device_id:
                raise ValueError("current snapshot does not belong to the execution")
            previous = None
            comparison_status = _comparison_status(context)
            if comparison_status == "comparable":
                allowed = {
                    str(item)
                    for item in context.baseline_snapshot_ids_json or []
                }
                previous_id = str(data.previous_snapshot_id or "")
                if not previous_id or previous_id not in allowed:
                    raise ValueError(
                        "previous snapshot must come from the frozen comparison context"
                    )
                previous = db.get(CommandSnapshot, previous_id)
                if previous is None:
                    raise ValueError("frozen baseline snapshot is missing")
            elif data.previous_snapshot_id:
                raise ValueError(
                    "a non-comparable execution cannot accept a baseline snapshot"
                )
            comparison = compare_text(
                current_text=str(current.raw_text or ""),
                previous_text=(
                    str(previous.raw_text or "") if previous is not None else None
                ),
                profile=profile,
            )
            changes: list[NetworkChangeRecord] = []
            if comparison.changed is True:
                changes.append(
                    self._save_text_change(
                        db,
                        execution=execution,
                        check_version=check_version,
                        current=current,
                        previous=previous,
                        comparison=comparison,
                        profile=profile,
                        detected_at=at,
                    )
                )
            evidence = sorted(
                {
                    *(str(item) for item in execution.evidence_refs_json or []),
                    *(str(item) for item in data.evidence_refs if str(item)),
                    execution.id,
                    current.id,
                    *(str(previous.id) for _ in [0] if previous is not None),
                    str(execution.runtime_bundle_version_id),
                    profile.version_id,
                }
            )
            execution.execution_status = "completed"
            execution.reason_codes_json = []
            execution.observation_count = 0
            execution.observed_entity_count = 1
            execution.change_count = len(changes)
            execution.event_count = 0
            execution.current_finding_count = 0
            execution.evidence_refs_json = evidence
            execution.completed_at = at
            execution.updated_at = at
            decision = CheckAssessmentService.assess(
                CheckAssessmentInput(
                    execution_id=execution.id,
                    patrol_batch_id=execution.patrol_batch_id,
                    device_id=execution.device_id,
                    check_item_version_id=execution.check_item_version_id,
                    execution_status="completed",
                    assessment_mode=check_version.assessment_mode,
                    comparison_status=comparison_status,
                    observation_count=0,
                    observed_entity_count=1,
                    evaluated_entity_count=1,
                    rule_assets_ready=True,
                    evidence_refs=tuple(evidence),
                    assessment_rule_bundle_digest=(
                        execution.runtime_bundle_digest
                    ),
                    runtime_bundle_version_id=(
                        str(execution.runtime_bundle_version_id)
                        if execution.runtime_bundle_version_id
                        else None
                    ),
                    assessment_policy_snapshot_digest=(
                        str(execution.assessment_policy_snapshot_digest)
                        if execution.assessment_policy_snapshot_digest
                        else None
                    ),
                    comparison_context_digest=(
                        str(context.digest) if context else None
                    ),
                    assessed_at=at,
                )
            )
            db.add(CheckAssessmentRecord(**decision.values))
            execution.assessment_id = decision.id
            db.flush()
            return self._result_payload(
                execution,
                decision.values,
                changes=[item.to_dict() for item in changes],
                comparison={
                    "status": comparison.comparison_status,
                    "changed": comparison.changed,
                    "profile_version_id": comparison.profile_version_id,
                    "profile_digest": comparison.profile_digest,
                    "previous_digest": comparison.previous_digest,
                    "current_digest": comparison.current_digest,
                    "added_lines": list(comparison.added_lines),
                    "removed_lines": list(comparison.removed_lines),
                    "redacted_preview": list(comparison.redacted_preview),
                },
            )

    def finalize_evidence(
        self,
        data: EvidenceExecutionResultInput,
    ) -> dict[str, Any]:
        existing = self._existing_result(data.execution_id)
        if existing is not None:
            return existing
        at = _utc(data.assessed_at)
        with self._session_scope() as db:
            execution, check_version, context = self._locked_context(
                db,
                data.execution_id,
            )
            if str(check_version.result_mode) != "evidence_only":
                raise ValueError("check execution is not evidence-only")
            evidence = sorted(
                {
                    *(str(item) for item in execution.evidence_refs_json or []),
                    *(str(item) for item in data.evidence_refs if str(item)),
                    execution.id,
                }
            )
            execution.execution_status = "completed"
            execution.reason_codes_json = []
            execution.evidence_refs_json = evidence
            execution.completed_at = at
            execution.updated_at = at
            decision = CheckAssessmentService.assess(
                CheckAssessmentInput(
                    execution_id=execution.id,
                    patrol_batch_id=execution.patrol_batch_id,
                    device_id=execution.device_id,
                    check_item_version_id=execution.check_item_version_id,
                    execution_status="completed",
                    assessment_mode="evidence",
                    comparison_status=_comparison_status(context),
                    observation_count=0,
                    observed_entity_count=1,
                    evaluated_entity_count=1,
                    rule_assets_ready=True,
                    evidence_refs=tuple(evidence),
                    assessment_rule_bundle_digest=(
                        execution.runtime_bundle_digest
                    ),
                    runtime_bundle_version_id=(
                        str(execution.runtime_bundle_version_id)
                        if execution.runtime_bundle_version_id
                        else None
                    ),
                    assessment_policy_snapshot_digest=(
                        str(execution.assessment_policy_snapshot_digest)
                        if execution.assessment_policy_snapshot_digest
                        else None
                    ),
                    comparison_context_digest=(
                        str(context.digest) if context else None
                    ),
                    assessed_at=at,
                )
            )
            db.add(CheckAssessmentRecord(**decision.values))
            execution.assessment_id = decision.id
            db.flush()
            return self._result_payload(execution, decision.values)

    def _frozen_bundle(self, execution: CheckExecutionRecord) -> dict[str, Any]:
        if not execution.runtime_bundle_version_id:
            raise ValueError("check execution has no frozen runtime bundle")
        bundle = CheckItemRuntimeBundleService(self._session_scope).get_version(
            str(execution.runtime_bundle_version_id)
        )
        if bundle is None:
            raise ValueError("frozen runtime bundle is missing")
        if str(bundle["digest"]) != str(execution.runtime_bundle_digest):
            raise ValueError("frozen runtime bundle digest differs from execution")
        if str(bundle["check_item_version_id"]) != str(
            execution.check_item_version_id
        ):
            raise ValueError("runtime bundle check-item version mismatch")
        if str(bundle["implementation_version_id"]) != str(
            execution.implementation_version_id
        ):
            raise ValueError("runtime bundle implementation version mismatch")
        frozen_policy_contract = dict(
            dict(bundle.get("compatibility") or {}).get(
                "assessment_policy"
            )
            or {}
        )
        if frozen_policy_contract:
            target_snapshot = dict(
                getattr(execution, "assessment_policy_snapshot_json", None)
                or {}
            )
            if not validate_assessment_policy_snapshot(target_snapshot):
                raise ValueError(
                    "frozen assessment policy snapshot is missing or invalid"
                )
            snapshot_digest = str(target_snapshot.get("digest") or "")
            if snapshot_digest != str(
                getattr(
                    execution,
                    "assessment_policy_snapshot_digest",
                    None,
                )
                or ""
            ):
                raise ValueError(
                    "assessment policy snapshot digest differs from execution"
                )
            if str(target_snapshot.get("contract_digest") or "") != str(
                frozen_policy_contract.get("digest") or ""
            ):
                raise ValueError(
                    "assessment policy snapshot differs from runtime bundle"
                )
        return bundle

    @staticmethod
    def _evidence_policy_snapshot(bundle: dict[str, Any]) -> dict[str, Any]:
        compatibility = dict(bundle.get("compatibility") or {})
        explicit = compatibility.get("evidence_policy")
        if isinstance(explicit, dict):
            return dict(explicit)
        return {
            key: dict(compatibility.get(key) or {})
            for key in (
                "freshness_policy",
                "empty_result_policy",
                "coverage_policy",
                "output_quality_profile",
            )
            if isinstance(compatibility.get(key), dict)
        }

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return _utc(value)
        if not value:
            return None
        try:
            return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        except ValueError:
            return None

    @staticmethod
    def _validate_observation(
        item: dict[str, Any],
        *,
        execution: CheckExecutionRecord,
        bundle: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(item)
        if str(normalized.get("device_id") or "") != str(execution.device_id):
            raise ValueError("observation belongs to another device")
        parser_ids = {
            str(asset["asset_version_id"])
            for asset in bundle["assets"]
            if asset["asset_type"] == "parser_template"
        }
        parser_id = str(normalized.get("parser_template_id") or "")
        if parser_id not in parser_ids:
            raise ValueError("observation Parser is not frozen in the runtime bundle")
        expected_types = set(
            bundle["compatibility"].get("expected_observation_types") or []
        )
        if str(normalized.get("observation_type") or "") not in expected_types:
            raise ValueError("observation type is outside the check-item contract")
        return normalized

    @staticmethod
    def _load_current_state_rules(
        db: Any,
        assets: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        version_ids = sorted(
            {
                str(item["asset_version_id"])
                for item in assets
                if item["asset_type"] == "current_state_rule"
            }
        )
        if not version_ids:
            return ()
        rows = (
            db.query(EventRuleVersionRecord, EventRuleRecord)
            .join(
                EventRuleRecord,
                EventRuleRecord.id == EventRuleVersionRecord.rule_id,
            )
            .filter(EventRuleVersionRecord.id.in_(version_ids))
            .all()
        )
        if len(rows) != len(version_ids):
            raise ValueError("one or more frozen current-state rules are missing")
        return tuple(
            {
                "rule_id": str(rule.id),
                "rule_name": str(rule.name),
                "version_id": str(version.id),
                "version": int(version.version),
                "digest": str(version.digest),
                "event_type": str(version.event_type),
                "severity": str(version.severity),
                "event_class": str(version.event_class or "fault"),
                "evaluation_scope": govern_evaluation_scope(
                    version.evaluation_scope,
                    event_class=str(version.event_class or "fault"),
                ),
                "condition": dict(version.condition_json or {}),
                "priority": int(version.priority or 100),
                "exclusive": bool(version.exclusive),
            }
            for version, rule in rows
        )

    @staticmethod
    def _locked_context(
        db: Any,
        execution_id: str,
    ) -> tuple[
        CheckExecutionRecord,
        CheckItemVersionRecord,
        ComparisonContextRecord | None,
    ]:
        execution = (
            db.query(CheckExecutionRecord)
            .filter(CheckExecutionRecord.id == execution_id)
            .with_for_update()
            .first()
        )
        if execution is None:
            raise ValueError("check execution not found")
        if execution.assessment_id:
            raise ValueError("check execution is already terminal")
        check_version = db.get(
            CheckItemVersionRecord,
            execution.check_item_version_id,
        )
        if check_version is None:
            raise ValueError("check-item version is missing")
        context = (
            db.get(ComparisonContextRecord, execution.comparison_context_id)
            if execution.comparison_context_id
            else None
        )
        return execution, check_version, context

    def _existing_result(self, execution_id: str) -> dict[str, Any] | None:
        with self._session_scope() as db:
            execution = db.get(CheckExecutionRecord, execution_id)
            if execution is None:
                raise ValueError("check execution not found")
            if not execution.assessment_id:
                return None
            assessment = db.get(CheckAssessmentRecord, execution.assessment_id)
            if assessment is None:
                raise ValueError("terminal execution has a missing Assessment")
            return self._result_payload(execution, assessment.to_dict())

    @staticmethod
    def _open_risks(
        db: Any,
        *,
        check_item_id: str,
        device_id: str,
    ) -> list[RiskCaseRecord]:
        return (
            db.query(RiskCaseRecord)
            .filter(
                RiskCaseRecord.check_item_id == check_item_id,
                RiskCaseRecord.device_id == device_id,
                RiskCaseRecord.status.in_(
                    ["open", "acknowledged", "recovering"]
                ),
            )
            .order_by(RiskCaseRecord.first_seen_at, RiskCaseRecord.id)
            .all()
        )

    @staticmethod
    def _bind_changes(
        db: Any,
        *,
        ids: Iterable[str],
        execution: CheckExecutionRecord,
        check_item_id: str,
    ) -> list[NetworkChangeRecord]:
        normalized = sorted(set(str(item) for item in ids if str(item)))
        if not normalized:
            return []
        rows = (
            db.query(NetworkChangeRecord)
            .filter(NetworkChangeRecord.id.in_(normalized))
            .all()
        )
        if len(rows) != len(normalized):
            raise ValueError("one or more supplied Changes do not exist")
        for row in rows:
            if str(row.device_id or "") != str(execution.device_id):
                raise ValueError("Change belongs to another device")
            if row.check_execution_id and row.check_execution_id != execution.id:
                raise ValueError("Change is already bound to another check execution")
            row.check_item_id = check_item_id
            row.check_item_version_id = execution.check_item_version_id
            row.check_execution_id = execution.id
        return rows

    @staticmethod
    def _bind_events(
        db: Any,
        *,
        ids: Iterable[str],
        execution: CheckExecutionRecord,
        check_item_id: str,
    ) -> list[NetworkEventRecord]:
        normalized = sorted(set(str(item) for item in ids if str(item)))
        if not normalized:
            return []
        rows = (
            db.query(NetworkEventRecord)
            .filter(NetworkEventRecord.id.in_(normalized))
            .all()
        )
        if len(rows) != len(normalized):
            raise ValueError("one or more supplied Events do not exist")
        for row in rows:
            if str(row.device_id or "") != str(execution.device_id):
                raise ValueError("Event belongs to another device")
            if row.check_execution_id and row.check_execution_id != execution.id:
                raise ValueError("Event is already bound to another check execution")
            row.check_item_id = check_item_id
            row.check_item_version_id = execution.check_item_version_id
            row.check_execution_id = execution.id
        return rows

    @staticmethod
    def _bind_event_occurrences(
        db: Any,
        *,
        ids: Iterable[str],
        execution: CheckExecutionRecord,
        changes: Iterable[NetworkChangeRecord],
    ) -> list[NetworkEventOccurrenceRecord]:
        normalized = sorted(set(str(item) for item in ids if str(item)))
        if not normalized:
            return []
        rows = (
            db.query(NetworkEventOccurrenceRecord)
            .filter(NetworkEventOccurrenceRecord.id.in_(normalized))
            .all()
        )
        if len(rows) != len(normalized):
            raise ValueError("one or more supplied Event occurrences do not exist")
        change_ids = {str(item.id) for item in changes}
        for row in rows:
            if str(row.source_change_id or "") not in change_ids:
                raise ValueError(
                    "Event occurrence is not linked to this check execution"
                )
            payload = dict(row.payload_json or {})
            if str(payload.get("device_id") or "") != str(execution.device_id):
                raise ValueError("Event occurrence belongs to another device")
        return rows

    @staticmethod
    def _save_text_change(
        db: Any,
        *,
        execution: CheckExecutionRecord,
        check_version: CheckItemVersionRecord,
        current: CommandSnapshot,
        previous: CommandSnapshot | None,
        comparison: Any,
        profile: Any,
        detected_at: datetime,
    ) -> NetworkChangeRecord:
        identity = {
            "execution_id": execution.id,
            "previous_snapshot_id": previous.id if previous else None,
            "current_snapshot_id": current.id,
            "profile_version_id": profile.version_id,
            "previous_digest": comparison.previous_digest,
            "current_digest": comparison.current_digest,
        }
        idempotency_key = _digest(identity)
        existing = (
            db.query(NetworkChangeRecord)
            .filter(
                NetworkChangeRecord.idempotency_key == idempotency_key
            )
            .first()
        )
        if existing is not None:
            return existing
        contract = dict(check_version.canonical_contract_json or {})
        row = NetworkChangeRecord(
            id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/check-item-text-change/{idempotency_key}",
                )
            ),
            idempotency_key=idempotency_key,
            run_id=current.run_id,
            check_item_id=check_version.check_item_id,
            check_item_version_id=check_version.id,
            check_execution_id=execution.id,
            device_id=execution.device_id,
            canonical_command=current.canonical_command,
            entity_type=str(
                contract.get("entity_type") or "raw_command_output"
            ),
            entity_key=str(execution.device_id),
            dimensions_json={
                "comparison_profile_version_id": profile.version_id,
                "redacted_preview": list(comparison.redacted_preview),
            },
            field="raw_text_hash",
            observation_type="raw.command.hash",
            old_value_json=comparison.previous_digest,
            new_value_json=comparison.current_digest,
            change_type="modified",
            previous_snapshot_id=previous.id if previous else None,
            current_snapshot_id=current.id,
            source="patrol_check_item",
            analysis_revision="check_item_v2",
            rule_bundle_digest=execution.runtime_bundle_digest,
            event_status="no_event",
            comparison_type=f"text_{profile.mode}",
            decision_reason="normalized_text_changed",
            policy_snapshot_json={
                "profile_id": profile.id,
                "profile_version_id": profile.version_id,
                "profile_digest": profile.digest,
                "mode": profile.mode,
            },
            observed_at=current.observed_at,
            detected_at=detected_at,
            processed_at=detected_at,
            confidence=1.0,
            created_at=detected_at,
        )
        db.add(row)
        db.flush()
        return row

    @staticmethod
    def _result_payload(
        execution: CheckExecutionRecord,
        assessment: dict[str, Any],
        *,
        findings: list[dict[str, Any]] | None = None,
        risks: list[dict[str, Any]] | None = None,
        changes: list[dict[str, Any]] | None = None,
        events: list[dict[str, Any]] | None = None,
        comparison: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "execution": execution.to_dict(),
            "assessment": dict(assessment),
            "findings": list(findings or []),
            "risks": list(risks or []),
            "changes": list(changes or []),
            "events": list(events or []),
            "comparison": dict(comparison or {}),
        }
