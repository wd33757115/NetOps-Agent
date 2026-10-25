"""Compile literal CLI evidence and typed captures into TextFSM row patterns."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceCapture:
    literal: str
    pattern: str
    field: str | None = None


def _literal_regex(value: str, *, generalize_numbers: bool = False) -> str:
    parts = re.split(r"(\s+)", str(value or ""))
    return "".join(
        r"\s+"
        if part.isspace()
        else r"[-+]?\d+(?:\.\d+)?"
        if generalize_numbers and re.fullmatch(r"[-+]?\d+(?:\.\d+)?", part)
        else re.escape(part)
        for part in parts
    )


def compile_evidence_line(
    line: str,
    captures: tuple[EvidenceCapture, ...] = (),
    *,
    generalize_unbound_numbers: bool = False,
) -> str:
    """Compile literal evidence plus typed captures into one anchored rule."""

    source = str(line or "").strip()
    occupied: list[tuple[int, int]] = []
    spans: list[tuple[int, int, EvidenceCapture]] = []
    cursor = 0
    for capture in captures:
        literal = str(capture.literal or "")
        if not literal:
            raise ValueError("evidence capture has no literal value")
        candidates = [match.span() for match in re.finditer(re.escape(literal), source)]
        span = next(
            (
                candidate
                for candidate in candidates
                if candidate[0] >= cursor
                and not any(
                    candidate[0] < end and candidate[1] > start
                    for start, end in occupied
                )
            ),
            None,
        )
        if span is None:
            span = next(
                (
                    candidate
                    for candidate in candidates
                    if not any(
                        candidate[0] < end and candidate[1] > start
                        for start, end in occupied
                    )
                ),
                None,
            )
        if span is None:
            raise ValueError(f"evidence value {literal!r} is absent from line")
        occupied.append(span)
        spans.append((span[0], span[1], capture))
        cursor = span[1]

    output: list[str] = [r"^\s*"]
    cursor = 0
    for start, end, capture in sorted(spans, key=lambda item: item[0]):
        output.append(
            _literal_regex(
                source[cursor:start],
                generalize_numbers=generalize_unbound_numbers,
            )
        )
        output.append(
            f"${{{capture.field}}}"
            if capture.field
            else f"(?:{capture.pattern})"
        )
        cursor = end
    output.append(
        _literal_regex(
            source[cursor:],
            generalize_numbers=generalize_unbound_numbers,
        )
    )
    output.append(r"\s*$$")
    return "".join(output)
