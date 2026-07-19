from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "src" / "skills" / "textfsm-generator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from config_loader import CategorySpec  # noqa: E402
from template_providers import (  # noqa: E402
    EvidenceRepeatedBlockProvider,
    GenericTableProvider,
    KeyValueSingletonProvider,
    LegacyBuiltinProvider,
    MultiSectionTableProvider,
    ProviderContext,
    RepeatedBlockProvider,
    SectionedTableProvider,
    TemplateProviderRegistry,
    _prioritize_capture_rules,
    expected_primary_key_tuples_from_tables,
)
from template_validator import validate_template  # noqa: E402

from src.core.parser.parser_ir import ParserRule  # noqa: E402
from src.core.parser.structure_profiler import profile_sample_set  # noqa: E402


@dataclass(frozen=True)
class Sample:
    output: str


def _context(text: str, spec: CategorySpec) -> ProviderContext:
    return ProviderContext(
        command="unknown vendor command",
        samples=(Sample(text),),
        spec=spec,
        structure_profile=profile_sample_set([("sample-1", text)]),
    )


def test_automatic_registry_excludes_manual_command_specific_provider():
    automatic = TemplateProviderRegistry()
    manual = TemplateProviderRegistry(include_manual_builtins=True)

    assert not any(isinstance(provider, LegacyBuiltinProvider) for provider in automatic.providers)
    assert any(isinstance(provider, LegacyBuiltinProvider) for provider in manual.providers)


def test_evidence_rules_prioritize_richer_capture_over_sparse_match():
    sparse = ParserRule(r"^\s*\S+\s+${temperature}\s*$$")
    rich = ParserRule(r"^\s*${temperature}\s+${voltage}\s*$$")

    rules, metadata = _prioritize_capture_rules(
        [sparse, rich],
        [
            (sparse.pattern, ("temperature",)),
            (rich.pattern, ("temperature", "voltage")),
        ],
    )

    assert rules == [rich, sparse]
    assert metadata[0][1] == ("temperature", "voltage")


def test_generic_table_provider_compiles_and_parses_without_command_rules():
    text = """interface  status  speed
Gi1/0/1  up  1000
Gi1/0/2  down  1000
"""
    spec = CategorySpec(
        name="interface",
        required_fields=["interface", "status"],
        optional_fields=["speed"],
        entity_type="interface",
        primary_keys=["interface"],
        validators={},
    )
    candidate = GenericTableProvider().propose(_context(text, spec))
    assert candidate is not None
    result = validate_template(
        candidate.template_text,
        text,
        spec.required_fields,
        primary_keys=spec.primary_keys,
        expected_min_records=2,
    )
    assert result.is_valid, result.errors


def test_generic_table_provider_resolves_semantic_header_aliases():
    text = """Interface           Physical Protocol IP address      VPN instance Description
MGE0/0/0            up       up       10.34.2.145     --           uplink
MGE0/0/1            down     down     --              --           --
"""
    spec = CategorySpec(
        name="interface_l3",
        required_fields=["interface", "status", "protocol"],
        optional_fields=["ip_address", "vpn_instance", "description"],
        entity_type="interface_l3",
        primary_keys=["interface"],
        validators={"ip_address": {"type": "ip_or_unassigned"}},
    )
    candidate = GenericTableProvider().propose(_context(text, spec))
    assert candidate is not None
    result = validate_template(
        candidate.template_text,
        text,
        spec.required_fields,
        spec.validators,
        primary_keys=spec.primary_keys,
        expected_min_records=2,
    )
    assert result.is_valid, result.errors
    assert result.records[0]["status"] == "up"
    assert result.records[0]["ip_address"] == "10.34.2.145"


def test_generic_table_provider_handles_unknown_multivalue_trailing_columns():
    text = """Interface           Physical Protocol IP address/Mask    VPN instance Description
GE2/0/0             up       up       192.0.2.1/30       --           to remote router
Loop0               up       up(s)    192.0.2.2/32       --           --
MGE0                up       up       192.0.2.3/25       MGT
"""
    spec = CategorySpec(
        name="interface",
        required_fields=["interface", "physical", "protocol"],
        optional_fields=["ip_address_mask"],
        entity_type="interface",
        primary_keys=["interface"],
        validators={
            "physical": {"type": "enum", "values": ["up", "down"]},
            "protocol": {"type": "enum", "values": ["up", "down", "up(s)"]},
            "ip_address_mask": {"type": "text"},
        },
    )

    candidate = GenericTableProvider().propose(_context(text, spec))

    assert candidate is not None
    expected_keys = expected_primary_key_tuples_from_tables(text, spec)
    result = validate_template(
        candidate.template_text,
        text,
        spec.required_fields,
        spec.validators,
        primary_keys=spec.primary_keys,
        expected_min_records=3,
        expected_primary_keys=expected_keys,
    )
    assert result.is_valid, result.errors
    assert {record["interface"] for record in result.records} == {
        "GE2/0/0",
        "Loop0",
        "MGE0",
    }


def test_multi_table_provider_scopes_each_row_shape_to_its_header():
    text = """Brief information on interfaces in route mode:
Interface            Link Protocol Primary IP        Description
InLoop0              UP   UP(s)    --
MGE0/0/0             UP   UP       10.34.2.145       uplink

Brief information on interfaces in bridge mode:
Interface            Link Speed     Duplex Type PVID Description
BAGG330              UP   80G(a)    F(a)   T    1
WGE1/0/1             UP   10G       F(a)   A    43   server one
"""
    spec = CategorySpec(
        name="interface_oper",
        required_fields=["interface", "status"],
        optional_fields=[
            "protocol",
            "ip_address",
            "speed",
            "duplex",
            "type",
            "pvid",
            "description",
        ],
        entity_type="interface",
        primary_keys=["interface"],
        validators={
            "protocol": {
                "type": "regex",
                "pattern": r"^UP(\(s\))?$|^DOWN$|^--$",
            },
            "speed": {
                "type": "regex",
                "pattern": r"^\d+G(\(a\))?$|^auto$",
            },
            "description": {"type": "regex", "pattern": r"^.*$"},
            "ip_address": {"type": "ip_or_unassigned"},
        },
    )
    context = ProviderContext(
        command="display interface brief",
        samples=(Sample(text),),
        spec=spec,
        structure_profile=profile_sample_set([("sample-1", text)]),
    )
    candidate = MultiSectionTableProvider().propose(context)
    assert candidate is not None
    expected_keys = expected_primary_key_tuples_from_tables(text, spec)
    result = validate_template(
        candidate.template_text,
        text,
        spec.required_fields,
        spec.validators,
        primary_keys=spec.primary_keys,
        expected_min_records=4,
        expected_primary_keys=expected_keys,
    )
    assert result.is_valid, result.errors
    assert expected_keys == {
        ("InLoop0",),
        ("MGE0/0/0",),
        ("BAGG330",),
        ("WGE1/0/1",),
    }
    assert {record["interface"] for record in result.records} == {
        "InLoop0",
        "MGE0/0/0",
        "BAGG330",
        "WGE1/0/1",
    }


def test_generic_key_value_provider_compiles_and_parses_singleton():
    text = """model: S1
os_version: 7.1
status: normal
"""
    spec = CategorySpec(
        name="device",
        required_fields=["model", "os_version"],
        optional_fields=["status"],
        entity_type="device",
        primary_keys=[],
        validators={},
    )
    candidate = KeyValueSingletonProvider().propose(_context(text, spec))
    assert candidate is not None
    result = validate_template(candidate.template_text, text, spec.required_fields)
    assert result.is_valid, result.errors
    assert result.record_count == 1


def test_generic_repeated_block_provider_compiles_and_parses_entities():
    text = """Item A:
state: up
speed: 1000
Item B:
state: down
speed: 100
"""
    spec = CategorySpec(
        name="item",
        required_fields=["item", "state"],
        optional_fields=["speed"],
        entity_type="item",
        primary_keys=["item"],
        validators={},
    )
    candidate = RepeatedBlockProvider().propose(_context(text, spec))
    assert candidate is not None
    result = validate_template(
        candidate.template_text,
        text,
        spec.required_fields,
        primary_keys=spec.primary_keys,
        expected_min_records=2,
    )
    assert result.is_valid, result.errors


def test_evidence_ir_compiler_owns_repeated_block_record_lifecycle():
    text = """Item A details:
Metric: 10
Item B details:
Unavailable
"""
    spec = CategorySpec(
        name="item",
        required_fields=["item"],
        optional_fields=["metric"],
        entity_type="item",
        primary_keys=["item"],
        validators={
            "item": {"type": "regex", "pattern": "^[A-Z]$"},
            "metric": {"type": "number"},
        },
    )
    context = ProviderContext(
        command="unknown vendor command",
        samples=(Sample(text),),
        spec=spec,
        structure_profile={"dominant_layout": "repeated_block"},
        field_bindings={
            "item": [
                {
                    "sample_id": "sample-1",
                    "line": 1,
                    "source_value": "A",
                    "capture_kind": "record_anchor",
                },
                {
                    "sample_id": "sample-1",
                    "line": 3,
                    "source_value": "B",
                    "capture_kind": "record_anchor",
                },
            ],
            "metric": [
                {
                    "sample_id": "sample-1",
                    "line": 2,
                    "source_value": "10",
                    "capture_kind": "key_value",
                }
            ],
        },
        evidence_samples={"sample-1": text},
    )

    candidate = EvidenceRepeatedBlockProvider().propose(context)

    assert candidate is not None
    result = validate_template(
        candidate.template_text,
        text,
        spec.required_fields,
        spec.validators,
        primary_keys=spec.primary_keys,
        expected_min_records=2,
        expected_record_count=2,
    )
    assert result.is_valid, result.errors
    assert result.records == [
        {"item": "A", "metric": "10"},
        {"item": "B", "metric": ""},
    ]


def test_evidence_ir_compiler_records_final_heterogeneous_sibling_block():
    text = """Slot 1 CPU 0:
DEVICE_NAME          : Switch
SERIAL NUMBER        : ABC123
Fan 1:
DEVICE_NAME          : Fan
SERIAL NUMBER        : NONE
Power 1:
DEVICE_NAME          : Power
MANU SERIAL NUMBER   : PSU123
"""
    spec = CategorySpec(
        name="inventory",
        required_fields=["module_name"],
        optional_fields=["device_name", "serial_number"],
        entity_type="module",
        primary_keys=["module_name"],
        validators={
            "module_name": {"type": "regex", "pattern": r"^[A-Za-z0-9 /]+:$"},
            "device_name": {"type": "regex", "pattern": r"^[A-Za-z0-9-]+$"},
            "serial_number": {"type": "regex", "pattern": r"^[A-Za-z0-9]+$|^NONE$"},
        },
    )
    context = ProviderContext(
        command="unknown vendor command",
        samples=(Sample(text),),
        spec=spec,
        structure_profile={"dominant_layout": "repeated_block"},
        field_bindings={
            "module_name": [
                {
                    "sample_id": "sample-1",
                    "line": 1,
                    "source_value": "Slot 1 CPU 0:",
                    "capture_kind": "record_anchor",
                },
                {
                    "sample_id": "sample-1",
                    "line": 4,
                    "source_value": "Fan 1:",
                    "capture_kind": "record_anchor",
                },
                {
                    "sample_id": "sample-1",
                    "line": 7,
                    "source_value": "Power 1:",
                    "capture_kind": "record_anchor",
                },
            ],
            "device_name": [
                {
                    "sample_id": "sample-1",
                    "line": 2,
                    "source_value": "Switch",
                    "capture_kind": "key_value",
                },
                {
                    "sample_id": "sample-1",
                    "line": 5,
                    "source_value": "Fan",
                    "capture_kind": "key_value",
                },
                {
                    "sample_id": "sample-1",
                    "line": 8,
                    "source_value": "Power",
                    "capture_kind": "key_value",
                },
            ],
            "serial_number": [
                {
                    "sample_id": "sample-1",
                    "line": 3,
                    "source_value": "ABC123",
                    "capture_kind": "key_value",
                },
                {
                    "sample_id": "sample-1",
                    "line": 6,
                    "source_value": "NONE",
                    "capture_kind": "key_value",
                },
                {
                    "sample_id": "sample-1",
                    "line": 9,
                    "source_value": "PSU123",
                    "capture_kind": "key_value",
                },
            ],
        },
        evidence_samples={"sample-1": text},
    )

    candidate = EvidenceRepeatedBlockProvider().propose(context)

    assert candidate is not None
    result = validate_template(
        candidate.template_text,
        text,
        spec.required_fields,
        spec.validators,
        primary_keys=spec.primary_keys,
        expected_min_records=3,
        expected_record_count=3,
    )
    assert result.is_valid, result.errors
    assert [record["module_name"] for record in result.records] == [
        "Slot 1 CPU 0:",
        "Fan 1:",
        "Power 1:",
    ]
    assert result.records[-1]["serial_number"] == "PSU123"


def test_evidence_ir_compiler_preserves_repeated_child_values_as_lists():
    text = """Item A details:
Values
1 10 20
2 11 20
End
Item B details:
Values
1 12 20
End
"""
    spec = CategorySpec(
        name="item",
        required_fields=["item"],
        optional_fields=["current", "limit"],
        entity_type="item",
        primary_keys=["item"],
        validators={
            "item": {"type": "regex", "pattern": "^[A-Z]$"},
            "current": {"type": "number"},
            "limit": {"type": "number"},
        },
    )
    context = ProviderContext(
        command="unknown vendor command",
        samples=(Sample(text),),
        spec=spec,
        structure_profile={"dominant_layout": "repeated_block"},
        field_bindings={
            "item": [
                {
                    "sample_id": "sample-1",
                    "line": 1,
                    "source_value": "A",
                    "capture_kind": "record_anchor",
                },
                {
                    "sample_id": "sample-1",
                    "line": 6,
                    "source_value": "B",
                    "capture_kind": "record_anchor",
                },
            ],
            "current": [
                {
                    "sample_id": "sample-1",
                    "line": 3,
                    "source_value": "10",
                    "capture_kind": "column",
                },
                {
                    "sample_id": "sample-1",
                    "line": 4,
                    "source_value": "11",
                    "capture_kind": "column",
                },
            ],
            "limit": [
                {
                    "sample_id": "sample-1",
                    "line": 3,
                    "source_value": "20",
                    "capture_kind": "column",
                },
                {
                    "sample_id": "sample-1",
                    "line": 4,
                    "source_value": "20",
                    "capture_kind": "column",
                },
            ],
        },
        evidence_samples={"sample-1": text},
    )

    candidate = EvidenceRepeatedBlockProvider().propose(context)

    assert candidate is not None
    assert "Value List current" in candidate.template_text
    records = validate_template(
        candidate.template_text,
        text,
        spec.required_fields,
        spec.validators,
        primary_keys=spec.primary_keys,
        expected_min_records=2,
        expected_record_count=2,
    ).records
    assert records[0]["current"] == ["10", "11"]
    assert records[1]["current"] == ["12"]


def test_evidence_ir_scopes_identical_rows_by_nearest_section_heading():
    text = """Port A diagnostics:
Current values:
RX
-5
Alarm thresholds:
RX
Low -18
Warning thresholds:
RX
Low -15
Port B diagnostics:
Unavailable
"""
    spec = CategorySpec(
        name="sensor",
        required_fields=["port"],
        optional_fields=["current_rx", "alarm_low_rx", "warning_low_rx"],
        entity_type="sensor",
        primary_keys=["port"],
        validators={
            "port": {"type": "regex", "pattern": "^[A-Z]$"},
            "current_rx": {"type": "number"},
            "alarm_low_rx": {"type": "number"},
            "warning_low_rx": {"type": "number"},
        },
    )
    context = ProviderContext(
        command="unknown vendor command",
        samples=(Sample(text),),
        spec=spec,
        structure_profile={"dominant_layout": "repeated_block"},
        field_bindings={
            "port": [
                {
                    "sample_id": "sample-1",
                    "line": 1,
                    "source_value": "A",
                    "capture_kind": "record_anchor",
                },
                {
                    "sample_id": "sample-1",
                    "line": 11,
                    "source_value": "B",
                    "capture_kind": "record_anchor",
                },
            ],
            "current_rx": [
                {"sample_id": "sample-1", "line": 4, "source_value": "-5", "capture_kind": "column"}
            ],
            "alarm_low_rx": [
                {
                    "sample_id": "sample-1",
                    "line": 7,
                    "source_value": "-18",
                    "capture_kind": "column",
                }
            ],
            "warning_low_rx": [
                {
                    "sample_id": "sample-1",
                    "line": 10,
                    "source_value": "-15",
                    "capture_kind": "column",
                }
            ],
        },
        evidence_samples={"sample-1": text},
    )

    candidate = EvidenceRepeatedBlockProvider().propose(context)

    assert candidate is not None
    assert "Context1" in candidate.template_text
    result = validate_template(
        candidate.template_text,
        text,
        spec.required_fields,
        spec.validators,
        primary_keys=spec.primary_keys,
        expected_min_records=2,
        expected_record_count=2,
    )
    assert result.is_valid, result.errors
    assert result.records == [
        {
            "port": "A",
            "current_rx": "-5",
            "alarm_low_rx": "-18",
            "warning_low_rx": "-15",
        },
        {
            "port": "B",
            "current_rx": "",
            "alarm_low_rx": "",
            "warning_low_rx": "",
        },
    ]


def test_evidence_ir_compiles_redacted_placeholder_as_runtime_capture():
    prompt_text = """Item A:
SERIAL: <SERIAL>
Item B:
SERIAL: <SERIAL>
"""
    runtime_text = """Item A:
SERIAL: ABC123
Item B:
SERIAL: XYZ789
"""
    spec = CategorySpec(
        name="component",
        required_fields=["item"],
        optional_fields=["serial_number"],
        entity_type="component",
        primary_keys=["item"],
        validators={
            "item": {"type": "regex", "pattern": r"^Item [A-Z]:$"},
            "serial_number": {
                "type": "regex",
                "pattern": r"^[A-Za-z0-9._/-]+$",
            },
        },
    )
    context = ProviderContext(
        command="inspect component",
        samples=(Sample(runtime_text),),
        spec=spec,
        structure_profile={"dominant_layout": "repeated_block"},
        field_bindings={
            "item": [
                {
                    "sample_id": "sample-1",
                    "line": 1,
                    "source_value": "Item A:",
                    "capture_kind": "record_anchor",
                },
                {
                    "sample_id": "sample-1",
                    "line": 3,
                    "source_value": "Item B:",
                    "capture_kind": "record_anchor",
                },
            ],
            "serial_number": [
                {
                    "sample_id": "sample-1",
                    "line": 2,
                    "source_value": "<SERIAL>",
                    "capture_kind": "key_value",
                },
                {
                    "sample_id": "sample-1",
                    "line": 4,
                    "source_value": "<SERIAL>",
                    "capture_kind": "key_value",
                },
            ],
        },
        evidence_samples={"sample-1": prompt_text},
    )

    candidate = EvidenceRepeatedBlockProvider().propose(context)

    assert candidate is not None
    assert "${serial_number}" in candidate.template_text
    result = validate_template(
        candidate.template_text,
        runtime_text,
        spec.required_fields,
        spec.validators,
        primary_keys=spec.primary_keys,
        expected_min_records=2,
        expected_record_count=2,
    )
    assert result.is_valid, result.errors
    assert [record["serial_number"] for record in result.records] == [
        "ABC123",
        "XYZ789",
    ]


def test_generic_sectioned_table_provider_compiles_and_parses_children():
    text = """slot 1:
fan  status
1  normal
2  normal
slot 2:
fan  status
1  failed
2  normal
"""
    spec = CategorySpec(
        name="fan",
        required_fields=["slot", "fan", "status"],
        optional_fields=[],
        entity_type="fan",
        primary_keys=["slot", "fan"],
        validators={},
    )
    candidate = SectionedTableProvider().propose(_context(text, spec))
    assert candidate is not None
    result = validate_template(
        candidate.template_text,
        text,
        spec.required_fields,
        primary_keys=spec.primary_keys,
        expected_min_records=4,
    )
    assert result.is_valid, result.errors
