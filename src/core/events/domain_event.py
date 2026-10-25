# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""领域事件标准 envelope v1。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class DomainEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    schema_version: str = "1"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "system"  # chat | workflow | api | celery | system
    correlation_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None

    @property
    def idempotency_key(self) -> str:
        """Return the independently consumable fact identity."""
        return str(self.payload.get("occurrence_id") or self.event_id)

    @classmethod
    def from_stream_fields(cls, fields: dict[str, str]) -> DomainEvent:
        import json

        raw = fields.get("event") or fields.get("payload") or "{}"
        data = json.loads(raw) if isinstance(raw, str) else raw
        if "occurred_at" in data and isinstance(data["occurred_at"], str):
            data["occurred_at"] = datetime.fromisoformat(data["occurred_at"].replace("Z", "+00:00"))
        return cls.model_validate(data)

    def to_stream_fields(self) -> dict[str, str]:
        import json

        return {"event": json.dumps(self.model_dump(mode="json"), ensure_ascii=False, default=str)}
