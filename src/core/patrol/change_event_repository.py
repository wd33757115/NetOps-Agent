"""PostgreSQL repository for derived network changes and events."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
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


def change_idempotency_key(change: Any) -> str:
    lineage = (
        getattr(change, "check_item_id", None),
        getattr(change, "check_item_version_id", None),
        getattr(change, "check_execution_id", None),
    )
    scoped_prefix = lineage if any(lineage) else ()
    return _digest(
        (*scoped_prefix,
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
            getattr(change, "analysis_revision", "original"),
            getattr(change, "rule_bundle_digest", None),
        )
    )


def deterministic_change_id(change: Any) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, change_idempotency_key(change)))


def _event_class(event: Any) -> str:
    rule = dict((event.payload or {}).get("rule") or {})
    return str(rule.get("event_class") or "fault")


def _event_order_key(event: Any) -> str:
    event_class_rank = "1" if _event_class(event) == "recovery" else "0"
    return f"{event_class_rank}|{event.source_change_id or event.event_id}"


def _event_not_older(event: Any, row: NetworkEventRecord) -> bool:
    candidate = _datetime(event.occurred_at)
    current = _datetime(row.last_seen_at)
    return (candidate, _event_order_key(event)) >= (
        current,
        str(row.last_seen_order_key or ""),
    )


ACTIVE_EVENT_STATUSES = ("open", "acknowledged", "recovering")


def close_events_in_session(
    db: Any,
    *,
    event_types: Iterable[str],
    reason: str,
    note: str,
    actor: str,
    now: datetime,
) -> dict[str, int]:
    """Close the events still open under ``event_types``, whose rules no longer run.

    Nothing recovered, so no occurrence is recorded and nothing is published;
    each aggregate carries why it was closed and by whom. Joins the caller's
    transaction. Returns how many were closed per event type.
    """

    wanted = sorted({str(item) for item in event_types if item})
    closed: dict[str, int] = {}
    if not wanted:
        return closed
    rows = (
        db.query(NetworkEventRecord)
        .filter(NetworkEventRecord.event_type.in_(wanted))
        .filter(NetworkEventRecord.status.in_(ACTIVE_EVENT_STATUSES))
        .with_for_update()
        .all()
    )
    for row in rows:
        payload = dict(row.payload_json or {})
        payload["resolved_at"] = now.isoformat()
        payload["closed_reason"] = reason
        payload["closed_note"] = note
        payload["closed_by"] = actor
        row.payload_json = payload
        row.status = "resolved"
        closed[str(row.event_type)] = closed.get(str(row.event_type), 0) + 1
    db.flush()
    return closed


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
            decision_values = [self._decision_values(decision) for decision in decisions or []]
            for offset in range(0, len(decision_values), 500):
                db.execute(
                    pg_insert(NetworkChangeDecisionRecord)
                    .values(decision_values[offset : offset + 500])
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                )
            change_values: list[dict[str, Any]] = []
            for change in changes:
                key = change_idempotency_key(change)
                change_values.append(
                    {
                        "id": deterministic_change_id(change),
                        "idempotency_key": key,
                        "run_id": change.run_id,
                        "check_item_id": getattr(
                            change, "check_item_id", None
                        ),
                        "check_item_version_id": getattr(
                            change, "check_item_version_id", None
                        ),
                        "check_execution_id": getattr(
                            change, "check_execution_id", None
                        ),
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
                        "analysis_revision": getattr(change, "analysis_revision", "original"),
                        "rule_bundle_digest": getattr(change, "rule_bundle_digest", None),
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
            for offset in range(0, len(change_values), 500):
                stmt = (
                    pg_insert(NetworkChangeRecord)
                    .values(change_values[offset : offset + 500])
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                    .returning(NetworkChangeRecord.id)
                )
                created += len(db.execute(stmt).all())
        return created

    def canonical_change_ids(self, changes: list[Any]) -> dict[str, str]:
        keys = {change_idempotency_key(change) for change in changes}
        if not keys:
            return {}
        result: dict[str, str] = {}
        ordered = sorted(keys)
        with get_db_session() as db:
            for offset in range(0, len(ordered), 1000):
                rows = (
                    db.query(
                        NetworkChangeRecord.idempotency_key,
                        NetworkChangeRecord.id,
                    )
                    .filter(
                        NetworkChangeRecord.idempotency_key.in_(ordered[offset : offset + 1000])
                    )
                    .all()
                )
                result.update({str(key): str(change_id) for key, change_id in rows})
        return result

    def list_changes(
        self,
        *,
        run_id: str | None = None,
        device_id: str | None = None,
        analysis_revision: str | None = None,
        rule_bundle_digest: str | None = None,
        change_ids: list[str] | set[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_change_ids: list[str] | None = None
        if change_ids is not None:
            normalized_change_ids = sorted(
                {str(change_id) for change_id in change_ids if str(change_id)}
            )
            if not normalized_change_ids:
                return []
        with get_db_session() as db:
            query = db.query(NetworkChangeRecord)
            if run_id:
                query = query.filter(NetworkChangeRecord.run_id == run_id)
            if device_id:
                query = query.filter(NetworkChangeRecord.device_id == device_id)
            if analysis_revision is not None:
                query = query.filter(NetworkChangeRecord.analysis_revision == analysis_revision)
            if rule_bundle_digest is not None:
                query = query.filter(NetworkChangeRecord.rule_bundle_digest == rule_bundle_digest)
            if normalized_change_ids is not None:
                query = query.filter(NetworkChangeRecord.id.in_(normalized_change_ids))
            rows = query.order_by(NetworkChangeRecord.detected_at).all()
            return [
                {
                    "change_id": row.id,
                    "run_id": row.run_id,
                    "check_item_id": row.check_item_id,
                    "check_item_version_id": row.check_item_version_id,
                    "check_execution_id": row.check_execution_id,
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
                    "analysis_revision": row.analysis_revision,
                    "rule_bundle_digest": row.rule_bundle_digest,
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
        decision_reasons: dict[str, str] | None = None,
    ) -> int:
        if not change_ids:
            return 0
        updated = 0
        with get_db_session() as db:
            for offset in range(0, len(change_ids), 1000):
                query = db.query(NetworkChangeRecord).filter(
                    NetworkChangeRecord.id.in_(change_ids[offset : offset + 1000])
                )
                if only_pending:
                    query = query.filter(NetworkChangeRecord.event_status == "pending")
                rows = query.all()
                for row in rows:
                    row.event_status = status
                    if decision_reasons and str(row.id) in decision_reasons:
                        row.decision_reason = decision_reasons[str(row.id)]
                updated += len(rows)
        return updated

    def save_events(self, events: list[Any]) -> int:
        created = 0
        with get_db_session() as db:
            required_locks = sorted(
                {lock for event in events for lock in self._required_event_correlations(event)}
            )
            for event_type, correlation_key in required_locks:
                self._lock_correlation_identity(db, event_type, correlation_key)
            for event in events:
                if self._resolve_recovered_event(db, event):
                    if event.source_change_id:
                        change = db.get(NetworkChangeRecord, event.source_change_id)
                        if change is not None:
                            change.event_status = "event_created"
                    continue
                correlation_key = str((event.payload or {}).get("correlation_key") or "")
                rule = dict((event.payload or {}).get("rule") or {})
                key = _digest(
                    (
                        event.source_change_id,
                        event.event_type,
                        event.device_id,
                        event.entity_type,
                        event.entity_key,
                        event.run_id,
                        correlation_key,
                        rule.get("rule_id"),
                        rule.get("version"),
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
                    occurrence_id = self._record_event_occurrence(
                        db,
                        active.id,
                        event,
                        occurrence_kind="recurrence",
                        projection_metadata={
                            "aggregate_status_after_occurrence": active.status,
                        },
                    )
                    if occurrence_id is None:
                        continue
                    observed_at = _datetime(event.occurred_at)
                    if _event_not_older(event, active):
                        payload = dict(active.payload_json or {})
                        payload["latest_change"] = event.payload.get("change")
                        payload["latest_evaluation"] = event.payload.get("evaluation")
                        payload["latest_rule"] = event.payload.get("rule")
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
                        active.last_seen_at = observed_at
                        active.last_seen_order_key = _event_order_key(event)
                    active.occurrence_count = int(active.occurrence_count or 1) + 1
                    if event.source_change_id:
                        change = db.get(NetworkChangeRecord, event.source_change_id)
                        if change is not None:
                            change.event_status = "event_created"
                    self._enqueue_event(
                        db,
                        event,
                        event_id=active.id,
                        occurrence_id=occurrence_id,
                        publication_kind="recurrence",
                    )
                    continue
                latest = self._find_latest_event(db, event)
                observed_at = _datetime(event.occurred_at)
                if (
                    latest is not None
                    and latest.status == "resolved"
                    and not _event_not_older(event, latest)
                    and _event_class(event) != "recovery"
                ):
                    occurrence_id = self._record_event_occurrence(
                        db,
                        latest.id,
                        event,
                        occurrence_kind="recurrence",
                        projection_metadata={
                            "projection_applied": False,
                            "superseded": True,
                            "superseded_reason": "fault_precedes_recovery_watermark",
                            "aggregate_status_after_occurrence": latest.status,
                        },
                    )
                    if occurrence_id is not None:
                        latest.occurrence_count = int(latest.occurrence_count or 1) + 1
                        self._enqueue_event(
                            db,
                            event,
                            event_id=latest.id,
                            occurrence_id=occurrence_id,
                            publication_kind="recurrence",
                            publication_metadata={
                                "projection_applied": False,
                                "superseded": True,
                                "aggregate_status": latest.status,
                            },
                        )
                    if event.source_change_id:
                        change = db.get(NetworkChangeRecord, event.source_change_id)
                        if change is not None:
                            change.event_status = "event_created"
                    continue
                db.add(
                    NetworkEventRecord(
                        id=event.event_id,
                        idempotency_key=key,
                        check_item_id=getattr(
                            event, "check_item_id", None
                        ),
                        check_item_version_id=getattr(
                            event, "check_item_version_id", None
                        ),
                        check_execution_id=getattr(
                            event, "check_execution_id", None
                        ),
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
                        last_seen_order_key=_event_order_key(event),
                        occurrence_count=1,
                        status=("resolved" if _event_class(event) == "recovery" else event.status),
                        payload_json=self._event_payload(event),
                        created_at=datetime.now(timezone.utc),
                    )
                )
                db.flush()
                occurrence_id = self._record_event_occurrence(
                    db,
                    event.event_id,
                    event,
                    occurrence_kind="opening",
                    projection_metadata={
                        "aggregate_status_after_occurrence": (
                            "resolved" if _event_class(event) == "recovery" else event.status
                        ),
                    },
                )
                if occurrence_id is None:
                    raise RuntimeError("opening event occurrence was not persisted")
                self._enqueue_event(
                    db,
                    event,
                    event_id=event.event_id,
                    occurrence_id=occurrence_id,
                    publication_kind="opening",
                )
                if event.source_change_id:
                    change = db.get(NetworkChangeRecord, event.source_change_id)
                    if change is not None:
                        change.event_status = "event_created"
                created += 1
        return created

    def _record_event_occurrence(
        self,
        db,
        event_id: str,
        event: Any,
        *,
        occurrence_kind: str,
        projection_metadata: dict[str, Any] | None = None,
    ) -> str | None:
        if occurrence_kind not in {"opening", "recurrence", "recovery"}:
            raise ValueError(f"unsupported event occurrence kind: {occurrence_kind}")
        key = _digest(
            (
                event_id,
                event.source_change_id,
                event.run_id,
                event.event_type,
                event.occurred_at,
                occurrence_kind,
            )
        )
        payload = self._event_payload(event)
        payload.update(projection_metadata or {})
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
                occurrence_kind=occurrence_kind,
                payload_json=payload,
                created_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(NetworkEventOccurrenceRecord.id)
        )
        return db.execute(stmt).scalar_one_or_none()

    def _decision_values(self, decision: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(decision.get("metadata") or {})
        lineage = (
            metadata.get("check_item_version_id"),
            metadata.get("check_execution_id"),
        )
        scoped_suffix = lineage if any(lineage) else ()
        key = _digest(
            (
                decision.get("run_id"),
                decision.get("previous_observation_id"),
                decision.get("current_observation_id"),
                decision.get("decision"),
                decision.get("reason"),
                decision.get("change_policy_version_id"),
                decision.get("analysis_revision"),
                decision.get("rule_bundle_digest"),
                *scoped_suffix,
            )
        )
        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
            "idempotency_key": key,
            "run_id": str(decision.get("run_id") or ""),
            "device_id": decision.get("device_id"),
            "canonical_command": decision.get("canonical_command"),
            "entity_type": str(decision.get("entity_type") or "record"),
            "entity_key": str(decision.get("entity_key") or ""),
            "field": str(decision.get("field") or ""),
            "observation_type": decision.get("observation_type"),
            "previous_observation_id": decision.get("previous_observation_id"),
            "current_observation_id": decision.get("current_observation_id"),
            "decision": str(decision.get("decision") or "invalid"),
            "reason": decision.get("reason"),
            "change_policy_id": decision.get("change_policy_id"),
            "change_policy_version_id": decision.get("change_policy_version_id"),
            "comparison_type": decision.get("comparison_type"),
            "old_value_json": decision.get("old"),
            "new_value_json": decision.get("new"),
            "observed_at": (
                _datetime(decision.get("observed_at")) if decision.get("observed_at") else None
            ),
            "expires_at": (
                _datetime(decision.get("expires_at")) if decision.get("expires_at") else None
            ),
            "metadata_json": {
                **dict(decision.get("metadata") or {}),
                "analysis_revision": decision.get("analysis_revision") or "original",
                "rule_bundle_digest": decision.get("rule_bundle_digest"),
            },
            "created_at": datetime.now(timezone.utc),
        }

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
        payload.setdefault("event_type", event.event_type)
        payload.setdefault("severity", event.severity)
        payload.setdefault(
            "check_item_id",
            getattr(event, "check_item_id", None),
        )
        payload.setdefault(
            "check_item_version_id",
            getattr(event, "check_item_version_id", None),
        )
        payload.setdefault(
            "check_execution_id",
            getattr(event, "check_execution_id", None),
        )
        payload.setdefault("device_id", event.device_id)
        payload.setdefault("entity_type", event.entity_type)
        payload.setdefault("entity_key", event.entity_key)
        payload.setdefault("occurred_at", event.occurred_at)
        return payload

    def _enqueue_event(
        self,
        db,
        event: Any,
        *,
        event_id: str,
        occurrence_id: str,
        publication_kind: str,
        publication_metadata: dict[str, Any] | None = None,
    ) -> None:
        if bool((event.payload or {}).get("historical_backfill")):
            return
        if (
            db.query(NetworkEventOutboxRecord.id)
            .filter(NetworkEventOutboxRecord.occurrence_id == occurrence_id)
            .first()
        ):
            return
        now = datetime.now(timezone.utc)
        payload = event.to_dict()
        payload["event_id"] = event_id
        payload["occurrence_id"] = occurrence_id
        payload["publication_kind"] = publication_kind
        payload["status"] = "resolved" if _event_class(event) == "recovery" else event.status
        payload.update(publication_metadata or {})
        db.add(
            NetworkEventOutboxRecord(
                id=str(uuid.uuid4()),
                event_id=event_id,
                occurrence_id=occurrence_id,
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
            .with_for_update()
            .first()
        )

    def _find_latest_event(self, db, event: Any) -> NetworkEventRecord | None:
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
            .order_by(NetworkEventRecord.last_seen_at.desc())
            .with_for_update()
            .first()
        )

    @staticmethod
    def _lock_event_correlation(db, event_type: str, event: Any) -> None:
        payload = event.payload or {}
        keys = {
            str(value)
            for value in (
                payload.get("correlation_key"),
                payload.get("legacy_correlation_key"),
            )
            if value
        }
        for correlation_key in sorted(keys):
            digest = hashlib.sha256(
                f"network-event-v1|{event_type}|{correlation_key}".encode()
            ).digest()
            lock_id = int.from_bytes(digest[:8], byteorder="big", signed=True)
            db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )

    @staticmethod
    def _required_event_correlations(event: Any) -> set[tuple[str, str]]:
        payload = event.payload or {}
        keys = {
            str(value)
            for value in (
                payload.get("correlation_key"),
                payload.get("legacy_correlation_key"),
            )
            if value
        }
        rule = dict(payload.get("rule") or {})
        recovery = dict(rule.get("recovery") or {})
        target_types = recovery.get("resolves") or []
        if isinstance(target_types, str):
            target_types = [target_types]
        event_types = {str(event.event_type), *(str(item) for item in target_types)}
        return {
            (event_type, correlation_key) for event_type in event_types for correlation_key in keys
        }

    @staticmethod
    def _lock_correlation_identity(db, event_type: str, correlation_key: str) -> None:
        digest = hashlib.sha256(
            f"network-event-v1|{event_type}|{correlation_key}".encode()
        ).digest()
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": int.from_bytes(digest[:8], byteorder="big", signed=True)},
        )

    @classmethod
    def _lock_required_event_correlations(cls, db, event: Any) -> None:
        for event_type, correlation_key in sorted(cls._required_event_correlations(event)):
            cls._lock_correlation_identity(db, event_type, correlation_key)

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
                .with_for_update()
                .all()
            )
            for row in rows:
                recovered_at = _datetime(event.occurred_at)
                projection_applied = _event_not_older(event, row)
                occurrence_id = self._record_event_occurrence(
                    db,
                    row.id,
                    event,
                    occurrence_kind="recovery",
                    projection_metadata={
                        "projection_applied": projection_applied,
                        "superseded": not projection_applied,
                        "superseded_reason": (
                            None
                            if projection_applied
                            else "recovery_precedes_latest_fault_watermark"
                        ),
                        "aggregate_status_after_occurrence": (
                            "resolved" if projection_applied else row.status
                        ),
                    },
                )
                if occurrence_id is not None:
                    self._enqueue_event(
                        db,
                        event,
                        event_id=row.id,
                        occurrence_id=occurrence_id,
                        publication_kind="recovery",
                        publication_metadata={
                            "projection_applied": projection_applied,
                            "superseded": not projection_applied,
                            "aggregate_status": ("resolved" if projection_applied else row.status),
                        },
                    )
                    row.occurrence_count = int(row.occurrence_count or 1) + 1
                    if projection_applied:
                        row.latest_change_id = event.source_change_id
                        row.last_seen_run_id = event.run_id
                        row.last_seen_at = recovered_at
                        row.last_seen_order_key = _event_order_key(event)
                if projection_applied:
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
                    "check_item_id": row.check_item_id,
                    "check_item_version_id": row.check_item_version_id,
                    "check_execution_id": row.check_execution_id,
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

    def list_run_event_facts(
        self,
        *,
        run_id: str,
        device_id: str | None = None,
        source_change_ids: list[str] | set[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the event lifecycle facts produced by one patrol run."""

        with get_db_session() as db:
            query = (
                db.query(NetworkEventOccurrenceRecord, NetworkEventRecord)
                .join(
                    NetworkEventRecord,
                    NetworkEventRecord.id == NetworkEventOccurrenceRecord.event_id,
                )
                .filter(NetworkEventOccurrenceRecord.run_id == run_id)
            )
            if device_id:
                query = query.filter(NetworkEventRecord.device_id == device_id)
            if source_change_ids is not None:
                normalized_change_ids = sorted(
                    {str(change_id) for change_id in source_change_ids if str(change_id)}
                )
                if not normalized_change_ids:
                    return []
                query = query.filter(
                    NetworkEventOccurrenceRecord.source_change_id.in_(normalized_change_ids)
                )
            rows = query.order_by(
                NetworkEventOccurrenceRecord.observed_at,
                NetworkEventOccurrenceRecord.id,
            ).all()
            facts: list[dict[str, Any]] = []
            for occurrence, event in rows:
                payload = dict(occurrence.payload_json or {})
                status_after_occurrence = payload.get("aggregate_status_after_occurrence")
                if not status_after_occurrence:
                    if occurrence.occurrence_kind == "recovery":
                        status_after_occurrence = "resolved"
                    elif occurrence.occurrence_kind in {"opening", "recurrence"}:
                        status_after_occurrence = "open"
                    else:
                        status_after_occurrence = event.status
                facts.append(
                    {
                        "occurrence_id": occurrence.id,
                        "event_id": event.id,
                        "run_id": occurrence.run_id,
                        "source_change_id": occurrence.source_change_id,
                        "occurrence_kind": occurrence.occurrence_kind,
                        "event_type": payload.get("event_type") or event.event_type,
                        "severity": payload.get("severity") or occurrence.severity,
                        "check_item_id": (
                            payload.get("check_item_id")
                            or event.check_item_id
                        ),
                        "check_item_version_id": (
                            payload.get("check_item_version_id")
                            or event.check_item_version_id
                        ),
                        "check_execution_id": (
                            payload.get("check_execution_id")
                            or event.check_execution_id
                        ),
                        "device_id": payload.get("device_id") or event.device_id,
                        "entity_type": payload.get("entity_type") or event.entity_type,
                        "entity_key": payload.get("entity_key") or event.entity_key,
                        "correlation_key": event.correlation_key,
                        "aggregate_status": event.status,
                        "status": event.status,
                        "status_after_occurrence": status_after_occurrence,
                        "observed_at": occurrence.observed_at.isoformat(),
                        "occurred_at": occurrence.observed_at.isoformat(),
                        "payload": payload,
                    }
                )
            return facts

    def summarize_run_events(
        self,
        *,
        run_id: str,
        device_id: str | None = None,
        source_change_ids: list[str] | set[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        facts = self.list_run_event_facts(
            run_id=run_id,
            device_id=device_id,
            source_change_ids=source_change_ids,
        )
        by_kind: dict[str, int] = {"opening": 0, "recurrence": 0, "recovery": 0}
        for item in facts:
            kind = str(item.get("occurrence_kind") or "")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        latest_by_event: dict[str, dict[str, Any]] = {}
        for item in facts:
            latest_by_event[str(item["event_id"])] = item
        active_statuses = {"open", "acknowledged", "recovering"}
        open_after_run_count = sum(
            str(item.get("status_after_occurrence") or "") in active_statuses
            for item in latest_by_event.values()
        )
        resolved_after_run_count = sum(
            str(item.get("status_after_occurrence") or "") == "resolved"
            for item in latest_by_event.values()
        )
        current_open_event_count = sum(
            str(item.get("aggregate_status") or "") in active_statuses
            for item in latest_by_event.values()
        )
        current_resolved_event_count = sum(
            str(item.get("aggregate_status") or "") == "resolved"
            for item in latest_by_event.values()
        )
        return {
            "persisted_occurrence_count": len(facts),
            "created_event_count": by_kind.get("opening", 0),
            "updated_event_count": by_kind.get("recurrence", 0),
            "recovered_event_count": by_kind.get("recovery", 0),
            "unique_event_count": len(latest_by_event),
            "open_after_run_count": open_after_run_count,
            "resolved_after_run_count": resolved_after_run_count,
            "current_open_event_count": current_open_event_count,
            "current_resolved_event_count": current_resolved_event_count,
            "open_event_count": current_open_event_count,
            "resolved_event_count": current_resolved_event_count,
        }
