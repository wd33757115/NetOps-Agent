"""PostgreSQL repository for derived network changes and events."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from src.infrastructure.db.models import (
    NetworkChangeRecord,
    NetworkEventOutboxRecord,
    NetworkEventRecord,
)
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


def _event_class(event: Any) -> str:
    rule = dict((event.payload or {}).get("rule") or {})
    return str(rule.get("event_class") or "fault")


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
                        observation_type=getattr(change, "observation_type", None),
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
                        change_policy_id=getattr(change, "change_policy_id", None),
                        change_policy_version_id=getattr(
                            change, "change_policy_version_id", None
                        ),
                        comparison_type=getattr(change, "comparison_type", None),
                        decision_reason=getattr(change, "decision_reason", None),
                        policy_snapshot_json=getattr(change, "policy_snapshot", None),
                        observed_at=(
                            _datetime(getattr(change, "observed_at", None))
                            if getattr(change, "observed_at", None)
                            else None
                        ),
                        detected_at=_datetime(change.detected_at),
                        processed_at=_datetime(
                            getattr(change, "processed_at", None) or change.detected_at
                        ),
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
                    "observation_type": row.observation_type,
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
                    "change_policy_id": row.change_policy_id,
                    "change_policy_version_id": row.change_policy_version_id,
                    "comparison_type": row.comparison_type,
                    "decision_reason": row.decision_reason,
                    "policy_snapshot": row.policy_snapshot_json,
                    "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                    "detected_at": row.detected_at.isoformat(),
                    "processed_at": row.processed_at.isoformat(),
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
                            if _event_class(event) == "recovery"
                            else event.status
                        ),
                        payload_json=self._event_payload(event),
                        created_at=datetime.now(timezone.utc),
                    )
                )
                db.flush()
                self._enqueue_event(db, event)
                if event.source_change_id:
                    change = db.get(NetworkChangeRecord, event.source_change_id)
                    if change is not None:
                        change.event_status = "event_created"
                created += 1
        return created

    @staticmethod
    def _event_payload(event: Any) -> dict[str, Any]:
        payload = dict(event.payload or {})
        payload.setdefault("event_class", _event_class(event))
        return payload

    def _enqueue_event(self, db, event: Any) -> None:
        if db.query(NetworkEventOutboxRecord.id).filter(
            NetworkEventOutboxRecord.event_id == event.event_id
        ).first():
            return
        now = datetime.now(timezone.utc)
        payload = event.to_dict()
        payload["status"] = (
            "resolved" if _event_class(event) == "recovery" else event.status
        )
        db.add(
            NetworkEventOutboxRecord(
                id=str(uuid.uuid4()),
                event_id=event.event_id,
                event_type=f"network.{event.event_type}",
                payload_json=payload,
                status="pending",
                attempts=0,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
        )

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
        rule = dict((event.payload or {}).get("rule") or {})
        recovery = dict(rule.get("recovery") or {})
        target_types = recovery.get("resolves")
        if not target_types:
            return False
        if isinstance(target_types, str):
            target_types = [target_types]
        change = (event.payload or {}).get("change") or {}
        resolved = False
        for target_type in target_types:
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

    def list_pending_event_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(NetworkEventOutboxRecord)
                .filter(
                    NetworkEventOutboxRecord.status == "pending",
                    NetworkEventOutboxRecord.available_at <= datetime.now(timezone.utc),
                )
                .order_by(NetworkEventOutboxRecord.created_at)
                .limit(max(1, min(int(limit), 1000)))
                .all()
            )
            return [row.to_dict() for row in rows]

    def mark_event_outbox_published(self, outbox_id: str, message_id: str) -> None:
        with get_db_session() as db:
            row = db.get(NetworkEventOutboxRecord, outbox_id)
            if row is None:
                return
            row.status = "published"
            row.message_id = message_id
            row.published_at = datetime.now(timezone.utc)
            row.updated_at = datetime.now(timezone.utc)

    def mark_event_outbox_failed(self, outbox_id: str, error: str) -> None:
        with get_db_session() as db:
            row = db.get(NetworkEventOutboxRecord, outbox_id)
            if row is None:
                return
            row.attempts = int(row.attempts or 0) + 1
            row.last_error = str(error)[:4000]
            row.updated_at = datetime.now(timezone.utc)

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
