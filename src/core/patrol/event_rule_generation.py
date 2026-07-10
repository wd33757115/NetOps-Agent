"""Generate draft EventRule DSL from contracts and ChangePolicy proposals."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from src.core.patrol.change_policy import ChangePolicyProposal, policy_from_dict
from src.core.patrol.event_rules import EventRuleDefinition
from src.core.patrol.rule_validation import validate_event_rule_definition


INTERFACE_ENTITY_TYPES = {
    "interface",
    "interface_l2",
    "interface_l3",
    "physical_interface",
}


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
    entity_type = str(contract.get("entity_type") or "")
    observation_type = str(policy.observation_type or "")
    definitions: list[EventRuleDefinition] = []

    if policy.compare_mode == "state_transition":
        fields = _state_fields(contract) or ["state"]
        if entity_type in INTERFACE_ENTITY_TYPES or "interface" in observation_type:
            definitions.extend(
                [
                    EventRuleDefinition(
                        rule_id="interface-down",
                        version=1,
                        event_type="InterfaceDown",
                        severity="major",
                        description="Generated interface down rule candidate.",
                        condition={
                            "kind": "transition",
                            "entity_types": sorted(INTERFACE_ENTITY_TYPES),
                            "fields": fields,
                            "from_group": "up",
                            "to_group": "down",
                            "allow_initial_target": True,
                        },
                    ),
                    EventRuleDefinition(
                        rule_id="interface-up",
                        version=1,
                        event_type="InterfaceUp",
                        severity="info",
                        description="Generated interface recovery rule candidate.",
                        condition={
                            "kind": "transition",
                            "entity_types": sorted(INTERFACE_ENTITY_TYPES),
                            "fields": fields,
                            "from_group": "down",
                            "to_group": "up",
                            "allow_initial_target": False,
                        },
                    ),
                ]
            )
        elif entity_type == "bgp_neighbor" or "bgp" in observation_type:
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
        high = float((policy.thresholds or {}).get("high") or 90)
        lowered = " ".join([observation_type, *fields]).lower()
        is_cpu = observation_type == "device.cpu.utilization" or "cpu" in lowered
        is_memory = observation_type == "device.memory.utilization" or "memory" in lowered or "mem" in lowered
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
                    "entity_types": [entity_type] if entity_type else [],
                    "fields": fields,
                    "gte": high,
                },
            )
        )
        definitions.append(
            EventRuleDefinition(
                rule_id="memory-recovered" if is_memory else "cpu-recovered",
                version=1,
                event_type="MemoryRecovered" if is_memory else "CPURecovered",
                severity="info",
                description="Generated threshold recovery rule candidate.",
                condition={
                    "kind": "threshold_recovery",
                    "entity_types": [entity_type] if entity_type else [],
                    "fields": fields,
                    "lt": float(policy.recovery_threshold or high),
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
        low = float((policy.thresholds or {}).get("low") or -17)
        recovered = float(policy.recovery_threshold or (low + float(policy.deadband or 2)))
        scoped_entity_types = [entity_type] if entity_type else ["interface_optical"]
        for field in fields:
            normalized_field = field.lower()
            if "rx_power" in normalized_field:
                definitions.append(
                    EventRuleDefinition(
                        rule_id="optical-rx-power-low",
                        version=1,
                        event_type="OpticalRxPowerLow",
                        severity="warning",
                        description="Generated optical Rx low-power rule candidate.",
                        condition={
                            "kind": "threshold_lte",
                            "entity_types": scoped_entity_types,
                            "fields": [field],
                            "lte": low,
                        },
                    )
                )
                definitions.append(
                    EventRuleDefinition(
                        rule_id="optical-rx-power-recovered",
                        version=1,
                        event_type="OpticalRecovered",
                        severity="info",
                        description="Generated optical Rx recovery rule candidate.",
                        condition={
                            "kind": "threshold_low_recovery",
                            "entity_types": scoped_entity_types,
                            "fields": [field],
                            "low": low,
                            "gt": recovered,
                        },
                    )
                )
            if "tx_power" in normalized_field:
                definitions.append(
                    EventRuleDefinition(
                        rule_id="optical-tx-power-low",
                        version=1,
                        event_type="OpticalTxPowerLow",
                        severity="warning",
                        description="Generated optical Tx low-power rule candidate.",
                        condition={
                            "kind": "threshold_lte",
                            "entity_types": scoped_entity_types,
                            "fields": [field],
                            "lte": low,
                        },
                    )
                )
                definitions.append(
                    EventRuleDefinition(
                        rule_id="optical-tx-power-recovered",
                        version=1,
                        event_type="OpticalRecovered",
                        severity="info",
                        description="Generated optical Tx recovery rule candidate.",
                        condition={
                            "kind": "threshold_low_recovery",
                            "entity_types": scoped_entity_types,
                            "fields": [field],
                            "low": low,
                            "gt": recovered,
                        },
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

    recovery_pairs = {
        "CPURecovered": ["CPUHigh"],
        "MemoryRecovered": ["MemoryHigh"],
        "InterfaceUp": ["InterfaceDown"],
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
