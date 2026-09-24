"""Deterministic replay validation for event rule packs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.patrol.change_policy import normalize_dimension_selector
from src.core.patrol.event_rules import (
    EventRule,
    EventRuleDefinition,
    default_event_rule_definitions,
    default_event_rules,
    load_event_rule_pack,
)
from src.core.state.observation_applicability import (
    APPLICABILITY_CONTEXT_KEY,
    applicability_decision,
    applicable_context_example,
    context_from_change,
    excluded_context_example,
    validate_applicability,
)

PRIVATE_CHANGE_FIELDS = {
    "change_id",
    "current_observation_id",
    "current_snapshot_id",
    "detected_at",
    "device_id",
    "previous_observation_id",
    "previous_snapshot_id",
    "run_id",
}


@dataclass(frozen=True)
class EventRuleValidationCase:
    name: str
    change: dict[str, Any]
    expected_event_type: str | None = None
    expected_rule_id: str | None = None


def sanitize_change(change: dict[str, Any]) -> dict[str, Any]:
    """Return a validation-safe change payload without private identifiers."""
    return {key: value for key, value in change.items() if key not in PRIVATE_CHANGE_FIELDS}


def match_rule_set(change: dict[str, Any], rules: list[EventRule]) -> EventRule | None:
    for rule in rules:
        if rule.match(change):
            return rule
    return None


def matched_rules(change: dict[str, Any], rules: list[EventRule]) -> list[EventRule]:
    return [rule for rule in rules if rule.match(change)]


def validate_event_rule_set(
    cases: list[EventRuleValidationCase],
    *,
    rules: list[EventRule] | None = None,
) -> dict[str, Any]:
    active_rules = rules or default_event_rules()
    rows: list[dict[str, Any]] = []
    passed = 0
    for case in cases:
        matched = match_rule_set(case.change, active_rules)
        actual_event_type = matched.event_type if matched else None
        actual_rule_id = matched.rule_id if matched else None
        ok = actual_event_type == case.expected_event_type and (
            case.expected_rule_id is None or actual_rule_id == case.expected_rule_id
        )
        if ok:
            passed += 1
        rows.append(
            {
                "name": case.name,
                "passed": ok,
                "expected_event_type": case.expected_event_type,
                "actual_event_type": actual_event_type,
                "expected_rule_id": case.expected_rule_id,
                "actual_rule_id": actual_rule_id,
                "change": sanitize_change(case.change),
            }
        )
    total = len(cases)
    return {
        "success": passed == total,
        "case_count": total,
        "passed_count": passed,
        "failed_count": total - passed,
        "pass_rate": (passed / total) if total else 1.0,
        "rows": rows,
    }


def detect_rule_collisions(
    cases: list[EventRuleValidationCase],
    rules: list[EventRule],
    *,
    existing_rules: list[EventRule] | None = None,
) -> dict[str, Any]:
    collisions: list[dict[str, Any]] = []
    for case in cases:
        candidate_matches = matched_rules(case.change, rules)
        if len(candidate_matches) > 1:
            collisions.append(
                {
                    "name": case.name,
                    "collision_type": "candidate_candidate",
                    "rule_ids": [rule.rule_id for rule in candidate_matches],
                    "event_types": [rule.event_type for rule in candidate_matches],
                    "change": sanitize_change(case.change),
                }
            )
        if existing_rules:
            existing_matches = matched_rules(case.change, existing_rules)
            if candidate_matches and existing_matches:
                collisions.append(
                    {
                        "name": case.name,
                        "collision_type": "candidate_active",
                        "rule_ids": [
                            *(rule.rule_id for rule in candidate_matches),
                            *(rule.rule_id for rule in existing_matches),
                        ],
                        "event_types": [
                            *(rule.event_type for rule in candidate_matches),
                            *(rule.event_type for rule in existing_matches),
                        ],
                        "change": sanitize_change(case.change),
                    }
                )
    return {
        "success": not collisions,
        "collision_count": len(collisions),
        "collisions": collisions,
    }


def validate_event_rule_definitions(
    definitions: list[EventRuleDefinition],
    *,
    existing_rules: list[EventRule] | None = None,
) -> dict[str, Any]:
    rules = [definition.to_rule() for definition in definitions]
    generated_cases: list[EventRuleValidationCase] = []
    rule_results: list[dict[str, Any]] = []
    isolated_rows: list[dict[str, Any]] = []
    passed_count = 0
    failed_count = 0
    for definition, rule in zip(definitions, rules, strict=True):
        cases = generate_validation_cases_for_rule(definition)
        generated_cases.extend(cases)
        isolated = validate_event_rule_set(cases, rules=[rule])
        isolated_rows.extend(isolated["rows"])
        passed_count += int(isolated["passed_count"])
        failed_count += int(isolated["failed_count"])
        rule_results.append(
            {
                "rule_id": definition.rule_id,
                "event_type": definition.event_type,
                **isolated,
            }
        )
    total = len(generated_cases)
    generated_result = {
        "success": failed_count == 0,
        "case_count": total,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "pass_rate": (passed_count / total) if total else 1.0,
        "rows": isolated_rows,
        "evaluation_scope": "isolated_rule",
    }
    collision_result = detect_rule_collisions(
        generated_cases,
        rules,
        existing_rules=existing_rules,
    )
    return {
        "success": generated_result["success"] and collision_result["success"],
        "rule_count": len(definitions),
        "case_count": generated_result["case_count"],
        "passed_count": generated_result["passed_count"],
        "failed_count": generated_result["failed_count"],
        "pass_rate": generated_result["pass_rate"],
        "rows": generated_result["rows"],
        "rule_results": rule_results,
        "generated_result": generated_result,
        "collision_result": collision_result,
        "validation_strategy": {
            "rule_behavior": "isolated",
            "cross_rule_collisions": "combined",
        },
    }


def _first(values: Any, default: str) -> str:
    if isinstance(values, (list, tuple)) and values:
        return str(values[0])
    return default


def _threshold_values(condition: dict[str, Any], *, high: bool) -> tuple[float, float]:
    if high:
        threshold = float(condition.get("gte") or 90)
        return threshold - 1, threshold
    threshold = float(condition.get("lt") or 90)
    return threshold, threshold - 1


def generate_validation_cases_for_rule(
    definition: EventRuleDefinition,
) -> list[EventRuleValidationCase]:
    """Generate deterministic positive/negative cases from a rule DSL definition."""
    condition = definition.condition
    kind = str(condition.get("kind") or "")
    entity_type = _first(condition.get("entity_types"), "entity")
    field = _first(condition.get("fields"), "status")
    cases: list[EventRuleValidationCase] = []

    if kind == "threshold":
        low, high = _threshold_values(condition, high=True)
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-positive-threshold",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": low,
                    "new": high,
                    "change_type": "modified",
                },
                expected_event_type=definition.event_type,
                expected_rule_id=definition.rule_id,
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-below-threshold",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": max(low - 1, 0),
                    "new": low,
                    "change_type": "modified",
                },
            )
        )
    elif kind == "threshold_recovery":
        old_value, recovered_value = _threshold_values(condition, high=False)
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-positive-recovery",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": old_value,
                    "new": recovered_value,
                    "change_type": "modified",
                },
                expected_event_type=definition.event_type,
                expected_rule_id=definition.rule_id,
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-initial-recovery",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": None,
                    "new": recovered_value,
                    "change_type": "initial",
                },
            )
        )
    elif kind == "threshold_lte":
        # The fallback only constructs a synthetic validator case. It must not
        # resemble a product or optic runtime default.
        threshold = float(condition["lte"] if condition.get("lte") is not None else 0.0)
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-positive-low-threshold",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": threshold + 1,
                    "new": threshold,
                    "change_type": "modified",
                },
                expected_event_type=definition.event_type,
                expected_rule_id=definition.rule_id,
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-still-low",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": threshold - 1,
                    "new": threshold - 2,
                    "change_type": "modified",
                },
            )
        )
    elif kind == "threshold_low_recovery":
        low = float(condition["low"] if condition.get("low") is not None else 0.0)
        recovered = float(condition.get("gt") or low + 2)
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-positive-low-recovery",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": low,
                    "new": recovered,
                    "change_type": "modified",
                },
                expected_event_type=definition.event_type,
                expected_rule_id=definition.rule_id,
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-low-not-recovered",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": low,
                    "new": recovered - 1,
                    "change_type": "modified",
                },
            )
        )
    elif kind in {
        "threshold_field_gte",
        "threshold_field_high_recovery",
        "threshold_field_lte",
        "threshold_field_low_recovery",
    }:
        reference_field = str(condition.get("reference_field") or "reference")
        reference = 10.0
        hysteresis = float(condition.get("hysteresis") or 2.0)
        context = {APPLICABILITY_CONTEXT_KEY: {reference_field: reference}}
        high_direction = kind in {
            "threshold_field_gte",
            "threshold_field_high_recovery",
        }
        recovery = kind in {
            "threshold_field_high_recovery",
            "threshold_field_low_recovery",
        }
        if high_direction and not recovery:
            old_value, new_value, negative_new = reference - 1, reference, reference - 1
        elif high_direction:
            old_value, new_value, negative_new = (
                reference,
                reference - hysteresis,
                reference - hysteresis + 1,
            )
        elif not recovery:
            old_value, new_value, negative_new = reference + 1, reference, reference + 1
        else:
            old_value, new_value, negative_new = (
                reference,
                reference + hysteresis,
                reference + hysteresis - 1,
            )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-positive-reference-threshold",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "dimensions": context,
                    "field": field,
                    "old": old_value,
                    "new": new_value,
                    "change_type": "modified",
                },
                expected_event_type=definition.event_type,
                expected_rule_id=definition.rule_id,
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-reference-threshold",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "dimensions": context,
                    "field": field,
                    "old": old_value,
                    "new": negative_new,
                    "change_type": "modified",
                },
            )
        )
    elif kind == "transition":
        from_group = str(condition.get("from_group") or "up")
        to_group = str(condition.get("to_group") or "down")
        from_value = "up" if from_group == "up" else "down"
        to_value = "down" if to_group == "down" else "up"
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-positive-transition",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": from_value,
                    "new": to_value,
                    "change_type": "modified",
                },
                expected_event_type=definition.event_type,
                expected_rule_id=definition.rule_id,
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-reverse-transition",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": to_value,
                    "new": from_value,
                    "change_type": "modified",
                },
            )
        )
        if condition.get("allow_initial_target"):
            cases.append(
                EventRuleValidationCase(
                    name=f"{definition.rule_id}-positive-initial-target",
                    change={
                        "entity_type": entity_type,
                        "entity_key": "entity=1",
                        "field": field,
                        "old": None,
                        "new": to_value,
                        "change_type": "initial",
                    },
                    expected_event_type=definition.event_type,
                    expected_rule_id=definition.rule_id,
                )
            )
        else:
            cases.append(
                EventRuleValidationCase(
                    name=f"{definition.rule_id}-negative-initial-target",
                    change={
                        "entity_type": entity_type,
                        "entity_key": "entity=1",
                        "field": field,
                        "old": None,
                        "new": to_value,
                        "change_type": "initial",
                    },
                )
            )
    elif kind == "bgp_transition":
        to_established = bool(condition.get("to_established"))
        new_value = "Established" if to_established else "Idle"
        old_value = "Idle" if to_established else "Established"
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-positive-bgp-transition",
                change={
                    "command": "display bgp peer",
                    "entity_type": "bgp_neighbor",
                    "entity_key": "peer=neighbor-1",
                    "field": field,
                    "old": old_value,
                    "new": new_value,
                    "change_type": "modified",
                },
                expected_event_type=definition.event_type,
                expected_rule_id=definition.rule_id,
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-non-bgp-state",
                change={
                    "command": "display power",
                    "entity_type": "power_supply",
                    "entity_key": "power_supply=1",
                    "field": field,
                    "old": old_value,
                    "new": new_value,
                    "change_type": "modified",
                },
            )
        )
    elif kind == "entity_status_down":
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-positive-entity-down",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": "normal",
                    "new": "failed",
                    "change_type": "modified",
                },
                expected_event_type=definition.event_type,
                expected_rule_id=definition.rule_id,
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-entity-normal",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": "failed",
                    "new": "normal",
                    "change_type": "modified",
                },
            )
        )
    elif kind == "raw_config_hash":
        marker = _first(condition.get("command_markers"), "configuration")
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-positive-raw-config-hash",
                change={
                    "command": f"show {marker}",
                    "entity_type": "raw_command_output",
                    "entity_key": "command=show-configuration",
                    "field": "raw_text_hash",
                    "old": "hash-old",
                    "new": "hash-new",
                    "change_type": "modified",
                },
                expected_event_type=definition.event_type,
                expected_rule_id=definition.rule_id,
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-raw-non-config",
                change={
                    "command": "display clock",
                    "entity_type": "raw_command_output",
                    "entity_key": "command=display-clock",
                    "field": "raw_text_hash",
                    "old": "hash-old",
                    "new": "hash-new",
                    "change_type": "modified",
                },
            )
        )
    elif kind == "field_change":
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-positive-field-change",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": "version-old",
                    "new": "version-new",
                    "change_type": "modified",
                },
                expected_event_type=definition.event_type,
                expected_rule_id=definition.rule_id,
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-unchanged-field",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": "version-same",
                    "new": "version-same",
                    "change_type": "modified",
                },
            )
        )
        cases.append(
            EventRuleValidationCase(
                name=f"{definition.rule_id}-negative-initial-field",
                change={
                    "entity_type": entity_type,
                    "entity_key": "entity=1",
                    "field": field,
                    "old": None,
                    "new": "version-new",
                    "change_type": "initial",
                },
            )
        )
    dimension_selector = normalize_dimension_selector(condition.get("dimensions"))
    if dimension_selector:
        for case in cases:
            case.change["dimensions"] = {
                **dict(case.change.get("dimensions") or {}),
                **dimension_selector,
            }
        positive = next(
            (case for case in cases if case.expected_event_type is not None),
            None,
        )
        if positive is not None:
            mismatch_dimensions = dict(positive.change.get("dimensions") or {})
            first_name = next(iter(dimension_selector))
            current = dimension_selector[first_name]
            mismatch_dimensions[first_name] = (
                not current
                if isinstance(current, bool)
                else current + 1
                if isinstance(current, (int, float))
                else f"{current}__other"
            )
            cases.append(
                EventRuleValidationCase(
                    name=f"{definition.rule_id}-negative-other-dimension",
                    change={
                        **positive.change,
                        "dimensions": mismatch_dimensions,
                    },
                )
            )

    applicability = condition.get("applicability")
    if applicability:
        for case in cases:
            seed = context_from_change(case.change) or {}
            applicable_context = applicable_context_example(
                applicability,
                seed=seed,
            )
            if applicable_context is not None:
                case.change["context"] = applicable_context
        excluded_context = excluded_context_example(applicability)
        positive = next(
            (case for case in cases if case.expected_event_type is not None),
            None,
        )
        if excluded_context is not None and positive is not None:
            excluded_change = dict(positive.change)
            excluded_change["context"] = {
                **(context_from_change(positive.change) or {}),
                **excluded_context,
            }
            if not applicability_decision(
                applicability,
                context=excluded_change["context"],
            )["applicable"]:
                cases.append(
                    EventRuleValidationCase(
                        name=f"{definition.rule_id}-negative-not-applicable",
                        change=excluded_change,
                    )
                )
    return cases


def validate_event_rule_condition(condition: dict[str, Any]) -> list[str]:
    """Validate DSL shape without executing an untrusted candidate predicate."""

    errors: list[str] = []
    kind = str(condition.get("kind") or "").strip()
    supported = {
        "transition",
        "bgp_transition",
        "threshold",
        "threshold_recovery",
        "threshold_lte",
        "threshold_low_recovery",
        "threshold_field_gte",
        "threshold_field_high_recovery",
        "threshold_field_lte",
        "threshold_field_low_recovery",
        "entity_status_down",
        "raw_config_hash",
        "field_change",
    }
    if kind not in supported:
        return [f"unsupported or missing condition.kind: {kind or '<empty>'}"]

    allowed_keys = {
        "threshold": {"kind", "entity_types", "fields", "gte", "applicability"},
        "threshold_recovery": {
            "kind",
            "entity_types",
            "fields",
            "lt",
            "applicability",
        },
        "threshold_lte": {
            "kind",
            "entity_types",
            "fields",
            "lte",
            "applicability",
        },
        "threshold_low_recovery": {
            "kind",
            "entity_types",
            "fields",
            "low",
            "gt",
            "hysteresis",
            "applicability",
        },
        "threshold_field_gte": {
            "kind",
            "entity_types",
            "fields",
            "reference_field",
            "fallback_reference_fields",
            "applicability",
        },
        "threshold_field_high_recovery": {
            "kind",
            "entity_types",
            "fields",
            "reference_field",
            "fallback_reference_fields",
            "hysteresis",
            "applicability",
        },
        "threshold_field_lte": {
            "kind",
            "entity_types",
            "fields",
            "reference_field",
            "fallback_reference_fields",
            "applicability",
        },
        "threshold_field_low_recovery": {
            "kind",
            "entity_types",
            "fields",
            "reference_field",
            "fallback_reference_fields",
            "hysteresis",
            "applicability",
        },
        "transition": {
            "kind",
            "entity_types",
            "fields",
            "from_group",
            "to_group",
            "from_values",
            "to_values",
            "allow_initial_target",
            "applicability",
        },
        "bgp_transition": {
            "kind",
            "entity_types",
            "fields",
            "from_established",
            "to_established",
            "allow_initial_target",
            "applicability",
        },
        "entity_status_down": {
            "kind",
            "entity_types",
            "fields",
            "values",
            "applicability",
        },
        "field_change": {
            "kind",
            "entity_types",
            "fields",
            "allow_initial",
            "applicability",
        },
        "raw_config_hash": {"kind", "command_markers"},
    }
    for condition_kind, keys in allowed_keys.items():
        if condition_kind != "raw_config_hash":
            keys.add("observation_types")
            keys.add("dimensions")
        if condition_kind.startswith("threshold"):
            keys.add("threshold_role")
    unknown_keys = sorted(set(condition) - allowed_keys[kind])
    if unknown_keys:
        errors.append(f"condition contains unsupported keys for {kind}: " + ", ".join(unknown_keys))
    if "threshold_role" in condition and not str(
        condition.get("threshold_role") or ""
    ).strip():
        errors.append("condition.threshold_role must be a non-empty string")

    def require_list(key: str) -> None:
        value = condition.get(key)
        if not isinstance(value, list) or not value:
            errors.append(f"condition.{key} must be a non-empty list")

    def require_number(key: str) -> None:
        value = condition.get(key)
        try:
            float(value)
        except (TypeError, ValueError):
            errors.append(f"condition.{key} must be a number")

    if kind == "raw_config_hash":
        require_list("command_markers")
        return errors
    if "observation_types" in condition:
        require_list("observation_types")
    if "dimensions" in condition:
        try:
            selector = normalize_dimension_selector(condition.get("dimensions"))
        except ValueError as exc:
            errors.append(f"condition.dimensions is invalid: {exc}")
        else:
            if not selector:
                errors.append("condition.dimensions must not be empty when provided")
    if condition.get("applicability"):
        errors.extend(validate_applicability(condition["applicability"]))
    require_list("fields")
    if kind == "threshold":
        require_number("gte")
    elif kind == "threshold_recovery":
        require_number("lt")
    elif kind == "threshold_lte":
        require_number("lte")
    elif kind == "threshold_low_recovery":
        require_number("low")
        if condition.get("gt") is None and condition.get("hysteresis") is None:
            errors.append("condition.gt or condition.hysteresis is required")
        elif condition.get("gt") is not None:
            require_number("gt")
        else:
            require_number("hysteresis")
    elif kind in {
        "threshold_field_gte",
        "threshold_field_lte",
    }:
        if not str(condition.get("reference_field") or "").strip():
            errors.append("condition.reference_field is required")
    elif kind in {
        "threshold_field_high_recovery",
        "threshold_field_low_recovery",
    }:
        if not str(condition.get("reference_field") or "").strip():
            errors.append("condition.reference_field is required")
        require_number("hysteresis")
    elif kind == "transition":
        for side in ("from", "to"):
            # The executor prefers explicit states; groups are its fallback.
            if not condition.get(f"{side}_values") and str(condition.get(f"{side}_group") or "") not in {"up", "down", "other"}:
                errors.append(f"condition.{side}_group must be up, down, or other when {side}_values is absent")
    elif kind == "bgp_transition":
        for key in ("from_established", "to_established"):
            if not isinstance(condition.get(key), bool):
                errors.append(f"condition.{key} must be a boolean")
    if kind.startswith("threshold_field_") and "fallback_reference_fields" in condition:
        fallback_fields = condition.get("fallback_reference_fields")
        if (
            not isinstance(fallback_fields, list)
            or not fallback_fields
            or any(not str(item).strip() for item in fallback_fields)
        ):
            errors.append("condition.fallback_reference_fields must be a non-empty string list")
        elif len({str(item).strip() for item in fallback_fields}) != len(fallback_fields):
            errors.append("condition.fallback_reference_fields must not contain duplicates")
    explicit_keys = ("from_values", "to_values") if kind == "transition" else ("values",) if kind == "entity_status_down" else ()
    for key in explicit_keys:
        if key in condition:
            values = condition[key]
            if not isinstance(values, list) or not values or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                errors.append(f"condition.{key} must be a non-empty string list")
    return errors


def validate_event_rule_definition(definition: EventRuleDefinition) -> dict[str, Any]:
    errors = validate_event_rule_condition(dict(definition.condition or {}))
    if errors:
        return {
            "success": False,
            "errors": errors,
            "rule_count": 1,
            "case_count": 1,
            "passed_count": 0,
            "failed_count": 1,
            "pass_rate": 0.0,
            "rows": [],
        }
    try:
        return validate_event_rule_definitions([definition])
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "success": False,
            "errors": [f"condition execution failed: {type(exc).__name__}: {exc}"],
            "rule_count": 1,
            "case_count": 1,
            "passed_count": 0,
            "failed_count": 1,
            "pass_rate": 0.0,
            "rows": [],
        }


def validate_event_rule_pack(path: str | None = None) -> dict[str, Any]:
    definitions = load_event_rule_pack(path) if path else default_event_rule_definitions()
    generated_cases: list[EventRuleValidationCase] = []
    for definition in definitions:
        generated_cases.extend(generate_validation_cases_for_rule(definition))
    invariant_result = validate_event_rule_set(
        builtin_event_rule_validation_cases(),
        rules=[definition.to_rule() for definition in definitions],
    )
    definition_results = [
        {
            "rule_id": definition.rule_id,
            "event_type": definition.event_type,
            **validate_event_rule_definition(definition),
        }
        for definition in definitions
    ]
    generated_failed = sum(item["failed_count"] for item in definition_results)
    generated_total = sum(item["case_count"] for item in definition_results)
    passed_count = (
        sum(item["passed_count"] for item in definition_results) + invariant_result["passed_count"]
    )
    case_count = generated_total + invariant_result["case_count"]
    return {
        "success": generated_failed == 0 and invariant_result["success"],
        "rule_count": len(definitions),
        "case_count": case_count,
        "passed_count": passed_count,
        "failed_count": case_count - passed_count,
        "pass_rate": (passed_count / case_count) if case_count else 1.0,
        "generated_case_count": generated_total,
        "invariant_case_count": invariant_result["case_count"],
        "rule_results": definition_results,
        "invariant_result": invariant_result,
    }


def builtin_event_rule_validation_cases() -> list[EventRuleValidationCase]:
    return [
        EventRuleValidationCase(
            name="initial-cpu-normal",
            change={
                "entity_type": "cpu",
                "entity_key": "slot=1",
                "field": "cpu_5s",
                "old": None,
                "new": 30,
                "change_type": "initial",
            },
        ),
        EventRuleValidationCase(
            name="initial-cpu-high",
            change={
                "entity_type": "cpu",
                "entity_key": "slot=1",
                "field": "cpu_5s",
                "old": None,
                "new": 98,
                "change_type": "initial",
            },
            expected_event_type="CPUHigh",
            expected_rule_id="cpu-high",
        ),
        EventRuleValidationCase(
            name="cpu-recovered",
            change={
                "entity_type": "cpu",
                "entity_key": "slot=1",
                "field": "cpu_5s",
                "old": 93,
                "new": 84,
                "change_type": "modified",
            },
            expected_event_type="CPURecovered",
            expected_rule_id="cpu-recovered",
        ),
        EventRuleValidationCase(
            name="initial-interface-up",
            change={
                "entity_type": "interface_l3",
                "entity_key": "interface=interface-1",
                "field": "protocol",
                "old": None,
                "new": "up",
                "change_type": "initial",
            },
        ),
        EventRuleValidationCase(
            name="initial-interface-admin-disabled",
            change={
                "entity_type": "interface_l2",
                "entity_key": "interface=interface-2",
                "field": "status",
                "old": None,
                "new": "disabled",
                "change_type": "initial",
            },
        ),
        EventRuleValidationCase(
            name="initial-bgp-established",
            change={
                "command": "display bgp peer",
                "entity_type": "bgp_neighbor",
                "entity_key": "peer=neighbor-1",
                "field": "state",
                "old": None,
                "new": "Established",
                "change_type": "initial",
            },
        ),
        EventRuleValidationCase(
            name="initial-bgp-idle",
            change={
                "command": "display bgp peer",
                "entity_type": "bgp_neighbor",
                "entity_key": "peer=neighbor-1",
                "field": "state",
                "old": None,
                "new": "Idle",
                "change_type": "initial",
            },
            expected_event_type="BGPNeighborLost",
            expected_rule_id="bgp-neighbor-lost",
        ),
        EventRuleValidationCase(
            name="power-failed-is-not-interface-down",
            change={
                "entity_type": "power_supply",
                "entity_key": "power_supply=1",
                "field": "status",
                "old": "normal",
                "new": "failed",
                "change_type": "modified",
            },
            expected_event_type="PowerFailed",
            expected_rule_id="power-failed",
        ),
        EventRuleValidationCase(
            name="fan-failed",
            change={
                "entity_type": "fan",
                "entity_key": "fan=1",
                "field": "state",
                "old": "normal",
                "new": "failed",
                "change_type": "modified",
            },
            expected_event_type="FanFailed",
            expected_rule_id="fan-failed",
        ),
    ]


def validate_builtin_event_rules() -> dict[str, Any]:
    return validate_event_rule_set(builtin_event_rule_validation_cases())
