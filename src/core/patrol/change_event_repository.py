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


def _event_key(
    *,
    event_type: str,
    device_id: str | None,
    entity_type: str,
    entity_key: str,
    field: Any,
) -> str:
    return "|".join(
        [event_type, str(device_id or ""), entity_type, entity_key, str(field or "")]
    )


RECOVERY_EVENT_TYPES = {
    "CPURecovered",
    "MemoryRecovered",
    "InterfaceUp",
    "BGPNeighborEstablished",
}


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
                        previous_observation_id=getattr(
                            change, "previous_observation_id", None
                        ),
                        current_observation_id=getattr(
                            change, "current_observation_id", None
                        ),
                        source=getattr(change, "source", "patrol"),
                        old_number=getattr(change, "old_number", None),
                        new_number=getattr(change, "new_number", None),
                        event_status=getattr(change, "event_status", "pending"),
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
                    "previous_observation_id": row.previous_observation_id,
                    "current_observation_id": row.current_observation_id,
                    "source": row.source,
                    "old_number": row.old_number,
                    "new_number": row.new_number,
                    "event_status": row.event_status,
                    "detected_at": row.detected_at.isoformat(),
                    "confidence": row.confidence,
                }
                for row in rows
            ]

    def mark_changes_event_status(
        self,
        change_ids: list[str],
        *,
        status: str,
        only_pending: bool = True,
    ) -> int:
        if not change_ids:
            return 0
        with get_db_session() as db:
            query = db.query(NetworkChangeRecord).filter(
                NetworkChangeRecord.id.in_(change_ids)
            )
            if only_pending:
                query = query.filter(NetworkChangeRecord.event_status == "pending")
            rows = query.all()
            for row in rows:
                row.event_status = status
            return len(rows)

    def save_events(self, events: list[Any]) -> int:
        created = 0
        with get_db_session() as db:
            for event in events:
                if self._resolve_recovered_event(db, event):
                    if event.source_change_id:
                        change = db.get(NetworkChangeRecord, event.source_change_id)
                        if change is not None:
                            change.event_status = "event_created"
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
                active = self._find_active_event(db, event)
                if active is not None:
                    payload = dict(active.payload_json or {})
                    payload["latest_change"] = event.payload.get("change")
                    payload["latest_source_change_id"] = event.source_change_id
                    payload["latest_observed_at"] = event.occurred_at
                    active.payload_json = payload
                    active.run_id = event.run_id or active.run_id
                    if event.source_change_id:
                        change = db.get(NetworkChangeRecord, event.source_change_id)
                        if change is not None:
                            change.event_status = "event_created"
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
                        status=(
                            "resolved"
                            if event.event_type in RECOVERY_EVENT_TYPES
                            else event.status
                        ),
                        payload_json=self._event_payload(event),
                        created_at=datetime.now(timezone.utc),
                    )
                )
                db.flush()
                if event.source_change_id:
                    change = db.get(NetworkChangeRecord, event.source_change_id)
                    if change is not None:
                        change.event_status = "event_created"
                created += 1
        return created

    @staticmethod
    def _event_payload(event: Any) -> dict[str, Any]:
        payload = dict(event.payload or {})
        payload.setdefault(
            "event_class",
            "recovery" if event.event_type in RECOVERY_EVENT_TYPES else "fault",
        )
        return payload

    def _find_active_event(self, db, event: Any) -> NetworkEventRecord | None:
        dedupe_key = (event.payload or {}).get("dedupe_key")
        if not dedupe_key:
            return None
        rows = (
            db.query(NetworkEventRecord)
            .filter(NetworkEventRecord.event_type == event.event_type)
            .filter(NetworkEventRecord.device_id == event.device_id)
            .filter(NetworkEventRecord.entity_type == event.entity_type)
            .filter(NetworkEventRecord.entity_key == event.entity_key)
            .filter(NetworkEventRecord.status.in_(["open", "acknowledged", "recovering"]))
            .order_by(NetworkEventRecord.occurred_at.desc())
            .all()
        )
        for row in rows:
            if (row.payload_json or {}).get("dedupe_key") == dedupe_key:
                return row
        return None

    def _resolve_recovered_event(self, db, event: Any) -> bool:
        recovery_pairs = {
            "CPURecovered": "CPUHigh",
            "MemoryRecovered": "MemoryHigh",
            "InterfaceUp": "InterfaceDown",
            "BGPNeighborEstablished": "BGPNeighborLost",
        }
        target_type = recovery_pairs.get(event.event_type)
        if not target_type:
            return False
        change = (event.payload or {}).get("change") or {}
        target_key = _event_key(
            event_type=target_type,
            device_id=event.device_id,
            entity_type=event.entity_type,
            entity_key=event.entity_key,
            field=change.get("field"),
        )
        rows = (
            db.query(NetworkEventRecord)
            .filter(NetworkEventRecord.event_type == target_type)
            .filter(NetworkEventRecord.device_id == event.device_id)
            .filter(NetworkEventRecord.entity_type == event.entity_type)
            .filter(NetworkEventRecord.entity_key == event.entity_key)
            .filter(NetworkEventRecord.status.in_(["open", "acknowledged", "recovering"]))
            .all()
        )
        resolved = False
        for row in rows:
            if (row.payload_json or {}).get("dedupe_key") != target_key:
                continue
            payload = dict(row.payload_json or {})
            payload["recovery_change"] = change
            payload["recovered_by_event_type"] = event.event_type
            payload["resolved_at"] = event.occurred_at
            row.payload_json = payload
            row.status = "resolved"
            resolved = True
        return resolved

    def list_events(
        self,
        *,
        run_id: str | None = None,
        device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(NetworkEventRecord)
            if run_id:
                query = query.filter(NetworkEventRecord.run_id == run_id)
            if device_id:
                query = query.filter(NetworkEventRecord.device_id == device_id)
            rows = query.order_by(NetworkEventRecord.occurred_at).all()
            return [
                {
                    "event_id": row.id,
                    "event_type": row.event_type,
                    "severity": row.severity,
                    "device_id": row.device_id,
                    "entity_type": row.entity_type,
                    "entity_key": row.entity_key,
                    "source_change_id": row.source_change_id,
                    "run_id": row.run_id,
                    "occurred_at": row.occurred_at.isoformat(),
                    "status": row.status,
                    "payload": row.payload_json,
                }
                for row in rows
            ]
