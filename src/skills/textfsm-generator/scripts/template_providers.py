"""Command-neutral providers plus an explicitly opt-in manual fallback."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol

from config_loader import CategorySpec

from src.core.parser.evidence_utils import literal_occurs
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
from src.core.parser.textfsm_input import TEXTFSM_END_OF_INPUT


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
    entity_scope: str | None = None


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
    if validator.get("type") == "text":
        return r"\S(?:.*\S)?"
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
            len(re.findall(r"[A-Za-z0-9]+", alias))
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
    tokens = [
        f"${{{field}}}" if field else (r"\S(?:.*\S)?" if index == len(mapped) - 1 else r"\S+")
        for index, field in enumerate(mapped)
    ]
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
        if trailing in required:
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
    if value.casefold() in _MISSING_MARKERS and validator.get("type") in {
        "number",
        "percent",
    }:
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


def _has_bounded_literal_context(
    source_line: str,
    captures: list[EvidenceCapture],
) -> bool:
    """Reject capture rules that reduce an entire arbitrary line to text."""

    remainder = str(source_line or "").strip()
    for capture in captures:
        remainder = remainder.replace(str(capture.literal or ""), "", 1)
    literal = re.sub(r"\s+", "", remainder)
    if len(re.findall(r"[A-Za-z0-9]", literal)) >= 2:
        return True
    if any(token in literal for token in ("<>", "[]", "()", "{}")):
        return True
    return all(
        len(re.findall(r"[A-Za-z]{2,}", capture.pattern)) > 0
        for capture in captures
    )


def _python_rule(pattern: str, field_patterns: dict[str, str]) -> re.Pattern[str]:
    value = pattern.replace("$$", "$")
    for field, field_pattern in field_patterns.items():
        value = value.replace(f"${{{field}}}", f"(?:{field_pattern})")
    return re.compile(value)


def _primary_key_anchor_patterns(
    spec: CategorySpec,
    field_bindings: dict[str, Any] | None,
    *,
    evidence_samples: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Compile capture and non-capturing entity anchors from Contract evidence."""

    primary_keys = tuple(spec.primary_keys)
    if not primary_keys:
        return [], []
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for field in primary_keys:
        for item in _binding_items(field_bindings, field):
            if not _binding_is_direct(item):
                continue
            sample_id = str(item.get("sample_id") or "")
            line_number = _binding_line(item)
            if not line_number:
                continue
            grouped.setdefault((sample_id, line_number), {})[field] = item

    field_patterns = {field: _value_pattern(field, spec) for field in primary_keys}
    capture_patterns: list[str] = []
    boundary_patterns: list[str] = []
    for (sample_id, line_number), items in sorted(grouped.items()):
        if any(field not in items for field in primary_keys):
            continue
        source_line = ""
        sample_lines = str((evidence_samples or {}).get(sample_id) or "").splitlines()
        if line_number <= len(sample_lines):
            source_line = sample_lines[line_number - 1]
        if not source_line:
            labels = {str(items[field].get("source_label") or "").strip() for field in primary_keys}
            labels.discard("")
            if len(labels) != 1:
                continue
            source_line = labels.pop()

        captures: list[EvidenceCapture] = []
        boundaries: list[EvidenceCapture] = []
        for field in primary_keys:
            source_value = str(items[field].get("source_value") or "").strip()
            if not source_value:
                captures = []
                break
            captures.append(EvidenceCapture(source_value, field_patterns[field], field))
            boundaries.append(EvidenceCapture(source_value, field_patterns[field], None))
        if not captures:
            continue
        try:
            capture_pattern = compile_evidence_line(
                source_line,
                tuple(captures),
                generalize_unbound_numbers=True,
            )
            boundary_pattern = compile_evidence_line(
                source_line,
                tuple(boundaries),
                generalize_unbound_numbers=True,
            )
        except ValueError:
            continue
        if capture_pattern not in capture_patterns:
            capture_patterns.append(capture_pattern)
        if boundary_pattern not in boundary_patterns:
            boundary_patterns.append(boundary_pattern)
    return capture_patterns, boundary_patterns


def expected_entity_count_from_evidence(
    sample_text: str,
    spec: CategorySpec,
    *,
    field_bindings: dict[str, Any] | None = None,
) -> int | None:
    """Count repeated entities from evidenced primary-key anchors."""

    _captures, boundaries = _primary_key_anchor_patterns(spec, field_bindings)
    lines = str(sample_text or "").splitlines()
    matchers = [_python_rule(pattern, {}) for pattern in boundaries]
    matched_lines = {
        index
        for index, line in enumerate(lines)
        if any(matcher.match(line) for matcher in matchers)
    }
    required_evidence_counts: list[int] = []
    for field_name in spec.required_fields:
        evidence_lines = {
            index
            for item in _binding_items(field_bindings, field_name)
            if _binding_is_direct(item)
            for index, line in enumerate(lines)
            if (
                literal_occurs(line, str(item.get("source_label") or "").strip())
                if str(item.get("source_label") or "").strip()
                else (
                    str(item.get("source_value") or "").strip()
                    and literal_occurs(line, str(item.get("source_value") or "").strip())
                )
            )
        }
        if evidence_lines:
            required_evidence_counts.append(len(evidence_lines))
    return max([len(matched_lines), *required_evidence_counts], default=0) or None


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


def _nearest_block_section(
    *,
    sample_id: str,
    line_number: int,
    blocks: list[tuple[str, int, int]],
    evidence_samples: dict[str, str],
    anchor_matchers: list[re.Pattern[str]],
) -> str | None:
    """Return the nearest non-entity section heading for an evidenced row."""

    block = next(
        (
            (start_line, end_line)
            for block_sample, start_line, end_line in blocks
            if block_sample == sample_id and start_line <= line_number <= end_line
        ),
        None,
    )
    if block is None:
        return None
    start_line, _end_line = block
    lines = str(evidence_samples.get(sample_id) or "").splitlines()
    for index in range(line_number - 2, start_line - 1, -1):
        candidate = lines[index].strip()
        if not candidate.endswith(":"):
            continue
        if any(pattern.match(lines[index]) for pattern in anchor_matchers):
            return None
        return candidate
    return None


class EvidenceHierarchicalRowProvider:
    """Compile parent Filldown context plus repeated child record rows."""

    name = "generic_evidence_hierarchy"

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        primary_keys = tuple(context.spec.primary_keys)
        if len(primary_keys) < 2 or not context.evidence_samples:
            return None

        key_lines: dict[str, dict[str, set[int]]] = {}
        for field in primary_keys:
            for item in _binding_items(context.field_bindings, field):
                sample_id = str(item.get("sample_id") or "")
                line_number = _binding_line(item)
                if not line_number or sample_id not in context.evidence_samples:
                    continue
                key_lines.setdefault(field, {}).setdefault(sample_id, set()).add(line_number)
        key_counts = {
            field: sum(len(lines) for lines in by_sample.values())
            for field, by_sample in key_lines.items()
        }
        if set(key_counts) != set(primary_keys):
            return None
        maximum_count = max(key_counts.values(), default=0)
        parent_keys = tuple(
            field for field in primary_keys if key_counts[field] < maximum_count
        )
        child_keys = tuple(field for field in primary_keys if field not in parent_keys)
        if not parent_keys or not child_keys:
            return None

        for sample_id in context.evidence_samples:
            child_line_numbers = sorted(
                set().union(*(key_lines[field].get(sample_id, set()) for field in child_keys))
            )
            if not child_line_numbers:
                return None
            for parent in parent_keys:
                parent_line_numbers = sorted(key_lines[parent].get(sample_id, set()))
                if not parent_line_numbers or any(
                    not any(parent_line <= child_line for parent_line in parent_line_numbers)
                    for child_line in child_line_numbers
                ):
                    return None

        field_patterns = {
            field: _value_pattern(field, context.spec) for field in context.spec.fields
        }
        field_order = {field: index for index, field in enumerate(context.spec.fields)}
        line_bindings: dict[tuple[str, int], list[tuple[str, dict[str, Any]]]] = {}
        for field in context.spec.fields:
            for item in _binding_items(context.field_bindings, field):
                sample_id = str(item.get("sample_id") or "")
                line_number = _binding_line(item)
                if (
                    not line_number
                    or sample_id not in context.evidence_samples
                    or not _binding_is_capturable(field, item, context.spec)
                ):
                    continue
                line_bindings.setdefault((sample_id, line_number), []).append((field, item))

        def compile_group(
            sample_id: str,
            line_number: int,
            bindings: list[tuple[str, dict[str, Any]]],
        ) -> tuple[ParserRule, tuple[str, ...]] | None:
            lines = str(context.evidence_samples.get(sample_id) or "").splitlines()
            if line_number > len(lines):
                return None
            source_line = lines[line_number - 1]
            captures = [
                EvidenceCapture(
                    str(item.get("source_value") or ""),
                    field_patterns[field],
                    field,
                )
                for field, item in sorted(
                    bindings,
                    key=lambda pair: (
                        source_line.find(str(pair[1].get("source_value") or "")),
                        field_order.get(pair[0], 10_000),
                    ),
                )
            ]
            if not captures or not _has_bounded_literal_context(source_line, captures):
                return None
            try:
                pattern = compile_evidence_line(
                    source_line,
                    tuple(captures),
                    generalize_unbound_numbers=True,
                )
            except ValueError:
                return None
            return ParserRule(pattern), tuple(
                capture.field for capture in captures if capture.field
            )

        parent_rules: list[ParserRule] = []
        parent_metadata: list[tuple[str, tuple[str, ...]]] = []
        parent_fields: set[str] = set()
        seen_parent_patterns: set[str] = set()
        for (sample_id, line_number), bindings in sorted(line_bindings.items()):
            fields = {field for field, _item in bindings}
            if not set(parent_keys).issubset(fields) or set(child_keys).intersection(fields):
                continue
            compiled = compile_group(sample_id, line_number, bindings)
            if not compiled or compiled[0].pattern in seen_parent_patterns:
                continue
            rule, captured = compiled
            seen_parent_patterns.add(rule.pattern)
            parent_rules.append(rule)
            parent_metadata.append((rule.pattern, captured))
            parent_fields.update(captured)
        if not set(parent_keys).issubset(parent_fields):
            return None

        child_required = set(context.spec.required_fields) - parent_fields
        child_rules: list[ParserRule] = []
        child_metadata: list[tuple[str, tuple[str, ...]]] = []
        child_fields: set[str] = set()
        seen_child_patterns: set[str] = set()
        for (sample_id, line_number), bindings in sorted(line_bindings.items()):
            fields = {field for field, _item in bindings}
            if not set(child_keys).issubset(fields) or not child_required.issubset(fields):
                continue
            compiled = compile_group(sample_id, line_number, bindings)
            if not compiled or compiled[0].pattern in seen_child_patterns:
                continue
            rule, captured = compiled
            seen_child_patterns.add(rule.pattern)
            child_rules.append(ParserRule(rule.pattern, "Record"))
            child_metadata.append((rule.pattern, captured))
            child_fields.update(captured)
        if not set(child_keys).issubset(child_fields) or not child_required.issubset(
            child_fields
        ):
            return None

        parent_rules, _parent_metadata = _prioritize_capture_rules(
            parent_rules, parent_metadata
        )
        child_rules, _child_metadata = _prioritize_capture_rules(
            child_rules, child_metadata
        )
        used_fields = parent_fields | child_fields
        values = tuple(
            ParserValue(
                field,
                field_patterns[field],
                ("Filldown",) if field in parent_fields else (),
            )
            for field in context.spec.fields
            if field in used_fields
        )
        ir = ParserIR(
            values=values,
            states=(ParserState("Start", tuple([*parent_rules, *child_rules])),),
            explicit_eof=True,
            record_grain="hierarchical_child_row",
        )
        return ProviderCandidate(
            compile_textfsm(ir),
            self.name,
            "contract_evidence_parent_filldown_child_record",
        )


class EvidenceRepeatedBlockProvider:
    """Compile a repeated-block state machine from Contract line evidence."""

    name = "generic_evidence_ir"

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        if context.structure_profile.get("dominant_layout") != "repeated_block":
            return None
        if not context.spec.primary_keys or not context.evidence_samples:
            return None

        field_patterns = {
            field: _value_pattern(field, context.spec) for field in context.spec.fields
        }
        anchor_patterns, boundary_patterns = _primary_key_anchor_patterns(
            context.spec,
            context.field_bindings,
            evidence_samples=context.evidence_samples,
        )
        if not anchor_patterns:
            return None

        anchor_matchers = [_python_rule(pattern, field_patterns) for pattern in anchor_patterns]
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
                    blocks.append((sample_id, start + 1, end + 1))

        field_order = {field: index for index, field in enumerate(context.spec.fields)}
        line_bindings: dict[tuple[str, int], list[tuple[str, dict[str, Any]]]] = {}
        for field in context.spec.fields:
            if field in context.spec.primary_keys:
                continue
            for item in _binding_items(context.field_bindings, field):
                if not _binding_is_capturable(field, item, context.spec):
                    continue
                line_number = _binding_line(item)
                sample_id = str(item.get("sample_id") or "")
                if not line_number or sample_id not in context.evidence_samples:
                    continue
                line_bindings.setdefault((sample_id, line_number), []).append((field, item))

        body_rules: list[ParserRule] = []
        body_rule_fields: list[tuple[str, tuple[str, ...]]] = []
        body_rule_contexts: list[str | None] = []
        seen_rules: set[tuple[str, str | None]] = set()
        used_fields: set[str] = set(context.spec.primary_keys)
        for sample_id, line_number in sorted(line_bindings):
            lines = str(context.evidence_samples.get(sample_id) or "").splitlines()
            if line_number < 1 or line_number > len(lines):
                continue
            captures: list[EvidenceCapture] = []
            for field, item in sorted(
                line_bindings.get((sample_id, line_number), []),
                key=lambda pair: field_order.get(pair[0], 10_000),
            ):
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
            section = _nearest_block_section(
                sample_id=sample_id,
                line_number=line_number,
                blocks=blocks,
                evidence_samples=context.evidence_samples,
                anchor_matchers=anchor_matchers,
            )
            signature = (pattern, section)
            if signature in seen_rules:
                continue
            seen_rules.add(signature)
            body_rules.append(ParserRule(pattern))
            body_rule_fields.append(
                (pattern, tuple(capture.field for capture in captures if capture.field))
            )
            body_rule_contexts.append(section)

        inferred_rules: list[ParserRule] = []
        inferred_rule_fields: list[tuple[str, tuple[str, ...]]] = []
        inferred_rule_contexts: list[str | None] = []
        for sample_id, start_line, end_line in blocks:
            lines = str(context.evidence_samples[sample_id]).splitlines()
            for header_index in range(start_line, end_line):
                mapped = _mapped_header(
                    lines[header_index],
                    context.spec,
                    excluded=set(context.spec.primary_keys),
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
                        excluded=set(context.spec.primary_keys),
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
                    section = _nearest_block_section(
                        sample_id=sample_id,
                        line_number=row_index + 1,
                        blocks=blocks,
                        evidence_samples=context.evidence_samples,
                        anchor_matchers=anchor_matchers,
                    )
                    signature = (pattern, section)
                    if signature in seen_rules:
                        continue
                    seen_rules.add(signature)
                    inferred_rules.append(ParserRule(pattern))
                    inferred_rule_fields.append((pattern, fields))
                    inferred_rule_contexts.append(section)
                    used_fields.update(fields)

        evidence_entries = sorted(
            zip(body_rules, body_rule_fields, body_rule_contexts, strict=True),
            key=lambda entry: (-len(entry[1][1]), -len(entry[0].pattern)),
        )
        body_rules = [entry[0] for entry in evidence_entries]
        body_rule_fields = [entry[1] for entry in evidence_entries]
        body_rule_contexts = [entry[2] for entry in evidence_entries]

        # Merge schema-derived and evidence-derived rules, then prefer the rule
        # that captures the most fields. A sparse rule that also matches a full
        # row must never consume that row before its richer sibling.
        body_rules = [*inferred_rules, *body_rules]
        body_rule_fields = [*inferred_rule_fields, *body_rule_fields]
        body_rule_contexts = [*inferred_rule_contexts, *body_rule_contexts]
        prioritized_entries = sorted(
            zip(body_rules, body_rule_fields, body_rule_contexts, strict=True),
            key=lambda entry: (-len(entry[1][1]), -len(entry[0].pattern)),
        )
        body_rules = [entry[0] for entry in prioritized_entries]
        body_rule_fields = [entry[1] for entry in prioritized_entries]
        body_rule_contexts = [entry[2] for entry in prioritized_entries]

        if not blocks:
            return None
        repeated_fields: set[str] = set()
        for (pattern, fields), rule_context in zip(
            body_rule_fields,
            body_rule_contexts,
            strict=True,
        ):
            if not fields:
                continue
            matcher = _python_rule(pattern, field_patterns)
            is_repeated = False
            for sample_id, start_line, end_line in blocks:
                lines = str(context.evidence_samples[sample_id]).splitlines()
                match_count = sum(
                    bool(matcher.match(lines[line_number - 1]))
                    and (
                        rule_context is None
                        or _nearest_block_section(
                            sample_id=sample_id,
                            line_number=line_number,
                            blocks=blocks,
                            evidence_samples=context.evidence_samples,
                            anchor_matchers=anchor_matchers,
                        )
                        == rule_context
                    )
                    for line_number in range(start_line + 1, end_line + 1)
                )
                if match_count > 1:
                    is_repeated = True
                    break
            if is_repeated:
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
            boundary_rules.extend(
                (
                    ParserRule(boundary, "Continue.Record"),
                    ParserRule(capture, "Start"),
                )
            )
        section_names = list(dict.fromkeys(section for section in body_rule_contexts if section))
        section_states = {
            section: f"Context{index + 1}" for index, section in enumerate(section_names)
        }
        section_transitions = [
            ParserRule(_literal_line_pattern(section), state_name)
            for section, state_name in section_states.items()
        ]
        flush_rules = (
            [
                ParserRule(
                    rf"^\s*{re.escape(TEXTFSM_END_OF_INPUT)}\s*$$",
                    "Record EOF",
                )
            ]
            if section_states
            else []
        )
        common_rules = [
            rule
            for rule, section in zip(body_rules, body_rule_contexts, strict=True)
            if section is None
        ]
        states = [
            ParserState(
                "Start",
                tuple(
                    [
                        *boundary_rules,
                        *section_transitions,
                        *flush_rules,
                        *common_rules,
                    ]
                ),
            )
        ]
        for section, state_name in section_states.items():
            contextual_rules = [
                rule
                for rule, rule_section in zip(
                    body_rules,
                    body_rule_contexts,
                    strict=True,
                )
                if rule_section == section
            ]
            states.append(
                ParserState(
                    state_name,
                    tuple(
                        [
                            *boundary_rules,
                            *section_transitions,
                            *flush_rules,
                            *contextual_rules,
                            *common_rules,
                        ]
                    ),
                )
            )
        ir = ParserIR(
            values=values,
            # One state plus TextFSM's implicit EOF record gives a uniform
            # lifecycle: a new sibling anchor commits the previous block and
            # end-of-input commits the final block. No field label needs to be
            # guessed as the terminal line for every entity variant.
            states=tuple(states),
            explicit_eof=bool(section_states),
            record_grain="repeated_entity",
        )
        return ProviderCandidate(
            compile_textfsm(ir),
            self.name,
            "evidence_repeated_block",
        )


class EvidenceSingletonProvider:
    """Compile one snapshot record from governed Contract line evidence."""

    name = "generic_evidence_singleton"

    def propose(self, context: ProviderContext) -> ProviderCandidate | None:
        if context.entity_scope != "singleton":
            return None
        if context.spec.primary_keys or not context.evidence_samples:
            return None

        field_patterns = {
            field: _value_pattern(field, context.spec) for field in context.spec.fields
        }
        field_order = {field: index for index, field in enumerate(context.spec.fields)}
        line_bindings: dict[tuple[str, int], list[tuple[str, dict[str, Any]]]] = {}
        binding_lines_by_field: dict[str, dict[str, set[int]]] = {}
        for field in context.spec.fields:
            for item in _binding_items(context.field_bindings, field):
                sample_id = str(item.get("sample_id") or "")
                line_number = _binding_line(item)
                if (
                    not line_number
                    or sample_id not in context.evidence_samples
                    or not _binding_is_capturable(field, item, context.spec)
                ):
                    continue
                line_bindings.setdefault((sample_id, line_number), []).append((field, item))
                binding_lines_by_field.setdefault(field, {}).setdefault(sample_id, set()).add(
                    line_number
                )

        rules: list[ParserRule] = []
        metadata: list[tuple[str, tuple[str, ...]]] = []
        used_fields: set[str] = set()
        seen_patterns: set[str] = set()
        for (sample_id, line_number), bindings in sorted(line_bindings.items()):
            lines = str(context.evidence_samples.get(sample_id) or "").splitlines()
            if line_number > len(lines):
                continue
            source_line = lines[line_number - 1]
            captures = [
                EvidenceCapture(
                    str(item.get("source_value") or ""),
                    field_patterns[field],
                    field,
                )
                for field, item in sorted(
                    bindings,
                    key=lambda pair: (
                        source_line.find(str(pair[1].get("source_value") or "")),
                        field_order.get(pair[0], 10_000),
                    ),
                )
            ]
            if not captures or not _has_bounded_literal_context(source_line, captures):
                continue
            try:
                pattern = compile_evidence_line(
                    source_line,
                    tuple(captures),
                    generalize_unbound_numbers=True,
                )
            except ValueError:
                continue
            if pattern in seen_patterns:
                continue
            seen_patterns.add(pattern)
            fields = tuple(capture.field for capture in captures if capture.field)
            rules.append(ParserRule(pattern))
            metadata.append((pattern, fields))
            used_fields.update(fields)

        if not set(context.spec.required_fields).issubset(used_fields):
            return None
        rules, _metadata = _prioritize_capture_rules(rules, metadata)
        repeated_fields = {
            field
            for field, sample_lines in binding_lines_by_field.items()
            if any(len(lines) > 1 for lines in sample_lines.values())
        }
        values = tuple(
            ParserValue(
                field,
                field_patterns[field],
                ("List",) if field in repeated_fields else (),
            )
            for field in context.spec.fields
            if field in used_fields
        )
        flush = ParserRule(
            rf"^\s*{re.escape(TEXTFSM_END_OF_INPUT)}\s*$$",
            "Record EOF",
        )
        ir = ParserIR(
            values=values,
            states=(ParserState("Start", tuple([flush, *rules])),),
            explicit_eof=True,
            record_grain="snapshot_singleton",
        )
        return ProviderCandidate(
            compile_textfsm(ir),
            self.name,
            "contract_evidence_singleton",
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
            EvidenceSingletonProvider(),
            EvidenceHierarchicalRowProvider(),
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
