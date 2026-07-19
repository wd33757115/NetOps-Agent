"""Patrol data-plane domain records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SnapshotRecord:
    snapshot_id: str
    run_id: str
    device_id: str
    device_name: str
    ip: str | None
    vendor: str | None
    model: str | None
    command: str
    command_canonical: str
    parser_name: str | None
    parser_version: str | None
    observed_at: str
    structured_json: Any
    raw_text: str
    raw_text_hash: str
    parse_status: str
    dataset_id: str | None = None
    source_file_hash: str | None = None
    import_fingerprint: str | None = None
    evidence_id: str | None = None
    source_type: str = "cli"
    schema_id: str | None = None
    execution_item_id: str | None = None
    output_status: str = "not_classified"
    metadata: dict[str, Any] | None = None
