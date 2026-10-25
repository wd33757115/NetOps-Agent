"""Evidence-bound event requirements derived from an approved ChangePolicy."""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping


THRESHOLD_FAULT_ROLES = (
    "low",
    "critical_low",
    "high",
    "critical_high",
)
THRESHOLD_RECOVERY_ROLES = {
    "low": "low_recovery",
    "critical_low": "low_recovery",
    "high": "high_recovery",
    "critical_high": "high_recovery",
}


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(str(value).strip()))
    except (TypeError, ValueError):
        return False


def threshold_roles_from_policy(policy: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return threshold roles that the policy can resolve at runtime."""

    policy = policy or {}
    thresholds = dict(policy.get("thresholds") or {})
    references = dict(policy.get("threshold_references") or {})
    return tuple(
        role
        for role in THRESHOLD_FAULT_ROLES
        if _is_number(thresholds.get(role))
        or (
            isinstance(references.get(role), Mapping)
            and bool(
                str(
                    references[role].get("field_name")
                    or references[role].get("context_key")
                    or ""
                ).strip()
            )
        )
    )


def scope_event_constraints_to_policy(
    constraints: Mapping[str, Any] | None,
    policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve alternative threshold event roles against proven policy evidence.

    A check item may support both warning and critical thresholds while a device
    exposes only one of them.  When ``evidence_bound_roles`` is enabled, only
    roles backed by the approved ChangePolicy become required runtime assets.
    """

    original = copy.deepcopy(dict(constraints or {}))
    if original.get("evidence_bound_roles") is not True:
        return {
            "success": True,
            "constraints": original,
            "active_roles": [],
            "active_event_types": list(
                dict.fromkeys(original.get("required_event_types") or [])
            ),
            "errors": [],
            "normalizations": [],
        }

    role_event_types = {
        str(role).strip(): str(event_type).strip()
        for role, event_type in dict(
            original.get("role_event_types") or {}
        ).items()
        if str(role).strip() and str(event_type).strip()
    }
    fault_roles = list(threshold_roles_from_policy(policy))
    errors: list[str] = []
    if not fault_roles:
        errors.append(
            "evidence-bound event constraints require at least one proven "
            "ChangePolicy threshold role"
        )

    missing_fault_mappings = [
        role for role in fault_roles if role not in role_event_types
    ]
    if missing_fault_mappings:
        errors.append(
            "event constraints do not map active threshold roles: "
            + ", ".join(missing_fault_mappings)
        )

    recovery_roles: list[str] = []
    for role in fault_roles:
        recovery_role = THRESHOLD_RECOVERY_ROLES[role]
        if recovery_role in role_event_types and recovery_role not in recovery_roles:
            recovery_roles.append(recovery_role)
    active_roles = [
        role
        for role in (*fault_roles, *recovery_roles)
        if role in role_event_types
    ]
    active_event_types = list(
        dict.fromkeys(role_event_types[role] for role in active_roles)
    )
    active_event_type_set = set(active_event_types)

    scoped = copy.deepcopy(original)
    scoped["role_event_types"] = {
        role: role_event_types[role] for role in active_roles
    }
    scoped["required_event_types"] = active_event_types
    for key in (
        "required_event_classes",
        "required_severities",
        "required_condition_kinds",
        "required_condition_values",
    ):
        values = scoped.get(key)
        if isinstance(values, Mapping):
            scoped[key] = {
                str(event_type): copy.deepcopy(value)
                for event_type, value in values.items()
                if str(event_type) in active_event_type_set
            }
    for key in (
        "required_exclusive_event_types",
    ):
        values = scoped.get(key)
        if isinstance(values, (list, tuple, set, frozenset)):
            scoped[key] = [
                str(value)
                for value in values
                if str(value) in active_event_type_set
            ]
    scoped["required_recovery_pairs"] = [
        copy.deepcopy(item)
        for item in scoped.get("required_recovery_pairs") or []
        if isinstance(item, Mapping)
        and str(item.get("fault") or "") in active_event_type_set
        and str(item.get("recovery") or "") in active_event_type_set
    ]
    scoped["priority_order"] = [
        copy.deepcopy(item)
        for item in scoped.get("priority_order") or []
        if isinstance(item, Mapping)
        and str(item.get("higher") or "") in active_event_type_set
        and str(item.get("lower") or "") in active_event_type_set
    ]
    if active_event_types:
        scoped["max_rule_count"] = min(
            int(scoped.get("max_rule_count") or len(active_event_types)),
            len(active_event_types),
        )
    scoped["effective_roles"] = active_roles

    normalization = {
        "kind": "evidence_bound_event_roles",
        "source": "approved_change_policy",
        "available_threshold_roles": fault_roles,
        "effective_roles": active_roles,
        "effective_event_types": active_event_types,
    }
    return {
        "success": not errors,
        "constraints": scoped,
        "active_roles": active_roles,
        "active_event_types": active_event_types,
        "errors": errors,
        "normalizations": [normalization],
    }
