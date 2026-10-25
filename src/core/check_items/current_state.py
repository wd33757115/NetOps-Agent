"""Auditable current-state evaluation and immutable Finding persistence."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.core.check_items.assessment_policy import validate_assessment_basis
from src.core.state.observation_applicability import APPLICABILITY_CONTEXT_KEY
from src.infrastructure.db.models import (
    CheckExecutionRecord,
    CurrentStateFindingRecord,
)
from src.infrastructure.db.postgres import get_db_session


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


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _normalized_observation(item: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(item.get("metadata") or item.get("metadata_json") or {})
    value = item.get("value") if "value" in item else item.get("value_json")
    return {
        **item,
        "id": item.get("id") or item.get("observation_id"),
        "field": item.get("field") or item.get("field_name"),
        "value": value,
        "command": item.get("command") or item.get("canonical_command"),
        "dimensions": dict(item.get("dimensions") or item.get("dimensions_json") or {}),
        "context": (
            item.get("context")
            if isinstance(item.get("context"), dict)
            else metadata.get(APPLICABILITY_CONTEXT_KEY)
        ),
        "metadata": metadata,
    }


def _baseline_key(item: dict[str, Any]) -> tuple[str, str, str, str, str]:
    dimensions = dict(item.get("dimensions") or {})
    dimensions.pop(APPLICABILITY_CONTEXT_KEY, None)
    return (
        str(item.get("device_id") or ""),
        str(item.get("entity_type") or ""),
        str(item.get("entity_key") or ""),
        str(item.get("field") or ""),
        json.dumps(
            dimensions,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )


def _attach_baselines(
    current: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach matching immutable baseline evidence without inventing identity."""

    latest: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for item in baseline:
        key = _baseline_key(item)
        selected = latest.get(key)
        if selected is None or str(item.get("observed_at") or "") >= str(
            selected.get("observed_at") or ""
        ):
            latest[key] = item
    enriched: list[dict[str, Any]] = []
    for item in current:
        context = dict(item.get("context") or {})
        prior = latest.get(_baseline_key(item))
        if prior is None:
            context["baseline_status"] = "missing"
        else:
            prior_context = dict(prior.get("context") or {})
            context.update(
                {
                    "baseline_status": "available",
                    "baseline_value": prior.get("value"),
                    "baseline_observation_id": prior.get("id"),
                    "baseline_snapshot_id": prior.get("snapshot_id"),
                    "baseline_unit": prior.get("unit"),
                    "baseline_context": prior_context,
                }
            )
        enriched.append({**item, "context": context})
    return enriched


@dataclass(frozen=True, slots=True)
class CurrentStateEvaluationInput:
    patrol_batch_id: str
    check_execution_id: str
    device_id: str
    check_item_id: str
    check_item_version_id: str
    implementation_version_id: str | None
    runtime_bundle_version_id: str
    runtime_bundle_digest: str
    observations: tuple[dict[str, Any], ...]
    rules: tuple[dict[str, Any], ...]
    runtime_assets: tuple[dict[str, Any], ...]
    assessed_at: datetime
    assessment_policy_snapshot: dict[str, Any] = field(default_factory=dict)
    baseline_observations: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class CurrentStateEvaluationResult:
    coverage: dict[str, Any]
    finding_values: tuple[dict[str, Any], ...]
    assessment_basis: dict[str, Any] = field(default_factory=dict)

    @property
    def finding_ids(self) -> tuple[str, ...]:
        return tuple(str(item["id"]) for item in self.finding_values)


class CurrentStateEvaluationService:
    """Run only rules frozen in the supplied runtime bundle."""

    @staticmethod
    def evaluate(data: CurrentStateEvaluationInput) -> CurrentStateEvaluationResult:
        from src.core.workflows.patrol_batch_service import evaluate_current_state

        current_assets: dict[str, set[str]] = {}
        for item in data.runtime_assets:
            if str(item.get("asset_type") or "") != "current_state_rule":
                continue
            version_id = str(item.get("asset_version_id") or "")
            observation_type = str(item.get("observation_type") or "")
            if version_id:
                current_assets.setdefault(version_id, set())
                if observation_type:
                    current_assets[version_id].add(observation_type)
        frozen_rules: list[dict[str, Any]] = []
        for raw_rule in data.rules:
            version_id = str(raw_rule.get("version_id") or "")
            if version_id not in current_assets:
                continue
            condition = dict(
                raw_rule.get("effective_condition")
                or raw_rule.get("condition")
                or {}
            )
            observation_types = sorted(current_assets[version_id])
            if observation_types:
                condition["observation_types"] = observation_types
            frozen_rules.append(
                {
                    **raw_rule,
                    "condition": condition,
                    "effective_condition": condition,
                }
            )
        observations = [
            _normalized_observation(item)
            for item in data.observations
            if str(item.get("device_id") or "") == data.device_id
        ]
        baselines = [
            _normalized_observation(item)
            for item in data.baseline_observations
            if str(item.get("device_id") or "") == data.device_id
        ]
        observations = _attach_baselines(observations, baselines)
        coverage = evaluate_current_state(
            observations,
            frozen_rules,
            rule_source="check_item_runtime_bundle",
            rule_bundle_digest=data.runtime_bundle_digest,
            runtime_bundle_version_id=data.runtime_bundle_version_id,
            assessment_policy_snapshot=data.assessment_policy_snapshot,
        )
        assessment_basis = dict(coverage.get("assessment_basis") or {})
        if not validate_assessment_basis(assessment_basis):
            raise ValueError("current-state aggregate AssessmentBasis is invalid")
        values: list[dict[str, Any]] = []
        for finding in coverage["findings"]:
            observation_id = str(finding.get("observation_id") or "")
            rule_version_id = str(finding.get("rule_version_id") or "")
            rule_digest = str(finding.get("rule_digest") or "")
            if not observation_id or not rule_version_id or not rule_digest:
                raise ValueError(
                    "frozen current-state Finding lacks observation or rule version evidence"
                )
            finding_basis = dict(finding.get("assessment_basis") or {})
            if not validate_assessment_basis(finding_basis):
                raise ValueError("current-state Finding AssessmentBasis is invalid")
            finding_basis_digest = str(finding_basis["digest"])
            evidence_refs = sorted(
                {
                    str(value)
                    for value in (
                        observation_id,
                        finding.get("snapshot_id"),
                        finding.get("evidence_id"),
                        finding.get("parser_template_id"),
                        finding.get("observation_contract_id"),
                        rule_version_id,
                        data.runtime_bundle_version_id,
                        data.check_execution_id,
                        f"assessment-basis:{finding_basis_digest}",
                    )
                    if str(value or "").strip()
                }
            )
            evidence_digest = _digest(
                {
                    "evidence_refs": evidence_refs,
                    "assessment_basis_digest": finding_basis_digest,
                }
            )
            identity_payload = {
                "batch_id": data.patrol_batch_id,
                "execution_id": data.check_execution_id,
                "observation_id": observation_id,
                "rule_version_id": rule_version_id,
                "dimensions": dict(finding.get("dimensions") or {}),
                "runtime_bundle_digest": data.runtime_bundle_digest,
                "assessment_basis_digest": finding_basis_digest,
            }
            idempotency_key = _digest(identity_payload)
            finding_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/current-state-finding/{idempotency_key}",
                )
            )
            actual = finding.get("value")
            reference = finding.get("reference_value")
            observed_at = _utc(finding.get("observed_at"))
            values.append(
                {
                    "id": finding_id,
                    "idempotency_key": idempotency_key,
                    "patrol_batch_id": data.patrol_batch_id,
                    "check_execution_id": data.check_execution_id,
                    "device_id": data.device_id,
                    "check_item_version_id": data.check_item_version_id,
                    "implementation_version_id": data.implementation_version_id,
                    "entity_type": str(finding.get("entity_type") or ""),
                    "entity_key": str(finding.get("entity_key") or ""),
                    "dimensions_json": dict(finding.get("dimensions") or {}),
                    "observation_id": observation_id,
                    "observation_type": finding.get("observation_type"),
                    "actual_value_json": actual,
                    "actual_value_text": None if actual is None else str(actual),
                    "actual_value_number": _number(actual),
                    "unit": finding.get("unit"),
                    "reference_value_json": reference,
                    "reference_value_text": (
                        None if reference is None else str(reference)
                    ),
                    "reference_value_number": _number(reference),
                    "reference_source": (
                        str(finding.get("reference_source") or "") or None
                    ),
                    "severity": str(finding.get("severity") or "warning"),
                    "event_type": str(finding.get("event_type") or ""),
                    "event_rule_version_id": rule_version_id,
                    "rule_digest": rule_digest,
                    "runtime_bundle_version_id": data.runtime_bundle_version_id,
                    "runtime_bundle_digest": data.runtime_bundle_digest,
                    "observed_at": observed_at,
                    "assessed_at": data.assessed_at,
                    "evidence_refs_json": evidence_refs,
                    "evidence_digest": evidence_digest,
                    "assessment_basis_json": finding_basis,
                    "assessment_basis_digest": finding_basis_digest,
                    "created_at": datetime.now(timezone.utc),
                }
            )
        return CurrentStateEvaluationResult(
            coverage=coverage,
            finding_values=tuple(values),
            assessment_basis=assessment_basis,
        )


class CurrentStateFindingRepository:
    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def save(
        self,
        result: CurrentStateEvaluationResult,
        *,
        check_execution_id: str,
    ) -> list[dict[str, Any]]:
        with self._session_scope() as db:
            execution = db.get(CheckExecutionRecord, check_execution_id)
            if execution is None:
                raise ValueError("check execution not found")
            saved: list[CurrentStateFindingRecord] = []
            for values in result.finding_values:
                existing = (
                    db.query(CurrentStateFindingRecord)
                    .filter(
                        CurrentStateFindingRecord.idempotency_key
                        == values["idempotency_key"]
                    )
                    .first()
                )
                if existing is None:
                    existing = CurrentStateFindingRecord(**values)
                    db.add(existing)
                    db.flush()
                elif str(existing.evidence_digest) != str(values["evidence_digest"]):
                    raise ValueError(
                        "current-state Finding idempotency key has different evidence"
                    )
                saved.append(existing)
            execution.current_finding_count = len(saved)
            execution.updated_at = datetime.now(timezone.utc)
            db.flush()
            return [item.to_dict() for item in saved]
