"""Immutable human sign-off for deterministic patrol report artifacts."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from src.infrastructure.db.models import ReportManifestRecord, ReportReleaseRecord
from src.infrastructure.db.postgres import get_db_session

REQUIRED_DISCLOSURE_CONFIRMATIONS = {
    "facts_reviewed",
    "unknown_scope_disclosed",
    "artifact_digests_verified",
}


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


class ReportReleaseService:
    """Sign one final manifest only after exact artifact hashes are verified."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def release(
        self,
        report_manifest_id: str,
        *,
        signed_by: str,
        signoff_comment: str,
        disclosure_confirmations: Iterable[str],
        verified_artifact_sha256: Mapping[str, str],
        signed_at: datetime | None = None,
    ) -> dict[str, Any]:
        actor = str(signed_by or "").strip()
        comment = str(signoff_comment or "").strip()
        if not actor:
            raise ValueError("report release requires a signer")
        if not comment:
            raise ValueError("report release requires a sign-off comment")
        confirmations = sorted(
            {
                str(item).strip()
                for item in disclosure_confirmations
                if str(item).strip()
            }
        )
        missing = sorted(
            REQUIRED_DISCLOSURE_CONFIRMATIONS - set(confirmations)
        )
        if missing:
            raise ValueError(
                "report release is missing disclosure confirmations: "
                + ", ".join(missing)
            )
        verified = {
            str(name): str(digest).strip().lower()
            for name, digest in verified_artifact_sha256.items()
        }
        with self._session_scope() as db:
            manifest = (
                db.query(ReportManifestRecord)
                .filter(ReportManifestRecord.id == report_manifest_id)
                .with_for_update()
                .first()
            )
            if manifest is None:
                raise ValueError("report manifest not found")
            existing = (
                db.query(ReportReleaseRecord)
                .filter(
                    ReportReleaseRecord.report_manifest_id == manifest.id
                )
                .first()
            )
            if existing is not None:
                if (
                    dict(existing.artifact_sha256_json or {}) != verified
                    or str(existing.source_facts_digest)
                    != str(manifest.source_digest)
                ):
                    raise ValueError(
                        "report manifest already has a different immutable release"
                    )
                return existing.to_dict()
            if str(manifest.status) != "final":
                raise ValueError("only a final report manifest can be released")
            attached = {
                str(name): str(digest).lower()
                for name, digest in dict(
                    manifest.artifact_sha256_json or {}
                ).items()
            }
            object_keys = dict(manifest.artifact_object_keys_json or {})
            if not attached or set(attached) != set(object_keys):
                raise ValueError(
                    "report artifacts must be attached before release"
                )
            if verified != attached:
                raise ValueError(
                    "verified artifact SHA-256 values do not match the manifest"
                )
            if any(
                len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in verified.values()
            ):
                raise ValueError("report release requires valid SHA-256 values")
            occurred_at = signed_at or datetime.now(timezone.utc)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
            payload = {
                "report_manifest_id": str(manifest.id),
                "patrol_batch_id": str(manifest.patrol_batch_id),
                "source_facts_digest": str(manifest.source_digest),
                "artifact_sha256": verified,
                "signed_by": actor,
                "signed_at": occurred_at.isoformat(),
                "signoff_comment": comment,
                "disclosure_confirmations": confirmations,
            }
            release_digest = _digest(payload)
            release_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/report-release/{release_digest}",
                )
            )
            row = ReportReleaseRecord(
                id=release_id,
                report_manifest_id=manifest.id,
                patrol_batch_id=manifest.patrol_batch_id,
                status="released",
                source_facts_digest=manifest.source_digest,
                artifact_sha256_json=verified,
                release_digest=release_digest,
                signed_by=actor,
                signed_at=occurred_at,
                signoff_comment=comment,
                disclosure_confirmations_json=confirmations,
                created_at=datetime.now(timezone.utc),
            )
            db.add(row)
            db.flush()
            return row.to_dict()

    def get_for_manifest(
        self,
        report_manifest_id: str,
    ) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = (
                db.query(ReportReleaseRecord)
                .filter(
                    ReportReleaseRecord.report_manifest_id
                    == report_manifest_id
                )
                .first()
            )
            return row.to_dict() if row is not None else None

    def latest_for_batch(
        self,
        patrol_batch_id: str,
    ) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = (
                db.query(ReportReleaseRecord)
                .filter(
                    ReportReleaseRecord.patrol_batch_id == patrol_batch_id
                )
                .order_by(
                    ReportReleaseRecord.signed_at.desc(),
                    ReportReleaseRecord.id.desc(),
                )
                .first()
            )
            return row.to_dict() if row is not None else None
