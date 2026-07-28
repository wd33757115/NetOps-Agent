from __future__ import annotations

from src.core.parser.structure_profiler import profile_cli_structure
from src.core.parser.textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    build_textfsm_system_prompt,
    dialect_hash,
    lint_textfsm_template,
)


def test_dialect_prompt_is_version_bound_and_hashed():
    prompt = build_textfsm_system_prompt()
    assert "textfsm==2.1.0" in prompt
    assert "Continue.Record" in prompt
    assert "StateName.Record" in prompt
    assert "Generic multiline-record recipe" in prompt
    assert "Never write `Record Continue`" in prompt
    assert dialect_hash() in prompt


def test_allowed_dialect_compiles_with_real_runtime():
    template = """Value Filldown HOST (\\S+)
Value INTERFACE (\\S+)
Value STATUS (\\S+)

Start
  ^Host: ${HOST}
  ^BEGIN -> Detail

Detail
  ^${INTERFACE}\\s+${STATUS}$$ -> Continue.Record
  ^END -> Record Start

EOF
"""
    assert lint_textfsm_template(template) == ()


def test_required_is_rejected_to_preserve_incomplete_entity_diagnostics():
    template = """Value Required ITEM (\\S+)

Start
  ^${ITEM} -> Record
"""
    violations = lint_textfsm_template(template)
    assert any(item.code == "unsupported_platform_construct" for item in violations)


def test_compiler_list_values_are_supported_by_platform_dialect():
    template = """Value ITEM (\\S+)
Value List METRIC (-?\\d+(?:\\.\\d+)?)

Start
  ^${ITEM}\\s+${METRIC} -> Record
"""
    assert lint_textfsm_template(template) == ()


def test_hallucinated_actions_are_rejected_with_stable_codes():
    templates = (
        "Value X (\\S+)\n\nStart\n  ^${X} -> Set X value\n",
        "Value X (\\S+)\n\nStart\n  ^${X} -> Other.Record\n",
        "Value X (\\S+)\n\nStart\n  ^${X} -> Continue Other\n",
    )
    for template in templates:
        violations = lint_textfsm_template(template)
        assert any(item.code == "unsupported_platform_construct" for item in violations)


def test_filldown_without_explicit_eof_is_rejected():
    template = """Value Filldown X (\\S+)

Start
  ^${X} -> Record
"""
    assert any(item.code == "implicit_eof_record" for item in lint_textfsm_template(template))


def test_structure_profiler_is_vendor_neutral():
    table = """Port  Status  Speed
Gi1/0/1  up  1000
Gi1/0/2  down  1000
"""
    block = """Item A:
  State: up
Item B:
  State: down
"""
    assert profile_cli_structure(table).layout_kind == "table"
    assert profile_cli_structure(block).layout_kind == "repeated_block"
    assert DEFAULT_TEXTFSM_DIALECT.runtime_version == "2.1.0"
