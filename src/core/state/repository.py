"""PostgreSQL repository for normalized state observations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.evidence import RawEvidenceRepository, evidence_id_for_command_snapshot
from src.core.state.domain import StateObservation
from src.infrastructure.db.models import (
    CommandSnapshot,
    CurrentStateRecord,
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
                query = query.filter(CommandSnapshot.device_id.in_(device_ids))
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
            ).all()
            return [row.to_dict() for row in rows]

    def list_observations_for_runs(
        self,
        run_ids: list[str],
        *,
        device_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        with get_db_session() as db:
            query = db.query(StateObservationRecord).filter(
                StateObservationRecord.run_id.in_(run_ids)
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
            ).all()
            return [row.to_dict() for row in rows]

    def find_previous_observation(self, observation: dict[str, Any]) -> dict[str, Any] | None:
        observed_at = _datetime(observation.get("observed_at"))
        with get_db_session() as db:
            row = (
                db.query(StateObservationRecord)
                .filter(
                    StateObservationRecord.id != observation["id"],
                    StateObservationRecord.run_id != observation["run_id"],
                    StateObservationRecord.mode == observation["mode"],
                    StateObservationRecord.device_id == observation["device_id"],
                    StateObservationRecord.entity_type == observation["entity_type"],
                    StateObservationRecord.entity_key == observation["entity_key"],
                    StateObservationRecord.field_name == observation["field_name"],
                    StateObservationRecord.observed_at <= observed_at,
                )
                .order_by(StateObservationRecord.observed_at.desc())
                .first()
            )
            return row.to_dict() if row else None

    def list_prior_observations_for_run(self, run_id: str) -> list[dict[str, Any]]:
        from src.infrastructure.db.models import PatrolRun

        with get_db_session() as db:
            current_run = db.get(PatrolRun, run_id)
            if current_run is None:
                return []
            rows = (
                db.query(StateObservationRecord)
                .join(PatrolRun, PatrolRun.id == StateObservationRecord.run_id)
                .filter(StateObservationRecord.run_id != run_id)
                .filter(StateObservationRecord.mode == current_run.mode)
                .filter(PatrolRun.started_at <= current_run.started_at)
                .order_by(
                    PatrolRun.started_at.desc(),
                    StateObservationRecord.observed_at.desc(),
                )
                .all()
            )
            return [row.to_dict() for row in rows]

    def save_observations(
        self,
        observations: list[StateObservation],
        *,
        update_current_state: bool,
    ) -> tuple[int, int]:
        created = 0
        current_updates = 0
        with get_db_session() as db:
            for item in observations:
                inserted = self._insert_observation(db, item)
                if not inserted:
                    continue
                created += 1
                if update_current_state:
                    current_updates += self._upsert_current_state(db, item)
        return created, current_updates

    def _observation_values(self, item: StateObservation) -> dict[str, Any]:
        return {
            "id": item.observation_id,
            "idempotency_key": item.idempotency_key,
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
            "entity_type": item.entity_type,
            "entity_key": item.entity_key,
            "field_name": item.field_name,
            "value_json": item.value,
            "value_text": item.value_text,
            "value_number": item.value_number,
            "value_type": item.value_type,
            "observation_kind": item.observation_kind,
            "observed_at": _datetime(item.observed_at),
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
        now = datetime.now(timezone.utc)
        incoming_observed_at = _datetime(item.observed_at)
        values = {
            "id": item.observation_id,
            "state_key": item.state_key,
            "device_id": item.device_id,
            "vendor": item.vendor,
            "model": item.model,
            "entity_type": item.entity_type,
            "entity_key": item.entity_key,
            "field_name": item.field_name,
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
            "version": 1,
            "confidence": item.confidence,
            "metadata_json": item.metadata or {},
            "created_at": now,
            "updated_at": now,
        }
        stmt = pg_insert(CurrentStateRecord).values(**values)
        excluded = stmt.excluded
        update_values = {
            "device_id": excluded.device_id,
            "vendor": excluded.vendor,
            "model": excluded.model,
            "entity_type": excluded.entity_type,
            "entity_key": excluded.entity_key,
            "field_name": excluded.field_name,
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
            "version": CurrentStateRecord.version + 1,
            "confidence": excluded.confidence,
            "metadata_json": excluded.metadata_json,
            "updated_at": excluded.updated_at,
        }
        stmt = (
            stmt.on_conflict_do_update(
                index_elements=["state_key"],
                set_=update_values,
                where=excluded.observed_at > CurrentStateRecord.observed_at,
            )
            .returning(CurrentStateRecord.id)
        )
        return 1 if db.execute(stmt).scalar_one_or_none() is not None else 0
