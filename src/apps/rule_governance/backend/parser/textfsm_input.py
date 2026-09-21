"""Platform-owned TextFSM input framing helpers."""

from __future__ import annotations

import re

TEXTFSM_END_OF_INPUT = "__NETOPS_TEXTFSM_END_OF_INPUT_7F3A9C__"


def _remove_incidental_trailing_whitespace(value: str) -> str:
    """Remove terminal CLI padding without changing indentation or columns."""

    return re.sub(r"[\t ]+(?=\r?$)", "", value, flags=re.MULTILINE)


def prepare_textfsm_input(template_text: str, cli_output: str) -> str:
    """Append an explicit flush marker only for templates that declare it.

    TextFSM's implicit EOF record is disabled by the platform dialect because it
    can silently emit an incomplete entity. Multi-section entities still need a
    deterministic way to flush the final block after all optional sections have
    been read. A template opts in by matching the reserved marker literally.
    """

    raw = _remove_incidental_trailing_whitespace(str(cli_output or ""))
    if TEXTFSM_END_OF_INPUT not in str(template_text or ""):
        return raw
    if any(line.strip() == TEXTFSM_END_OF_INPUT for line in raw.splitlines()):
        raise ValueError("CLI output contains the reserved TextFSM end marker")
    return f"{raw.rstrip(chr(13) + chr(10))}\n{TEXTFSM_END_OF_INPUT}\n"
