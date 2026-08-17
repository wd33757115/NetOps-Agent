from __future__ import annotations

from src.core.observation.record_set_relations import (
    apply_record_set_relations,
    materialize_record_set_references,
    required_threshold_reference_errors,
)


def _contract(*, relations: bool = True) -> dict:
    contract = {
        "entity_type": "multi_record",
        "record_sets": [
            {
                "name": "measurements",
                "entity_type": "sensor",
                "primary_keys": ["interface", "channel"],
                "required_fields": ["interface", "channel", "rx_power"],
                "optional_fields": [],
                "validators": {"rx_power": {"type": "number"}},
                "state_semantics": {
                    "interface": {"semantic_type": "identity"},
                    "channel": {"semantic_type": "identity"},
                    "rx_power": {
                        "semantic_type": "gauge",
                        "observation_type": "interface.optical.rx_power",
                        "policy_constraints": {
                            "require_structured_threshold_references": True,
                            "required_threshold_reference_any": [
                                "low",
                                "critical_low",
                            ],
                        },
                    },
                },
            },
            {
                "name": "thresholds",
                "entity_type": "interface_threshold",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "rx_alarm_low"],
                "optional_fields": [],
                "validators": {
                    "rx_alarm_low": {
                        "type": "number",
                        "unavailable_tokens": ["N/A"],
                    }
                },
                "state_semantics": {
                    "interface": {"semantic_type": "identity"},
                    "rx_alarm_low": {
                        "semantic_type": "gauge",
                        "comparison_role": "threshold",
                        "monitor": False,
                    },
                },
            },
        ],
    }
    if relations:
        contract["record_set_relations"] = [
            {
                "name": "measurement_thresholds",
                "source_record_set": "measurements",
                "target_record_set": "thresholds",
                "join_keys": {"interface": "interface"},
                "threshold_bindings": {
                    "rx_power": {"critical_low": "rx_alarm_low"}
                },
                "match_policy": "partial",
            }
        ]
    return contract


def _groups() -> list[dict]:
    return [
        {
            "group_id": "snapshot-1",
            "records": [
                {
                    "__record_set__": "measurements",
                    "interface": "Eth1/1",
                    "channel": "1",
                    "rx_power": "-8.2",
                },
                {
                    "__record_set__": "measurements",
                    "interface": "Eth1/2",
                    "channel": "1",
                    "rx_power": "-9.0",
                },
                {
                    "__record_set__": "thresholds",
                    "interface": "Eth1/1",
                    "rx_alarm_low": "-12.0",
                },
            ],
        },
        {
            "group_id": "snapshot-2",
            "records": [
                {
                    "__record_set__": "measurements",
                    "interface": "Eth1/1",
                    "channel": "1",
                    "rx_power": "-8.4",
                },
                {
                    "__record_set__": "thresholds",
                    "interface": "Eth1/1",
                    "rx_alarm_low": "-13.0",
                },
            ],
        },
    ]


def test_relation_is_proved_per_snapshot_and_compiled_into_semantics():
    result = apply_record_set_relations(
        contract=_contract(),
        record_groups=_groups(),
    )

    assert result["success"] is True
    report = result["reports"][0]
    assert report["source_record_count"] == 3
    assert report["matched_record_count"] == 2
    assert report["unmatched_record_count"] == 1
    source = result["contract"]["record_sets"][0]
    reference = source["state_semantics"]["rx_power"][
        "threshold_references"
    ]["critical_low"]
    assert reference["record_set_name"] == "thresholds"
    assert reference["join_keys"] == {"interface": "interface"}
    assert reference["context_key"] == "threshold.critical_low"
    assert required_threshold_reference_errors(result["contract"]) == []


def test_approved_alarm_label_overrides_model_warning_role():
    contract = _contract()
    contract["record_sets"][1]["source_field_hints"] = {
        "rx_alarm_low": {
            "source_labels": ["Alarm Low RX power(dBm)"],
            "role": "threshold",
        }
    }
    contract["record_set_relations"][0]["threshold_bindings"] = {
        "rx_power": {"low": "rx_alarm_low"}
    }

    result = apply_record_set_relations(
        contract=contract,
        record_groups=_groups(),
    )

    assert result["success"] is True
    assert result["contract"]["record_set_relations"][0][
        "threshold_bindings"
    ] == {"rx_power": {"critical_low": "rx_alarm_low"}}
    assert result["role_normalizations"] == [
        {
            "kind": "threshold_role_from_approved_source_label",
            "relation_name": "measurement_thresholds",
            "target_record_set": "thresholds",
            "target_field": "rx_alarm_low",
            "proposed_role": "low",
            "effective_role": "critical_low",
            "source_labels": ["Alarm Low RX power(dBm)"],
        }
    ]


def test_role_normalization_has_stable_fallback_relation_name():
    contract = _contract()
    contract["record_sets"][1]["source_field_hints"] = {
        "rx_alarm_low": {
            "source_labels": ["Alarm Low RX power(dBm)"],
            "role": "threshold",
        }
    }
    contract["record_set_relations"][0].pop("name")
    contract["record_set_relations"][0]["threshold_bindings"] = {
        "rx_power": {"low": "rx_alarm_low"}
    }

    result = apply_record_set_relations(
        contract=contract,
        record_groups=_groups(),
    )

    assert result["success"] is True
    assert result["role_normalizations"][0]["relation_name"] == (
        "measurements_to_thresholds"
    )


def test_relation_rejects_ambiguous_target_key_within_snapshot():
    groups = _groups()
    groups[0]["records"].append(
        {
            "__record_set__": "thresholds",
            "interface": "Eth1/1",
            "rx_alarm_low": "-14.0",
        }
    )

    result = apply_record_set_relations(
        contract=_contract(),
        record_groups=groups,
    )

    assert result["success"] is False
    assert any("duplicate target join key" in error for error in result["errors"])


def test_relation_accepts_explicitly_unavailable_threshold_value():
    groups = _groups()
    groups[0]["records"].append(
        {
            "__record_set__": "thresholds",
            "interface": "Eth1/2",
            "rx_alarm_low": "N/A",
        }
    )

    result = apply_record_set_relations(
        contract=_contract(),
        record_groups=groups,
    )

    assert result["success"] is True
    assert result["reports"][0]["matched_record_count"] == 3
    assert result["reports"][0]["matched_threshold_values"] == {
        "rx_power.critical_low": 2
    }


def test_required_device_threshold_is_blocked_when_relation_is_missing():
    errors = required_threshold_reference_errors(_contract(relations=False))

    assert errors == [
        {
            "code": "structured_threshold_reference_missing",
            "record_set_name": "measurements",
            "field_name": "rx_power",
            "observation_type": "interface.optical.rx_power",
            "required_any": ["critical_low", "low"],
            "message": (
                "measurements.rx_power requires a validated structured threshold "
                "reference"
            ),
        }
    ]


def test_runtime_materialization_joins_only_the_current_snapshot():
    compiled = apply_record_set_relations(
        contract=_contract(),
        record_groups=_groups(),
    )["contract"]
    structured = {
        "measurements": [
            {"interface": "Eth1/1", "channel": "1", "rx_power": "-8.2"},
            {"interface": "Eth1/2", "channel": "1", "rx_power": "-9.0"},
        ],
        "thresholds": [
            {"interface": "Eth1/1", "rx_alarm_low": "-12.0"}
        ],
    }

    materialized, record_sets = materialize_record_set_references(
        contract=compiled,
        structured_sets=structured,
    )

    reference = record_sets[0]["state_semantics"]["rx_power"][
        "threshold_references"
    ]["critical_low"]
    runtime_field = reference["runtime_field_name"]
    assert materialized["measurements"][0][runtime_field] == "-12.0"
    assert runtime_field not in materialized["measurements"][1]
    assert record_sets[0]["validators"] == {"rx_power": {"type": "number"}}
    assert reference["validator"] == {
        "type": "number",
        "unavailable_tokens": ["N/A"],
    }
