from __future__ import annotations

import io

import pytest

from src.core.parser.textfsm_input import (
    TEXTFSM_END_OF_INPUT,
    prepare_textfsm_input,
)

textfsm = pytest.importorskip("textfsm")


def _parse(template: str, raw_text: str) -> list[dict[str, str]]:
    parser = textfsm.TextFSM(io.StringIO(template))
    rows = parser.ParseText(prepare_textfsm_input(template, raw_text))
    return [dict(zip(parser.header, row, strict=False)) for row in rows]


def test_prepare_textfsm_input_removes_only_terminal_cli_padding():
    raw_text = "  aligned   columns  \t\r\nnext\tvalue   \r\n"

    assert prepare_textfsm_input("Value value (.+)\n\nStart\n  ^${value}$$ -> Record\n", raw_text) == (
        "  aligned   columns\r\nnext\tvalue\r\n"
    )


def test_terminal_padding_does_not_break_end_anchored_value_rule():
    template = r"""Value temperature (\S+)
Value voltage (\S+)

Start
  ^\s+${temperature}\s+${voltage}$$ -> Record
"""

    assert _parse(template, "          47         3.26     \n") == [
        {"temperature": "47", "voltage": "3.26"}
    ]


def test_explicit_end_marker_is_appended_after_normalization():
    template = rf"""Value value (\S+)

Start
  ^${{value}}$$
  ^{TEXTFSM_END_OF_INPUT}$$ -> Record EOF
"""

    prepared = prepare_textfsm_input(template, "answer   \r\n")

    assert prepared == f"answer\n{TEXTFSM_END_OF_INPUT}\n"


def test_reserved_end_marker_in_cli_output_is_rejected():
    template = rf"""Value value (\S+)

Start
  ^{TEXTFSM_END_OF_INPUT}$$ -> Record EOF
"""

    with pytest.raises(ValueError, match="reserved TextFSM end marker"):
        prepare_textfsm_input(template, f"value\n{TEXTFSM_END_OF_INPUT}\n")
