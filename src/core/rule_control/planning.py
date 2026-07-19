"""Compile validated observation semantics into rule-generation targets.

This module deliberately knows nothing about vendors, device models, commands,
field names, alarm names, or product-specific thresholds. Those facts belong
to approved contracts and sampled structured data.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

COMPARABLE_SEMANTIC_TYPES = frozenset(
    {"boolean", "counter", "enum", "gauge", "percentage", "raw_hash", "state"}
)
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


def compile_rule_targets(
    *,
    contract: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compile a validated Contract into deterministic ChangePolicy targets."""

    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError("records must be a list of objects")

    contract = enrich_contract_reference_semantics(contract=contract, records=records)
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
    blockers: list[dict[str, Any]] = []
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

        values = [item for record in records for item in _scalar_values(record.get(field))]
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
        constraints = field_semantics.get("policy_constraints")
        if isinstance(constraints, dict) and constraints:
            target["policy_constraints"] = constraints
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
        targets.append(target)

    observation_types = [item["observation_type"] for item in targets]
    duplicates = sorted(value for value, count in Counter(observation_types).items() if count > 1)
    for observation_type in duplicates:
        blockers.append(
            {
                "code": "duplicate_observation_type",
                "observation_type": observation_type,
                "fields": [
                    item["field_name"]
                    for item in targets
                    if item["observation_type"] == observation_type
                ],
                "message": f"observation_type {observation_type} maps to multiple fields",
            }
        )
    if not targets:
        blockers.append(
            {
                "code": "no_policy_targets",
                "message": "validated contract contains no evidenced comparable observation",
            }
        )

    return {
        "success": not blockers,
        "policy_targets": targets,
        "historical_values_by_field": historical_values_by_field,
        "field_profiles": profiles,
        "contract": contract,
        "unmapped_comparable_fields": unmapped,
        "blockers": blockers,
        "field_name": targets[0]["field_name"] if targets else None,
        "observation_type": targets[0]["observation_type"] if targets else None,
    }
