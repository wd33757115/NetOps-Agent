"""Fail-closed provenance guards for rule-asset generation inputs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

from src.infrastructure.db.models import (
    CommandSnapshot,
    FileMetadata,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
    PatrolDataset,
    PatrolRun,
    RuleGenerationContextRecord,
)

RULE_ASSET_SOURCE_PURPOSE = "rule_asset_source"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RuleAssetSourceScopeError(ValueError):
    """A stable, product-facing reason why source evidence is not immutable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def require_rule_asset_source_job(
    db: Any,
    job_id: str,
    *,
    for_update: bool = False,
) -> OfflinePatrolJobRecord:
    """Return a governed asset-source job and reject patrol execution jobs."""

    query = db.query(OfflinePatrolJobRecord).filter(
        OfflinePatrolJobRecord.id == job_id
    )
    if for_update:
        query = query.with_for_update()
    job = query.one_or_none()
    if job is None:
        raise KeyError("rule asset source job not found")
    if job.purpose != RULE_ASSET_SOURCE_PURPOSE:
        raise ValueError(
            "rule assets can only be generated from Parser control asset-source jobs"
        )
    return job


def _scope_error(code: str, message: str) -> RuleAssetSourceScopeError:
    return RuleAssetSourceScopeError(code, message)


def _scope_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def rule_generation_context_digest(
    *,
    job_id: str,
    dataset_ids: Iterable[str],
    context: dict[str, Any],
    privacy: dict[str, Any],
) -> str:
    """Hash every persisted input that a generated rule bundle depends on."""

    return _scope_digest(
        {
            "job_id": str(job_id),
            "dataset_ids": sorted(
                {
                    str(dataset_id).strip()
                    for dataset_id in dataset_ids
                    if str(dataset_id).strip()
                }
            ),
            "context": dict(context or {}),
            "privacy": dict(privacy or {}),
        }
    )


def require_immutable_rule_asset_source_datasets(
    db: Any,
    dataset_ids: Iterable[str],
    *,
    snapshot_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Prove that selected datasets are completed, hashed asset-source evidence.

    The returned digest binds the offline jobs, periods, authoritative PatrolRuns,
    verified source files and optional Parser sample snapshots. Callers must
    persist the digest and re-evaluate it immediately before side effects.
    """

    selected_dataset_ids = sorted(
        {
            str(dataset_id).strip()
            for dataset_id in dataset_ids
            if str(dataset_id).strip()
        }
    )
    if not selected_dataset_ids:
        raise _scope_error(
            "dataset_missing",
            "rule generation requires at least one offline asset-source dataset",
        )

    periods = (
        db.query(OfflinePatrolPeriodRecord)
        .filter(OfflinePatrolPeriodRecord.dataset_id.in_(selected_dataset_ids))
        .order_by(
            OfflinePatrolPeriodRecord.dataset_id,
            OfflinePatrolPeriodRecord.id,
        )
        .all()
    )
    period_by_dataset = {str(period.dataset_id): period for period in periods}
    if len(period_by_dataset) != len(periods):
        raise _scope_error(
            "dataset_ambiguous",
            "an offline dataset is mapped to more than one patrol period",
        )
    missing_periods = sorted(set(selected_dataset_ids) - set(period_by_dataset))
    if missing_periods:
        raise _scope_error(
            "dataset_not_governed",
            "datasets are not owned by Parser control asset-source jobs: "
            + ", ".join(missing_periods),
        )

    job_ids = sorted({str(period.job_id) for period in periods})
    jobs = (
        db.query(OfflinePatrolJobRecord)
        .filter(OfflinePatrolJobRecord.id.in_(job_ids))
        .order_by(OfflinePatrolJobRecord.id)
        .all()
    )
    job_by_id = {str(job.id): job for job in jobs}
    if set(job_by_id) != set(job_ids):
        raise _scope_error(
            "source_job_missing",
            "an offline asset-source job referenced by a dataset no longer exists",
        )
    for job in jobs:
        if str(job.purpose) != RULE_ASSET_SOURCE_PURPOSE:
            raise _scope_error(
                "source_job_wrong_purpose",
                "rule assets can only be generated from Parser control asset-source jobs",
            )
        if str(job.status) != "completed":
            raise _scope_error(
                "source_job_not_completed",
                f"asset-source job {job.id} is not completed",
            )
        if bool(job.archived):
            raise _scope_error(
                "source_job_archived",
                f"asset-source job {job.id} is archived",
            )

    datasets = (
        db.query(PatrolDataset)
        .filter(PatrolDataset.id.in_(selected_dataset_ids))
        .order_by(PatrolDataset.id)
        .all()
    )
    dataset_by_id = {str(dataset.id): dataset for dataset in datasets}
    if set(dataset_by_id) != set(selected_dataset_ids):
        raise _scope_error(
            "dataset_record_missing",
            "an offline asset-source dataset record no longer exists",
        )
    for dataset in datasets:
        if str(dataset.mode) != "parser_validation":
            raise _scope_error(
                "dataset_wrong_mode",
                f"dataset {dataset.id} is not a parser_validation dataset",
            )

    run_ids: list[str] = []
    for period in periods:
        if str(period.status) != "completed":
            raise _scope_error(
                "period_not_completed",
                f"offline period {period.id} is not completed",
            )
        run_id = str(period.patrol_run_id or "").strip()
        if not run_id:
            raise _scope_error(
                "authoritative_run_missing",
                f"offline period {period.id} has no authoritative PatrolRun",
            )
        run_ids.append(run_id)

    runs = (
        db.query(PatrolRun)
        .filter(PatrolRun.id.in_(run_ids))
        .order_by(PatrolRun.id)
        .all()
    )
    run_by_id = {str(run.id): run for run in runs}
    if set(run_by_id) != set(run_ids):
        raise _scope_error(
            "authoritative_run_missing",
            "an authoritative PatrolRun referenced by an offline period no longer exists",
        )
    for period in periods:
        run = run_by_id[str(period.patrol_run_id)]
        if str(run.dataset_id or "") != str(period.dataset_id):
            raise _scope_error(
                "authoritative_run_dataset_mismatch",
                f"PatrolRun {run.id} does not belong to dataset {period.dataset_id}",
            )
        if str(run.mode) != "parser_validation" or str(run.run_purpose) != (
            "parser_validation"
        ):
            raise _scope_error(
                "authoritative_run_wrong_purpose",
                f"PatrolRun {run.id} is not governed parser-validation evidence",
            )
        if str(run.status) != "completed" or str(run.collection_status) != (
            "completed"
        ):
            raise _scope_error(
                "authoritative_run_not_completed",
                f"PatrolRun {run.id} did not complete collection successfully",
            )
        metadata = dict(run.metadata_json or {})
        if (
            str(metadata.get("offline_job_id") or "") != str(period.job_id)
            or str(metadata.get("period_role") or "") != str(period.role)
        ):
            raise _scope_error(
                "authoritative_run_identity_mismatch",
                f"PatrolRun {run.id} is not bound to its offline job and period",
            )

    source_rows = (
        db.query(OfflinePatrolSourceFileRecord)
        .filter(OfflinePatrolSourceFileRecord.job_id.in_(job_ids))
        .order_by(
            OfflinePatrolSourceFileRecord.job_id,
            OfflinePatrolSourceFileRecord.period_role,
            OfflinePatrolSourceFileRecord.relative_path,
            OfflinePatrolSourceFileRecord.id,
        )
        .all()
    )
    source_by_period: dict[tuple[str, str], list[OfflinePatrolSourceFileRecord]] = {}
    for source in source_rows:
        source_by_period.setdefault(
            (str(source.job_id), str(source.period_role)),
            [],
        ).append(source)
    file_ids = sorted({str(source.file_id) for source in source_rows})
    files = (
        db.query(FileMetadata)
        .filter(FileMetadata.id.in_(file_ids))
        .all()
        if file_ids
        else []
    )
    file_by_id = {str(file.id): file for file in files}
    for period in periods:
        period_sources = source_by_period.get(
            (str(period.job_id), str(period.role)),
            [],
        )
        if not period_sources:
            raise _scope_error(
                "source_file_missing",
                f"offline period {period.id} has no retained source file",
            )
        for source in period_sources:
            digest = str(source.sha256 or "").strip().casefold()
            if (
                str(source.status) != "verified"
                or not _SHA256_PATTERN.fullmatch(digest)
                or int(source.size_bytes or 0) <= 0
            ):
                raise _scope_error(
                    "source_file_unverified",
                    f"source file {source.id} has no verified SHA-256 evidence",
                )
            file_row = file_by_id.get(str(source.file_id))
            if (
                file_row is None
                or bool(file_row.is_deleted)
                or str(file_row.status) != "active"
            ):
                raise _scope_error(
                    "source_file_unavailable",
                    f"source file {source.id} is no longer retained and accessible",
                )

    selected_snapshot_ids = sorted(
        {
            str(snapshot_id).strip()
            for snapshot_id in snapshot_ids or ()
            if str(snapshot_id).strip()
        }
    )
    snapshot_manifest: list[dict[str, str]] = []
    if selected_snapshot_ids:
        snapshots = (
            db.query(CommandSnapshot)
            .filter(CommandSnapshot.id.in_(selected_snapshot_ids))
            .order_by(CommandSnapshot.id)
            .all()
        )
        snapshot_by_id = {str(snapshot.id): snapshot for snapshot in snapshots}
        if set(snapshot_by_id) != set(selected_snapshot_ids):
            raise _scope_error(
                "sample_snapshot_missing",
                "a Parser sample snapshot no longer exists",
            )
        for snapshot in snapshots:
            dataset_id = str(snapshot.dataset_id or "")
            period = period_by_dataset.get(dataset_id)
            if period is None or str(snapshot.run_id) != str(period.patrol_run_id):
                raise _scope_error(
                    "sample_snapshot_outside_scope",
                    f"Parser sample snapshot {snapshot.id} is outside the authoritative "
                    "offline period run",
                )
            snapshot_manifest.append(
                {
                    "snapshot_id": str(snapshot.id),
                    "dataset_id": dataset_id,
                    "run_id": str(snapshot.run_id),
                    "raw_text_hash": str(snapshot.raw_text_hash or ""),
                }
            )

    manifest = {
        "scope_version": 1,
        "jobs": [
            {
                "job_id": str(job.id),
                "purpose": str(job.purpose),
                "status": str(job.status),
                "attempt_number": int(job.attempt_number or 0),
            }
            for job in jobs
        ],
        "periods": [
            {
                "period_id": str(period.id),
                "job_id": str(period.job_id),
                "role": str(period.role),
                "dataset_id": str(period.dataset_id),
                "run_id": str(period.patrol_run_id),
                "observed_at": period.observed_at.isoformat(),
            }
            for period in periods
        ],
        "runs": [
            {
                "run_id": str(run.id),
                "dataset_id": str(run.dataset_id),
                "mode": str(run.mode),
                "status": str(run.status),
                "collection_status": str(run.collection_status),
                "run_purpose": str(run.run_purpose),
            }
            for run in runs
        ],
        "source_files": [
            {
                "source_id": str(source.id),
                "job_id": str(source.job_id),
                "period_role": str(source.period_role),
                "file_id": str(source.file_id),
                "relative_path": str(source.relative_path),
                "sha256": str(source.sha256).casefold(),
                "size_bytes": int(source.size_bytes),
            }
            for source in source_rows
            if (str(source.job_id), str(source.period_role))
            in {
                (str(period.job_id), str(period.role))
                for period in periods
            }
        ],
        "snapshots": snapshot_manifest,
    }
    return {
        "dataset_ids": selected_dataset_ids,
        "job_ids": job_ids,
        "period_scope": {
            str(period.dataset_id): str(period.patrol_run_id)
            for period in periods
        },
        "manifest": manifest,
        "scope_digest": _scope_digest(manifest),
    }


def require_current_rule_generation_context(
    db: Any,
    context: RuleGenerationContextRecord,
    *,
    expected_context_digest: str | None = None,
    current_source_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-prove a frozen context immediately before a governed side effect."""

    if not str(context.offline_job_id or "").strip():
        raise _scope_error(
            "context_source_job_missing",
            "rule generation context is not backed by an offline patrol job",
        )
    privacy = dict(context.privacy_report_json or {})
    if (
        privacy.get("success") is not True
        or privacy.get("raw_cli_included") is not False
        or privacy.get("scope_type") != "offline_period_authoritative_runs"
    ):
        raise _scope_error(
            "context_privacy_invalid",
            "rule generation context privacy evidence is incomplete",
        )

    stored_digest = str(context.context_digest or "").strip()
    computed_digest = rule_generation_context_digest(
        job_id=str(context.job_id),
        dataset_ids=list(context.dataset_ids_json or []),
        context=dict(context.context_json or {}),
        privacy=privacy,
    )
    if not stored_digest or stored_digest != computed_digest:
        raise _scope_error(
            "context_content_changed",
            "rule generation context content changed after it was frozen",
        )
    expected_digest = str(expected_context_digest or "").strip()
    if expected_digest and expected_digest != stored_digest:
        raise _scope_error(
            "context_digest_mismatch",
            "rule bundle does not reference the current immutable context digest",
        )

    source_scope = current_source_scope or require_immutable_rule_asset_source_datasets(
        db,
        list(context.dataset_ids_json or []),
    )
    frozen_source_digest = str(privacy.get("source_scope_digest") or "").strip()
    if not frozen_source_digest or frozen_source_digest != str(
        source_scope["scope_digest"]
    ):
        raise _scope_error(
            "context_source_changed",
            "rule generation context source evidence changed after it was frozen",
        )
    if privacy.get("source_scope_manifest") != source_scope["manifest"]:
        raise _scope_error(
            "context_source_manifest_changed",
            "rule generation context source manifest no longer matches retained evidence",
        )

    source_job_ids = list(source_scope["job_ids"])
    if len(source_job_ids) != 1 or str(context.offline_job_id or "") != str(
        source_job_ids[0]
    ):
        raise _scope_error(
            "context_source_job_mismatch",
            "rule generation context is not bound to exactly one offline asset-source job",
        )
    return {
        "success": True,
        "context_id": str(context.id),
        "context_digest": stored_digest,
        "source_scope_digest": str(source_scope["scope_digest"]),
        "offline_job_id": str(context.offline_job_id),
        "dataset_ids": list(source_scope["dataset_ids"]),
        "source_scope": source_scope,
    }


__all__ = [
    "RULE_ASSET_SOURCE_PURPOSE",
    "RuleAssetSourceScopeError",
    "require_immutable_rule_asset_source_datasets",
    "require_current_rule_generation_context",
    "require_rule_asset_source_job",
    "rule_generation_context_digest",
]
