"""PostgreSQL repository for patrol datasets, runs, and command snapshots."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError

from src.core.evidence import RawEvidenceRepository, evidence_id_for_command_snapshot
from src.core.parser.output_classifier import OUTPUT_STATUSES
from src.core.patrol.domain import SnapshotRecord
from src.core.patrol.run_status_projector import PatrolRunStatusProjector
from src.core.patrol.statuses import (
    PARSE_STATUSES,
    PatrolCommandStatus,
    derive_command_status,
    validate_command_status,
)
from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkDevice,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    ParserTemplate,
    SnapshotParseResult,
    PatrolAnalysisAttemptRecord,
    PatrolCollectionResult,
    PatrolDataset,
    PatrolRun,
    StateObservationRecord,
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
        except ValueError:
            parsed = None
        if parsed is None:
            for pattern in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
        if parsed is None:
            raise ValueError(f"invalid fact timestamp: {value!r}")
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return _utcnow()


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _lease_is_live(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return value >= now


@dataclass(frozen=True)
class RedeliveryDecision:
    action: str
    attempt_number: int
    run_id: str
    reason: str
    owner_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AnalysisAttemptFencedError(RuntimeError):
    """Raised when a stale analysis generation attempts to mutate state."""


class AnalysisAttemptInProgressError(RuntimeError):
    """Raised when another owner still holds a live analysis lease."""


class PatrolRunFencedError(RuntimeError):
    """Raised when a stale collection owner attempts to mutate a patrol run."""


def _compact_analysis_result(result: dict[str, Any]) -> dict[str, Any]:
    omitted = {"changes", "events", "results"}
    return {key: value for key, value in result.items() if key not in omitted}


class AnalysisLeaseHeartbeat:
    """Wall-clock lease renewal independent of analysis item throughput."""

    def __init__(
        self,
        repository: PatrolRepository,
        attempt: dict[str, Any],
        *,
        interval_seconds: float = 30.0,
    ) -> None:
        self._repository = repository
        self._attempt = attempt
        self._interval = max(1.0, float(interval_seconds))
        self._last_heartbeat = time.monotonic()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> AnalysisLeaseHeartbeat:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=min(5.0, self._interval))

    def __call__(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_heartbeat < self._interval:
            return
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_heartbeat < self._interval:
                return
            self._repository.touch_analysis_attempt(
                str(self._attempt["attempt_id"]),
                attempt_number=int(self._attempt["attempt_number"]),
                owner_token=str(self._attempt["owner_token"]),
            )
            self._last_heartbeat = now

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self(force=True)
            except AnalysisAttemptFencedError:
                return
            except Exception:
                continue


class _PatrolRepositoryBase:
    """Shared patrol ownership guard used by write repositories."""

    @staticmethod
    def _require_collection_owner(db, run_id: str, owner_token: str | None) -> PatrolRun:
        row = db.query(PatrolRun).filter(PatrolRun.id == run_id).with_for_update().one_or_none()
        if row is None:
            raise ValueError(f"patrol run not found: {run_id}")
        if row.owner_token is None and owner_token is None and row.status != "abandoned":
            return row
        if row.status != "running":
            raise PatrolRunFencedError(f"patrol run no longer accepts collection writes: {run_id}")
        if row.owner_token is not None and row.owner_token != owner_token:
            raise PatrolRunFencedError(f"patrol run collection owner was fenced: {run_id}")
        return row


class PatrolRunRepositoryBackend(_PatrolRepositoryBase):
    """Patrol run, dataset, lease and collection ownership persistence."""

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
        logical_run_id: str | None = None,
        attempt_number: int = 0,
        worker_id: str | None = None,
        lease_seconds: int | None = None,
        owner_token: str | None = None,
        owner_generation: int = 1,
    ) -> str:
        run_id = run_id or str(uuid.uuid4())
        with get_db_session() as db:
            row = db.get(PatrolRun, run_id)
            if row is None:
                now = _utcnow()
                row = PatrolRun(
                    id=run_id,
                    source=source,
                    mode=mode,
                    dataset_id=dataset_id,
                    status="running",
                    collection_status="running",
                    analysis_status="pending",
                    baseline_eligible=False,
                    run_purpose=run_purpose
                    or ("parser_validation" if mode == "parser_validation" else "scheduled_patrol"),
                    logical_run_id=logical_run_id or run_id,
                    attempt_number=max(0, int(attempt_number)),
                    owner_generation=max(1, int(owner_generation)),
                    owner_token=owner_token,
                    worker_id=worker_id,
                    heartbeat_at=now,
                    lease_until=(
                        now + timedelta(seconds=max(30, int(lease_seconds)))
                        if lease_seconds
                        else None
                    ),
                    last_progress_at=now,
                    started_at=_as_datetime(started_at),
                    metadata_json=metadata or {},
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
                db.add(row)
            else:
                if logical_run_id is not None and (
                    row.logical_run_id != logical_run_id
                    or int(row.attempt_number or 0) != max(0, int(attempt_number))
                ):
                    raise ValueError("existing patrol run attempt identity does not match request")
                if row.status == "reserved":
                    if row.worker_id and worker_id and row.worker_id != worker_id:
                        raise ValueError("reserved patrol attempt belongs to another worker")
                    now = _utcnow()
                    if row.owner_token is not None and row.owner_token != owner_token:
                        raise PatrolRunFencedError("reserved patrol attempt owner token mismatch")
                    row.status = "running"
                    row.collection_status = "running"
                    row.analysis_status = "pending"
                    row.source = source
                    row.mode = mode
                    row.dataset_id = dataset_id
                    row.run_purpose = run_purpose or row.run_purpose
                    row.worker_id = worker_id or row.worker_id
                    row.heartbeat_at = now
                    row.last_progress_at = now
                    row.lease_until = (
                        now + timedelta(seconds=max(30, int(lease_seconds)))
                        if lease_seconds
                        else row.lease_until
                    )
                    row.started_at = _as_datetime(started_at)
                    row.metadata_json = {
                        **(row.metadata_json or {}),
                        "attempt_reserved": False,
                    }
                elif row.worker_id and worker_id and row.worker_id != worker_id:
                    raise ValueError("patrol attempt belongs to another worker")
                elif row.owner_token is not None and row.owner_token != owner_token:
                    raise PatrolRunFencedError("patrol attempt owner token mismatch")
                if row.mode != mode or row.source != source:
                    raise ValueError(
                        "existing patrol run identity does not match requested source/mode"
                    )
                row.metadata_json = {**(row.metadata_json or {}), **(metadata or {})}
                row.updated_at = _utcnow()
        return run_id

    def touch_run(
        self,
        run_id: str,
        *,
        lease_seconds: int,
        worker_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        owner_token: str | None = None,
    ) -> None:
        now = _utcnow()
        with get_db_session() as db:
            row = self._require_collection_owner(db, run_id, owner_token)
            row.heartbeat_at = now
            row.last_progress_at = now
            row.lease_until = now + timedelta(seconds=max(30, int(lease_seconds)))
            if worker_id:
                if row.worker_id and row.worker_id != worker_id:
                    raise PatrolRunFencedError(f"patrol run worker was fenced: {run_id}")
                row.worker_id = worker_id
            if metadata:
                row.metadata_json = {**(row.metadata_json or {}), **metadata}
            row.updated_at = now

    def reconcile_stale_runs(self, *, limit: int = 100) -> dict[str, Any]:
        now = _utcnow()
        reconciled: list[str] = []
        with get_db_session() as db:
            rows = (
                db.query(PatrolRun)
                .filter(
                    PatrolRun.status.in_(["running", "reserved"]),
                    PatrolRun.lease_until.is_not(None),
                    PatrolRun.lease_until < now,
                )
                .order_by(PatrolRun.lease_until)
                .limit(max(1, min(int(limit), 1000)))
                .with_for_update(skip_locked=True)
                .all()
            )
            for row in rows:
                snapshot_count = (
                    db.query(CommandSnapshot.id).filter(CommandSnapshot.run_id == row.id).count()
                )
                row.status = "abandoned"
                row.collection_status = "partial" if snapshot_count else "failed"
                row.analysis_status = "failed"
                row.baseline_eligible = False
                row.completed_at = now
                row.lease_until = None
                row.metadata_json = {
                    **(row.metadata_json or {}),
                    "abandoned_reason": "patrol_run_lease_expired",
                    "reconciled_at": now.isoformat(),
                    "reconciled_snapshot_count": snapshot_count,
                }
                row.updated_at = now
                reconciled.append(row.id)
        return {"reconciled_count": len(reconciled), "run_ids": reconciled}

    def prepare_redelivery_attempt(
        self,
        logical_run_id: str,
        *,
        worker_id: str | None = None,
        source: str = "device-patrol",
        mode: str = "production",
        lease_seconds: int = 900,
    ) -> RedeliveryDecision:
        """Atomically decide and reserve a redelivered patrol attempt."""
        now = _utcnow()
        with get_db_session() as db:
            digest = hashlib.sha256(f"patrol-attempt-v1|{logical_run_id}".encode()).digest()
            db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": int.from_bytes(digest[:8], "big", signed=True)},
            )
            rows = (
                db.query(PatrolRun)
                .filter(PatrolRun.logical_run_id == logical_run_id)
                .order_by(PatrolRun.attempt_number)
                .with_for_update()
                .all()
            )
            latest = rows[-1] if rows else None
            if (
                latest is not None
                and latest.status in {"completed", "partial"}
                and latest.collection_status in {"completed", "partial"}
                and latest.completed_at is not None
            ):
                return RedeliveryDecision(
                    action="already_completed",
                    attempt_number=int(latest.attempt_number or 0),
                    run_id=latest.id,
                    reason="broker_redelivered_after_terminal_collection_commit",
                    owner_token=latest.owner_token,
                )
            next_attempt = (
                max(
                    [int(row.attempt_number or 0) for row in rows],
                    default=0,
                )
                + 1
            )
            for row in rows:
                if row.status not in {"running", "reserved"}:
                    continue
                snapshot_count = (
                    db.query(CommandSnapshot.id).filter(CommandSnapshot.run_id == row.id).count()
                )
                row.status = "abandoned"
                row.collection_status = "partial" if snapshot_count else "failed"
                row.analysis_status = "failed"
                row.baseline_eligible = False
                row.completed_at = now
                row.lease_until = None
                row.metadata_json = {
                    **(row.metadata_json or {}),
                    "abandoned_reason": "celery_worker_lost_redelivery",
                    "redelivered_at": now.isoformat(),
                }
                row.updated_at = now
            run_id = f"{logical_run_id}-attempt-{next_attempt}"
            owner_token = uuid.uuid4().hex
            db.add(
                PatrolRun(
                    id=run_id,
                    source=source,
                    mode=mode,
                    status="reserved",
                    collection_status="running",
                    analysis_status="pending",
                    baseline_eligible=False,
                    run_purpose="scheduled_patrol",
                    logical_run_id=logical_run_id,
                    attempt_number=next_attempt,
                    owner_generation=next_attempt + 1,
                    owner_token=owner_token,
                    worker_id=worker_id,
                    heartbeat_at=now,
                    lease_until=now + timedelta(seconds=max(30, int(lease_seconds))),
                    last_progress_at=now,
                    started_at=now,
                    metadata_json={
                        "attempt_reserved": True,
                        "redelivered_at": now.isoformat(),
                    },
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            return RedeliveryDecision(
                action="allocate_new_attempt",
                attempt_number=next_attempt,
                run_id=run_id,
                reason="worker_lost_attempt_replaced",
                owner_token=owner_token,
            )

    def mark_celery_task_completed(self, run_id: str, *, owner_token: str | None = None) -> None:
        with get_db_session() as db:
            row = db.get(PatrolRun, run_id)
            if row is None:
                return
            if row.owner_token is not None and row.owner_token != owner_token:
                raise PatrolRunFencedError(
                    f"patrol run completion marker owner was fenced: {run_id}"
                )
            now = _utcnow()
            row.metadata_json = {
                **(row.metadata_json or {}),
                "celery_task_completed": True,
                "celery_task_completed_at": now.isoformat(),
            }
            row.updated_at = now

    def finish_run(
        self,
        run_id: str,
        *,
        collection_status: str = "completed",
        parser_readiness_status: str | None = None,
        fact_readiness_status: str | None = None,
        analysis_status: str | None = None,
        baseline_eligible: bool | None = None,
        metadata: dict[str, Any] | None = None,
        owner_token: str | None = None,
    ) -> None:
        with get_db_session() as db:
            row = self._require_collection_owner(db, run_id, owner_token)
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
            collection_rows = (
                db.query(PatrolCollectionResult)
                .filter(PatrolCollectionResult.run_id == run_id)
                .all()
            )
            readiness = PatrolRunStatusProjector.project(collection_rows)
            row.parser_readiness_status = (
                readiness.parser_readiness_status
                if collection_rows
                else parser_readiness_status or readiness.parser_readiness_status
            )
            row.fact_readiness_status = (
                readiness.fact_readiness_status
                if collection_rows
                else fact_readiness_status or readiness.fact_readiness_status
            )
            if analysis_status is not None:
                row.analysis_status = analysis_status
            if baseline_eligible is not None:
                row.baseline_eligible = baseline_eligible
            if metadata:
                row.metadata_json = {**(row.metadata_json or {}), **metadata}
            row.completed_at = _utcnow()
            row.lease_until = None
            row.heartbeat_at = _utcnow()
            row.last_progress_at = _utcnow()
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
            collection_rows = (
                db.query(PatrolCollectionResult)
                .filter(PatrolCollectionResult.run_id == run_id)
                .all()
            )
            readiness = PatrolRunStatusProjector.project(collection_rows)
            row.parser_readiness_status = readiness.parser_readiness_status
            row.fact_readiness_status = readiness.fact_readiness_status
            if baseline_eligible is not None:
                row.baseline_eligible = baseline_eligible
            if metadata:
                row.metadata_json = {**(row.metadata_json or {}), **metadata}
            row.updated_at = _utcnow()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(PatrolRun, run_id)
            return row.to_dict() if row else None


class PatrolAnalysisAttemptRepository(_PatrolRepositoryBase):
    """Immutable rule bundle, lease and completion for analysis attempts."""

    def begin_analysis_attempt(
        self,
        *,
        run_id: str,
        stage: str,
        scope: dict[str, Any],
        rule_bundle: dict[str, Any],
        lease_seconds: int = 900,
    ) -> dict[str, Any]:
        scope_hash = hashlib.sha256(
            json.dumps(scope, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        bundle_digest = hashlib.sha256(
            json.dumps(
                rule_bundle,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        now = _utcnow()
        with get_db_session() as db:
            lock_digest = hashlib.sha256(
                f"analysis-attempt-v1|{run_id}|{stage}|{scope_hash}".encode()
            ).digest()
            db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": int.from_bytes(lock_digest[:8], "big", signed=True)},
            )
            row = (
                db.query(PatrolAnalysisAttemptRecord)
                .filter(
                    PatrolAnalysisAttemptRecord.run_id == run_id,
                    PatrolAnalysisAttemptRecord.stage == stage,
                    PatrolAnalysisAttemptRecord.scope_hash == scope_hash,
                )
                .with_for_update()
                .first()
            )
            if row is not None and row.status == "completed":
                return {
                    "reused": True,
                    "in_progress": False,
                    "attempt_id": row.id,
                    "attempt_number": int(row.attempt_number),
                    "owner_token": row.owner_token,
                    "rule_bundle": dict(row.rule_bundle_json or {}),
                    "rule_bundle_digest": row.rule_bundle_digest,
                    "result": dict(row.result_json or {}),
                }
            if row is not None and row.status == "running" and _lease_is_live(row.lease_until, now):
                return {
                    "reused": False,
                    "in_progress": True,
                    "attempt_id": row.id,
                    "attempt_number": int(row.attempt_number),
                    "owner_token": None,
                    "rule_bundle": dict(row.rule_bundle_json or {}),
                    "rule_bundle_digest": row.rule_bundle_digest,
                    "result": None,
                }
            owner_token = uuid.uuid4().hex
            if row is None:
                row = PatrolAnalysisAttemptRecord(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    stage=stage,
                    scope_hash=scope_hash,
                    scope_json=scope,
                    rule_bundle_digest=bundle_digest,
                    rule_bundle_json=rule_bundle,
                    status="running",
                    attempt_number=1,
                    owner_token=owner_token,
                    heartbeat_at=now,
                    lease_until=now + timedelta(seconds=max(30, int(lease_seconds))),
                    started_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.status = "running"
                row.attempt_number = int(row.attempt_number or 0) + 1
                row.owner_token = owner_token
                row.heartbeat_at = now
                row.lease_until = now + timedelta(seconds=max(30, int(lease_seconds)))
                row.error = None
                row.completed_at = None
                row.updated_at = now
            db.flush()
            return {
                "reused": False,
                "in_progress": False,
                "attempt_id": row.id,
                "attempt_number": int(row.attempt_number),
                "owner_token": owner_token,
                "rule_bundle": dict(row.rule_bundle_json or {}),
                "rule_bundle_digest": row.rule_bundle_digest,
                "result": None,
            }

    def complete_analysis_attempt(
        self,
        attempt_id: str,
        *,
        attempt_number: int,
        owner_token: str,
        result: dict[str, Any],
    ) -> None:
        compact_result = _compact_analysis_result(result)
        result_digest = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        with get_db_session() as db:
            now = _utcnow()
            updated = (
                db.query(PatrolAnalysisAttemptRecord)
                .filter(
                    PatrolAnalysisAttemptRecord.id == attempt_id,
                    PatrolAnalysisAttemptRecord.attempt_number == int(attempt_number),
                    PatrolAnalysisAttemptRecord.owner_token == owner_token,
                    PatrolAnalysisAttemptRecord.status == "running",
                )
                .update(
                    {
                        "status": "completed",
                        "result_json": compact_result,
                        "result_digest": result_digest,
                        "error": None,
                        "completed_at": now,
                        "heartbeat_at": now,
                        "lease_until": now,
                        "updated_at": now,
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise AnalysisAttemptFencedError(
                    f"analysis attempt generation no longer owns completion: {attempt_id}"
                )

    def touch_analysis_attempt(
        self,
        attempt_id: str,
        *,
        attempt_number: int,
        owner_token: str,
        lease_seconds: int = 900,
    ) -> None:
        now = _utcnow()
        with get_db_session() as db:
            updated = (
                db.query(PatrolAnalysisAttemptRecord)
                .filter(
                    PatrolAnalysisAttemptRecord.id == attempt_id,
                    PatrolAnalysisAttemptRecord.attempt_number == int(attempt_number),
                    PatrolAnalysisAttemptRecord.owner_token == owner_token,
                    PatrolAnalysisAttemptRecord.status == "running",
                )
                .update(
                    {
                        "heartbeat_at": now,
                        "lease_until": now + timedelta(seconds=max(30, int(lease_seconds))),
                        "updated_at": now,
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise AnalysisAttemptFencedError(
                    f"analysis attempt generation no longer owns heartbeat: {attempt_id}"
                )

    def fail_analysis_attempt(
        self,
        attempt_id: str,
        *,
        attempt_number: int,
        owner_token: str,
        error: str,
    ) -> None:
        with get_db_session() as db:
            now = _utcnow()
            updated = (
                db.query(PatrolAnalysisAttemptRecord)
                .filter(
                    PatrolAnalysisAttemptRecord.id == attempt_id,
                    PatrolAnalysisAttemptRecord.attempt_number == int(attempt_number),
                    PatrolAnalysisAttemptRecord.owner_token == owner_token,
                    PatrolAnalysisAttemptRecord.status == "running",
                )
                .update(
                    {
                        "status": "failed",
                        "error": str(error)[:4000],
                        "completed_at": now,
                        "heartbeat_at": now,
                        "lease_until": now,
                        "updated_at": now,
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise AnalysisAttemptFencedError(
                    f"analysis attempt generation no longer owns failure: {attempt_id}"
                )

    def reconcile_stale_analysis_attempts(self, *, limit: int = 100) -> dict[str, Any]:
        now = _utcnow()
        run_ids: set[str] = set()
        with get_db_session() as db:
            rows = (
                db.query(PatrolAnalysisAttemptRecord)
                .filter(
                    PatrolAnalysisAttemptRecord.status == "running",
                    PatrolAnalysisAttemptRecord.lease_until < now,
                )
                .order_by(PatrolAnalysisAttemptRecord.lease_until)
                .with_for_update(skip_locked=True)
                .limit(max(1, min(int(limit), 1000)))
                .all()
            )
            for row in rows:
                row.status = "failed"
                row.error = "analysis_lease_expired"
                row.completed_at = now
                row.updated_at = now
                run = db.get(PatrolRun, row.run_id)
                updates_run_status = bool((row.scope_json or {}).get("full_run", True))
                if (
                    run is not None
                    and updates_run_status
                    and run.analysis_status
                    in {
                        "normalizing",
                        "detecting",
                        "building_events",
                    }
                ):
                    run.analysis_status = "failed"
                    run.baseline_eligible = False
                    run.metadata_json = {
                        **(run.metadata_json or {}),
                        "analysis_failure_stage": row.stage,
                        "analysis_failure_reason": "analysis_lease_expired",
                    }
                    run.updated_at = now
                run_ids.add(row.run_id)
        return {"reconciled_count": len(run_ids), "run_ids": sorted(run_ids)}


class PatrolCollectionResultRepository(_PatrolRepositoryBase):
    """Command collection and normalization result persistence."""

    def save_collection_result(
        self,
        *,
        run_id: str,
        device_id: str,
        canonical_command: str,
        status: str | None = None,
        execution_status: str | None = None,
        parse_status: str | None = None,
        output_status: str = "not_classified",
        fact_status: str | None = None,
        normalization_status: str | None = None,
        parser_candidate_id: str | None = None,
        inventory_device_id: str | None = None,
        authoritative_for_deletion: bool = False,
        baseline_eligible: bool = False,
        observed_entity_count: int | None = None,
        failure_code: str | None = None,
        observed_at: str | datetime | None = None,
        execution_item_id: str | None = None,
        collection_action_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        owner_token: str | None = None,
    ) -> str:
        if output_status not in OUTPUT_STATUSES:
            raise ValueError(f"unsupported output_status: {output_status}")
        normalized_command = " ".join(str(canonical_command).strip().lower().split())
        legacy_status = str(status or "").strip().lower()
        if execution_status is None:
            execution_status = (
                legacy_status
                if legacy_status in {"complete", "completed", "failed", "timeout", "cancelled"}
                else "complete"
                if legacy_status in {"parsed", "empty", "ignored"}
                else "failed"
            )
        if parse_status is None:
            parse_status = {
                "parsed": "parsed",
                "empty": "parse_empty",
                "ignored": "ignored",
            }.get(legacy_status, "not_attempted")
        derived = derive_command_status(
            execution_status=execution_status,
            parse_status=parse_status,
        )
        command_status = PatrolCommandStatus(
            execution_status=derived.execution_status,
            parse_status=derived.parse_status,
            fact_status=fact_status or derived.fact_status,
            normalization_status=normalization_status or derived.normalization_status,
        )
        validate_command_status(command_status)
        if (authoritative_for_deletion or baseline_eligible) and command_status.fact_status not in {
            "ready",
            "empty",
        }:
            raise ValueError("collection result without ready facts cannot have baseline authority")
        execution_item_id = (
            execution_item_id
            or hashlib.sha256(
                f"collection-result-v1|{run_id}|{device_id}|{normalized_command}".encode()
            ).hexdigest()
        )
        with get_db_session() as db:
            self._require_collection_owner(db, run_id, owner_token)
            row = (
                db.query(PatrolCollectionResult)
                .filter(
                    PatrolCollectionResult.run_id == run_id,
                    PatrolCollectionResult.device_id == device_id,
                    PatrolCollectionResult.execution_item_id == execution_item_id,
                )
                .first()
            )
            now = _utcnow()
            if row is None:
                row = PatrolCollectionResult(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    device_id=device_id,
                    execution_item_id=execution_item_id,
                    canonical_command=normalized_command,
                    created_at=now,
                )
                db.add(row)
            row.inventory_device_id = inventory_device_id or row.inventory_device_id
            row.collection_action_id = (
                collection_action_id or row.collection_action_id
            )
            # Deprecated compatibility projection. New readers use the four dimensions.
            row.status = command_status.execution_status
            row.execution_status = command_status.execution_status
            row.parse_status = command_status.parse_status
            row.output_status = output_status
            row.fact_status = command_status.fact_status
            row.normalization_status = command_status.normalization_status
            row.parser_candidate_id = parser_candidate_id or row.parser_candidate_id
            row.authoritative_for_deletion = bool(authoritative_for_deletion)
            row.baseline_eligible = bool(baseline_eligible)
            row.observed_entity_count = observed_entity_count
            row.failure_code = failure_code
            row.observed_at = _as_datetime(observed_at) if observed_at else row.observed_at
            row.metadata_json = {**(row.metadata_json or {}), **(metadata or {})}
            row.updated_at = now
            db.flush()
            return row.id

    def update_collection_normalization_status(
        self,
        *,
        run_id: str,
        snapshot_id: str,
        normalization_status: str,
        fact_status: str | None = None,
        failure_code: str | None = None,
        authoritative_for_deletion: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if normalization_status not in {"normalized", "empty", "skipped", "blocked", "failed"}:
            raise ValueError(f"unsupported normalization_status: {normalization_status}")
        with get_db_session() as db:
            snapshot = db.get(CommandSnapshot, snapshot_id)
            if snapshot is None or snapshot.run_id != run_id:
                return
            device_id = str(snapshot.inventory_device_id or snapshot.device_id)
            row = (
                db.query(PatrolCollectionResult)
                .filter(
                    PatrolCollectionResult.run_id == run_id,
                    PatrolCollectionResult.device_id == device_id,
                    PatrolCollectionResult.execution_item_id == snapshot.execution_item_id,
                )
                .first()
            )
            if row is None:
                return
            row.normalization_status = normalization_status
            if fact_status is not None:
                row.fact_status = fact_status
            if failure_code is not None:
                row.failure_code = failure_code
            if authoritative_for_deletion is not None:
                row.authoritative_for_deletion = bool(authoritative_for_deletion)
                if not authoritative_for_deletion:
                    row.baseline_eligible = False
            if metadata:
                row.metadata_json = {
                    **dict(row.metadata_json or {}),
                    **dict(metadata),
                }
            row.updated_at = _utcnow()

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

    def mark_collection_analysis_failed(
        self,
        *,
        run_id: str,
        snapshot_id: str,
        failure_code: str,
        error: str,
    ) -> None:
        with get_db_session() as db:
            snapshot = db.get(CommandSnapshot, snapshot_id)
            if snapshot is None or snapshot.run_id != run_id:
                return
            device_id = str(snapshot.inventory_device_id or snapshot.device_id)
            row = (
                db.query(PatrolCollectionResult)
                .filter(
                    PatrolCollectionResult.run_id == run_id,
                    PatrolCollectionResult.device_id == device_id,
                    PatrolCollectionResult.execution_item_id == snapshot.execution_item_id,
                )
                .first()
            )
            if row is None:
                row = PatrolCollectionResult(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    inventory_device_id=snapshot.inventory_device_id,
                    device_id=device_id,
                    execution_item_id=snapshot.execution_item_id,
                    canonical_command=snapshot.canonical_command,
                    created_at=_utcnow(),
                )
                db.add(row)
                row.execution_status = "complete"
                row.parse_status = snapshot.parse_status or "parsed"
            row.status = row.execution_status or "complete"
            row.fact_status = (
                "blocked_by_contract"
                if failure_code in {"contract_version_unavailable", "entity_key_invalid"}
                else "normalization_failed"
            )
            row.normalization_status = "failed"
            row.authoritative_for_deletion = False
            row.baseline_eligible = False
            row.failure_code = failure_code
            row.metadata_json = {
                **(row.metadata_json or {}),
                "analysis_error": error,
                "snapshot_id": snapshot_id,
            }
            row.updated_at = _utcnow()


class PatrolSnapshotRepository(_PatrolRepositoryBase):
    """Immutable command snapshot and dataset query persistence."""

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
        output_status: str = "not_classified",
        start_line: int | None = None,
        end_line: int | None = None,
        dataset_id: str | None = None,
        source_file_hash: str | None = None,
        import_fingerprint: str | None = None,
        snapshot_id: str | None = None,
        snapshot_kind: str = "full",
        metadata: dict[str, Any] | None = None,
        inventory_device_id: str | None = None,
        execution_item_id: str | None = None,
        collection_action_id: str | None = None,
        implementation_version_id: str | None = None,
        check_item_ids: list[str] | tuple[str, ...] | None = None,
        owner_token: str | None = None,
    ) -> tuple[str, bool]:
        if output_status not in OUTPUT_STATUSES:
            raise ValueError(f"unsupported output_status: {output_status}")
        raw_hash = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()
        status = parse_status or ("parsed" if structured_json is not None else "raw_only")
        snapshot_id = snapshot_id or str(uuid.uuid4())
        execution_item_id = (
            execution_item_id
            or hashlib.sha256(
                (
                    f"command-snapshot-item-v1|{run_id}|{device_id}|"
                    f"{command_canonical}|{start_line or 0}"
                ).encode()
            ).hexdigest()
        )
        try:
            with get_db_session() as db:
                self._require_collection_owner(db, run_id, owner_token)
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
                    db.get(NetworkDevice, inventory_device_id) if inventory_device_id else None
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
                    execution_item_id=execution_item_id,
                    collection_action_id=collection_action_id,
                    implementation_version_id=implementation_version_id,
                    check_item_ids_json=sorted(
                        {
                            str(item)
                            for item in check_item_ids or []
                            if str(item)
                        }
                    ),
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
                    output_status=output_status,
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

    def attach_parser_result(
        self,
        *,
        snapshot_id: str,
        expected_raw_text_hash: str,
        parse_status: str,
        structured_json: Any,
        parser_name: str | None,
        parser_version: str | None,
        metadata: dict[str, Any] | None = None,
        owner_token: str | None = None,
    ) -> tuple[str, bool]:
        """Attach one derived Parser result while keeping raw evidence immutable."""
        status = str(parse_status or "").strip()
        if status not in PARSE_STATUSES - {"not_attempted"}:
            raise ValueError(f"unsupported Parser attachment status: {status}")
        with get_db_session() as db:
            row = (
                db.query(CommandSnapshot)
                .filter(CommandSnapshot.id == snapshot_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise ValueError(f"command snapshot not found: {snapshot_id}")
            self._require_collection_owner(db, str(row.run_id), owner_token)
            if str(row.raw_text_hash) != str(expected_raw_text_hash):
                raise ValueError("raw snapshot hash changed before Parser attachment")
            if str(row.parse_status) != "not_attempted":
                same_result = (
                    str(row.parse_status) == status
                    and row.structured_json == structured_json
                    and str(row.parser_template_id or "")
                    == str(parser_name or "")
                    and str(row.parser_version or "")
                    == str(parser_version or "")
                )
                if same_result:
                    return str(row.id), False
                raise ValueError(
                    "command snapshot already has a different Parser result"
                )
            contract_version = None
            if parser_name:
                template = db.get(ParserTemplate, parser_name)
                contract_version = (
                    template.contract_version if template else None
                )
            row.parse_status = status
            row.structured_json = structured_json
            row.parser_template_id = parser_name
            row.parser_version = parser_version
            row.contract_version = contract_version
            row.metadata_json = {
                **dict(row.metadata_json or {}),
                **dict(metadata or {}),
                "parser_attached": True,
            }
            if parser_name:
                parse_result_status = {
                    "parsed": "success",
                    "parse_empty": "empty",
                }.get(status, "failed")
                existing = (
                    db.query(SnapshotParseResult)
                    .filter(
                        SnapshotParseResult.snapshot_id == row.id,
                        SnapshotParseResult.template_id == parser_name,
                    )
                    .one_or_none()
                )
                if existing is None:
                    db.add(
                        SnapshotParseResult(
                            id=str(uuid.uuid4()),
                            snapshot_id=str(row.id),
                            template_id=parser_name,
                            template_version=str(parser_version or ""),
                            parse_status=parse_result_status,
                            structured_json=structured_json,
                            failure_code=(
                                None
                                if parse_result_status in {"success", "empty"}
                                else status
                            ),
                            parsed_at=_utcnow(),
                        )
                    )
            db.flush()
            return str(row.id), True

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

    def list_datasets(
        self,
        *,
        mode: str | None = None,
        dataset_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(
                PatrolDataset,
                func.count(CommandSnapshot.id).label("snapshot_count"),
                func.count(func.distinct(CommandSnapshot.device_id)).label("device_count"),
                func.count(func.distinct(CommandSnapshot.canonical_command)).label("command_count"),
                func.min(CommandSnapshot.created_at).label("first_imported_at"),
                func.max(CommandSnapshot.created_at).label("last_imported_at"),
            ).outerjoin(CommandSnapshot, CommandSnapshot.dataset_id == PatrolDataset.id)
            if mode:
                query = query.filter(PatrolDataset.mode == mode)
            if dataset_ids:
                query = query.filter(PatrolDataset.id.in_(dataset_ids))
            rows = (
                query.group_by(PatrolDataset.id)
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

    def get_offline_dataset_run_scopes(
        self,
        dataset_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Resolve the authoritative current attempt for offline patrol datasets."""

        normalized = sorted({str(item).strip() for item in dataset_ids if str(item).strip()})
        if not normalized:
            return {}
        with get_db_session() as db:
            rows = (
                db.query(
                    OfflinePatrolPeriodRecord,
                    OfflinePatrolJobRecord.purpose,
                )
                .join(
                    OfflinePatrolJobRecord,
                    OfflinePatrolJobRecord.id == OfflinePatrolPeriodRecord.job_id,
                )
                .filter(OfflinePatrolPeriodRecord.dataset_id.in_(normalized))
                .all()
            )
            return {
                period.dataset_id: {
                    "offline_job_id": period.job_id,
                    "job_purpose": purpose,
                    "period_role": period.role,
                    "observed_at": _iso(period.observed_at),
                    "patrol_run_id": period.patrol_run_id,
                    "period_status": period.status,
                }
                for period, purpose in rows
            }

    def list_snapshots_with_entity_keys(self, snapshot_ids: list[str]) -> set[str]:
        """Return normalized snapshots that produced at least one non-empty entity key."""

        normalized = sorted({str(item).strip() for item in snapshot_ids if str(item).strip()})
        if not normalized:
            return set()
        with get_db_session() as db:
            rows = (
                db.query(StateObservationRecord.snapshot_id)
                .filter(
                    StateObservationRecord.snapshot_id.in_(normalized),
                    StateObservationRecord.entity_key != "",
                )
                .distinct()
                .all()
            )
            return {str(snapshot_id) for (snapshot_id,) in rows}

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
                query = query.join(PatrolRun, PatrolRun.id == CommandSnapshot.run_id).filter(
                    PatrolRun.mode == mode
                )
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
                    or_(
                        CommandSnapshot.inventory_device_id == device_id,
                        CommandSnapshot.device_id == device_id,
                    ),
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
                    or_(
                        CommandSnapshot.inventory_device_id == device_id,
                        CommandSnapshot.device_id == device_id,
                    ),
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
                query = query.filter(
                    or_(
                        CommandSnapshot.inventory_device_id == device_id,
                        CommandSnapshot.device_id == device_id,
                    )
                )
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
            execution_item_id=row.execution_item_id,
            output_status=row.output_status,
            metadata=dict(row.metadata_json or {}),
        )


class PatrolRepository(
    PatrolRunRepositoryBackend,
    PatrolAnalysisAttemptRepository,
    PatrolCollectionResultRepository,
    PatrolSnapshotRepository,
):
    """Compatibility facade; new callers should depend on one aggregate repository."""
