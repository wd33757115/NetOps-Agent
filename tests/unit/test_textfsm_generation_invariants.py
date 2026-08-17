from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.core.parser.contract_inference import infer_contract
from src.core.parser.structure_profiler import profile_cli_structure

ROOT = Path(__file__).resolve().parents[2]
AUTOMATIC_GENERATION_FILES = (
    ROOT / "src/core/parser_generation/service.py",
    ROOT / "src/core/parser_generation/validation.py",
    ROOT / "src/core/parser_generation/compilers/table.py",
    ROOT / "src/core/parser_generation/compilers/key_value.py",
    ROOT / "src/core/parser_generation/compilers/repeated_block.py",
    ROOT / "src/skills/textfsm-generator/scripts/generate_templates.py",
    ROOT / "src/skills/textfsm-generator/scripts/parser_model.py",
)


def _names(node: ast.AST) -> set[str]:
    return {
        part.id.casefold()
        for part in ast.walk(node)
        if isinstance(part, ast.Name)
    } | {
        part.attr.casefold()
        for part in ast.walk(node)
        if isinstance(part, ast.Attribute)
    }


def test_automatic_generation_has_no_literal_command_or_entity_branches():
    violations: list[str] = []
    for path in AUTOMATIC_GENERATION_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.IfExp, ast.Match)):
                continue
            condition = node.test if isinstance(node, (ast.If, ast.IfExp)) else node.subject
            names = _names(condition)
            if not names.intersection(
                {
                    "command",
                    "command_canonical",
                    "entity_type",
                    "vendor",
                    "model",
                    "profile",
                }
            ):
                continue
            literals = [
                item.value
                for item in ast.walk(condition)
                if isinstance(item, ast.Constant)
                and isinstance(item.value, str)
                and item.value.strip()
                and item.value
                not in {
                    "command",
                    "command_canonical",
                    "entity_type",
                    "vendor",
                    "model",
                    "profile",
                }
            ]
            if literals:
                violations.append(f"{path.name}:{node.lineno}:{literals}")

    assert violations == []


def test_automatic_generation_has_no_vendor_command_or_metric_literals():
    forbidden = {
        "h3c",
        "huawei",
        "cisco",
        "display interface",
        "show interface",
        "transceiver",
        "optical",
        "rx_power",
        "tx_power",
    }
    violations: list[str] = []
    for path in AUTOMATIC_GENERATION_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            text = node.value.casefold()
            hits = sorted(value for value in forbidden if value in text)
            if hits:
                violations.append(f"{path.name}:{node.lineno}:{hits}")

    assert violations == []


def test_automatic_contract_path_does_not_short_circuit_to_manual_profile():
    calls = 0

    def model(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return """{
          "category": "measurement",
          "entity_type": "port_measurement",
          "primary_keys": ["port"],
          "required_fields": ["port", "receive_level"],
          "optional_fields": [],
          "validators": {"receive_level": {"type": "number"}},
          "evidence": {
            "field_bindings": {
              "port": [{
                "sample_id": "sample-1", "line": 1,
                "source_label": "Port", "source_value": "1",
                "capture_kind": "record_anchor"
              }],
              "receive_level": [{
                "sample_id": "sample-1", "line": 2,
                "source_label": "Receive level", "source_value": "-12.4",
                "capture_kind": "key_value"
              }]
            }
          }
        }"""

    proposal = infer_contract(
        command="inspect optical health",
        samples_text=(
            '<CLI_SAMPLE id="sample-1" sha256="abc">\n'
            "Port 1\nReceive level: -12.4\n"
            "</CLI_SAMPLE>"
        ),
        llm_call=model,
    )

    assert calls == 1
    assert proposal.source == "llm_proposal"
    assert proposal.required_fields == ("port", "receive_level")


def test_manual_contract_profile_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="requires an LLM callback"):
        infer_contract(
            command="inspect optical health",
            samples_text="Port 1\nReceive level: -12.4",
        )


def test_structure_profiler_counts_all_regions_without_schema_rules():
    sample = """Group alpha:
Item  State
A1    up
A2    down

Group beta:
Node  Health  Value
B1    ok      10
B2    ok      20
B3    bad     30
"""

    plan = profile_cli_structure(sample, sample_id="sample-1")

    assert plan.layout_kind == "multi_table"
    assert plan.estimated_entities == 5
    assert [region.row_count for region in plan.table_regions] == [2, 3]


def test_structure_profiler_prefers_repeated_entities_over_nested_measurements():
    sample = """Port A:
Metric  Current  Limit
rx      -10      -17
tx      -3       -8

Port B:
Metric  Current  Limit
rx      -11      -17
tx      -4       -8

Port C:
State: absent
"""

    plan = profile_cli_structure(sample, sample_id="sample-1")

    assert plan.layout_kind == "repeated_block"
    assert plan.estimated_entities == 3
