from __future__ import annotations

import pytest

from src.core.parser.contract_inference import infer_contract
from src.core.parser.key_contract import (
    SINGLETON_ENTITY_KEY,
    EntityKeyError,
    build_entity_key,
    index_records,
    validate_entity_keys,
)
from src.core.patrol.change_detector import detect_changes_between_values


def test_single_record_uses_external_snapshot_scope():
    result = validate_entity_keys([{"model": "S5130", "version": "7.1"}], [])

    assert result.valid
    assert result.keys == (SINGLETON_ENTITY_KEY,)


def test_collection_requires_explicit_primary_keys():
    result = validate_entity_keys([{"slot": "1"}, {"slot": "2"}], [])

    assert not result.valid
    assert "multiple records require explicit primary_keys" in result.errors[0]


def test_composite_key_requires_all_fields_and_avoids_delimiter_collisions():
    first = build_entity_key(
        {"slot": "1|2", "fan": "3"},
        ["slot", "fan"],
    )
    second = build_entity_key(
        {"slot": "1", "fan": "2|3"},
        ["slot", "fan"],
    )

    assert first != second
    assert not validate_entity_keys(
        [{"slot": "1", "fan": ""}],
        ["slot", "fan"],
    ).valid


def test_duplicate_keys_are_rejected_instead_of_silently_overwritten():
    records = [
        {"interface": "Gi1/0/1", "status": "up"},
        {"interface": "Gi1/0/1", "status": "down"},
    ]

    with pytest.raises(EntityKeyError, match="duplicate entity keys"):
        index_records(records, ["interface"])

    with pytest.raises(EntityKeyError, match="duplicate entity keys"):
        detect_changes_between_values(
            records,
            records,
            entity_type="interface_l3",
            primary_keys=["interface"],
        )


def test_singleton_field_change_is_reported_as_modified():
    changes = detect_changes_between_values(
        [{"model": "S5130", "os_version": "7.1"}],
        [{"model": "S5130", "os_version": "7.2"}],
        entity_type="device",
        primary_keys=[],
    )

    assert len(changes) == 1
    assert changes[0].entity_key == SINGLETON_ENTITY_KEY
    assert changes[0].field == "os_version"
    assert changes[0].change_type == "modified"


def test_contract_inference_proposes_stable_interface_identity():
    proposal = infer_contract(
        command="show ip interface brief",
        samples_text="Interface IP-Address OK? Method Status Protocol",
    )

    assert proposal.category == "interface_l3"
    assert proposal.primary_keys == ("interface",)
    assert proposal.validators["interface"]["type"] == "interface_name"
    assert proposal.validators["ip_address"]["type"] == "ip_or_unassigned"


def test_contract_inference_uses_llm_only_for_unknown_semantics():
    proposal = infer_contract(
        command="show fabric widget",
        samples_text="Widget State\n1 up",
        llm_call=lambda _: """{
          "category": "fabric_widget",
          "entity_type": "widget",
          "primary_keys": ["widget"],
          "required_fields": ["widget", "state"],
          "optional_fields": [],
          "validators": {},
          "allow_empty": false,
          "empty_patterns": [],
          "ignore_fields": []
        }""",
    )

    assert proposal.source == "llm_proposal"
    assert proposal.primary_keys == ("widget",)
