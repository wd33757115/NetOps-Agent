"""Generate draft EventRule DSL from contracts and ChangePolicy proposals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from src.core.patrol.change_policy import (
    ChangePolicyProposal,
    normalize_dimension_selector,
    policy_from_dict,
)
from src.core.patrol.event_rules import EventRuleDefinition
from src.core.patrol.rule_validation import validate_event_rule_definition
from src.core.state.observation_applicability import attach_governed_applicability


def _fields(contract: dict[str, Any]) -> list[str]:
    return [
        str(field)
        for field in [
            *(contract.get("required_fields") or []),
            *(contract.get("optional_fields") or []),
        ]
    ]


def _state_fields(contract: dict[str, Any]) -> list[str]:
    semantics = dict(contract.get("state_semantics") or {})
    result = [
        field
        for field, metadata in semantics.items()
        if isinstance(metadata, dict)
        and metadata.get("semantic_type") in {"state", "enum"}
    ]
    if result:
        return result
    return [
        field
        for field in _fields(contract)
        if field.lower() in {"status", "state", "protocol", "oper_status", "line_protocol"}
    ]


def _gauge_fields(contract: dict[str, Any]) -> list[str]:
    semantics = dict(contract.get("state_semantics") or {})
    result = [
        field
        for field, metadata in semantics.items()
        if isinstance(metadata, dict) and metadata.get("semantic_type") == "gauge"
    ]
    if result:
        return result
    return [
        field
        for field in _fields(contract)
        if any(
            token in field.lower()
            for token in ("cpu", "memory", "usage", "rx_power", "tx_power", "dbm")
        )
    ]


def _policy_gauge_fields(contract: dict[str, Any], observation_type: str) -> list[str]:
    semantics = dict(contract.get("state_semantics") or {})
    matched = [
        field
        for field, metadata in semantics.items()
        if isinstance(metadata, dict)
        and metadata.get("semantic_type") == "gauge"
        and str(metadata.get("observation_type") or "") == observation_type
    ]
    return matched or _gauge_fields(contract)


def generate_event_rule_definitions(
    *,
    contract: dict[str, Any],
    change_policy: dict[str, Any] | ChangePolicyProposal,
    intent: str | None = None,
) -> list[EventRuleDefinition]:
    policy = (
        change_policy
        if isinstance(change_policy, ChangePolicyProposal)
        else policy_from_dict(dict(change_policy or {}))
    )
    entity_type = str(
        contract.get("canonical_entity_type")
        or contract.get("entity_type")
        or ""
    )
    observation_type = str(policy.observation_type or "")
    definitions: list[EventRuleDefinition] = []

    if policy.compare_mode == "state_transition":
        fields = _state_fields(contract) or ["state"]
        if entity_type == "bgp_neighbor" or "bgp" in observation_type:
            definitions.extend(
                [
                    EventRuleDefinition(
                        rule_id="bgp-neighbor-lost",
                        version=1,
                        event_type="BGPNeighborLost",
                        severity="critical",
                        description="Generated BGP neighbor lost rule candidate.",
                        condition={
                            "kind": "bgp_transition",
                            "fields": fields,
                            "from_established": True,
                            "to_established": False,
                            "allow_initial_target": True,
                        },
                    ),
                    EventRuleDefinition(
                        rule_id="bgp-neighbor-established",
                        version=1,
                        event_type="BGPNeighborEstablished",
                        severity="info",
                        description="Generated BGP neighbor recovery rule candidate.",
                        condition={
                            "kind": "bgp_transition",
                            "fields": fields,
                            "from_established": False,
                            "to_established": True,
                            "allow_initial_target": False,
                        },
                    ),
                ]
            )
        elif entity_type in {"fan", "environment_fan"}:
            definitions.append(
                EventRuleDefinition(
                    rule_id="fan-failed",
                    version=1,
                    event_type="FanFailed",
                    severity="major",
                    description="Generated fan failed rule candidate.",
                    condition={
                        "kind": "entity_status_down",
                        "entity_types": ["fan", "environment_fan"],
                        "fields": fields,
                    },
                )
            )
        elif entity_type in {"power", "power_supply", "environment_power"}:
            definitions.append(
                EventRuleDefinition(
                    rule_id="power-failed",
                    version=1,
                    event_type="PowerFailed",
                    severity="major",
                    description="Generated power failed rule candidate.",
                    condition={
                        "kind": "entity_status_down",
                        "entity_types": ["power", "power_supply", "environment_power"],
                        "fields": fields,
                    },
                )
            )
    elif policy.compare_mode == "threshold_and_deadband":
        fields = _gauge_fields(contract) or [observation_type or "value"]
        high_value = (policy.thresholds or {}).get("high")
        if high_value is None:
            return definitions
        high = float(high_value)
        lowered = " ".join([observation_type, *fields]).lower()
        is_cpu = observation_type == "device.cpu.utilization" or "cpu" in lowered
        is_memory = (
            observation_type == "device.memory.utilization"
            or "memory" in lowered
            or "mem" in lowered
        )
        if not is_cpu and not is_memory:
            return definitions
        definitions.append(
            EventRuleDefinition(
                rule_id="memory-high" if is_memory else "cpu-high",
                version=1,
                event_type="MemoryHigh" if is_memory else "CPUHigh",
                severity="warning",
                description="Generated threshold high rule candidate.",
                condition={
                    "kind": "threshold",
                    "threshold_role": "high",
                    "entity_types": [entity_type] if entity_type else [],
                    "fields": fields,
                    "gte": high,
                },
            )
        )
        if policy.recovery_threshold is not None:
            definitions.append(
                EventRuleDefinition(
                    rule_id="memory-recovered" if is_memory else "cpu-recovered",
                    version=1,
                    event_type="MemoryRecovered" if is_memory else "CPURecovered",
                    severity="info",
                    description="Generated threshold recovery rule candidate.",
                    condition={
                        "kind": "threshold_recovery",
                        "threshold_role": "high",
                        "entity_types": [entity_type] if entity_type else [],
                        "fields": fields,
                        "lt": float(policy.recovery_threshold),
                    },
                )
            )
    elif policy.compare_mode == "threshold_low_and_deadband":
        fields = _policy_gauge_fields(contract, observation_type) or [
            observation_type or "value"
        ]
        lowered = " ".join([observation_type, *fields, entity_type]).lower()
        if "optical" not in lowered and "rx_power" not in lowered and "tx_power" not in lowered:
            return definitions
        thresholds = dict(policy.thresholds or {})
        references = dict(policy.threshold_references or {})
        fault_roles = [
            (role, thresholds.get(role), dict(references.get(role) or {}))
            for role in ("low", "critical_low")
            if thresholds.get(role) is not None or references.get(role)
        ]
        if not fault_roles:
            return definitions
        primary_role = next(
            (item for item in fault_roles if item[0] == "low"),
            fault_roles[0],
        )
        deadband = abs(float(policy.deadband or 0))
        scoped_entity_types = [entity_type] if entity_type else ["interface_optical"]
        for field in fields:
            normalized_field = field.lower()
            direction = (
                "Rx"
                if "rx_power" in normalized_field
                else "Tx"
                if "tx_power" in normalized_field
                else None
            )
            if not direction:
                continue
            fault_event_types: list[str] = []
            for role, threshold, reference in fault_roles:
                critical = role == "critical_low"
                event_type = (
                    f"Optical{direction}PowerCritical"
                    if critical
                    else f"Optical{direction}PowerLow"
                )
                condition = {
                    "kind": "threshold_field_lte",
                    "threshold_role": role,
                    "entity_types": scoped_entity_types,
                    "fields": [field],
                    "reference_field": reference["field_name"],
                } if reference else {
                    "kind": "threshold_lte",
                    "threshold_role": role,
                    "entity_types": scoped_entity_types,
                    "fields": [field],
                    "lte": float(threshold),
                }
                definitions.append(
                    EventRuleDefinition(
                        rule_id=f"optical-{direction.lower()}-power-{role.replace('_', '-')}",
                        version=1,
                        event_type=event_type,
                        severity="critical" if critical else "warning",
                        description=(
                            f"Generated optical {direction} "
                            f"{'critical' if critical else 'low-power'} rule candidate."
                        ),
                        condition=condition,
                        priority=200 if critical else 100,
                        exclusive=critical,
                    )
                )
                fault_event_types.append(event_type)

            primary_threshold_role, primary_threshold, primary_reference = primary_role
            if primary_reference and deadband:
                recovery_condition = {
                    "kind": "threshold_field_low_recovery",
                    "threshold_role": primary_threshold_role,
                    "entity_types": scoped_entity_types,
                    "fields": [field],
                    "reference_field": primary_reference["field_name"],
                    "hysteresis": deadband,
                }
                fallback_fields = [
                    str(reference.get("field_name") or "")
                    for role, _threshold, reference in fault_roles
                    if role == "critical_low"
                    and reference
                    and reference.get("field_name")
                    != primary_reference.get("field_name")
                ]
                if fallback_fields:
                    recovery_condition["fallback_reference_fields"] = fallback_fields
            elif primary_threshold is not None:
                low = float(primary_threshold)
                recovered = (
                    float(policy.recovery_threshold)
                    if policy.recovery_threshold is not None
                    else low + deadband
                )
                recovery_condition = {
                    "kind": "threshold_low_recovery",
                    "threshold_role": primary_threshold_role,
                    "entity_types": scoped_entity_types,
                    "fields": [field],
                    "low": low,
                    "gt": recovered,
                }
            else:
                recovery_condition = None
            if recovery_condition is not None:
                definitions.append(
                    EventRuleDefinition(
                        rule_id=f"optical-{direction.lower()}-power-recovered",
                        version=1,
                        event_type=f"Optical{direction}PowerRecovered",
                        severity="info",
                        description=f"Generated optical {direction} recovery rule candidate.",
                        condition=recovery_condition,
                        event_class="recovery",
                        recovery={"resolves": fault_event_types},
                    )
                )
    elif policy.compare_mode == "hash_diff":
        definitions.append(
            EventRuleDefinition(
                rule_id="config-changed",
                version=1,
                event_type="ConfigChanged",
                severity="minor",
                description="Generated raw hash change rule candidate.",
                condition={
                    "kind": "raw_config_hash",
                    "command_markers": ["configuration", "running-config", "current-configuration"],
                },
            )
        )

    if observation_type:
        definitions = [
            replace(
                definition,
                condition={
                    **dict(definition.condition or {}),
                    "observation_types": [observation_type],
                },
            )
            if str((definition.condition or {}).get("kind") or "") != "raw_config_hash"
            else definition
            for definition in definitions
        ]
    dimension_selector = normalize_dimension_selector(policy.dimension_selector)
    if dimension_selector:
        dimension_keys = tuple(sorted(dimension_selector))
        dimension_suffix = hashlib.sha256(
            json.dumps(
                dimension_selector,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:8]
        definitions = [
            replace(
                definition,
                rule_id=f"{definition.rule_id}-dim-{dimension_suffix}",
                condition={
                    **dict(definition.condition or {}),
                    "dimensions": dimension_selector,
                },
                correlation_keys=tuple(
                    dict.fromkeys((*definition.correlation_keys, *dimension_keys))
                ),
            )
            for definition in definitions
        ]

    recovery_pairs = {
        "CPURecovered": ["CPUHigh"],
        "MemoryRecovered": ["MemoryHigh"],
        "BGPNeighborEstablished": ["BGPNeighborLost"],
        "OpticalRecovered": ["OpticalRxPowerLow", "OpticalTxPowerLow"],
    }
    definitions = [
        replace(
            definition,
            event_class="recovery",
            recovery={"resolves": recovery_pairs[definition.event_type]},
        )
        if definition.event_type in recovery_pairs
        else definition
        for definition in definitions
    ]
    if intent:
        definitions = [
            replace(
                definition,
                description=f"{definition.description or ''} Intent: {intent}".strip(),
            )
            for definition in definitions
        ]
    definitions = [
        replace(
            definition,
            condition=attach_governed_applicability(
                dict(definition.condition), contract
            )[0],
        )
        for definition in definitions
    ]
    return definitions


def generate_and_validate_event_rules(
    *,
    contract: dict[str, Any],
    change_policy: dict[str, Any] | ChangePolicyProposal,
    intent: str | None = None,
) -> dict[str, Any]:
    definitions = generate_event_rule_definitions(
        contract=contract,
        change_policy=change_policy,
        intent=intent,
    )
    reports = [
        {
            "rule": {
                "name": definition.rule_id,
                "version": definition.version,
                "event_type": definition.event_type,
                "severity": definition.severity,
                "description": definition.description,
                "condition": definition.condition,
                "priority": definition.priority,
                "exclusive": definition.exclusive,
                "event_class": definition.event_class,
                "recovery": definition.recovery or {},
                "correlation_keys": list(definition.correlation_keys),
            },
            "validation": validate_event_rule_definition(definition),
        }
        for definition in definitions
    ]
    return {
        "success": bool(definitions) and all(item["validation"]["success"] for item in reports),
        "generated_rule_count": len(definitions),
        "rules": [item["rule"] for item in reports],
        "reports": reports,
    }
