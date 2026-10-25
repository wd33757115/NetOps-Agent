"""Shared rule-asset composition contract derived from CheckItem requirements."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from src.apps.rule_governance.backend.rule_control.replay_gate import (
    evaluate_historical_replay_gate,
)


def normalize_rule_asset_requirements(
    raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize v1 requirements while preserving legacy full-bundle callers."""

    if not isinstance(raw, Mapping):
        raw = {
            "schema_version": "rule-asset-requirements/legacy-v1",
            "requires_observation_contract": False,
            "requires_change_policy": True,
            "requires_current_state_rule": False,
            "requires_event_rule": True,
            "requires_event_rule_assets": True,
            "requires_historical_replay": True,
        }
    requires_current = raw.get("requires_current_state_rule") is True
    requires_event = raw.get("requires_event_rule") is True
    current_state_types = sorted(
        {
            str(item).strip()
            for item in raw.get("current_state_observation_types") or []
            if str(item).strip()
        }
    )
    change_policy_types = sorted(
        {
            str(item).strip()
            for item in raw.get("change_policy_observation_types") or []
            if str(item).strip()
        }
    )
    required_event_types: dict[str, list[str]] = {}
    for observation_type, event_types in dict(
        raw.get("required_event_types_by_observation") or {}
    ).items():
        normalized_observation_type = str(observation_type).strip()
        if not normalized_observation_type:
            continue
        required_event_types[normalized_observation_type] = sorted(
            {
                str(item).strip()
                for item in event_types or []
                if str(item).strip()
            }
        )
    return {
        **dict(raw),
        "requires_observation_contract": False,
        "requires_change_policy": raw.get("requires_change_policy") is True,
        "requires_current_state_rule": requires_current,
        "requires_event_rule": requires_event,
        "requires_event_rule_assets": (
            raw.get("requires_event_rule_assets") is True
            or requires_current
            or requires_event
        ),
        "requires_historical_replay": (
            raw.get("requires_historical_replay") is True
        ),
        "current_state_observation_types": current_state_types,
        "change_policy_observation_types": change_policy_types,
        "required_event_types_by_observation": required_event_types,
    }


def _role_observation_types(role: Mapping[str, Any]) -> set[str]:
    direct = role.get("observation_types")
    if not isinstance(direct, list):
        direct = dict(role.get("condition") or {}).get("observation_types")
    return {
        str(item).strip()
        for item in direct or []
        if str(item).strip()
    }


def event_rule_role_errors(
    *,
    requirements: Mapping[str, Any],
    event_rule_roles: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Validate logical CurrentStateRule/EventRule coverage."""

    normalized = normalize_rule_asset_requirements(requirements)
    roles = [dict(item) for item in event_rule_roles]
    errors: list[str] = []

    current_allowed_scopes = (
        {"current_state", "both"}
        if normalized["requires_event_rule"]
        else {"current_state"}
    )
    current_roles = [
        item
        for item in roles
        if str(item.get("event_class") or "fault").casefold() != "recovery"
        and str(item.get("evaluation_scope") or "both").casefold()
        in current_allowed_scopes
    ]
    if normalized["requires_current_state_rule"] and not current_roles:
        errors.append("bundle requires a current-state EventRule role")

    required_current_types = set(
        normalized["current_state_observation_types"]
    )
    if required_current_types:
        covered_current_types = {
            observation_type
            for role in current_roles
            for observation_type in _role_observation_types(role)
        }
        for observation_type in sorted(
            required_current_types - covered_current_types
        ):
            errors.append(
                "bundle requires current-state rule coverage for "
                f"{observation_type}"
            )

    change_roles = [
        item
        for item in roles
        if str(item.get("evaluation_scope") or "both").casefold()
        in {"change", "both"}
    ]
    if normalized["requires_event_rule"] and not change_roles:
        errors.append("bundle requires a change/event EventRule role")
    if not normalized["requires_event_rule"] and change_roles:
        errors.append(
            "current-state-only bundle cannot contain change-capable EventRule roles"
        )

    event_types_by_observation: dict[str, set[str]] = defaultdict(set)
    for role in roles:
        event_type = str(role.get("event_type") or "").strip()
        if not event_type:
            continue
        for observation_type in _role_observation_types(role):
            event_types_by_observation[observation_type].add(event_type)
    for observation_type, required_event_types in sorted(
        normalized["required_event_types_by_observation"].items()
    ):
        missing = set(required_event_types) - event_types_by_observation.get(
            observation_type, set()
        )
        if missing:
            errors.append(
                f"bundle is missing required event types for {observation_type}: "
                + ", ".join(sorted(missing))
            )
    return errors


def rule_asset_composition_errors(
    *,
    counts: Mapping[str, int],
    requirements: Mapping[str, Any],
    change_policy_roles: Iterable[Mapping[str, Any]] = (),
    event_rule_roles: Iterable[Mapping[str, Any]] = (),
) -> list[str]:
    """Validate physical child assets against logical check-item roles."""

    normalized = normalize_rule_asset_requirements(requirements)
    errors: list[str] = []
    if int(counts.get("OBSERVATION_CONTRACT") or 0):
        errors.append("旧数据契约已退役，请按检查项语义重新生成规则")
    if normalized["requires_change_policy"] and (
        int(counts.get("CHANGE_POLICY") or 0) < 1
    ):
        errors.append("bundle requires one or more ChangePolicies")
    if (
        not normalized["requires_change_policy"]
        and int(counts.get("CHANGE_POLICY") or 0) > 0
    ):
        errors.append("bundle contains ChangePolicies that are not required")
    if normalized["requires_event_rule_assets"] and (
        int(counts.get("EVENT_RULE") or 0) < 1
    ):
        errors.append("bundle requires one or more EventRuleVersion assets")
    if (
        not normalized["requires_event_rule_assets"]
        and int(counts.get("EVENT_RULE") or 0) > 0
    ):
        errors.append("bundle contains EventRules that are not required")

    required_policy_types = set(
        normalized["change_policy_observation_types"]
    )
    if required_policy_types:
        covered_policy_types = {
            str(item.get("observation_type") or "").strip()
            for item in change_policy_roles
            if str(item.get("observation_type") or "").strip()
        }
        for observation_type in sorted(
            required_policy_types - covered_policy_types
        ):
            errors.append(
                f"bundle requires ChangePolicy coverage for {observation_type}"
            )

    errors.extend(
        event_rule_role_errors(
            requirements=normalized,
            event_rule_roles=event_rule_roles,
        )
    )
    return errors


def evaluate_requirement_replay_gate(
    *,
    requirements: Mapping[str, Any],
    replay: Mapping[str, Any] | None,
    context_gate: Mapping[str, Any] | None,
) -> dict[str, Any]:
    normalized = normalize_rule_asset_requirements(requirements)
    if not normalized["requires_historical_replay"]:
        return {
            "success": True,
            "errors": [],
            "skipped": True,
            "reason": (
                "asset_requirement_profile_does_not_require_historical_replay"
            ),
        }
    replay_data = dict(replay) if isinstance(replay, Mapping) else {}
    active_equivalence = dict(replay_data.get("active_asset_equivalence") or {})
    if (
        replay_data.get("success") is True
        and int(replay_data.get("collision_count") or 0) == 0
        and active_equivalence.get("success") is True
        and int(active_equivalence.get("change_policy_count") or 0) > 0
        and int(active_equivalence.get("event_rule_count") or 0) > 0
    ):
        return {
            "success": True,
            "errors": [],
            "skipped": True,
            "reason": "all_candidate_rules_are_active_equivalents",
            "mode": "active_asset_equivalence",
            "active_equivalence": active_equivalence,
        }
    return evaluate_historical_replay_gate(
        replay_data or None,
        dict(context_gate) if isinstance(context_gate, Mapping) else None,
    )


__all__ = [
    "event_rule_role_errors",
    "evaluate_requirement_replay_gate",
    "normalize_rule_asset_requirements",
    "rule_asset_composition_errors",
]
