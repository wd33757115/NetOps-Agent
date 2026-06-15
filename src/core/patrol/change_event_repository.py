"""PostgreSQL repository for derived network changes and events."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from src.infrastructure.db.models import NetworkChangeRecord, NetworkEventRecord
from src.infrastructure.db.postgres import get_db_session


def _datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _digest(parts: Iterable[Any]) -> str:
    encoded = json.dumps(
        list(parts),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ChangeEventRepository:
    def save_changes(self, changes: list[Any]) -> int:
        created = 0
        with get_db_session() as db:
            for change in changes:
                key = _digest(
                    (
                        change.previous_snapshot_id,
                        change.current_snapshot_id,
                        change.device_id,
                        change.command,
                        change.entity_type,
                        change.entity_key,
                        change.field,
                        change.change_type,
                        change.old_value,
                        change.new_value,
                    )
                )
                if (
                    db.query(NetworkChangeRecord.id)
                    .filter(NetworkChangeRecord.idempotency_key == key)
                    .first()
                ):
                    continue
                db.add(
                    NetworkChangeRecord(
                        id=change.change_id,
                        idempotency_key=key,
                        run_id=change.run_id,
                        device_id=change.device_id,
                        canonical_command=change.command,
                        entity_type=change.entity_type,
                        entity_key=change.entity_key,
                        field=change.field,
                        old_value_json=change.old_value,
                        new_value_json=change.new_value,
                        change_type=change.change_type,
                        previous_snapshot_id=change.previous_snapshot_id,
                        current_snapshot_id=change.current_snapshot_id,
                        detected_at=_datetime(change.detected_at),
                        confidence=change.confidence,
                        created_at=datetime.now(timezone.utc),
                    )
                )
                created += 1
        return created

    def list_changes(
        self,
        *,
        run_id: str | None = None,
        device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(NetworkChangeRecord)
            if run_id:
                query = query.filter(NetworkChangeRecord.run_id == run_id)
            if device_id:
                query = query.filter(NetworkChangeRecord.device_id == device_id)
            rows = query.order_by(NetworkChangeRecord.detected_at).all()
            return [
                {
                    "change_id": row.id,
                    "run_id": row.run_id,
                    "device_id": row.device_id,
                    "command": row.canonical_command,
                    "entity_type": row.entity_type,
                    "entity_key": row.entity_key,
                    "field": row.field,
                    "old": row.old_value_json,
                    "new": row.new_value_json,
                    "change_type": row.change_type,
                    "previous_snapshot_id": row.previous_snapshot_id,
                    "current_snapshot_id": row.current_snapshot_id,
                    "detected_at": row.detected_at.isoformat(),
                    "confidence": row.confidence,
                }
                for row in rows
            ]

    def save_events(self, events: list[Any]) -> int:
        created = 0
        with get_db_session() as db:
            for event in events:
                key = _digest(
                    (
                        event.source_change_id,
                        event.event_type,
                        event.device_id,
                        event.entity_type,
                        event.entity_key,
                        event.run_id,
                    )
                )
                if (
                    db.query(NetworkEventRecord.id)
                    .filter(NetworkEventRecord.idempotency_key == key)
                    .first()
                ):
                    continue
                db.add(
                    NetworkEventRecord(
                        id=event.event_id,
                        idempotency_key=key,
                        event_type=event.event_type,
                        severity=event.severity,
                        device_id=event.device_id,
                        entity_type=event.entity_type,
                        entity_key=event.entity_key,
                        source_change_id=event.source_change_id,
                        run_id=event.run_id,
                        occurred_at=_datetime(event.occurred_at),
                        status=event.status,
                        payload_json=event.payload,
                        created_at=datetime.now(timezone.utc),
                    )
                )
                created += 1
        return created
