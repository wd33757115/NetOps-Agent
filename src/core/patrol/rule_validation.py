"""Deterministic replay validation for event rule packs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.patrol.event_rules import (
    EventRule,
    EventRuleDefinition,
    default_event_rule_definitions,
    default_event_rules,
    load_event_rule_pack,
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
    return {
        key: value
        for key, value in change.items()
        if key not in PRIVATE_CHANGE_FIELDS
    }


def match_rule_set(change: dict[str, Any], rules: list[EventRule]) -> EventRule | None:
    for rule in rules:
        if rule.match(change):
            return rule
    return None


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
    return cases


def validate_event_rule_definition(definition: EventRuleDefinition) -> dict[str, Any]:
    return validate_event_rule_set(
        generate_validation_cases_for_rule(definition),
        rules=[definition.to_rule()],
    )


def validate_event_rule_pack(path: str | None = None) -> dict[str, Any]:
    definitions = (
        load_event_rule_pack(path) if path else default_event_rule_definitions()
    )
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
        sum(item["passed_count"] for item in definition_results)
        + invariant_result["passed_count"]
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
            name="initial-interface-down",
            change={
                "entity_type": "interface_l3",
                "entity_key": "interface=interface-1",
                "field": "protocol",
                "old": None,
                "new": "down",
                "change_type": "initial",
            },
            expected_event_type="InterfaceDown",
            expected_rule_id="interface-down",
        ),
        EventRuleValidationCase(
            name="interface-up",
            change={
                "entity_type": "interface_l2",
                "entity_key": "interface=interface-1",
                "field": "status",
                "old": "notconnect",
                "new": "connected",
                "change_type": "modified",
            },
            expected_event_type="InterfaceUp",
            expected_rule_id="interface-up",
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
