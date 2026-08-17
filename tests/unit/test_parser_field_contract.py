from __future__ import annotations

from types import SimpleNamespace

from src.core.check_items.runtime_bundle_assembly import (
    contract_source_field_blockers,
)
from src.core.parser.field_contract import build_parser_field_contract
from src.core.parser.parser_bundle import build_parser_bundle


def _spec(fields: dict[str, dict]) -> dict:
    return {
        "parser_spec": {
            "entity": "interface",
            "primary_keys": ["interface"],
            "fields": fields,
            "allow_empty": False,
            "empty_patterns": [],
        }
    }


def test_single_template_field_contract_requires_exact_parser_spec_parity():
    template = r"""Value interface (\S+)
Value state (\S+)

Start
  ^${interface}\s+${state} -> Record
"""
    aligned = build_parser_field_contract(
        validation=_spec(
            {
                "interface": {"required": True, "type": "text"},
                "state": {"required": True, "type": "text"},
            }
        ),
        template_text=template,
    )
    drifted = build_parser_field_contract(
        validation=_spec(
            {
                "interface": {"required": True, "type": "text"},
                "state": {"required": True, "type": "text"},
                "description": {"required": False, "type": "text"},
            }
        ),
        template_text=template,
    )

    assert aligned["status"] == "aligned"
    assert aligned["template_record_sets"] == {
        "default": ["interface", "state"]
    }
    assert drifted["status"] == "schema_drift"
    assert drifted["differences"][0]["missing_in_template"] == [
        "description"
    ]


def test_parser_bundle_field_contract_preserves_record_set_grain():
    template = build_parser_bundle(
        {
            "measurements": r"""Value interface (\S+)
Value rx_power (\S+)

Start
  ^${interface}\s+${rx_power} -> Record
""",
            "thresholds": r"""Value interface (\S+)
Value rx_low (\S+)

Start
  ^${interface}\s+${rx_low} -> Record
""",
        }
    ).serialize()
    validation = {
        "source_parser_spec": {
            "version": 2,
            "record_sets": [
                {
                    "name": "measurements",
                    "primary_keys": ["interface"],
                    "fields": {
                        "interface": {"required": True},
                        "rx_power": {"required": True},
                    },
                },
                {
                    "name": "thresholds",
                    "primary_keys": ["interface"],
                    "fields": {
                        "interface": {"required": True},
                        "rx_low": {"required": True},
                    },
                },
            ],
        },
        "record_sets": [
            {"name": "measurements"},
            {"name": "thresholds"},
        ],
    }

    contract = build_parser_field_contract(
        validation=validation,
        template_text=template,
    )

    assert contract["status"] == "aligned"
    assert set(contract["template_record_sets"]) == {
        "measurements",
        "thresholds",
    }


def test_contract_source_fields_and_cross_record_thresholds_must_exist():
    field_contract = {
        "status": "aligned",
        "template_record_sets": {
            "measurements": ["interface", "rx_power"],
            "thresholds": ["interface", "rx_low"],
        },
    }
    contract = SimpleNamespace(
        record_sets_json=[
            {
                "name": "measurements",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "rx_power"],
                "optional_fields": [],
                "validators": {},
                "ignore_fields": [],
                "entity_dimensions": {
                    "interface": {"field": "interface"}
                },
                "state_semantics": {
                    "rx_power": {
                        "observation_type": "interface.optical.rx_power",
                        "threshold_references": {
                            "critical_low": {
                                "record_set_name": "thresholds",
                                "field_name": "rx_low_missing",
                                "join_keys": {"interface": "interface"},
                            }
                        },
                    }
                },
            },
            {
                "name": "thresholds",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "rx_low"],
                "optional_fields": [],
                "validators": {},
                "ignore_fields": [],
                "entity_dimensions": {},
                "state_semantics": {
                    "rx_low": {
                        "comparison_role": "threshold",
                        "target_field": "rx_power",
                    }
                },
            },
        ],
        record_set_relations_json=[
            {
                "source_record_set": "measurements",
                "target_record_set": "thresholds",
                "join_keys": {"interface": "interface"},
                "threshold_bindings": {
                    "rx_power": {"critical_low": "rx_low"}
                },
            }
        ],
        primary_keys_json=[],
        required_fields_json=[],
        optional_fields_json=[],
        validators_json={},
        ignore_fields_json=[],
        entity_dimensions_json={},
        state_semantics_json={},
    )

    blockers = contract_source_field_blockers(
        contract,
        parser_field_contract=field_contract,
    )

    assert any(
        item["code"] == "observation_contract_source_field_missing"
        and item["record_set"] == "thresholds"
        and item["fields"] == ["rx_low_missing"]
        for item in blockers
    )
