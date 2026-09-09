from copy import deepcopy

import pytest

from src.core.check_items.current_state import _attach_baselines
from src.core.check_items.numeric_health import evaluate_numeric_health
from src.core.workflows.patrol_batch_service import evaluate_current_state

POLICY = {"window": "5m", "max_relative_deviation": 0.1, "device_thresholds": {"high": ["alarm"]}}


def observation(value=20, baseline=20):
    return {
        "id": "now",
        "snapshot_id": "now-snapshot",
        "device_id": "sw",
        "entity_type": "cpu_scope",
        "entity_key": '["1"]',
        "field": "cpu_usage_percent",
        "observation_type": "device.cpu.utilization",
        "value": value,
        "unit": "percent",
        "dimensions": {"window": "5m"},
        "context": {
            "alarm": 80,
            "baseline_value": baseline,
            "baseline_observation_id": "base",
            "baseline_snapshot_id": "base-snapshot",
        },
        "metadata": {
            "check_item_id": "system.cpu_usage",
            "check_item_version_id": "approved-v2",
            "semantics": {"health_policy": deepcopy(POLICY)},
        },
    }


@pytest.mark.parametrize(
    "value,baseline,status",
    [
        (22, 20, "healthy"),
        (18, 20, "healthy"),
        (22.1, 20, "abnormal"),
        (17.9, 20, "abnormal"),
        (0, 0, "healthy"),
        (1, 0, "unknown"),
    ],
)
def test_absolute_relative_deviation_and_zero(value, baseline, status):
    assert evaluate_numeric_health(observation(value, baseline), POLICY)["status"] == status


def test_equal_threshold_is_allowed_and_missing_data_not_healthy():
    obs = observation(80, 80)
    assert evaluate_numeric_health(obs, POLICY)["status"] == "healthy"
    obs["context"].pop("alarm")
    assert evaluate_numeric_health(obs, POLICY)["status"] == "unknown"
    obs = observation()
    obs["dimensions"] = {}
    assert evaluate_numeric_health(obs, POLICY)["status"] == "unknown"
    obs = observation()
    obs["context"]["baseline_unit"] = "bytes"
    assert evaluate_numeric_health(obs, POLICY)["status"] == "unknown"


def test_optical_uses_dbm_absolute_deviation_and_both_limits():
    policy = {
        "max_relative_deviation": 0.1,
        "device_thresholds": {"high": ["high"], "low": ["low"]},
    }
    obs = observation(-11, -10)
    obs["unit"] = "dbm"
    obs["context"].update(high=0, low=-20)
    assert evaluate_numeric_health(obs, policy)["status"] == "healthy"
    obs["value"] = -11.01
    assert evaluate_numeric_health(obs, policy)["violations"] == [
        "baseline_relative_deviation_exceeded"
    ]
    obs["value"] = -10
    obs["context"]["low"] = -9
    assert evaluate_numeric_health(obs, policy)["violations"] == ["device_low_threshold_exceeded"]


def test_health_policy_executes_without_event_rule_and_is_auditable():
    result = evaluate_current_state([observation(25)], [], rule_source="test")
    assert result["finding_count"] == 1
    finding = result["findings"][0]
    assert finding["assessment_source"] == "check_item_health_policy"
    assert finding["health_assessment"]["relative_deviation"] == 0.25
    assert finding["assessment_basis"]["baseline_binding"]["baseline_observation_id"] == "base"
    obs = observation()
    obs["context"].pop("alarm")
    result = evaluate_current_state([obs], [], rule_source="test")
    assert result["finding_count"] == 0
    assert result["assessment_policy_missing_observation_count"] == 1


def test_baseline_matching_never_crosses_windows_or_interfaces():
    current = observation()
    current["context"] = {}
    base = {**observation(), "id": "base", "dimensions": {"window": "1m"}}
    attached = _attach_baselines([current], [base])[0]
    assert attached["context"]["baseline_status"] == "missing"


def test_interface_expected_state_requirement_survives_legacy_report_path():
    obs = observation()
    obs.update(
        entity_type="interface",
        entity_key='["GE1/0/1"]',
        field="oper_status",
        observation_type="interface.oper_status",
        value="down",
        dimensions={},
    )
    obs["metadata"]["semantics"] = {"event_constraints": {"allow_initial_transition_fault": False}}
    rules = [
        {
            "rule_id": "InterfaceDown",
            "evaluation_scope": "both",
            "event_type": "InterfaceDown",
            "condition": {
                "kind": "transition",
                "fields": ["oper_status"],
                "to_group": "down",
                "from_group": "up",
                "allow_initial_target": False,
            },
        }
    ]
    result = evaluate_current_state([obs], rules, rule_source="legacy")
    assert result["finding_count"] == 0
    assert result["expected_state_unknown_observation_count"] == 1


def test_revision_is_idempotent_and_preserves_manual_command_associations():
    from src.core.check_items.catalog import load_catalog, validate_check_item_definition
    from src.core.check_items.health_policy_revision import TARGETS, revise_health_catalog

    original = load_catalog()
    revised = revise_health_catalog(original)
    again = revise_health_catalog(revised)
    assert revised.check_items == again.check_items
    assert revised.implementations == again.implementations
    for old, new in zip(original.implementations, revised.implementations, strict=True):
        assert old.commands == new.commands
        assert old.check_item_ids == new.check_item_ids
    for item in revised.check_items:
        if item.id in TARGETS:
            validate_check_item_definition(item)
