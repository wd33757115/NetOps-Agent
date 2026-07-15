"""Command-neutral providers plus an explicitly opt-in manual fallback."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol

from config_loader import CategorySpec

from src.core.parser.parser_ir import (
    EvidenceCapture,
    ParserIR,
    ParserRule,
    ParserState,
    ParserValue,
    compile_evidence_line,
    compile_textfsm,
)
from src.core.parser.sanitization import runtime_pattern_for_redacted_value
from src.core.parser.semantic_catalog import field_aliases


class HasOutput(Protocol):
    output: str


@dataclass(frozen=True)
class ProviderContext:
    command: str
    samples: tuple[HasOutput, ...]
    spec: CategorySpec
    structure_profile: dict
    field_bindings: dict[str, Any] | None = None
    evidence_samples: dict[str, str] | None = None


@dataclass(frozen=True)
class ProviderCandidate:
    template_text: str
    provider: str
    strategy: str


def _prioritize_capture_rules(
    rules: list[ParserRule],
    metadata: list[tuple[str, tuple[str, ...]]],
) -> tuple[list[ParserRule], list[tuple[str, tuple[str, ...]]]]:
    prioritized = sorted(
        zip(rules, metadata, strict=True),
        key=lambda pair: (-len(pair[1][1]), -len(pair[0].pattern)),
    )
    return (
        [pair[0] for pair in prioritized],
        [pair[1] for pair in prioritized],
    )


class TemplateProvider(Protocol):
    name: str

    def propose(self, context: ProviderContext) -> ProviderCandidate | None: ...


def _normalize_label(value: str) -> str:
    text = re.sub(r"\([^)]*\)", "", str(value).lower())
    text = re.sub(r"\b(?:dbm|percent|percentage)\b", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _binding_labels(
    field: str,
    field_bindings: dict[str, Any] | None,
) -> tuple[str, ...]:
    raw = dict(field_bindings or {}).get(field) or []
    items = raw if isinstance(raw, list) else [raw]
    labels: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("source_label", "label", "header", "key_text"):
            value = str(item.get(key) or "").strip()
            if value and value not in labels:
                labels.append(value)
    return tuple(labels)


def _field_by_label(
    spec: CategorySpec,
    field_bindings: dict[str, Any] | None = None,
) -> dict[str, str]:
    fields = [*spec.required_fields, *spec.optional_fields]
    output: dict[str, str] = {}
    for field in fields:
        aliases = (field, *field_aliases(field), *_binding_labels(field, field_bindings))
        for alias in aliases:
            normalized = _normalize_label(alias)
            if not normalized:
                continue
            output.setdefault(normalized, field)
            for suffix in ("dbm", "percent", "percentage"):
                if normalized.endswith(suffix):
                    output.setdefault(normalized[: -len(suffix)], field)
    return output


def _embeddable_value_regex(pattern: str) -> str:
    """Turn a standalone validator regex into a TextFSM Value fragment."""

    source = str(pattern or "").strip()
    try:
        if re.fullmatch(source, "") is not None:
            return r"\S(?:.*\S)?"
    except re.error:
        return source
    # Validators often anchor every top-level alternative independently. Those
    # anchors become impossible once the fragment is embedded in a row rule.
    source = re.sub(r"(^|\|)\^", r"\1", source)
    source = re.sub(r"(?<!\\)\$(?=\||$)", "", source)
    return f"(?:{source})" if "|" in source else source


def _value_pattern(field: str, spec: CategorySpec) -> str:
    validator = (spec.validators or {}).get(field) or {}
    if validator.get("type") == "regex" and str(validator.get("pattern") or ""):
        return _embeddable_value_regex(str(validator["pattern"]))
    if validator.get("type") == "enum" and validator.get("values"):
        values = [
            re.escape(str(value).strip()).replace(r"\ ", r"\s+")
            for value in validator.get("values") or []
            if str(value).strip()
        ]
        if values:
            return "(?:" + "|".join(values) + ")"
    if validator.get("type") in {"number", "percent"}:
        return r"-?\d+(?:\.\d+)?%?"
    if field in {"description", "name", "reason"}:
        return r"\S(?:.*\S)?"
    return r"\S+"


def _value_declarations(
    spec: CategorySpec,
    fields: list[str],
    *,
    filldown: str | None = None,
) -> str:
    rows: list[str] = []
    for field in fields:
        options: list[str] = []
        if field == filldown:
            options.append("Filldown")
        option_text = f" {','.join(options)}" if options else ""
        rows.append(f"Value{option_text} {field} ({_value_pattern(field, spec)})")
    return "\n".join(rows)


def _mapped_header(
    header: str | list[str],
    spec: CategorySpec,
    *,
    excluded: set[str] | None = None,
    field_bindings: dict[str, Any] | None = None,
) -> list[str | None] | None:
    by_label = _field_by_label(spec, field_bindings)
    excluded = excluded or set()
    tokens = re.findall(r"\S+", header if isinstance(header, str) else " ".join(header))
    if len(tokens) < 2:
        return None
    max_phrase_tokens = max(
        (
            len(re.findall(r"\S+", alias))
            for field in spec.fields
            for alias in (
                field,
                *field_aliases(field),
                *_binding_labels(field, field_bindings),
            )
        ),
        default=1,
    )
    mapped: list[str | None] = []
    cursor = 0
    while cursor < len(tokens):
        matched_field: str | None = None
        matched_length = 1
        max_length = min(max_phrase_tokens, len(tokens) - cursor)
        for length in range(max_length, 0, -1):
            phrase = " ".join(tokens[cursor : cursor + length])
            field = by_label.get(_normalize_label(phrase))
            if field:
                matched_field = field
                matched_length = length
                break
        mapped.append(matched_field)
        cursor += matched_length
    mapped_fields = {item for item in mapped if item}
    if not mapped_fields:
        return None
    required = set(spec.required_fields) - excluded
    if not required.issubset(mapped_fields):
        return None
    if len(mapped_fields) != sum(item is not None for item in mapped):
        return None
    return mapped


def _row_pattern(mapped: list[str | None]) -> str:
    tokens = [f"${{{field}}}" if field else r"\S+" for field in mapped]
    return r"^\s*" + r"\s+".join(tokens) + r"\s*$$"


def _row_rule(mapped: list[str | None]) -> str:
    return f"  {_row_pattern(mapped)} -> Record"


def _row_rules(mapped: list[str | None], spec: CategorySpec) -> list[str]:
    """Emit full and trailing-optional row shapes, longest first."""

    required = set(spec.required_fields) | set(spec.primary_keys)
    variants: list[str] = []
    current = list(mapped)
    while current:
        variants.append(_row_rule(current))
        trailing = current[-1]
        if trailing is None or trailing in required:
            break
        current = current[:-1]
    return variants


def _mapped_table_headers(
    sample_text: str,
    spec: CategorySpec,
    *,
    excluded: set[str] | None = None,
    field_bindings: dict[str, Any] | None = None,
) -> list[tuple[int, str, list[str | None]]]:
    lines = str(sample_text or "").splitlines()
    headers: list[tuple[int, str, list[str | None]]] = []
    for index, line in enumerate(lines):
        if not line.strip() or ":" in line or re.fullmatch(r"\s*[-=]+\s*", line):
            continue
        mapped = _mapped_header(
            line,
            spec,
            excluded=excluded,
            field_bindings=field_bindings,
        )
        if not mapped:
            continue
        next_line = next(
            (candidate for candidate in lines[index + 1 :] if candidate.strip()),
            "",
        )
        minimum_columns = max(
            (
                position + 1
                for position, field in enumerate(mapped)
                if field in (set(spec.required_fields) - (excluded or set()))
            ),
            default=2,
        )
        if len(next_line.split()) < minimum_columns:
            continue
        headers.append((index, line.strip(), mapped))
    return headers


def expected_primary_key_tuples_from_tables(
    sample_text: str,
    spec: CategorySpec,
    *,
    field_bindings: dict[str, Any] | None = None,
) -> set[tuple[str, ...]]:
    """Extract exact entity keys from semantically mapped table headers."""

    if not spec.primary_keys:
        return set()
    lines = str(sample_text or "").splitlines()
    headers = _mapped_table_headers(
        sample_text,
        spec,
        field_bindings=field_bindings,
    )
    header_indexes = {index for index, _line, _mapped in headers}
    expected: set[tuple[str, ...]] = set()
    for header_index, _header_line, mapped in headers:
        key_positions: list[int] = []
        for primary_key in spec.primary_keys:
            try:
                key_positions.append(mapped.index(primary_key))
            except ValueError:
                key_positions = []
                break
        if not key_positions:
            continue
        started = False
        for index in range(header_index + 1, len(lines)):
            line = lines[index]
            stripped = line.strip()
            if index in header_indexes:
                break
            if not stripped:
                if started:
                    break
                continue
            if re.fullmatch(r"[-=]+", stripped) or (stripped.endswith(":") and started):
                break
            columns = stripped.split()
            if len(columns) <= max(key_positions):
                if started:
                    break
                continue
            key = tuple(columns[position] for position in key_positions)
            if any(
                not value
                or ":" in value
                or re.fullmatch(r"[-=]+", value)
                or _normalize_label(value) in _field_by_label(spec, field_bindings)
                for value in key
            ):
                if started:
                    break
                continue
            expected.add(key)
            started = True
    return expected


def _fields_for_mappings(
    spec: CategorySpec,
    mappings: list[list[str | None]],
) -> list[str]:
    present = {field for mapped in mappings for field in mapped if field}
    return [field for field in spec.fields if field in present]


def _literal_line_pattern(line: str) -> str:
    tokens = [re.escape(token) for token in line.strip().split()]
    return r"^\s*" + r"\s+".join(tokens) + r"\s*$$"


_MISSING_MARKERS = {
    "n/a",
    "na",
    "--",
    "none",
    "null",
    "unknown",
    "unavailable",
    "not available",
}


def _binding_items(
    field_bindings: dict[str, Any] | None,
    field_name: str,
) -> list[dict[str, Any]]:
    raw = dict(field_bindings or {}).get(field_name) or []
    values = raw if isinstance(raw, list) else [raw]
    return [dict(item) for item in values if isinstance(item, dict)]


def _binding_line(item: dict[str, Any]) -> int | None:
    for key in ("line", "line_number", "start_line"):
        try:
            value = int(item.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _binding_is_direct(item: dict[str, Any]) -> bool:
    return str(item.get("capture_kind") or "") != "derived"


def _binding_is_capturable(
    field_name: str,
    item: dict[str, Any],
    spec: CategorySpec,
) -> bool:
    value = str(item.get("source_value") or "").strip()
    if not value or not _binding_is_direct(item):
        return False
    # Privacy placeholders are evidence that a runtime value exists. Their
    # literal prompt representation must not be tested against the validator
    # compiled for the original value; the generated Value pattern already
    # carries that runtime validator.
    if runtime_pattern_for_redacted_value(value):
        return True
    validator = dict((spec.validators or {}).get(field_name) or {})
    if value.casefold() in _MISSING_MARKERS:
        return False
    if validator.get("type") in {"number", "percent"}:
        try:
            float(value.rstrip("%"))
        except ValueError:
            return False
    if validator.get("type") == "regex":
        pattern = str(validator.get("pattern") or "")
        if pattern and re.fullmatch(pattern, value) is None:
            return False
    return True


def _python_rule(pattern: str, field_patterns: dict[str, str]) -> re.Pattern[str]:
    value = pattern.replace("$$", "$")
    for field, field_pattern in field_patterns.items():
        value = value.replace(f"${{{field}}}", f"(?:{field_pattern})")
    return re.compile(value)


def _contextualize_fields(
    mapped: list[str | None],
    context_token: str,
    spec: CategorySpec,
) -> list[str | None] | None:
    context = _normalize_label(context_token)
    if not context or not re.search(r"[A-Za-z]", context_token):
        return None
    output: list[str | None] = []
    changed = False
    for field in mapped:
        if field is None:
            output.append(None)
            continue
        aliases = {
            _normalize_label(value)
            for value in (field, *field_aliases(field))
            if _normalize_label(value)
        }
        candidates = [
            candidate
            for candidate in spec.fields
            if context in _normalize_label(candidate)
            and aliases.intersection(
                {
                    _normalize_label(value)
                    for value in (candidate, *field_aliases(candidate))
                    if _normalize_label(value)
                }
            )
        ]
        replacement = candidates[0] if len(candidates) == 1 else field
        changed = changed or replacement != field
        output.append(replacement)
    return output if changed else None


def _evidence_row_pattern(
    mapped: list[str | None],
    row_tokens: list[str],
    spec: CategorySpec,
) -> tuple[str, tuple[str, ...]]:
    expressions: list[str] = []
    captured: list[str] = []
    for field, token in zip(mapped, row_tokens, strict=True):
        if field is None:
            expressions.append(
                r"[-+]?\d+(?:\.\d+)?"
                if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", token)
                else re.escape(token)
            )
            continue
        validator = dict((spec.validators or {}).get(field) or {})
        if token.casefold() in _MISSING_MARKERS and validator.get("type") in {
            "number",
            "percent",
        }:
            expressions.append(re.escape(token))
            continue
        expressions.append(f"${{{field}}}")
        captured.append(field)
    return r"^\s*" + r"\s+".join(expressions) + r"\s*$$", tuple(captured)


class EvidenceRepeatedBlockProvider:
    """Compile a repeated-block state machine from Contract line evidence."""

    name = "generic_evidence_ir"

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        if context.structure_profile.get("dominant_layout") != "repeated_block":
            return None
        if len(context.spec.primary_keys) != 1 or not context.evidence_samples:
            return None
        key = context.spec.primary_keys[0]
        key_items = [
            item for item in _binding_items(context.field_bindings, key) if _binding_is_direct(item)
        ]
        if not key_items:
            return None

        field_patterns = {
            field: _value_pattern(field, context.spec) for field in context.spec.fields
        }
        anchor_patterns: list[str] = []
        boundary_patterns: list[str] = []
        for item in key_items:
            sample_id = str(item.get("sample_id") or "")
            line_number = _binding_line(item)
            lines = str(context.evidence_samples.get(sample_id) or "").splitlines()
            if not line_number or line_number > len(lines):
                continue
            source_value = str(item.get("source_value") or "").strip()
            if not source_value:
                continue
            try:
                capture_pattern = compile_evidence_line(
                    lines[line_number - 1],
                    (EvidenceCapture(source_value, field_patterns[key], key),),
                )
                boundary_pattern = compile_evidence_line(
                    lines[line_number - 1],
                    (EvidenceCapture(source_value, field_patterns[key], None),),
                )
            except ValueError:
                continue
            if capture_pattern not in anchor_patterns:
                anchor_patterns.append(capture_pattern)
            if boundary_pattern not in boundary_patterns:
                boundary_patterns.append(boundary_pattern)
        if not anchor_patterns:
            return None

        anchor_matchers = [_python_rule(pattern, field_patterns) for pattern in anchor_patterns]
        block_ends: set[tuple[str, int]] = set()
        blocks: list[tuple[str, int, int]] = []
        for sample_id, sample_text in context.evidence_samples.items():
            lines = str(sample_text or "").splitlines()
            anchors = [
                index
                for index, line in enumerate(lines)
                if any(pattern.match(line) for pattern in anchor_matchers)
            ]
            for position, start in enumerate(anchors):
                next_start = anchors[position + 1] if position + 1 < len(anchors) else len(lines)
                end = next_start - 1
                while end > start and not lines[end].strip():
                    end -= 1
                if end > start:
                    block_ends.add((sample_id, end + 1))
                    blocks.append((sample_id, start + 1, end + 1))

        field_order = {field: index for index, field in enumerate(context.spec.fields)}
        line_bindings: dict[tuple[str, int], list[tuple[str, dict[str, Any]]]] = {}
        for field in context.spec.fields:
            if field == key:
                continue
            for item in _binding_items(context.field_bindings, field):
                line_number = _binding_line(item)
                sample_id = str(item.get("sample_id") or "")
                if not line_number or sample_id not in context.evidence_samples:
                    continue
                line_bindings.setdefault((sample_id, line_number), []).append((field, item))

        body_rules: list[ParserRule] = []
        body_rule_fields: list[tuple[str, tuple[str, ...]]] = []
        seen_rules: set[tuple[str, str | None]] = set()
        used_fields: set[str] = {key}
        all_lines = set(line_bindings) | block_ends
        for sample_id, line_number in sorted(all_lines):
            lines = str(context.evidence_samples.get(sample_id) or "").splitlines()
            if line_number < 1 or line_number > len(lines):
                continue
            captures: list[EvidenceCapture] = []
            for field, item in sorted(
                line_bindings.get((sample_id, line_number), []),
                key=lambda pair: field_order.get(pair[0], 10_000),
            ):
                if not _binding_is_capturable(field, item, context.spec):
                    continue
                captures.append(
                    EvidenceCapture(
                        str(item.get("source_value") or ""),
                        field_patterns[field],
                        field,
                    )
                )
                used_fields.add(field)
            try:
                pattern = compile_evidence_line(
                    lines[line_number - 1],
                    tuple(captures),
                    generalize_unbound_numbers=True,
                )
            except ValueError:
                continue
            signature = (pattern, None)
            if signature in seen_rules:
                continue
            seen_rules.add(signature)
            body_rules.append(ParserRule(pattern))
            body_rule_fields.append(
                (pattern, tuple(capture.field for capture in captures if capture.field))
            )

        inferred_rules: list[ParserRule] = []
        inferred_rule_fields: list[tuple[str, tuple[str, ...]]] = []
        for sample_id, start_line, end_line in blocks:
            lines = str(context.evidence_samples[sample_id]).splitlines()
            for header_index in range(start_line, end_line):
                mapped = _mapped_header(
                    lines[header_index],
                    context.spec,
                    excluded={key},
                    field_bindings=None,
                )
                if not mapped:
                    continue
                for row_index in range(header_index + 1, end_line):
                    row = lines[row_index].strip()
                    if not row or row.endswith(":"):
                        break
                    if _mapped_header(
                        lines[row_index],
                        context.spec,
                        excluded={key},
                        field_bindings=None,
                    ):
                        break
                    row_tokens = row.split()
                    value_count = sum(
                        bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?|N/A|NA|--", token, re.I))
                        for token in row_tokens
                    )
                    if value_count < 2:
                        continue
                    row_mapping: list[str | None] | None = None
                    if len(row_tokens) == len(mapped):
                        row_mapping = list(mapped)
                    elif mapped and mapped[0] is None and len(row_tokens) == len(mapped) - 1:
                        row_mapping = list(mapped[1:])
                    elif len(row_tokens) == len(mapped) + 1:
                        contextual = _contextualize_fields(
                            mapped,
                            row_tokens[0],
                            context.spec,
                        )
                        if contextual is not None:
                            row_mapping = [None, *contextual]
                        elif re.fullmatch(r"[-+]?\d+(?:\.\d+)?", row_tokens[0]):
                            row_mapping = [None, *mapped]
                    if row_mapping is None:
                        continue
                    pattern, fields = _evidence_row_pattern(
                        row_mapping,
                        row_tokens,
                        context.spec,
                    )
                    if not fields:
                        continue
                    signature = (pattern, None)
                    if signature in seen_rules:
                        continue
                    seen_rules.add(signature)
                    inferred_rules.append(ParserRule(pattern))
                    inferred_rule_fields.append((pattern, fields))
                    used_fields.update(fields)

        evidence_pairs = sorted(
            zip(body_rules, body_rule_fields, strict=True),
            key=lambda pair: -len(pair[1][1]),
        )
        body_rules = [pair[0] for pair in evidence_pairs]
        body_rule_fields = [pair[1] for pair in evidence_pairs]

        # Merge schema-derived and evidence-derived rules, then prefer the rule
        # that captures the most fields. A sparse rule that also matches a full
        # row must never consume that row before its richer sibling.
        body_rules = [*inferred_rules, *body_rules]
        body_rule_fields = [*inferred_rule_fields, *body_rule_fields]
        body_rules, body_rule_fields = _prioritize_capture_rules(
            body_rules,
            body_rule_fields,
        )

        if not blocks:
            return None
        repeated_fields: set[str] = set()
        for pattern, fields in body_rule_fields:
            if not fields:
                continue
            matcher = _python_rule(pattern, field_patterns)
            if any(
                sum(
                    bool(matcher.match(line))
                    for line in str(context.evidence_samples[sample_id]).splitlines()[
                        start_line:end_line
                    ]
                )
                > 1
                for sample_id, start_line, end_line in blocks
            ):
                repeated_fields.update(fields)
        values = tuple(
            ParserValue(
                field,
                field_patterns[field],
                ("List",) if field in repeated_fields else (),
            )
            for field in context.spec.fields
            if field in used_fields
        )
        boundary_rules: list[ParserRule] = []
        for boundary, capture in zip(boundary_patterns, anchor_patterns, strict=False):
            boundary_rules.extend((ParserRule(boundary, "Continue.Record"), ParserRule(capture)))
        ir = ParserIR(
            values=values,
            # One state plus TextFSM's implicit EOF record gives a uniform
            # lifecycle: a new sibling anchor commits the previous block and
            # end-of-input commits the final block. No field label needs to be
            # guessed as the terminal line for every entity variant.
            states=(ParserState("Start", tuple([*boundary_rules, *body_rules])),),
            explicit_eof=False,
            record_grain="repeated_entity",
        )
        return ProviderCandidate(
            compile_textfsm(ir),
            self.name,
            "evidence_repeated_block",
        )


class MultiSectionTableProvider:
    name = "generic_multi_table"

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        headers = _mapped_table_headers(
            context.samples[0].output,
            context.spec,
            field_bindings=context.field_bindings,
        )
        unique_headers: list[tuple[str, list[str | None]]] = []
        seen: set[tuple[str | None, ...]] = set()
        for _index, header_line, mapped in headers:
            key = tuple(mapped)
            if key not in seen:
                unique_headers.append((header_line, mapped))
                seen.add(key)
        if len(unique_headers) < 2:
            return None
        unique_mappings = [mapped for _line, mapped in unique_headers]
        fields = _fields_for_mappings(context.spec, unique_mappings)
        transitions = [
            f"  {_literal_line_pattern(line)} -> Table{index}"
            for index, (line, _mapped) in enumerate(unique_headers)
        ]
        states: list[str] = []
        for index, (_line, mapped) in enumerate(unique_headers):
            states.extend(
                [
                    f"Table{index}",
                    *transitions,
                    r"  ^\s*$$ -> Start",
                    r"  ^\s*.*:\s*$$ -> Start",
                    *_row_rules(mapped, context.spec),
                    "",
                ]
            )
        template = (
            f"{_value_declarations(context.spec, fields)}\n\n"
            "Start\n" + "\n".join(transitions) + "\n\n" + "\n".join(states) + "\nEOF\n"
        )
        return ProviderCandidate(template, self.name, "semantic_multi_table")


class GenericTableProvider:
    name = "generic_table"

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        headers = _mapped_table_headers(
            context.samples[0].output,
            context.spec,
            field_bindings=context.field_bindings,
        )
        for _index, header_line, mapped in headers:
            fields = [item for item in mapped if item]
            header_rule = f"  {_literal_line_pattern(header_line)} -> Rows"
            template = (
                f"{_value_declarations(context.spec, fields)}\n\n"
                "Start\n"
                f"{header_rule}\n\n"
                "Rows\n"
                f"{header_rule}\n"
                r"  ^\s*$$ -> Start"
                "\n"
                r"  ^\s*.*:\s*$$ -> Start"
                "\n" + "\n".join(_row_rules(mapped, context.spec)) + "\n\nEOF\n"
            )
            return ProviderCandidate(template, self.name, "semantic_table")
        return None


class KeyValueSingletonProvider:
    name = "generic_key_value_singleton"

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        if context.structure_profile.get("dominant_layout") != "key_value_singleton":
            return None
        by_label = _field_by_label(context.spec, context.field_bindings)
        orders: list[list[tuple[str, str]]] = []
        for sample in context.samples:
            rows: list[tuple[str, str]] = []
            for line in sample.output.splitlines():
                match = re.match(r"^\s*([^:]{1,80}?)\s*:\s*(.*?)\s*$", line)
                if not match:
                    continue
                field = by_label.get(_normalize_label(match.group(1)))
                if field and field not in {item[0] for item in rows}:
                    rows.append((field, match.group(1).strip()))
            orders.append(rows)
        expected_order = [field for field, _label in orders[0]] if orders else []
        if not orders or any([field for field, _label in row] != expected_order for row in orders):
            return None
        fields = [field for field, _label in orders[0]]
        if not set(context.spec.required_fields).issubset(fields):
            return None
        rules: list[str] = []
        for index, (field, label) in enumerate(orders[0]):
            action = " -> Record" if index == len(orders[0]) - 1 else ""
            rules.append(rf"  ^\s*{re.escape(label)}\s*:\s*${{{field}}}\s*$${action}")
        template = (
            f"{_value_declarations(context.spec, fields)}\n\nStart\n" + "\n".join(rules) + "\n"
        )
        return ProviderCandidate(template, self.name, "key_value_singleton")


class RepeatedBlockProvider:
    name = "generic_repeated_block"

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        if context.structure_profile.get("dominant_layout") != "repeated_block":
            return None
        if len(context.spec.primary_keys) != 1:
            return None
        key = context.spec.primary_keys[0]
        first_lines = [line for line in context.samples[0].output.splitlines() if line.strip()]
        headers = [
            match
            for line in first_lines
            if (match := re.match(r"^\s*([A-Za-z][\w -]*?)\s+(\S+)\s*:\s*$", line))
        ]
        if len(headers) < 2 or len({match.group(1) for match in headers}) != 1:
            return None
        prefix = headers[0].group(1)
        by_label = _field_by_label(context.spec, context.field_bindings)
        mapped_rows: list[tuple[str, str]] = []
        for line in first_lines:
            match = re.match(r"^\s*([^:]{1,80}?)\s*:\s*(.*?)\s*$", line)
            if not match:
                continue
            field = by_label.get(_normalize_label(match.group(1)))
            if field and field != key and field not in {item[0] for item in mapped_rows}:
                mapped_rows.append((field, match.group(1).strip()))
        if not (set(context.spec.required_fields) - {key}).issubset(
            {field for field, _label in mapped_rows}
        ):
            return None
        fields = [key, *(field for field, _label in mapped_rows)]
        header_rule = rf"^\s*{re.escape(prefix)}\s+${{{key}}}\s*:\s*$$"
        rules = [f"  {header_rule} -> Block", "", "Block", f"  {header_rule} -> Continue.Record"]
        for index, (field, label) in enumerate(mapped_rows):
            action = " -> Record Start" if index == len(mapped_rows) - 1 else ""
            rules.append(rf"  ^\s*{re.escape(label)}\s*:\s*${{{field}}}\s*$${action}")
        template = (
            f"{_value_declarations(context.spec, fields)}\n\nStart\n"
            + "\n".join(rules)
            + "\n\nEOF\n"
        )
        return ProviderCandidate(template, self.name, "repeated_key_value_block")


class SectionedTableProvider:
    name = "generic_sectioned_table"

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        if context.structure_profile.get("dominant_layout") != "sectioned_table":
            return None
        if len(context.spec.primary_keys) < 2:
            return None
        parent_key = context.spec.primary_keys[0]
        lines = [line for line in context.samples[0].output.splitlines() if line.strip()]
        parent = next(
            (
                match
                for line in lines
                if (match := re.match(r"^\s*([A-Za-z][\w -]*?)\s+(\S+)\s*:\s*$", line))
                and _normalize_label(match.group(1)) == _normalize_label(parent_key)
            ),
            None,
        )
        if parent is None:
            return None
        for line in lines:
            mapped = _mapped_header(
                line,
                context.spec,
                excluded={parent_key},
                field_bindings=context.field_bindings,
            )
            if not mapped:
                continue
            fields = [parent_key, *(item for item in mapped if item)]
            parent_rule = rf"^\s*{re.escape(parent.group(1))}\s+${{{parent_key}}}\s*:\s*$$"
            template = (
                f"{_value_declarations(context.spec, fields, filldown=parent_key)}\n\n"
                "Start\n"
                f"  {parent_rule} -> Rows\n\n"
                "Rows\n"
                f"  {parent_rule} -> Rows\n"
                + "\n".join(_row_rules(mapped, context.spec))
                + "\n\nEOF\n"
            )
            return ProviderCandidate(template, self.name, "sectioned_fixed_width_table")
        return None


class LegacyBuiltinProvider:
    """Manual compatibility provider; never loaded by the automatic registry."""

    name = "legacy_builtin"

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        from template_synthesizer import synthesize_template

        candidate = synthesize_template(
            command=context.command,
            samples=context.samples,
            spec=context.spec,
        )
        if not candidate:
            return None
        return ProviderCandidate(candidate.template_text, self.name, candidate.strategy)


class TemplateProviderRegistry:
    def __init__(
        self,
        providers: tuple[TemplateProvider, ...] | None = None,
        *,
        include_manual_builtins: bool = False,
    ) -> None:
        automatic: tuple[TemplateProvider, ...] = (
            EvidenceRepeatedBlockProvider(),
            MultiSectionTableProvider(),
            GenericTableProvider(),
            KeyValueSingletonProvider(),
            RepeatedBlockProvider(),
            SectionedTableProvider(),
        )
        if providers is not None:
            self.providers = providers
        elif include_manual_builtins:
            self.providers = (*automatic, LegacyBuiltinProvider())
        else:
            self.providers = automatic

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        candidates = self.propose_all(context, limit=1)
        return candidates[0] if candidates else None

    def propose_all(
        self,
        context: ProviderContext,
        *,
        limit: int = 2,
    ) -> tuple[ProviderCandidate, ...]:
        candidates: list[ProviderCandidate] = []
        seen_templates: set[str] = set()
        for provider in self.providers:
            candidate = provider.propose(context)
            if not candidate:
                continue
            digest = hashlib.sha256(candidate.template_text.encode("utf-8")).hexdigest()
            if digest in seen_templates:
                continue
            seen_templates.add(digest)
            candidates.append(candidate)
            if len(candidates) >= max(1, int(limit)):
                break
        return tuple(candidates)
