"""Materialized patrol batch summary and report cache."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkChangeRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    PatrolBatchSummaryProjection,
    PatrolCollectionResult,
    StateObservationRecord,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _stable_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _find_nested_values(payload: Any, key: str) -> list[Any]:
    values: list[Any] = []
    if isinstance(payload, dict):
        if key in payload and payload[key] is not None:
            values.append(payload[key])
        for value in payload.values():
            values.extend(_find_nested_values(value, key))
    elif isinstance(payload, list):
        for value in payload:
            values.extend(_find_nested_values(value, key))
    return values


def patrol_batch_fact_revision(runs: list[Any]) -> str:
    """Fingerprint every persisted fact that contributes to a batch report."""
    workflow_run_ids = sorted({str(run.id) for run in runs})
    if not workflow_run_ids:
        return _stable_digest([])

    with get_db_session() as db:
        steps = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.run_id.in_(workflow_run_ids))
            .order_by(WorkflowStepRecord.run_id, WorkflowStepRecord.step_index)
            .all()
        )
        snapshot_run_ids = sorted(
            {
                str(value)
                for step in steps
                for value in _find_nested_values(step.result or {}, "snapshot_run_id")
                if value
            }
        )
        payload: dict[str, Any] = {
            "steps": [(step.id, step.status, step.completed_at, step.result) for step in steps],
            "snapshot_run_ids": snapshot_run_ids,
        }
        if not snapshot_run_ids:
            return _stable_digest(payload)

        payload["snapshots"] = (
            db.query(
                CommandSnapshot.id,
                CommandSnapshot.raw_text_hash,
                CommandSnapshot.parse_status,
                CommandSnapshot.output_status,
                CommandSnapshot.parser_template_id,
                CommandSnapshot.parser_version,
                CommandSnapshot.contract_version,
            )
            .filter(CommandSnapshot.run_id.in_(snapshot_run_ids))
            .order_by(CommandSnapshot.id)
            .all()
        )
        payload["collection_results"] = (
            db.query(
                PatrolCollectionResult.id,
                PatrolCollectionResult.status,
                PatrolCollectionResult.execution_status,
                PatrolCollectionResult.parse_status,
                PatrolCollectionResult.output_status,
                PatrolCollectionResult.fact_status,
                PatrolCollectionResult.normalization_status,
                PatrolCollectionResult.updated_at,
            )
            .filter(PatrolCollectionResult.run_id.in_(snapshot_run_ids))
            .order_by(PatrolCollectionResult.id)
            .all()
        )
        payload["observations"] = (
            db.query(
                StateObservationRecord.id,
                StateObservationRecord.value_json,
                StateObservationRecord.value_text,
                StateObservationRecord.value_number,
                StateObservationRecord.observation_type,
                StateObservationRecord.parser_template_id,
                StateObservationRecord.contract_version,
            )
            .filter(StateObservationRecord.run_id.in_(snapshot_run_ids))
            .order_by(StateObservationRecord.id)
            .all()
        )
        payload["changes"] = (
            db.query(
                NetworkChangeRecord.id,
                NetworkChangeRecord.old_value_json,
                NetworkChangeRecord.new_value_json,
                NetworkChangeRecord.event_status,
                NetworkChangeRecord.decision_reason,
                NetworkChangeRecord.change_policy_id,
                NetworkChangeRecord.change_policy_version_id,
                NetworkChangeRecord.analysis_revision,
                NetworkChangeRecord.rule_bundle_digest,
            )
            .filter(NetworkChangeRecord.run_id.in_(snapshot_run_ids))
            .order_by(NetworkChangeRecord.id)
            .all()
        )
        occurrences = (
            db.query(
                NetworkEventOccurrenceRecord.id,
                NetworkEventOccurrenceRecord.event_id,
                NetworkEventOccurrenceRecord.source_change_id,
                NetworkEventOccurrenceRecord.occurrence_kind,
                NetworkEventOccurrenceRecord.severity,
                NetworkEventOccurrenceRecord.observed_at,
                NetworkEventOccurrenceRecord.payload_json,
            )
            .filter(NetworkEventOccurrenceRecord.run_id.in_(snapshot_run_ids))
            .order_by(NetworkEventOccurrenceRecord.id)
            .all()
        )
        payload["event_occurrences"] = occurrences
        event_ids = sorted({str(row.event_id) for row in occurrences if row.event_id})
        if event_ids:
            payload["event_identities"] = (
                db.query(
                    NetworkEventRecord.id,
                    NetworkEventRecord.event_type,
                    NetworkEventRecord.device_id,
                    NetworkEventRecord.entity_type,
                    NetworkEventRecord.entity_key,
                    NetworkEventRecord.correlation_key,
                )
                .filter(NetworkEventRecord.id.in_(event_ids))
                .order_by(NetworkEventRecord.id)
                .all()
            )
        legacy_query = db.query(
            NetworkEventRecord.id,
            NetworkEventRecord.status,
            NetworkEventRecord.severity,
            NetworkEventRecord.occurrence_count,
            NetworkEventRecord.last_seen_at,
            NetworkEventRecord.latest_change_id,
            NetworkEventRecord.payload_json,
        ).filter(NetworkEventRecord.run_id.in_(snapshot_run_ids))
        if event_ids:
            legacy_query = legacy_query.filter(~NetworkEventRecord.id.in_(event_ids))
        payload["legacy_events"] = legacy_query.order_by(NetworkEventRecord.id).all()
    return _stable_digest(payload)


def patrol_batch_source_digest(
    runs: list[Any],
    *,
    fact_revision: str | None = None,
) -> str:
    def iso_attr(run: Any, name: str) -> str | None:
        value = getattr(run, name, None)
        if not value:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    payload = [
        {
            "id": str(run.id),
            "status": str(run.status or ""),
            "step": int(getattr(run, "current_step_index", 0) or 0),
            "updated_at": iso_attr(run, "updated_at") or iso_attr(run, "created_at"),
            "completed_at": iso_attr(run, "completed_at"),
        }
        for run in sorted(runs, key=lambda item: str(item.id))
    ]
    return _stable_digest({"runs": payload, "fact_revision": fact_revision})


class PatrolBatchProjectionRepository:
    def get(self, patrol_batch_id: str, *, source_digest: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(PatrolBatchSummaryProjection, patrol_batch_id)
            if row is None or row.source_digest != source_digest:
                return None
            return {
                "markdown": str(row.markdown_text),
                "summary": dict(row.summary_json or {}),
                "object_key": row.object_key,
                "generated_at": row.generated_at,
            }

    def get_markdown(self, patrol_batch_id: str, *, source_digest: str) -> str | None:
        row = self.get(patrol_batch_id, source_digest=source_digest)
        return str(row["markdown"]) if row else None

    def upsert(
        self,
        *,
        patrol_batch_id: str,
        source_digest: str,
        summary: dict[str, Any],
        markdown: str,
        generated_at: datetime,
        object_key: str | None = None,
    ) -> None:
        values = {
            "patrol_batch_id": patrol_batch_id,
            "source_digest": source_digest,
            "status": str(summary.get("status") or "unknown"),
            "run_count": int(summary.get("run_count") or 0),
            "summary_json": summary,
            "markdown_text": markdown,
            "object_key": object_key,
            "generated_at": generated_at,
            "updated_at": datetime.now(timezone.utc),
        }
        statement = pg_insert(PatrolBatchSummaryProjection).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["patrol_batch_id"],
            set_={key: value for key, value in values.items() if key != "patrol_batch_id"},
        )
        with get_db_session() as db:
            db.execute(statement)


class MaterializePatrolBatchReportHandler:
    """Return a current report and materialize it for subsequent reads."""

    def __init__(
        self,
        *,
        summary_builder: Callable[[str, list[Any]], dict[str, Any]],
        renderer: Callable[..., str],
        repository: PatrolBatchProjectionRepository | None = None,
        object_storage: Any | None = None,
        source_revision_provider: Callable[[list[Any]], str] | None = None,
        projection_version: str | None = None,
    ) -> None:
        self._summary_builder = summary_builder
        self._renderer = renderer
        self._repository = repository or PatrolBatchProjectionRepository()
        self._object_storage = object_storage
        self._source_revision_provider = source_revision_provider or patrol_batch_fact_revision
        self._projection_version = projection_version

    def handle(
        self,
        patrol_batch_id: str,
        runs: list[Any],
        *,
        generated_at: datetime | None = None,
    ) -> str:
        fact_revision = self._source_revision_provider(runs)
        if self._projection_version:
            fact_revision = f"{self._projection_version}|{fact_revision}"
        source_digest = patrol_batch_source_digest(
            runs,
            fact_revision=fact_revision,
        )
        materialized = None
        try:
            if hasattr(self._repository, "get"):
                materialized = self._repository.get(
                    patrol_batch_id,
                    source_digest=source_digest,
                )
                cached = str(materialized["markdown"]) if materialized else None
            else:
                cached = self._repository.get_markdown(
                    patrol_batch_id,
                    source_digest=source_digest,
                )
        except Exception:
            cached = None
        if cached is not None and (
            self._object_storage is None or (materialized or {}).get("object_key")
        ):
            return cached
        generated_at = generated_at or datetime.now(timezone.utc)
        if cached is not None and materialized is not None:
            summary = dict(materialized.get("summary") or {})
            markdown = cached
            generated_at = materialized.get("generated_at") or generated_at
        else:
            summary = self._summary_builder(patrol_batch_id, runs)
            markdown = self._renderer(
                patrol_batch_id,
                summary,
                generated_at=generated_at,
            )
        object_key = None
        if self._object_storage is not None:
            candidate_key = f"reports/patrol/{patrol_batch_id}/{source_digest}.md"
            uploaded = self._object_storage.upload_file(
                candidate_key,
                markdown.encode("utf-8"),
                content_type="text/markdown; charset=utf-8",
            )
            if not uploaded:
                raise RuntimeError("failed to upload patrol batch report artifact")
            object_key = candidate_key
        try:
            self._repository.upsert(
                patrol_batch_id=patrol_batch_id,
                source_digest=source_digest,
                summary=summary,
                markdown=markdown,
                generated_at=generated_at,
                object_key=object_key,
            )
        except Exception:
            if (
                object_key
                and self._object_storage is not None
                and hasattr(self._object_storage, "delete_object")
            ):
                self._object_storage.delete_object(object_key)
            raise
        return markdown
