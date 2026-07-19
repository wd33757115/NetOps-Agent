"""Compile LLM event intents into the platform's deterministic EventRule DSL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuleRole:
    name: str
    event_class: str
    condition: dict[str, Any]
    default_severity: str
    resolves_roles: tuple[str, ...] = ()


_ROLE_ALIASES = {
    "high": "high",
    "high_fault": "high",
    "upper": "high",
    "low": "low",
    "low_fault": "low",
    "lower": "low",
    "critical_low": "critical_low",
    "critical-low": "critical_low",
    "critical_high": "critical_high",
    "critical-high": "critical_high",
    "high_recovery": "high_recovery",
    "recovery_high": "high_recovery",
    "high-recovery": "high_recovery",
    "low_recovery": "low_recovery",
    "recovery_low": "low_recovery",
    "low-recovery": "low_recovery",
    "down": "down",
    "fault": "down",
    "up": "up",
    "recovery": "up",
}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")


def _target_fields(contract: dict[str, Any], observation_type: str) -> list[str]:
    semantics = dict(contract.get("state_semantics") or {})
    return [
        str(field)
        for field, metadata in semantics.items()
        if isinstance(metadata, dict)
        and str(metadata.get("observation_type") or "") == observation_type
    ]


def expected_rule_roles(
    *,
    contract: dict[str, Any],
    change_policy: dict[str, Any],
) -> tuple[list[RuleRole], list[str]]:
    """Return runtime roles implied only by Contract and ChangePolicy."""

    errors: list[str] = []
    observation_type = str(change_policy.get("observation_type") or "").strip()
    entity_type = str(contract.get("entity_type") or "").strip()
    fields = _target_fields(contract, observation_type)
    if not entity_type:
        errors.append("contract entity_type is required for EventRule compilation")
    if not fields:
        errors.append(
            "change policy observation_type is not mapped to a Contract observation field"
        )
    if errors:
        return [], errors

    base = {"entity_types": [entity_type], "fields": fields}
    compare_mode = str(change_policy.get("compare_mode") or "").strip()
    thresholds = {
        str(key): value
        for key, value in dict(change_policy.get("thresholds") or {}).items()
        if _number(value) is not None
    }
    deadband = abs(_number(change_policy.get("deadband")) or 0.0)
    explicit_recovery = _number(change_policy.get("recovery_threshold"))
    threshold_references = {
        str(role): dict(reference)
        for role, reference in dict(change_policy.get("threshold_references") or {}).items()
        if isinstance(reference, dict) and str(reference.get("field_name") or "").strip()
    }
    roles: list[RuleRole] = []

    if compare_mode in {
        "threshold_and_deadband",
        "threshold_low_and_deadband",
    }:
        high = _number(thresholds.get("high"))
        low = _number(thresholds.get("low"))
        critical_low = _number(thresholds.get("critical_low"))
        critical_high = _number(thresholds.get("critical_high"))
        high_reference = threshold_references.get("high")
        low_reference = threshold_references.get("low")
        critical_low_reference = threshold_references.get("critical_low")
        critical_high_reference = threshold_references.get("critical_high")
        high_fault_roles: list[str] = []
        if high is not None or high_reference:
            high_condition = (
                {
                    "kind": "threshold_field_gte",
                    **base,
                    "reference_field": high_reference["field_name"],
                }
                if high_reference
                else {"kind": "threshold", **base, "gte": high}
            )
            roles.append(
                RuleRole(
                    "high",
                    "fault",
                    high_condition,
                    "warning",
                )
            )
            high_fault_roles.append("high")
        critical_high_is_distinct = bool(
            critical_high_reference
            and (
                not high_reference
                or critical_high_reference["field_name"] != high_reference["field_name"]
            )
        ) or bool(
            critical_high is not None and (high is None or critical_high != high)
        )
        if critical_high_is_distinct:
            critical_high_condition = (
                {
                    "kind": "threshold_field_gte",
                    **base,
                    "reference_field": critical_high_reference["field_name"],
                }
                if critical_high_reference
                else {"kind": "threshold", **base, "gte": critical_high}
            )
            roles.append(
                RuleRole(
                    "critical_high",
                    "fault",
                    critical_high_condition,
                    "critical",
                )
            )
            high_fault_roles.append("critical_high")
        effective_high = high if high is not None else critical_high
        effective_high_reference = high_reference or critical_high_reference
        if high_fault_roles:
            recovery_high = (
                explicit_recovery
                if effective_high is not None
                and explicit_recovery is not None
                and explicit_recovery < effective_high
                else effective_high - deadband
                if effective_high is not None and deadband
                else None
            )
            if effective_high_reference and deadband:
                high_recovery_condition = {
                    "kind": "threshold_field_high_recovery",
                    **base,
                    "reference_field": effective_high_reference["field_name"],
                    "hysteresis": deadband,
                }
                fallback_fields = [
                    str(reference.get("field_name") or "")
                    for reference in (critical_high_reference,)
                    if reference
                    and str(reference.get("field_name") or "")
                    != str(effective_high_reference.get("field_name") or "")
                ]
                if fallback_fields:
                    high_recovery_condition["fallback_reference_fields"] = fallback_fields
            elif recovery_high is not None:
                high_recovery_condition = {
                    "kind": "threshold_recovery",
                    **base,
                    "lt": recovery_high,
                }
            else:
                high_recovery_condition = None
            if high_recovery_condition:
                roles.append(
                    RuleRole(
                        "high_recovery",
                        "recovery",
                        high_recovery_condition,
                        "info",
                        tuple(high_fault_roles),
                    )
                )
        low_fault_roles: list[str] = []
        if low is not None or low_reference:
            low_condition = (
                {
                    "kind": "threshold_field_lte",
                    **base,
                    "reference_field": low_reference["field_name"],
                }
                if low_reference
                else {"kind": "threshold_lte", **base, "lte": low}
            )
            roles.append(
                RuleRole(
                    "low",
                    "fault",
                    low_condition,
                    "warning",
                )
            )
            low_fault_roles.append("low")
        critical_low_is_distinct = bool(
            critical_low_reference
            and (
                not low_reference
                or critical_low_reference["field_name"] != low_reference["field_name"]
            )
        ) or bool(critical_low is not None and (low is None or critical_low != low))
        if critical_low_is_distinct:
            critical_condition = (
                {
                    "kind": "threshold_field_lte",
                    **base,
                    "reference_field": critical_low_reference["field_name"],
                }
                if critical_low_reference
                else {"kind": "threshold_lte", **base, "lte": critical_low}
            )
            roles.append(
                RuleRole(
                    "critical_low",
                    "fault",
                    critical_condition,
                    "critical",
                )
            )
            low_fault_roles.append("critical_low")
        effective_low = low if low is not None else critical_low
        effective_low_reference = low_reference or critical_low_reference
        if low_fault_roles:
            recovery_low = (
                explicit_recovery
                if compare_mode == "threshold_low_and_deadband"
                and effective_low is not None
                and explicit_recovery is not None
                and explicit_recovery > effective_low
                else effective_low + deadband
                if effective_low is not None and deadband
                else None
            )
            if effective_low_reference and deadband:
                low_recovery_condition = {
                    "kind": "threshold_field_low_recovery",
                    **base,
                    "reference_field": effective_low_reference["field_name"],
                    "hysteresis": deadband,
                }
                fallback_fields = [
                    str(reference.get("field_name") or "")
                    for reference in (critical_low_reference,)
                    if reference
                    and str(reference.get("field_name") or "")
                    != str(effective_low_reference.get("field_name") or "")
                ]
                if fallback_fields:
                    low_recovery_condition["fallback_reference_fields"] = fallback_fields
            elif recovery_low is not None:
                low_recovery_condition = {
                    "kind": "threshold_low_recovery",
                    **base,
                    "low": effective_low,
                    "gt": recovery_low,
                }
            else:
                low_recovery_condition = None
            if low_recovery_condition:
                roles.append(
                    RuleRole(
                        "low_recovery",
                        "recovery",
                        low_recovery_condition,
                        "info",
                        tuple(low_fault_roles),
                    )
                )
        if not roles:
            errors.append("numeric ChangePolicy does not define a usable high or low threshold")
        return roles, errors

    if compare_mode in {"state_transition", "enum_transition"}:
        return [
            RuleRole(
                "down",
                "fault",
                {
                    "kind": "transition",
                    **base,
                    "from_group": "up",
                    "to_group": "down",
                    "allow_initial_target": False,
                },
                "major",
            ),
            RuleRole(
                "up",
                "recovery",
                {
                    "kind": "transition",
                    **base,
                    "from_group": "down",
                    "to_group": "up",
                    "allow_initial_target": False,
                },
                "info",
                ("down",),
            ),
        ], []

    return [], []


def _infer_role(rule: dict[str, Any]) -> str | None:
    explicit = _ROLE_ALIASES.get(str(rule.get("trigger_role") or "").casefold())
    if explicit:
        return explicit
    condition = dict(rule.get("condition") or {})
    kind = str(condition.get("kind") or "").casefold()
    event_class = str(rule.get("event_class") or "fault").casefold()
    text = "_".join(
        _token(value)
        for value in (rule.get("event_type"), rule.get("name"), rule.get("description"))
    )
    recovery = event_class == "recovery" or any(
        word in text for word in ("recover", "restore", "clear", "normal")
    )
    is_critical_low = any(
        word in text for word in ("critical_low", "loss_of_light", "signal_loss", "los")
    )
    is_critical_high = "critical_high" in text
    is_low = is_critical_low or any(word in text for word in ("_low", "low_", "below", "under"))
    is_high = is_critical_high or any(
        word in text for word in ("_high", "high_", "above", "over")
    )
    if recovery and is_low:
        return "low_recovery"
    if recovery and is_high:
        return "high_recovery"
    if recovery and any(word in text for word in ("_up", "up_")):
        return "up"
    if is_critical_low:
        return "critical_low"
    if is_critical_high:
        return "critical_high"
    if is_low:
        return "low"
    if is_high:
        return "high"
    if kind == "threshold_low_recovery":
        return "low_recovery"
    if kind == "threshold_recovery":
        return "high_recovery"
    if kind == "threshold_lte":
        return "low"
    if kind == "threshold":
        return "high"
    if kind in {"transition", "bgp_transition"}:
        target = str(condition.get("to_group") or "").casefold()
        if target in {"down", "up"}:
            return target
    if kind == "entity_status_down":
        return "down"
    operator = str(condition.get("operator") or "").casefold()
    if operator in {"lt", "lte", "<", "<="} or "lt" in condition or "lte" in condition:
        return "low_recovery" if recovery else "low"
    if operator in {"gt", "gte", ">", ">="} or "gt" in condition or "gte" in condition:
        return "high_recovery" if recovery else "high"
    return "up" if recovery else None


def _fallback_event_type(observation_type: str, role: str) -> str:
    prefix = _token(observation_type) or "observation"
    return f"{prefix}_{role}"


def compile_event_rule_intents(
    *,
    candidate_rules: list[dict[str, Any]],
    contract: dict[str, Any],
    change_policy: dict[str, Any],
) -> dict[str, Any]:
    """Compile permissive LLM intent objects into executable, policy-bound rules."""

    roles, errors = expected_rule_roles(
        contract=contract,
        change_policy=change_policy,
    )
    if errors or not roles:
        return {
            "success": not errors,
            "rules": [dict(rule) for rule in candidate_rules],
            "errors": errors,
            "normalizations": [],
            "compiled": False,
        }

    observation_type = str(change_policy.get("observation_type") or "")
    classified: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, rule in enumerate(candidate_rules):
        if not isinstance(rule, dict):
            continue
        inferred = _infer_role(rule)
        if inferred:
            classified.setdefault(inferred, []).append((index, rule))

    used: set[int] = set()
    compiled_by_role: dict[str, dict[str, Any]] = {}
    normalizations: list[dict[str, Any]] = []
    for role in roles:
        selected: dict[str, Any] | None = None
        selected_index: int | None = None
        for index, candidate in classified.get(role.name, []):
            if index not in used:
                selected_index, selected = index, candidate
                break
        if selected_index is not None:
            used.add(selected_index)
        source = dict(selected or {})
        event_type = str(source.get("event_type") or "").strip() or _fallback_event_type(
            observation_type,
            role.name,
        )
        rule_name = str(source.get("name") or source.get("rule_id") or event_type)
        compiled = {
            "name": rule_name,
            "version": int(source.get("version") or 1),
            "event_type": event_type,
            "trigger_role": role.name,
            "severity": (
                "info"
                if role.event_class == "recovery"
                else role.default_severity
                if role.name in {"critical_low", "critical_high"}
                else str(source.get("severity") or role.default_severity)
            ),
            "description": str(
                source.get("description")
                or f"Generated {observation_type} {role.name} event candidate."
            ),
            "condition": dict(role.condition),
            "priority": int(source.get("priority") or 100),
            "exclusive": bool(source.get("exclusive", False)),
            "event_class": role.event_class,
            "recovery": {},
            "correlation_keys": list(
                source.get("correlation_keys")
                or source.get("correlation_key")
                or contract.get("primary_keys")
                or ["device_id", "entity_type", "entity_key", "field"]
            ),
        }
        compiled_by_role[role.name] = compiled
        normalizations.append(
            {
                "kind": "event_intent_compiled",
                "trigger_role": role.name,
                "source_rule_index": selected_index,
                "event_type": event_type,
            }
        )

    for role in roles:
        if not role.resolves_roles:
            continue
        resolves = [
            compiled_by_role[name]["event_type"]
            for name in role.resolves_roles
            if name in compiled_by_role
        ]
        compiled_by_role[role.name]["recovery"] = {"resolves": resolves}

    dropped = [
        {
            "kind": "unused_event_intent_dropped",
            "source_rule_index": index,
            "event_type": rule.get("event_type"),
            "inferred_role": _infer_role(rule),
        }
        for index, rule in enumerate(candidate_rules)
        if isinstance(rule, dict) and index not in used
    ]
    return {
        "success": True,
        "rules": [compiled_by_role[role.name] for role in roles],
        "errors": [],
        "normalizations": [*normalizations, *dropped],
        "compiled": True,
    }
