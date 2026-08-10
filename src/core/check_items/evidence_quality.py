"""Fail-closed evidence qualification and entity coverage decisions."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.infrastructure.db.models import (
    CheckExecutionRecord,
    CoverageDecisionRecord,
    EvidenceQualificationRecord,
)
from src.infrastructure.db.postgres import get_db_session

_IDENTITY_STATUSES = {
    "confirmed",
    "ambiguous",
    "conflict",
    "external_unbound",
    "unknown",
}
_OUTPUT_STATUSES = {
    "complete",
    "empty",
    "unsupported",
    "device_error",
    "truncated",
    "paged",
    "ambiguous",
    "unknown",
}
_COVERAGE_BASES = {
    "fixed",
    "inventory_entity_set",
    "derived_entity_set",
    "command_reported_total",
    "non_exhaustive",
    "unknown",
}
_OUTPUT_STATUS_MAP = {
    "data": "complete",
    "success": "complete",
    "complete": "complete",
    "valid_empty": "empty",
    "empty": "empty",
    "unsupported_command": "unsupported",
    "unsupported": "unsupported",
    "command_error": "device_error",
    "device_error": "device_error",
    "truncated": "truncated",
    "paged": "paged",
    "header_only_unverified": "ambiguous",
    "empty_unverified": "ambiguous",
    "not_classified": "ambiguous",
    "ambiguous": "ambiguous",
    "unknown": "unknown",
}
_OUTPUT_PRECEDENCE = {
    "unknown": 0,
    "complete": 1,
    "empty": 2,
    "unsupported": 3,
    "ambiguous": 4,
    "device_error": 5,
    "paged": 6,
    "truncated": 7,
}
_DURATION_RE = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>s|sec|m|min|h|d)?\s*$",
    re.IGNORECASE,
)


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


def _utc(value: datetime) -> datetime:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc)


def _duration_seconds(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) >= 0 else None
    match = _DURATION_RE.fullmatch(str(value))
    if match is None:
        return None
    number = float(match.group("value"))
    multiplier = {
        None: 1.0,
        "s": 1.0,
        "sec": 1.0,
        "m": 60.0,
        "min": 60.0,
        "h": 3600.0,
        "d": 86400.0,
    }[str(match.group("unit")).lower() if match.group("unit") else None]
    return number * multiplier


def _entity_key(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _canonical(value)


@dataclass(frozen=True, slots=True)
class EvidenceQualificationInput:
    execution_id: str
    patrol_batch_id: str
    device_id: str
    check_item_version_id: str
    implementation_version_id: str | None
    runtime_bundle_version_id: str | None
    runtime_bundle_digest: str | None
    snapshot_ids: tuple[str, ...]
    source_device_ids: tuple[str, ...]
    inventory_device_ids: tuple[str | None, ...]
    output_statuses: tuple[str, ...]
    observed_ats: tuple[datetime, ...]
    output_reasons: tuple[str | None, ...] = ()
    # Kept as compatibility-only inputs for older callers. Raw-evidence
    # qualification must never depend on Parser or Observation results.
    observation_count: int | None = None
    assessment_mode: str | None = None
    policy_snapshot: Mapping[str, Any] = field(default_factory=dict)
    profile_version_id: str | None = None
    profile_digest: str | None = None
    evidence_refs: tuple[str, ...] = ()
    qualified_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True, slots=True)
class EvidenceQualificationDecision:
    id: str
    digest: str
    source_identity_status: str
    output_quality_status: str
    freshness_status: str
    empty_result_classification: str
    reason_codes: tuple[str, ...]
    values: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CoverageDecisionInput:
    execution_id: str
    patrol_batch_id: str
    device_id: str
    check_item_version_id: str
    observed_entity_keys: tuple[Any, ...]
    evaluated_entity_count: int
    policy_snapshot: Mapping[str, Any] = field(default_factory=dict)
    expected_entity_keys: tuple[Any, ...] = ()
    expected_entity_count: int | None = None
    denominator_ref: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    evaluable: bool = True
    evaluation_failure_reason_codes: tuple[str, ...] = ()
    decided_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True, slots=True)
class CoverageDecision:
    id: str
    digest: str
    coverage_status: str
    coverage_basis: str
    reason_codes: tuple[str, ...]
    values: dict[str, Any]


class EvidenceQualificationService:
    """Qualify identity, output completeness and time without reading raw CLI."""

    @staticmethod
    def qualify(data: EvidenceQualificationInput) -> EvidenceQualificationDecision:
        qualified_at = _utc(data.qualified_at)
        policy = dict(data.policy_snapshot or {})
        reasons: list[str] = []
        identity = EvidenceQualificationService._identity_status(data, reasons)
        output = EvidenceQualificationService._output_status(
            data.output_statuses,
            reasons,
        )
        freshness, observation_min_at, observation_max_at = (
            EvidenceQualificationService._freshness_status(
                data.observed_ats,
                policy=dict(policy.get("freshness_policy") or {}),
                qualified_at=qualified_at,
                reasons=reasons,
            )
        )
        empty_result = EvidenceQualificationService._empty_result(
            output_status=output,
            output_statuses=data.output_statuses,
            output_reasons=data.output_reasons,
            policy=dict(policy.get("empty_result_policy") or {}),
            applicability_decision=dict(
                policy.get("applicability_decision") or {}
            ),
            reasons=reasons,
        )
        policy_digest = _digest(policy)
        payload = {
            "check_execution_id": data.execution_id,
            "patrol_batch_id": data.patrol_batch_id,
            "device_id": data.device_id,
            "check_item_version_id": data.check_item_version_id,
            "implementation_version_id": data.implementation_version_id,
            "runtime_bundle_version_id": data.runtime_bundle_version_id,
            "runtime_bundle_digest": data.runtime_bundle_digest,
            "source_identity_status": identity,
            "output_quality_status": output,
            "freshness_status": freshness,
            "empty_result_classification": empty_result,
            "snapshot_ids": sorted(set(data.snapshot_ids)),
            "observation_min_at": (
                observation_min_at.isoformat() if observation_min_at else None
            ),
            "observation_max_at": (
                observation_max_at.isoformat() if observation_max_at else None
            ),
            "qualification_reason_codes": list(dict.fromkeys(reasons)),
            "policy_digest": policy_digest,
            "profile_version_id": data.profile_version_id,
            "profile_digest": data.profile_digest,
            "evidence_refs": sorted(set(data.evidence_refs)),
            "qualified_at": qualified_at.isoformat(),
        }
        digest = _digest(payload)
        decision_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/evidence-qualification/{data.execution_id}/{digest}",
            )
        )
        values = {
            "id": decision_id,
            "digest": digest,
            "check_execution_id": data.execution_id,
            "patrol_batch_id": data.patrol_batch_id,
            "device_id": data.device_id,
            "check_item_version_id": data.check_item_version_id,
            "implementation_version_id": data.implementation_version_id,
            "runtime_bundle_version_id": data.runtime_bundle_version_id,
            "runtime_bundle_digest": data.runtime_bundle_digest,
            "source_identity_status": identity,
            "output_quality_status": output,
            "freshness_status": freshness,
            "empty_result_classification": empty_result,
            "snapshot_ids_json": sorted(set(data.snapshot_ids)),
            "observation_min_at": observation_min_at,
            "observation_max_at": observation_max_at,
            "qualification_reason_codes_json": list(dict.fromkeys(reasons)),
            "policy_snapshot_json": policy,
            "policy_digest": policy_digest,
            "profile_version_id": data.profile_version_id,
            "profile_digest": data.profile_digest,
            "evidence_refs_json": sorted(set(data.evidence_refs)),
            "qualified_at": qualified_at,
            "created_at": datetime.now(timezone.utc),
        }
        return EvidenceQualificationDecision(
            id=decision_id,
            digest=digest,
            source_identity_status=identity,
            output_quality_status=output,
            freshness_status=freshness,
            empty_result_classification=empty_result,
            reason_codes=tuple(dict.fromkeys(reasons)),
            values=values,
        )

    @staticmethod
    def _identity_status(
        data: EvidenceQualificationInput,
        reasons: list[str],
    ) -> str:
        expected = str(data.device_id or "")
        inventory_ids = {
            str(item)
            for item in data.inventory_device_ids
            if item is not None and str(item)
        }
        source_ids = {
            str(item) for item in data.source_device_ids if str(item)
        }
        if inventory_ids:
            if inventory_ids == {expected} and (
                not source_ids or source_ids == {expected}
            ):
                return "confirmed"
            reasons.append("source_identity_conflict")
            return "conflict"
        if source_ids == {expected}:
            reasons.append("source_identity_legacy_exact_match")
            return "confirmed"
        if not source_ids:
            reasons.append("source_identity_unknown")
            return "unknown"
        if len(source_ids) > 1 or expected not in source_ids:
            reasons.append("source_identity_conflict")
            return "conflict"
        reasons.append("source_identity_ambiguous")
        return "ambiguous"

    @staticmethod
    def _output_status(
        statuses: Iterable[str],
        reasons: list[str],
    ) -> str:
        normalized = [
            _OUTPUT_STATUS_MAP.get(str(item or "").strip(), "unknown")
            for item in statuses
        ]
        if not normalized:
            reasons.append("output_quality_unknown")
            return "unknown"
        selected = max(normalized, key=lambda item: _OUTPUT_PRECEDENCE[item])
        if selected != "complete":
            reasons.append(f"output_quality_{selected}")
        return selected

    @staticmethod
    def _freshness_status(
        observed_ats: Iterable[datetime],
        *,
        policy: Mapping[str, Any],
        qualified_at: datetime,
        reasons: list[str],
    ) -> tuple[str, datetime | None, datetime | None]:
        observed = sorted(_utc(item) for item in observed_ats if item is not None)
        if not observed:
            reasons.append("observation_time_unknown")
            return "unknown", None, None
        earliest, latest = observed[0], observed[-1]
        max_age = _duration_seconds(
            policy.get("max_observation_age_seconds")
            if "max_observation_age_seconds" in policy
            else policy.get("max_observation_age")
        )
        max_span = _duration_seconds(
            policy.get("max_collection_span_seconds")
            if "max_collection_span_seconds" in policy
            else policy.get("max_collection_span")
        )
        skew = _duration_seconds(
            policy.get("clock_skew_tolerance_seconds")
            if "clock_skew_tolerance_seconds" in policy
            else policy.get("clock_skew_tolerance")
        )
        if max_age is None:
            reasons.append("freshness_policy_missing")
            return "unknown", earliest, latest
        if latest > qualified_at + timedelta(seconds=skew or 0):
            reasons.append("observation_clock_ahead")
            return "stale", earliest, latest
        if (qualified_at - latest).total_seconds() > max_age:
            reasons.append("observation_stale")
            return "stale", earliest, latest
        if max_span is None:
            reasons.append("collection_span_policy_missing")
            return "unknown", earliest, latest
        if (latest - earliest).total_seconds() > max_span:
            reasons.append("collection_span_exceeded")
            return "stale", earliest, latest
        return "fresh", earliest, latest

    @staticmethod
    def _empty_result(
        *,
        output_status: str,
        output_statuses: Iterable[str],
        output_reasons: Iterable[str | None],
        policy: Mapping[str, Any],
        applicability_decision: Mapping[str, Any],
        reasons: list[str],
    ) -> str:
        if output_status == "complete":
            return "not_evaluated"
        if output_status == "unsupported":
            reasons.append("unsupported_command_cannot_be_not_applicable")
            return "unsupported_unapproved"
        if output_status == "empty":
            normalized_statuses = tuple(
                _OUTPUT_STATUS_MAP.get(str(item or "").strip(), "unknown")
                for item in output_statuses
            )
            if not normalized_statuses or any(
                item != "empty" for item in normalized_statuses
            ):
                reasons.append("mixed_data_and_empty_output")
                return "invalid"
            selected = dict(
                applicability_decision.get("selected_policy") or {}
            )
            classification = str(
                selected.get("empty_result_classification") or "invalid"
            ).strip()
            approved_patterns = {
                str(item)
                for item in selected.get("legal_empty_patterns") or []
                if str(item)
            }
            observed_reasons = tuple(
                str(item) for item in output_reasons if item is not None and str(item)
            )
            governed = bool(selected.get("policy_version_id"))
            if governed:
                if (
                    classification in {"expected_empty", "not_applicable"}
                    and len(observed_reasons) == len(normalized_statuses)
                    and set(observed_reasons).issubset(approved_patterns)
                ):
                    reasons.append("governed_legal_empty_evidence_matched")
                    return classification
                reasons.append("empty_output_not_authorized_by_frozen_applicability")
                return "invalid"

            configured = str(policy.get("classification") or "").strip()
            if configured in {"expected_empty", "not_applicable"}:
                reasons.append("legacy_empty_result_policy_used")
                return configured
            reasons.append("empty_output_without_approved_policy")
            return "invalid"
        return "not_evaluated"


class CoverageDecisionService:
    """Compare canonical entities against an auditable denominator."""

    @staticmethod
    def decide(data: CoverageDecisionInput) -> CoverageDecision:
        policy = dict(data.policy_snapshot or {})
        basis = str(policy.get("model") or "unknown")
        if basis not in _COVERAGE_BASES:
            basis = "unknown"
        observed = sorted(
            set(_entity_key(item) for item in data.observed_entity_keys)
        )
        expected = sorted(
            set(_entity_key(item) for item in data.expected_entity_keys)
        )
        expected_count = (
            len(expected)
            if expected
            else int(data.expected_entity_count)
            if data.expected_entity_count is not None
            else int(policy["expected_count"])
            if policy.get("expected_count") is not None
            else None
        )
        reasons: list[str] = []
        missing = sorted(set(expected) - set(observed))
        unexpected = sorted(set(observed) - set(expected)) if expected else []
        denominator_ref = dict(data.denominator_ref or {})
        reference_basis = str(
            denominator_ref.get("basis")
            or denominator_ref.get("model")
            or ""
        )
        if not data.evaluable:
            status = "not_evaluable"
            reasons.extend(
                [
                    "coverage_not_evaluable",
                    *(
                        str(item)
                        for item in data.evaluation_failure_reason_codes
                        if str(item)
                    ),
                ]
            )
        elif basis == "non_exhaustive":
            status = "unbounded"
            reasons.append("coverage_non_exhaustive")
        elif basis == "unknown":
            status = "unknown"
            reasons.append("coverage_policy_missing")
        elif (
            basis
            in {
                "inventory_entity_set",
                "derived_entity_set",
                "command_reported_total",
            }
            and (
                not denominator_ref
                or reference_basis != basis
            )
        ):
            status = "unknown"
            reasons.append("coverage_denominator_reference_missing")
        elif expected_count == 0 and not observed:
            status = "complete"
        elif not observed:
            status = "empty"
            reasons.append("coverage_empty")
        elif expected:
            status = "complete" if not missing else "partial"
        elif expected_count is None:
            status = "unknown"
            reasons.append("coverage_denominator_missing")
        elif len(observed) < expected_count:
            status = "partial"
            reasons.append("entity_coverage_incomplete")
        elif (
            basis in {"fixed", "command_reported_total"}
            and len(observed) > expected_count
        ):
            status = "partial"
            reasons.append("entity_coverage_unexpected_count")
        else:
            status = "complete"
        if (
            status == "complete"
            and bool(policy.get("require_rule_evaluation", True))
            and
            observed
            and max(0, int(data.evaluated_entity_count)) < len(observed)
        ):
            status = "partial"
            reasons.append("entity_evaluation_incomplete")
        if missing:
            reasons.append("entity_keys_missing")
        if unexpected:
            reasons.append("entity_keys_unexpected")
        policy_digest = _digest(policy)
        expected_digest = _digest(expected) if expected else None
        observed_digest = _digest(observed)
        decided_at = _utc(data.decided_at)
        payload = {
            "check_execution_id": data.execution_id,
            "patrol_batch_id": data.patrol_batch_id,
            "device_id": data.device_id,
            "check_item_version_id": data.check_item_version_id,
            "coverage_status": status,
            "coverage_basis": basis,
            "expected_entity_count": expected_count,
            "observed_entity_count": len(observed),
            "evaluated_entity_count": max(0, int(data.evaluated_entity_count)),
            "expected_entity_set_digest": expected_digest,
            "observed_entity_set_digest": observed_digest,
            "denominator_ref": denominator_ref,
            "missing_entity_sample": missing[:100],
            "unexpected_entity_sample": unexpected[:100],
            "reason_codes": list(dict.fromkeys(reasons)),
            "evaluable": bool(data.evaluable),
            "policy_digest": policy_digest,
            "evidence_refs": sorted(set(data.evidence_refs)),
            "decided_at": decided_at.isoformat(),
        }
        digest = _digest(payload)
        decision_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/coverage-decision/{data.execution_id}/{digest}",
            )
        )
        values = {
            "id": decision_id,
            "digest": digest,
            "check_execution_id": data.execution_id,
            "patrol_batch_id": data.patrol_batch_id,
            "device_id": data.device_id,
            "check_item_version_id": data.check_item_version_id,
            "coverage_status": status,
            "coverage_basis": basis,
            "expected_entity_count": expected_count,
            "observed_entity_count": len(observed),
            "evaluated_entity_count": max(0, int(data.evaluated_entity_count)),
            "expected_entity_set_digest": expected_digest,
            "observed_entity_set_digest": observed_digest,
            "denominator_ref_json": denominator_ref,
            "missing_entity_sample_json": missing[:100],
            "unexpected_entity_sample_json": unexpected[:100],
            "reason_codes_json": list(dict.fromkeys(reasons)),
            "policy_snapshot_json": policy,
            "policy_digest": policy_digest,
            "evidence_refs_json": sorted(set(data.evidence_refs)),
            "decided_at": decided_at,
            "created_at": datetime.now(timezone.utc),
        }
        return CoverageDecision(
            id=decision_id,
            digest=digest,
            coverage_status=status,
            coverage_basis=basis,
            reason_codes=tuple(dict.fromkeys(reasons)),
            values=values,
        )


class EvidenceQualityRepository:
    """Persist immutable decisions and project their summaries onto execution."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def save_qualification(
        self,
        decision: EvidenceQualificationDecision,
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            execution = (
                db.query(CheckExecutionRecord)
                .filter(
                    CheckExecutionRecord.id
                    == decision.values["check_execution_id"]
                )
                .with_for_update()
                .first()
            )
            if execution is None:
                raise ValueError("check execution not found")
            existing = (
                db.query(EvidenceQualificationRecord)
                .filter(
                    EvidenceQualificationRecord.check_execution_id
                    == execution.id
                )
                .first()
            )
            if existing is not None:
                if str(existing.digest) != decision.digest:
                    raise ValueError(
                        "check execution already has a different evidence qualification"
                    )
                return existing.to_dict()
            row = EvidenceQualificationRecord(**decision.values)
            db.add(row)
            db.flush()
            execution.evidence_qualification_id = row.id
            execution.source_identity_status = row.source_identity_status
            execution.output_quality_status = row.output_quality_status
            execution.freshness_status = row.freshness_status
            execution.empty_result_classification = (
                row.empty_result_classification
            )
            execution.observation_min_at = row.observation_min_at
            execution.observation_max_at = row.observation_max_at
            execution.updated_at = datetime.now(timezone.utc)
            db.flush()
            return row.to_dict()

    def save_coverage(self, decision: CoverageDecision) -> dict[str, Any]:
        with self._session_scope() as db:
            execution = (
                db.query(CheckExecutionRecord)
                .filter(
                    CheckExecutionRecord.id
                    == decision.values["check_execution_id"]
                )
                .with_for_update()
                .first()
            )
            if execution is None:
                raise ValueError("check execution not found")
            existing = (
                db.query(CoverageDecisionRecord)
                .filter(
                    CoverageDecisionRecord.check_execution_id == execution.id
                )
                .first()
            )
            if existing is not None:
                if str(existing.digest) != decision.digest:
                    raise ValueError(
                        "check execution already has a different coverage decision"
                    )
                return existing.to_dict()
            row = CoverageDecisionRecord(**decision.values)
            db.add(row)
            db.flush()
            execution.coverage_decision_id = row.id
            execution.coverage_status = row.coverage_status
            execution.coverage_basis = row.coverage_basis
            execution.expected_entity_count = row.expected_entity_count
            execution.updated_at = datetime.now(timezone.utc)
            db.flush()
            return row.to_dict()
