"""Domain objects for normalized field-level state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class StateObservation:
    observation_id: str
    idempotency_key: str
    state_key: str
    run_id: str
    snapshot_id: str
    dataset_id: str | None
    mode: str
    source: str
    source_type: str
    evidence_id: str | None
    schema_id: str | None
    device_id: str
    vendor: str | None
    model: str | None
    canonical_command: str
    entity_type: str
    entity_key: str
    field_name: str
    value: Any
    value_text: str | None
    value_number: float | None
    value_type: str
    observation_kind: str
    observed_at: str
    parser_template_id: str | None
    parser_version: str | None
    contract_version: int | None
    observation_type: str | None = None
    semantic_type: str | None = None
    unit: str | None = None
    dimensions: dict[str, Any] | None = None
    dimension_signature: str | None = None
    execution_item_id: str | None = None
    fact_order_key: str = ""
    confidence: float = 1.0
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["id"] = data.pop("observation_id")
        return data
