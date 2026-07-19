from __future__ import annotations

from src.core.rule_control.planning import compile_rule_targets, profile_fields
from src.core.skills.resolver import get_entry_output_mode


def test_rule_target_compiler_is_multi_field_and_flattens_list_measurements():
    contract = {
        "required_fields": ["entity"],
        "optional_fields": ["operational_state", "signal", "reference_limit", "note"],
        "state_semantics": {
            "entity": {"semantic_type": "identity"},
            "operational_state": {
                "semantic_type": "state",
                "observation_type": "network.entity.operational_state",
            },
            "signal": {
                "semantic_type": "gauge",
                "unit": "measurement-unit",
                "observation_type": "network.entity.signal",
            },
            "reference_limit": {
                "semantic_type": "gauge",
                "comparison_role": "reference",
            },
            "note": {"semantic_type": "text"},
        },
    }
    records = [
        {
            "entity": "A",
            "operational_state": "up",
            "signal": [1.5, 2.5],
            "reference_limit": -10,
            "note": "sample",
        },
        {
            "entity": "B",
            "operational_state": "down",
            "signal": [3.5, 4.5],
            "reference_limit": -10,
            "note": "sample",
        },
    ]

    result = compile_rule_targets(contract=contract, records=records)

    assert result["success"] is True
    assert [item["field_name"] for item in result["policy_targets"]] == [
        "operational_state",
        "signal",
    ]
    assert result["historical_values_by_field"]["signal"] == [1.5, 2.5, 3.5, 4.5]
    assert result["field_profiles"]["signal"]["list_valued"] is True
    assert result["field_profiles"]["signal"]["numeric_count"] == 4


def test_rule_target_compiler_reports_an_explicit_unmapped_observation():
    result = compile_rule_targets(
        contract={
            "required_fields": ["entity", "state_value"],
            "optional_fields": [],
            "state_semantics": {
                "entity": {"semantic_type": "identity"},
                "state_value": {
                    "semantic_type": "state",
                    "comparison_role": "observation",
                    "monitor": True,
                },
            },
        },
        records=[{"entity": "A", "state_value": "up"}],
    )

    assert result["success"] is False
    assert {item["code"] for item in result["blockers"]} == {
        "no_policy_targets",
        "observation_type_missing",
    }
    assert result["unmapped_comparable_fields"][0]["field_name"] == "state_value"


def test_field_profiles_are_data_driven_and_do_not_require_domain_names():
    profiles = profile_fields(
        [
            {"key": "one", "value": "1.0"},
            {"key": "two", "value": 2},
            {"key": "three", "value": None},
        ]
    )

    assert profiles["value"]["populated_record_count"] == 2
    assert profiles["value"]["numeric_count"] == 2
    assert profiles["value"]["minimum"] == 1.0
    assert profiles["value"]["maximum"] == 2.0


def test_rule_target_planner_uses_json_only_skill_contract():
    assert get_entry_output_mode("rule-target-planner") == "none"


def test_rule_target_compiler_links_structured_reference_thresholds_generically():
    contract = {
        "required_fields": ["sensor_id"],
        "optional_fields": [
            "measured_signal_dbm",
            "alarm_low_signal_dbm",
            "alarm_high_signal_dbm",
        ],
        "state_semantics": {
            "sensor_id": {"semantic_type": "identity"},
            "measured_signal_dbm": {
                "semantic_type": "gauge",
                "unit": "dbm",
                "observation_type": "generic.signal.level",
            },
            "alarm_low_signal_dbm": {"semantic_type": "gauge", "unit": "dbm"},
            "alarm_high_signal_dbm": {"semantic_type": "gauge", "unit": "dbm"},
        },
    }

    result = compile_rule_targets(
        contract=contract,
        records=[
            {
                "sensor_id": "one",
                "measured_signal_dbm": -4.0,
                "alarm_low_signal_dbm": -12.0,
                "alarm_high_signal_dbm": 1.0,
            },
            {
                "sensor_id": "two",
                "measured_signal_dbm": -5.0,
                "alarm_low_signal_dbm": -15.0,
                "alarm_high_signal_dbm": 2.0,
            },
        ],
    )

    assert result["success"] is True
    target = result["policy_targets"][0]
    assert target["threshold_references"]["critical_low"]["field_name"] == (
        "alarm_low_signal_dbm"
    )
    assert target["threshold_references"]["critical_low"]["context_key"] == (
        "threshold.critical_low"
    )
    assert target["threshold_references"]["critical_high"]["field_name"] == (
        "alarm_high_signal_dbm"
    )
    assert target["threshold_references"]["critical_high"]["context_key"] == (
        "threshold.critical_high"
    )
    semantics = result["contract"]["state_semantics"]
    assert semantics["measured_signal_dbm"]["context_fields"] == [
        "alarm_high_signal_dbm",
        "alarm_low_signal_dbm",
    ]
    assert semantics["alarm_low_signal_dbm"]["comparison_role"] == "threshold"
    assert semantics["alarm_low_signal_dbm"]["target_field"] == "measured_signal_dbm"


def test_rule_target_compiler_keeps_alarm_only_reference_critical():
    result = compile_rule_targets(
        contract={
            "required_fields": ["sensor_id"],
            "optional_fields": ["signal_dbm", "alarm_low_signal_dbm"],
            "state_semantics": {
                "sensor_id": {"semantic_type": "identity"},
                "signal_dbm": {
                    "semantic_type": "gauge",
                    "unit": "dbm",
                    "observation_type": "generic.signal.level",
                },
                "alarm_low_signal_dbm": {
                    "semantic_type": "gauge",
                    "unit": "dbm",
                },
            },
        },
        records=[
            {
                "sensor_id": "one",
                "signal_dbm": -10.0,
                "alarm_low_signal_dbm": -18.0,
            }
        ],
    )

    target = result["policy_targets"][0]
    assert "low" not in target["threshold_references"]
    critical = target["threshold_references"]["critical_low"]
    assert critical["field_name"] == "alarm_low_signal_dbm"
    assert critical["context_key"] == "threshold.critical_low"
    assert critical["level"] == "alarm"
    assert critical["source"] == "structured_reference"
    assert critical["field_profile"]["numeric_count"] == 1
    assert (
        result["contract"]["state_semantics"]["alarm_low_signal_dbm"][
            "threshold_role"
        ]
        == "critical_low"
    )
