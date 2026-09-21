"""Shared, evidence-bound TextFSM compiler helpers."""

from __future__ import annotations

import re

from src.apps.rule_governance.backend.parser.value_types import IP_UNASSIGNED_TOKENS

from ..spec import FieldSpec, ParserSpec


def normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().casefold())


def field_label_map(spec: ParserSpec) -> dict[str, str]:
    output: dict[str, str] = {}
    for field in spec.fields:
        for label in (field.name, field.name.replace("_", " "), *field.source_labels):
            normalized = normalize_label(label)
            if normalized:
                output.setdefault(normalized, field.name)
    return output


def value_pattern(field: FieldSpec) -> str:
    unavailable_pattern = "|".join(
        re.escape(item).replace(r"\ ", r"\s+")
        for item in sorted(
            field.unavailable_tokens,
            key=lambda item: (-len(item), item),
        )
    )

    def with_unavailable(pattern: str) -> str:
        if not unavailable_pattern:
            return pattern
        return rf"(?:{pattern}|(?i:{unavailable_pattern}))"

    if field.value_type == "enum":
        values = [re.escape(value).replace(r"\ ", r"\s+") for value in field.values]
        return "(?i:" + "|".join(values) + ")"
    if field.value_type == "number":
        return with_unavailable(r"[-+]?\d+(?:\.\d+)?")
    if field.value_type == "percent":
        return with_unavailable(r"[-+]?\d+(?:\.\d+)?%?")
    if field.value_type == "ip":
        return r"(?:\d{1,3}\.){3}\d{1,3}"
    if field.value_type == "ip_or_unassigned":
        empty_tokens = "|".join(
            re.escape(item)
            for item in sorted(
                IP_UNASSIGNED_TOKENS,
                key=lambda item: (-len(item), item),
            )
        )
        return rf"(?:(?:\d{{1,3}}\.){{3}}\d{{1,3}}|(?i:{empty_tokens}))"
    if field.value_type == "regex":
        pattern = str(field.pattern or "").strip()
        pattern = re.sub(r"^\^", "", pattern)
        pattern = re.sub(r"(?<!\\)\$$", "", pattern)
        return f"(?:{pattern})"
    if field.value_type == "text":
        # Text values may sit beside other text columns. A greedy capture lets an
        # early field consume later columns while still producing a superficially
        # valid record count. Lazy matching preserves the next column boundary and
        # still expands to the full value when the field is the last capture.
        return r"\S(?:.*?\S)?"
    return r"\S+"


def value_declarations(
    spec: ParserSpec,
    field_names: list[str],
    *,
    filldown_fields: frozenset[str] = frozenset(),
) -> str:
    field_map = spec.field_map
    return "\n".join(
        (
            f"Value{' Filldown' if name in filldown_fields else ''} "
            f"{name} ({value_pattern(field_map[name])})"
        )
        for name in field_names
    )


def literal_line_pattern(line: str) -> str:
    tokens = re.findall(r"\S+", line.strip())
    return r"^\s*" + r"\s+".join(re.escape(token) for token in tokens) + r"\s*$$"


def render_row_pattern(
    mapped: list[str | None],
    spec: ParserSpec,
    *,
    required_fields: tuple[str, ...] | None = None,
    optional_trailing_field: bool = False,
) -> str | None:
    captured = [field for field in mapped if field]
    required = set(required_fields if required_fields is not None else spec.required_fields)
    if len(captured) < 1 or not required.issubset(captured):
        return None
    parts: list[str] = []
    for index, field in enumerate(mapped):
        if field:
            value = f"${{{field}}}"
        elif index == len(mapped) - 1:
            value = r"\S(?:.*\S)?"
        else:
            value = r"\S+"
        if index == 0:
            parts.append(value)
        elif index == len(mapped) - 1 and (
            field is None or optional_trailing_field
        ):
            parts.append(r"(?:\s+" + value + r")?")
        else:
            parts.append(r"\s+" + value)
    return r"  ^\s*" + "".join(parts) + r"\s*$$ -> Record"
