from __future__ import annotations

from src.core.patrol.event_rules import (
    EventRule,
    EventRuleDefinition,
    condition_matches,
    default_event_rule_definitions,
    event_rules_from_snapshot,
    load_event_rules,
)
from src.core.patrol.rule_validation import (
    builtin_event_rule_validation_cases,
    sanitize_change,
    validate_builtin_event_rules,
    validate_event_rule_definition,
    validate_event_rule_pack,
)


def test_event_rules_can_be_rebuilt_from_immutable_snapshot():
    rules = event_rules_from_snapshot(
        [
            {
                "rule_name": "optical-rx-low-v7",
                "version": 7,
                "event_type": "OpticalRxPowerLow",
                "severity": "major",
                "condition": {
                    "kind": "threshold_lte",
                    "fields": ["rx_power_dbm"],
                    "lte": -17,
                },
                "priority": 200,
                "correlation_keys": ["device_id", "entity_key", "field"],
            }
        ]
    )

    assert rules[0].rule_id == "optical-rx-low-v7"
    assert rules[0].version == 7
    assert rules[0].match({"field": "rx_power_dbm", "new": -18.5}) is True


def test_event_rule_governance_injects_device_and_entity_identity():
    rule = EventRule(
        rule_id="weak-llm-rule",
        version=1,
        event_type="OpticalRxPowerLow",
        severity="warning",
        predicate=lambda _change: True,
        correlation_keys=("interface_name",),
    )

    assert rule.correlation_keys == (
        "device_id",
        "entity_type",
        "entity_key",
        "interface_name",
    )


def test_threshold_condition_matches_cpu_high():
    assert condition_matches(
        {
            "kind": "threshold",
            "fields": ["cpu_5s"],
            "gte": 90,
        },
        {"field": "cpu_5s", "new": "95%"},
    )
    assert not condition_matches(
        {
            "kind": "threshold",
            "fields": ["cpu_5s"],
            "gte": 90,
        },
        {"field": "cpu_5s", "old": "15%", "new": "18%"},
    )
    assert not condition_matches(
        {
            "kind": "threshold",
            "fields": ["cpu_5s"],
            "gte": 90,
        },
        {"field": "cpu_5s", "old": "91%", "new": "92%"},
    )


def test_dynamic_reference_threshold_and_recovery_conditions():
    base_change = {
        "entity_type": "generic_sensor",
        "field": "measurement",
        "dimensions": {"_observation_context": {"low_reference": -10.0}},
        "change_type": "modified",
    }
    low_condition = {
        "kind": "threshold_field_lte",
        "entity_types": ["generic_sensor"],
        "fields": ["measurement"],
        "reference_field": "low_reference",
    }
    recovery_condition = {
        "kind": "threshold_field_low_recovery",
        "entity_types": ["generic_sensor"],
        "fields": ["measurement"],
        "reference_field": "low_reference",
        "hysteresis": 2.0,
    }

    assert condition_matches(low_condition, {**base_change, "old": -9, "new": -10})
    assert not condition_matches(low_condition, {**base_change, "old": -11, "new": -12})
    assert condition_matches(
        recovery_condition,
        {**base_change, "old": -10, "new": -8},
    )
    assert not condition_matches(
        recovery_condition,
        {**base_change, "old": -10, "new": -9},
    )


def test_threshold_condition_matches_memory_high():
    assert condition_matches(
        {
            "kind": "threshold",
            "fields": ["memory_usage", "used_percent"],
            "gte": 90,
        },
        {"field": "memory_usage", "new": "93%"},
    )


def test_threshold_condition_respects_entity_scope():
    assert not condition_matches(
        {
            "kind": "threshold",
            "entity_types": ["cpu"],
            "fields": ["cpu_5s"],
            "gte": 90,
        },
        {"entity_type": "memory", "field": "cpu_5s", "new": "95%"},
    )


def test_transition_condition_matches_interface_down():
    assert condition_matches(
        {
            "kind": "transition",
            "fields": ["protocol"],
            "from_group": "up",
            "to_group": "down",
        },
        {"field": "protocol", "old": "up", "new": "down"},
    )


def test_transition_condition_matches_initial_interface_down():
    assert condition_matches(
        {
            "kind": "transition",
            "entity_types": ["interface_l3"],
            "fields": ["protocol"],
            "from_group": "up",
            "to_group": "down",
            "allow_initial_target": True,
        },
        {
            "entity_type": "interface_l3",
            "field": "protocol",
            "old": None,
            "new": "down",
            "change_type": "initial",
        },
    )


def test_transition_condition_does_not_match_initial_interface_up():
    assert not condition_matches(
        {
            "kind": "transition",
            "entity_types": ["interface_l3"],
            "fields": ["protocol"],
            "from_group": "down",
            "to_group": "up",
            "allow_initial_target": False,
        },
        {
            "entity_type": "interface_l3",
            "field": "protocol",
            "old": None,
            "new": "up",
            "change_type": "initial",
        },
    )


def test_bgp_condition_matches_initial_neighbor_lost():
    assert condition_matches(
        {
            "kind": "bgp_transition",
            "fields": ["state"],
            "from_established": True,
            "to_established": False,
            "allow_initial_target": True,
        },
        {
            "command": "display bgp peer",
            "entity_type": "bgp_neighbor",
            "field": "state",
            "old": None,
            "new": "Idle",
            "change_type": "initial",
        },
    )


def test_bgp_condition_does_not_match_initial_established():
    assert not condition_matches(
        {
            "kind": "bgp_transition",
            "fields": ["state"],
            "from_established": False,
            "to_established": True,
            "allow_initial_target": False,
        },
        {
            "command": "display bgp peer",
            "entity_type": "bgp_neighbor",
            "field": "state",
            "old": None,
            "new": "Established",
            "change_type": "initial",
        },
    )


def test_bgp_condition_does_not_match_unrelated_state_field():
    assert not condition_matches(
        {
            "kind": "bgp_transition",
            "fields": ["state"],
            "from_established": True,
            "to_established": False,
            "allow_initial_target": True,
        },
        {
            "command": "display power",
            "entity_type": "power_supply",
            "field": "state",
            "old": None,
            "new": "normal",
            "change_type": "initial",
        },
    )


def test_threshold_recovery_condition_matches_cpu_recovered():
    assert condition_matches(
        {
            "kind": "threshold_recovery",
            "fields": ["cpu_5s"],
            "lt": 90,
        },
        {"field": "cpu_5s", "old": "92%", "new": "85%"},
    )


def test_low_threshold_condition_matches_optical_power_drop_and_recovery():
    assert condition_matches(
        {
            "kind": "threshold_lte",
            "entity_types": ["interface_optical"],
            "fields": ["rx_power_dbm"],
            "lte": -17,
        },
        {
            "entity_type": "interface_optical",
            "field": "rx_power_dbm",
            "old": -14.2,
            "new": -18.1,
            "change_type": "modified",
        },
    )
    assert not condition_matches(
        {
            "kind": "threshold_lte",
            "entity_types": ["interface_optical"],
            "fields": ["rx_power_dbm"],
            "lte": -17,
        },
        {
            "entity_type": "interface_optical",
            "field": "rx_power_dbm",
            "old": -18.1,
            "new": -18.3,
            "change_type": "modified",
        },
    )
    assert condition_matches(
        {
            "kind": "threshold_low_recovery",
            "entity_types": ["interface_optical"],
            "fields": ["rx_power_dbm"],
            "low": -17,
            "gt": -15,
        },
        {
            "entity_type": "interface_optical",
            "field": "rx_power_dbm",
            "old": -18.1,
            "new": -14.8,
            "change_type": "modified",
        },
    )


def test_optical_condition_respects_governed_measurement_applicability():
    applicability = {
        "version": 1,
        "on_missing_context": "reject",
        "exclude_when": [
            {
                "code": "transceiver_absent",
                "when": {
                    "field": "transceiver_present",
                    "op": "in_ci",
                    "values": ["absent"],
                },
            },
            {
                "code": "measurement_not_applicable",
                "when": {
                    "all": [
                        {"field": "bias_ma", "op": "lte", "value": 0},
                        {
                            "field": "rx_power_dbm",
                            "op": "equals_field",
                            "other_field": "tx_power_dbm",
                        },
                    ]
                },
            },
        ],
    }
    condition = {
        "kind": "threshold_lte",
        "entity_types": ["interface_optical"],
        "fields": ["rx_power_dbm"],
        "lte": -17,
        "applicability": applicability,
    }
    change = {
        "entity_type": "interface_optical",
        "field": "rx_power_dbm",
        "old": -10,
        "new": -40,
        "change_type": "modified",
    }

    assert condition_matches(condition, change) is False
    assert condition_matches(
        condition,
        {**change, "context": {"bias_ma": 6.5, "rx_power_dbm": -40, "tx_power_dbm": -2}},
    ) is True
    assert condition_matches(
        condition,
        {**change, "context": {"transceiver_present": "absent"}},
    ) is False
    assert condition_matches(
        condition,
        {**change, "context": {"bias_ma": 0, "rx_power_dbm": -40, "tx_power_dbm": -40}},
    ) is False


def test_transition_condition_matches_cisco_interface_status_down():
    assert condition_matches(
        {
            "kind": "transition",
            "entity_types": ["interface_l2"],
            "fields": ["status"],
            "from_group": "up",
            "to_group": "down",
        },
        {
            "entity_type": "interface_l2",
            "field": "status",
            "old": "connected",
            "new": "notconnec",
        },
    )


def test_transition_condition_suppresses_initial_admin_and_absent_states():
    condition = {
        "kind": "transition",
        "entity_types": ["interface_l2"],
        "fields": ["status"],
        "from_group": "up",
        "to_group": "down",
        "allow_initial_target": True,
    }

    assert not condition_matches(
        condition,
        {
            "entity_type": "interface_l2",
            "field": "status",
            "old": None,
            "new": "disabled",
            "change_type": "initial",
        },
    )
    assert condition_matches(
        condition,
        {
            "entity_type": "interface_l2",
            "field": "status",
            "old": "connected",
            "new": "disabled",
            "change_type": "modified",
        },
    )
    assert not condition_matches(
        condition,
        {
            "entity_type": "interface_l2",
            "field": "status",
            "old": None,
            "new": "xcvrAbsen",
            "change_type": "initial",
        },
    )


def test_transition_condition_does_not_match_power_supply_status():
    assert not condition_matches(
        {
            "kind": "transition",
            "entity_types": ["interface_l2", "interface_l3"],
            "fields": ["status"],
            "from_group": "up",
            "to_group": "down",
        },
        {
            "entity_type": "power_supply",
            "field": "status",
            "old": "normal",
            "new": "failed",
        },
    )


def test_raw_config_hash_condition_matches_config_command():
    assert condition_matches(
        {
            "kind": "raw_config_hash",
            "command_markers": ["running-config"],
        },
        {
            "entity_type": "raw_command_output",
            "field": "raw_text_hash",
            "command": "show running-config",
        },
    )


def test_load_event_rules_does_not_resurrect_missing_builtins(monkeypatch):
    class FakeRepository:
        def list_active_rules(self):
            return [
                EventRule(
                    rule_id="cpu-high",
                    version=99,
                    event_type="CPUHighCustom",
                    severity="critical",
                    predicate=lambda _change: False,
                )
            ]

    monkeypatch.setattr("src.core.patrol.event_rules.EventRuleRepository", FakeRepository)

    rules = load_event_rules()
    by_id = {rule.rule_id: rule for rule in rules}

    assert by_id["cpu-high"].version == 99
    assert "memory-high" not in by_id


def test_builtin_event_rules_are_loaded_from_rule_pack():
    definitions = default_event_rule_definitions()
    by_id = {definition.rule_id: definition for definition in definitions}

    assert by_id["interface-down"].condition["kind"] == "transition"
    assert by_id["interface-down"].condition["allow_initial_target"] is True
    assert "interface_l3" in by_id["interface-down"].condition["entity_types"]
    assert by_id["power-failed"].condition["entity_types"] == [
        "power",
        "power_supply",
        "environment_power",
    ]


def test_builtin_event_rule_validation_cases_pass_and_are_generic():
    result = validate_builtin_event_rules()

    assert result["success"] is True
    assert result["failed_count"] == 0
    assert result["case_count"] == len(builtin_event_rule_validation_cases())
    assert all("10." not in str(row["change"]) for row in result["rows"])


def test_event_rule_pack_validation_generates_rule_cases():
    result = validate_event_rule_pack()

    assert result["success"] is True
    assert result["rule_count"] >= 10
    assert result["generated_case_count"] >= result["rule_count"]
    assert result["invariant_case_count"] == len(builtin_event_rule_validation_cases())


def test_candidate_rule_validation_generates_repair_feedback_surface():
    definition = default_event_rule_definitions()[0]
    result = validate_event_rule_definition(definition)

    assert result["success"] is True
    assert result["case_count"] >= 2
    assert all("device_id" not in row["change"] for row in result["rows"])


def test_malformed_threshold_rule_returns_feedback_instead_of_throwing():
    definition = EventRuleDefinition(
        rule_id="missing-threshold",
        version=1,
        event_type="OpticalRxPowerLow",
        severity="warning",
        condition={
            "kind": "threshold_lte",
            "entity_types": ["interface_optical"],
            "fields": ["rx_power_dbm"],
            "lte": None,
        },
    )

    result = validate_event_rule_definition(definition)

    assert result["success"] is False
    assert result["errors"] == ["condition.lte must be a number"]
    assert condition_matches(definition.condition, {"field": "rx_power_dbm", "new": -40}) is False


def test_rule_validation_sanitizes_private_identifiers():
    sanitized = sanitize_change(
        {
            "device_id": "private-device",
            "run_id": "run-1",
            "previous_snapshot_id": "snapshot-1",
            "entity_type": "cpu",
            "field": "cpu_5s",
            "new": 95,
        }
    )

    assert sanitized == {"entity_type": "cpu", "field": "cpu_5s", "new": 95}
