#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[7]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.change_policy import normalize_dimension_selector  # noqa: E402
from src.core.patrol.event_constraint_governance import (  # noqa: E402
    scope_event_constraints_to_policy,
)
from src.core.patrol.event_rule_compiler import (  # noqa: E402
    compile_event_rule_intents,
    govern_event_rule_severity,
    infer_event_rule_role,
)
from src.core.patrol.event_rule_generation import (  # noqa: E402
    generate_and_validate_event_rules,
)
from src.core.patrol.event_rules import EventRuleDefinition  # noqa: E402
from src.core.patrol.rule_validation import validate_event_rule_definition  # noqa: E402
from src.apps.rule_governance.backend.rule_control.asset_requirements import (  # noqa: E402
    event_rule_role_errors,
    normalize_rule_asset_requirements,
)
from src.apps.rule_governance.backend.rule_control.generation_agent import (  # noqa: E402
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
                "match rules to the contract canonical runtime entity type",
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
                    "entity_type": (
                        (params.get("contract") or {}).get(
                            "canonical_entity_type"
                        )
                        or (params.get("contract") or {}).get("entity_type")
                    )
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
    rule_asset_requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rules = candidate.get("rules") or []
    if not isinstance(rules, list) or not rules:
        return {"success": False, "errors": ["at least one event rule is required"]}
    compilation = compile_event_rule_intents(
        candidate_rules=rules,
        contract=contract,
        change_policy=change_policy,
        event_constraints=dict(event_constraints or {}),
        rule_asset_requirements=dict(rule_asset_requirements or {}),
    )
    rules = list(compilation.get("rules") or [])
    declared_fields = {
        str(item)
        for item in [
            *(contract.get("required_fields") or []),
            *(contract.get("optional_fields") or []),
        ]
    }
    entity_type = str(
        contract.get("canonical_entity_type")
        or contract.get("entity_type")
        or ""
    ).strip()
    reports: list[dict[str, Any]] = []
    errors: list[str] = [str(item) for item in compilation.get("errors") or []]
    fault_event_types: set[str] = set()
    recovery_rules: list[dict[str, Any]] = []
    normalized_rules: list[dict[str, Any]] = []
    guard_normalizations: list[dict[str, Any]] = []
    dimension_selector = normalize_dimension_selector(
        change_policy.get("dimension_selector")
    )
    for index, item in enumerate(rules):
        if not isinstance(item, dict):
            errors.append(f"rules[{index}] must be an object")
            continue
        condition, guard_changed = attach_governed_applicability(
            dict(item.get("condition") or {}), contract
        )
        proposed_dimensions = dict(condition.get("dimensions") or {})
        if dimension_selector:
            condition["dimensions"] = dimension_selector
            if proposed_dimensions != dimension_selector:
                guard_normalizations.append(
                    {
                        "kind": "dimension_scope_governance",
                        "rule_index": index,
                        "event_type": item.get("event_type"),
                        "proposed": proposed_dimensions,
                        "effective": dimension_selector,
                    }
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
                "rules["
                f"{index}].condition.entity_types must include the contract "
                "canonical runtime entity type"
            )
        if entity_type and entity_types and entity_type not in entity_types:
            errors.append(
                f"rules[{index}] does not include the contract canonical "
                "runtime entity type"
            )
        proposed_correlation_keys = tuple(
            item.get("correlation_keys")
            or item.get("correlation_key")
            or ("device_id", "entity_type", "entity_key", "field")
        )
        if dimension_selector:
            proposed_correlation_keys = tuple(
                dict.fromkeys(
                    (*proposed_correlation_keys, *sorted(dimension_selector))
                )
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
            evaluation_scope=item.get("evaluation_scope"),
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
            "evaluation_scope": definition.evaluation_scope,
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
            dimension_selector=dimension_selector,
        )
    )
    governance_repairs = [
        dict(item)
        for item in normalizations
        if isinstance(item, dict)
        and item.get("kind") in {"event_type_governance", "severity_governance"}
    ]
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
            rule_asset_requirements=rule_asset_requirements,
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
    if rule_asset_requirements:
        errors.extend(
            event_rule_role_errors(
                requirements=rule_asset_requirements,
                event_rule_roles=normalized_rules,
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
        "governance_repairs": governance_repairs,
        "compiler": {
            "compiled": bool(compilation.get("compiled")),
            "normalization_count": len(compilation.get("normalizations") or []),
        },
    }


def _rule_name_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "rule").casefold()).strip("-")


def _event_type_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "observation").casefold()).strip("_")


def _govern_fanout_event_types(
    scoped_results: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Give every observation target an unambiguous, stable event namespace."""

    claims: dict[str, list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]]] = {}
    for observation_type, result, constraints in scoped_results:
        for rule in result.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            event_type = str(rule.get("event_type") or "").strip()
            if event_type:
                claims.setdefault(event_type, []).append(
                    (observation_type, result, constraints, rule)
                )

    repairs: list[dict[str, Any]] = []
    errors: list[str] = []
    renamed_faults: dict[str, dict[str, str]] = {}
    effective_owners: dict[str, tuple[str, str]] = {}
    for event_type, event_claims in sorted(claims.items()):
        observation_types = {claim[0] for claim in event_claims}
        if len(observation_types) <= 1:
            claim = event_claims[0]
            role = str(
                claim[3].get("trigger_role") or infer_event_rule_role(claim[3]) or ""
            )
            effective_owners[event_type] = (claim[0], role)
            continue

        locked: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for claim in event_claims:
            observation_type, _result, constraints, rule = claim
            role = str(rule.get("trigger_role") or infer_event_rule_role(rule) or "")
            role_mapping = dict(constraints.get("role_event_types") or {})
            required_types = {str(item) for item in constraints.get("required_event_types") or []}
            if str(role_mapping.get(role) or "") == event_type or event_type in required_types:
                locked.append(claim)
        if len(locked) > 1:
            errors.append(
                f"event_type {event_type} is explicitly required by multiple observation targets: "
                + ", ".join(sorted(observation_types))
            )
            continue

        locked_claim = locked[0] if locked else None
        if locked_claim:
            locked_role = str(
                locked_claim[3].get("trigger_role")
                or infer_event_rule_role(locked_claim[3])
                or ""
            )
            effective_owners[event_type] = (locked_claim[0], locked_role)

        for observation_type, result, _constraints, rule in event_claims:
            if locked_claim is not None and rule is locked_claim[3]:
                continue
            role = str(
                rule.get("trigger_role") or infer_event_rule_role(rule) or ""
            ).strip()
            if not role:
                errors.append(
                    f"event_type {event_type} is shared across observation targets but its "
                    f"trigger role cannot be inferred for {observation_type}"
                )
                continue
            effective = f"{_event_type_token(observation_type)}_{_event_type_token(role)}"
            owner = effective_owners.get(effective)
            if owner and owner != (observation_type, role):
                errors.append(
                    f"target-specific event_type {effective} collides between "
                    f"{owner[0]} and {observation_type}"
                )
                continue
            effective_owners[effective] = (observation_type, role)
            rule["event_type"] = effective
            repair = {
                "kind": "cross_target_event_type_namespace",
                "observation_type": observation_type,
                "trigger_role": role,
                "proposed": event_type,
                "effective": effective,
                "reason": "event_type_reused_across_observation_targets",
                "source": "deterministic_control_plane",
            }
            repairs.append(repair)
            validation = result.get("validation")
            if isinstance(validation, dict):
                validation.setdefault("normalizations", []).append(repair)
                validation.setdefault("governance_repairs", []).append(repair)
            loop = result.get("agent_loop")
            if (
                isinstance(loop, dict)
                and isinstance(loop.get("attempts"), list)
                and loop["attempts"]
            ):
                last_attempt = loop["attempts"][-1]
                if isinstance(last_attempt, dict):
                    last_attempt.setdefault("governance_repairs", []).append(repair)
            if str(rule.get("event_class") or "fault") != "recovery":
                renamed_faults.setdefault(observation_type, {})[event_type] = effective

    for observation_type, result, _constraints in scoped_results:
        fault_mapping = renamed_faults.get(observation_type) or {}
        if not fault_mapping:
            continue
        for rule in result.get("rules") or []:
            if not isinstance(rule, dict) or str(rule.get("event_class") or "fault") != "recovery":
                continue
            recovery = dict(rule.get("recovery") or {})
            recovery["resolves"] = [
                fault_mapping.get(str(value), str(value))
                for value in recovery.get("resolves") or []
            ]
            rule["recovery"] = recovery
    return repairs, list(dict.fromkeys(errors))


def _normalize_rule_ir(
    rules: list[dict[str, Any]],
    *,
    observation_type: str,
    constraints: dict[str, Any],
    dimension_selector: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compile unambiguous names and precedence without changing rule semantics."""

    changes: list[dict[str, Any]] = []
    selector = normalize_dimension_selector(dimension_selector)
    selector_token = "-".join(
        f"{_rule_name_token(key)}-{_rule_name_token(value)}"
        for key, value in selector.items()
    )
    namespace = _rule_name_token(observation_type)
    if selector_token:
        namespace = f"{namespace}-{selector_token}"
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
    required_exclusive_types = {
        str(event_type)
        for event_type in constraints.get("required_exclusive_event_types") or []
        if str(event_type)
    }
    for rule in rules:
        event_type = str(rule.get("event_type") or "")
        if event_type not in required_exclusive_types or bool(rule.get("exclusive")):
            continue
        rule["exclusive"] = True
        changes.append(
            {
                "kind": "exclusive_precedence",
                "rule": rule.get("name"),
                "from": False,
                "to": True,
                "reason": f"{event_type} supersedes lower-severity matches",
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
    rule_asset_requirements: dict[str, Any] | None = None,
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
    normalized_requirements = normalize_rule_asset_requirements(
        rule_asset_requirements
    )
    current_state_only = bool(
        normalized_requirements.get("requires_current_state_rule")
        and not normalized_requirements.get("requires_event_rule")
    )
    for rule in rules:
        condition = dict(rule.get("condition") or {})
        fields = {str(item) for item in condition.get("fields") or []}
        if fields and not fields.intersection(target_fields):
            errors.append(f"rule {rule.get('name')} does not target the ChangePolicy field")
    if compare_mode == "threshold_low_and_deadband":
        threshold_values = dict(change_policy.get("thresholds") or {})
        low_specs = [
            (
                role,
                threshold_values.get(role),
                dict(threshold_references.get(role) or {}),
            )
            for role in ("low", "critical_low")
            if threshold_values.get(role) is not None or threshold_references.get(role)
        ]
        if not low_specs:
            errors.append("low-threshold policy has no usable low threshold")
        for role, value, reference in low_specs:
            expected_kind = "threshold_field_lte" if reference else "threshold_lte"
            matching_faults = [
                rule
                for rule in fault_rules
                if (rule.get("condition") or {}).get("kind") == expected_kind
                and target_fields.intersection((rule.get("condition") or {}).get("fields") or [])
            ]
            if reference:
                matched = any(
                    str((rule.get("condition") or {}).get("reference_field") or "")
                    == str(reference.get("field_name") or "")
                    for rule in matching_faults
                )
            else:
                matched = any(
                    _numbers_equal((rule.get("condition") or {}).get("lte"), value)
                    for rule in matching_faults
                )
            if not matched:
                errors.append(f"fault rule must match ChangePolicy {role} threshold")
        primary_role, primary_value, primary_reference = next(
            (item for item in low_specs if item[0] == "low"),
            low_specs[0] if low_specs else ("low", None, {}),
        )
        recovery_threshold = change_policy.get("recovery_threshold")
        deadband = change_policy.get("deadband")
        expected_recovery = (
            recovery_threshold
            if recovery_threshold is not None
            else float(primary_value) + float(deadband)
            if primary_value is not None and deadband
            else None
        )
        if not current_state_only and (
            expected_recovery is not None or (primary_reference and deadband)
        ):
            matching_recovery = [
                rule
                for rule in recovery_rules
                if (rule.get("condition") or {}).get("kind")
                == (
                    "threshold_field_low_recovery"
                    if primary_reference
                    else "threshold_low_recovery"
                )
            ]
            if not matching_recovery:
                errors.append("low-threshold policy with hysteresis requires a recovery rule")
            elif primary_reference and not any(
                str((rule.get("condition") or {}).get("reference_field") or "")
                == str(primary_reference.get("field_name") or "")
                for rule in matching_recovery
            ):
                errors.append("recovery rule reference must match the ChangePolicy")
            elif not primary_reference and not any(
                _numbers_equal((rule.get("condition") or {}).get("low"), primary_value)
                and _numbers_equal((rule.get("condition") or {}).get("gt"), expected_recovery)
                for rule in matching_recovery
            ):
                errors.append("recovery rule thresholds must match the ChangePolicy")
    elif compare_mode == "threshold_and_deadband":
        threshold_values = dict(change_policy.get("thresholds") or {})
        high_specs = [
            (
                role,
                threshold_values.get(role),
                dict(threshold_references.get(role) or {}),
            )
            for role in ("high", "critical_high")
            if threshold_values.get(role) is not None or threshold_references.get(role)
        ]
        if not high_specs:
            errors.append("high-threshold policy has no usable high threshold")
        for role, value, reference in high_specs:
            expected_kind = "threshold_field_gte" if reference else "threshold"
            matching_faults = [
                rule
                for rule in fault_rules
                if (rule.get("condition") or {}).get("kind") == expected_kind
                and target_fields.intersection((rule.get("condition") or {}).get("fields") or [])
            ]
            if reference:
                matched = any(
                    str((rule.get("condition") or {}).get("reference_field") or "")
                    == str(reference.get("field_name") or "")
                    for rule in matching_faults
                )
            else:
                matched = any(
                    _numbers_equal((rule.get("condition") or {}).get("gte"), value)
                    for rule in matching_faults
                )
            if not matched:
                errors.append(f"fault rule must match ChangePolicy {role} threshold")
        _primary_role, primary_value, primary_reference = next(
            (item for item in high_specs if item[0] == "high"),
            high_specs[0] if high_specs else ("high", None, {}),
        )
        recovery_threshold = change_policy.get("recovery_threshold")
        deadband = change_policy.get("deadband")
        expected_recovery = (
            recovery_threshold
            if recovery_threshold is not None
            else float(primary_value) - float(deadband)
            if primary_value is not None and deadband
            else None
        )
        if (
            not current_state_only
            and (expected_recovery is not None or (primary_reference and deadband))
            and not any(
                (rule.get("condition") or {}).get("kind")
                == (
                    "threshold_field_high_recovery"
                    if primary_reference
                    else "threshold_recovery"
                )
                and (
                    str((rule.get("condition") or {}).get("reference_field") or "")
                    == str(primary_reference.get("field_name") or "")
                    if primary_reference
                    else _numbers_equal(
                        (rule.get("condition") or {}).get("lt"),
                        expected_recovery,
                    )
                )
                for rule in recovery_rules
            )
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
    return [
        (
            f"event type {rule.get('event_type')} uses critical severity without "
            "explicit event_constraints authorization; use major or warning"
        )
        for rule in rules
        if str(rule.get("severity") or "warning").casefold() == "critical"
        and govern_event_rule_severity(
            event_type=str(rule.get("event_type") or ""),
            proposed_severity="critical",
            constraints=constraints,
        )[1]
        is not None
    ]


def _govern_llm_event_rule_candidate(
    candidate: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply platform policy to model output without inventing event semantics."""

    governed = copy.deepcopy(candidate)
    rules = governed.get("rules")
    if not isinstance(rules, list):
        return governed, []

    constraints = dict(context.get("event_constraints") or {})
    if constraints.get("allow_critical") is True:
        return governed, []

    repairs: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        event_type = str(rule.get("event_type") or "")
        proposed = str(rule.get("severity") or "warning").casefold()
        effective, repair = govern_event_rule_severity(
            event_type=event_type,
            proposed_severity=proposed,
            constraints=constraints,
        )
        if repair is None:
            continue
        rule["severity"] = effective
        repairs.append(
            {
                **repair,
                "path": f"rules[{index}].severity",
            }
        )
    return governed, repairs


def _numbers_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return False


def _policy_target_key(
    observation_type: Any,
    dimension_selector: Any,
) -> str:
    selector = normalize_dimension_selector(dimension_selector)
    return (
        f"{str(observation_type or '').strip()}:"
        + json.dumps(
            selector,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _expand_dimension_dispatch_policies(
    policies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose dimension branches to generation while runtime keeps one asset."""

    expanded: list[dict[str, Any]] = []
    for raw_policy in policies:
        policy = dict(raw_policy)
        if str(policy.get("compare_mode") or "") != "dimension_dispatch":
            expanded.append(policy)
            continue
        observation_type = str(policy.get("observation_type") or "")
        semantic_type = str(policy.get("semantic_type") or "")
        for branch in policy.get("dimension_policies") or []:
            if not isinstance(branch, dict) or not isinstance(
                branch.get("policy"), dict
            ):
                continue
            child = dict(branch["policy"])
            child.setdefault("observation_type", observation_type)
            child.setdefault("semantic_type", semantic_type)
            child["dimension_selector"] = normalize_dimension_selector(
                branch.get("selector")
            )
            expanded.append(child)
    return expanded


def _scoped_rule_asset_requirements(
    raw_requirements: dict[str, Any] | None,
    *,
    target: dict[str, Any] | None,
    observation_type: str,
) -> dict[str, Any] | None:
    """Limit logical rule roles to the observation handled by one fanout call."""

    if not raw_requirements:
        return None
    requirements = normalize_rule_asset_requirements(raw_requirements)
    target_roles = {
        str(item).strip()
        for item in (target or {}).get("required_rule_roles") or []
        if str(item).strip()
    }
    requires_current = bool(
        "current_state" in target_roles
        or observation_type in set(
            requirements.get("current_state_observation_types") or []
        )
    )
    required_event_types = dict(
        requirements.get("required_event_types_by_observation") or {}
    )
    requires_change_event = bool(
        "change_event" in target_roles
        or (
            not target_roles
            and (
                observation_type in required_event_types
                or requirements.get("requires_event_rule") is True
            )
        )
    )
    return {
        **requirements,
        "requires_current_state_rule": requires_current,
        "requires_event_rule": requires_change_event,
        "requires_event_rule_assets": (
            requires_current or requires_change_event
        ),
        "requires_historical_replay": bool(
            requires_change_event
            and requirements.get("requires_historical_replay") is True
        ),
        "current_state_observation_types": (
            [observation_type] if requires_current else []
        ),
        "change_policy_observation_types": (
            [observation_type]
            if observation_type
            in set(requirements.get("change_policy_observation_types") or [])
            else []
        ),
        "required_event_types_by_observation": (
            {observation_type: required_event_types[observation_type]}
            if requires_change_event and observation_type in required_event_types
            else {}
        ),
    }


def _scoped_event_constraints(
    constraints: dict[str, Any],
    *,
    target: dict[str, Any] | None,
) -> dict[str, Any]:
    """Remove recovery-only constraints from current-state-only fan-out targets."""

    scoped = dict(constraints or {})
    target_roles = {
        str(item).strip()
        for item in (target or {}).get("required_rule_roles") or []
        if str(item).strip()
    }
    if not target_roles or "change_event" in target_roles:
        return scoped
    if "current_state" not in target_roles:
        return scoped

    role_event_types = dict(scoped.get("role_event_types") or {})
    event_classes = dict(scoped.get("required_event_classes") or {})
    recovery_pairs = [
        dict(item)
        for item in scoped.get("required_recovery_pairs") or []
        if isinstance(item, dict)
    ]
    recovery_event_types = {
        str(item.get("recovery") or "").strip()
        for item in recovery_pairs
        if str(item.get("recovery") or "").strip()
    }
    recovery_event_types.update(
        str(event_type)
        for event_type, event_class in event_classes.items()
        if str(event_class).strip().casefold() == "recovery"
    )
    filtered_roles = {
        str(role): event_type
        for role, event_type in role_event_types.items()
        if "recovery" not in str(role).casefold()
        and str(event_type) not in recovery_event_types
    }
    allowed_event_types = set(filtered_roles.values())
    if not allowed_event_types:
        allowed_event_types = {
            str(event_type)
            for event_type in scoped.get("required_event_types") or []
            if str(event_type) not in recovery_event_types
        }

    scoped["role_event_types"] = filtered_roles
    scoped["required_event_types"] = [
        event_type
        for event_type in scoped.get("required_event_types") or []
        if str(event_type) in allowed_event_types
    ]
    scoped["required_event_classes"] = {
        event_type: event_class
        for event_type, event_class in event_classes.items()
        if str(event_type) in allowed_event_types
    }
    scoped["required_severities"] = {
        event_type: severity
        for event_type, severity in dict(
            scoped.get("required_severities") or {}
        ).items()
        if str(event_type) in allowed_event_types
    }
    scoped["required_recovery_pairs"] = []
    if scoped.get("max_rule_count") is not None and allowed_event_types:
        scoped["max_rule_count"] = min(
            int(scoped["max_rule_count"]),
            len(allowed_event_types),
        )
    return scoped


def run_event_rule_generation(params: dict[str, Any]) -> dict[str, Any]:
    contract = params.get("contract")
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    change_policies = params.get("change_policies")
    if isinstance(change_policies, list) and change_policies:
        change_policies = _expand_dimension_dispatch_policies(
            [dict(item) for item in change_policies if isinstance(item, dict)]
        )
        event_policy_targets = [
            dict(item)
            for item in params.get("event_policy_targets") or []
            if isinstance(item, dict)
        ]
        targets_by_key = {
            _policy_target_key(
                target.get("observation_type"),
                target.get("dimension_selector"),
            ): target
            for target in event_policy_targets
        }
        if event_policy_targets:
            change_policies = [
                policy
                for policy in change_policies
                if _policy_target_key(
                    policy.get("observation_type"),
                    policy.get("dimension_selector"),
                )
                in targets_by_key
            ]
        event_observation_types = params.get("event_observation_types")
        if not event_policy_targets and isinstance(event_observation_types, list):
            eligible = {
                str(item).strip()
                for item in event_observation_types
                if str(item).strip()
            }
            change_policies = [
                policy
                for policy in change_policies
                if (
                    isinstance(policy, dict)
                    and str(policy.get("observation_type") or "").strip() in eligible
                )
            ]
        results: list[dict[str, Any]] = []
        scoped_results: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        attempts: list[dict[str, Any]] = []
        child_governance_repairs: list[dict[str, Any]] = []
        rules_by_name: dict[str, dict[str, Any]] = {}
        conflicts: list[str] = []
        for index, policy in enumerate(change_policies):
            if not isinstance(policy, dict):
                raise ValueError(f"change_policies[{index}] must be an object")
            constraints_by_type = params.get("event_constraints_by_observation_type")
            observation_type = str(policy.get("observation_type") or "")
            target = targets_by_key.get(
                _policy_target_key(
                    observation_type,
                    policy.get("dimension_selector"),
                )
            )
            scoped_constraints = (
                (target or {}).get("event_constraints")
                or constraints_by_type.get(observation_type)
                if isinstance(constraints_by_type, dict)
                else (target or {}).get("event_constraints")
            )
            scoped_constraints = _scoped_event_constraints(
                dict(scoped_constraints or params.get("event_constraints") or {}),
                target=target,
            )
            scoped_contract = (
                (target or {}).get("event_contract")
                if isinstance((target or {}).get("event_contract"), dict)
                else contract
            )
            result = run_event_rule_generation(
                {
                    **params,
                    "change_policies": None,
                    "contract": scoped_contract,
                    "change_policy": policy,
                    "rule_asset_requirements": _scoped_rule_asset_requirements(
                        dict(params.get("rule_asset_requirements") or {}),
                        target=target,
                        observation_type=observation_type,
                    ),
                    "event_constraints": scoped_constraints,
                    "reserved_fault_event_types": {},
                }
            )
            results.append(result)
            effective_constraints = dict(
                result.get("effective_event_constraints")
                or scoped_constraints
                or {}
            )
            scoped_results.append(
                (
                    observation_type,
                    result,
                    effective_constraints,
                )
            )
            loop = result.get("agent_loop")
            if isinstance(loop, dict):
                for attempt in loop.get("attempts") or []:
                    if isinstance(attempt, dict):
                        attempts.append({**attempt, "attempt": len(attempts) + 1})
            child_governance_repairs.extend(
                dict(item)
                for item in result.get("governance_repairs") or []
                if isinstance(item, dict)
            )
        fanout_repairs, governance_errors = _govern_fanout_event_types(
            scoped_results
        )
        governance_repairs = list(
            {
                json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ): item
                for item in [
                    *child_governance_repairs,
                    *fanout_repairs,
                ]
            }.values()
        )
        conflicts.extend(governance_errors)
        for observation_type, result, scoped_constraints in scoped_results:
            if result.get("success") is not True:
                child_errors = list(
                    (result.get("validation") or {}).get("errors") or []
                )
                if not child_errors:
                    child_errors = ["event rule generation failed"]
                conflicts.extend(
                    f"{observation_type}: {message}"
                    for message in child_errors
                )
            constraint_errors = _event_constraint_errors(
                list(result.get("rules") or []), scoped_constraints
            )
            if constraint_errors:
                conflicts.extend(
                    f"{observation_type}: {message}" for message in constraint_errors
                )
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
        success = all(result.get("success") is True for result in results) and not conflicts
        role_errors = event_rule_role_errors(
            requirements=dict(params.get("rule_asset_requirements") or {}),
            event_rule_roles=list(rules_by_name.values()),
        ) if params.get("rule_asset_requirements") else []
        conflicts.extend(role_errors)
        success = success and not role_errors
        return {
            "success": success,
            "mode": "change_policy_fanout",
            "generated_rule_count": len(rules_by_name),
            "rules": list(rules_by_name.values()),
            "results": results,
            "validation": {
                "success": success,
                "errors": list(dict.fromkeys(conflicts)),
                "governance_repairs": governance_repairs,
            },
            "governance_repairs": governance_repairs,
            "agent_loop": {
                "ready_for_approval": success,
                "next_action": (
                    "submit_for_validation" if success else "revise_event_rule_candidate"
                ),
                "attempt_count": len(attempts),
                "attempts": attempts,
                "governance_repairs": governance_repairs,
            },
            "generation_provider": str(params.get("generation_provider") or "builtin").casefold(),
        }
    change_policy = params.get("change_policy")
    if not isinstance(change_policy, dict):
        raise ValueError("change_policy must be an object")
    constraint_scope = scope_event_constraints_to_policy(
        dict(params.get("event_constraints") or {}),
        change_policy,
    )
    event_constraints = dict(constraint_scope.get("constraints") or {})
    if constraint_scope.get("success") is not True:
        errors = list(constraint_scope.get("errors") or [])
        return {
            "success": False,
            "generated_rule_count": 0,
            "rules": [],
            "reports": [],
            "validation": {
                "success": False,
                "errors": errors,
                "normalizations": list(
                    constraint_scope.get("normalizations") or []
                ),
            },
            "agent_loop": {
                "ready_for_approval": False,
                "next_action": "revise_change_policy",
                "attempt_count": 0,
                "attempts": [],
            },
            "effective_event_constraints": event_constraints,
            "event_constraint_scope": constraint_scope,
            "generation_provider": str(
                params.get("generation_provider") or "builtin"
            ).casefold(),
        }
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
                "event_constraints": event_constraints,
                "reserved_fault_event_types": dict(params.get("reserved_fault_event_types") or {}),
                "rule_asset_requirements": dict(
                    params.get("rule_asset_requirements") or {}
                ),
            },
            validator=lambda candidate: _validate_llm_rules(
                candidate,
                contract=contract,
                change_policy=change_policy,
                event_constraints=event_constraints,
                reserved_fault_event_types=dict(params.get("reserved_fault_event_types") or {}),
                rule_asset_requirements=dict(
                    params.get("rule_asset_requirements") or {}
                ),
            ),
            candidate_governor=_govern_llm_event_rule_candidate,
        )
        validation = dict(generated.get("validation") or {})
        return {
            "success": generated.get("success") is True,
            "generated_rule_count": validation.get("generated_rule_count", 0),
            "rules": validation.get("rules") or [],
            "reports": validation.get("reports") or [],
            "validation": validation,
            "agent_loop": generated.get("agent_loop"),
            "effective_event_constraints": event_constraints,
            "event_constraint_scope": constraint_scope,
            "generation_provider": generation_provider,
        }
    result = generate_and_validate_event_rules(
        contract=contract,
        change_policy=change_policy,
        intent=params.get("intent"),
    )
    if event_constraints or params.get("rule_asset_requirements"):
        governed = _validate_llm_rules(
            {"rules": list(result.get("rules") or [])},
            contract=contract,
            change_policy=change_policy,
            event_constraints=event_constraints,
            reserved_fault_event_types=dict(
                params.get("reserved_fault_event_types") or {}
            ),
            rule_asset_requirements=dict(
                params.get("rule_asset_requirements") or {}
            ),
        )
        result["success"] = (
            result.get("success") is True
            and governed.get("success") is True
        )
        result["rules"] = list(governed.get("rules") or [])
        result["generated_rule_count"] = len(result["rules"])
        result["reports"] = list(governed.get("reports") or [])
        result["validation"] = governed
        result["governance_repairs"] = list(
            governed.get("governance_repairs") or []
        )
    constraint_errors = _event_constraint_errors(
        list(result.get("rules") or []),
        event_constraints,
    )
    role_errors = (
        event_rule_role_errors(
            requirements=dict(params.get("rule_asset_requirements") or {}),
            event_rule_roles=list(result.get("rules") or []),
        )
        if params.get("rule_asset_requirements")
        else []
    )
    constraint_errors.extend(role_errors)
    if constraint_errors:
        result["success"] = False
        result["validation"] = {
            "success": False,
            "errors": constraint_errors,
        }
    if params.get("agent_loop"):
        result["agent_loop"] = _agent_loop(result, params)
    result["effective_event_constraints"] = event_constraints
    result["event_constraint_scope"] = constraint_scope
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
