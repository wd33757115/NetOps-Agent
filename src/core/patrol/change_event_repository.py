"""PostgreSQL repository for derived network changes and events."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.db.models import (
    NetworkChangeDecisionRecord,
    NetworkChangeRecord,
    NetworkEventOccurrenceRecord,
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


def _event_class(event: Any) -> str:
    rule = dict((event.payload or {}).get("rule") or {})
    return str(rule.get("event_class") or "fault")


class ChangeEventRepository:
    def save_changes(
        self,
        changes: list[Any],
        *,
        derived_observations: list[Any] | None = None,
        decisions: list[dict[str, Any]] | None = None,
    ) -> int:
        created = 0
        with get_db_session() as db:
            if derived_observations:
                from src.core.state.repository import StateRepository

                StateRepository().save_observations_in_session(
                    db,
                    list(derived_observations),
                    update_current_state=True,
                )
            for decision in decisions or []:
                self._save_decision_row(db, decision)
            change_values: list[dict[str, Any]] = []
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
                change_values.append(
                    {
                        "id": change.change_id,
                        "idempotency_key": key,
                        "run_id": change.run_id,
                        "device_id": change.device_id,
                        "canonical_command": change.command,
                        "entity_type": change.entity_type,
                        "entity_key": change.entity_key,
                        "dimensions_json": getattr(change, "dimensions", None),
                        "field": change.field,
                        "observation_type": getattr(change, "observation_type", None),
                        "old_value_json": change.old_value,
                        "new_value_json": change.new_value,
                        "change_type": change.change_type,
                        "previous_snapshot_id": change.previous_snapshot_id,
                        "current_snapshot_id": change.current_snapshot_id,
                        "previous_observation_id": getattr(change, "previous_observation_id", None),
                        "current_observation_id": getattr(change, "current_observation_id", None),
                        "source": getattr(change, "source", "patrol"),
                        "old_number": getattr(change, "old_number", None),
                        "new_number": getattr(change, "new_number", None),
                        "event_status": getattr(change, "event_status", "pending"),
                        "change_policy_id": getattr(change, "change_policy_id", None),
                        "change_policy_version_id": getattr(
                            change, "change_policy_version_id", None
                        ),
                        "comparison_type": getattr(change, "comparison_type", None),
                        "decision_reason": getattr(change, "decision_reason", None),
                        "policy_snapshot_json": getattr(change, "policy_snapshot", None),
                        "observed_at": (
                            _datetime(getattr(change, "observed_at", None))
                            if getattr(change, "observed_at", None)
                            else None
                        ),
                        "detected_at": _datetime(change.detected_at),
                        "processed_at": _datetime(
                            getattr(change, "processed_at", None) or change.detected_at
                        ),
                        "confidence": change.confidence,
                        "created_at": datetime.now(timezone.utc),
                    }
                )
            if change_values:
                stmt = (
                    pg_insert(NetworkChangeRecord)
                    .values(change_values)
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                    .returning(NetworkChangeRecord.id)
                )
                created = len(db.execute(stmt).all())
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
                    "dimensions": row.dimensions_json or {},
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
            query = db.query(NetworkChangeRecord).filter(NetworkChangeRecord.id.in_(change_ids))
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
                    occurrence_created = self._record_event_occurrence(db, active.id, event)
                    if not occurrence_created:
                        continue
                    payload = dict(active.payload_json or {})
                    payload["latest_change"] = event.payload.get("change")
                    payload["latest_source_change_id"] = event.source_change_id
                    payload["latest_observed_at"] = event.occurred_at
                    active.payload_json = payload
                    active.correlation_key = str(
                        (event.payload or {}).get("correlation_key") or active.correlation_key
                    )
                    active.correlation_dimensions_json = dict(
                        (event.payload or {}).get("correlation_dimensions") or {}
                    )
                    active.latest_change_id = event.source_change_id
                    active.last_seen_run_id = event.run_id
                    active.last_seen_at = _datetime(event.occurred_at)
                    active.occurrence_count = int(active.occurrence_count or 1) + 1
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
                        correlation_key=str((event.payload or {}).get("correlation_key") or ""),
                        correlation_dimensions_json=dict(
                            (event.payload or {}).get("correlation_dimensions") or {}
                        ),
                        source_change_id=event.source_change_id,
                        run_id=event.run_id,
                        latest_change_id=event.source_change_id,
                        last_seen_run_id=event.run_id,
                        occurred_at=_datetime(event.occurred_at),
                        first_seen_at=_datetime(event.occurred_at),
                        last_seen_at=_datetime(event.occurred_at),
                        occurrence_count=1,
                        status=("resolved" if _event_class(event) == "recovery" else event.status),
                        payload_json=self._event_payload(event),
                        created_at=datetime.now(timezone.utc),
                    )
                )
                db.flush()
                self._record_event_occurrence(db, event.event_id, event)
                self._enqueue_event(db, event)
                if event.source_change_id:
                    change = db.get(NetworkChangeRecord, event.source_change_id)
                    if change is not None:
                        change.event_status = "event_created"
                created += 1
        return created

    def _record_event_occurrence(self, db, event_id: str, event: Any) -> bool:
        key = _digest(
            (
                event_id,
                event.source_change_id,
                event.run_id,
                event.event_type,
                event.occurred_at,
            )
        )
        stmt = (
            pg_insert(NetworkEventOccurrenceRecord)
            .values(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                idempotency_key=key,
                event_id=event_id,
                run_id=event.run_id,
                source_change_id=event.source_change_id,
                observed_at=_datetime(event.occurred_at),
                severity=event.severity,
                occurrence_kind=("recovery" if _event_class(event) == "recovery" else "observed"),
                payload_json=self._event_payload(event),
                created_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(NetworkEventOccurrenceRecord.id)
        )
        return db.execute(stmt).scalar_one_or_none() is not None

    def _save_decision_row(self, db, decision: dict[str, Any]) -> None:
        key = _digest(
            (
                decision.get("run_id"),
                decision.get("previous_observation_id"),
                decision.get("current_observation_id"),
                decision.get("decision"),
                decision.get("reason"),
                decision.get("change_policy_version_id"),
            )
        )
        if (
            db.query(NetworkChangeDecisionRecord.id)
            .filter(NetworkChangeDecisionRecord.idempotency_key == key)
            .first()
        ):
            return
        db.add(
            NetworkChangeDecisionRecord(
                id=str(uuid.uuid4()),
                idempotency_key=key,
                run_id=str(decision.get("run_id") or ""),
                device_id=decision.get("device_id"),
                canonical_command=decision.get("canonical_command"),
                entity_type=str(decision.get("entity_type") or "record"),
                entity_key=str(decision.get("entity_key") or ""),
                field=str(decision.get("field") or ""),
                observation_type=decision.get("observation_type"),
                previous_observation_id=decision.get("previous_observation_id"),
                current_observation_id=decision.get("current_observation_id"),
                decision=str(decision.get("decision") or "invalid"),
                reason=decision.get("reason"),
                change_policy_id=decision.get("change_policy_id"),
                change_policy_version_id=decision.get("change_policy_version_id"),
                comparison_type=decision.get("comparison_type"),
                old_value_json=decision.get("old"),
                new_value_json=decision.get("new"),
                observed_at=(
                    _datetime(decision.get("observed_at")) if decision.get("observed_at") else None
                ),
                expires_at=(
                    _datetime(decision.get("expires_at")) if decision.get("expires_at") else None
                ),
                metadata_json=dict(decision.get("metadata") or {}),
                created_at=datetime.now(timezone.utc),
            )
        )

    def list_change_decisions(self, *, run_id: str) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(NetworkChangeDecisionRecord)
                .filter(NetworkChangeDecisionRecord.run_id == run_id)
                .order_by(NetworkChangeDecisionRecord.created_at)
                .all()
            )
            return [row.to_dict() for row in rows]

    def purge_expired_change_decisions(self, *, limit: int = 5000) -> int:
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            ids = [
                row[0]
                for row in db.query(NetworkChangeDecisionRecord.id)
                .filter(
                    NetworkChangeDecisionRecord.expires_at.is_not(None),
                    NetworkChangeDecisionRecord.expires_at < now,
                )
                .order_by(NetworkChangeDecisionRecord.expires_at)
                .limit(max(1, min(int(limit), 10000)))
                .all()
            ]
            if not ids:
                return 0
            return (
                db.query(NetworkChangeDecisionRecord)
                .filter(NetworkChangeDecisionRecord.id.in_(ids))
                .delete(synchronize_session=False)
            )

    @staticmethod
    def _event_payload(event: Any) -> dict[str, Any]:
        payload = dict(event.payload or {})
        payload.setdefault("event_class", _event_class(event))
        return payload

    def _enqueue_event(self, db, event: Any) -> None:
        if (
            db.query(NetworkEventOutboxRecord.id)
            .filter(NetworkEventOutboxRecord.event_id == event.event_id)
            .first()
        ):
            return
        now = datetime.now(timezone.utc)
        payload = event.to_dict()
        payload["status"] = "resolved" if _event_class(event) == "recovery" else event.status
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
        correlation_key = str((event.payload or {}).get("correlation_key") or "")
        if not correlation_key:
            return None
        correlation_keys = [correlation_key]
        legacy = str((event.payload or {}).get("legacy_correlation_key") or "")
        if legacy and legacy not in correlation_keys:
            correlation_keys.append(legacy)
        return (
            db.query(NetworkEventRecord)
            .filter(NetworkEventRecord.event_type == event.event_type)
            .filter(NetworkEventRecord.correlation_key.in_(correlation_keys))
            .filter(NetworkEventRecord.status.in_(["open", "acknowledged", "recovering"]))
            .order_by(NetworkEventRecord.occurred_at.desc())
            .first()
        )

    def _resolve_recovered_event(self, db, event: Any) -> bool:
        rule = dict((event.payload or {}).get("rule") or {})
        recovery = dict(rule.get("recovery") or {})
        target_types = recovery.get("resolves")
        if not target_types:
            return False
        if isinstance(target_types, str):
            target_types = [target_types]
        change = (event.payload or {}).get("change") or {}
        correlation_key = str((event.payload or {}).get("correlation_key") or "")
        if not correlation_key:
            return False
        correlation_keys = [correlation_key]
        legacy = str((event.payload or {}).get("legacy_correlation_key") or "")
        if legacy and legacy not in correlation_keys:
            correlation_keys.append(legacy)
        resolved = False
        for target_type in target_types:
            rows = (
                db.query(NetworkEventRecord)
                .filter(NetworkEventRecord.event_type == target_type)
                .filter(NetworkEventRecord.correlation_key.in_(correlation_keys))
                .filter(NetworkEventRecord.status.in_(["open", "acknowledged", "recovering"]))
                .all()
            )
            for row in rows:
                payload = dict(row.payload_json or {})
                payload["recovery_change"] = change
                payload["recovered_by_event_type"] = event.event_type
                payload["resolved_at"] = event.occurred_at
                row.payload_json = payload
                row.status = "resolved"
                resolved = True
        return resolved

    def claim_event_outbox(
        self,
        *,
        limit: int = 10,
        lease_seconds: int = 60,
    ) -> tuple[str, list[dict[str, Any]]]:
        claim_token = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            rows = (
                db.query(NetworkEventOutboxRecord)
                .filter(
                    NetworkEventOutboxRecord.available_at <= datetime.now(timezone.utc),
                    (NetworkEventOutboxRecord.status.in_(["pending", "retry_wait"]))
                    | (
                        (NetworkEventOutboxRecord.status == "publishing")
                        & (NetworkEventOutboxRecord.lease_until < now)
                    ),
                )
                .order_by(NetworkEventOutboxRecord.created_at)
                .with_for_update(skip_locked=True)
                .limit(max(1, min(int(limit), 1000)))
                .all()
            )
            lease_until = now + timedelta(seconds=max(5, int(lease_seconds)))
            for row in rows:
                row.status = "publishing"
                row.claim_token = claim_token
                row.lease_until = lease_until
                row.updated_at = now
            db.flush()
            return claim_token, [row.to_dict() for row in rows]

    def renew_event_outbox_claim(
        self,
        outbox_id: str,
        *,
        claim_token: str,
        lease_seconds: int = 60,
    ) -> bool:
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            updated = (
                db.query(NetworkEventOutboxRecord)
                .filter(
                    NetworkEventOutboxRecord.id == outbox_id,
                    NetworkEventOutboxRecord.status == "publishing",
                    NetworkEventOutboxRecord.claim_token == claim_token,
                )
                .update(
                    {
                        "lease_until": now + timedelta(seconds=max(5, int(lease_seconds))),
                        "updated_at": now,
                    },
                    synchronize_session=False,
                )
            )
            return updated == 1

    def mark_event_outbox_published(
        self,
        outbox_id: str,
        message_id: str,
        *,
        claim_token: str,
    ) -> None:
        with get_db_session() as db:
            row = db.get(NetworkEventOutboxRecord, outbox_id)
            if row is None or row.claim_token != claim_token:
                return
            row.status = "published"
            row.message_id = message_id
            row.published_at = datetime.now(timezone.utc)
            row.claim_token = None
            row.lease_until = None
            row.updated_at = datetime.now(timezone.utc)

    def mark_event_outbox_failed(
        self,
        outbox_id: str,
        error: str,
        *,
        claim_token: str,
        max_attempts: int = 8,
        base_backoff_seconds: int = 5,
    ) -> None:
        with get_db_session() as db:
            row = db.get(NetworkEventOutboxRecord, outbox_id)
            if row is None or row.claim_token != claim_token:
                return
            row.attempts = int(row.attempts or 0) + 1
            row.last_error = str(error)[:4000]
            now = datetime.now(timezone.utc)
            row.claim_token = None
            row.lease_until = None
            if row.attempts >= max(1, int(max_attempts)):
                row.status = "dead_letter"
                row.dead_letter_at = now
            else:
                row.status = "retry_wait"
                delay = min(
                    3600,
                    max(1, int(base_backoff_seconds)) * (2 ** (row.attempts - 1)),
                )
                row.available_at = now + timedelta(seconds=delay)
            row.updated_at = now

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
                    "correlation_key": row.correlation_key,
                    "correlation_dimensions": row.correlation_dimensions_json or {},
                    "source_change_id": row.source_change_id,
                    "run_id": row.run_id,
                    "latest_change_id": row.latest_change_id,
                    "last_seen_run_id": row.last_seen_run_id,
                    "first_seen_at": row.first_seen_at.isoformat(),
                    "last_seen_at": row.last_seen_at.isoformat(),
                    "occurrence_count": row.occurrence_count,
                    "occurred_at": row.occurred_at.isoformat(),
                    "status": row.status,
                    "payload": row.payload_json,
                }
                for row in rows
            ]

    def list_event_occurrences(
        self,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(NetworkEventOccurrenceRecord)
            if run_id:
                query = query.filter(NetworkEventOccurrenceRecord.run_id == run_id)
            if event_id:
                query = query.filter(NetworkEventOccurrenceRecord.event_id == event_id)
            rows = query.order_by(NetworkEventOccurrenceRecord.observed_at).all()
            return [row.to_dict() for row in rows]
