from __future__ import annotations

from src.core.observation.contracts import build_contract_payload


def test_observation_contract_payload_preserves_multi_record_semantics():
    payload = build_contract_payload(
        source_type="cli",
        schema_id="parser-display-version",
        validation={
            "entity_type": "multi_record",
            "record_sets": [
                {
                    "name": "device_version",
                    "entity_type": "device",
                    "primary_keys": [],
                    "required_fields": ["software_version"],
                    "state_semantics": {
                        "software_version": {
                            "semantic_type": "text",
                            "observation_type": "device.software.version",
                        }
                    },
                },
                {
                    "name": "slot_inventory",
                    "entity_type": "device_slot",
                    "primary_keys": ["slot"],
                    "required_fields": ["slot", "board_type"],
                    "state_semantics": {
                        "slot": {"semantic_type": "identity"},
                        "board_type": {
                            "semantic_type": "text",
                            "observation_type": "device.slot.board_type",
                        },
                    },
                },
            ],
        },
    )

    assert [item["name"] for item in payload["record_sets"]] == [
        "device_version",
        "slot_inventory",
    ]
    assert payload["record_sets"][1]["primary_keys"] == ["slot"]
    assert len(payload["contract_sha256"]) == 64


def test_observation_contract_payload_preserves_canonical_entity_projection():
    payload = build_contract_payload(
        source_type="cli",
        schema_id="parser-display-cpu",
        validation={
            "entity_type": "cpu_record",
            "primary_keys": ["slot", "cpu"],
            "canonical_entity_type": "cpu_scope",
            "entity_identity_fields": ["scope", "scope_id"],
            "entity_dimensions": {
                "scope": {"literal": "processor"},
                "scope_id": {
                    "op": "concat",
                    "args": [
                        {"field": "slot"},
                        {"literal": "/"},
                        {"field": "cpu"},
                    ],
                },
            },
            "required_fields": ["slot", "cpu", "usage_5s"],
        },
    )

    assert payload["entity_type"] == "cpu_record"
    assert payload["canonical_entity_type"] == "cpu_scope"
    assert payload["entity_identity_fields"] == ("scope", "scope_id")
    assert payload["entity_dimensions"]["scope_id"]["op"] == "concat"
