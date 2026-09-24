from __future__ import annotations

from types import SimpleNamespace

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
