"""Repository helpers for raw evidence metadata."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from src.core.evidence.domain import RawEvidence
from src.infrastructure.db.models import CommandSnapshot, RawEvidenceRecord
from src.infrastructure.db.postgres import get_db_session


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def evidence_id_for_command_snapshot(snapshot_id: str) -> str:
    digest = hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()[:32]
    return f"ev-cli-{digest}"


def build_cli_evidence(snapshot: CommandSnapshot) -> RawEvidence:
    """Build the logical RawEvidence view for an existing command snapshot."""
    metadata: dict[str, Any] = {
        "command": snapshot.command,
        "canonical_command": snapshot.canonical_command,
        "parser_template_id": snapshot.parser_template_id,
        "parser_version": snapshot.parser_version,
        "contract_version": snapshot.contract_version,
        "parse_status": snapshot.parse_status,
        "snapshot_kind": snapshot.snapshot_kind,
        "source_file_hash": snapshot.source_file_hash,
        "import_fingerprint": snapshot.import_fingerprint,
    }
    if snapshot.start_line is not None or snapshot.end_line is not None:
        metadata["source_lines"] = {
            "start": snapshot.start_line,
            "end": snapshot.end_line,
        }
    return RawEvidence(
        evidence_id=evidence_id_for_command_snapshot(snapshot.id),
        evidence_key=f"cli_command_snapshot:{snapshot.id}",
        source="cli",
        source_type="cli",
        evidence_type="cli_command_output",
        device_id=snapshot.device_id,
        inventory_device_id=snapshot.inventory_device_id,
        run_id=snapshot.run_id,
        dataset_id=snapshot.dataset_id,
        observed_at=_iso(snapshot.observed_at),
        source_snapshot_id=snapshot.id,
        content_ref=f"command_snapshot:{snapshot.id}",
        content_hash=snapshot.raw_text_hash,
        retention_policy="patrol_raw_output",
        metadata={
            key: value
            for key, value in metadata.items()
            if value is not None
        },
    )


class RawEvidenceRepository:
    """Persist and query source-agnostic evidence metadata."""

    def upsert_cli_command_evidence(
        self,
        *,
        snapshot_id: str,
    ) -> RawEvidence:
        with get_db_session() as db:
            snapshot = db.get(CommandSnapshot, snapshot_id)
            if snapshot is None:
                raise ValueError(f"command snapshot not found: {snapshot_id}")
            return self.upsert_cli_command_evidence_row(db, snapshot)

    def upsert_cli_command_evidence_row(
        self,
        db,
        snapshot: CommandSnapshot,
    ) -> RawEvidence:
        evidence = build_cli_evidence(snapshot)
        row = db.get(RawEvidenceRecord, evidence.evidence_id)
        now = _utcnow()
        if row is None:
            row = RawEvidenceRecord(
                id=evidence.evidence_id,
                evidence_key=evidence.evidence_key,
                created_at=now,
            )
            db.add(row)
        row.source = evidence.source or evidence.source_type or "cli"
        row.source_type = evidence.source_type
        row.evidence_type = evidence.evidence_type
        row.run_id = evidence.run_id
        row.dataset_id = evidence.dataset_id
        row.inventory_device_id = evidence.inventory_device_id
        row.device_id = evidence.device_id
        row.source_snapshot_id = evidence.source_snapshot_id
        row.observed_at = snapshot.observed_at
        row.ingested_at = now
        row.content_ref = evidence.content_ref
        row.content_hash = evidence.content_hash
        row.retention_policy = evidence.retention_policy
        row.metadata_json = evidence.metadata or {}
        row.updated_at = now
        return evidence

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(RawEvidenceRecord, evidence_id)
            return row.to_dict() if row else None

    def get_cli_evidence_for_snapshot(
        self,
        snapshot_id: str,
    ) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = (
                db.query(RawEvidenceRecord)
                .filter(RawEvidenceRecord.source_snapshot_id == snapshot_id)
                .first()
            )
            if row is not None:
                return row.to_dict()
            snapshot = db.get(CommandSnapshot, snapshot_id)
            if snapshot is None:
                return None
            return build_cli_evidence(snapshot).to_dict()
