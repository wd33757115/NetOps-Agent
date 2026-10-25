"""Deterministic assessment-policy resolution and frozen decision evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

ASSESSMENT_POLICY_SNAPSHOT_SCHEMA = "assessment-policy-snapshot/v1"
ASSESSMENT_BASIS_SCHEMA = "check-assessment-basis/v1"

SOURCE_PRIORITY = {
    "device_output": 400,
    "inventory_profile": 300,
    "scope_override": 200,
    "catalog_default": 100,
    "approved_rule_constant": 50,
}
DEFAULT_SOURCE_PRECEDENCE = (
    "device_output",
    "inventory_profile",
    "scope_override",
    "catalog_default",
)

_THRESHOLD_KINDS = {
    "threshold": ("high", "gte"),
    "threshold_field_gte": ("high", "gte"),
    "threshold_lte": ("low", "lte"),
    "threshold_field_lte": ("low", "lte"),
}


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _unit(value: Any) -> str:
    normalized = _text(value).replace(" ", "")
    return {
        "%": "percent",
        "pct": "percent",
        "percentage": "percent",
    }.get(normalized, normalized)


def _strings(value: Any) -> list[str]:
    if isinstance(value, (str, bytes, bytearray)):
        values: Iterable[Any] = [value]
    elif isinstance(value, Iterable):
        values = value
    else:
        values = []
    return sorted(
        {
            str(item).strip()
            for item in values
            if item is not None and str(item).strip()
        }
    )


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _datetime(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(
                str(value).strip().replace("Z", "+00:00")
            )
        except ValueError:
            return None
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def normalize_assessment_policy_contract(
    canonical_contract: Mapping[str, Any] | None,
    applicability_requirement: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile catalog semantics into one command-independent policy contract."""

    contract = dict(canonical_contract or {})
    explicit = dict(contract.get("assessment_policy") or {})
    threshold_requirements: dict[str, dict[str, Any]] = {}
    expected_state_requirements: dict[str, dict[str, Any]] = {}
    for raw_observation in contract.get("observations") or []:
        if not isinstance(raw_observation, Mapping):
            continue
        observation = dict(raw_observation)
        observation_type = str(observation.get("observation_type") or "").strip()
        if not observation_type:
            continue
        constraints = dict(observation.get("policy_constraints") or {})
        defaults = {
            str(role): {
                "value": float(value),
                "unit": observation.get("unit"),
                "hysteresis": constraints.get("required_deadband")
                or constraints.get("minimum_hysteresis"),
            }
            for role, value in dict(constraints.get("required_thresholds") or {}).items()
            if _number(value) is not None
        }
        required_any_roles = _strings(
            constraints.get("required_threshold_reference_any")
        )
        if defaults or required_any_roles or constraints.get(
            "require_structured_threshold_references"
        ):
            threshold_requirements[observation_type] = {
                "required": True,
                "required_any_roles": required_any_roles,
                "allow_device_output": True,
                "allow_inventory_profile": True,
                "allow_scope_override": True,
                "allow_catalog_default": bool(defaults),
                "unit": observation.get("unit"),
                "defaults": defaults,
            }

        event_constraints = dict(observation.get("event_constraints") or {})
        if (
            str(observation.get("semantic_type") or "") in {"state", "enum"}
            and event_constraints.get("allow_initial_transition_fault") is False
        ):
            expected_state_requirements[observation_type] = {
                "required": True,
                "allow_inventory_profile": True,
                "allow_scope_override": True,
                "allow_catalog_default": False,
                "default_states": [],
            }

    for observation_type, raw_requirement in dict(
        explicit.get("thresholds") or {}
    ).items():
        if not isinstance(raw_requirement, Mapping):
            continue
        current = dict(threshold_requirements.get(str(observation_type)) or {})
        supplied = dict(raw_requirement)
        current.update(
            {
                key: value
                for key, value in supplied.items()
                if key != "defaults"
            }
        )
        if "defaults" in supplied:
            current["defaults"] = dict(supplied.get("defaults") or {})
        threshold_requirements[str(observation_type)] = current

    for observation_type, requirement in dict(
        explicit.get("expected_states") or {}
    ).items():
        if not isinstance(requirement, Mapping):
            continue
        expected_state_requirements[str(observation_type)] = {
            **dict(expected_state_requirements.get(str(observation_type)) or {}),
            **dict(requirement),
        }
    requested_precedence = tuple(
        str(item)
        for item in explicit.get("source_precedence") or ()
        if str(item) in SOURCE_PRIORITY
    )
    if requested_precedence and requested_precedence != DEFAULT_SOURCE_PRECEDENCE:
        raise ValueError(
            "assessment policy source precedence is platform-governed and "
            "cannot be overridden by a check-item asset"
        )
    payload = {
        "schema_version": "assessment-policy-contract/v1",
        "source_precedence": list(DEFAULT_SOURCE_PRECEDENCE),
        "applicability": dict(
            applicability_requirement
            or explicit.get("applicability")
            or {}
        ),
        "thresholds": threshold_requirements,
        "expected_states": expected_state_requirements,
    }
    payload["digest"] = canonical_digest(payload)
    return payload


def assessment_policy_source_digest(source: Mapping[str, Any]) -> str:
    """Digest the immutable source payload while excluding approval state metadata."""

    return canonical_digest(
        {
            key: value
            for key, value in dict(source).items()
            if key not in {"digest", "status", "approved_at", "approved_by"}
        }
    )


def _device_scope_matches(
    source: Mapping[str, Any],
    device: Mapping[str, Any],
) -> bool:
    scope = dict(source.get("scope") or {})
    device_id = str(device.get("device_id") or device.get("id") or "")
    site = str(device.get("site") or "")
    role = str(device.get("role") or "")
    selectors = (
        ("device_ids", device_id),
        ("sites", site),
        ("roles", role),
    )
    return all(
        not _strings(scope.get(key)) or value in _strings(scope.get(key))
        for key, value in selectors
    )


def _normalize_external_source(
    raw: Mapping[str, Any],
    *,
    source_kind: str,
    check_item_id: str,
    device: Mapping[str, Any],
    effective_at: datetime | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    source = dict(raw)
    policy_id = str(source.get("policy_id") or source.get("id") or "").strip()
    version = str(source.get("version") or "").strip()
    observation_type = str(source.get("observation_type") or "").strip()
    policy_role = str(source.get("policy_role") or "").strip().lower()
    status = str(source.get("status") or "").strip().lower()
    reason = None
    integrity_errors = _strings(source.get("integrity_errors"))
    if str(source.get("check_item_id") or "") != check_item_id:
        return None, None
    if not _device_scope_matches(source, device):
        return None, None
    if integrity_errors:
        reason = "asset_integrity_mismatch"
    elif status != "approved":
        reason = "policy_not_approved"
    elif not policy_id or not version or not observation_type:
        reason = "policy_identity_incomplete"
    elif not source.get("approved_by") or not source.get("approved_at"):
        reason = "policy_approval_evidence_missing"

    supplied_digest = str(source.get("digest") or "").strip()
    computed_digest = assessment_policy_source_digest(source)
    if reason is None and not supplied_digest:
        reason = "policy_digest_missing"
    elif reason is None and supplied_digest != computed_digest:
        reason = "policy_digest_mismatch"
    approved_at = _datetime(source.get("approved_at"))
    valid_from_raw = source.get("valid_from") or source.get("effective_from")
    expires_at_raw = source.get("expires_at") or source.get("valid_until")
    valid_from = _datetime(valid_from_raw)
    expires_at = _datetime(expires_at_raw)
    if reason is None and source.get("approved_at") and approved_at is None:
        reason = "policy_approved_at_invalid"
    elif reason is None and valid_from_raw and valid_from is None:
        reason = "policy_valid_from_invalid"
    elif reason is None and expires_at_raw and expires_at is None:
        reason = "policy_expires_at_invalid"
    elif (
        reason is None
        and valid_from is not None
        and expires_at is not None
        and valid_from >= expires_at
    ):
        reason = "policy_validity_window_invalid"
    elif (
        reason is None
        and effective_at is not None
        and approved_at is not None
        and approved_at > effective_at
    ):
        reason = "policy_not_yet_approved"
    elif (
        reason is None
        and effective_at is not None
        and valid_from is not None
        and effective_at < valid_from
    ):
        reason = "policy_not_yet_effective"
    elif (
        reason is None
        and effective_at is not None
        and expires_at is not None
        and effective_at >= expires_at
    ):
        reason = "policy_expired"
    if reason is not None:
        return None, {
            "policy_id": policy_id or None,
            "version": version or None,
            "policy_version_id": source.get("policy_version_id"),
            "binding_id": source.get("binding_id"),
            "approval_digest": source.get("approval_digest"),
            "observation_type": observation_type or None,
            "policy_role": policy_role or None,
            "source_kind": source_kind,
            "reason_code": reason,
            "scope": dict(source.get("scope") or {}),
            "digest": supplied_digest or None,
            "integrity_errors": integrity_errors,
        }

    thresholds: dict[str, dict[str, Any]] = {}
    for role, raw_binding in dict(source.get("thresholds") or {}).items():
        binding = (
            dict(raw_binding)
            if isinstance(raw_binding, Mapping)
            else {"value": raw_binding}
        )
        value = _number(binding.get("value"))
        if value is None:
            continue
        thresholds[str(role)] = {
            "value": value,
            "unit": binding.get("unit"),
            "hysteresis": binding.get("hysteresis"),
            "window": binding.get("window"),
        }
    normalized = {
        "source_kind": source_kind,
        "priority": SOURCE_PRIORITY[source_kind],
        "policy_id": policy_id,
        "version": version,
        "policy_version_id": source.get("policy_version_id"),
        "policy_version_digest": source.get("policy_version_digest"),
        "binding_id": source.get("binding_id"),
        "binding_digest": source.get("binding_digest"),
        "approval_digest": source.get("approval_digest"),
        "digest": computed_digest,
        "check_item_id": check_item_id,
        "observation_type": observation_type,
        "policy_role": policy_role,
        "scope": dict(source.get("scope") or {}),
        "thresholds": thresholds,
        "expected_states": _strings(source.get("expected_states")),
        "applicability": (
            dict(source.get("applicability") or {})
            if isinstance(source.get("applicability"), Mapping)
            else {}
        ),
        "evidence_refs": _strings(source.get("evidence_refs")),
        "approved_by": str(source.get("approved_by")),
        "approved_at": str(source.get("approved_at")),
        "valid_from": _timestamp(valid_from),
        "expires_at": _timestamp(expires_at),
    }
    return normalized, None


def _device_capability_evidence(device: Mapping[str, Any]) -> dict[str, Any]:
    attributes = dict(device.get("attributes") or device.get("attributes_json") or {})
    source_present = "capabilities" in device or "capabilities" in attributes
    values = device.get("capabilities")
    if values is None:
        values = attributes.get("capabilities")
    capabilities = sorted(
        {
            str(item).strip().casefold()
            for item in values or []
            if str(item).strip()
        }
    )
    payload = {
        "source_present": source_present,
        "capabilities": capabilities,
    }
    payload["digest"] = canonical_digest(payload)
    return payload


def _evaluate_applicability_policy(
    source: Mapping[str, Any],
    *,
    capability_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    policy = dict(source.get("applicability") or {})
    mode = str(policy.get("mode") or "").strip().lower()
    if mode == "explicit":
        status = str(policy.get("decision") or "").strip().lower()
        if status not in {"applicable", "not_applicable"}:
            status = "conflict"
        reason_code = str(policy.get("reason_code") or "").strip() or (
            "policy_explicit_not_applicable"
            if status == "not_applicable"
            else "policy_explicit_applicable"
        )
        return {
            **dict(source),
            "decision_status": status,
            "reason_code": reason_code,
            "empty_result_classification": str(
                policy.get("empty_result_classification") or "invalid"
            ),
            "legal_empty_patterns": _strings(
                policy.get("legal_empty_patterns") or []
            ),
        }
    if mode != "capability":
        return {
            **dict(source),
            "decision_status": "conflict",
            "reason_code": "applicability_policy_invalid",
            "empty_result_classification": "invalid",
        }

    required = {
        str(item).strip().casefold()
        for item in policy.get("required_capabilities") or []
        if str(item).strip()
    }
    observed = set(capability_evidence.get("capabilities") or [])
    if not required:
        status = "conflict"
        reason_code = "applicability_policy_invalid"
    elif not bool(capability_evidence.get("source_present")):
        status = "unknown"
        reason_code = "applicability_capability_unknown"
    elif required.issubset(observed):
        status = "applicable"
        reason_code = "capability_confirmed"
    else:
        status = "not_applicable"
        reason_code = str(policy.get("reason_code") or "").strip() or (
            "capability_explicitly_absent"
        )
    return {
        **dict(source),
        "decision_status": status,
        "reason_code": reason_code,
        "required_capabilities": sorted(required),
        "missing_capabilities": sorted(required - observed),
        "empty_result_classification": str(
            policy.get("empty_result_classification") or "invalid"
        ),
        "legal_empty_patterns": _strings(
            policy.get("legal_empty_patterns") or []
        ),
    }


def _resolve_applicability_decision(
    *,
    sources: Iterable[Mapping[str, Any]],
    invalid_sources: Iterable[Mapping[str, Any]],
    device: Mapping[str, Any],
    legacy_requirement: Mapping[str, Any] | None,
    check_item_version_id: str,
    allow_legacy_sources: bool = True,
) -> dict[str, Any]:
    capability_evidence = _device_capability_evidence(device)
    relevant_invalid = [
        dict(item)
        for item in invalid_sources
        if str(item.get("policy_role") or "") == "applicability"
    ]
    candidates = [
        _evaluate_applicability_policy(
            dict(source),
            capability_evidence=capability_evidence,
        )
        for source in sources
        if str(source.get("policy_role") or "") == "applicability"
        and isinstance(source.get("applicability"), Mapping)
    ]

    selected: dict[str, Any] | None = None
    status = "applicable"
    reason_codes: list[str] = []
    source_kind = "none"
    if relevant_invalid:
        status = "conflict"
        reason_codes = ["assessment_applicability_source_invalid"]
        source_kind = "governed_policy"
    elif candidates:
        highest = max(int(item.get("priority") or 0) for item in candidates)
        top = [item for item in candidates if int(item.get("priority") or 0) == highest]
        signatures = {
            canonical_digest(dict(item.get("applicability") or {})) for item in top
        }
        if len(signatures) > 1:
            status = "conflict"
            reason_codes = ["assessment_applicability_conflict"]
            source_kind = "governed_policy"
        else:
            top.sort(
                key=lambda item: (
                    str(item.get("source_kind") or ""),
                    str(item.get("policy_id") or ""),
                    str(item.get("version") or ""),
                )
            )
            selected = dict(top[0])
            status = str(selected.get("decision_status") or "conflict")
            reason_codes = [str(selected.get("reason_code") or status)]
            source_kind = str(selected.get("source_kind") or "governed_policy")
    elif allow_legacy_sources:
        requirement = dict(legacy_requirement or {})
        required_capability = str(requirement.get("capability") or "").strip()
        source_kind = "check_item_contract"
        if not required_capability:
            status = "applicable"
            reason_codes = []
        elif not bool(capability_evidence.get("source_present")):
            status = "unknown"
            reason_codes = ["applicability_capability_unknown"]
        elif required_capability.casefold() not in set(
            capability_evidence.get("capabilities") or []
        ):
            status = "not_applicable"
            reason_codes = ["capability_explicitly_absent"]
        else:
            status = "applicable"
            reason_codes = ["capability_confirmed"]
        selected = {
            "source_kind": source_kind,
            "policy_id": check_item_version_id,
            "version": check_item_version_id,
            "digest": canonical_digest(requirement),
            "applicability": requirement,
            "decision_status": status,
            "reason_code": reason_codes[0] if reason_codes else None,
            "required_capabilities": (
                [required_capability] if required_capability else []
            ),
            "empty_result_classification": (
                "not_applicable" if status == "not_applicable" else "invalid"
            ),
            "evidence_refs": [check_item_version_id],
            "legacy_contract": True,
        }
    else:
        status = "unknown"
        reason_codes = ["assessment_applicability_policy_missing"]
        source_kind = "governed_policy"
        selected = None

    considered_sources = []
    for item in candidates:
        considered_sources.append(
            {
                key: item.get(key)
                for key in (
                    "source_kind",
                    "priority",
                    "policy_id",
                    "version",
                    "policy_version_id",
                    "policy_version_digest",
                    "binding_id",
                    "binding_digest",
                    "approval_digest",
                    "digest",
                    "decision_status",
                    "reason_code",
                    "required_capabilities",
                    "missing_capabilities",
                    "empty_result_classification",
                    "legal_empty_patterns",
                    "evidence_refs",
                )
                if item.get(key) is not None
            }
            | {
                "selected": bool(
                    selected
                    and str(item.get("policy_id") or "")
                    == str(selected.get("policy_id") or "")
                    and str(item.get("version") or "")
                    == str(selected.get("version") or "")
                )
            }
        )
    considered_sources.extend(
        {**item, "selected": False} for item in relevant_invalid
    )
    payload = {
        "schema_version": "assessment-applicability-decision/v1",
        "status": status,
        "reason_codes": sorted(set(reason_codes)),
        "source_kind": source_kind,
        "selected_policy": (
            {
                key: selected.get(key)
                for key in (
                    "source_kind",
                    "priority",
                    "policy_id",
                    "version",
                    "policy_version_id",
                    "policy_version_digest",
                    "binding_id",
                    "binding_digest",
                    "approval_digest",
                    "digest",
                    "decision_status",
                    "reason_code",
                    "required_capabilities",
                    "missing_capabilities",
                    "empty_result_classification",
                    "legal_empty_patterns",
                    "evidence_refs",
                    "legacy_contract",
                )
                if selected.get(key) is not None
            }
            if selected is not None
            else None
        ),
        "considered_sources": considered_sources,
        "invalid_sources": relevant_invalid,
        "capability_evidence": capability_evidence,
    }
    payload["digest"] = canonical_digest(payload)
    return payload


def build_target_assessment_policy_snapshot(
    *,
    check_item_id: str,
    check_item_version_id: str,
    canonical_contract: Mapping[str, Any],
    device: Mapping[str, Any],
    scope_overrides: Iterable[Mapping[str, Any]] = (),
    governed_sources: Iterable[Mapping[str, Any]] = (),
    applicability_requirement: Mapping[str, Any] | None = None,
    policy_effective_at: datetime | str | None = None,
    allow_legacy_sources: bool = True,
    allow_legacy_rule_fallback: bool = False,
    resolver_transition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze approved target-specific sources without consulting mutable runtime state."""

    contract = normalize_assessment_policy_contract(
        canonical_contract,
        applicability_requirement,
    )
    effective_at = _datetime(policy_effective_at)
    if policy_effective_at is not None and effective_at is None:
        raise ValueError("assessment policy effective time is invalid")
    attributes = dict(
        device.get("attributes") or device.get("attributes_json") or {}
    )
    inventory_sources = (
        list(
            attributes.get("assessment_policy_bindings")
            or attributes.get("assessment_policies")
            or []
        )
        if allow_legacy_sources
        else []
    )
    sources: list[dict[str, Any]] = []
    invalid_sources: list[dict[str, Any]] = []
    candidate_sources = [
        (item, "inventory_profile") for item in inventory_sources
    ]
    if allow_legacy_sources:
        candidate_sources.extend(
            (item, "scope_override") for item in scope_overrides
        )
    for item in governed_sources:
        if isinstance(item, Mapping):
            candidate_sources.append(
                (item, str(item.get("source_kind") or ""))
            )
        else:
            candidate_sources.append((item, ""))
    for raw, source_kind in candidate_sources:
        if not isinstance(raw, Mapping):
            invalid_sources.append(
                {
                    "source_kind": source_kind,
                    "reason_code": "policy_payload_invalid",
                }
            )
            continue
        if source_kind not in {
            "inventory_profile",
            "scope_override",
            "catalog_default",
        }:
            invalid_sources.append(
                {
                    "source_kind": source_kind or None,
                    "reason_code": "policy_source_kind_invalid",
                }
            )
            continue
        normalized, invalid = _normalize_external_source(
            raw,
            source_kind=source_kind,
            check_item_id=check_item_id,
            device=device,
            effective_at=effective_at,
        )
        if normalized is not None:
            sources.append(normalized)
        if invalid is not None:
            invalid_sources.append(invalid)
    sources.sort(
        key=lambda item: (
            -int(item["priority"]),
            str(item["observation_type"]),
            str(item["policy_id"]),
            str(item["version"]),
        )
    )
    payload = {
        "schema_version": ASSESSMENT_POLICY_SNAPSHOT_SCHEMA,
        "check_item_id": check_item_id,
        "check_item_version_id": check_item_version_id,
        "device_scope": {
            "device_id": str(device.get("device_id") or device.get("id") or ""),
            "site": device.get("site"),
            "role": device.get("role"),
        },
        "policy_effective_at": _timestamp(effective_at),
        "legacy_sources_allowed": bool(allow_legacy_sources),
        "legacy_rule_fallback_allowed": bool(allow_legacy_rule_fallback),
        "resolver_mode": str(
            dict(resolver_transition or {}).get("resolver_mode")
            or ("legacy_compat" if allow_legacy_sources else "governed_only")
        ),
        "resolver_transition": dict(resolver_transition or {}),
        "contract": {
            key: value for key, value in contract.items() if key != "digest"
        },
        "contract_digest": contract["digest"],
        "sources": sources,
        "invalid_sources": invalid_sources,
        "applicability_decision": _resolve_applicability_decision(
            sources=sources,
            invalid_sources=invalid_sources,
            device=device,
            legacy_requirement=applicability_requirement,
            check_item_version_id=check_item_version_id,
            allow_legacy_sources=allow_legacy_sources,
        ),
    }
    payload["digest"] = canonical_digest(payload)
    return payload


def validate_assessment_policy_snapshot(snapshot: Mapping[str, Any]) -> bool:
    payload = dict(snapshot or {})
    supplied = str(payload.pop("digest", ""))
    return (
        payload.get("schema_version") == ASSESSMENT_POLICY_SNAPSHOT_SCHEMA
        and len(supplied) == 64
        and canonical_digest(payload) == supplied
    )


def validate_assessment_basis(basis: Mapping[str, Any]) -> bool:
    payload = dict(basis or {})
    supplied = str(payload.pop("digest", ""))
    return (
        payload.get("schema_version") == ASSESSMENT_BASIS_SCHEMA
        and len(supplied) == 64
        and canonical_digest(payload) == supplied
    )


def _entity_scope_matches(
    source: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> bool:
    scope = dict(source.get("scope") or {})
    entity_types = _strings(scope.get("entity_types"))
    entity_keys = _strings(scope.get("entity_keys"))
    if entity_types and str(observation.get("entity_type") or "") not in entity_types:
        return False
    if entity_keys and str(observation.get("entity_key") or "") not in entity_keys:
        return False
    required_dimensions = dict(scope.get("dimensions") or {})
    dimensions = dict(
        observation.get("dimensions") or observation.get("dimensions_json") or {}
    )
    return all(
        not _strings(expected)
        or str(dimensions.get(key) or "") in _strings(expected)
        for key, expected in required_dimensions.items()
    )


def _binding_signature(binding: Mapping[str, Any], *, expected: bool = False) -> str:
    if expected:
        payload = {"expected_states": _strings(binding.get("expected_states"))}
    else:
        payload = {
            "value": _number(binding.get("value")),
            "unit": str(binding.get("unit") or "").lower(),
            "hysteresis": _number(binding.get("hysteresis")),
            "window": str(binding.get("window") or ""),
        }
    return canonical_digest(payload)


def _choose_binding(
    candidates: list[dict[str, Any]],
    *,
    expected: bool = False,
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if not candidates:
        return "missing", None, []
    highest = max(int(item["priority"]) for item in candidates)
    selected_level = [item for item in candidates if int(item["priority"]) == highest]
    signatures = {
        _binding_signature(item, expected=expected) for item in selected_level
    }
    if len(signatures) > 1:
        return "conflict", None, selected_level
    selected_level.sort(
        key=lambda item: (
            str(item.get("source_kind") or ""),
            str(item.get("policy_id") or ""),
            str(item.get("version") or ""),
            str(item.get("reference_field") or ""),
        )
    )
    selected = dict(selected_level[0])
    selected["equivalent_source_refs"] = [
        {
            "source_kind": item.get("source_kind"),
            "policy_id": item.get("policy_id"),
            "version": item.get("version"),
            "digest": item.get("digest"),
            "reference_field": item.get("reference_field"),
        }
        for item in selected_level
    ]
    return "resolved", selected, selected_level


def _candidate_audit(
    candidates: Iterable[Mapping[str, Any]],
    *,
    selected: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    selected_signature = (
        (
            str(selected.get("source_kind") or ""),
            str(selected.get("policy_id") or ""),
            str(selected.get("version") or ""),
            str(selected.get("digest") or ""),
            str(selected.get("reference_field") or ""),
        )
        if selected is not None
        else None
    )
    rows: list[dict[str, Any]] = []
    for raw in candidates:
        item = dict(raw)
        signature = (
            str(item.get("source_kind") or ""),
            str(item.get("policy_id") or ""),
            str(item.get("version") or ""),
            str(item.get("digest") or ""),
            str(item.get("reference_field") or ""),
        )
        is_selected = selected_signature is not None and signature == selected_signature
        rows.append(
            {
                key: item.get(key)
                for key in (
                    "source_kind",
                    "priority",
                    "policy_id",
                    "version",
                    "policy_version_id",
                    "policy_version_digest",
                    "binding_id",
                    "binding_digest",
                    "approval_digest",
                    "digest",
                    "reference_field",
                    "threshold_role",
                    "value",
                    "unit",
                    "hysteresis",
                    "window",
                    "expected_states",
                    "evidence_refs",
                    "approved_by",
                    "approved_at",
                    "valid_from",
                    "expires_at",
                )
                if item.get(key) is not None
            }
            | {
                "selected": is_selected,
                "selection_reason": (
                    "highest_priority"
                    if is_selected
                    else "lower_priority_or_equivalent"
                    if selected is not None
                    else "unresolved"
                ),
            }
        )
    rows.sort(
        key=lambda item: (
            -int(item.get("priority") or 0),
            str(item.get("source_kind") or ""),
            str(item.get("policy_id") or ""),
            str(item.get("version") or ""),
            str(item.get("reference_field") or ""),
        )
    )
    return rows


def _source_candidates(
    snapshot: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    role: str | None = None,
    expected: bool = False,
) -> list[dict[str, Any]]:
    observation_type = str(observation.get("observation_type") or "")
    rows: list[dict[str, Any]] = []
    for source in snapshot.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        source = dict(source)
        if source.get("observation_type") != observation_type:
            continue
        if not _entity_scope_matches(source, observation):
            continue
        if expected:
            states = _strings(source.get("expected_states"))
            if states:
                rows.append({**source, "expected_states": states})
            continue
        binding = dict(dict(source.get("thresholds") or {}).get(str(role)) or {})
        if _number(binding.get("value")) is not None:
            rows.append({**source, **binding, "threshold_role": role})
    return rows


def _catalog_threshold_candidates(
    snapshot: Mapping[str, Any],
    observation: Mapping[str, Any],
    role: str,
) -> list[dict[str, Any]]:
    contract = dict(snapshot.get("contract") or {})
    requirement = dict(
        dict(contract.get("thresholds") or {}).get(
            str(observation.get("observation_type") or "")
        )
        or {}
    )
    binding = dict(dict(requirement.get("defaults") or {}).get(role) or {})
    value = _number(binding.get("value"))
    if value is None or not requirement.get("allow_catalog_default", bool(binding)):
        return []
    return [
        {
            "source_kind": "catalog_default",
            "priority": SOURCE_PRIORITY["catalog_default"],
            "policy_id": snapshot.get("check_item_id"),
            "version": snapshot.get("check_item_version_id"),
            "digest": snapshot.get("contract_digest"),
            "value": value,
            "unit": binding.get("unit") or requirement.get("unit"),
            "hysteresis": binding.get("hysteresis"),
            "window": binding.get("window"),
            "threshold_role": role,
            "evidence_refs": [str(snapshot.get("check_item_version_id") or "")],
        }
    ]


def _device_output_candidates(
    rule: Mapping[str, Any],
    observation: Mapping[str, Any],
    role: str,
) -> list[dict[str, Any]]:
    condition = dict(rule.get("effective_condition") or rule.get("condition") or {})
    context = dict(observation.get("context") or {})
    source_metadata = dict(context.get("threshold_reference_sources") or {})
    role_source = dict(source_metadata.get(role) or {})
    fields = [
        str(condition.get("reference_field") or ""),
        *(str(item) for item in condition.get("fallback_reference_fields") or []),
        str(role_source.get("context_key") or ""),
        str(role_source.get("field_name") or ""),
    ]
    if not any(fields):
        return []
    rows: list[dict[str, Any]] = []
    for field_name in dict.fromkeys(item for item in fields if item):
        value = _number(context.get(field_name))
        if value is None:
            continue
        provenance = role_source
        rows.append(
            {
                "source_kind": "device_output",
                "priority": SOURCE_PRIORITY["device_output"],
                "policy_id": "device_output",
                "version": observation.get("parser_version") or "observed",
                "digest": canonical_digest(
                    {
                        "observation_id": observation.get("id"),
                        "reference_field": field_name,
                        "value": value,
                        "provenance": provenance,
                    }
                ),
                "value": value,
                "unit": observation.get("unit"),
                "hysteresis": condition.get("hysteresis"),
                "window": None,
                "threshold_role": role,
                "reference_field": field_name,
                "provenance": provenance,
                "evidence_refs": _strings(
                    [
                        observation.get("id"),
                        observation.get("snapshot_id"),
                        observation.get("parser_template_id"),
                        observation.get("observation_contract_id"),
                    ]
                ),
            }
        )
    return rows


def _legacy_rule_candidate(
    rule: Mapping[str, Any],
    role: str,
) -> list[dict[str, Any]]:
    condition = dict(rule.get("effective_condition") or rule.get("condition") or {})
    key = "gte" if role in {"high", "critical_high"} else "lte"
    value = _number(condition.get(key))
    if value is None:
        return []
    return [
        {
            "source_kind": "approved_rule_constant",
            "priority": SOURCE_PRIORITY["approved_rule_constant"],
            "policy_id": rule.get("rule_id"),
            "version": rule.get("version_id") or rule.get("version"),
            "digest": rule.get("digest"),
            "value": value,
            "unit": None,
            "hysteresis": condition.get("hysteresis"),
            "window": None,
            "threshold_role": role,
            "evidence_refs": _strings([rule.get("version_id")]),
        }
    ]


@dataclass(frozen=True, slots=True)
class AssessmentBasisResolution:
    status: str
    blocker_code: str | None
    effective_condition: dict[str, Any]
    should_evaluate: bool
    basis: dict[str, Any]


def resolve_assessment_basis(
    *,
    rule: Mapping[str, Any],
    observation: Mapping[str, Any],
    policy_snapshot: Mapping[str, Any] | None,
    runtime_bundle_version_id: str,
    runtime_bundle_digest: str,
) -> AssessmentBasisResolution:
    """Resolve one rule/observation decision without mutable configuration lookup."""

    snapshot = dict(policy_snapshot or {})
    strict = bool(snapshot)
    snapshot_valid = validate_assessment_policy_snapshot(snapshot) if strict else False
    legacy_sources_allowed = bool(snapshot.get("legacy_sources_allowed", True))
    legacy_rule_fallback_allowed = bool(
        snapshot.get("legacy_rule_fallback_allowed", False)
    )
    condition = dict(rule.get("effective_condition") or rule.get("condition") or {})
    kind = str(condition.get("kind") or "")
    role, operator = _THRESHOLD_KINDS.get(kind, (None, None))
    role = str(condition.get("threshold_role") or role or "") or None
    observation_type = str(observation.get("observation_type") or "")
    evidence_refs = _strings(
        [
            observation.get("id"),
            observation.get("snapshot_id"),
            observation.get("parser_template_id"),
            observation.get("observation_contract_id"),
            rule.get("version_id"),
            runtime_bundle_version_id,
        ]
    )
    basis = {
        "schema_version": ASSESSMENT_BASIS_SCHEMA,
        "observation_id": observation.get("id"),
        "observation_type": observation_type,
        "observed_value": observation.get("value")
        if "value" in observation
        else observation.get("value_json"),
        "unit": observation.get("unit"),
        "observed_at": _timestamp(observation.get("observed_at")),
        "entity_identity": {
            "device_id": observation.get("device_id"),
            "entity_type": observation.get("entity_type"),
            "entity_key": observation.get("entity_key"),
            "dimensions": dict(
                observation.get("dimensions")
                or observation.get("dimensions_json")
                or {}
            ),
        },
        "rule_binding": {
            "rule_id": rule.get("rule_id"),
            "rule_version_id": rule.get("version_id"),
            "rule_digest": rule.get("digest"),
            "condition_kind": kind,
        },
        "runtime_bundle_version_id": runtime_bundle_version_id,
        "runtime_bundle_digest": runtime_bundle_digest,
        "policy_snapshot_digest": snapshot.get("digest") if strict else None,
        "applicability_decision": (
            dict(snapshot.get("applicability_decision") or {})
            if strict
            else {
                "status": "applicable",
                "source_kind": "legacy_rule_scope",
            }
        ),
        "expected_state_binding": {"status": "not_required"},
        "threshold_binding": {"status": "not_required"},
        "baseline_binding": {"status": "not_required"},
        "evidence_refs": evidence_refs,
    }
    if strict and not snapshot_valid:
        basis["policy_resolution"] = {
            "status": "conflict",
            "reason_code": "assessment_policy_snapshot_invalid",
        }
        basis["digest"] = canonical_digest(basis)
        return AssessmentBasisResolution(
            status="conflict",
            blocker_code="assessment_policy_snapshot_invalid",
            effective_condition=condition,
            should_evaluate=False,
            basis=basis,
        )
    applicability = dict(basis.get("applicability_decision") or {})
    applicability_status = str(applicability.get("status") or "unknown")
    selected_applicability = dict(applicability.get("selected_policy") or {})
    basis["evidence_refs"] = _strings(
        [
            *basis["evidence_refs"],
            *selected_applicability.get("evidence_refs", []),
        ]
    )
    if strict and applicability_status in {"unknown", "conflict"}:
        blocker = (
            "assessment_applicability_conflict"
            if applicability_status == "conflict"
            else "assessment_applicability_unknown"
        )
        basis["policy_resolution"] = {
            "status": applicability_status,
            "reason_code": blocker,
        }
        basis["digest"] = canonical_digest(basis)
        return AssessmentBasisResolution(
            status=applicability_status,
            blocker_code=blocker,
            effective_condition=condition,
            should_evaluate=False,
            basis=basis,
        )
    if strict and applicability_status == "not_applicable":
        basis["policy_resolution"] = {
            "status": "resolved",
            "reason_code": "not_applicable",
        }
        basis["digest"] = canonical_digest(basis)
        return AssessmentBasisResolution(
            status="resolved",
            blocker_code=None,
            effective_condition=condition,
            should_evaluate=False,
            basis=basis,
        )
    expected_requirement = dict(
        dict(dict(snapshot.get("contract") or {}).get("expected_states") or {}).get(
            observation_type
        )
        or {}
    )
    # Report compatibility callers must also honor the frozen CheckItem semantics.
    semantics = dict(
        dict(observation.get("metadata") or observation.get("metadata_json") or {}).get("semantics")
        or {}
    )
    if (
        not expected_requirement
        and dict(semantics.get("event_constraints") or {}).get("allow_initial_transition_fault")
        is False
    ):
        expected_requirement = {"required": True, "allow_catalog_default": False}

    if kind == "numeric_health":
        from src.core.check_items.numeric_health import evaluate_numeric_health

        health = evaluate_numeric_health(
            dict(observation), dict(condition.get("health_policy") or {})
        )
        basis["health_assessment"] = health
        basis["baseline_binding"] = {
            "status": "not_required" if health.get("baseline_mode") == "trend_only" else "resolved"
            if health["baseline_observation_id"] and health["baseline_value"] is not None
            else "missing",
            "value": health["baseline_value"],
            "relative_deviation": health["relative_deviation"],
            "baseline_observation_id": health["baseline_observation_id"],
            "baseline_snapshot_id": health["baseline_snapshot_id"],
            "trend": health.get("trend"),
        }
        basis["threshold_binding"] = {
            "status": "missing"
            if any("threshold" in code for code in health["missing"])
            else "resolved",
            "source_kind": "device_output",
            "limits": health["thresholds"],
        }
        resolved = health["status"] != "unknown"
        blocker = health["missing"][0] if health["missing"] else None
        basis["policy_resolution"] = {
            "status": "resolved" if resolved else "missing",
            "reason_code": blocker,
        }
        basis["digest"] = canonical_digest(basis)
        return AssessmentBasisResolution(
            status="resolved" if resolved else "missing",
            blocker_code=blocker,
            effective_condition={
                **condition,
                "kind": "numeric_health_result",
                "unhealthy": health["status"] == "abnormal",
            },
            should_evaluate=health["status"] == "abnormal",
            basis=basis,
        )
    relevant_policy_roles = {str(role)} if role else set()
    if expected_requirement.get("required"):
        relevant_policy_roles.add("expected_state")
    relevant_invalid_sources = [
        dict(item)
        for item in snapshot.get("invalid_sources") or []
        if isinstance(item, Mapping)
        and (
            not item.get("observation_type")
            or str(item.get("observation_type")) == observation_type
        )
        and (not item.get("policy_role") or str(item.get("policy_role")) in relevant_policy_roles)
        and _entity_scope_matches(dict(item), observation)
    ]
    if strict and relevant_invalid_sources:
        basis["policy_resolution"] = {
            "status": "conflict",
            "reason_code": "assessment_policy_source_invalid",
            "conflicts": relevant_invalid_sources,
        }
        basis["digest"] = canonical_digest(basis)
        return AssessmentBasisResolution(
            status="conflict",
            blocker_code="assessment_policy_source_invalid",
            effective_condition=condition,
            should_evaluate=False,
            basis=basis,
        )

    if role is not None and operator is not None:
        candidates = _device_output_candidates(rule, observation, role)
        candidates.extend(
            _source_candidates(snapshot, observation, role=role)
        )
        if legacy_sources_allowed:
            candidates.extend(
                _catalog_threshold_candidates(snapshot, observation, role)
            )
        if not strict or legacy_rule_fallback_allowed:
            candidates.extend(_legacy_rule_candidate(rule, role))
        state, selected, conflicts = _choose_binding(candidates)
        if state != "resolved" or selected is None:
            blocker = (
                "assessment_policy_conflict"
                if state == "conflict"
                else "assessment_policy_missing"
            )
            basis["threshold_binding"] = {
                "status": state,
                "threshold_role": role,
                "conflicts": conflicts,
                "considered_sources": _candidate_audit(
                    candidates,
                    selected=None,
                ),
            }
            basis["policy_resolution"] = {
                "status": state,
                "reason_code": blocker,
            }
            basis["digest"] = canonical_digest(basis)
            return AssessmentBasisResolution(
                status=state,
                blocker_code=blocker,
                effective_condition=condition,
                should_evaluate=False,
                basis=basis,
            )
        contract_requirement = dict(
            dict(dict(snapshot.get("contract") or {}).get("thresholds") or {}).get(
                observation_type
            )
            or {}
        )
        required_unit = _unit(
            contract_requirement.get("unit") or observation.get("unit")
        )
        selected_unit = _unit(selected.get("unit"))
        if strict and required_unit and selected_unit != required_unit:
            basis["threshold_binding"] = {
                "status": "conflict",
                **selected,
                "required_unit": required_unit,
                "selected_unit": selected_unit or None,
                "considered_sources": _candidate_audit(
                    candidates,
                    selected=selected,
                ),
            }
            basis["policy_resolution"] = {
                "status": "conflict",
                "reason_code": "assessment_policy_unit_mismatch",
            }
            basis["digest"] = canonical_digest(basis)
            return AssessmentBasisResolution(
                status="conflict",
                blocker_code="assessment_policy_unit_mismatch",
                effective_condition=condition,
                should_evaluate=False,
                basis=basis,
            )
        value = float(selected["value"])
        effective = {
            **condition,
            "kind": "threshold" if operator == "gte" else "threshold_lte",
            operator: value,
            "threshold_role": role,
        }
        for key in ("reference_field", "fallback_reference_fields"):
            effective.pop(key, None)
        basis["threshold_binding"] = {
            "status": "resolved",
            **selected,
            "considered_sources": _candidate_audit(
                candidates,
                selected=selected,
            ),
        }
        basis["evidence_refs"] = _strings(
            [*evidence_refs, *selected.get("evidence_refs", [])]
        )
        condition = effective

    if kind == "linear_power_baseline_loss":
        context = dict(observation.get("context") or {})
        identity_fields = _strings(condition.get("identity_fields"))
        baseline_context = dict(context.get("baseline_context") or {})
        missing_identity = [
            field
            for field in identity_fields
            if not str(context.get(field) or "").strip()
            or not str(baseline_context.get(field) or "").strip()
        ]
        changed_identity = [
            field
            for field in identity_fields
            if field not in missing_identity
            and str(context.get(field)).strip() != str(baseline_context.get(field)).strip()
        ]
        baseline_value = _number(context.get("baseline_value"))
        current_value = _number(basis.get("observed_value"))
        if changed_identity:
            blocker = "baseline_module_identity_changed"
        elif missing_identity:
            blocker = "baseline_module_identity_missing"
        elif baseline_value is None or current_value is None:
            blocker = "baseline_reference_missing"
        else:
            blocker = ""
        if blocker:
            basis["baseline_binding"] = {
                "status": "missing",
                "reason_code": blocker,
                "identity_fields": identity_fields,
                "missing_identity_fields": missing_identity,
                "changed_identity_fields": changed_identity,
                "baseline_observation_id": context.get("baseline_observation_id"),
                "baseline_snapshot_id": context.get("baseline_snapshot_id"),
            }
            basis["policy_resolution"] = {
                "status": "missing",
                "reason_code": blocker,
            }
            basis["digest"] = canonical_digest(basis)
            return AssessmentBasisResolution(
                status="missing",
                blocker_code=blocker,
                effective_condition=condition,
                should_evaluate=False,
                basis=basis,
            )
        baseline_mw = math.pow(10.0, float(baseline_value) / 10.0)
        current_mw = math.pow(10.0, float(current_value) / 10.0)
        loss_ratio = (baseline_mw - current_mw) / baseline_mw
        max_loss_ratio = float(condition.get("max_loss_ratio") or 0.10)
        condition = {
            **condition,
            "kind": "linear_power_loss_ratio",
            "loss_ratio": loss_ratio,
            "gt": max_loss_ratio,
        }
        basis["baseline_binding"] = {
            "status": "resolved",
            "source_kind": "qualified_baseline_observation",
            "value": baseline_value,
            "unit": observation.get("unit"),
            "loss_ratio": loss_ratio,
            "max_loss_ratio": max_loss_ratio,
            "identity": {
                field: context.get(field) for field in identity_fields
            },
            "baseline_observation_id": context.get("baseline_observation_id"),
            "baseline_snapshot_id": context.get("baseline_snapshot_id"),
        }
        basis["evidence_refs"] = _strings(
            [
                *basis["evidence_refs"],
                context.get("baseline_observation_id"),
                context.get("baseline_snapshot_id"),
            ]
        )

    if expected_requirement.get("required"):
        expected_candidates = _source_candidates(
            snapshot,
            observation,
            expected=True,
        )
        defaults = _strings(expected_requirement.get("default_states"))
        if (
            legacy_sources_allowed
            and defaults
            and expected_requirement.get("allow_catalog_default")
        ):
            expected_candidates.append(
                {
                    "source_kind": "catalog_default",
                    "priority": SOURCE_PRIORITY["catalog_default"],
                    "policy_id": snapshot.get("check_item_id"),
                    "version": snapshot.get("check_item_version_id"),
                    "digest": snapshot.get("contract_digest"),
                    "expected_states": defaults,
                    "evidence_refs": [
                        str(snapshot.get("check_item_version_id") or "")
                    ],
                }
            )
        state, selected, conflicts = _choose_binding(
            expected_candidates,
            expected=True,
        )
        if state != "resolved" or selected is None:
            blocker = (
                "assessment_policy_conflict"
                if state == "conflict"
                else "expected_state_missing"
            )
            basis["expected_state_binding"] = {
                "status": state,
                "conflicts": conflicts,
                "considered_sources": _candidate_audit(
                    expected_candidates,
                    selected=None,
                ),
            }
            basis["policy_resolution"] = {
                "status": state,
                "reason_code": blocker,
            }
            basis["digest"] = canonical_digest(basis)
            return AssessmentBasisResolution(
                status=state,
                blocker_code=blocker,
                effective_condition=condition,
                should_evaluate=False,
                basis=basis,
            )
        expected_states = _strings(selected.get("expected_states"))
        basis["expected_state_binding"] = {
            "status": "resolved",
            **selected,
            "expected_states": expected_states,
            "considered_sources": _candidate_audit(
                expected_candidates,
                selected=selected,
            ),
        }
        basis["evidence_refs"] = _strings(
            [*basis["evidence_refs"], *selected.get("evidence_refs", [])]
        )
        observed = _text(basis["observed_value"])
        should_evaluate = observed not in {_text(item) for item in expected_states}
    else:
        should_evaluate = True

    basis["policy_resolution"] = {"status": "resolved"}
    basis["digest"] = canonical_digest(basis)
    return AssessmentBasisResolution(
        status="resolved",
        blocker_code=None,
        effective_condition=condition,
        should_evaluate=should_evaluate,
        basis=basis,
    )


def build_assessment_basis(
    *,
    entries: Iterable[Mapping[str, Any]],
    policy_snapshot_digest: str | None,
    runtime_bundle_version_id: str | None,
    runtime_bundle_digest: str | None,
    applicability_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = sorted(
        (dict(item) for item in entries),
        key=lambda item: (
            str(dict(item.get("entity_identity") or {}).get("entity_type") or ""),
            str(dict(item.get("entity_identity") or {}).get("entity_key") or ""),
            str(item.get("observation_type") or ""),
            str(dict(item.get("rule_binding") or {}).get("rule_version_id") or ""),
        ),
    )
    status_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for item in rows:
        status = str(dict(item.get("policy_resolution") or {}).get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        for binding_key in (
            "threshold_binding",
            "expected_state_binding",
            "baseline_binding",
        ):
            source = str(dict(item.get(binding_key) or {}).get("source_kind") or "")
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1
    payload = {
        "schema_version": ASSESSMENT_BASIS_SCHEMA,
        "policy_snapshot_digest": policy_snapshot_digest,
        "runtime_bundle_version_id": runtime_bundle_version_id,
        "runtime_bundle_digest": runtime_bundle_digest,
        "applicability_decision": dict(applicability_decision or {}),
        "summary": {
            "entry_count": len(rows),
            "status_counts": dict(sorted(status_counts.items())),
            "source_counts": dict(sorted(source_counts.items())),
        },
        "entries": rows,
    }
    payload["digest"] = canonical_digest(payload)
    return payload


__all__ = [
    "ASSESSMENT_BASIS_SCHEMA",
    "ASSESSMENT_POLICY_SNAPSHOT_SCHEMA",
    "AssessmentBasisResolution",
    "SOURCE_PRIORITY",
    "build_assessment_basis",
    "build_target_assessment_policy_snapshot",
    "canonical_digest",
    "normalize_assessment_policy_contract",
    "resolve_assessment_basis",
    "validate_assessment_basis",
    "validate_assessment_policy_snapshot",
]
