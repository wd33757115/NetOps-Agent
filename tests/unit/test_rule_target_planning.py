from __future__ import annotations

from src.core.rule_control.planning import (
    compile_rule_targets,
    normalize_contract_semantics,
    profile_fields,
)
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
    assert result["event_observation_types"] == [
        "network.entity.operational_state"
    ]


def test_rule_target_compiler_only_promotes_evidenced_fault_semantics_to_events():
    result = compile_rule_targets(
        contract={
            "required_fields": ["entity", "oper_status"],
            "optional_fields": ["duplex", "speed"],
            "state_semantics": {
                "entity": {"semantic_type": "identity"},
                "oper_status": {
                    "semantic_type": "state",
                    "observation_type": "interface.oper_status",
                    "value_mapping": {
                        "connected": "up",
                        "notconnect": "down",
                    },
                },
                "duplex": {
                    "semantic_type": "state",
                    "observation_type": "interface.duplex",
                },
                "speed": {
                    "semantic_type": "gauge",
                    "observation_type": "interface.speed",
                },
            },
        },
        records=[
            {
                "entity": "one",
                "oper_status": "connected",
                "duplex": "full",
                "speed": "1000",
            }
        ],
    )

    assert result["success"] is True
    assert [item["observation_type"] for item in result["policy_targets"]] == [
        "interface.duplex",
        "interface.oper_status",
        "interface.speed",
    ]
    assert result["event_observation_types"] == ["interface.oper_status"]
    by_type = {
        item["observation_type"]: item for item in result["policy_targets"]
    }
    assert by_type["interface.oper_status"]["event_generation_reason"] == (
        "governed_state_polarity"
    )
    assert by_type["interface.duplex"]["event_generation"] is False
    assert by_type["interface.speed"]["event_generation"] is False


def test_rule_target_compiler_governs_weak_model_event_suggestions():
    result = compile_rule_targets(
        contract={
            "required_fields": ["device_name"],
            "optional_fields": [
                "software_version",
                "memory_capacity",
                "uptime_text",
                "reset_reason",
            ],
            "state_semantics": {
                "device_name": {
                    "semantic_type": "identity",
                    "comparison_role": "identity",
                    "monitor": False,
                },
                "software_version": {
                    "semantic_type": "text",
                    "observation_type": "device.software_version",
                    "comparison_role": "observation",
                    "monitor": True,
                    "event_generation": True,
                    "event_role": "change_only",
                },
                "memory_capacity": {
                    "semantic_type": "gauge",
                    "observation_type": "device.memory_capacity",
                    "comparison_role": "observation",
                    "monitor": True,
                    "event_generation": True,
                    "event_role": "capacity",
                },
                "uptime_text": {
                    "semantic_type": "text",
                    "observation_type": "device.uptime",
                    "comparison_role": "observation",
                    "monitor": True,
                    "event_generation": True,
                    "event_role": "health",
                },
                "reset_reason": {
                    "semantic_type": "text",
                    "observation_type": "device.reset_reason",
                    "comparison_role": "observation",
                    "monitor": True,
                    "event_generation": True,
                    "event_role": "fault_state",
                },
            },
        },
        records=[
            {
                "device_name": "one",
                "software_version": "1.0",
                "memory_capacity": 1024,
                "uptime_text": "10 days",
                "reset_reason": "Unknown",
            },
            {
                "device_name": "one",
                "software_version": "1.0",
                "memory_capacity": 1024,
                "uptime_text": "20 days",
                "reset_reason": "Unknown",
            },
        ],
    )

    by_type = {
        item["observation_type"]: item for item in result["policy_targets"]
    }
    assert result["event_observation_types"] == ["device.software_version"]
    assert by_type["device.software_version"]["event_generation_reason"] == (
        "explicit_change_event"
    )
    assert by_type["device.memory_capacity"]["event_generation_reason"] == (
        "static_capacity_has_no_threshold_evidence"
    )
    assert by_type["device.uptime"]["event_generation_reason"] == (
        "event_semantics_not_executable"
    )
    assert by_type["device.reset_reason"]["event_generation_reason"] == (
        "event_semantics_not_executable"
    )


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


def test_business_semantic_aliases_compile_to_executable_text_comparison():
    contract = normalize_contract_semantics(
        {
            "required_fields": ["slot", "software_version"],
            "optional_fields": [],
            "state_semantics": {
                "slot": {"semantic_type": "identity"},
                "software_version": {
                    "semantic_type": "software_version",
                    "observation_type": "slot.software_version",
                    "comparison_role": "observation",
                    "monitor": True,
                },
            },
        }
    )

    semantics = contract["state_semantics"]["software_version"]
    assert semantics["semantic_type"] == "text"
    assert semantics["domain_semantic_type"] == "software_version"

    result = compile_rule_targets(
        contract=contract,
        records=[{"slot": "0", "software_version": "7.1.064, Release 0821P18"}],
    )

    assert result["success"] is True
    assert result["policy_targets"][0]["field_semantics"]["semantic_type"] == "text"


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


def test_rule_target_compiler_allows_one_metric_with_distinct_dimensions():
    result = compile_rule_targets(
        contract={
            "required_fields": ["slot", "cpu_5s", "cpu_1m"],
            "optional_fields": [],
            "state_semantics": {
                "slot": {"semantic_type": "identity"},
                "cpu_5s": {
                    "semantic_type": "percentage",
                    "observation_type": "device.cpu.utilization",
                    "dimensions": {
                        "scope": {"literal": "slot"},
                        "window": {"literal": "5s"},
                    },
                },
                "cpu_1m": {
                    "semantic_type": "percentage",
                    "observation_type": "device.cpu.utilization",
                    "dimensions": {
                        "scope": {"literal": "slot"},
                        "window": {"literal": "1m"},
                    },
                },
            },
        },
        records=[{"slot": "0", "cpu_5s": 12, "cpu_1m": 15}],
    )

    assert result["success"] is True
    assert len(result["policy_targets"]) == 2
    assert {
        item["dimensions"]["window"]["literal"]
        for item in result["policy_targets"]
    } == {"5s", "1m"}
    assert {
        item["dimension_selector"]["window"]
        for item in result["policy_targets"]
    } == {"5s", "1m"}


def test_rule_target_compiler_blocks_ambiguous_duplicate_metric_mapping():
    result = compile_rule_targets(
        contract={
            "required_fields": ["cpu_current", "cpu_average"],
            "optional_fields": [],
            "state_semantics": {
                "cpu_current": {
                    "semantic_type": "percentage",
                    "observation_type": "device.cpu.utilization",
                },
                "cpu_average": {
                    "semantic_type": "percentage",
                    "observation_type": "device.cpu.utilization",
                },
            },
        },
        records=[{"cpu_current": 12, "cpu_average": 15}],
    )

    assert result["success"] is False
    assert result["blockers"][0]["code"] == "ambiguous_observation_dimensions"


def test_rule_target_compiler_keeps_multi_record_fields_in_their_own_scope():
    result = compile_rule_targets(
        contract={
            "entity_type": "multi_record",
            "record_sets": [
                {
                    "name": "device_version",
                    "entity_type": "device",
                    "primary_keys": [],
                    "required_fields": ["software_version"],
                    "optional_fields": [],
                    "state_semantics": {
                        "software_version": {
                            "semantic_type": "text",
                            "observation_type": "device.software.version",
                            "event_generation": False,
                            "event_role": "change_only",
                        }
                    },
                },
                {
                    "name": "slot_health",
                    "entity_type": "device_slot",
                    "primary_keys": ["slot"],
                    "required_fields": ["slot", "cpu_5s"],
                    "optional_fields": [],
                    "state_semantics": {
                        "slot": {"semantic_type": "identity"},
                        "cpu_5s": {
                            "semantic_type": "percentage",
                            "observation_type": "device.cpu.utilization",
                            "dimensions": {
                                "scope": {"literal": "slot"},
                                "window": {"literal": "5s"},
                            },
                            "event_generation": True,
                            "event_role": "health",
                        },
                    },
                },
            ],
        },
        records=[
            {
                "__record_set__": "device_version",
                "software_version": "7.1.064",
            },
            {
                "__record_set__": "slot_health",
                "slot": "0",
                "cpu_5s": 24,
            },
        ],
    )

    assert result["success"] is True
    by_type = {
        item["observation_type"]: item
        for item in result["policy_targets"]
    }
    assert by_type["device.software.version"]["record_set_name"] == (
        "device_version"
    )
    assert by_type["device.cpu.utilization"]["field_ref"] == (
        "slot_health.cpu_5s"
    )
    assert result["historical_values_by_field"]["slot_health.cpu_5s"] == [24]
