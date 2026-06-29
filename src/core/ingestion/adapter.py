"""Base interface for platform ingestion adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.core.ingestion.domain import IngestionResult


class IngestionAdapter(ABC):
    """Collect evidence from one source type."""

    source_type: str

    @abstractmethod
    def collect(self, plan: Any) -> IngestionResult:
        """Collect evidence according to a source-specific plan."""
