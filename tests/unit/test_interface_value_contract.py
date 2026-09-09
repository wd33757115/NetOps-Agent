import io
from copy import deepcopy
from pathlib import Path

import pytest
import textfsm

from src.core.check_items.adapter_projection_migration import generate_projection_candidate
from src.core.check_items.canonical_rule_context import (
    build_canonical_rule_context,
    generate_canonical_rule_candidates,
)
from src.core.check_items.command_adapter import execute_adapter
from src.core.check_items.interface_values import FIELDS, definition
from src.core.check_items.value_contract import same_name_fields, validate_same_name_record_sets
from src.core.patrol.change_policy import compare_with_policy, policy_from_dict
from src.core.patrol.event_rules import condition_matches

TEMPLATE = Path(
    "src/skills/shared/textfsm-templates/H3C/Comware7/display_interface_values__1.0.0.textfsm"
)


def parse(text):
    parser = textfsm.TextFSM(io.StringIO(TEMPLATE.read_text(encoding="utf-8")))
    return [dict(zip(parser.header, row)) for row in parser.ParseText(text)]


def candidates(enabled=True):
    item = definition()
    contract = deepcopy(item.canonical_contract)
    contract["change_events"] = enabled
    return generate_canonical_rule_candidates(
        build_canonical_rule_context(
            check_item_id=item.id,
            check_item_version_id=item.version_id,
            result_mode="structured",
            assessment_mode="change",
            canonical_contract=contract,
        )
    )


def test_every_field_has_a_change_event_and_switch_off_disables_all():
    result = candidates()
    assert result["success"], result["blockers"]
    assert len(result["change_policies"]) == len(FIELDS)
    assert len(result["event_rules"]) == len(FIELDS)
    assert {r["event_class"] for r in result["event_rules"]} == {"change"}
    assert {r["evaluation_scope"] for r in result["event_rules"]} == {"change"}
    for policy, rule in zip(result["change_policies"], result["event_rules"]):
        field = rule["condition"]["fields"][0]
        numeric = FIELDS[field][0] in {"counter", "gauge"}
        old, new = (10, 11) if numeric else ("UP", "DOWN")
        assert compare_with_policy(policy_from_dict(policy), old=old, new=new)["changed"]
        event = {
            "entity_type": "interface",
            "field": field,
            "observation_type": policy["observation_type"],
            "old": old,
            "new": new,
            "change_type": "modified",
        }
        assert condition_matches(rule["condition"], event)
        assert not condition_matches(rule["condition"], {**event, "new": old})
        assert not condition_matches(rule["condition"], {**event, "change_type": "initial"})
    assert candidates(False)["event_rules"] == []
    assert candidates(False)["change_policies"] == []


def test_strict_values_reject_aliases():
    with pytest.raises(ValueError, match="undeclared"):
        same_name_fields(
            definition().canonical_contract,
            ["interface", "line_status", "protocol_status", "statistics_input_errors"],
        )


def test_shared_command_can_have_separate_same_name_record_sets():
    contracts = [
        {"identity_dimensions": [identity], "observations": [{"canonical_field": field}]}
        for identity, field in [("interface", "line_status"), ("slot", "temperature")]
    ]
    sets = [{"fields": ["interface", "line_status"]}, {"fields": ["slot", "temperature"]}]
    validate_same_name_record_sets(contracts, sets)
    with pytest.raises(ValueError, match="single record set"):
        validate_same_name_record_sets(contracts, [{"fields": ["interface", "slot"]}])
    with pytest.raises(ValueError, match="undeclared"):
        validate_same_name_record_sets(contracts, sets + [{"fields": ["alias"]}])


def test_direct_values_execute_without_ai_or_mapping_and_missing_is_not_zero():
    item = definition()
    parser = textfsm.TextFSM(io.StringIO(TEMPLATE.read_text(encoding="utf-8")))
    result = generate_projection_candidate(
        {
            "selected_check_item": {
                "id": item.id,
                "version_id": item.version_id,
                "canonical_contract": item.canonical_contract,
            },
            "parser": {
                "template_id": "test",
                "source_record_set": "default",
                "source_fields": parser.header,
            },
            "generation_provider": "llm",
        }
    )
    assert result["success"], result
    assert result["generation_provider"] == "same_name"
    rows = parse(
        Path("tests/fixtures/interface_values/huawei_x2e_display_interface.txt").read_text(
            encoding="utf-8"
        )
    )
    batches = execute_adapter(
        result["proposal"],
        record_sets={"default": rows},
        check_items={
            item.id: {"version_id": item.version_id, "canonical_contract": item.canonical_contract}
        },
        device_id="test",
        adapter_version_id="test",
    )
    assert batches[0].quality_status == "complete", batches[0].errors
    assert batches[0].records[0].get("output_errors") in (None, "")


def test_production_parser_runtime_uses_standard_identity_contract():
    import hashlib
    from types import SimpleNamespace

    from src.core.rule_control.command_autopilot_steps import parse_samples

    content = TEMPLATE.read_text(encoding="utf-8")
    template = SimpleNamespace(
        id="test",
        vendor="Huawei",
        model="X2E",
        profile="X2E",
        family="VRP",
        version="test",
        category="interface",
        canonical_command="display interface",
        template_text=content,
        asset_path=str(TEMPLATE.resolve()),
        sha256=hashlib.sha256(content.encode()).hexdigest(),
        validation_json={
            "category": "interface",
            "entity_type": "interface",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "line_status", "protocol_status"],
        },
    )
    result = parse_samples(
        template,
        [
            {
                "id": "test",
                "sanitization_status": "sanitized",
                "sanitized_text": Path(
                    "tests/fixtures/interface_values/huawei_x2e_display_interface.txt"
                ).read_text(encoding="utf-8"),
            }
        ],
    )
    assert len(result[0]["record_sets"]["default"]) == 1


def test_two_records_missing_output_errors_and_full_states():
    rows = parse("""100GE1/0/1 current state : Administratively DOWN
Line protocol current state : DOWN (CFM down)
Description:
Input: 10 packets, 100 bytes
  Total Error: 2
  CRC: 1, Giants: 0
Output: 20 packets, 200 bytes
100GE1/0/2 current state : UP
Line protocol current state : UP
Input: 0 packets, 0 bytes
  Total Error: 0
Output: 0 packets, 0 bytes
""")
    assert len(rows) == 2
    assert rows[0]["line_status"] == "Administratively DOWN"
    assert rows[0]["protocol_status"] == "DOWN (CFM down)"
    assert rows[0]["input_errors"] == "2" and rows[0]["input_crc_errors"] == "1"
    assert rows[0]["output_errors"] == "" and rows[1]["input_crc_errors"] == ""
