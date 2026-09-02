from __future__ import annotations

import pytest

from src.core.parser_generation import ParserSpec, ParserSpecDocument


def test_legacy_parser_spec_upgrades_to_one_record_set_without_digest_shape_drift():
    payload = {
        "entity": "interface_l2",
        "primary_keys": ["interface"],
        "fields": {
            "interface": {
                "required": True,
                "type": "interface_name",
            },
            "status": {
                "required": True,
                "type": "text",
            },
        },
        "allow_empty": False,
        "empty_patterns": [],
    }

    spec = ParserSpec.from_dict(payload)
    document = ParserSpecDocument.from_dict(payload)

    assert spec.to_dict() == payload
    assert document.single_record_set.entity == "interface_l2"
    assert document.single_record_set.record_set_name == "interface_l2"
    assert document.is_multi_record is False


def test_parser_spec_v2_supports_device_slot_and_subslot_record_sets():
    payload = {
        "version": 2,
        "business_purpose": (
            "Inspect device software, slot inventory and subslot inventory"
        ),
        "record_sets": [
            {
                "name": "device_version",
                "entity": "device",
                "cardinality": "singleton",
                "primary_keys": [],
                "fields": {
                    "os_version": {
                        "required": True,
                        "type": "text",
                        "role": "state",
                        "operational_reason": "Detect unexpected software changes",
                        "change_behavior": "stable",
                        "monitor": True,
                        "canonical_candidate": "device.software.version",
                        "evidence": ["Version 7.1.064"],
                    }
                },
                "allow_empty": False,
                "empty_patterns": [],
                "expected_records": [{"os_version": "7.1.064"}],
            },
            {
                "name": "slot_inventory",
                "entity": "device_slot",
                "cardinality": "repeated",
                "primary_keys": ["slot"],
                "fields": {
                    "slot": {
                        "required": True,
                        "type": "number",
                        "role": "identity",
                    },
                    "board_type": {
                        "required": True,
                        "type": "text",
                        "role": "state",
                    },
                },
                "allow_empty": False,
                "empty_patterns": [],
                "expected_records": [{"slot": "0", "board_type": "MPU-60"}],
            },
            {
                "name": "subslot_inventory",
                "entity": "device_subslot",
                "cardinality": "repeated",
                "primary_keys": ["slot", "subslot_number", "subslot_type"],
                "fields": {
                    "slot": {
                        "required": True,
                        "type": "number",
                        "role": "identity",
                    },
                    "subslot_number": {
                        "required": True,
                        "type": "number",
                        "role": "identity",
                    },
                    "subslot_type": {
                        "required": True,
                        "type": "text",
                        "role": "identity",
                    },
                },
                "allow_empty": False,
                "empty_patterns": [],
                "expected_records": [],
            },
        ],
    }

    document = ParserSpecDocument.from_dict(payload)

    assert document.to_dict() == payload
    assert document.is_multi_record is True
    assert [item.record_set_name for item in document.record_sets] == [
        "device_version",
        "slot_inventory",
        "subslot_inventory",
    ]
    assert document.record_sets[0].primary_keys == ()
    assert document.record_sets[0].cardinality == "singleton"


def test_repeated_record_set_requires_primary_key():
    with pytest.raises(ValueError, match="requires at least one primary key"):
        ParserSpec.from_dict(
            {
                "name": "bad_rows",
                "entity": "bad_row",
                "cardinality": "repeated",
                "primary_keys": [],
                "fields": {"value": {"required": True, "type": "text"}},
            }
        )


def test_parser_spec_rejects_unqualified_canonical_candidate():
    with pytest.raises(ValueError, match="canonical candidate"):
        ParserSpec.from_dict(
            {
                "entity": "device",
                "primary_keys": [],
                "fields": {
                    "value": {
                        "required": True,
                        "type": "text",
                        "canonical_candidate": "cpu_usage",
                    }
                },
            }
        )


def test_parser_spec_v2_supports_optional_record_sets_and_numeric_unavailability():
    payload = {
        "version": 2,
        "business_purpose": "Inspect optional optical diagnostics",
        "record_sets": [
            {
                "name": "optical_channels",
                "entity": "interface_optical_channel",
                "cardinality": "repeated",
                "presence": "optional",
                "primary_keys": ["interface", "channel"],
                "fields": {
                    "interface": {"required": True, "type": "interface_name"},
                    "channel": {"required": True, "type": "number"},
                    "rx_power_dbm": {
                        "required": True,
                        "type": "number",
                        "unavailable_tokens": ["N/A", "--"],
                    },
                },
                "allow_empty": False,
                "empty_patterns": [],
            }
        ],
    }

    document = ParserSpecDocument.from_dict(payload)

    assert document.to_dict() == {**payload, "record_sets": [{**payload["record_sets"][0], "expected_records": []}]}
    record_set = document.record_sets[0]
    assert record_set.presence == "optional"
    assert record_set.field_map["rx_power_dbm"].validator() == {
        "type": "number",
        "unavailable_tokens": ["N/A", "--"],
    }


def test_parser_spec_v2_treats_single_unavailable_token_as_one_value():
    payload = {
        "version": 2,
        "business_purpose": "Inspect optical receive power",
        "record_sets": [
            {
                "name": "optical_power",
                "entity": "interface_optical",
                "cardinality": "repeated",
                "primary_keys": ["interface"],
                "fields": {
                    "interface": {"required": True, "type": "interface_name"},
                    "rx_power_dbm": {
                        "required": True,
                        "type": "number",
                        "unavailable_tokens": "N/A",
                    },
                },
            }
        ],
    }

    document = ParserSpecDocument.from_dict(payload)

    assert document.record_sets[0].field_map["rx_power_dbm"].unavailable_tokens == (
        "N/A",
    )


def test_parser_spec_allows_an_explicit_default_for_a_sparse_primary_key():
    payload = {
        "version": 2,
        "business_purpose": "Inspect optical records whose aggregate channel is implicit.",
        "record_sets": [
            {
                "name": "optics",
                "entity": "interface_optical",
                "cardinality": "repeated",
                "primary_keys": ["interface", "channel_token"],
                "fields": {
                    "interface": {"required": True, "type": "interface_name"},
                    "channel_token": {
                        "required": False,
                        "type": "text",
                        "primary_key_default": "aggregate",
                    },
                },
            }
        ],
    }

    document = ParserSpecDocument.from_dict(payload)

    assert document.to_dict()["record_sets"][0]["fields"]["channel_token"] == {
        "required": False,
        "type": "text",
        "primary_key_default": "aggregate",
    }


def test_parser_spec_rejects_a_primary_key_default_outside_the_primary_key():
    with pytest.raises(ValueError, match="allowed only for primary keys"):
        ParserSpec.from_dict(
            {
                "entity": "interface",
                "primary_keys": ["interface"],
                "fields": {
                    "interface": {"required": True, "type": "interface_name"},
                    "label": {
                        "required": False,
                        "type": "text",
                        "primary_key_default": "unknown",
                    },
                },
            }
        )
