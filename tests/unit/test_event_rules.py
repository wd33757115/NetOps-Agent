from __future__ import annotations

from src.core.patrol.event_rules import EventRule
from src.core.patrol.event_rules import condition_matches
from src.core.patrol.event_rules import load_event_rules


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


def test_threshold_condition_matches_memory_high():
    assert condition_matches(
        {
            "kind": "threshold",
            "fields": ["memory_usage", "used_percent"],
            "gte": 90,
        },
        {"field": "memory_usage", "new": "93%"},
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


def test_load_event_rules_merges_new_builtin_defaults(monkeypatch):
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
    assert by_id["memory-high"].event_type == "MemoryHigh"
