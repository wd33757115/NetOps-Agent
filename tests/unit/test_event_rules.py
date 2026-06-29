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
        {"field": "cpu_5s", "new": "18%"},
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
