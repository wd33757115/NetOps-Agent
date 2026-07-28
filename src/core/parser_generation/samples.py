"""Explicit sample roles and acceptance expectations for Parser generation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


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

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw_text.encode("utf-8")).hexdigest()

    @property
    def model_text(self) -> str:
        if self.role is not SampleRole.GENERATION:
            raise ValueError("only generation samples may be exposed to the model")
        return self.prompt_text if self.prompt_text is not None else self.raw_text


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
        return tuple(sample.model_text for sample in self.generation)
