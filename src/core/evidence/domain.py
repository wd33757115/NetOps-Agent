"""Domain objects for source-agnostic raw evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RawEvidence:
    """Immutable metadata for one piece of collected source evidence."""

    evidence_id: str
    evidence_key: str
    source_type: str
    evidence_type: str
    device_id: str | None
    run_id: str | None
    dataset_id: str | None
    observed_at: str | None
    content_ref: str
    content_hash: str
    source_snapshot_id: str | None = None
    inventory_device_id: str | None = None
    retention_policy: str | None = None
    metadata: dict[str, Any] | None = None
    source: str = "cli"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["id"] = data.pop("evidence_id")
        return data
