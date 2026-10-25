"""Governed qualification for offline evidence that may describe current state."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from src.infrastructure.db.models import (
    OfflinePatrolDeviceBindingRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
    PatrolBatchRecord,
    PatrolScopeDecisionRecord,
    PatrolSourceQualificationRecord,
)
from src.infrastructure.db.postgres import get_db_session


LEGACY_SOURCE_QUALIFICATION_SCHEMA_VERSION = "patrol-source-qualification/v1"
SOURCE_QUALIFICATION_SCHEMA_VERSION = "patrol-source-qualification/v2"
ATTESTED_OFFLINE_CURRENT = "attested_offline_current"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        )
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_attested_offline_current_qualification(
    *,
    qualification_id: str,
    patrol_batch_id: str,
    offline_job_id: str,
    version: int,
    observed_at_min: datetime | str,
    observed_at_max: datetime | str,
    source_file_manifest: Sequence[Mapping[str, Any]],
    identity_bindings: Sequence[Mapping[str, Any]],
    scope_contract_digest: str,
    intended_target_matrix_digest: str,
    completeness_statement: str,
    completeness_evidence_refs: Sequence[str],
    collection_tool: str,
    collector_identity: str,
    imported_by: str,
    attested_by: str,
    attested_at: datetime | str,
    request_id: str | None = None,
    request_digest: str | None = None,
    imported_by_principal_id: str | None = None,
    imported_by_display: str | None = None,
    attested_by_principal_id: str | None = None,
    attested_by_display: str | None = None,
    supersedes_id: str | None = None,
    version_reason: str | None = None,
) -> dict[str, Any]:
    """Build one immutable, independently attested qualification."""

    observed_min = _utc(observed_at_min)
    observed_max = _utc(observed_at_max)
    attested = _utc(attested_at)
    if observed_min > observed_max:
        raise ValueError("offline observed_at range is invalid")
    if not imported_by or not attested_by:
        raise ValueError("imported_by and attested_by are required")
    imported_principal = str(imported_by_principal_id or imported_by).strip()
    attested_principal = str(attested_by_principal_id or attested_by).strip()
    if not imported_principal or not attested_principal:
        raise ValueError("importer and attester principal ids are required")
    if imported_principal == attested_principal:
        raise ValueError("offline current qualification requires an independent attester")
    governed_request_id = str(request_id or "").strip() or None
    governed_request_digest = str(request_digest or "").strip() or None
    if bool(governed_request_id) != bool(governed_request_digest):
        raise ValueError("source qualification request id and digest must be supplied together")
    if governed_request_digest and len(governed_request_digest) != 64:
        raise ValueError("request_digest must be a SHA-256 digest")
    for name, value in (
        ("scope_contract_digest", scope_contract_digest),
        ("intended_target_matrix_digest", intended_target_matrix_digest),
    ):
        if len(str(value or "")) != 64:
            raise ValueError(f"{name} must be a SHA-256 digest")

    files = sorted(
        (dict(item) for item in source_file_manifest),
        key=lambda item: (
            str(item.get("period_role") or ""),
            str(item.get("relative_path") or ""),
            str(item.get("sha256") or ""),
        ),
    )
    if not files or any(
        len(str(item.get("sha256") or "")) != 64 or str(item.get("status") or "") != "verified"
        for item in files
    ):
        raise ValueError("every offline source file must be hash-verified")
    bindings = sorted(
        (dict(item) for item in identity_bindings),
        key=lambda item: str(item.get("external_device_id") or ""),
    )
    if not bindings or any(
        str(item.get("match_status") or "") != "confirmed"
        or not str(item.get("inventory_device_id") or "")
        or not str(item.get("confirmed_by") or "")
        for item in bindings
    ):
        raise ValueError("every offline device must be confirmed against inventory")
    statement = str(completeness_statement or "").strip()
    evidence_refs = sorted(
        {str(item).strip() for item in completeness_evidence_refs if str(item).strip()}
    )
    if len(statement) < 10 or not evidence_refs:
        raise ValueError("completeness attestation requires a statement and evidence refs")
    if not str(collection_tool or "").strip() or not str(collector_identity or "").strip():
        raise ValueError("collection tool and collector identity are required")

    chain_of_custody = {
        "offline_job_id": offline_job_id,
        "files": files,
        "collection_tool": str(collection_tool).strip(),
        "collector_identity": str(collector_identity).strip(),
        "imported_by_principal_id": imported_principal,
    }
    identity_facts = {
        "offline_job_id": offline_job_id,
        "bindings": bindings,
    }
    completeness_facts = {
        "statement": statement,
        "evidence_refs": evidence_refs,
        "source_file_count": len(files),
        "bound_device_count": len(bindings),
    }
    payload = {
        "schema_version": (
            SOURCE_QUALIFICATION_SCHEMA_VERSION
            if governed_request_id
            else LEGACY_SOURCE_QUALIFICATION_SCHEMA_VERSION
        ),
        "qualification_id": qualification_id,
        "request_id": governed_request_id,
        "request_digest": governed_request_digest,
        "patrol_batch_id": patrol_batch_id,
        "offline_job_id": offline_job_id,
        "version": int(version),
        "acquisition_mode": "offline_upload",
        "source_kind": ATTESTED_OFFLINE_CURRENT,
        "status": "approved",
        "observed_at_min": observed_min.isoformat(),
        "observed_at_max": observed_max.isoformat(),
        "chain_of_custody_digest": _digest(chain_of_custody),
        "identity_binding_digest": _digest(identity_facts),
        "scope_contract_digest": scope_contract_digest,
        "intended_target_matrix_digest": intended_target_matrix_digest,
        "completeness_evidence_digest": _digest(completeness_facts),
        "chain_of_custody": chain_of_custody,
        "identity_bindings": identity_facts,
        "completeness_evidence": completeness_facts,
        "imported_by": imported_principal,
        "attested_by": attested_principal,
        "imported_by_principal_id": imported_principal,
        "imported_by_display": str(imported_by_display or imported_by).strip(),
        "attested_by_principal_id": attested_principal,
        "attested_by_display": str(attested_by_display or attested_by).strip(),
        "supersedes_id": str(supersedes_id or "").strip() or None,
        "version_reason": str(version_reason or "").strip() or None,
        "attested_at": attested.isoformat(),
    }
    payload["digest"] = _digest(payload)
    return payload


def source_qualification_is_valid(
    value: Mapping[str, Any] | None,
    *,
    expected_patrol_batch_id: str | None = None,
    expected_scope_contract_digest: str | None = None,
    expected_intended_target_matrix_digest: str | None = None,
) -> bool:
    qualification = dict(value or {})
    supplied_digest = str(qualification.pop("digest", "") or "")
    if (
        len(supplied_digest) != 64
        or _digest(qualification) != supplied_digest
        or qualification.get("schema_version")
        not in {
            LEGACY_SOURCE_QUALIFICATION_SCHEMA_VERSION,
            SOURCE_QUALIFICATION_SCHEMA_VERSION,
        }
        or qualification.get("acquisition_mode") != "offline_upload"
        or qualification.get("source_kind") != ATTESTED_OFFLINE_CURRENT
        or qualification.get("status") != "approved"
    ):
        return False
    imported_principal = str(
        qualification.get("imported_by_principal_id") or qualification.get("imported_by") or ""
    )
    attested_principal = str(
        qualification.get("attested_by_principal_id") or qualification.get("attested_by") or ""
    )
    if not imported_principal or not attested_principal or imported_principal == attested_principal:
        return False
    if qualification.get("schema_version") == SOURCE_QUALIFICATION_SCHEMA_VERSION and (
        not qualification.get("request_id")
        or len(str(qualification.get("request_digest") or "")) != 64
    ):
        return False
    if expected_patrol_batch_id and (
        qualification.get("patrol_batch_id") != expected_patrol_batch_id
    ):
        return False
    if expected_scope_contract_digest and (
        qualification.get("scope_contract_digest") != expected_scope_contract_digest
    ):
        return False
    if expected_intended_target_matrix_digest and (
        qualification.get("intended_target_matrix_digest") != expected_intended_target_matrix_digest
    ):
        return False
    try:
        return _utc(qualification["observed_at_min"]) <= _utc(qualification["observed_at_max"])
    except (KeyError, TypeError, ValueError):
        return False


class PatrolSourceQualificationService:
    """Create and revalidate current-authority qualifications."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (get_db_session),
    ) -> None:
        self._session_scope = session_scope

    def attest_offline_current(
        self,
        *,
        patrol_batch_id: str,
        offline_job_id: str,
        attested_by: str,
        completeness_statement: str,
        completeness_evidence_refs: Sequence[str],
        collection_tool: str,
        collector_identity: str,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            batch = (
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == patrol_batch_id)
                .with_for_update()
                .one_or_none()
            )
            if batch is None:
                raise ValueError("patrol batch not found")
            if str(batch.target_type or "") != "offline_import":
                raise ValueError("only an offline-import batch can be attested")
            if str(batch.report_purpose or "") != "production_current":
                raise ValueError("batch was not created with current-attestation intent")
            if not batch.scope_contract_digest or not (batch.intended_target_matrix_digest):
                raise ValueError("patrol scope must be frozen before source attestation")
            unresolved_count = (
                db.query(PatrolScopeDecisionRecord)
                .filter(
                    PatrolScopeDecisionRecord.patrol_batch_id == patrol_batch_id,
                    PatrolScopeDecisionRecord.decision == "unresolved",
                )
                .count()
            )
            if unresolved_count:
                raise ValueError("patrol scope contains unresolved target decisions")
            job = db.get(OfflinePatrolJobRecord, offline_job_id)
            if job is None or str(job.purpose) != "offline_patrol":
                raise ValueError("offline patrol job not found")
            if str(job.report_intent or "historical") != ("current_attestation"):
                raise ValueError("offline job was not created for current attestation")
            if str(job.status or "") != "completed":
                raise ValueError("offline patrol job must be completed before attestation")
            selector = dict(batch.selector_json or {})
            if str(selector.get("job_id") or "") != offline_job_id:
                raise ValueError("patrol batch and offline job identity do not match")
            period = (
                db.query(OfflinePatrolPeriodRecord)
                .filter(
                    OfflinePatrolPeriodRecord.job_id == offline_job_id,
                    OfflinePatrolPeriodRecord.role == "current",
                )
                .one_or_none()
            )
            if period is None:
                raise ValueError("offline job has no current period")
            source_rows = (
                db.query(OfflinePatrolSourceFileRecord)
                .filter(
                    OfflinePatrolSourceFileRecord.job_id == offline_job_id,
                    OfflinePatrolSourceFileRecord.period_role == "current",
                )
                .all()
            )
            binding_rows = (
                db.query(OfflinePatrolDeviceBindingRecord)
                .filter(
                    OfflinePatrolDeviceBindingRecord.job_id == offline_job_id,
                    OfflinePatrolDeviceBindingRecord.period_role == "current",
                )
                .all()
            )
            latest = (
                db.query(PatrolSourceQualificationRecord)
                .filter(PatrolSourceQualificationRecord.patrol_batch_id == patrol_batch_id)
                .order_by(PatrolSourceQualificationRecord.version.desc())
                .first()
            )
            if latest is not None and latest.status == "approved":
                return latest.to_dict()
            version = int(latest.version or 0) + 1 if latest else 1
            qualification_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/patrol-source-qualification/{patrol_batch_id}/{version}",
                )
            )
            payload = build_attested_offline_current_qualification(
                qualification_id=qualification_id,
                patrol_batch_id=patrol_batch_id,
                offline_job_id=offline_job_id,
                version=version,
                observed_at_min=period.observed_at,
                observed_at_max=period.observed_at,
                source_file_manifest=[
                    {
                        "source_id": str(row.id),
                        "period_role": str(row.period_role),
                        "file_id": str(row.file_id),
                        "relative_path": str(row.relative_path),
                        "sha256": str(row.sha256 or ""),
                        "size_bytes": int(row.size_bytes or 0),
                        "status": str(row.status or ""),
                    }
                    for row in source_rows
                ],
                identity_bindings=[
                    {
                        "binding_id": str(row.id),
                        "external_device_id": str(row.external_device_id),
                        "inventory_device_id": str(row.inventory_device_id or ""),
                        "match_status": str(row.match_status or ""),
                        "confirmed_by": str(row.confirmed_by or ""),
                        "confirmed_at": (
                            row.confirmed_at.isoformat() if row.confirmed_at else None
                        ),
                    }
                    for row in binding_rows
                ],
                scope_contract_digest=str(batch.scope_contract_digest),
                intended_target_matrix_digest=str(batch.intended_target_matrix_digest),
                completeness_statement=completeness_statement,
                completeness_evidence_refs=completeness_evidence_refs,
                collection_tool=collection_tool,
                collector_identity=collector_identity,
                imported_by=str(job.owner_id),
                attested_by=attested_by,
                attested_at=now,
            )
            row = PatrolSourceQualificationRecord(
                id=qualification_id,
                patrol_batch_id=patrol_batch_id,
                offline_job_id=offline_job_id,
                version=version,
                acquisition_mode="offline_upload",
                source_kind=ATTESTED_OFFLINE_CURRENT,
                status="approved",
                observed_at_min=_utc(payload["observed_at_min"]),
                observed_at_max=_utc(payload["observed_at_max"]),
                chain_of_custody_digest=payload["chain_of_custody_digest"],
                identity_binding_digest=payload["identity_binding_digest"],
                scope_contract_digest=payload["scope_contract_digest"],
                intended_target_matrix_digest=payload["intended_target_matrix_digest"],
                completeness_evidence_digest=payload["completeness_evidence_digest"],
                qualification_json=payload,
                digest=payload["digest"],
                imported_by=str(job.owner_id),
                attested_by=attested_by,
                attested_at=now,
                created_at=now,
            )
            db.add(row)
            batch.source_qualification_id = qualification_id
            batch.source_qualification_digest = payload["digest"]
            batch.updated_at = now
            db.flush()
            return row.to_dict()

    def get_for_batch(self, patrol_batch_id: str) -> dict[str, Any] | None:
        with self._session_scope() as db:
            batch = db.get(PatrolBatchRecord, patrol_batch_id)
            if batch is None or not batch.source_qualification_id:
                return None
            row = db.get(
                PatrolSourceQualificationRecord,
                batch.source_qualification_id,
            )
            if row is None:
                return None
            payload = dict(row.qualification_json or {})
            if not source_qualification_is_valid(
                payload,
                expected_patrol_batch_id=patrol_batch_id,
                expected_scope_contract_digest=str(batch.scope_contract_digest or ""),
                expected_intended_target_matrix_digest=str(
                    batch.intended_target_matrix_digest or ""
                ),
            ):
                raise ValueError("stored patrol source qualification is invalid")
            return {
                **payload,
                "lifecycle_status": str(row.status),
                "revoked_at": row.revoked_at,
                "revoked_by_principal_id": str(row.revoked_by_principal_id or row.revoked_by or "")
                or None,
                "revocation_reason": row.revocation_reason,
            }


__all__ = [
    "ATTESTED_OFFLINE_CURRENT",
    "PatrolSourceQualificationService",
    "SOURCE_QUALIFICATION_SCHEMA_VERSION",
    "build_attested_offline_current_qualification",
    "source_qualification_is_valid",
]
