"""Shared lexical checks for evidence-bound parser generation."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[-+]?\d+(?:\.\d+)?|[\w]+(?:[-/][\w]+)*|[^\s]", re.UNICODE)


def evidence_tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(str(value or "").casefold()))


def literal_occurs(line: str, literal: str) -> bool:
    """Match a literal token sequence without numeric substring collisions."""

    source = evidence_tokens(line)
    expected = evidence_tokens(literal)
    if not source or not expected or len(expected) > len(source):
        return False
    width = len(expected)
    return any(
        source[index : index + width] == expected
        for index in range(len(source) - width + 1)
    )
