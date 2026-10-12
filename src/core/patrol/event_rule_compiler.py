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
    # A direction rule outranks the plain change rule so one change yields one event.
    priority: int = 100
    exclusive: bool = False
    evaluation_scope: str | None = None


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
    "change": "change",
    "changed": "change",
    "lifecycle_change": "change",
    "baseline_degradation": "baseline_degradation",
}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")


def govern_event_rule_severity(
    *,
    event_type: str,
    proposed_severity: str,
    constraints: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Enforce platform severity policy without changing event conditions."""

    proposed = str(proposed_severity or "warning").casefold()
    if proposed != "critical":
        return proposed, None
    policy = dict(constraints or {})
    required = {
        str(item_type): str(severity).casefold()
        for item_type, severity in dict(policy.get("required_severities") or {}).items()
    }
    allowed = policy.get("allow_critical") is True or event_type in {
        str(item) for item in policy.get("allow_critical_event_types") or [] if str(item)
    }
    allowed = allowed or required.get(event_type) == "critical"
    if allowed:
        return proposed, None
    configured = str(policy.get("unauthorized_critical_fallback") or "major").casefold()
    fallback = required.get(event_type)
    effective = (
        fallback
        if fallback in {"major", "warning"}
        else configured
        if configured in {"major", "warning"}
        else "major"
    )
    return effective, {
        "kind": "severity_governance",
        "event_type": event_type,
        "proposed": proposed,
        "effective": effective,
        "reason": "critical_requires_explicit_authorization",
        "source": "deterministic_control_plane",
    }


def _target_fields(contract: dict[str, Any], observation_type: str) -> list[str]:
    semantics = dict(contract.get("state_semantics") or {})
    return [
        str(field)
        for field, metadata in semantics.items()
        if isinstance(metadata, dict)
        and str(metadata.get("observation_type") or "") == observation_type
    ]


def _state_values(
    contract: dict[str, Any],
    observation_type: str,
) -> tuple[list[str], list[str]]:
    """Return explicit canonical healthy/unhealthy values for one target."""

    semantics = dict(contract.get("state_semantics") or {})
    healthy: set[str] = set()
    unhealthy: set[str] = set()
    for metadata in semantics.values():
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("observation_type") or "") != observation_type:
            continue
        groups = dict(metadata.get("state_groups") or {})
        healthy.update(str(item) for item in groups.get("healthy") or [] if str(item))
        unhealthy.update(str(item) for item in groups.get("unhealthy") or [] if str(item))
    return sorted(healthy), sorted(unhealthy)


def expected_rule_roles(
    *,
    contract: dict[str, Any],
    change_policy: dict[str, Any],
    event_constraints: dict[str, Any] | None = None,
) -> tuple[list[RuleRole], list[str]]:
    """Return runtime roles implied only by Contract and ChangePolicy."""

    errors: list[str] = []
    observation_type = str(change_policy.get("observation_type") or "").strip()
    entity_type = str(
        contract.get("canonical_entity_type")
        or contract.get("entity_type")
        or ""
    ).strip()
    fields = _target_fields(contract, observation_type)
    if not entity_type:
        errors.append(
            "contract canonical_entity_type or entity_type is required for "
            "EventRule compilation"
        )
    if not fields:
        errors.append(
            "change policy observation_type is not mapped to a Contract observation field"
        )
    if errors:
        return [], errors

    base = {
        "entity_types": [entity_type],
        "fields": fields,
        "observation_types": [observation_type],
    }
    dimension_selector = dict(change_policy.get("dimension_selector") or {})
    compare_mode = str(change_policy.get("compare_mode") or "").strip()
    if any(dict(contract.get("state_semantics", {}).get(field) or {}).get("event_on_change") for field in fields):
        change = RuleRole("change", "change", {"kind": "field_change", **base, "allow_initial": False}, "info")
        healthy_values, unhealthy_values = _state_values(contract, observation_type)
        declared = dict(event_constraints or {}).get("role_event_types") or {}
        if (
            compare_mode not in {"state_transition", "enum_transition"}
            or not {"down", "up"} <= set(declared)
            or not healthy_values
            or not unhealthy_values
        ):
            return [change], []
        # Only a move between the declared groups is a fault or its recovery; an
        # object that was already abnormal (an unused port) raises nothing.
        move = {"kind": "transition", **base, "allow_initial_target": False}
        return [
            RuleRole(
                "down", "fault",
                {**move, "from_group": "up", "to_group": "down", "from_values": healthy_values, "to_values": unhealthy_values},
                "major", priority=200, exclusive=True, evaluation_scope="change",
            ),
            RuleRole(
                "up", "recovery",
                {**move, "from_group": "down", "to_group": "up", "from_values": unhealthy_values, "to_values": healthy_values},
                "info", ("down",), priority=200, exclusive=True, evaluation_scope="change",
            ),
            change,
        ], []
    if dimension_selector:
        base["dimensions"] = dimension_selector
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
                    "threshold_role": "high",
                    **base,
                    "reference_field": high_reference["field_name"],
                }
                if high_reference
                else {
                    "kind": "threshold",
                    "threshold_role": "high",
                    **base,
                    "gte": high,
                }
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
        ) or bool(critical_high is not None and (high is None or critical_high != high))
        if critical_high_is_distinct:
            critical_high_condition = (
                {
                    "kind": "threshold_field_gte",
                    "threshold_role": "critical_high",
                    **base,
                    "reference_field": critical_high_reference["field_name"],
                }
                if critical_high_reference
                else {
                    "kind": "threshold",
                    "threshold_role": "critical_high",
                    **base,
                    "gte": critical_high,
                }
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
        effective_high_role = (
            "high" if high is not None or high_reference else "critical_high"
        )
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
                    "threshold_role": effective_high_role,
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
                    "threshold_role": effective_high_role,
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
                    "threshold_role": "low",
                    **base,
                    "reference_field": low_reference["field_name"],
                }
                if low_reference
                else {
                    "kind": "threshold_lte",
                    "threshold_role": "low",
                    **base,
                    "lte": low,
                }
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
                    "threshold_role": "critical_low",
                    **base,
                    "reference_field": critical_low_reference["field_name"],
                }
                if critical_low_reference
                else {
                    "kind": "threshold_lte",
                    "threshold_role": "critical_low",
                    **base,
                    "lte": critical_low,
                }
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
        effective_low_role = (
            "low" if low is not None or low_reference else "critical_low"
        )
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
                    "threshold_role": effective_low_role,
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
                    "threshold_role": effective_low_role,
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
        baseline = dict(change_policy.get("baseline_comparison") or {})
        if baseline:
            roles.append(
                RuleRole(
                    "baseline_degradation",
                    "fault",
                    {
                        "kind": "linear_power_baseline_loss",
                        **base,
                        "max_loss_ratio": float(baseline["max_loss_ratio"]),
                        "identity_fields": list(baseline["identity_fields"]),
                    },
                    "warning",
                )
            )
        if not roles:
            errors.append("numeric ChangePolicy does not define a usable high or low threshold")
        return roles, errors

    if compare_mode in {"state_transition", "enum_transition"}:
        healthy_values, unhealthy_values = _state_values(contract, observation_type)
        down_condition = {
            "kind": "transition",
            **base,
            "from_group": "up",
            "to_group": "down",
            "allow_initial_target": False,
        }
        up_condition = {
            "kind": "transition",
            **base,
            "from_group": "down",
            "to_group": "up",
            "allow_initial_target": False,
        }
        if healthy_values and unhealthy_values:
            down_condition.update(
                from_values=healthy_values,
                to_values=unhealthy_values,
            )
            up_condition.update(
                from_values=unhealthy_values,
                to_values=healthy_values,
            )
        return [
            RuleRole(
                "down",
                "fault",
                down_condition,
                "major",
            ),
            RuleRole(
                "up",
                "recovery",
                up_condition,
                "info",
                ("down",),
            ),
        ], []

    if compare_mode in {"exact", "hash_diff", "value_change"}:
        return [
            RuleRole(
                "change",
                "fault",
                {
                    "kind": "field_change",
                    **base,
                    "allow_initial": False,
                },
                "warning",
            )
        ], []

    return [], []


def infer_event_rule_role(rule: dict[str, Any]) -> str | None:
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
    is_high = is_critical_high or any(word in text for word in ("_high", "high_", "above", "over"))
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
    if kind == "linear_power_baseline_loss":
        return "baseline_degradation"
    if kind in {"transition", "bgp_transition"}:
        target = str(condition.get("to_group") or "").casefold()
        if target in {"down", "up"}:
            return target
    if kind == "entity_status_down":
        return "down"
    if kind == "field_change":
        return "change"
    operator = str(condition.get("operator") or "").casefold()
    if operator in {"lt", "lte", "<", "<="} or "lt" in condition or "lte" in condition:
        return "low_recovery" if recovery else "low"
    if operator in {"gt", "gte", ">", ">="} or "gt" in condition or "gte" in condition:
        return "high_recovery" if recovery else "high"
    return "up" if recovery else None


def _fallback_event_type(observation_type: str, role: str) -> str:
    prefix = _token(observation_type) or "observation"
    return f"{prefix}_{role}"


def _requires_current_state_only(
    requirements: dict[str, Any] | None,
    *,
    observation_type: str,
) -> bool:
    policy = dict(requirements or {})
    if policy.get("requires_current_state_rule") is not True:
        return False
    events = dict(policy.get("required_event_types_by_observation") or {})
    # Each Value has one use: sibling Values that report changes do not turn a
    # health-only Value into a change rule.
    if policy.get("requires_event_rule") is True and (not events or observation_type in events):
        return False
    scoped_types = {
        str(item).strip()
        for item in policy.get("current_state_observation_types") or []
        if str(item).strip()
    }
    return not scoped_types or observation_type in scoped_types


def _compile_current_state_roles(
    roles: list[RuleRole],
) -> tuple[list[RuleRole], list[str]]:
    """Remove history-only roles and make state faults executable on a snapshot."""

    compiled: list[RuleRole] = []
    errors: list[str] = []
    for role in roles:
        if role.event_class == "recovery":
            continue
        condition = dict(role.condition)
        kind = str(condition.get("kind") or "")
        if kind in {"transition", "bgp_transition"}:
            if role.name != "down":
                errors.append(
                    f"current-state rule cannot compile transition role: {role.name}"
                )
                continue
            explicit_values = list(condition.get("to_values") or [])
            condition = {
                key: value
                for key, value in condition.items()
                if key in {
                    "entity_types",
                    "fields",
                    "observation_types",
                    "dimensions",
                    "applicability",
                }
            }
            condition["kind"] = "entity_status_down"
            if explicit_values:
                condition["values"] = explicit_values
        elif kind in {"field_change", "raw_config_hash"}:
            errors.append(
                f"current-state rule cannot compile history-only condition: {kind}"
            )
            continue
        compiled.append(
            RuleRole(
                name=role.name,
                event_class=role.event_class,
                condition=condition,
                default_severity=role.default_severity,
            )
        )
    if not compiled and not errors:
        errors.append("current-state profile produced no executable fault role")
    return compiled, errors


def _govern_event_type_for_role(
    *,
    proposed_event_type: str,
    observation_type: str,
    role: RuleRole,
    constraints: dict[str, Any] | None,
    reserved_tokens: dict[str, str],
) -> tuple[str, dict[str, Any] | None]:
    """Keep machine event identity consistent with the compiled trigger role."""

    policy = dict(constraints or {})
    configured = str(dict(policy.get("role_event_types") or {}).get(role.name) or "").strip()
    proposed = str(proposed_event_type or "").strip()
    fallback = _fallback_event_type(observation_type, role.name)
    effective = configured or proposed or fallback
    reason: str | None = None
    inferred_role: str | None = None

    if configured:
        if proposed and proposed != configured:
            reason = "explicit_role_event_type_mapping"
    elif proposed:
        inferred_role = infer_event_rule_role(
            {
                "event_type": proposed,
                "event_class": role.event_class,
            }
        )
        if inferred_role is not None and inferred_role != role.name:
            effective = fallback
            reason = "event_type_direction_conflicts_with_trigger_role"

    token = _token(effective)
    reserved_by = reserved_tokens.get(token)
    if reserved_by and reserved_by != role.name:
        effective = fallback
        token = _token(effective)
        reason = "event_type_reused_by_another_trigger_role"
    reserved_tokens[token] = role.name

    if not reason or effective == proposed:
        return effective, None
    return effective, {
        "kind": "event_type_governance",
        "trigger_role": role.name,
        "proposed": proposed,
        "effective": effective,
        "inferred_role": inferred_role,
        "reason": reason,
        "source": "deterministic_control_plane",
    }


def compile_event_rule_intents(
    *,
    candidate_rules: list[dict[str, Any]],
    contract: dict[str, Any],
    change_policy: dict[str, Any],
    event_constraints: dict[str, Any] | None = None,
    rule_asset_requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile permissive LLM intent objects into executable, policy-bound rules."""

    roles, errors = expected_rule_roles(
        contract=contract,
        change_policy=change_policy,
        event_constraints=event_constraints,
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
    current_state_only = _requires_current_state_only(
        rule_asset_requirements,
        observation_type=observation_type,
    )
    if current_state_only:
        roles, current_state_errors = _compile_current_state_roles(roles)
        if current_state_errors:
            return {
                "success": False,
                "rules": [dict(rule) for rule in candidate_rules],
                "errors": current_state_errors,
                "normalizations": [],
                "compiled": False,
            }
    classified: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, rule in enumerate(candidate_rules):
        if not isinstance(rule, dict):
            continue
        inferred = infer_event_rule_role(rule)
        if inferred:
            classified.setdefault(inferred, []).append((index, rule))

    used: set[int] = set()
    reserved_event_type_tokens: dict[str, str] = {}
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
        event_type, event_type_repair = _govern_event_type_for_role(
            proposed_event_type=str(source.get("event_type") or ""),
            observation_type=observation_type,
            role=role,
            constraints=event_constraints,
            reserved_tokens=reserved_event_type_tokens,
        )
        rule_name = str(source.get("name") or source.get("rule_id") or event_type)
        proposed_severity = (
            "info"
            if role.event_class == "recovery"
            else str(source.get("severity") or role.default_severity)
        )
        effective_severity, severity_repair = govern_event_rule_severity(
            event_type=event_type,
            proposed_severity=proposed_severity,
            constraints=event_constraints,
        )
        compiled = {
            "name": rule_name,
            "version": int(source.get("version") or 1),
            "event_type": event_type,
            "trigger_role": role.name,
            "severity": effective_severity,
            "description": str(
                source.get("description")
                or f"Generated {observation_type} {role.name} event candidate."
            ),
            "condition": dict(role.condition),
            "priority": int(source.get("priority") or role.priority),
            "exclusive": bool(source.get("exclusive", role.exclusive)),
            "event_class": role.event_class,
            "evaluation_scope": (
                "current_state"
                if current_state_only
                else role.evaluation_scope
                if role.evaluation_scope
                else "change"
                if role.event_class in {"recovery", "change"}
                else str(source.get("evaluation_scope") or "both")
            ),
            "recovery": {},
            "correlation_keys": list(
                source.get("correlation_keys")
                or source.get("correlation_key")
                or contract.get("primary_keys")
                or ["device_id", "entity_type", "entity_key", "field"]
            ),
        }
        compiled_by_role[role.name] = compiled
        if event_type_repair:
            normalizations.append(
                {
                    **event_type_repair,
                    "source_rule_index": selected_index,
                }
            )
        if severity_repair:
            normalizations.append(
                {
                    **severity_repair,
                    "trigger_role": role.name,
                    "source_rule_index": selected_index,
                }
            )
        normalizations.append(
            {
                "kind": "event_intent_compiled",
                "trigger_role": role.name,
                "source_rule_index": selected_index,
                "event_type": event_type,
            }
        )
        if current_state_only:
            normalizations.append(
                {
                    "kind": "current_state_profile_compiled",
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
            "inferred_role": infer_event_rule_role(rule),
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
