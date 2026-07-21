"""Unit tests for command four-state aggregation."""

from __future__ import annotations

from src.core.rule_control.command_four_state import command_four_states


def test_command_four_states_ready_and_missing() -> None:
    rows = command_four_states(
        coverage=[
            {
                "command": "display version",
                "policy_status": "eligible",
                "coverage_status": "ready",
                "template_id": "tpl-1",
            },
            {
                "command": "display fan",
                "policy_status": "eligible",
                "coverage_status": "template_missing",
            },
        ],
        runtime_asset_health={
            "analysis_ready": True,
            "event_evaluation_ready": False,
            "missing_observation_contracts": [],
            "missing_change_policies": [],
            "missing_event_rules": ["cpu.usage"],
        },
    )
    assert rows[0]["parser_status"] == "ready"
    assert rows[0]["contract_status"] == "ready"
    assert rows[1]["parser_status"] == "template_missing"
    assert rows[1]["next_action"] == "补齐 Parser 模板"
