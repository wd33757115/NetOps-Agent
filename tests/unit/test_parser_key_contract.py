from __future__ import annotations

import pytest

from src.core.parser.contract_inference import infer_contract
from src.core.parser.contract_validation import validate_contract_candidate
from src.core.parser.key_contract import (
    SINGLETON_ENTITY_KEY,
    EntityKeyError,
    build_entity_key,
    index_records,
    validate_entity_keys,
)
from src.core.parser.semantic_catalog import field_aliases, field_semantics
from src.core.parser.structure_profiler import profile_sample_set
from src.core.patrol.change_detector import detect_changes_between_values


def test_single_record_uses_external_snapshot_scope():
    result = validate_entity_keys([{"model": "S5130", "version": "7.1"}], [])

    assert result.valid
    assert result.keys == (SINGLETON_ENTITY_KEY,)


def test_semantic_suffix_composition_is_field_name_driven():
    aliases = field_aliases("channel_rx_power_dbm")
    measurement = field_semantics("channel_rx_power_dbm")
    threshold = field_semantics("alarm_high_rx_power_dbm")

    assert "rx power" in aliases
    assert measurement["semantic_type"] == "gauge"
    assert measurement["unit"] == "dbm"
    assert measurement["observation_type"] == "interface.optical.rx_power"
    assert "applicability" not in measurement
    assert threshold["semantic_type"] == "gauge"
    assert threshold["unit"] == "dbm"
    assert "observation_type" not in threshold
    assert "voltage" in field_aliases("module_voltage_volts")
    assert field_semantics("module_voltage_volts") == {
        "semantic_type": "gauge",
        "unit": "volt",
    }
    assert field_semantics("channel_bias_milliamps") == {
        "semantic_type": "gauge",
        "unit": "milliampere",
    }


def test_multi_table_contract_rebuilds_column_evidence_and_optionalizes_sparse_fields():
    body = (
        "First group:\n"
        "Name    State  Metric\n"
        "A       up     1\n"
        "B       down   2\n"
        "\n"
        "Second group:\n"
        "Name    Status Speed\n"
        "C       up     10\n"
        "D       down   20"
    )
    samples = f'<CLI_SAMPLE id="sample-1" sha256="abc">\n{body}\n</CLI_SAMPLE>'
    proposal = infer_contract(
        command="inspect items",
        samples_text=samples,
        structure_profile=profile_sample_set([("sample-1", body)]),
        llm_call=lambda _prompt: """{
          "category": "item",
          "entity_type": "item",
          "primary_keys": ["name"],
          "required_fields": ["name", "state", "metric"],
          "optional_fields": ["speed"],
          "validators": {
            "state": {"type": "enum", "values": ["up", "down"]},
            "metric": {"type": "number"},
            "speed": {"type": "number"}
          },
          "evidence": {
            "field_bindings": {
              "name": [{
                "sample_id":"sample-1","line":3,"source_label":"A",
                "source_value":"A","capture_kind":"column"
              }],
              "state": [{
                "sample_id":"sample-1","line":3,"source_label":"up",
                "source_value":"up","capture_kind":"column"
              }],
              "metric": [{
                "sample_id":"sample-1","line":8,"source_label":"10",
                "source_value":"10","capture_kind":"column"
              }],
              "speed": [{
                "sample_id":"sample-1","line":8,"source_label":"10",
                "source_value":"10","capture_kind":"column"
              }]
            }
          }
        }""",
    )

    valid, diagnostics = validate_contract_candidate(
        proposal,
        structure_profile=profile_sample_set([("sample-1", body)]),
        samples_text=samples,
    )

    assert valid, diagnostics
    assert proposal.required_fields == ("name", "state")
    assert set(proposal.optional_fields) == {"metric", "speed"}
    metric = proposal.evidence["field_bindings"]["metric"]
    assert metric == [
        {
            "sample_id": "sample-1",
            "line": 3,
            "header_line": 2,
            "source_label": "Metric",
            "source_value": "1",
            "capture_kind": "column",
        }
    ]
    normalization = proposal.evidence["table_binding_normalization"]
    assert normalization["region_count"] == 2
    assert normalization["reclassified_fields"] == ["metric"]


def test_contract_field_identifiers_are_normalized_across_all_references():
    body = "Item Name  Reading Value\nitem-a     1\nitem-b     2"
    samples = f'<CLI_SAMPLE id="sample-1" sha256="abc">\n{body}\n</CLI_SAMPLE>'
    plan = profile_sample_set([("sample-1", body)])
    proposal = infer_contract(
        command="inspect readings",
        samples_text=samples,
        structure_profile=plan,
        llm_call=lambda _prompt: """{
          "category": "reading",
          "entity_type": "reading",
          "primary_keys": ["Item Name"],
          "required_fields": ["Item Name", "Reading Value"],
          "optional_fields": [],
          "validators": {"Reading Value": {"type": "number"}},
          "state_semantics": {
            "Reading Value": {
              "semantic_type": "gauge",
              "applicability": {
                "version": 1,
                "on_missing_context": "allow",
                "exclude_when": [{
                  "code": "zero",
                  "when": {"field": "Reading Value", "op": "lte", "value": 0}
                }]
              }
            }
          },
          "evidence": {
            "field_bindings": {
              "Item Name": [{
                "sample_id":"sample-1","line":2,"source_label":"item-a",
                "source_value":"item-a","capture_kind":"column"
              }],
              "Reading Value": [{
                "sample_id":"sample-1","line":2,"source_label":"1",
                "source_value":"1","capture_kind":"column"
              }]
            }
          }
        }""",
    )

    valid, diagnostics = validate_contract_candidate(
        proposal,
        structure_profile=plan,
        samples_text=samples,
    )

    assert valid, diagnostics
    assert proposal.primary_keys == ("item_name",)
    assert proposal.required_fields == ("item_name", "reading_value")
    assert proposal.validators == {"reading_value": {"type": "number"}}
    assert set(proposal.evidence["field_bindings"]) == {
        "item_name",
        "reading_value",
    }
    assert proposal.evidence["field_name_normalization"]["mapping"] == {
        "Item Name": "item_name",
        "Reading Value": "reading_value",
    }
    assert (
        proposal.state_semantics["reading_value"]["applicability"]["exclude_when"][0][
            "when"
        ]["field"]
        == "reading_value"
    )


def test_contract_removes_applicability_with_undeclared_context_fields():
    body = "Item Name  Reading Value\nitem-a     1\nitem-b     2"
    samples = f'<CLI_SAMPLE id="sample-1" sha256="abc">\n{body}\n</CLI_SAMPLE>'
    plan = profile_sample_set([("sample-1", body)])
    proposal = infer_contract(
        command="inspect readings",
        samples_text=samples,
        structure_profile=plan,
        llm_call=lambda _prompt: """{
          "category": "reading",
          "entity_type": "reading",
          "primary_keys": ["Item Name"],
          "required_fields": ["Item Name", "Reading Value"],
          "optional_fields": [],
          "validators": {"Reading Value": {"type": "number"}},
          "state_semantics": {
            "Reading Value": {
              "semantic_type": "gauge",
              "applicability": {
                "version": 1,
                "on_missing_context": "reject",
                "exclude_when": [{
                  "code": "inactive",
                  "when": {"field": "Missing Status", "op": "eq", "value": "off"}
                }]
              }
            }
          },
          "evidence": {
            "field_bindings": {
              "Item Name": [{
                "sample_id":"sample-1","line":2,"source_label":"item-a",
                "source_value":"item-a","capture_kind":"column"
              }],
              "Reading Value": [{
                "sample_id":"sample-1","line":2,"source_label":"1",
                "source_value":"1","capture_kind":"column"
              }]
            }
          }
        }""",
    )

    assert "applicability" not in proposal.state_semantics["reading_value"]
    governance = proposal.evidence["semantic_reference_governance"]
    assert governance["removed_applicability"]["reading_value"] == {
        "reason": "undeclared_context_fields",
        "missing_fields": ["missing_status"],
    }


def test_table_evidence_widens_weak_regex_without_domain_branches():
    body = (
        "Name    Description\n"
        "item-a  [rack.1]/primary\n"
        "item-b  --"
    )
    samples = f'<CLI_SAMPLE id="sample-1" sha256="abc">\n{body}\n</CLI_SAMPLE>'
    plan = profile_sample_set([("sample-1", body)])
    proposal = infer_contract(
        command="inspect items",
        samples_text=samples,
        structure_profile=plan,
        llm_call=lambda _prompt: """{
          "category": "item",
          "entity_type": "item",
          "primary_keys": ["name"],
          "required_fields": ["name"],
          "optional_fields": ["description"],
          "validators": {
            "description": {"type": "regex", "pattern": "^[A-Za-z0-9/-]+$"}
          },
          "evidence": {
            "field_bindings": {
              "name": [{
                "sample_id":"sample-1","line":2,"source_label":"item-a",
                "source_value":"item-a","capture_kind":"column"
              }],
              "description": [{
                "sample_id":"sample-1","line":3,"source_label":"--",
                "source_value":"--","capture_kind":"column"
              }]
            }
          }
        }""",
    )

    valid, diagnostics = validate_contract_candidate(
        proposal,
        structure_profile=plan,
        samples_text=samples,
    )

    assert valid, diagnostics
    assert proposal.validators["description"] == {"type": "text"}
    assert proposal.evidence["field_bindings"]["description"][0]["source_value"] == (
        "[rack.1]/primary"
    )
    assert proposal.evidence["table_binding_normalization"][
        "normalized_validator_fields"
    ] == ["description"]


def test_table_evidence_preserves_ip_semantics_when_missing_markers_exist():
    body = "Name    Address\nitem-a  192.0.2.1\nitem-b  --"
    samples = f'<CLI_SAMPLE id="sample-1" sha256="abc">\n{body}\n</CLI_SAMPLE>'
    plan = profile_sample_set([("sample-1", body)])
    proposal = infer_contract(
        command="inspect addresses",
        samples_text=samples,
        structure_profile=plan,
        llm_call=lambda _prompt: """{
          "category": "item",
          "entity_type": "item",
          "primary_keys": ["name"],
          "required_fields": ["name"],
          "optional_fields": ["address"],
          "validators": {
            "address": {"type": "regex", "pattern": "^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$"}
          },
          "evidence": {
            "field_bindings": {
              "name": [{
                "sample_id":"sample-1","line":2,"source_label":"item-a",
                "source_value":"item-a","capture_kind":"column"
              }],
              "address": [{
                "sample_id":"sample-1","line":2,"source_label":"192.0.2.1",
                "source_value":"192.0.2.1","capture_kind":"column"
              }]
            }
          }
        }""",
    )

    assert proposal.validators["address"] == {"type": "ip_or_unassigned"}


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


def test_manual_contract_profile_proposes_stable_interface_identity():
    proposal = infer_contract(
        command="show ip interface brief",
        samples_text="Interface IP-Address OK? Method Status Protocol",
        use_manual_profile=True,
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


def test_semantic_catalog_generalizes_optical_contract_without_vendor_rules():
    proposal = infer_contract(
        command="inspect laser health",
        samples_text="Port 1\nRX power (dBm): -12.4\nTX power (dBm): -3.1",
        use_manual_profile=True,
    )

    assert proposal.entity_type == "interface_optical"
    assert proposal.primary_keys == ("interface",)
    assert proposal.source == "semantic_catalog:interface-optical"


def test_weak_catalog_review_cannot_relabel_optical_records_as_interface_status():
    proposal = infer_contract(
        command="inspect optical health",
        samples_text="Port 1\nRX power (dBm): -12.4\nTX power (dBm): -3.1",
        review_catalog=True,
        llm_call=lambda _prompt: """{
          "category": "interface_l2",
          "entity_type": "interface_l2",
          "primary_keys": ["interface"],
          "required_fields": ["interface", "status"],
          "optional_fields": [],
          "validators": {},
          "evidence": {"sample_ids": ["sample-1"], "line_numbers": [1]}
        }""",
    )

    assert proposal.entity_type == "interface_optical"
    assert proposal.source == "semantic_catalog:interface-optical"
    assert "conflicts with semantic evidence" in proposal.evidence["llm_fallback_reason"]


def test_unknown_contract_normalizes_weak_model_validator_shorthand():
    proposal = infer_contract(
        command="show fabric widget",
        samples_text="Widget Utilization\n1 42",
        llm_call=lambda _prompt: """{
          "category": "fabric_widget",
          "entity_type": "widget",
          "primary_keys": ["widget"],
          "required_fields": ["widget", "utilization"],
          "validators": {"utilization": "numeric"}
        }""",
    )

    assert proposal.validators == {"utilization": {"type": "number"}}


def test_llm_contract_line_evidence_must_resolve_to_the_sanitized_sample():
    samples = (
        '<CLI_SAMPLE id="sample-1" sha256="abc">\n'
        "Widget  State\n1  up\n"
        "</CLI_SAMPLE>"
    )
    proposal = infer_contract(
        command="show fabric widget",
        samples_text=samples,
        llm_call=lambda _prompt: """{
          "category": "fabric_widget",
          "entity_type": "widget",
          "primary_keys": ["widget"],
          "required_fields": ["widget", "state"],
          "validators": {},
          "evidence": {
            "entity_evidence": [
              {"sample_id": "sample-1", "line": 99, "key_text": "1"}
            ]
          }
        }""",
    )

    valid, diagnostics = validate_contract_candidate(
        proposal,
        structure_profile={
            "dominant_layout": "table",
            "samples": [{"estimated_entities": 1}],
        },
        samples_text=samples,
    )

    assert not valid
    assert diagnostics[0].code == "contract_evidence_conflict"
    assert "outside sample" in diagnostics[0].message


def test_llm_contract_rejects_field_semantics_that_conflict_with_source_label():
    samples = (
        '<CLI_SAMPLE id="sample-1" sha256="abc">\n'
        "Port 1\nRX power (dBm): -12.4\n"
        "</CLI_SAMPLE>"
    )
    proposal = infer_contract(
        command="inspect measurement",
        samples_text=samples,
        llm_call=lambda _prompt: """{
          "category": "interface_state",
          "entity_type": "interface",
          "primary_keys": ["interface"],
          "required_fields": ["interface", "status"],
          "optional_fields": [],
          "validators": {},
          "evidence": {
            "field_bindings": {
              "interface": [{
                "sample_id": "sample-1", "line": 1,
                "source_label": "Port", "source_value": "1",
                "capture_kind": "record_anchor"
              }],
              "status": [{
                "sample_id": "sample-1", "line": 2,
                "source_label": "RX power (dBm)", "source_value": "-12.4",
                "capture_kind": "key_value"
              }]
            }
          }
        }""",
    )

    valid, diagnostics = validate_contract_candidate(
        proposal,
        structure_profile={
            "dominant_layout": "repeated_block",
            "samples": [
                {
                    "estimated_entities": 1,
                    "evidence": [{"sample_id": "sample-1"}],
                }
            ],
        },
        samples_text=samples,
    )

    assert not valid
    assert "conflicts with governed label" in diagnostics[0].message


def test_required_field_evidence_must_cover_every_positive_sample():
    samples = (
        '<CLI_SAMPLE id="sample-1" sha256="abc">\nItem  State\n1  up\n</CLI_SAMPLE>\n'
        '<CLI_SAMPLE id="sample-2" sha256="def">\nItem  State\n2  down\n</CLI_SAMPLE>'
    )
    proposal = infer_contract(
        command="inspect items",
        samples_text=samples,
        llm_call=lambda _prompt: """{
          "category": "item",
          "entity_type": "item",
          "primary_keys": ["item"],
          "required_fields": ["item", "state"],
          "optional_fields": [],
          "validators": {},
          "evidence": {
            "field_bindings": {
              "item": [{
                "sample_id": "sample-1", "line": 1,
                "source_label": "Item", "source_value": "Item",
                "capture_kind": "column"
              }],
              "state": [{
                "sample_id": "sample-1", "line": 1,
                "source_label": "State", "source_value": "State",
                "capture_kind": "column"
              }]
            }
          }
        }""",
    )

    valid, diagnostics = validate_contract_candidate(
        proposal,
        structure_profile={
            "dominant_layout": "table",
            "samples": [
                {
                    "estimated_entities": 1,
                    "evidence": [{"sample_id": "sample-1"}],
                },
                {
                    "estimated_entities": 1,
                    "evidence": [{"sample_id": "sample-2"}],
                },
            ],
        },
        samples_text=samples,
    )

    assert not valid
    assert "lacks evidence in samples" in diagnostics[0].message


def test_llm_evidence_line_drift_is_corrected_by_unique_literal_match():
    samples = (
        '<CLI_SAMPLE id="sample-1" sha256="abc">\n'
        "Item  State\n\n1  up\n"
        "</CLI_SAMPLE>"
    )
    proposal = infer_contract(
        command="inspect items",
        samples_text=samples,
        llm_call=lambda _prompt: """{
          "category": "item",
          "entity_type": "item",
          "primary_keys": ["item"],
          "required_fields": ["item", "state"],
          "optional_fields": [],
          "validators": {},
          "evidence": {
            "field_bindings": {
              "item": [{
                "sample_id": "sample-1", "line": 2,
                "source_label": "Item", "source_value": "Item",
                "capture_kind": "column"
              }],
              "state": [{
                "sample_id": "sample-1", "line": 2,
                "source_label": "State", "source_value": "State",
                "capture_kind": "column"
              }]
            }
          }
        }""",
    )

    assert proposal.evidence["field_bindings"]["item"][0]["line"] == 1
    assert proposal.evidence["line_normalization"]["strategy"] == (
        "literal_line_reconciliation"
    )


def test_repeated_block_non_key_requirement_is_reclassified_without_domain_rules():
    samples = (
        '<CLI_SAMPLE id="sample-1" sha256="abc">\n'
        "Item A:\nState: ready\nItem B:\nState: unavailable\n"
        "</CLI_SAMPLE>"
    )
    proposal = infer_contract(
        command="inspect items",
        samples_text=samples,
        structure_profile={
            "dominant_layout": "repeated_block",
            "samples": [
                {
                    "layout_kind": "repeated_block",
                    "estimated_entities": 2,
                    "evidence": [{"sample_id": "sample-1"}],
                }
            ],
        },
        llm_call=lambda _prompt: """{
          "category": "item",
          "entity_type": "item",
          "primary_keys": ["item"],
          "required_fields": ["item", "state"],
          "optional_fields": [],
          "validators": {},
          "evidence": {
            "field_bindings": {
              "item": [{
                "sample_id": "sample-1", "line": 1,
                "source_label": "Item", "source_value": "Item A",
                "capture_kind": "record_anchor"
              }],
              "state": [{
                "sample_id": "sample-1", "line": 2,
                "source_label": "State", "source_value": "ready",
                "capture_kind": "key_value"
              }]
            }
          }
        }""",
    )

    assert proposal.required_fields == ("item",)
    assert proposal.optional_fields == ("state",)
    assert proposal.evidence["capture_normalization"]["reclassified_fields"] == [
        "state"
    ]


def test_contract_validator_rejects_regex_that_excludes_evidenced_key_value():
    samples = (
        '<CLI_SAMPLE id="sample-1" sha256="abc">\n'
        "Item-A details:\nState: ready\n"
        "</CLI_SAMPLE>"
    )
    proposal = infer_contract(
        command="inspect items",
        samples_text=samples,
        llm_call=lambda _prompt: """{
          "category": "item",
          "entity_type": "item",
          "primary_keys": ["item"],
          "required_fields": ["item"],
          "optional_fields": [],
          "validators": {"item": {"type": "regex", "pattern": "^[A-Za-z]+$"}},
          "evidence": {
            "field_bindings": {
              "item": [{
                "sample_id": "sample-1", "line": 1,
                "source_label": "Item-A details:", "source_value": "Item-A",
                "capture_kind": "record_anchor"
              }]
            }
          }
        }""",
    )

    valid, diagnostics = validate_contract_candidate(
        proposal,
        structure_profile={
            "dominant_layout": "repeated_block",
            "samples": [
                {
                    "layout_kind": "repeated_block",
                    "estimated_entities": 2,
                    "evidence": [{"sample_id": "sample-1"}],
                }
            ],
        },
        samples_text=samples,
    )

    assert not valid
    assert "validator rejects evidenced value" in diagnostics[0].message


def test_evidence_line_reconciliation_does_not_confuse_numeric_substrings():
    samples = (
        '<CLI_SAMPLE id="sample-1" sha256="abc">\n'
        "Item A:\nHigh 0.50\nLow -10.50\n"
        "</CLI_SAMPLE>"
    )
    proposal = infer_contract(
        command="inspect items",
        samples_text=samples,
        llm_call=lambda _prompt: """{
          "category": "item",
          "entity_type": "item",
          "primary_keys": ["item"],
          "required_fields": ["item"],
          "optional_fields": ["high_limit"],
          "validators": {"high_limit": {"type": "number"}},
          "evidence": {
            "field_bindings": {
              "item": [{
                "sample_id": "sample-1", "line": 1,
                "source_label": "Item A", "source_value": "A",
                "capture_kind": "record_anchor"
              }],
              "high_limit": [{
                "sample_id": "sample-1", "line": 3,
                "source_label": "0.50", "source_value": "0.50",
                "capture_kind": "column"
              }]
            }
          }
        }""",
    )

    assert proposal.evidence["field_bindings"]["high_limit"][0]["line"] == 2
