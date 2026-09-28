"""PostgreSQL repository for normalized state observations."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.evidence import RawEvidenceRepository, evidence_id_for_command_snapshot
from src.core.state.domain import StateObservation
from src.infrastructure.db.models import (
    CommandSnapshot,
    CurrentStateRecord,
    PatrolCollectionResult,
    PatrolRun,
    StateObservationRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


class StateRepository:
    """Persist immutable observations and the production current-state view."""

    def get_snapshot_context(self, snapshot_id: str) -> dict[str, Any] | None:
        from src.infrastructure.db.models import PatrolRun

        with get_db_session() as db:
            row = (
                db.query(CommandSnapshot, PatrolRun)
                .join(PatrolRun, PatrolRun.id == CommandSnapshot.run_id)
                .filter(CommandSnapshot.id == snapshot_id)
                .first()
            )
            if not row:
                return None
            snapshot, run = row
            RawEvidenceRepository().upsert_cli_command_evidence_row(db, snapshot)
            snapshot_dict = snapshot.to_dict()
            snapshot_dict.update(
                {
                    "evidence_id": evidence_id_for_command_snapshot(snapshot.id),
                    "source_type": "cli",
                    "schema_id": snapshot.parser_template_id,
                }
            )
            return {
                "snapshot": snapshot_dict,
                "mode": run.mode,
                "source": run.source,
                "dataset_id": snapshot.dataset_id or run.dataset_id,
            }

    def list_run_snapshot_ids(
        self,
        run_id: str,
        *,
        device_ids: list[str] | None = None,
    ) -> list[str]:
        with get_db_session() as db:
            query = db.query(CommandSnapshot.id).filter(CommandSnapshot.run_id == run_id)
            if device_ids:
                query = query.filter(
                    or_(
                        CommandSnapshot.inventory_device_id.in_(device_ids),
                        CommandSnapshot.device_id.in_(device_ids),
                    )
                )
            return [
                row[0]
                for row in query.order_by(
                    CommandSnapshot.device_id,
                    CommandSnapshot.canonical_command,
                ).all()
            ]

    def list_run_observations(
        self,
        run_id: str,
        *,
        device_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(StateObservationRecord).filter(
                StateObservationRecord.run_id == run_id
            )
            if device_ids:
                query = query.filter(StateObservationRecord.device_id.in_(device_ids))
            rows = query.order_by(
                StateObservationRecord.device_id,
                StateObservationRecord.entity_type,
                StateObservationRecord.entity_key,
                StateObservationRecord.field_name,
                StateObservationRecord.observed_at,
            ).yield_per(1000)
            return [row.to_dict() for row in rows]

    def list_observations_for_runs(
        self,
        run_ids: list[str],
        *,
        device_ids: list[str] | None = None,
        baseline_only: bool = False,
    ) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        with get_db_session() as db:
            query = db.query(StateObservationRecord).filter(
                StateObservationRecord.run_id.in_(run_ids)
            )
            if baseline_only:
                query = query.join(
                    PatrolRun, PatrolRun.id == StateObservationRecord.run_id
                ).join(
                    PatrolCollectionResult,
                    and_(
                        PatrolCollectionResult.run_id == StateObservationRecord.run_id,
                        PatrolCollectionResult.device_id == StateObservationRecord.device_id,
                        PatrolCollectionResult.execution_item_id
                        == StateObservationRecord.execution_item_id,
                    ),
                ).filter(
                    PatrolRun.baseline_eligible.is_(True),
                    PatrolRun.analysis_status == "completed",
                    PatrolCollectionResult.baseline_eligible.is_(True),
                )
            if device_ids:
                query = query.filter(StateObservationRecord.device_id.in_(device_ids))
            rows = query.order_by(
                StateObservationRecord.run_id,
                StateObservationRecord.device_id,
                StateObservationRecord.entity_type,
                StateObservationRecord.entity_key,
                StateObservationRecord.field_name,
                StateObservationRecord.observed_at,
            ).yield_per(1000)
            return [row.to_dict() for row in rows]

    def find_previous_observation(self, observation: dict[str, Any]) -> dict[str, Any] | None:
        observed_at = _datetime(observation.get("observed_at"))
        with get_db_session() as db:
            row = (
                db.query(StateObservationRecord)
                .join(PatrolRun, PatrolRun.id == StateObservationRecord.run_id)
                .join(
                    PatrolCollectionResult,
                    and_(
                        PatrolCollectionResult.run_id == StateObservationRecord.run_id,
                        PatrolCollectionResult.device_id == StateObservationRecord.device_id,
                        PatrolCollectionResult.execution_item_id
                        == StateObservationRecord.execution_item_id,
                    ),
                )
                .filter(
                    StateObservationRecord.id != observation["id"],
                    StateObservationRecord.run_id != observation["run_id"],
                    StateObservationRecord.mode == observation["mode"],
                    StateObservationRecord.device_id == observation["device_id"],
                    StateObservationRecord.entity_type == observation["entity_type"],
                    StateObservationRecord.entity_key == observation["entity_key"],
                    StateObservationRecord.field_name == observation["field_name"],
                    StateObservationRecord.observed_at <= observed_at,
                    PatrolRun.baseline_eligible.is_(True),
                    PatrolRun.analysis_status == "completed",
                    PatrolCollectionResult.baseline_eligible.is_(True),
                )
                .order_by(StateObservationRecord.observed_at.desc())
                .first()
            )
            return row.to_dict() if row else None

    def list_prior_observations_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with get_db_session() as db:
            current_run = db.get(PatrolRun, run_id)
            if current_run is None:
                return []
            # A run cannot compare state on unrelated devices. Use a SQL subquery
            # rather than loading the complete estate's history into each worker.
            current_devices = db.query(StateObservationRecord.device_id).filter(
                StateObservationRecord.run_id == run_id
            ).union(db.query(PatrolCollectionResult.device_id).filter(
                PatrolCollectionResult.run_id == run_id
            ))
            rows = (
                db.query(StateObservationRecord)
                .join(PatrolRun, PatrolRun.id == StateObservationRecord.run_id)
                .join(
                    PatrolCollectionResult,
                    and_(
                        PatrolCollectionResult.run_id == StateObservationRecord.run_id,
                        PatrolCollectionResult.device_id == StateObservationRecord.device_id,
                        PatrolCollectionResult.execution_item_id
                        == StateObservationRecord.execution_item_id,
                    ),
                )
                .filter(StateObservationRecord.run_id != run_id)
                .filter(StateObservationRecord.device_id.in_(current_devices))
                .filter(StateObservationRecord.mode == current_run.mode)
                .filter(PatrolRun.baseline_eligible.is_(True))
                .filter(PatrolRun.analysis_status == "completed")
                .filter(PatrolCollectionResult.baseline_eligible.is_(True))
                .filter(PatrolRun.completed_at <= current_run.started_at)
                .order_by(
                    PatrolRun.started_at.desc(),
                    StateObservationRecord.observed_at.desc(),
                )
                .yield_per(1000)
            )
            return [row.to_dict() for row in rows]

    def save_observations(
        self,
        observations: list[StateObservation],
        *,
        update_current_state: bool,
    ) -> tuple[int, int]:
        with get_db_session() as db:
            return self.save_observations_in_session(
                db,
                observations,
                update_current_state=update_current_state,
            )

    def save_observations_in_session(
        self,
        db,
        observations: list[StateObservation],
        *,
        update_current_state: bool,
        chunk_size: int = 1000,
    ) -> tuple[int, int]:
        if not observations:
            return 0, 0
        created = 0
        current_updates = 0
        for offset in range(0, len(observations), max(1, int(chunk_size))):
            chunk = observations[offset : offset + max(1, int(chunk_size))]
            values = [self._observation_values(item) for item in chunk]
            stmt = (
                pg_insert(StateObservationRecord.__table__)
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(StateObservationRecord.id)
            )
            # Separate parameters let SQLAlchemy batch RETURNING inserts without
            # constructing/compiling one expression node per field per row.
            inserted_ids = {str(row[0]) for row in db.execute(stmt, values).all()}
            created += len(inserted_ids)
            if update_current_state and inserted_ids:
                inserted = [item for item in chunk if item.observation_id in inserted_ids]
                current_updates += self._bulk_upsert_current_state(db, inserted)
        return created, current_updates

    def _observation_values(self, item: StateObservation) -> dict[str, Any]:
        return {
            "id": item.observation_id,
            "idempotency_key": item.idempotency_key,
            "state_key": item.state_key,
            "run_id": item.run_id,
            "snapshot_id": item.snapshot_id,
            "dataset_id": item.dataset_id,
            "mode": item.mode,
            "source": item.source,
            "source_type": item.source_type,
            "evidence_id": item.evidence_id,
            "schema_id": item.schema_id,
            "device_id": item.device_id,
            "vendor": item.vendor,
            "model": item.model,
            "canonical_command": item.canonical_command,
            "execution_item_id": item.execution_item_id or "",
            "entity_type": item.entity_type,
            "entity_key": item.entity_key,
            "field_name": item.field_name,
            "observation_type": item.observation_type,
            "semantic_type": item.semantic_type,
            "unit": item.unit,
            "dimensions_json": item.dimensions or {},
            "dimension_signature": item.dimension_signature,
            "value_json": item.value,
            "value_text": item.value_text,
            "value_number": item.value_number,
            "value_type": item.value_type,
            "observation_kind": item.observation_kind,
            "observed_at": _datetime(item.observed_at),
            "fact_order_key": item.fact_order_key,
            "parser_template_id": item.parser_template_id,
            "parser_version": item.parser_version,
            "contract_version": item.contract_version,
            "confidence": item.confidence,
            "metadata_json": item.metadata or {},
        }

    def _insert_observation(self, db, item: StateObservation) -> bool:
        stmt = (
            pg_insert(StateObservationRecord)
            .values(**self._observation_values(item))
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(StateObservationRecord.id)
        )
        return db.execute(stmt).scalar_one_or_none() is not None

    def _upsert_current_state(self, db, item: StateObservation) -> int:
        return self._bulk_upsert_current_state(db, [item])

    def _current_state_values(self, item: StateObservation) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        incoming_observed_at = _datetime(item.observed_at)
        return {
            "id": item.observation_id,
            "state_key": item.state_key,
            "device_id": item.device_id,
            "vendor": item.vendor,
            "model": item.model,
            "entity_type": item.entity_type,
            "entity_key": item.entity_key,
            "field_name": item.field_name,
            "observation_type": item.observation_type,
            "semantic_type": item.semantic_type,
            "unit": item.unit,
            "dimensions_json": item.dimensions or {},
            "dimension_signature": item.dimension_signature,
            "value_json": item.value,
            "value_text": item.value_text,
            "value_number": item.value_number,
            "value_type": item.value_type,
            "observation_kind": item.observation_kind,
            "source": item.source,
            "source_type": item.source_type,
            "source_run_id": item.run_id,
            "source_snapshot_id": item.snapshot_id,
            "source_evidence_id": item.evidence_id,
            "schema_id": item.schema_id,
            "observed_at": incoming_observed_at,
            "fact_order_key": item.fact_order_key,
            "source_execution_item_id": item.execution_item_id,
            "source_observation_id": item.observation_id,
            "lifecycle_status": (
                "deleted"
                if (item.metadata or {}).get("presence_state") == "absent"
                else "stale"
                if item.observation_kind == "tombstone"
                else "active"
            ),
            "missing_since": (
                _datetime((item.metadata or {}).get("missing_since"))
                if item.observation_kind == "tombstone"
                else None
            ),
            "missing_count": int((item.metadata or {}).get("missing_count") or 0),
            "version": 1,
            "confidence": item.confidence,
            "metadata_json": item.metadata or {},
            "created_at": now,
            "updated_at": now,
        }

    def _bulk_upsert_current_state(
        self,
        db,
        observations: list[StateObservation],
    ) -> int:
        if not observations:
            return 0
        latest_by_state_key: dict[str, StateObservation] = {}
        for item in observations:
            existing = latest_by_state_key.get(item.state_key)
            if existing is None or (
                _datetime(item.observed_at), item.fact_order_key
            ) > (
                _datetime(existing.observed_at), existing.fact_order_key
            ):
                latest_by_state_key[item.state_key] = item
        observations = list(latest_by_state_key.values())
        values = [self._current_state_values(item) for item in observations]
        stmt = pg_insert(CurrentStateRecord.__table__)
        excluded = stmt.excluded
        update_values = {
            "device_id": excluded.device_id,
            "vendor": excluded.vendor,
            "model": excluded.model,
            "entity_type": excluded.entity_type,
            "entity_key": excluded.entity_key,
            "field_name": excluded.field_name,
            "observation_type": excluded.observation_type,
            "semantic_type": excluded.semantic_type,
            "unit": excluded.unit,
            "dimensions_json": excluded.dimensions_json,
            "dimension_signature": excluded.dimension_signature,
            "value_json": excluded.value_json,
            "value_text": excluded.value_text,
            "value_number": excluded.value_number,
            "value_type": excluded.value_type,
            "observation_kind": excluded.observation_kind,
            "source": excluded.source,
            "source_type": excluded.source_type,
            "source_run_id": excluded.source_run_id,
            "source_snapshot_id": excluded.source_snapshot_id,
            "source_evidence_id": excluded.source_evidence_id,
            "schema_id": excluded.schema_id,
            "observed_at": excluded.observed_at,
            "fact_order_key": excluded.fact_order_key,
            "source_execution_item_id": excluded.source_execution_item_id,
            "source_observation_id": excluded.source_observation_id,
            "lifecycle_status": excluded.lifecycle_status,
            "missing_since": excluded.missing_since,
            "missing_count": excluded.missing_count,
            "version": CurrentStateRecord.version + 1,
            "confidence": excluded.confidence,
            "metadata_json": excluded.metadata_json,
            "updated_at": excluded.updated_at,
        }
        stmt = (
            stmt.on_conflict_do_update(
                index_elements=["state_key"],
                set_=update_values,
                where=or_(
                    excluded.observed_at > CurrentStateRecord.observed_at,
                    and_(
                        excluded.observed_at == CurrentStateRecord.observed_at,
                        excluded.fact_order_key > CurrentStateRecord.fact_order_key,
                    ),
                ),
            )
            .returning(CurrentStateRecord.id)
        )
        result = db.execute(stmt, values)
        if hasattr(result, "all"):
            return len(result.all())
        return int(result.scalar_one_or_none() is not None)

    def build_absence_observation(
        self,
        previous: dict[str, Any],
        *,
        run_id: str,
        snapshot_id: str,
        observed_at: str | datetime | None,
        missing_count: int,
        missing_confirmations: int,
    ) -> StateObservation:
        presence_state = (
            "absent" if missing_count >= missing_confirmations else "missing_candidate"
        )
        missing_since = (
            (previous.get("metadata_json") or {}).get("missing_since")
            or str(observed_at or datetime.now(timezone.utc).isoformat())
        )
        state_parts = [
            previous.get("device_id"),
            previous.get("entity_type"),
            previous.get("entity_key"),
            previous.get("field_name"),
        ]
        state_key = hashlib.sha256(
            json.dumps(
                state_parts,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        idempotency_key = hashlib.sha256(
            f"{run_id}|{state_key}|{presence_state}|{missing_count}".encode()
        ).hexdigest()
        metadata = {
            **dict(previous.get("metadata_json") or {}),
            "presence_state": presence_state,
            "missing_since": missing_since,
            "missing_count": missing_count,
        }
        item = StateObservation(
            observation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)),
            idempotency_key=idempotency_key,
            state_key=state_key,
            run_id=run_id,
            snapshot_id=snapshot_id,
            dataset_id=previous.get("dataset_id"),
            mode=str(previous.get("mode") or "production"),
            source=str(previous.get("source") or "patrol"),
            source_type=str(previous.get("source_type") or "cli"),
            evidence_id=evidence_id_for_command_snapshot(snapshot_id),
            schema_id=previous.get("schema_id"),
            device_id=str(previous.get("device_id") or ""),
            vendor=previous.get("vendor"),
            model=previous.get("model"),
            canonical_command=str(previous.get("canonical_command") or ""),
            entity_type=str(previous.get("entity_type") or "record"),
            entity_key=str(previous.get("entity_key") or ""),
            field_name=str(previous.get("field_name") or ""),
            observation_type=previous.get("observation_type"),
            semantic_type=previous.get("semantic_type"),
            unit=previous.get("unit"),
            dimensions=dict(previous.get("dimensions_json") or {}),
            dimension_signature=previous.get("dimension_signature"),
            value=None,
            value_text=None,
            value_number=None,
            value_type="null",
            observation_kind="tombstone",
            observed_at=str(observed_at or datetime.now(timezone.utc).isoformat()),
            parser_template_id=previous.get("parser_template_id"),
            parser_version=previous.get("parser_version"),
            contract_version=previous.get("contract_version"),
            confidence=float(previous.get("confidence") or 1.0),
            metadata=metadata,
        )
        return item

    def record_absence(self, previous: dict[str, Any], **kwargs) -> StateObservation:
        item = self.build_absence_observation(previous, **kwargs)
        self.save_observations([item], update_current_state=True)
        return item
