"""Deterministic parser generation sample partitioning and prompt redaction."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .sanitization import sanitize_parser_sample


class HasParserSample(Protocol):
    output: str
    device_id: str
    file_path: str
    prompt_output: str | None


NEGATIVE_PATTERNS = (
    r"(?i)command execution failed",
    r"(?i)connection (?:timed out|reset|failed)",
    r"(?i)output truncated",
    r"(?i)(?:invalid|unrecognized|incomplete) command",
    r"(?i)permission denied",
    r"(?i)not supported",
)

DEFAULT_PROMPT_MAX_LINES = 180
DEFAULT_PROMPT_MAX_CHARS = 18_000
_SECTION_LINE_RE = re.compile(r"^(?P<indent>\s*)\S.*:\s*$")


@dataclass(frozen=True)
class SampleRef:
    index: int
    sample_id: str
    content_sha256: str
    layout_signature: str
    negative: bool


@dataclass(frozen=True)
class SamplePartition:
    train: tuple[SampleRef, ...]
    holdout: tuple[SampleRef, ...]
    negative: tuple[SampleRef, ...]
    source_sample_count: int

    @property
    def unique_sample_count(self) -> int:
        return len(self.train) + len(self.holdout) + len(self.negative)

    @property
    def duplicate_sample_count(self) -> int:
        return max(0, self.source_sample_count - self.unique_sample_count)

    @property
    def train_set_hash(self) -> str:
        return _set_hash(self.train)

    @property
    def holdout_set_hash(self) -> str:
        return _set_hash(self.holdout)

    def to_dict(self) -> dict[str, Any]:
        def serialize(rows: tuple[SampleRef, ...]) -> list[dict[str, Any]]:
            return [row.__dict__ for row in rows]

        return {
            "train": serialize(self.train),
            "holdout": serialize(self.holdout),
            "negative": serialize(self.negative),
            "source_sample_count": self.source_sample_count,
            "unique_sample_count": self.unique_sample_count,
            "duplicate_sample_count": self.duplicate_sample_count,
            "train_set_hash": self.train_set_hash,
            "holdout_set_hash": self.holdout_set_hash,
        }


def _set_hash(rows: tuple[SampleRef, ...]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row.content_sha256.encode("ascii"))
    return digest.hexdigest()


def _layout_signature(text: str) -> str:
    shapes: list[str] = []
    for line in str(text or "").splitlines()[:80]:
        normalized = re.sub(r"[A-Fa-f0-9]{2}(?::[A-Fa-f0-9]{2}){5}", "<MAC>", line)
        normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "<N>", normalized)
        normalized = re.sub(r"[A-Za-z]+", "A", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if normalized:
            shapes.append(normalized[:160])
    payload = "\n".join(shapes)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _semantic_line_shape(line: str) -> str:
    """Retain structural words while removing high-cardinality literal values."""

    value = str(line or "").casefold()
    value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", value)
    value = re.sub(r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", "<mac>", value)
    value = re.sub(r"[-+]?\d+(?:\.\d+)?", "<n>", value)
    return re.sub(r"\s+", " ", value).strip()[:240]


def _representative_blocks(lines: list[str]) -> list[tuple[int, int]]:
    sections: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        match = _SECTION_LINE_RE.match(line)
        if match:
            sections.append((index, len(match.group("indent"))))
    if len(sections) < 2:
        return []
    minimum_indent = min(indent for _index, indent in sections)
    anchors = [index for index, indent in sections if indent == minimum_indent]
    if len(anchors) < 2:
        return []
    return [
        (start, anchors[position + 1] if position + 1 < len(anchors) else len(lines))
        for position, start in enumerate(anchors)
    ]


def compact_parser_sample(
    text: str,
    *,
    max_lines: int = DEFAULT_PROMPT_MAX_LINES,
    max_chars: int = DEFAULT_PROMPT_MAX_CHARS,
) -> str:
    """Select structurally distinct CLI regions for model context.

    The full sample is always retained outside this function for deterministic
    validation. This representation exists only to avoid sending hundreds of
    shape-equivalent rows or blocks to every repair round.
    """

    raw = str(text or "")
    lines = raw.splitlines()
    if len(lines) <= max_lines and len(raw) <= max_chars:
        return raw

    blocks = _representative_blocks(lines)
    selected_lines: list[str] = []
    if blocks:
        seen: set[str] = set()
        selected_blocks: list[tuple[int, int]] = []
        for start, end in blocks:
            shape = "\n".join(_semantic_line_shape(line) for line in lines[start:end])
            signature = hashlib.sha256(shape.encode("utf-8")).hexdigest()
            if signature in seen:
                continue
            seen.add(signature)
            selected_blocks.append((start, end))
        for start, end in selected_blocks:
            block = lines[start:end]
            if selected_lines and len(selected_lines) + len(block) > max_lines:
                break
            selected_lines.extend(block)
    else:
        seen_shapes: set[str] = set()
        selected_indices: set[int] = set(range(min(12, len(lines))))
        for index, line in enumerate(lines):
            shape = _semantic_line_shape(line)
            if not shape or shape in seen_shapes:
                continue
            seen_shapes.add(shape)
            selected_indices.update(
                range(max(0, index - 1), min(len(lines), index + 2))
            )
            if len(selected_indices) >= max_lines:
                break
        selected_lines = [lines[index] for index in sorted(selected_indices)[:max_lines]]

    if not selected_lines:
        selected_lines = lines[:max_lines]
    output: list[str] = []
    size = 0
    for line in selected_lines[:max_lines]:
        projected = size + len(line) + (1 if output else 0)
        if output and projected > max_chars:
            break
        output.append(line)
        size = projected
    return "\n".join(output)


def _is_negative(text: str) -> bool:
    stripped = str(text or "").strip()
    return not stripped or any(re.search(pattern, stripped) for pattern in NEGATIVE_PATTERNS)


def partition_parser_samples(
    samples: tuple[HasParserSample, ...],
    *,
    max_train_samples: int = 4,
    holdout_ratio: float = 0.2,
) -> SamplePartition:
    refs: list[SampleRef] = []
    for index, sample in enumerate(samples):
        text = str(sample.output or "")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        refs.append(
            SampleRef(
                index=index,
                sample_id=f"sample-{digest[:12]}",
                content_sha256=digest,
                layout_signature=_layout_signature(text),
                negative=_is_negative(text),
            )
        )

    # Snapshot history is evidence, not an independent validation corpus. Repeated
    # imports may contribute many rows with identical output; retain the first
    # representative for training/validation and report the source count separately.
    unique_by_content: dict[str, SampleRef] = {}
    for row in refs:
        unique_by_content.setdefault(row.content_sha256, row)
    unique_refs = tuple(unique_by_content.values())

    negative = tuple(row for row in unique_refs if row.negative)
    eligible = [row for row in unique_refs if not row.negative]
    if len(eligible) <= 1:
        return SamplePartition(
            train=tuple(eligible),
            holdout=(),
            negative=negative,
            source_sample_count=len(refs),
        )

    holdout_count = max(1, round(len(eligible) * max(0.0, min(0.5, holdout_ratio))))
    holdout_order = sorted(
        eligible,
        key=lambda row: hashlib.sha256(f"holdout:{row.content_sha256}".encode("ascii")).hexdigest(),
    )
    holdout = tuple(holdout_order[:holdout_count])
    holdout_ids = {row.sample_id for row in holdout}
    remaining = [row for row in eligible if row.sample_id not in holdout_ids]

    # Select one representative per layout before filling remaining prompt slots.
    selected: list[SampleRef] = []
    seen_layouts: set[str] = set()
    for row in sorted(remaining, key=lambda item: (item.layout_signature, item.content_sha256)):
        if row.layout_signature in seen_layouts:
            continue
        selected.append(row)
        seen_layouts.add(row.layout_signature)
    for row in sorted(remaining, key=lambda item: item.content_sha256):
        if row not in selected:
            selected.append(row)
    train = tuple(selected[: max(1, int(max_train_samples))])
    train_ids = {row.sample_id for row in train}
    extra_holdout = tuple(row for row in remaining if row.sample_id not in train_ids)
    combined_holdout = tuple(
        sorted(
            {row.sample_id: row for row in (*holdout, *extra_holdout)}.values(),
            key=lambda row: row.content_sha256,
        )
    )
    return SamplePartition(
        train=train,
        holdout=combined_holdout,
        negative=negative,
        source_sample_count=len(refs),
    )


def render_sanitized_training_samples(
    samples: tuple[HasParserSample, ...],
    refs: tuple[SampleRef, ...],
) -> str:
    parts: list[str] = []
    for ref in refs:
        sample = samples[ref.index]
        sanitized = sanitize_parser_sample(
            str(getattr(sample, "prompt_output", None) or sample.output)
        )
        prompt_text = compact_parser_sample(sanitized.text)
        parts.append(
            f'<CLI_SAMPLE id="{ref.sample_id}" sha256="{sanitized.sha256}">\n'
            f"{prompt_text}\n"
            "</CLI_SAMPLE>"
        )
    return "\n\n".join(parts)


def prompt_line_map_for_sample(sample: HasParserSample) -> dict[int, int]:
    """Map compacted prompt line numbers back to full runtime sample lines.

    Contract evidence cites the compacted, sanitized prompt shown to the model,
    while parser validation deliberately executes against the complete raw
    sample. Sanitization preserves line boundaries, but compaction may remove
    repeated regions. This ordered mapping lets validation compare evidence and
    runtime records without exposing raw values to the model.
    """

    raw_text = str(sample.output or "")
    prompt_source = str(getattr(sample, "prompt_output", None) or raw_text)
    sanitized_prompt = sanitize_parser_sample(prompt_source).text
    sanitized_runtime = sanitize_parser_sample(raw_text).text
    compacted = compact_parser_sample(sanitized_prompt)

    prompt_lines = sanitized_prompt.splitlines()
    runtime_lines = sanitized_runtime.splitlines()
    compact_lines = compacted.splitlines()

    def ordered_positions(
        selected: list[str],
        source: list[str],
    ) -> dict[int, int]:
        positions: dict[int, int] = {}
        cursor = 0
        for selected_index, line in enumerate(selected, 1):
            match = next(
                (
                    source_index
                    for source_index in range(cursor, len(source))
                    if source[source_index] == line
                ),
                None,
            )
            if match is None:
                continue
            positions[selected_index] = match + 1
            cursor = match + 1
        return positions

    compact_to_prompt = ordered_positions(compact_lines, prompt_lines)
    prompt_to_runtime = ordered_positions(prompt_lines, runtime_lines)
    return {
        compact_line: runtime_line
        for compact_line, prompt_line in compact_to_prompt.items()
        if (runtime_line := prompt_to_runtime.get(prompt_line)) is not None
    }
