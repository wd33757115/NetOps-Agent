from __future__ import annotations

import pytest

from scripts.test.lixian_rule_report_e2e import (
    ASSET_SPECS,
    _event_constraints,
    _merge_rule_generation_seed,
    _observation_target_fields,
)


def _contract(*, optional_fields: list[str]) -> dict:
    fields = ["interface", *optional_fields]
    return {
        "entity_type": "interface_optical",
        "primary_keys": ["interface"],
        "required_fields": ["interface"],
        "optional_fields": optional_fields,
        "validators": {field: {"type": "number"} for field in optional_fields},
        "state_semantics": {
            "interface": {"semantic_type": "identity"},
            **{
                field: {"semantic_type": "gauge", "unit": "dbm"}
                for field in fields
                if field != "interface"
            },
        },
    }


def test_model_contract_merge_keeps_union_optional_and_intersection_required():
    reports = [
        {"vendor": "H3C", "model": "S5130S-52S-HI"},
        {"vendor": "H3C", "model": "MSR5620"},
    ]
    contracts = [
        _contract(optional_fields=["rx_power_dbm", "rx_low_alarm_dbm"]),
        _contract(optional_fields=["transceiver_present"]),
    ]

    merged = _merge_rule_generation_seed(reports, contracts)

    assert merged["required_fields"] == ["interface"]
    assert set(merged["optional_fields"]) == {
        "rx_power_dbm",
        "rx_low_alarm_dbm",
        "transceiver_present",
    }
    assert merged["source"] == "model_contract_compatibility_merge"
    assert len(merged["evidence"]["compatibility_profiles"]) == 2


def test_model_contract_merge_blocks_entity_identity_conflict():
    reports = [
        {"vendor": "H3C", "model": "S5130S-52S-HI"},
        {"vendor": "H3C", "model": "MSR5620"},
    ]
    first = _contract(optional_fields=["rx_power_dbm"])
    second = {**_contract(optional_fields=[]), "entity_type": "interface"}

    with pytest.raises(AssertionError, match="disagree on entity identity"):
        _merge_rule_generation_seed(reports, [first, second])


def test_alarm_only_optical_policy_requires_critical_and_recovery_only():
    spec = next(item for item in ASSET_SPECS if item["key"] == "optical")
    constraints = _event_constraints(
        spec,
        "interface.optical.rx_power",
        "rx_power_dbm",
        {
            "threshold_references": {
                "critical_low": {"field_name": "rx_low_alarm_dbm"}
            }
        },
    )

    assert constraints["required_event_types"] == [
        "OpticalRxPowerCritical",
        "OpticalRxPowerRecovered",
    ]
    assert constraints["required_condition_values"][
        "OpticalRxPowerCritical"
    ] == {"reference_field": "rx_low_alarm_dbm"}


def test_warning_and_alarm_policy_requires_distinct_precedence():
    spec = next(item for item in ASSET_SPECS if item["key"] == "optical")
    constraints = _event_constraints(
        spec,
        "interface.optical.tx_power",
        "tx_power_dbm",
        {
            "threshold_references": {
                "low": {"field_name": "tx_low_warning_dbm"},
                "critical_low": {"field_name": "tx_low_alarm_dbm"},
            }
        },
    )

    assert constraints["required_event_types"] == [
        "OpticalTxPowerLow",
        "OpticalTxPowerCritical",
        "OpticalTxPowerRecovered",
    ]
    assert constraints["required_exclusive_event_types"] == [
        "OpticalTxPowerCritical"
    ]
    assert constraints["priority_order"] == [
        {
            "higher": "OpticalTxPowerCritical",
            "lower": "OpticalTxPowerLow",
        }
    ]


def test_profile_target_resolution_uses_canonical_semantics_without_model_rules():
    contract = {
        "required_fields": ["interface_name"],
        "optional_fields": [
            "current_rx",
            "rx_low_warning_dbm",
            "rx_low_alarm_dbm",
        ],
        "state_semantics": {
            "current_rx": {
                "semantic_type": "gauge",
                "unit": "dbm",
                "observation_type": "interface.optical.rx_power",
            }
        },
    }

    assert _observation_target_fields(
        contract,
        {
            "canonical_field": "rx_power_dbm",
            "observation_type": "interface.optical.rx_power",
        },
    ) == ["current_rx"]
