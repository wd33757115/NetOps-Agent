"""Compile validated observation semantics into rule-generation targets.

This module deliberately knows nothing about vendors, device models, commands,
field names, alarm names, or product-specific thresholds. Those facts belong
to approved contracts and sampled structured data.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from src.apps.rule_governance.backend.rule_control.asset_requirements import (
    normalize_rule_asset_requirements,
)
from src.core.observation.record_set_relations import (
    apply_record_set_relations,
    required_threshold_reference_errors,
)
from src.core.observation.semantic_projection import (
    SemanticProjectionError,
    dimension_schema_signature,
    normalize_dimensions,
)

COMPARABLE_SEMANTIC_TYPES = frozenset(
    {
        "boolean",
        "counter",
        "enum",
        "gauge",
        "percentage",
        "raw_hash",
        "state",
        "text",
    }
)
SEMANTIC_TYPE_ALIASES = {
    "driver_version": "text",
    "firmware_version": "text",
    "hardware_version": "text",
    "software_version": "text",
    "state_reason": "text",
    "uptime": "text",
}
NON_TARGET_ROLES = frozenset({"dimension", "identity", "metadata", "reference", "threshold"})
REFERENCE_MARKERS = frozenset(
    {"alarm", "limit", "reference", "threshold", "warn", "warning"}
)
REFERENCE_ROLE_TOKENS = frozenset(
    {
        "alarm",
        "critical",
        "high",
        "limit",
        "low",
        "lower",
        "max",
        "maximum",
        "min",
        "minimum",
        "reference",
        "threshold",
        "upper",
        "warn",
        "warning",
    }
)
MEASUREMENT_SCOPE_TOKENS = frozenset(
    {"channel", "current", "measured", "measurement", "module", "sensor", "value"}
)
EVENT_ENABLED_ROLES = frozenset(
    {"alarm", "fault", "fault_state", "health", "health_state", "threshold"}
)
EVENT_DISABLED_ROLES = frozenset(
    {"change_only", "configuration", "informational", "inventory", "none"}
)
CHANGE_EVENT_ROLES = frozenset({"change", "change_only", "lifecycle_change"})
STATIC_CAPACITY_ROLES = frozenset({"capacity", "inventory_capacity"})


def normalize_semantic_type(value: Any) -> str:
    """Return the executable comparison type for a business semantic label."""

    semantic_type = str(value or "text").strip().casefold()
    return SEMANTIC_TYPE_ALIASES.get(semantic_type, semantic_type)


def _literal_dimension_selector(dimensions: Any) -> dict[str, Any]:
    declared = normalize_dimensions(dimensions)
    return {
        name: source["literal"]
        for name, source in declared.items()
        if "literal" in source
    }


def normalize_contract_semantics(contract: dict[str, Any]) -> dict[str, Any]:
    """Preserve business meaning while compiling semantics for runtime policies."""

    output = dict(contract)
    semantics = dict(output.get("state_semantics") or {})
    normalized: dict[str, Any] = {}
    for field, raw_metadata in semantics.items():
        if not isinstance(raw_metadata, dict):
            normalized[str(field)] = raw_metadata
            continue
        metadata = dict(raw_metadata)
        original = str(metadata.get("semantic_type") or "text").strip().casefold()
        executable = normalize_semantic_type(original)
        if executable != original:
            metadata.setdefault("domain_semantic_type", original)
            metadata["semantic_type"] = executable
        normalized[str(field)] = metadata
    output["state_semantics"] = normalized
    return output
HEALTHY_STATE_VALUES = frozenset(
    {"active", "available", "connected", "healthy", "normal", "ok", "present", "up"}
)
FAULT_STATE_VALUES = frozenset(
    {
        "absent",
        "critical",
        "degraded",
        "disconnected",
        "down",
        "error",
        "failed",
        "fault",
        "unavailable",
    }
)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _scalar_values(value: Any) -> list[Any]:
    if _is_empty(value):
        return []
    if isinstance(value, list):
        values: list[Any] = []
        for item in value:
            values.extend(_scalar_values(item))
        return values
    if isinstance(value, dict):
        return []
    return [value]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _event_generation_decision(
    *,
    field_semantics: dict[str, Any],
    values: list[Any],
) -> tuple[bool, str]:
    """Conservatively separate change tracking from event generation."""

    explicit = field_semantics.get("event_generation")
    if explicit is False:
        return False, "explicit_contract_disabled"
    event_role = str(field_semantics.get("event_role") or "").strip().casefold()
    if field_semantics.get("threshold_references"):
        return True, "structured_threshold_reference"
    constraints = field_semantics.get("policy_constraints")
    if isinstance(constraints, dict) and any(
        key in constraints
        for key in (
            "thresholds",
            "high_threshold",
            "low_threshold",
            "critical_threshold",
            "recovery_threshold",
            "required_thresholds",
            "required_recovery_threshold",
            "required_deadband",
        )
    ):
        return True, "approved_threshold_constraint"

    semantic_type = str(field_semantics.get("semantic_type") or "").strip().casefold()
    if event_role in CHANGE_EVENT_ROLES:
        if explicit is True:
            return True, "explicit_change_event"
        return False, f"event_role:{event_role}"
    if event_role in STATIC_CAPACITY_ROLES:
        return False, "static_capacity_has_no_threshold_evidence"
    if event_role in EVENT_DISABLED_ROLES:
        return False, f"event_role:{event_role}"
    if semantic_type in {"gauge", "percentage", "counter"}:
        return False, "numeric_event_has_no_threshold_evidence"
    if semantic_type not in {"boolean", "enum", "state"}:
        return False, "event_semantics_not_executable"
    value_mapping = {
        str(key).strip().casefold(): str(value).strip().casefold()
        for key, value in dict(field_semantics.get("value_mapping") or {}).items()
        if str(value).strip()
    }
    canonical_values = {
        value_mapping.get(str(value).strip().casefold(), str(value).strip().casefold())
        for value in values
        if str(value).strip()
    }
    # A governed mapping may describe the failure state even when the current
    # sample is healthy. It is still stronger evidence than an LLM-created name.
    canonical_values.update(value_mapping.values())
    has_healthy = bool(canonical_values.intersection(HEALTHY_STATE_VALUES))
    has_fault = bool(canonical_values.intersection(FAULT_STATE_VALUES))
    if has_healthy and has_fault:
        return True, "governed_state_polarity"
    return False, "state_has_no_fault_health_polarity"


def _policy_constraints_apply(
    constraints: dict[str, Any],
    *,
    dimension_selector: dict[str, Any],
) -> bool:
    """Apply an approved policy only to its declared metric dimensions."""

    expected = constraints.get("applies_to_dimensions")
    if not isinstance(expected, dict) or not expected:
        return True
    for name, allowed_raw in expected.items():
        actual = dimension_selector.get(str(name))
        allowed = allowed_raw if isinstance(allowed_raw, list) else [allowed_raw]
        if actual not in allowed:
            return False
    return True


def event_generation_decision(
    field_semantics: dict[str, Any],
    values: list[Any] | None = None,
) -> tuple[bool, str]:
    """Expose the shared, vendor-neutral EventRule eligibility decision."""

    return _event_generation_decision(
        field_semantics=dict(field_semantics or {}),
        values=list(values or []),
    )


def profile_fields(
    records: list[dict[str, Any]],
    *,
    field_inventory: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return deterministic, privacy-safe field statistics for a weak LLM."""

    inventory = field_inventory or sorted(
        {
            str(field)
            for record in records
            if isinstance(record, dict)
            for field in record
            if str(field).strip()
        }
    )
    profiles: dict[str, dict[str, Any]] = {}
    for field in inventory:
        values = [
            item
            for record in records
            if isinstance(record, dict)
            for item in _scalar_values(record.get(field))
        ]
        numbers = [number for item in values if (number := _number(item)) is not None]
        types = Counter(type(item).__name__ for item in values)
        distinct = {str(item) for item in values}
        profile: dict[str, Any] = {
            "record_count": len(records),
            "populated_record_count": sum(
                1
                for record in records
                if isinstance(record, dict) and not _is_empty(record.get(field))
            ),
            "value_count": len(values),
            "numeric_count": len(numbers),
            "distinct_count": len(distinct),
            "value_types": dict(sorted(types.items())),
            "list_valued": any(
                isinstance(record.get(field), list)
                for record in records
                if isinstance(record, dict)
            ),
        }
        if numbers:
            ordered = sorted(numbers)
            profile.update(
                minimum=ordered[0],
                maximum=ordered[-1],
                median=ordered[len(ordered) // 2],
            )
        if values and len(distinct) <= 20:
            profile["observed_values"] = sorted(distinct)
        profiles[field] = profile
    return profiles


def _semantic_tokens(field_name: str) -> set[str]:
    aliases = {
        "temp": "temperature",
        "volt": "voltage",
    }
    tokens = {
        aliases.get(token, token)
        for token in str(field_name).casefold().replace("-", "_").split("_")
        if token
    }
    return tokens


def _reference_descriptor(
    field_name: str,
    semantics: dict[str, Any],
) -> dict[str, Any] | None:
    tokens = _semantic_tokens(field_name)
    comparison_role = str(semantics.get("comparison_role") or "").casefold()
    explicit_role = str(semantics.get("threshold_role") or "").casefold()
    governed = comparison_role in {"reference", "threshold"} or bool(explicit_role)
    if not governed and not tokens.intersection(REFERENCE_MARKERS):
        return None
    direction = str(semantics.get("threshold_direction") or "").casefold()
    if not direction:
        if tokens.intersection({"low", "lower", "min", "minimum"}):
            direction = "low"
        elif tokens.intersection({"high", "upper", "max", "maximum"}):
            direction = "high"
    if direction not in {"low", "high"}:
        return None
    level = str(semantics.get("threshold_level") or "").casefold()
    if not level:
        if "warning" in tokens or "warn" in tokens:
            level = "warning"
        elif "alarm" in tokens or "critical" in tokens:
            level = "alarm"
        else:
            level = "reference"
    return {
        "field_name": field_name,
        "direction": direction,
        "level": level,
        "target_field": str(semantics.get("target_field") or "").strip() or None,
        "base_tokens": tokens - REFERENCE_ROLE_TOKENS,
    }


def enrich_contract_reference_semantics(
    *,
    contract: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach generic measurement-to-reference relations to a Contract."""

    output = dict(contract)
    semantics = {
        str(field): dict(metadata) if isinstance(metadata, dict) else {}
        for field, metadata in dict(output.get("state_semantics") or {}).items()
    }
    profiles = profile_fields(records, field_inventory=sorted(semantics))
    targets = [
        field
        for field, metadata in semantics.items()
        if str(metadata.get("observation_type") or "").strip()
        and str(metadata.get("semantic_type") or "").casefold()
        in {"gauge", "percentage"}
    ]
    references = [
        descriptor
        for field, metadata in semantics.items()
        if profiles.get(field, {}).get("numeric_count", 0)
        and (descriptor := _reference_descriptor(field, metadata)) is not None
    ]

    matched: dict[str, list[dict[str, Any]]] = {field: [] for field in targets}
    for reference in references:
        explicit_target = reference.get("target_field")
        candidates: list[tuple[float, str]] = []
        for target in targets:
            if explicit_target and explicit_target != target:
                continue
            target_metadata = semantics[target]
            reference_metadata = semantics[reference["field_name"]]
            target_unit = str(target_metadata.get("unit") or "").casefold()
            reference_unit = str(reference_metadata.get("unit") or "").casefold()
            if target_unit and reference_unit and target_unit != reference_unit:
                continue
            target_tokens = _semantic_tokens(target) - MEASUREMENT_SCOPE_TOKENS
            overlap = target_tokens.intersection(reference["base_tokens"])
            if not overlap:
                continue
            denominator = max(1, len(reference["base_tokens"] | target_tokens))
            score = len(overlap) / denominator
            if target_unit and target_unit == reference_unit:
                score += 0.25
            if explicit_target == target:
                score += 1.0
            if score >= 0.5:
                candidates.append((score, target))
        if candidates:
            candidates.sort(key=lambda item: (-item[0], item[1]))
            matched[candidates[0][1]].append(reference)

    level_order = {"warning": 0, "reference": 1, "alarm": 2}
    for target, related in matched.items():
        if not related:
            continue
        target_metadata = semantics[target]
        compiled: dict[str, dict[str, Any]] = {}
        for direction in ("low", "high"):
            directional = [item for item in related if item["direction"] == direction]
            directional.sort(
                key=lambda item: (
                    level_order.get(str(item.get("level")), 1),
                    str(item["field_name"]),
                )
            )
            if not directional:
                continue
            primary = directional[0]
            primary_role = (
                f"critical_{direction}"
                if primary.get("level") == "alarm"
                else direction
            )
            compiled[primary_role] = {
                "field_name": primary["field_name"],
                "context_key": f"threshold.{primary_role}",
                "level": primary["level"],
                "source": "structured_reference",
            }
            alarm = next(
                (
                    item
                    for item in directional[1:]
                    if item.get("level") == "alarm"
                ),
                None,
            )
            if alarm:
                compiled[f"critical_{direction}"] = {
                    "field_name": alarm["field_name"],
                    "context_key": f"threshold.critical_{direction}",
                    "level": alarm["level"],
                    "source": "structured_reference",
                }
        if not compiled:
            continue
        context_fields = {
            str(item)
            for item in target_metadata.get("context_fields") or []
            if str(item).strip()
        }
        context_fields.update(
            str(item["field_name"])
            for item in compiled.values()
        )
        target_metadata["threshold_references"] = compiled
        target_metadata["context_fields"] = sorted(context_fields)
        semantics[target] = target_metadata
        for role, relation in compiled.items():
            reference_metadata = semantics[relation["field_name"]]
            reference_metadata.update(
                comparison_role="threshold",
                monitor=False,
                target_field=target,
                threshold_role=role,
                context_key=relation["context_key"],
                threshold_direction="low" if "low" in role else "high",
                threshold_level=relation["level"],
            )
            semantics[relation["field_name"]] = reference_metadata
    output["state_semantics"] = semantics
    return output


def _multi_source_target_signature(target: dict[str, Any]) -> str:
    semantics = dict(target.get("field_semantics") or {})
    references = {
        str(role): {
            "context_key": str(
                reference.get("context_key")
                or reference.get("field_name")
                or ""
            ),
            "level": str(reference.get("level") or "reference"),
        }
        for role, reference in dict(
            target.get("threshold_references") or {}
        ).items()
        if isinstance(reference, dict)
    }
    payload = {
        "observation_type": target.get("observation_type"),
        "canonical_entity_type": (
            (target.get("event_contract") or {}).get(
                "canonical_entity_type"
            )
            or (target.get("event_contract") or {}).get("entity_type")
        ),
        "entity_identity_fields": (
            (target.get("event_contract") or {}).get(
                "entity_identity_fields"
            )
            or []
        ),
        "dimension_selector": target.get("dimension_selector") or {},
        "semantic_type": semantics.get("semantic_type"),
        "domain_semantic_type": semantics.get("domain_semantic_type"),
        "unit": semantics.get("canonical_unit") or semantics.get("unit"),
        "applicability": semantics.get("applicability") or {},
        "policy_constraints": target.get("policy_constraints") or {},
        "event_constraints": target.get("event_constraints") or {},
        "event_generation": target.get("event_generation"),
        "threshold_references": references,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _event_semantics_signature(metadata: Any) -> str:
    normalized = dict(metadata or {}) if isinstance(metadata, dict) else {}
    references = {}
    for role, raw_reference in dict(
        normalized.get("threshold_references") or {}
    ).items():
        if not isinstance(raw_reference, dict):
            continue
        references[str(role)] = {
            key: raw_reference.get(key)
            for key in (
                "field_name",
                "context_key",
                "level",
                "source",
                "match_policy",
            )
            if raw_reference.get(key) is not None
        }
    if references:
        normalized["threshold_references"] = references
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _merge_event_contract_views(
    members: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    contracts = [
        dict(item.get("event_contract") or {})
        for item in members
        if isinstance(item.get("event_contract"), dict)
    ]
    if not contracts:
        return {}, ["multi-source target has no event Contract view"]
    runtime_entities = {
        str(
            contract.get("canonical_entity_type")
            or contract.get("entity_type")
            or ""
        ).strip()
        for contract in contracts
    }
    runtime_entities.discard("")
    identity_signatures = {
        tuple(
            str(item)
            for item in contract.get("entity_identity_fields")
            or contract.get("primary_keys")
            or []
        )
        for contract in contracts
    }
    errors: list[str] = []
    if len(runtime_entities) != 1:
        errors.append(
            "multi-source event Contract views use different canonical entity types"
        )
    if len(identity_signatures) != 1:
        errors.append(
            "multi-source event Contract views use different canonical identities"
        )

    output = dict(contracts[0])
    output["required_fields"] = list(
        dict.fromkeys(
            str(field)
            for contract in contracts
            for field in contract.get("required_fields") or []
            if str(field).strip()
        )
    )
    output["optional_fields"] = list(
        dict.fromkeys(
            str(field)
            for contract in contracts
            for field in contract.get("optional_fields") or []
            if str(field).strip()
            and str(field) not in output["required_fields"]
        )
    )
    validators: dict[str, Any] = {}
    semantics: dict[str, Any] = {}
    for contract in contracts:
        for field, validator in dict(contract.get("validators") or {}).items():
            field = str(field)
            if field in validators and validators[field] != validator:
                errors.append(
                    f"multi-source event Contract validator conflict for {field}"
                )
            else:
                validators[field] = validator
        for field, metadata in dict(
            contract.get("state_semantics") or {}
        ).items():
            field = str(field)
            if field in semantics and _event_semantics_signature(
                semantics[field]
            ) != _event_semantics_signature(metadata):
                errors.append(
                    f"multi-source event Contract semantics conflict for {field}"
                )
            else:
                semantics[field] = metadata
    output["validators"] = validators
    output["state_semantics"] = semantics
    output["record_sets"] = []
    output["source_record_set_names"] = sorted(
        {
            str(item.get("record_set_name") or "")
            for item in members
            if str(item.get("record_set_name") or "")
        }
    )
    return output, list(dict.fromkeys(errors))


def _merge_multi_source_targets(
    targets: list[dict[str, Any]],
    *,
    historical_values: dict[str, list[Any]],
    field_profiles: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge parser-native sources that emit the same canonical observation."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for target in targets:
        grouped.setdefault(str(target.get("target_id") or ""), []).append(target)
    merged_targets: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for target_id, members in grouped.items():
        if len(members) == 1:
            merged_targets.append(members[0])
            continue
        signatures = {_multi_source_target_signature(item) for item in members}
        if len(signatures) != 1:
            blockers.append(
                {
                    "code": "incompatible_multi_source_target",
                    "target_id": target_id,
                    "observation_type": members[0].get("observation_type"),
                    "source_fields": sorted(
                        str(item.get("field_ref") or item.get("field_name") or "")
                        for item in members
                    ),
                    "message": (
                        "multiple Parser record sets emit the same canonical "
                        "observation with incompatible policy semantics"
                    ),
                }
            )
            merged_targets.append(members[0])
            continue
        representative = dict(members[0])
        event_contract, contract_errors = _merge_event_contract_views(
            members
        )
        if contract_errors:
            blockers.append(
                {
                    "code": "incompatible_multi_source_event_contract",
                    "target_id": target_id,
                    "observation_type": members[0].get(
                        "observation_type"
                    ),
                    "errors": contract_errors,
                    "message": (
                        "multiple Parser record sets cannot share one EventRule "
                        "because their canonical Contract views differ"
                    ),
                }
            )
        source_targets = [
            {
                "record_set_name": str(item.get("record_set_name") or ""),
                "field_name": str(item.get("field_name") or ""),
                "field_ref": str(item.get("field_ref") or ""),
                "field_profile": dict(item.get("field_profile") or {}),
            }
            for item in members
        ]
        combined_values = [
            value
            for item in source_targets
            for value in historical_values.get(item["field_ref"], [])
        ]
        canonical_ref = f"canonical:{target_id}"
        historical_values[canonical_ref] = combined_values
        combined_profile = profile_fields(
            [{"value": value} for value in combined_values],
            field_inventory=["value"],
        ).get("value", {})
        field_profiles[canonical_ref] = combined_profile
        representative.update(
            field_ref=canonical_ref,
            record_set_name=None,
            source_targets=source_targets,
            source_record_set_names=sorted(
                {
                    item["record_set_name"]
                    for item in source_targets
                    if item["record_set_name"]
                }
            ),
            field_profile=combined_profile,
            event_contract=event_contract,
        )
        merged_targets.append(representative)
    return merged_targets, blockers


def _rule_asset_targets(
    targets: list[dict[str, Any]],
    requirements: dict[str, Any],
) -> list[dict[str, Any]]:
    """Select every observation that needs a CurrentState or change EventRule."""

    current_state_types = set(
        requirements.get("current_state_observation_types") or []
    )
    selected: list[dict[str, Any]] = []
    for target in targets:
        observation_type = str(target.get("observation_type") or "")
        roles: list[str] = []
        if observation_type in current_state_types:
            roles.append("current_state")
        # A field-level event hint can select a target only when the check-item
        # requirement profile authorizes change-driven EventRules.  Current-state
        # only profiles deliberately reuse the same state polarity metadata, but
        # must not silently grow transition/recovery roles.
        if (
            requirements.get("requires_event_rule") is True
            and target.get("event_generation") is True
        ):
            roles.append("change_event")
        if roles:
            selected.append(
                {
                    **target,
                    "required_rule_roles": roles,
                }
            )
    return selected


def _scope_rule_asset_requirements(
    requirements: dict[str, Any],
    *,
    policy_targets: list[dict[str, Any]],
    event_targets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Scope catalog requirements to observations this implementation can emit.

    Check-item requirements describe the complete business capability. A concrete
    command may expose only a subset of the required metric dimensions (for
    example, instant CPU rather than a governed five-minute window). Downstream
    generation must not invent unavailable event semantics merely to satisfy the
    catalog-wide profile.
    """

    normalized = normalize_rule_asset_requirements(requirements)
    available_observation_types = {
        str(target.get("observation_type") or "").strip()
        for target in policy_targets
        if str(target.get("observation_type") or "").strip()
    }
    current_state_types = {
        str(target.get("observation_type") or "").strip()
        for target in event_targets
        if "current_state" in set(target.get("required_rule_roles") or [])
        and str(target.get("observation_type") or "").strip()
    }
    change_event_types = {
        str(target.get("observation_type") or "").strip()
        for target in event_targets
        if "change_event" in set(target.get("required_rule_roles") or [])
        and str(target.get("observation_type") or "").strip()
    }
    declared_change_types = set(
        normalized.get("change_policy_observation_types") or []
    )
    effective_change_types = (
        available_observation_types & declared_change_types
        if declared_change_types
        else available_observation_types
    )
    declared_event_types = dict(
        normalized.get("required_event_types_by_observation") or {}
    )
    effective_event_types = {
        observation_type: list(declared_event_types[observation_type])
        for observation_type in sorted(change_event_types)
        if observation_type in declared_event_types
    }

    requires_current = bool(
        normalized.get("requires_current_state_rule") and current_state_types
    )
    requires_change = bool(
        normalized.get("requires_change_policy") and effective_change_types
    )
    requires_event = bool(
        normalized.get("requires_event_rule") and change_event_types
    )
    required_asset_types = {
        str(item)
        for item in normalized.get("required_asset_types") or []
        if str(item)
        not in {"current_state_rule", "change_policy", "event_rule"}
    }
    if requires_current:
        required_asset_types.add("current_state_rule")
    if requires_change:
        required_asset_types.add("change_policy")
    if requires_event:
        required_asset_types.add("event_rule")

    suppressed_event_types = {
        observation_type: list(event_types)
        for observation_type, event_types in sorted(declared_event_types.items())
        if observation_type not in change_event_types
    }
    return {
        **normalized,
        "required_asset_types": sorted(required_asset_types),
        "requires_change_policy": requires_change,
        "requires_current_state_rule": requires_current,
        "requires_event_rule": requires_event,
        "requires_event_rule_assets": requires_current or requires_event,
        "requires_historical_replay": bool(
            requires_event and normalized.get("requires_historical_replay")
        ),
        "current_state_observation_types": sorted(current_state_types),
        "change_policy_observation_types": sorted(effective_change_types),
        "required_event_types_by_observation": effective_event_types,
        "suppressed_event_types_by_observation": suppressed_event_types,
        "scope_reason": "compiled_from_observed_fields_and_dimensions",
    }


def compile_rule_targets(
    *,
    contract: dict[str, Any],
    records: list[dict[str, Any]],
    record_groups: list[dict[str, Any]] | None = None,
    rule_asset_requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a validated Contract into deterministic ChangePolicy targets."""

    from src.core.observation.semantic_projection import project_value

    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError("records must be a list of objects")
    requirements = normalize_rule_asset_requirements(rule_asset_requirements)
    if not any(
        (
            requirements["requires_change_policy"],
            requirements["requires_current_state_rule"],
            requirements["requires_event_rule_assets"],
        )
    ):
        return {
            "success": True,
            "policy_targets": [],
            "event_policy_targets": [],
            "event_observation_types": [],
            "historical_values_by_field": {},
            "field_profiles": profile_fields(records),
            "contract": contract,
            "unmapped_comparable_fields": [],
            "blockers": [],
            "field_name": None,
            "observation_type": None,
            "planning_mode": "observation_contract_only",
            "rule_asset_requirements": requirements,
        }
    raw_record_sets = [
        dict(item)
        for item in contract.get("record_sets") or []
        if isinstance(item, dict)
    ]
    if raw_record_sets:
        targets: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        unmapped: list[dict[str, Any]] = []
        historical_values: dict[str, list[Any]] = {}
        field_profiles: dict[str, Any] = {}
        normalized_sets: list[dict[str, Any]] = []
        relation_validation = apply_record_set_relations(
            contract=contract,
            record_groups=(
                record_groups
                if record_groups is not None
                else [{"group_id": "default", "records": records}]
            ),
        )
        contract = dict(relation_validation.get("contract") or contract)
        blockers.extend(
            {
                "code": "record_set_relation_invalid",
                "message": str(message),
            }
            for message in relation_validation.get("errors") or []
        )
        raw_record_sets = [
            dict(item)
            for item in contract.get("record_sets") or []
            if isinstance(item, dict)
        ]
        for record_set in raw_record_sets:
            name = str(record_set.get("name") or "").strip()
            if not name:
                blockers.append(
                    {
                        "code": "record_set_name_missing",
                        "message": "multi-record contract contains an unnamed record set",
                    }
                )
                continue
            set_records = [
                {
                    str(key): value
                    for key, value in record.items()
                    if str(key) != "__record_set__"
                }
                for record in records
                if str(record.get("__record_set__") or "") == name
            ]
            result = compile_rule_targets(
                contract={**record_set, "record_sets": []},
                records=set_records,
                rule_asset_requirements=rule_asset_requirements,
            )
            normalized_set = dict(result.get("contract") or record_set)
            normalized_set["name"] = name
            normalized_sets.append(normalized_set)
            for target in result.get("policy_targets") or []:
                target = dict(target)
                target["record_set_name"] = name
                target["field_ref"] = f"{name}.{target['field_name']}"
                target["event_contract"] = normalized_set
                targets.append(target)
            for blocker in result.get("blockers") or []:
                if blocker.get("code") == "no_policy_targets":
                    continue
                blockers.append({"record_set_name": name, **dict(blocker)})
            unmapped.extend(
                {"record_set_name": name, **dict(item)}
                for item in result.get("unmapped_comparable_fields") or []
            )
            for field, values in dict(
                result.get("historical_values_by_field") or {}
            ).items():
                historical_values[f"{name}.{field}"] = list(values)
            for field, profile in dict(result.get("field_profiles") or {}).items():
                field_profiles[f"{name}.{field}"] = profile
        targets, merge_blockers = _merge_multi_source_targets(
            targets,
            historical_values=historical_values,
            field_profiles=field_profiles,
        )
        blockers.extend(merge_blockers)
        if not targets:
            blockers.append(
                {
                    "code": "no_policy_targets",
                    "message": (
                        "validated multi-record contract contains no evidenced "
                        "comparable observation"
                    ),
                }
            )
        normalized_contract = {
            **contract,
            "record_sets": normalized_sets,
        }
        blockers.extend(
            required_threshold_reference_errors(normalized_contract)
        )
        event_targets = _rule_asset_targets(targets, requirements)
        effective_requirements = _scope_rule_asset_requirements(
            requirements,
            policy_targets=targets,
            event_targets=event_targets,
        )
        return {
            "success": not blockers,
            "policy_targets": targets,
            "event_policy_targets": event_targets,
            "event_observation_types": list(
                dict.fromkeys(
                    item["observation_type"] for item in event_targets
                )
            ),
            "historical_values_by_field": historical_values,
            "field_profiles": field_profiles,
            "contract": normalized_contract,
            "unmapped_comparable_fields": unmapped,
            "blockers": blockers,
            "record_set_relation_validation": {
                "success": relation_validation.get("success") is True,
                "reports": relation_validation.get("reports") or [],
            },
            "rule_asset_requirements": effective_requirements,
            "field_name": targets[0]["field_name"] if targets else None,
            "observation_type": (
                targets[0]["observation_type"] if targets else None
            ),
        }

    contract = normalize_contract_semantics(contract)
    contract = enrich_contract_reference_semantics(contract=contract, records=records)
    reference_blockers = required_threshold_reference_errors(contract)
    declared_fields = {
        str(field)
        for key in ("required_fields", "optional_fields")
        for field in contract.get(key) or []
        if str(field).strip()
    }
    semantics = contract.get("state_semantics") or {}
    if not isinstance(semantics, dict):
        raise ValueError("contract.state_semantics must be an object")
    profiles = profile_fields(records, field_inventory=sorted(declared_fields))
    targets: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = list(reference_blockers)
    unmapped: list[dict[str, Any]] = []
    historical_values_by_field: dict[str, list[Any]] = {}

    for field in sorted(declared_fields):
        raw_semantics = semantics.get(field)
        if not isinstance(raw_semantics, dict):
            continue
        field_semantics = dict(raw_semantics)
        semantic_type = str(field_semantics.get("semantic_type") or "").strip().casefold()
        observation_type = str(field_semantics.get("observation_type") or "").strip()
        comparison_role = str(field_semantics.get("comparison_role") or "").strip().casefold()
        monitor = field_semantics.get("monitor")

        if (
            field_semantics.get("ignore") is True
            or monitor is False
            or comparison_role in NON_TARGET_ROLES
            or semantic_type not in COMPARABLE_SEMANTIC_TYPES
        ):
            continue
        if not observation_type:
            unmapped.append(
                {
                    "field_name": field,
                    "semantic_type": semantic_type,
                    "comparison_role": comparison_role or None,
                    "reason": "observation_type_missing",
                }
            )
            if monitor is True or comparison_role in {"measurement", "observation", "state"}:
                blockers.append(
                    {
                        "code": "observation_type_missing",
                        "field_name": field,
                        "message": (
                            f"field {field} is marked for comparison but has no observation_type"
                        ),
                    }
                )
            continue

        values: list[Any] = []
        for record_index, record in enumerate(records):
            normalized_record = {
                **record,
                **{
                    str(name).strip().lower(): value
                    for name, value in record.items()
                },
            }
            for raw_value in _scalar_values(record.get(field)):
                try:
                    projected_value = project_value(
                        field_name=field,
                        source_value=raw_value,
                        normalized_record=normalized_record,
                        field_semantics=field_semantics,
                    )
                except SemanticProjectionError as exc:
                    blockers.append(
                        {
                            "code": "historical_value_projection_failed",
                            "field_name": field,
                            "observation_type": observation_type,
                            "record_index": record_index,
                            "message": str(exc),
                        }
                    )
                    continue
                values.extend(_scalar_values(projected_value))
        if not values:
            blockers.append(
                {
                    "code": "no_observed_values",
                    "field_name": field,
                    "observation_type": observation_type,
                    "message": f"field {field} has no observed value in the rule context",
                }
            )
            continue
        historical_values_by_field[field] = values
        target = {
            "field_name": field,
            "observation_type": observation_type,
            "field_semantics": field_semantics,
            "field_profile": profiles.get(field) or {},
        }
        try:
            dimensions = normalize_dimensions(field_semantics.get("dimensions"))
        except SemanticProjectionError as exc:
            blockers.append(
                {
                    "code": "invalid_dimensions",
                    "field_name": field,
                    "observation_type": observation_type,
                    "message": str(exc),
                }
            )
            continue
        target["dimensions"] = dimensions
        target["dimension_schema_signature"] = dimension_schema_signature(dimensions)
        target["dimension_selector"] = _literal_dimension_selector(dimensions)
        target["dimension_selector_signature"] = dimension_schema_signature(
            target["dimension_selector"]
        )
        target["target_id"] = (
            f"{observation_type}:{target['dimension_selector_signature']}"
        )
        raw_constraints = field_semantics.get("policy_constraints")
        constraints_apply = True
        if isinstance(raw_constraints, dict) and raw_constraints:
            constraints_apply = _policy_constraints_apply(
                raw_constraints,
                dimension_selector=target["dimension_selector"],
            )
            if constraints_apply:
                target["policy_constraints"] = raw_constraints
        references = field_semantics.get("threshold_references")
        if isinstance(references, dict) and references:
            target["threshold_references"] = {
                str(role): {
                    **dict(reference),
                    "field_profile": profiles.get(str(reference.get("field_name") or ""))
                    or {},
                }
                for role, reference in references.items()
                if isinstance(reference, dict)
            }
        if isinstance(raw_constraints, dict) and raw_constraints and not constraints_apply:
            event_generation = False
            event_reason = "policy_constraints_dimension_not_applicable"
        else:
            event_generation, event_reason = _event_generation_decision(
                field_semantics=field_semantics,
                values=values,
            )
        target["event_generation"] = event_generation
        target["event_generation_reason"] = event_reason
        event_constraints = field_semantics.get("event_constraints")
        if isinstance(event_constraints, dict) and event_constraints:
            target["event_constraints"] = event_constraints
        targets.append(target)

    observation_types = [item["observation_type"] for item in targets]
    duplicates = sorted(value for value, count in Counter(observation_types).items() if count > 1)
    for observation_type in duplicates:
        matching = [
            item for item in targets if item["observation_type"] == observation_type
        ]
        signatures = [
            str(item.get("dimension_selector_signature") or "") for item in matching
        ]
        repeated_signatures = sorted(
            value for value, count in Counter(signatures).items() if count > 1
        )
        if repeated_signatures:
            blockers.append(
                {
                    "code": "ambiguous_observation_dimensions",
                    "observation_type": observation_type,
                    "fields": [item["field_name"] for item in matching],
                    "duplicate_dimension_selector_signatures": repeated_signatures,
                    "message": (
                        f"observation_type {observation_type} maps to multiple fields "
                        "without distinct runtime dimension selectors"
                    ),
                }
            )
    if not targets:
        blockers.append(
            {
                "code": "no_policy_targets",
                "message": "validated contract contains no evidenced comparable observation",
            }
        )

    event_targets = _rule_asset_targets(targets, requirements)
    effective_requirements = _scope_rule_asset_requirements(
        requirements,
        policy_targets=targets,
        event_targets=event_targets,
    )
    return {
        "success": not blockers,
        "policy_targets": targets,
        "event_policy_targets": event_targets,
        "event_observation_types": list(
            dict.fromkeys(
                item["observation_type"]
                for item in event_targets
            )
        ),
        "historical_values_by_field": historical_values_by_field,
        "field_profiles": profiles,
        "contract": contract,
        "unmapped_comparable_fields": unmapped,
        "blockers": blockers,
        "rule_asset_requirements": effective_requirements,
        "field_name": targets[0]["field_name"] if targets else None,
        "observation_type": targets[0]["observation_type"] if targets else None,
    }
