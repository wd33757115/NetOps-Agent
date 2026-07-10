"""PostgreSQL repository for patrol datasets, runs, and command snapshots."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from src.core.evidence import RawEvidenceRepository, evidence_id_for_command_snapshot
from src.core.patrol.domain import SnapshotRecord
from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkDevice,
    ParserTemplate,
    PatrolCollectionResult,
    PatrolDataset,
    PatrolRun,
)
from src.infrastructure.db.postgres import get_db_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return _utcnow()


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


class PatrolRepository:
    """Authoritative PostgreSQL patrol data-plane repository."""

    def ensure_dataset(
        self,
        *,
        dataset_id: str,
        mode: str,
        source: str,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        with get_db_session() as db:
            row = db.get(PatrolDataset, dataset_id)
            if row is None:
                row = PatrolDataset(
                    id=dataset_id,
                    name=name or dataset_id,
                    mode=mode,
                    source=source,
                    status="active",
                    metadata_json=metadata or {},
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
                db.add(row)
            else:
                row.name = name or row.name
                row.mode = mode
                row.source = source
                row.metadata_json = {**(row.metadata_json or {}), **(metadata or {})}
                row.updated_at = _utcnow()
        return dataset_id

    def create_run(
        self,
        *,
        run_id: str | None = None,
        source: str = "imported_file",
        mode: str = "production",
        dataset_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        started_at: str | datetime | None = None,
        run_purpose: str | None = None,
    ) -> str:
        run_id = run_id or str(uuid.uuid4())
        with get_db_session() as db:
            row = db.get(PatrolRun, run_id)
            if row is None:
                row = PatrolRun(
                    id=run_id,
                    source=source,
                    mode=mode,
                    dataset_id=dataset_id,
                    status="running",
                    collection_status="running",
                    analysis_status="pending",
                    baseline_eligible=False,
                    run_purpose=run_purpose or (
                        "parser_validation"
                        if mode == "parser_validation"
                        else "scheduled_patrol"
                    ),
                    started_at=_as_datetime(started_at),
                    metadata_json=metadata or {},
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
                db.add(row)
            else:
                row.source = source
                row.mode = mode
                row.dataset_id = dataset_id or row.dataset_id
                row.status = "running"
                row.collection_status = "running"
                row.analysis_status = "pending"
                row.baseline_eligible = False
                if run_purpose:
                    row.run_purpose = run_purpose
                row.completed_at = None
                row.metadata_json = {**(row.metadata_json or {}), **(metadata or {})}
                row.updated_at = _utcnow()
        return run_id

    def finish_run(
        self,
        run_id: str,
        *,
        collection_status: str = "completed",
        analysis_status: str | None = None,
        baseline_eligible: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with get_db_session() as db:
            row = db.get(PatrolRun, run_id)
            if row is None:
                raise ValueError(f"patrol run not found: {run_id}")
            counts = (
                db.query(
                    func.count(CommandSnapshot.id),
                    func.count(func.distinct(CommandSnapshot.device_id)),
                )
                .filter(CommandSnapshot.run_id == run_id)
                .one()
            )
            row.command_count = int(counts[0] or 0)
            row.device_count = int(counts[1] or 0)
            row.status = collection_status
            row.collection_status = collection_status
            if analysis_status is not None:
                row.analysis_status = analysis_status
            if baseline_eligible is not None:
                row.baseline_eligible = baseline_eligible
            if metadata:
                row.metadata_json = {**(row.metadata_json or {}), **metadata}
            row.completed_at = _utcnow()
            row.updated_at = _utcnow()
            if row.dataset_id:
                dataset = db.get(PatrolDataset, row.dataset_id)
                if dataset is not None:
                    dataset.imported_at = _utcnow()
                    dataset.updated_at = _utcnow()

    def update_analysis_status(
        self,
        run_id: str,
        *,
        analysis_status: str,
        baseline_eligible: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with get_db_session() as db:
            row = db.get(PatrolRun, run_id)
            if row is None:
                raise ValueError(f"patrol run not found: {run_id}")
            row.analysis_status = analysis_status
            if baseline_eligible is not None:
                row.baseline_eligible = baseline_eligible
            if metadata:
                row.metadata_json = {**(row.metadata_json or {}), **metadata}
            row.updated_at = _utcnow()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(PatrolRun, run_id)
            return row.to_dict() if row else None

    def save_collection_result(
        self,
        *,
        run_id: str,
        device_id: str,
        canonical_command: str,
        status: str,
        inventory_device_id: str | None = None,
        authoritative_for_deletion: bool = False,
        baseline_eligible: bool = False,
        observed_entity_count: int | None = None,
        failure_code: str | None = None,
        observed_at: str | datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        normalized_command = " ".join(str(canonical_command).strip().lower().split())
        with get_db_session() as db:
            row = (
                db.query(PatrolCollectionResult)
                .filter(
                    PatrolCollectionResult.run_id == run_id,
                    PatrolCollectionResult.device_id == device_id,
                    PatrolCollectionResult.canonical_command == normalized_command,
                )
                .first()
            )
            now = _utcnow()
            if row is None:
                row = PatrolCollectionResult(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    device_id=device_id,
                    canonical_command=normalized_command,
                    created_at=now,
                )
                db.add(row)
            row.inventory_device_id = inventory_device_id or row.inventory_device_id
            row.status = status
            row.authoritative_for_deletion = bool(authoritative_for_deletion)
            row.baseline_eligible = bool(baseline_eligible)
            row.observed_entity_count = observed_entity_count
            row.failure_code = failure_code
            row.observed_at = _as_datetime(observed_at) if observed_at else row.observed_at
            row.metadata_json = {**(row.metadata_json or {}), **(metadata or {})}
            row.updated_at = now
            db.flush()
            return row.id

    def list_collection_results(self, run_id: str) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(PatrolCollectionResult)
                .filter(PatrolCollectionResult.run_id == run_id)
                .order_by(
                    PatrolCollectionResult.device_id,
                    PatrolCollectionResult.canonical_command,
                )
                .all()
            )
            return [row.to_dict() for row in rows]

    def list_authoritative_collection_keys(self, run_id: str) -> set[tuple[str, str]]:
        with get_db_session() as db:
            rows = (
                db.query(
                    PatrolCollectionResult.device_id,
                    PatrolCollectionResult.canonical_command,
                )
                .filter(
                    PatrolCollectionResult.run_id == run_id,
                    PatrolCollectionResult.authoritative_for_deletion.is_(True),
                )
                .all()
            )
            return {(str(device_id), str(command)) for device_id, command in rows}

    def save_snapshot(
        self,
        **values: Any,
    ) -> str:
        snapshot_id, _ = self.save_snapshot_idempotent(**values)
        return snapshot_id

    def save_snapshot_idempotent(
        self,
        *,
        run_id: str,
        device_id: str,
        device_name: str,
        command: str,
        command_canonical: str,
        raw_text: str,
        ip: str | None = None,
        vendor: str | None = None,
        model: str | None = None,
        observed_at: str | datetime | None = None,
        structured_json: Any = None,
        parser_name: str | None = None,
        parser_version: str | None = None,
        parse_status: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        dataset_id: str | None = None,
        source_file_hash: str | None = None,
        import_fingerprint: str | None = None,
        snapshot_id: str | None = None,
        snapshot_kind: str = "full",
        metadata: dict[str, Any] | None = None,
        inventory_device_id: str | None = None,
    ) -> tuple[str, bool]:
        raw_hash = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()
        status = parse_status or ("parsed" if structured_json is not None else "raw_only")
        snapshot_id = snapshot_id or str(uuid.uuid4())
        try:
            with get_db_session() as db:
                existing_by_id = db.get(CommandSnapshot, snapshot_id)
                if existing_by_id is not None:
                    RawEvidenceRepository().upsert_cli_command_evidence_row(
                        db,
                        existing_by_id,
                    )
                    return existing_by_id.id, False
                if import_fingerprint:
                    existing = (
                        db.query(CommandSnapshot)
                        .filter(CommandSnapshot.import_fingerprint == import_fingerprint)
                        .first()
                    )
                    if existing is not None:
                        RawEvidenceRepository().upsert_cli_command_evidence_row(
                            db,
                            existing,
                        )
                        return existing.id, False
                inventory = (
                    db.get(NetworkDevice, inventory_device_id)
                    if inventory_device_id
                    else None
                )
                if inventory is None:
                    inventory = (
                        db.query(NetworkDevice)
                        .filter(
                            NetworkDevice.device_name == device_name,
                            NetworkDevice.management_ip == (ip or ""),
                        )
                        .first()
                        if ip
                        else None
                    )
                contract_version = None
                if parser_name:
                    template = db.get(ParserTemplate, parser_name)
                    contract_version = template.contract_version if template else None
                snapshot = CommandSnapshot(
                    id=snapshot_id,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    inventory_device_id=inventory.id if inventory else inventory_device_id,
                    device_id=device_id,
                    device_name=device_name,
                    management_ip=ip,
                    vendor=vendor,
                    model=model,
                    command=command,
                    canonical_command=command_canonical,
                    parser_template_id=parser_name,
                    parser_version=parser_version,
                    contract_version=contract_version,
                    observed_at=_as_datetime(observed_at),
                    structured_json=structured_json,
                    raw_text=raw_text or "",
                    raw_text_hash=raw_hash,
                    parse_status=status,
                    snapshot_kind=snapshot_kind,
                    source_file_hash=source_file_hash,
                    import_fingerprint=import_fingerprint,
                    start_line=start_line,
                    end_line=end_line,
                    metadata_json=metadata or {},
                    created_at=_utcnow(),
                )
                db.add(snapshot)
                db.flush()
                RawEvidenceRepository().upsert_cli_command_evidence_row(db, snapshot)
            return snapshot_id, True
        except IntegrityError:
            if not import_fingerprint:
                raise
            with get_db_session() as db:
                existing = (
                    db.query(CommandSnapshot)
                    .filter(CommandSnapshot.import_fingerprint == import_fingerprint)
                    .one()
                )
                RawEvidenceRepository().upsert_cli_command_evidence_row(db, existing)
                return existing.id, False

    def list_dataset_snapshots(
        self,
        dataset_id: str | None = None,
        *,
        mode: str | None = None,
    ) -> list[SnapshotRecord]:
        with get_db_session() as db:
            query = db.query(CommandSnapshot)
            if dataset_id:
                query = query.filter(CommandSnapshot.dataset_id == dataset_id)
            if mode:
                query = query.join(PatrolRun, PatrolRun.id == CommandSnapshot.run_id).filter(
                    PatrolRun.mode == mode
                )
            rows = query.order_by(
                CommandSnapshot.dataset_id,
                CommandSnapshot.device_id,
                CommandSnapshot.canonical_command,
                CommandSnapshot.observed_at,
            ).all()
            return [self._to_snapshot(row) for row in rows]

    def list_datasets(self, *, mode: str | None = None) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(
                PatrolDataset,
                func.count(CommandSnapshot.id).label("snapshot_count"),
                func.count(func.distinct(CommandSnapshot.device_id)).label("device_count"),
                func.count(func.distinct(CommandSnapshot.canonical_command)).label(
                    "command_count"
                ),
                func.min(CommandSnapshot.created_at).label("first_imported_at"),
                func.max(CommandSnapshot.created_at).label("last_imported_at"),
            ).outerjoin(CommandSnapshot, CommandSnapshot.dataset_id == PatrolDataset.id)
            if mode:
                query = query.filter(PatrolDataset.mode == mode)
            rows = (
                query
                .group_by(PatrolDataset.id)
                .order_by(func.max(CommandSnapshot.created_at).desc().nullslast())
                .all()
            )
            return [
                {
                    **dataset.to_dict(),
                    "snapshot_count": int(snapshot_count or 0),
                    "device_count": int(device_count or 0),
                    "command_count": int(command_count or 0),
                    "first_imported_at": _iso(first_imported_at),
                    "last_imported_at": _iso(last_imported_at),
                }
                for (
                    dataset,
                    snapshot_count,
                    device_count,
                    command_count,
                    first_imported_at,
                    last_imported_at,
                ) in rows
            ]

    def list_import_fingerprints(
        self,
        *,
        mode: str | None = None,
        dataset_id: str | None = None,
    ) -> set[str]:
        with get_db_session() as db:
            query = db.query(CommandSnapshot.import_fingerprint).filter(
                CommandSnapshot.import_fingerprint.is_not(None)
            )
            if dataset_id:
                query = query.filter(CommandSnapshot.dataset_id == dataset_id)
            if mode:
                query = query.join(
                    PatrolRun, PatrolRun.id == CommandSnapshot.run_id
                ).filter(PatrolRun.mode == mode)
            return {str(row[0]) for row in query.all() if row[0]}

    def find_snapshots_by_import_fingerprints(
        self,
        fingerprints: set[str],
    ) -> dict[str, SnapshotRecord]:
        if not fingerprints:
            return {}
        with get_db_session() as db:
            rows = (
                db.query(CommandSnapshot)
                .filter(CommandSnapshot.import_fingerprint.in_(fingerprints))
                .all()
            )
            return {
                str(row.import_fingerprint): self._to_snapshot(row)
                for row in rows
                if row.import_fingerprint
            }

    def get_snapshot(self, snapshot_id: str) -> SnapshotRecord | None:
        with get_db_session() as db:
            row = db.get(CommandSnapshot, snapshot_id)
            return self._to_snapshot(row) if row else None

    def get_snapshot_context(self, snapshot_id: str) -> dict[str, Any] | None:
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
            return {
                "snapshot": self._to_snapshot(snapshot),
                "mode": run.mode,
                "source": run.source,
                "dataset_id": snapshot.dataset_id or run.dataset_id,
                "run_metadata": dict(run.metadata_json or {}),
            }

    def get_run_metadata_for_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            row = (
                db.query(PatrolRun.metadata_json)
                .join(CommandSnapshot, CommandSnapshot.run_id == PatrolRun.id)
                .filter(CommandSnapshot.id == snapshot_id)
                .first()
            )
            return dict(row[0] or {}) if row else {}

    def find_snapshot(
        self,
        *,
        run_id: str,
        device_id: str,
        command_canonical: str,
    ) -> SnapshotRecord | None:
        with get_db_session() as db:
            row = (
                db.query(CommandSnapshot)
                .filter(
                    CommandSnapshot.run_id == run_id,
                    CommandSnapshot.device_id == device_id,
                    CommandSnapshot.canonical_command == command_canonical,
                )
                .order_by(CommandSnapshot.observed_at.desc())
                .first()
            )
            return self._to_snapshot(row) if row else None

    def find_previous_snapshot(
        self,
        *,
        current_run_id: str,
        device_id: str,
        command_canonical: str,
    ) -> SnapshotRecord | None:
        with get_db_session() as db:
            current_run = db.get(PatrolRun, current_run_id)
            if current_run is None:
                return None
            row = (
                db.query(CommandSnapshot)
                .join(PatrolRun, PatrolRun.id == CommandSnapshot.run_id)
                .filter(
                    CommandSnapshot.run_id != current_run_id,
                    CommandSnapshot.device_id == device_id,
                    CommandSnapshot.canonical_command == command_canonical,
                    PatrolRun.mode == current_run.mode,
                    PatrolRun.collection_status.in_(["completed", "partial"]),
                    PatrolRun.completed_at <= current_run.started_at,
                )
                .order_by(CommandSnapshot.observed_at.desc())
                .first()
            )
            return self._to_snapshot(row) if row else None

    def list_run_snapshots(
        self,
        run_id: str,
        device_id: str | None = None,
    ) -> list[SnapshotRecord]:
        with get_db_session() as db:
            query = db.query(CommandSnapshot).filter(CommandSnapshot.run_id == run_id)
            if device_id:
                query = query.filter(CommandSnapshot.device_id == device_id)
            return [
                self._to_snapshot(row)
                for row in query.order_by(
                    CommandSnapshot.device_id,
                    CommandSnapshot.canonical_command,
                ).all()
            ]

    @staticmethod
    def _to_snapshot(row: CommandSnapshot) -> SnapshotRecord:
        return SnapshotRecord(
            snapshot_id=row.id,
            run_id=row.run_id,
            device_id=row.device_id,
            device_name=row.device_name,
            ip=row.management_ip,
            vendor=row.vendor,
            model=row.model,
            command=row.command,
            command_canonical=row.canonical_command,
            parser_name=row.parser_template_id,
            parser_version=row.parser_version,
            observed_at=_iso(row.observed_at),
            structured_json=row.structured_json,
            raw_text=row.raw_text or "",
            raw_text_hash=row.raw_text_hash,
            parse_status=row.parse_status,
            dataset_id=row.dataset_id,
            source_file_hash=row.source_file_hash,
            import_fingerprint=row.import_fingerprint,
            evidence_id=evidence_id_for_command_snapshot(row.id),
            source_type="cli",
            schema_id=row.parser_template_id,
        )
