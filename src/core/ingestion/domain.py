"""Shared ingestion domain objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Result returned by a concrete ingestion adapter."""

    source_type: str
    evidence_type: str
    output_file: Path | None = None
    command_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
