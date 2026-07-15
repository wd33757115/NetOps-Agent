#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.event_rule_compiler import (  # noqa: E402
    compile_event_rule_intents,
)
from src.core.patrol.event_rule_generation import (  # noqa: E402
    generate_and_validate_event_rules,
)
from src.core.patrol.event_rules import EventRuleDefinition  # noqa: E402
from src.core.patrol.rule_validation import validate_event_rule_definition  # noqa: E402
from src.core.rule_control.generation_agent import (  # noqa: E402
    DEFAULT_RULE_GENERATION_PROVIDERS,
    RuleGenerationAgent,
)
from src.core.state.observation_applicability import (  # noqa: E402
    attach_governed_applicability,
    validate_applicability,
)


def _agent_loop(result: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    failed = []
    for report in result.get("reports") or []:
        validation = report.get("validation") if isinstance(report, dict) else {}
        if isinstance(validation, dict) and validation.get("success") is not True:
            failed.append(
                {
                    "rule": (report.get("rule") or {}).get("name"),
                    "errors": validation.get("errors") or [],
                }
            )
    success = result.get("success") is True
    revision_request = None
    if not success:
        revision_request = {
            "target_generator": "event-rule-generator",
            "reason": [
                "event rule generation or deterministic validation failed",
            ],
            "required_fixes": [
                "match rules to the contract entity_type",
                "keep recovery rules from matching initial observations",
                "include entity_types so status/state fields are not misclassified",
                "keep dedupe and resolves semantics compatible with event lifecycle",
            ],
            "failed_rules": failed,
            "next_params_patch": {
                "agent_loop": True,
            },
        }
    return {
        "ready_for_approval": success,
        "next_action": "submit_for_validation" if success else "revise_event_rule_candidate",
        "revision_request": revision_request,
        "attempts": [
            {
                "attempt": 1,
                "stage": "event_rule_generation",
                "generator": "event-rule-generator",
                "mode": "generate_and_validate",
                "success": success,
                "input_summary": {
                    "entity_type": (params.get("contract") or {}).get("entity_type")
                    if isinstance(params.get("contract"), dict)
                    else None,
                    "observation_type": (params.get("change_policy") or {}).get("observation_type")
                    if isinstance(params.get("change_policy"), dict)
                    else None,
                    "compare_mode": (params.get("change_policy") or {}).get("compare_mode")
                    if isinstance(params.get("change_policy"), dict)
                    else None,
                    "intent_provided": bool(params.get("intent")),
                },
                "output_summary": {
                    "generated_rule_count": result.get("generated_rule_count", 0),
                    "event_types": [
                        str(rule.get("event_type"))
                        for rule in result.get("rules") or []
                        if isinstance(rule, dict)
                    ],
                },
                "validator_feedback": {
                    "failed_rule_count": len(failed),
                    "failed_rules": failed,
                },
                "revision_request": revision_request,
            }
        ],
    }


def _validate_llm_rules(
    candidate: dict[str, Any],
    *,
    contract: dict[str, Any],
    change_policy: dict[str, Any],
    event_constraints: dict[str, Any] | None = None,
    reserved_fault_event_types: dict[str, str] | None = None,
) -> dict[str, Any]:
    rules = candidate.get("rules") or []
    if not isinstance(rules, list) or not rules:
        return {"success": False, "errors": ["at least one event rule is required"]}
    compilation = compile_event_rule_intents(
        candidate_rules=rules,
        contract=contract,
        change_policy=change_policy,
    )
    rules = list(compilation.get("rules") or [])
    declared_fields = {
        str(item)
        for item in [
            *(contract.get("required_fields") or []),
            *(contract.get("optional_fields") or []),
        ]
    }
    entity_type = str(contract.get("entity_type") or "").strip()
    reports: list[dict[str, Any]] = []
    errors: list[str] = [str(item) for item in compilation.get("errors") or []]
    fault_event_types: set[str] = set()
    recovery_rules: list[dict[str, Any]] = []
    normalized_rules: list[dict[str, Any]] = []
    guard_normalizations: list[dict[str, Any]] = []
    for index, item in enumerate(rules):
        if not isinstance(item, dict):
            errors.append(f"rules[{index}] must be an object")
            continue
        condition, guard_changed = attach_governed_applicability(
            dict(item.get("condition") or {}), contract
        )
        if guard_changed:
            guard_normalizations.append(
                {
                    "kind": "governed_applicability",
                    "rule_index": index,
                    "event_type": item.get("event_type"),
                }
            )
        condition_fields = {str(field) for field in condition.get("fields") or []}
        unknown_fields = sorted(condition_fields - declared_fields)
        if unknown_fields:
            errors.append(
                f"rules[{index}] references undeclared fields: " + ", ".join(unknown_fields)
            )
        if condition.get("applicability"):
            errors.extend(
                f"rules[{index}].condition.{message}"
                for message in validate_applicability(
                    condition["applicability"],
                    declared_fields=declared_fields,
                )
            )
        entity_types = {str(value) for value in condition.get("entity_types") or []}
        if condition.get("kind") != "raw_config_hash" and not entity_types:
            errors.append(
                f"rules[{index}].condition.entity_types must include contract entity_type"
            )
        if entity_type and entity_types and entity_type not in entity_types:
            errors.append(f"rules[{index}] does not include contract entity_type")
        proposed_correlation_keys = tuple(
            item.get("correlation_keys")
            or item.get("correlation_key")
            or ("device_id", "entity_type", "entity_key", "field")
        )
        definition = EventRuleDefinition(
            rule_id=str(item.get("name") or item.get("rule_id") or item.get("event_type") or ""),
            version=int(item.get("version") or 1),
            event_type=str(item.get("event_type") or ""),
            severity=str(item.get("severity") or "warning"),
            condition=condition,
            description=item.get("description"),
            priority=int(item.get("priority") or 100),
            exclusive=bool(item.get("exclusive", False)),
            event_class=str(item.get("event_class") or "fault"),
            recovery=dict(item.get("recovery") or {}),
            correlation_keys=proposed_correlation_keys,
        )
        if definition.correlation_keys != proposed_correlation_keys:
            guard_normalizations.append(
                {
                    "kind": "device_scoped_correlation_identity",
                    "rule_index": index,
                    "event_type": item.get("event_type"),
                    "proposed": list(proposed_correlation_keys),
                    "effective": list(definition.correlation_keys),
                }
            )
        report = validate_event_rule_definition(definition)
        reports.append({"rule": item, "validation": report})
        if report.get("success") is not True:
            errors.extend(str(value) for value in report.get("errors") or [])
        normalized = {
            "name": definition.rule_id,
            "version": definition.version,
            "event_type": definition.event_type,
            "trigger_role": str(item.get("trigger_role") or "").strip() or None,
            "severity": definition.severity,
            "description": definition.description,
            "condition": definition.condition,
            "priority": definition.priority,
            "exclusive": definition.exclusive,
            "event_class": definition.event_class,
            "recovery": definition.recovery or {},
            "correlation_keys": list(definition.correlation_keys),
        }
        normalized_rules.append(normalized)
        if definition.event_class == "recovery":
            recovery_rules.append(normalized)
        else:
            fault_event_types.add(definition.event_type)
    fault_name_to_type = {
        str(rule.get("name") or ""): str(rule.get("event_type") or "")
        for rule in normalized_rules
        if str(rule.get("event_class") or "fault") != "recovery"
    }
    for rule in recovery_rules:
        recovery = dict(rule.get("recovery") or {})
        recovery["resolves"] = [
            fault_name_to_type.get(str(value), str(value))
            for value in recovery.get("resolves") or []
        ]
        rule["recovery"] = recovery
    normalizations = (
        list(compilation.get("normalizations") or [])
        + guard_normalizations
        + _normalize_rule_ir(
            normalized_rules,
            observation_type=str(change_policy.get("observation_type") or "rule"),
            constraints=dict(event_constraints or {}),
        )
    )
    technical_names = [str(rule.get("name") or "") for rule in normalized_rules]
    if len(technical_names) != len(set(technical_names)):
        errors.append("compiled event rules contain duplicate logical rule names")
    for rule in recovery_rules:
        resolves = {str(value) for value in (rule.get("recovery") or {}).get("resolves") or []}
        if not resolves or not resolves.intersection(fault_event_types):
            errors.append(
                f"recovery rule {rule.get('name')} resolves {sorted(resolves)}; expected a "
                f"generated fault event_type from {sorted(fault_event_types)}"
            )
    errors.extend(
        _cross_asset_rule_errors(
            normalized_rules,
            contract=contract,
            change_policy=change_policy,
        )
    )
    errors.extend(
        _event_constraint_errors(
            normalized_rules,
            dict(event_constraints or {}),
        )
    )
    errors.extend(
        _critical_severity_errors(
            normalized_rules,
            dict(event_constraints or {}),
        )
    )
    current_observation_type = str(change_policy.get("observation_type") or "")
    for rule in normalized_rules:
        if str(rule.get("event_class") or "fault") == "recovery":
            continue
        event_type = str(rule.get("event_type") or "")
        reserved_by = str((reserved_fault_event_types or {}).get(event_type) or "")
        if reserved_by and reserved_by != current_observation_type:
            errors.append(
                f"fault event_type {event_type} is already reserved by observation_type "
                f"{reserved_by}; choose a target-specific event type"
            )
    return {
        "success": not errors and len(normalized_rules) == len(rules),
        "errors": list(dict.fromkeys(errors)),
        "reports": reports,
        "rules": normalized_rules,
        "generated_rule_count": len(normalized_rules),
        "normalizations": normalizations,
        "compiler": {
            "compiled": bool(compilation.get("compiled")),
            "normalization_count": len(compilation.get("normalizations") or []),
        },
    }


def _rule_name_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "rule").casefold()).strip("-")


def _normalize_rule_ir(
    rules: list[dict[str, Any]],
    *,
    observation_type: str,
    constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compile unambiguous names and precedence without changing rule semantics."""

    changes: list[dict[str, Any]] = []
    namespace = _rule_name_token(observation_type)
    for rule in rules:
        old_name = str(rule.get("name") or "rule")
        event_token = _rule_name_token(rule.get("event_type"))
        role_token = _rule_name_token(rule.get("trigger_role"))
        slot_token = role_token or event_token or _rule_name_token(old_name)
        new_name = f"{namespace}.{slot_token}"
        if old_name != new_name:
            rule["name"] = new_name
            changes.append(
                {
                    "kind": "rule_name_namespace",
                    "from": old_name,
                    "to": new_name,
                }
            )
    for item in constraints.get("priority_order") or []:
        if not isinstance(item, dict):
            continue
        higher_type = str(item.get("higher") or "")
        lower_type = str(item.get("lower") or "")
        higher_rules = [rule for rule in rules if str(rule.get("event_type") or "") == higher_type]
        lower_rules = [rule for rule in rules if str(rule.get("event_type") or "") == lower_type]
        if not higher_rules or not lower_rules:
            continue
        lower_priority = max(int(rule.get("priority") or 100) for rule in lower_rules)
        required_priority = lower_priority + 10
        for rule in higher_rules:
            current = int(rule.get("priority") or 100)
            if current >= required_priority:
                continue
            rule["priority"] = required_priority
            changes.append(
                {
                    "kind": "priority_precedence",
                    "rule": rule.get("name"),
                    "from": current,
                    "to": required_priority,
                    "reason": f"{higher_type} executes before {lower_type}",
                }
            )
    return changes


def _event_constraint_errors(rules: list[dict[str, Any]], constraints: dict[str, Any]) -> list[str]:
    if not constraints:
        return []
    errors: list[str] = []
    by_type: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        by_type.setdefault(str(rule.get("event_type") or ""), []).append(rule)

    required_types = {str(item) for item in constraints.get("required_event_types") or []}
    for event_type in sorted(required_types - set(by_type)):
        errors.append(f"required event type is missing: {event_type}")

    class_requirements = dict(constraints.get("required_event_classes") or {})
    for event_type, expected_class in class_requirements.items():
        if event_type in by_type and not any(
            str(rule.get("event_class") or "fault") == str(expected_class)
            for rule in by_type[event_type]
        ):
            errors.append(f"event type {event_type} must use event_class={expected_class}")

    kind_requirements = dict(constraints.get("required_condition_kinds") or {})
    for event_type, allowed in kind_requirements.items():
        allowed_kinds = {
            str(item) for item in (allowed if isinstance(allowed, list) else [allowed])
        }
        if event_type in by_type and not any(
            str((rule.get("condition") or {}).get("kind") or "") in allowed_kinds
            for rule in by_type[event_type]
        ):
            errors.append(
                f"event type {event_type} must use condition kind: "
                + ", ".join(sorted(allowed_kinds))
            )

    severity_requirements = dict(constraints.get("required_severities") or {})
    for event_type, severity in severity_requirements.items():
        if event_type in by_type and not any(
            str(rule.get("severity") or "warning") == str(severity) for rule in by_type[event_type]
        ):
            errors.append(f"event type {event_type} must use severity={severity}")

    value_requirements = dict(constraints.get("required_condition_values") or {})
    for event_type, expected_values in value_requirements.items():
        if event_type not in by_type or not isinstance(expected_values, dict):
            continue
        if not any(
            all(
                _numbers_equal((rule.get("condition") or {}).get(key), expected)
                if isinstance(expected, (int, float))
                else (rule.get("condition") or {}).get(key) == expected
                for key, expected in expected_values.items()
            )
            for rule in by_type[event_type]
        ):
            errors.append(
                f"event type {event_type} condition values must match "
                + json.dumps(expected_values, ensure_ascii=False, sort_keys=True)
            )

    for event_type in constraints.get("required_exclusive_event_types") or []:
        if event_type in by_type and not any(
            bool(rule.get("exclusive")) for rule in by_type[event_type]
        ):
            errors.append(f"event type {event_type} must be exclusive")

    for item in constraints.get("priority_order") or []:
        if not isinstance(item, dict):
            continue
        higher = str(item.get("higher") or "")
        lower = str(item.get("lower") or "")
        if higher in by_type and lower in by_type:
            highest = max(int(rule.get("priority") or 100) for rule in by_type[higher])
            lowest = max(int(rule.get("priority") or 100) for rule in by_type[lower])
            if highest <= lowest:
                errors.append(f"event type {higher} must have higher priority than {lower}")

    for item in constraints.get("required_recovery_pairs") or []:
        if not isinstance(item, dict):
            continue
        fault_type = str(item.get("fault") or "")
        recovery_type = str(item.get("recovery") or "")
        candidates = by_type.get(recovery_type) or []
        recovery_candidates = [
            rule for rule in candidates if str(rule.get("event_class") or "fault") == "recovery"
        ]
        if not any(
            str(rule.get("event_class") or "fault") == "recovery"
            and fault_type
            in {str(value) for value in (rule.get("recovery") or {}).get("resolves") or []}
            for rule in candidates
        ):
            actual = sorted(
                {
                    str(value)
                    for rule in recovery_candidates
                    for value in (rule.get("recovery") or {}).get("resolves") or []
                }
            )
            errors.append(
                f"recovery event {recovery_type} resolves {actual}; it must resolve "
                f"fault event_type {fault_type}"
            )

    if constraints.get("allow_initial_transition_fault") is not None:
        expected = bool(constraints["allow_initial_transition_fault"])
        for rule in rules:
            if str(rule.get("event_class") or "fault") == "recovery":
                continue
            condition = dict(rule.get("condition") or {})
            kind = str(condition.get("kind") or "")
            if kind == "entity_status_down" and not expected:
                errors.append(
                    f"fault rule {rule.get('name')} matches initial state but policy forbids it"
                )
            elif (
                kind in {"transition", "bgp_transition"}
                and bool(condition.get("allow_initial_target")) != expected
            ):
                errors.append(f"fault rule {rule.get('name')} has incorrect allow_initial_target")

    max_rules = constraints.get("max_rule_count")
    if max_rules is not None and len(rules) > int(max_rules):
        errors.append(f"generated rule count exceeds maximum {int(max_rules)}")
    return list(dict.fromkeys(errors))


def _cross_asset_rule_errors(
    rules: list[dict[str, Any]],
    *,
    contract: dict[str, Any],
    change_policy: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    observation_type = str(change_policy.get("observation_type") or "")
    semantics = dict(contract.get("state_semantics") or {})
    target_fields = {
        str(field)
        for field, value in semantics.items()
        if isinstance(value, dict) and str(value.get("observation_type") or "") == observation_type
    }
    if not target_fields:
        errors.append("change policy observation_type is not declared by the contract")
        return errors
    compare_mode = str(change_policy.get("compare_mode") or "")
    threshold_references = dict(change_policy.get("threshold_references") or {})
    fault_rules = [rule for rule in rules if str(rule.get("event_class") or "fault") != "recovery"]
    recovery_rules = [
        rule for rule in rules if str(rule.get("event_class") or "fault") == "recovery"
    ]
    for rule in rules:
        condition = dict(rule.get("condition") or {})
        fields = {str(item) for item in condition.get("fields") or []}
        if fields and not fields.intersection(target_fields):
            errors.append(f"rule {rule.get('name')} does not target the ChangePolicy field")
    if compare_mode == "threshold_low_and_deadband":
        low = (change_policy.get("thresholds") or {}).get("low")
        critical_low = (change_policy.get("thresholds") or {}).get("critical_low")
        low_reference = dict(threshold_references.get("low") or {})
        matching_faults = [
            rule
            for rule in fault_rules
            if (rule.get("condition") or {}).get("kind")
            == ("threshold_field_lte" if low_reference else "threshold_lte")
            and target_fields.intersection((rule.get("condition") or {}).get("fields") or [])
        ]
        if not matching_faults:
            errors.append("low-threshold policy requires a threshold_lte fault rule")
        elif low_reference and not any(
            str((rule.get("condition") or {}).get("reference_field") or "")
            == str(low_reference.get("field_name") or "")
            for rule in matching_faults
        ):
            errors.append("fault rule reference must match ChangePolicy low reference")
        elif (
            not low_reference
            and low is not None
            and not any(
                _numbers_equal((rule.get("condition") or {}).get("lte"), low)
                for rule in matching_faults
            )
        ):
            errors.append("fault rule threshold must match ChangePolicy thresholds.low")
        critical_reference = dict(threshold_references.get("critical_low") or {})
        if (critical_low is not None or critical_reference) and not any(
            (rule.get("condition") or {}).get("kind")
            == ("threshold_field_lte" if critical_reference else "threshold_lte")
            and target_fields.intersection((rule.get("condition") or {}).get("fields") or [])
            and (
                str((rule.get("condition") or {}).get("reference_field") or "")
                == str(critical_reference.get("field_name") or "")
                if critical_reference
                else _numbers_equal((rule.get("condition") or {}).get("lte"), critical_low)
            )
            for rule in fault_rules
        ):
            errors.append(
                "critical low threshold requires a fault rule matching "
                "ChangePolicy thresholds.critical_low"
            )
        recovery_threshold = change_policy.get("recovery_threshold")
        if recovery_threshold is not None or (low_reference and change_policy.get("deadband")):
            matching_recovery = [
                rule
                for rule in recovery_rules
                if (rule.get("condition") or {}).get("kind")
                == ("threshold_field_low_recovery" if low_reference else "threshold_low_recovery")
            ]
            if not matching_recovery:
                errors.append("low-threshold policy with hysteresis requires a recovery rule")
            elif low_reference and not any(
                str((rule.get("condition") or {}).get("reference_field") or "")
                == str(low_reference.get("field_name") or "")
                for rule in matching_recovery
            ):
                errors.append("recovery rule reference must match the ChangePolicy")
            elif not low_reference and not any(
                _numbers_equal((rule.get("condition") or {}).get("low"), low)
                and _numbers_equal((rule.get("condition") or {}).get("gt"), recovery_threshold)
                for rule in matching_recovery
            ):
                errors.append("recovery rule thresholds must match the ChangePolicy")
    elif compare_mode == "threshold_and_deadband":
        high = (change_policy.get("thresholds") or {}).get("high")
        high_reference = dict(threshold_references.get("high") or {})
        matching_faults = [
            rule
            for rule in fault_rules
            if (rule.get("condition") or {}).get("kind")
            == ("threshold_field_gte" if high_reference else "threshold")
            and target_fields.intersection((rule.get("condition") or {}).get("fields") or [])
        ]
        if not matching_faults:
            errors.append("high-threshold policy requires a threshold fault rule")
        elif high_reference and not any(
            str((rule.get("condition") or {}).get("reference_field") or "")
            == str(high_reference.get("field_name") or "")
            for rule in matching_faults
        ):
            errors.append("fault rule reference must match ChangePolicy high reference")
        elif (
            not high_reference
            and high is not None
            and not any(
                _numbers_equal((rule.get("condition") or {}).get("gte"), high)
                for rule in matching_faults
            )
        ):
            errors.append("fault rule threshold must match ChangePolicy thresholds.high")
        recovery_threshold = change_policy.get("recovery_threshold")
        if (
            recovery_threshold is not None or (high_reference and change_policy.get("deadband"))
        ) and not any(
            (rule.get("condition") or {}).get("kind")
            == ("threshold_field_high_recovery" if high_reference else "threshold_recovery")
            and (
                str((rule.get("condition") or {}).get("reference_field") or "")
                == str(high_reference.get("field_name") or "")
                if high_reference
                else _numbers_equal((rule.get("condition") or {}).get("lt"), recovery_threshold)
            )
            for rule in recovery_rules
        ):
            errors.append("recovery rule threshold must match the ChangePolicy")
    elif compare_mode in {"state_transition", "enum_transition"}:
        if not any(
            (rule.get("condition") or {}).get("kind")
            in {"transition", "bgp_transition", "entity_status_down"}
            for rule in fault_rules
        ):
            errors.append("state policy requires a transition fault rule")
    if compare_mode == "threshold_and_deadband" and threshold_references.get("low"):
        low_reference = dict(threshold_references["low"])
        if not any(
            (rule.get("condition") or {}).get("kind") == "threshold_field_lte"
            and str((rule.get("condition") or {}).get("reference_field") or "")
            == str(low_reference.get("field_name") or "")
            for rule in fault_rules
        ):
            errors.append("ChangePolicy low reference requires a threshold_field_lte rule")
        if change_policy.get("deadband") and not any(
            (rule.get("condition") or {}).get("kind") == "threshold_field_low_recovery"
            and str((rule.get("condition") or {}).get("reference_field") or "")
            == str(low_reference.get("field_name") or "")
            for rule in recovery_rules
        ):
            errors.append("ChangePolicy low reference requires a threshold_field_low_recovery rule")
    return errors


def _critical_severity_errors(
    rules: list[dict[str, Any]], constraints: dict[str, Any]
) -> list[str]:
    if constraints.get("allow_critical") is True:
        return []
    allowed_event_types = {
        str(item) for item in constraints.get("allow_critical_event_types") or [] if str(item)
    }
    allowed_event_types.update(
        str(event_type)
        for event_type, severity in dict(constraints.get("required_severities") or {}).items()
        if str(severity).casefold() == "critical"
    )
    return [
        (
            f"event type {rule.get('event_type')} uses critical severity without "
            "explicit event_constraints authorization; use major or warning"
        )
        for rule in rules
        if str(rule.get("severity") or "warning").casefold() == "critical"
        and str(rule.get("event_type") or "") not in allowed_event_types
    ]


def _numbers_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return False


def run_event_rule_generation(params: dict[str, Any]) -> dict[str, Any]:
    contract = params.get("contract")
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    change_policies = params.get("change_policies")
    if isinstance(change_policies, list) and change_policies:
        results: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        rules_by_name: dict[str, dict[str, Any]] = {}
        conflicts: list[str] = []
        reserved_fault_event_types: dict[str, str] = {}
        for index, policy in enumerate(change_policies):
            if not isinstance(policy, dict):
                raise ValueError(f"change_policies[{index}] must be an object")
            constraints_by_type = params.get("event_constraints_by_observation_type")
            observation_type = str(policy.get("observation_type") or "")
            scoped_constraints = (
                constraints_by_type.get(observation_type)
                if isinstance(constraints_by_type, dict)
                else None
            )
            result = run_event_rule_generation(
                {
                    **params,
                    "change_policies": None,
                    "change_policy": policy,
                    "event_constraints": scoped_constraints or params.get("event_constraints"),
                    "reserved_fault_event_types": reserved_fault_event_types,
                }
            )
            results.append(result)
            loop = result.get("agent_loop")
            if isinstance(loop, dict):
                for attempt in loop.get("attempts") or []:
                    if isinstance(attempt, dict):
                        attempts.append({**attempt, "attempt": len(attempts) + 1})
            for rule in result.get("rules") or []:
                if not isinstance(rule, dict):
                    continue
                name = str(rule.get("name") or rule.get("rule_id") or rule.get("event_type") or "")
                previous = rules_by_name.get(name)
                if previous is not None and json.dumps(
                    previous, sort_keys=True, default=str
                ) != json.dumps(rule, sort_keys=True, default=str):
                    conflicts.append(f"conflicting generated rule name: {name}")
                else:
                    rules_by_name[name] = rule
                if str(rule.get("event_class") or "fault") != "recovery":
                    event_type = str(rule.get("event_type") or "")
                    if event_type:
                        reserved_fault_event_types[event_type] = observation_type
        success = all(result.get("success") is True for result in results) and not conflicts
        return {
            "success": success,
            "mode": "change_policy_fanout",
            "generated_rule_count": len(rules_by_name),
            "rules": list(rules_by_name.values()),
            "results": results,
            "validation": {"success": success, "errors": conflicts},
            "agent_loop": {
                "ready_for_approval": success,
                "next_action": (
                    "submit_for_validation" if success else "revise_event_rule_candidate"
                ),
                "attempt_count": len(attempts),
                "attempts": attempts,
            },
            "generation_provider": str(params.get("generation_provider") or "builtin").casefold(),
        }
    change_policy = params.get("change_policy")
    if not isinstance(change_policy, dict):
        raise ValueError("change_policy must be an object")
    generation_provider = str(params.get("generation_provider") or "").strip().casefold()
    if generation_provider == "llm":
        agent = RuleGenerationAgent(
            DEFAULT_RULE_GENERATION_PROVIDERS.create(generation_provider),
            max_attempts=int(params.get("max_agent_attempts") or 3),
        )
        generated = agent.run(
            stage="event_rules",
            context={
                "contract": contract,
                "change_policy": change_policy,
                "event_intent": params.get("intent"),
                "event_constraints": dict(params.get("event_constraints") or {}),
                "reserved_fault_event_types": dict(params.get("reserved_fault_event_types") or {}),
            },
            validator=lambda candidate: _validate_llm_rules(
                candidate,
                contract=contract,
                change_policy=change_policy,
                event_constraints=dict(params.get("event_constraints") or {}),
                reserved_fault_event_types=dict(params.get("reserved_fault_event_types") or {}),
            ),
        )
        validation = dict(generated.get("validation") or {})
        return {
            "success": generated.get("success") is True,
            "generated_rule_count": validation.get("generated_rule_count", 0),
            "rules": validation.get("rules") or [],
            "reports": validation.get("reports") or [],
            "validation": validation,
            "agent_loop": generated.get("agent_loop"),
            "generation_provider": generation_provider,
        }
    result = generate_and_validate_event_rules(
        contract=contract,
        change_policy=change_policy,
        intent=params.get("intent"),
    )
    constraint_errors = _event_constraint_errors(
        list(result.get("rules") or []),
        dict(params.get("event_constraints") or {}),
    )
    if constraint_errors:
        result["success"] = False
        result["validation"] = {
            "success": False,
            "errors": constraint_errors,
        }
    if params.get("agent_loop"):
        result["agent_loop"] = _agent_loop(result, params)
    result["generation_provider"] = "builtin"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and validate EventRule DSL")
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    result = run_event_rule_generation(params)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
