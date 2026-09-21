"""Shared contracts for patrol report purpose and raw evidence identity."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from src.core.check_items.source_qualification import (
    ATTESTED_OFFLINE_CURRENT,
    source_qualification_is_valid,
)

PRODUCTION_CURRENT = "production_current"
OFFLINE_HISTORICAL = "offline_historical"
SHADOW_VALIDATION = "shadow_validation"
PARSER_VALIDATION = "parser_validation"
RULE_ASSET_REPLAY = "rule_asset_replay"

REPORT_PURPOSES = frozenset(
    {
        PRODUCTION_CURRENT,
        OFFLINE_HISTORICAL,
        SHADOW_VALIDATION,
        PARSER_VALIDATION,
        RULE_ASSET_REPLAY,
    }
)

CURRENT_STATE_AUTHORITATIVE_PURPOSES = frozenset({PRODUCTION_CURRENT})
REPORT_AUTHORITY_CONTEXT_SCHEMA_VERSION = "report-authority-context/v3"
REPORT_CONCLUSION_PERMISSION_SCHEMA_VERSION = "conclusion-permission/v2"
REPORT_CONCLUSION_BLOCKER_REASON_CODES = frozenset(
    {
        "terminal_truth_not_reconciled",
        "authority_context_invalid",
        "runtime_bundle_digest_mismatch",
        "health_scope_empty",
        "all_health_targets_not_applicable",
        "health_target_pair_at_risk",
        "health_target_pair_unable_to_determine",
        "target_pair_unable_to_determine",
        "unresolved_in_scope_risk_case",
        "scope_contract_missing",
        "scope_not_reconciled",
        "claim_boundary_limited",
        "conclusion_permission_digest_mismatch",
    }
)
_TERMINAL_EXECUTION_STATUSES = frozenset(
    {
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
)
_EXPLICIT_NO_EVIDENCE_STATUSES = frozenset(
    {
        "not_applicable",
        "unsupported",
        "blocked_asset_missing",
        "collection_failed",
        "cancelled",
    }
)
_HEALTH_ASSESSMENT_STATUSES = frozenset(
    {
        "healthy",
        "at_risk",
        "unable_to_determine",
        "not_applicable",
    }
)
_ONLINE_PRODUCTION_TARGET_TYPES = frozenset(
    {
        "devices",
        "dynamic_group",
        "filters",
        "group",
        "selected",
    }
)
_OFFLINE_TARGET_TYPES = frozenset(
    {
        "offline_import",
        "rule_asset_source",
    }
)
_SHADOW_TARGET_TYPES = frozenset(
    {
        "rollout_stage",
    }
)


def normalize_report_purpose(
    value: str | None,
    *,
    default: str = SHADOW_VALIDATION,
) -> str:
    """Normalize a report purpose without promoting unknown data to production."""

    normalized_default = str(default or "").strip().lower()
    if normalized_default not in REPORT_PURPOSES:
        raise ValueError(f"invalid default report purpose: {default}")

    normalized = str(value or "").strip().lower().replace("-", "_")
    if not normalized:
        return normalized_default
    if normalized not in REPORT_PURPOSES:
        raise ValueError(f"invalid report purpose: {value}")
    return normalized


def report_purpose_for_patrol_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower().replace("-", "_")
    if normalized == "production":
        return PRODUCTION_CURRENT
    if normalized == PARSER_VALIDATION:
        return PARSER_VALIDATION
    return SHADOW_VALIDATION


def derive_report_source_kind(
    *,
    patrol_mode: str | None,
    target_type: str | None,
) -> str:
    """Classify the evidence source without consulting report_purpose."""

    normalized_mode = str(patrol_mode or "").strip().lower().replace("-", "_")
    normalized_target_type = str(target_type or "").strip().lower().replace("-", "_")
    if normalized_target_type in _OFFLINE_TARGET_TYPES:
        return "offline_import"
    if normalized_target_type in _SHADOW_TARGET_TYPES:
        return "shadow_validation"
    if normalized_target_type == "parser_validation" or normalized_mode == PARSER_VALIDATION:
        return "parser_validation"
    if normalized_target_type == "rule_asset_replay" or normalized_mode == RULE_ASSET_REPLAY:
        return "rule_asset_replay"
    if normalized_mode in {"shadow", SHADOW_VALIDATION}:
        return "shadow_validation"
    if (
        normalized_mode == "production"
        and normalized_target_type in _ONLINE_PRODUCTION_TARGET_TYPES
    ):
        return "online_production"
    return "unclassified"


def _purpose_is_consistent_with_source(
    *,
    report_purpose: str,
    source_kind: str,
) -> bool:
    allowed = {
        "online_production": {
            PRODUCTION_CURRENT,
            SHADOW_VALIDATION,
        },
        "offline_import": {
            OFFLINE_HISTORICAL,
            PARSER_VALIDATION,
            RULE_ASSET_REPLAY,
        },
        ATTESTED_OFFLINE_CURRENT: {
            PRODUCTION_CURRENT,
            SHADOW_VALIDATION,
        },
        "shadow_validation": {SHADOW_VALIDATION},
        "parser_validation": {PARSER_VALIDATION},
        "rule_asset_replay": {RULE_ASSET_REPLAY},
        "unclassified": {SHADOW_VALIDATION},
    }
    return report_purpose in allowed.get(source_kind, set())


def report_purpose_is_consistent_with_source(
    *,
    report_purpose: str,
    patrol_mode: str | None,
    target_type: str | None,
) -> bool:
    """Return whether a requested purpose is allowed for source facts."""

    return _purpose_is_consistent_with_source(
        report_purpose=normalize_report_purpose(report_purpose),
        source_kind=derive_report_source_kind(
            patrol_mode=patrol_mode,
            target_type=target_type,
        ),
    )


def build_report_authority_context(
    *,
    report_purpose: str,
    patrol_mode: str | None,
    launch_mode: str | None,
    workflow_name: str | None,
    target_type: str | None,
    readiness_ready: bool,
    target_manifest_digest: str | None,
    inventory_snapshot_digest: str | None,
    runtime_manifest_digest: str | None,
    observation_min_at: str | None = None,
    observation_max_at: str | None = None,
    source_qualification: Mapping[str, Any] | None = None,
    scope_contract_digest: str | None = None,
    intended_target_matrix_digest: str | None = None,
    patrol_batch_id: str | None = None,
) -> dict[str, Any]:
    """Freeze why a report may, or may not, describe current production state."""

    purpose = normalize_report_purpose(report_purpose)
    normalized_mode = str(patrol_mode or "").strip().lower().replace("-", "_")
    normalized_target_type = str(target_type or "").strip().lower().replace("-", "_")
    base_source_kind = derive_report_source_kind(
        patrol_mode=normalized_mode,
        target_type=normalized_target_type,
    )
    qualification = dict(source_qualification or {})
    qualification_valid = bool(
        base_source_kind == "offline_import"
        and source_qualification_is_valid(
            qualification,
            expected_patrol_batch_id=str(patrol_batch_id or "") or None,
            expected_scope_contract_digest=str(scope_contract_digest or "") or None,
            expected_intended_target_matrix_digest=str(intended_target_matrix_digest or "") or None,
        )
    )
    source_kind = ATTESTED_OFFLINE_CURRENT if qualification_valid else base_source_kind
    purpose_source_consistent = _purpose_is_consistent_with_source(
        report_purpose=purpose,
        source_kind=source_kind,
    )
    reason_codes: list[str] = []
    if not purpose_source_consistent:
        reason_codes.append("report_purpose_source_mismatch")
    if purpose != PRODUCTION_CURRENT:
        reason_codes.append("report_purpose_not_current")
    else:
        if normalized_mode != "production":
            reason_codes.append("patrol_mode_not_production")
        if source_kind not in {
            "online_production",
            ATTESTED_OFFLINE_CURRENT,
        }:
            reason_codes.append("source_type_not_current_authority")
        if base_source_kind == "offline_import" and not qualification_valid:
            reason_codes.append("source_qualification_missing_or_invalid")
        if not readiness_ready:
            reason_codes.append("report_facts_not_final")
        if len(str(target_manifest_digest or "")) != 64:
            reason_codes.append("target_manifest_not_frozen")
        if len(str(inventory_snapshot_digest or "")) != 64:
            reason_codes.append("inventory_snapshot_not_frozen")
        if len(str(runtime_manifest_digest or "")) != 64:
            reason_codes.append("runtime_manifest_not_frozen")

    context = {
        "schema_version": REPORT_AUTHORITY_CONTEXT_SCHEMA_VERSION,
        "report_purpose": purpose,
        "patrol_batch_id": str(patrol_batch_id or "") or None,
        "source_kind": source_kind,
        "base_source_kind": base_source_kind,
        "source_kind_basis": {
            "patrol_mode": normalized_mode or None,
            "target_type": normalized_target_type or None,
            "source_qualification_id": (
                qualification.get("qualification_id") if qualification_valid else None
            ),
        },
        "purpose_source_consistent": purpose_source_consistent,
        "patrol_mode": normalized_mode or None,
        "launch_mode": str(launch_mode or "").strip().lower() or None,
        "workflow_name": str(workflow_name or "").strip() or None,
        "target_type": normalized_target_type or None,
        "facts_ready": bool(readiness_ready),
        "observation_min_at": observation_min_at,
        "observation_max_at": observation_max_at,
        "source_qualification": (qualification if qualification_valid else None),
        "source_qualification_valid": qualification_valid,
        "source_qualification_digest": (
            str(qualification.get("digest") or "") if qualification_valid else ""
        ),
        "scope_contract_digest": str(scope_contract_digest or ""),
        "intended_target_matrix_digest": str(intended_target_matrix_digest or ""),
        "target_manifest_digest": str(target_manifest_digest or ""),
        "inventory_snapshot_digest": str(inventory_snapshot_digest or ""),
        "runtime_manifest_digest": str(runtime_manifest_digest or ""),
        "current_state_eligible": not reason_codes,
        "reason_codes": reason_codes,
    }
    context["digest"] = hashlib.sha256(
        json.dumps(
            context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return context


def report_authority_context_is_valid(
    value: Mapping[str, Any] | None,
    *,
    expected_purpose: str,
) -> bool:
    """Verify the immutable authority context before granting current authority."""

    context = dict(value or {})
    digest = str(context.get("digest", "") or "")
    if (
        len(digest) != 64
        or context.get("schema_version") != REPORT_AUTHORITY_CONTEXT_SCHEMA_VERSION
        or context.get("report_purpose") != normalize_report_purpose(expected_purpose)
    ):
        return False
    expected = build_report_authority_context(
        report_purpose=str(context.get("report_purpose") or ""),
        patrol_mode=context.get("patrol_mode"),
        launch_mode=context.get("launch_mode"),
        workflow_name=context.get("workflow_name"),
        target_type=context.get("target_type"),
        readiness_ready=bool(context.get("facts_ready")),
        target_manifest_digest=context.get("target_manifest_digest"),
        inventory_snapshot_digest=context.get("inventory_snapshot_digest"),
        runtime_manifest_digest=context.get("runtime_manifest_digest"),
        observation_min_at=context.get("observation_min_at"),
        observation_max_at=context.get("observation_max_at"),
        source_qualification=context.get("source_qualification"),
        scope_contract_digest=context.get("scope_contract_digest"),
        intended_target_matrix_digest=context.get("intended_target_matrix_digest"),
        patrol_batch_id=context.get("patrol_batch_id"),
    )
    return context == expected


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _pair(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("device_id") or ""),
        str(row.get("check_item_version_id") or ""),
    )


def _pair_ref(pair: tuple[str, str]) -> dict[str, str]:
    return {
        "device_id": pair[0],
        "check_item_version_id": pair[1],
    }


def _normalized_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def build_report_conclusion_permission(
    *,
    report_purpose: str,
    report_authority_context: Mapping[str, Any] | None,
    target_manifest_digest: str | None,
    inventory_snapshot_digest: str | None,
    runtime_manifest_digest: str | None,
    readiness: Mapping[str, Any] | None,
    target_pairs: Sequence[Mapping[str, Any]],
    executions: Sequence[Mapping[str, Any]],
    assessments: Sequence[Mapping[str, Any]],
    evidence_qualifications: Sequence[Mapping[str, Any]],
    coverage_decisions: Sequence[Mapping[str, Any]],
    evidence_index: Sequence[Mapping[str, Any]],
    check_items: Mapping[str, Mapping[str, Any]],
    risks: Sequence[Mapping[str, Any]],
    scope_contract: Mapping[str, Any] | None = None,
    intended_target_pairs: Sequence[Mapping[str, Any]] = (),
    scope_decisions: Sequence[Mapping[str, Any]] = (),
    unmatched_scope_device_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Decide which current-network conclusions frozen facts may express."""

    purpose = normalize_report_purpose(report_purpose)
    authority_context = dict(report_authority_context or {})
    readiness_facts = dict(readiness or {})
    target_rows = [dict(item) for item in target_pairs]
    execution_rows = [dict(item) for item in executions]
    assessment_rows = [dict(item) for item in assessments]
    qualification_rows = [dict(item) for item in evidence_qualifications]
    coverage_rows = [dict(item) for item in coverage_decisions]
    evidence_rows = [dict(item) for item in evidence_index]
    risk_rows = [dict(item) for item in risks]
    check_item_rows = {str(key): dict(value) for key, value in check_items.items()}
    scope_contract_facts = dict(scope_contract or {})
    intended_scope_rows = [dict(item) for item in intended_target_pairs]
    scope_decision_rows = [dict(item) for item in scope_decisions]
    unmatched_scope_devices = sorted(
        {str(item) for item in unmatched_scope_device_ids if str(item)}
    )

    expected_pairs = [_pair(item) for item in target_rows]
    execution_pairs = [_pair(item) for item in execution_rows]
    assessment_pairs = [_pair(item) for item in assessment_rows]
    expected_counts = Counter(expected_pairs)
    execution_counts = Counter(execution_pairs)
    assessment_counts = Counter(assessment_pairs)
    expected_pair_set = set(expected_pairs)
    intended_pairs = [_pair(item) for item in intended_scope_rows]
    intended_counts = Counter(intended_pairs)
    intended_pair_set = set(intended_pairs)
    scope_decision_pairs = [_pair(item) for item in scope_decision_rows]
    scope_decision_counts = Counter(scope_decision_pairs)
    scope_decisions_by_pair = {
        _pair(item): item for item in scope_decision_rows if scope_decision_counts[_pair(item)] == 1
    }
    invalid_intended_pairs = sorted(pair for pair in intended_pair_set if not all(pair))
    duplicate_intended_pairs = sorted(pair for pair, count in intended_counts.items() if count != 1)
    duplicate_scope_decision_pairs = sorted(
        pair for pair, count in scope_decision_counts.items() if not all(pair) or count != 1
    )
    missing_scope_decision_pairs = sorted(
        pair for pair in intended_pair_set | expected_pair_set if scope_decision_counts[pair] != 1
    )
    invalid_scope_decision_pairs = sorted(
        pair
        for pair, item in scope_decisions_by_pair.items()
        if str(item.get("decision") or "")
        not in {
            "included",
            "approved_exclusion",
            "not_applicable",
            "unresolved",
        }
    )
    included_scope_pairs = {
        pair
        for pair, item in scope_decisions_by_pair.items()
        if str(item.get("decision") or "") == "included"
    }
    unaccounted_selected_pairs = sorted(
        pair for pair in expected_pair_set if pair not in included_scope_pairs
    )
    unresolved_scope_pairs = sorted(
        pair
        for pair, item in scope_decisions_by_pair.items()
        if str(item.get("decision") or "") == "unresolved"
    )
    scope_contract_present = bool(
        scope_contract_facts and str(scope_contract_facts.get("scope_contract_id") or "")
    )
    claim_boundary = str(scope_contract_facts.get("claim_boundary") or "unverified")
    scope_reconciled = bool(
        scope_contract_present
        and intended_pair_set
        and not any(
            (
                invalid_intended_pairs,
                duplicate_intended_pairs,
                duplicate_scope_decision_pairs,
                missing_scope_decision_pairs,
                invalid_scope_decision_pairs,
                unaccounted_selected_pairs,
                unresolved_scope_pairs,
                unmatched_scope_devices,
            )
        )
    )
    overall_claim_scope_allowed = bool(
        scope_reconciled and claim_boundary in {"network_scope", "selected_network_scope"}
    )

    invalid_expected_pairs = sorted(pair for pair in expected_pair_set if not all(pair))
    duplicate_expected_pairs = sorted(pair for pair, count in expected_counts.items() if count != 1)
    missing_execution_pairs = sorted(
        pair for pair in expected_pair_set if execution_counts[pair] != 1
    )
    unplanned_execution_pairs = sorted(
        pair for pair in execution_counts if pair not in expected_pair_set
    )
    duplicate_execution_pairs = sorted(
        pair for pair, count in execution_counts.items() if count > 1
    )
    missing_assessment_pairs = sorted(
        pair for pair in expected_pair_set if assessment_counts[pair] != 1
    )
    unplanned_assessment_pairs = sorted(
        pair for pair in assessment_counts if pair not in expected_pair_set
    )
    duplicate_assessment_pairs = sorted(
        pair for pair, count in assessment_counts.items() if count > 1
    )

    executions_by_id = {
        str(item.get("id") or ""): item for item in execution_rows if str(item.get("id") or "")
    }
    executions_by_pair = {
        _pair(item): item for item in execution_rows if execution_counts[_pair(item)] == 1
    }
    qualification_ids = [str(item.get("id") or "") for item in qualification_rows]
    coverage_ids = [str(item.get("id") or "") for item in coverage_rows]
    qualification_counts = Counter(qualification_ids)
    coverage_counts = Counter(coverage_ids)
    qualifications_by_id = {
        str(item.get("id") or ""): item
        for item in qualification_rows
        if (str(item.get("id") or "") and qualification_counts[str(item.get("id") or "")] == 1)
    }
    coverage_by_id = {
        str(item.get("id") or ""): item
        for item in coverage_rows
        if (str(item.get("id") or "") and coverage_counts[str(item.get("id") or "")] == 1)
    }
    snapshot_ids = [str(item.get("snapshot_id") or "") for item in evidence_rows]
    snapshot_counts = Counter(snapshot_ids)
    snapshots_by_id = {
        str(item.get("snapshot_id") or ""): item
        for item in evidence_rows
        if (
            str(item.get("snapshot_id") or "")
            and snapshot_counts[str(item.get("snapshot_id") or "")] == 1
        )
    }
    duplicate_qualification_ids = sorted(
        item_id for item_id, count in qualification_counts.items() if not item_id or count != 1
    )
    duplicate_coverage_ids = sorted(
        item_id for item_id, count in coverage_counts.items() if not item_id or count != 1
    )
    duplicate_snapshot_ids = sorted(
        item_id for item_id, count in snapshot_counts.items() if not item_id or count != 1
    )
    nonterminal_execution_ids = sorted(
        str(item.get("id") or "")
        for item in execution_rows
        if str(item.get("execution_status") or "") not in _TERMINAL_EXECUTION_STATUSES
    )
    assessment_execution_mismatches = sorted(
        str(item.get("id") or item.get("execution_id") or "")
        for item in assessment_rows
        if (
            str(item.get("execution_id") or "") not in executions_by_id
            or _pair(
                executions_by_id.get(
                    str(item.get("execution_id") or ""),
                    {},
                )
            )
            != _pair(item)
        )
    )

    evidence_missing_pairs: list[tuple[str, str]] = []
    qualification_reference_mismatches: list[str] = []
    referenced_qualification_ids: set[str] = set()
    coverage_reference_mismatches: list[str] = []
    referenced_coverage_ids: set[str] = set()
    evidence_snapshot_mismatches: list[str] = []
    for item in execution_rows:
        pair = _pair(item)
        execution_id = str(item.get("id") or "")
        status = str(item.get("execution_status") or "")
        reasons = [str(code) for code in item.get("reason_codes_json") or [] if str(code)]
        qualification_id = str(item.get("evidence_qualification_id") or "")
        if qualification_id:
            referenced_qualification_ids.add(qualification_id)
            qualification = qualifications_by_id.get(qualification_id)
            if (
                qualification is None
                or str(qualification.get("check_execution_id") or "") != execution_id
                or _pair(qualification) != pair
            ):
                qualification_reference_mismatches.append(execution_id or qualification_id)
            else:
                qualified_snapshot_ids = {
                    str(snapshot_id)
                    for snapshot_id in (qualification.get("snapshot_ids_json") or [])
                    if str(snapshot_id)
                }
                execution_snapshot_ids = {
                    str(reference)
                    for reference in (item.get("evidence_refs_json") or [])
                    if str(reference) in snapshots_by_id
                }
                if (
                    not qualified_snapshot_ids
                    or execution_snapshot_ids != qualified_snapshot_ids
                    or any(
                        snapshot_id not in snapshots_by_id
                        or str(
                            snapshots_by_id[snapshot_id].get("inventory_device_id")
                            or snapshots_by_id[snapshot_id].get("device_id")
                            or ""
                        )
                        != pair[0]
                        for snapshot_id in qualified_snapshot_ids
                    )
                ):
                    evidence_snapshot_mismatches.append(execution_id or qualification_id)
        if (
            pair in expected_pair_set
            and not qualification_id
            and status not in _EXPLICIT_NO_EVIDENCE_STATUSES
            and not reasons
        ):
            evidence_missing_pairs.append(pair)
        coverage_id = str(item.get("coverage_decision_id") or "")
        if coverage_id:
            referenced_coverage_ids.add(coverage_id)
            coverage = coverage_by_id.get(coverage_id)
            if (
                coverage is None
                or str(coverage.get("check_execution_id") or "") != execution_id
                or _pair(coverage) != pair
            ):
                coverage_reference_mismatches.append(execution_id or coverage_id)

    unreferenced_qualification_ids = sorted(
        set(qualifications_by_id) - referenced_qualification_ids
    )
    unreferenced_coverage_ids = sorted(set(coverage_by_id) - referenced_coverage_ids)

    assessment_by_pair = {
        _pair(item): item for item in assessment_rows if assessment_counts[_pair(item)] == 1
    }
    health_pairs = sorted(
        pair
        for pair in expected_pair_set
        if str(
            check_item_rows.get(pair[1], {}).get(
                "assessment_mode",
                "",
            )
        )
        == "health"
    )
    invalid_health_status_pairs: list[tuple[str, str]] = []
    coverage_missing_pairs: list[tuple[str, str]] = []
    not_applicable_evidence_missing_pairs: list[tuple[str, str]] = []
    health_status_counts: Counter[str] = Counter()
    for pair in health_pairs:
        assessment = assessment_by_pair.get(pair, {})
        execution = executions_by_pair.get(pair, {})
        status = str(assessment.get("assessment_status") or "unable_to_determine")
        health_status_counts[status] += 1
        if status not in _HEALTH_ASSESSMENT_STATUSES:
            invalid_health_status_pairs.append(pair)
        reasons = [
            str(code)
            for code in assessment.get(
                "blocking_reason_codes_json",
                [],
            )
            if str(code)
        ]
        assessment_qualification_id = str(assessment.get("evidence_qualification_id") or "")
        execution_qualification_id = str(execution.get("evidence_qualification_id") or "")
        if assessment_qualification_id != execution_qualification_id or (
            assessment_qualification_id and assessment_qualification_id not in qualifications_by_id
        ):
            qualification_reference_mismatches.append(
                str(assessment.get("id") or assessment.get("execution_id") or "")
            )
        assessment_coverage_id = str(assessment.get("coverage_decision_id") or "")
        execution_coverage_id = str(execution.get("coverage_decision_id") or "")
        if assessment_coverage_id != execution_coverage_id or (
            assessment_coverage_id and assessment_coverage_id not in coverage_by_id
        ):
            coverage_reference_mismatches.append(
                str(assessment.get("id") or assessment.get("execution_id") or "")
            )
        if not assessment_coverage_id and status != "not_applicable" and not reasons:
            coverage_missing_pairs.append(pair)
        if status == "not_applicable":
            coverage = coverage_by_id.get(
                assessment_coverage_id,
                {},
            )
            execution_reasons = {
                str(code)
                for code in execution.get(
                    "reason_codes_json",
                    [],
                )
                if str(code)
            }
            applicability = check_item_rows.get(pair[1], {}).get("applicability")
            if (
                str(execution.get("execution_status") or "") != "not_applicable"
                or not execution_reasons
                or not assessment_coverage_id
                or str(coverage.get("coverage_status") or "") != "not_applicable"
                or not {
                    str(code)
                    for code in coverage.get(
                        "reason_codes_json",
                        [],
                    )
                    if str(code)
                }
                or not isinstance(
                    coverage.get("denominator_ref_json"),
                    Mapping,
                )
                or not coverage.get("denominator_ref_json")
                or not isinstance(applicability, Mapping)
                or not applicability
            ):
                not_applicable_evidence_missing_pairs.append(pair)

    all_target_status_counts = Counter(
        str(
            assessment_by_pair.get(pair, {}).get(
                "assessment_status",
                "unable_to_determine",
            )
            or "unable_to_determine"
        )
        for pair in expected_pair_set
    )
    informational_count = all_target_status_counts["informational"]
    target_unable_count = all_target_status_counts["unable_to_determine"]

    terminal_truth_reconciled = not any(
        (
            invalid_expected_pairs,
            duplicate_expected_pairs,
            missing_execution_pairs,
            unplanned_execution_pairs,
            duplicate_execution_pairs,
            missing_assessment_pairs,
            unplanned_assessment_pairs,
            duplicate_assessment_pairs,
            nonterminal_execution_ids,
            assessment_execution_mismatches,
            evidence_missing_pairs,
            coverage_missing_pairs,
            not_applicable_evidence_missing_pairs,
            invalid_health_status_pairs,
            duplicate_qualification_ids,
            duplicate_coverage_ids,
            duplicate_snapshot_ids,
            qualification_reference_mismatches,
            coverage_reference_mismatches,
            evidence_snapshot_mismatches,
            unreferenced_qualification_ids,
            unreferenced_coverage_ids,
        )
    )
    frozen_target_matrix_complete = bool(expected_pair_set) and not any(
        (
            invalid_expected_pairs,
            duplicate_expected_pairs,
            missing_execution_pairs,
            unplanned_execution_pairs,
            missing_assessment_pairs,
            unplanned_assessment_pairs,
        )
    )

    authority_context_valid = bool(
        purpose == PRODUCTION_CURRENT
        and report_authority_context_is_valid(
            authority_context,
            expected_purpose=purpose,
        )
        and authority_context.get("current_state_eligible")
        and str(authority_context.get("target_manifest_digest") or "")
        == str(target_manifest_digest or "")
        and str(authority_context.get("inventory_snapshot_digest") or "")
        == str(inventory_snapshot_digest or "")
        and str(authority_context.get("runtime_manifest_digest") or "")
        == str(runtime_manifest_digest or "")
    )
    runtime_reason_codes = {
        str(code)
        for code in readiness_facts.get("reason_codes") or []
        if str(code).startswith("runtime_manifest")
    }
    runtime_bundle_digest_matched = bool(
        len(str(runtime_manifest_digest or "")) == 64
        and str(authority_context.get("runtime_manifest_digest") or "")
        == str(runtime_manifest_digest or "")
        and not runtime_reason_codes
    )
    current_conclusion_ready = bool(
        authority_context_valid
        and frozen_target_matrix_complete
        and runtime_bundle_digest_matched
        and terminal_truth_reconciled
    )

    check_item_id_by_version = {
        version_id: str(item.get("check_item_id") or "")
        for version_id, item in check_item_rows.items()
    }
    health_pair_by_scope = {
        (
            pair[0],
            check_item_id_by_version.get(pair[1], ""),
        ): pair
        for pair in health_pairs
    }
    unresolved_risk_ids: list[str] = []
    unresolved_evaluated_risk_ids: list[str] = []
    for risk in risk_rows:
        status = str(risk.get("status") or "")
        verification = str(risk.get("verification_status") or "")
        if status == "resolved" and verification == "resolved":
            continue
        pair = health_pair_by_scope.get(
            (
                str(risk.get("device_id") or ""),
                str(risk.get("check_item_id") or ""),
            )
        )
        if pair is None:
            continue
        risk_id = str(risk.get("id") or "")
        if risk_id:
            unresolved_risk_ids.append(risk_id)
        if (
            str(
                assessment_by_pair.get(pair, {}).get(
                    "assessment_status",
                    "",
                )
            )
            in {"healthy", "at_risk"}
            and risk_id
        ):
            unresolved_evaluated_risk_ids.append(risk_id)

    health_target_pair_count = len(health_pairs)
    healthy_count = health_status_counts["healthy"]
    at_risk_count = health_status_counts["at_risk"]
    health_unable_count = health_status_counts["unable_to_determine"]
    not_applicable_count = health_status_counts["not_applicable"]
    evaluated_count = healthy_count + at_risk_count
    evaluated_scope_no_abnormality_allowed = bool(
        current_conclusion_ready
        and evaluated_count > 0
        and at_risk_count == 0
        and healthy_count == evaluated_count
        and not unresolved_evaluated_risk_ids
    )
    overall_healthy_allowed = bool(
        current_conclusion_ready
        and overall_claim_scope_allowed
        and health_target_pair_count > 0
        and healthy_count > 0
        and healthy_count + not_applicable_count == health_target_pair_count
        and target_unable_count == 0
        and at_risk_count == 0
        and not unresolved_risk_ids
    )

    blocker_reason_codes: list[str] = []
    if not terminal_truth_reconciled:
        blocker_reason_codes.append("terminal_truth_not_reconciled")
    if not authority_context_valid:
        blocker_reason_codes.append("authority_context_invalid")
    if not runtime_bundle_digest_matched:
        blocker_reason_codes.append("runtime_bundle_digest_mismatch")
    if health_target_pair_count == 0:
        blocker_reason_codes.append("health_scope_empty")
    if health_target_pair_count > 0 and not_applicable_count == health_target_pair_count:
        blocker_reason_codes.append("all_health_targets_not_applicable")
    if at_risk_count:
        blocker_reason_codes.append("health_target_pair_at_risk")
    if health_unable_count:
        blocker_reason_codes.append("health_target_pair_unable_to_determine")
    if target_unable_count:
        blocker_reason_codes.append("target_pair_unable_to_determine")
    if unresolved_risk_ids:
        blocker_reason_codes.append("unresolved_in_scope_risk_case")
    if not scope_contract_present:
        blocker_reason_codes.append("scope_contract_missing")
    elif not scope_reconciled:
        blocker_reason_codes.append("scope_not_reconciled")
    if scope_reconciled and not overall_claim_scope_allowed:
        blocker_reason_codes.append("claim_boundary_limited")

    blocker_refs = {
        "invalid_expected_pairs": [_pair_ref(pair) for pair in invalid_expected_pairs],
        "duplicate_expected_pairs": [_pair_ref(pair) for pair in duplicate_expected_pairs],
        "missing_execution_pairs": [_pair_ref(pair) for pair in missing_execution_pairs],
        "unplanned_execution_pairs": [_pair_ref(pair) for pair in unplanned_execution_pairs],
        "duplicate_execution_pairs": [_pair_ref(pair) for pair in duplicate_execution_pairs],
        "missing_assessment_pairs": [_pair_ref(pair) for pair in missing_assessment_pairs],
        "unplanned_assessment_pairs": [_pair_ref(pair) for pair in unplanned_assessment_pairs],
        "duplicate_assessment_pairs": [_pair_ref(pair) for pair in duplicate_assessment_pairs],
        "nonterminal_execution_ids": nonterminal_execution_ids,
        "assessment_execution_mismatches": (assessment_execution_mismatches),
        "evidence_missing_pairs": [_pair_ref(pair) for pair in sorted(evidence_missing_pairs)],
        "coverage_missing_pairs": [_pair_ref(pair) for pair in sorted(coverage_missing_pairs)],
        "not_applicable_evidence_missing_pairs": [
            _pair_ref(pair) for pair in sorted(not_applicable_evidence_missing_pairs)
        ],
        "invalid_health_status_pairs": [
            _pair_ref(pair) for pair in sorted(invalid_health_status_pairs)
        ],
        "duplicate_qualification_ids": (duplicate_qualification_ids),
        "duplicate_coverage_ids": duplicate_coverage_ids,
        "duplicate_snapshot_ids": duplicate_snapshot_ids,
        "qualification_reference_mismatches": sorted(set(qualification_reference_mismatches)),
        "coverage_reference_mismatches": sorted(set(coverage_reference_mismatches)),
        "evidence_snapshot_mismatches": sorted(set(evidence_snapshot_mismatches)),
        "unreferenced_qualification_ids": (unreferenced_qualification_ids),
        "unreferenced_coverage_ids": unreferenced_coverage_ids,
        "unresolved_in_scope_risk_case_ids": sorted(set(unresolved_risk_ids)),
        "unresolved_evaluated_scope_risk_case_ids": sorted(set(unresolved_evaluated_risk_ids)),
        "invalid_intended_pairs": [_pair_ref(pair) for pair in invalid_intended_pairs],
        "duplicate_intended_pairs": [_pair_ref(pair) for pair in duplicate_intended_pairs],
        "missing_scope_decision_pairs": [_pair_ref(pair) for pair in missing_scope_decision_pairs],
        "invalid_scope_decision_pairs": [_pair_ref(pair) for pair in invalid_scope_decision_pairs],
        "unaccounted_selected_pairs": [_pair_ref(pair) for pair in unaccounted_selected_pairs],
        "unresolved_scope_pairs": [_pair_ref(pair) for pair in unresolved_scope_pairs],
        "unmatched_scope_device_ids": unmatched_scope_devices,
    }
    input_facts = {
        "report_purpose": purpose,
        "report_authority_context": authority_context,
        "target_manifest_digest": str(target_manifest_digest or ""),
        "inventory_snapshot_digest": str(inventory_snapshot_digest or ""),
        "runtime_manifest_digest": str(runtime_manifest_digest or ""),
        "readiness": readiness_facts,
        "target_pairs": sorted(
            target_rows,
            key=lambda item: _pair(item),
        ),
        "executions": sorted(
            execution_rows,
            key=lambda item: (
                _pair(item),
                str(item.get("id") or ""),
            ),
        ),
        "assessments": sorted(
            assessment_rows,
            key=lambda item: (
                _pair(item),
                str(item.get("id") or ""),
            ),
        ),
        "evidence_qualifications": sorted(
            qualification_rows,
            key=lambda item: str(item.get("id") or ""),
        ),
        "coverage_decisions": sorted(
            coverage_rows,
            key=lambda item: str(item.get("id") or ""),
        ),
        "evidence_index": sorted(
            evidence_rows,
            key=lambda item: str(item.get("snapshot_id") or ""),
        ),
        "check_items": dict(sorted(check_item_rows.items())),
        "risks": sorted(
            risk_rows,
            key=lambda item: str(item.get("id") or ""),
        ),
        "scope_contract": scope_contract_facts,
        "intended_target_pairs": sorted(
            intended_scope_rows,
            key=lambda item: _pair(item),
        ),
        "scope_decisions": sorted(
            scope_decision_rows,
            key=lambda item: _pair(item),
        ),
        "unmatched_scope_device_ids": unmatched_scope_devices,
    }
    decision = {
        "schema_version": (REPORT_CONCLUSION_PERMISSION_SCHEMA_VERSION),
        "terminal_truth_reconciled": (terminal_truth_reconciled),
        "current_conclusion_ready": current_conclusion_ready,
        "evaluated_scope_no_abnormality_allowed": (evaluated_scope_no_abnormality_allowed),
        "overall_healthy_allowed": overall_healthy_allowed,
        "scope_reconciled": scope_reconciled,
        "overall_claim_scope_allowed": overall_claim_scope_allowed,
        "claim_boundary": claim_boundary,
        "counts": {
            "intended_target_pair_count": len(intended_pair_set),
            "scope_included": sum(
                1 for item in scope_decision_rows if str(item.get("decision") or "") == "included"
            ),
            "scope_approved_exclusion": sum(
                1
                for item in scope_decision_rows
                if str(item.get("decision") or "") == "approved_exclusion"
            ),
            "scope_not_applicable": sum(
                1
                for item in scope_decision_rows
                if str(item.get("decision") or "") == "not_applicable"
            ),
            "scope_unresolved": len(unresolved_scope_pairs),
            "health_target_pair_count": (health_target_pair_count),
            "evaluated_health_target_pair_count": evaluated_count,
            "healthy": healthy_count,
            "at_risk": at_risk_count,
            "unable_to_determine": target_unable_count,
            "health_unable_to_determine": health_unable_count,
            "not_applicable": not_applicable_count,
            "informational": informational_count,
            "unresolved_in_scope_risk_case_count": len(set(unresolved_risk_ids)),
            "unresolved_evaluated_scope_risk_case_count": len(set(unresolved_evaluated_risk_ids)),
        },
        "blocker_reason_codes": blocker_reason_codes,
        "blocker_ref_digest": _stable_digest(blocker_refs),
        "input_fact_digest": _stable_digest(input_facts),
    }
    decision["decision_digest"] = _stable_digest(decision)
    return decision


def build_report_conclusion_permission_from_source_facts(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute a conclusion decision from one immutable source payload."""

    source_facts = dict(value)
    return build_report_conclusion_permission(
        report_purpose=str(source_facts.get("report_purpose") or ""),
        report_authority_context=dict(source_facts.get("report_authority_context") or {}),
        target_manifest_digest=source_facts.get("target_manifest_digest"),
        inventory_snapshot_digest=source_facts.get("inventory_snapshot_digest"),
        runtime_manifest_digest=source_facts.get("runtime_manifest_digest"),
        readiness=dict(source_facts.get("readiness") or {}),
        target_pairs=_normalized_rows(source_facts.get("target_pairs")),
        executions=_normalized_rows(source_facts.get("executions")),
        assessments=_normalized_rows(source_facts.get("assessments")),
        evidence_qualifications=_normalized_rows(source_facts.get("evidence_qualifications")),
        coverage_decisions=_normalized_rows(source_facts.get("coverage_decisions")),
        evidence_index=_normalized_rows(source_facts.get("evidence_index")),
        check_items={
            str(key): dict(item)
            for key, item in dict(source_facts.get("check_items") or {}).items()
            if isinstance(item, Mapping)
        },
        risks=_normalized_rows(source_facts.get("risks")),
        scope_contract=dict(source_facts.get("scope_contract") or {}),
        intended_target_pairs=_normalized_rows(source_facts.get("intended_target_pairs")),
        scope_decisions=_normalized_rows(source_facts.get("scope_decisions")),
        unmatched_scope_device_ids=[
            str(item) for item in source_facts.get("unmatched_scope_device_ids") or [] if str(item)
        ],
    )


def report_conclusion_permission_is_valid(
    value: Mapping[str, Any] | None,
) -> bool:
    """Validate the self-contained deterministic decision digest."""

    decision = dict(value or {})
    digest = str(decision.pop("decision_digest", "") or "")
    counts = dict(decision.get("counts") or {})
    count_keys = (
        "intended_target_pair_count",
        "scope_included",
        "scope_approved_exclusion",
        "scope_not_applicable",
        "scope_unresolved",
        "health_target_pair_count",
        "evaluated_health_target_pair_count",
        "healthy",
        "at_risk",
        "unable_to_determine",
        "health_unable_to_determine",
        "not_applicable",
        "informational",
        "unresolved_in_scope_risk_case_count",
        "unresolved_evaluated_scope_risk_case_count",
    )
    try:
        normalized_counts = {key: int(counts.get(key) or 0) for key in count_keys}
    except (TypeError, ValueError):
        return False
    health_count = normalized_counts["health_target_pair_count"]
    health_unable_count = (
        normalized_counts["health_unable_to_determine"]
        if "health_unable_to_determine" in counts
        else normalized_counts["unable_to_determine"]
    )
    health_sum = (
        normalized_counts["healthy"]
        + normalized_counts["at_risk"]
        + health_unable_count
        + normalized_counts["not_applicable"]
    )
    reason_codes = {str(code) for code in decision.get("blocker_reason_codes") or []}
    return bool(
        len(digest) == 64
        and decision.get("schema_version") == REPORT_CONCLUSION_PERMISSION_SCHEMA_VERSION
        and all(value >= 0 for value in normalized_counts.values())
        and health_count == health_sum
        and reason_codes <= REPORT_CONCLUSION_BLOCKER_REASON_CODES
        and _stable_digest(decision) == digest
    )


def canonical_raw_evidence_set(
    evidence_index: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project only immutable raw-evidence identity fields in stable order."""

    rows = [
        {
            "snapshot_id": str(item.get("snapshot_id") or ""),
            "raw_text_hash": str(item.get("raw_text_hash") or ""),
            "source_file_hash": (
                str(item.get("source_file_hash"))
                if item.get("source_file_hash") is not None
                else None
            ),
            "run_id": str(item.get("run_id") or ""),
            "dataset_id": (
                str(item.get("dataset_id")) if item.get("dataset_id") is not None else None
            ),
        }
        for item in evidence_index
    ]
    rows.sort(
        key=lambda item: (
            item["snapshot_id"],
            item["raw_text_hash"],
            item["source_file_hash"] or "",
            item["run_id"],
            item["dataset_id"] or "",
        )
    )
    return rows


def raw_evidence_set_digest(
    evidence_index: Iterable[Mapping[str, Any]],
) -> str:
    payload = canonical_raw_evidence_set(evidence_index)
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CURRENT_STATE_AUTHORITATIVE_PURPOSES",
    "OFFLINE_HISTORICAL",
    "PARSER_VALIDATION",
    "PRODUCTION_CURRENT",
    "REPORT_PURPOSES",
    "REPORT_CONCLUSION_BLOCKER_REASON_CODES",
    "REPORT_CONCLUSION_PERMISSION_SCHEMA_VERSION",
    "RULE_ASSET_REPLAY",
    "REPORT_AUTHORITY_CONTEXT_SCHEMA_VERSION",
    "SHADOW_VALIDATION",
    "build_report_authority_context",
    "build_report_conclusion_permission",
    "build_report_conclusion_permission_from_source_facts",
    "canonical_raw_evidence_set",
    "derive_report_source_kind",
    "normalize_report_purpose",
    "raw_evidence_set_digest",
    "report_authority_context_is_valid",
    "report_conclusion_permission_is_valid",
    "report_purpose_is_consistent_with_source",
    "report_purpose_for_patrol_mode",
]
