"""PostgreSQL repository for normalized state observations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

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

    def list_run_snapshot_ids(self, run_id: str) -> list[str]:
        with get_db_session() as db:
            return [
                row[0]
                for row in db.query(CommandSnapshot.id)
                .filter(CommandSnapshot.run_id == run_id)
                .order_by(CommandSnapshot.device_id, CommandSnapshot.canonical_command)
                .all()
            ]

    def list_run_observations(self, run_id: str) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(StateObservationRecord)
                .filter(StateObservationRecord.run_id == run_id)
                .order_by(
                    StateObservationRecord.device_id,
                    StateObservationRecord.entity_type,
                    StateObservationRecord.entity_key,
                    StateObservationRecord.field_name,
                    StateObservationRecord.observed_at,
                )
                .all()
            )
            return [row.to_dict() for row in rows]

    def list_observations_for_runs(self, run_ids: list[str]) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        with get_db_session() as db:
            rows = (
                db.query(StateObservationRecord)
                .filter(StateObservationRecord.run_id.in_(run_ids))
                .order_by(
                    StateObservationRecord.run_id,
                    StateObservationRecord.device_id,
                    StateObservationRecord.entity_type,
                    StateObservationRecord.entity_key,
                    StateObservationRecord.field_name,
                    StateObservationRecord.observed_at,
                )
                .all()
            )
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
                if (
                    db.query(StateObservationRecord.id)
                    .filter(
                        StateObservationRecord.idempotency_key == item.idempotency_key
                    )
                    .first()
                ):
                    continue
                db.add(
                    StateObservationRecord(
                        id=item.observation_id,
                        idempotency_key=item.idempotency_key,
                        run_id=item.run_id,
                        snapshot_id=item.snapshot_id,
                        dataset_id=item.dataset_id,
                        mode=item.mode,
                        source=item.source,
                        source_type=item.source_type,
                        evidence_id=item.evidence_id,
                        schema_id=item.schema_id,
                        device_id=item.device_id,
                        vendor=item.vendor,
                        model=item.model,
                        canonical_command=item.canonical_command,
                        entity_type=item.entity_type,
                        entity_key=item.entity_key,
                        field_name=item.field_name,
                        value_json=item.value,
                        value_text=item.value_text,
                        value_number=item.value_number,
                        value_type=item.value_type,
                        observation_kind=item.observation_kind,
                        observed_at=_datetime(item.observed_at),
                        parser_template_id=item.parser_template_id,
                        parser_version=item.parser_version,
                        contract_version=item.contract_version,
                        confidence=item.confidence,
                        metadata_json=item.metadata or {},
                    )
                )
                created += 1
                if update_current_state:
                    current_updates += self._upsert_current_state(db, item)
            try:
                db.flush()
            except IntegrityError:
                # Let the session rollback and surface the original data problem.
                raise
        return created, current_updates

    def _upsert_current_state(self, db, item: StateObservation) -> int:
        row = (
            db.query(CurrentStateRecord)
            .filter(CurrentStateRecord.state_key == item.state_key)
            .first()
        )
        now = datetime.now(timezone.utc)
        if row is None:
            row = CurrentStateRecord(
                id=item.observation_id,
                state_key=item.state_key,
                created_at=now,
            )
            db.add(row)
        row.device_id = item.device_id
        row.vendor = item.vendor
        row.model = item.model
        row.entity_type = item.entity_type
        row.entity_key = item.entity_key
        row.field_name = item.field_name
        row.value_json = item.value
        row.value_text = item.value_text
        row.value_number = item.value_number
        row.value_type = item.value_type
        row.observation_kind = item.observation_kind
        row.source = item.source
        row.source_type = item.source_type
        row.source_run_id = item.run_id
        row.source_snapshot_id = item.snapshot_id
        row.source_evidence_id = item.evidence_id
        row.schema_id = item.schema_id
        row.observed_at = _datetime(item.observed_at)
        row.version = int(row.version or 0) + 1
        row.confidence = item.confidence
        row.metadata_json = item.metadata or {}
        row.updated_at = now
        return 1
