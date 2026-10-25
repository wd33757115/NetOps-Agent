"""Explicit sample roles and acceptance expectations for Parser generation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

MAX_MODEL_SAMPLE_COUNT = 3
MAX_MODEL_SAMPLE_CHARS = 24_000


def _structure_fingerprint(text: str) -> str:
    normalized_lines: list[str] = []
    for line in str(text or "").splitlines():
        normalized = re.sub(r"<[^>\r\n]+>", "<REDACTED>", line)
        normalized = re.sub(
            r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_])",
            "<N>",
            normalized,
        )
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if normalized:
            normalized_lines.append(normalized)
    payload = "\n".join(normalized_lines)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bounded_model_text(text: str, *, max_chars: int = MAX_MODEL_SAMPLE_CHARS) -> str:
    value = str(text or "")
    limit = max(1_000, int(max_chars))
    if len(value) <= limit:
        return value
    tail_size = max(2_000, limit // 4)
    head_size = limit - tail_size
    head = value[:head_size]
    tail = value[-tail_size:]
    if "\n" in head:
        head = head.rsplit("\n", 1)[0]
    if "\n" in tail:
        tail = tail.split("\n", 1)[1]
    omitted = max(0, len(value) - len(head) - len(tail))
    return (
        f"{head}\n"
        f"<CLI_SAMPLE_EXCERPT_OMITTED chars=\"{omitted}\"/>\n"
        f"{tail}"
    )


def representative_samples(
    samples: tuple[ParserSample, ...],
    *,
    limit: int = MAX_MODEL_SAMPLE_COUNT,
) -> tuple[ParserSample, ...]:
    """Select structural variants for prompts without changing frozen validation input."""

    max_count = max(1, int(limit))
    selected: list[ParserSample] = []
    selected_ids: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for role in dict.fromkeys(sample.role for sample in samples):
        sample = next((item for item in samples if item.role is role), None)
        if sample is None:
            continue
        key = (sample.role.value, _structure_fingerprint(sample.validation_text))
        seen.add(key)
        selected.append(sample)
        selected_ids.add(sample.sample_id)
        if len(selected) >= max_count:
            return tuple(selected)
    for sample in samples:
        if sample.sample_id in selected_ids:
            continue
        key = (sample.role.value, _structure_fingerprint(sample.validation_text))
        if key in seen:
            continue
        seen.add(key)
        selected.append(sample)
        selected_ids.add(sample.sample_id)
        if len(selected) >= max_count:
            break
    return tuple(selected)


class SampleRole(StrEnum):
    GENERATION = "generation"
    ACCEPTANCE = "acceptance"
    NEGATIVE = "negative"


@dataclass(frozen=True)
class SampleExpectation:
    record_count: int | None = None
    primary_keys: frozenset[tuple[str, ...]] = frozenset()

    def __post_init__(self) -> None:
        if self.record_count is not None and self.record_count < 0:
            raise ValueError("expected record_count cannot be negative")
        if self.primary_keys and self.record_count is not None:
            if len(self.primary_keys) != self.record_count:
                raise ValueError("expected primary key count must equal record_count")


@dataclass(frozen=True)
class ParserSample:
    sample_id: str
    raw_text: str
    role: SampleRole
    prompt_text: str | None = None
    expectation: SampleExpectation | None = None
    validation_text_override: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw_text.encode("utf-8")).hexdigest()

    @property
    def model_text(self) -> str:
        if self.role is not SampleRole.GENERATION:
            raise ValueError("only generation samples may be exposed to the model")
        return self.prompt_text if self.prompt_text is not None else self.raw_text

    @property
    def validation_text(self) -> str:
        """Return the frozen evidence view used for generation and semantic review."""

        return (
            self.validation_text_override
            if self.validation_text_override is not None
            else self.raw_text
        )

    @property
    def validation_sha256(self) -> str:
        return hashlib.sha256(self.validation_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SampleSet:
    samples: tuple[ParserSample, ...]

    def __post_init__(self) -> None:
        ids = [sample.sample_id for sample in self.samples]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Parser sample IDs must be non-empty and unique")
        if not self.generation:
            raise ValueError("at least one generation sample is required")

    @property
    def generation(self) -> tuple[ParserSample, ...]:
        return tuple(sample for sample in self.samples if sample.role is SampleRole.GENERATION)

    @property
    def acceptance(self) -> tuple[ParserSample, ...]:
        return tuple(sample for sample in self.samples if sample.role is SampleRole.ACCEPTANCE)

    @property
    def negative(self) -> tuple[ParserSample, ...]:
        return tuple(sample for sample in self.samples if sample.role is SampleRole.NEGATIVE)

    @property
    def model_samples(self) -> tuple[str, ...]:
        return tuple(
            bounded_model_text(sample.model_text)
            for sample in representative_samples(self.generation)
        )
