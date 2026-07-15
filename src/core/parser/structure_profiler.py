"""Vendor-neutral structural profiling for semi-structured CLI samples."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class StructureEvidence:
    sample_id: str
    line_number: int
    text_shape: str
    role: str


@dataclass(frozen=True)
class TableRegion:
    sample_id: str
    header_line_number: int
    header_tokens: tuple[str, ...]
    first_row_line_number: int
    last_row_line_number: int
    row_count: int


@dataclass(frozen=True)
class ParsePlan:
    layout_kind: str
    confidence: float
    record_boundary: str
    estimated_entities: int | None
    header_tokens: tuple[str, ...] = ()
    evidence: tuple[StructureEvidence, ...] = ()
    table_regions: tuple[TableRegion, ...] = ()
    negative_classification: str | None = None
    plan_version: str = "structure-profiler-v2"

    @property
    def plan_hash(self) -> str:
        payload = repr(asdict(self)).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "plan_hash": self.plan_hash}


_KV_RE = re.compile(r"^\s*([^:]{1,80}?)\s*:\s*(.*?)\s*$")
_SECTION_RE = re.compile(r"^\s*([^:]{1,120})\s*:\s*$")


def _shape(line: str) -> str:
    value = re.sub(r"\b\d+(?:\.\d+)?\b", "<N>", line.strip())
    value = re.sub(r"[A-Za-z]+", "A", value)
    return re.sub(r"\s+", " ", value)[:160]


def _split_columns(line: str) -> tuple[str, ...]:
    columns = tuple(item.strip() for item in re.split(r"\s{2,}", line.strip()) if item.strip())
    if len(columns) > 1:
        return columns
    return tuple(line.strip().split())


def _fixed_columns(line: str) -> tuple[str, ...]:
    """Split a fixed-width row without guessing a command-specific schema."""

    return tuple(
        item.strip()
        for item in re.split(r"\s{2,}", str(line or "").strip())
        if item.strip()
    )


def _looks_like_table_header(line: str) -> tuple[str, ...]:
    stripped = str(line or "").strip()
    if (
        not stripped
        or stripped.endswith(":")
        or re.fullmatch(r"[-=]+", stripped)
        or _KV_RE.match(stripped)
    ):
        return ()
    columns = _fixed_columns(stripped)
    if len(columns) < 2:
        return ()
    alpha_columns = sum(bool(re.search(r"[A-Za-z]", column)) for column in columns)
    numeric_columns = sum(bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", column)) for column in columns)
    if alpha_columns < max(1, len(columns) // 2) or numeric_columns == len(columns):
        return ()
    return columns


def _looks_like_table_row(line: str, *, minimum_columns: int) -> bool:
    stripped = str(line or "").strip()
    if not stripped or stripped.endswith(":") or re.fullmatch(r"[-=]+", stripped):
        return False
    if re.match(r"(?i)^(?:note|legend|total|summary)\s*:", stripped):
        return False
    columns = _fixed_columns(stripped)
    if len(columns) >= minimum_columns:
        return True
    # Rows frequently omit trailing optional columns. Whitespace tokens still
    # provide a conservative lower bound without encoding any field names.
    return len(stripped.split()) >= minimum_columns


def _table_regions(
    lines: list[tuple[int, str]],
    *,
    sample_id: str,
) -> tuple[TableRegion, ...]:
    regions: list[TableRegion] = []
    cursor = 0
    while cursor < len(lines):
        header_number, header_line = lines[cursor]
        header = _looks_like_table_header(header_line)
        if not header:
            cursor += 1
            continue

        row_positions: list[int] = []
        position = cursor + 1
        while position < len(lines) and re.fullmatch(r"\s*[-=]+\s*", lines[position][1]):
            position += 1
        first_candidate = position
        while position < len(lines):
            row_number, row_line = lines[position]
            if not _looks_like_table_row(row_line, minimum_columns=2):
                break
            row_positions.append(row_number)
            position += 1

        if len(row_positions) >= 2:
            regions.append(
                TableRegion(
                    sample_id=sample_id,
                    header_line_number=header_number,
                    header_tokens=header,
                    first_row_line_number=row_positions[0],
                    last_row_line_number=row_positions[-1],
                    row_count=len(row_positions),
                )
            )
            cursor = position
            continue
        cursor += 1
    return tuple(regions)


def _negative_classification(text: str) -> str | None:
    rules = (
        ("unsupported", r"(?i)(?:not supported|unrecognized command|invalid command)"),
        ("permission_error", r"(?i)(?:permission denied|authorization failed)"),
        ("collection_error", r"(?i)(?:connection failed|timed out|output truncated)"),
        ("legal_empty_candidate", r"(?i)(?:not configured|no entries|no information)"),
    )
    return next((name for name, pattern in rules if re.search(pattern, text)), None)


def profile_cli_structure(text: str, *, sample_id: str = "sample") -> ParsePlan:
    lines = [
        (index, line) for index, line in enumerate(str(text or "").splitlines(), 1) if line.strip()
    ]
    negative = _negative_classification(text)
    if not lines:
        return ParsePlan("empty", 1.0, "none", 0, negative_classification="empty")
    if negative and len(lines) <= 4:
        return ParsePlan(
            "negative",
            0.98,
            "none",
            0,
            evidence=(StructureEvidence(sample_id, lines[0][0], _shape(lines[0][1]), "error"),),
            negative_classification=negative,
        )

    kv_lines = [(number, line, _KV_RE.match(line)) for number, line in lines]
    kv_lines = [(number, line, match) for number, line, match in kv_lines if match]
    sections = [
        (number, line, len(line) - len(line.lstrip()))
        for number, line in lines
        if _SECTION_RE.match(line)
    ]
    minimum_indent = min((indent for _number, _line, indent in sections), default=0)
    top_sections = [(number, line) for number, line, indent in sections if indent == minimum_indent]
    repeated_sections = len(top_sections) if len(top_sections) >= 2 else 0

    tables = _table_regions(lines, sample_id=sample_id)
    # A repeated top-level section is the entity boundary when its child tables
    # occur only for a subset of sections. This prevents nested measurement rows
    # from being mistaken for top-level entities without knowing the command.
    prefer_repeated_sections = repeated_sections >= 2 and (
        not tables or repeated_sections > len(tables)
    )
    if prefer_repeated_sections and len(kv_lines) >= repeated_sections:
        evidence = tuple(
            StructureEvidence(sample_id, number, _shape(line), "entity_header")
            for number, line in top_sections[:20]
        )
        return ParsePlan(
            "repeated_block",
            0.94,
            "repeated top-level section",
            repeated_sections,
            evidence=evidence,
            table_regions=tables,
            negative_classification=negative,
        )

    if tables:
        unique_headers = {region.header_tokens for region in tables}
        if len(tables) == 1:
            layout = "table"
        elif len(unique_headers) == 1 and top_sections:
            layout = "sectioned_table"
        else:
            layout = "multi_table"
        evidence: list[StructureEvidence] = []
        for region in tables[:20]:
            header_line = next(
                line for number, line in lines if number == region.header_line_number
            )
            first_row = next(
                line for number, line in lines if number == region.first_row_line_number
            )
            evidence.extend(
                (
                    StructureEvidence(
                        sample_id,
                        region.header_line_number,
                        _shape(header_line),
                        "table_header",
                    ),
                    StructureEvidence(
                        sample_id,
                        region.first_row_line_number,
                        _shape(first_row),
                        "entity_row",
                    ),
                )
            )
        return ParsePlan(
            layout,
            0.94 if len(tables) > 1 else 0.88,
            "one physical row across discovered table regions",
            sum(region.row_count for region in tables),
            header_tokens=tables[0].header_tokens,
            evidence=tuple(evidence),
            table_regions=tables,
            negative_classification=negative,
        )

    if repeated_sections >= 2:
        section_numbers = {number for number, _line in top_sections}
        sectioned_rows = 0
        sectioned_header: tuple[str, ...] = ()
        for position, (number, _line) in enumerate(lines[:-2]):
            if number not in section_numbers:
                continue
            header_line = lines[position + 1][1]
            row_line = lines[position + 2][1]
            if ":" in header_line or not re.search(r"\s{2,}", header_line):
                continue
            header = _split_columns(header_line)
            row = _split_columns(row_line)
            if len(header) >= 2 and len(row) >= 2:
                sectioned_header = header
                cursor = position + 2
                while cursor < len(lines) and lines[cursor][0] not in section_numbers:
                    if len(_split_columns(lines[cursor][1])) >= 2:
                        sectioned_rows += 1
                    cursor += 1
        if sectioned_rows >= 2 and sectioned_header:
            evidence = tuple(
                StructureEvidence(sample_id, number, _shape(line), "parent_section")
                for number, line in top_sections[:20]
            )
            return ParsePlan(
                "sectioned_table",
                0.92,
                "parent section plus one physical child row",
                sectioned_rows,
                header_tokens=sectioned_header,
                evidence=evidence,
                negative_classification=negative,
            )

    if repeated_sections >= 2 and len(kv_lines) >= repeated_sections:
        evidence = tuple(
            StructureEvidence(sample_id, number, _shape(line), "entity_header")
            for number, line in top_sections[:20]
        )
        return ParsePlan(
            "repeated_block",
            0.9,
            "repeated section header",
            repeated_sections,
            evidence=evidence,
            negative_classification=negative,
        )

    if len(kv_lines) >= 2 and len(kv_lines) >= round(len(lines) * 0.6):
        evidence = tuple(
            StructureEvidence(sample_id, number, _shape(line), "field")
            for number, line, _match in kv_lines[:20]
        )
        return ParsePlan(
            "key_value_singleton",
            0.9,
            "final mapped field",
            1,
            evidence=evidence,
            negative_classification=negative,
        )

    for position, (number, line) in enumerate(lines[:-2]):
        if ":" in line or re.fullmatch(r"\s*[-=]+\s*", line):
            continue
        header = _split_columns(line)
        if len(header) < 2 or not any(re.search(r"[A-Za-z]", token) for token in header):
            continue
        entity_rows: list[tuple[int, str]] = []
        previous_number = number
        for row_number, row in lines[position + 1 :]:
            if entity_rows and row_number > previous_number + 1:
                break
            if re.fullmatch(r"\s*[-=]+\s*", row) or (entity_rows and ":" in row):
                break
            if len(_split_columns(row)) < min(2, len(header)):
                if entity_rows:
                    break
                previous_number = row_number
                continue
            entity_rows.append((row_number, row))
            previous_number = row_number
        compatible = len(entity_rows)
        if compatible >= 2:
            evidence = (
                StructureEvidence(sample_id, number, _shape(line), "table_header"),
                *tuple(
                    StructureEvidence(sample_id, row_number, _shape(row), "entity_row")
                    for row_number, row in entity_rows[:5]
                ),
            )
            sectioned = bool(top_sections and top_sections[0][0] < number)
            return ParsePlan(
                "sectioned_table" if sectioned else "table",
                0.82,
                "one physical row",
                compatible,
                header_tokens=header,
                evidence=evidence,
                negative_classification=negative,
            )

    return ParsePlan(
        "unknown",
        0.35,
        "unresolved",
        None,
        evidence=(StructureEvidence(sample_id, lines[0][0], _shape(lines[0][1]), "sample"),),
        negative_classification=negative,
    )


def profile_sample_set(samples: list[tuple[str, str]]) -> dict[str, Any]:
    plans = [profile_cli_structure(text, sample_id=sample_id) for sample_id, text in samples]
    kinds = Counter(plan.layout_kind for plan in plans)
    dominant = kinds.most_common(1)[0][0] if kinds else "empty"
    return {
        "profiler_version": "structure-profiler-v2",
        "dominant_layout": dominant,
        "layout_counts": dict(sorted(kinds.items())),
        "samples": [plan.to_dict() for plan in plans],
    }
