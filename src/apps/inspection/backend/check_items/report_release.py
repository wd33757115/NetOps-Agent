"""Immutable human sign-off for deterministic patrol report artifacts."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from src.common.governance import actor_matches_any
from src.core.check_items.report_contract import (
    build_report_conclusion_permission_from_source_facts,
    normalize_report_purpose,
    report_authority_context_is_valid,
    report_conclusion_permission_is_valid,
)
from src.core.check_items.report_content_review import (
    ReportContentReviewService,
)
from src.core.check_items.report_claims import (
    validate_report_claim_ledger,
)
from src.core.check_items.source_qualification import (
    ATTESTED_OFFLINE_CURRENT,
    source_qualification_is_valid,
)
from src.infrastructure.db.models import (
    PatrolBatchRecord,
    PatrolSourceQualificationRecord,
    ReportClaimRecord,
    ReportManifestRecord,
    ReportReleaseRecord,
)
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
        *,
        object_storage: Any | None = None,
    ) -> None:
        self._session_scope = session_scope
        self._object_storage = object_storage

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
            {str(item).strip() for item in disclosure_confirmations if str(item).strip()}
        )
        missing = sorted(REQUIRED_DISCLOSURE_CONFIRMATIONS - set(confirmations))
        if missing:
            raise ValueError(
                "report release is missing disclosure confirmations: " + ", ".join(missing)
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
            if actor_matches_any(actor, [manifest.generated_by]):
                raise ValueError(
                    "report release requires a signer different from the manifest generator"
                )
            existing = (
                db.query(ReportReleaseRecord)
                .filter(ReportReleaseRecord.report_manifest_id == manifest.id)
                .first()
            )
            if str(manifest.status) != "final":
                raise ValueError("only a final report manifest can be released")
            source_facts = dict(manifest.source_facts_json or {})
            if _digest(source_facts) != str(manifest.source_digest):
                raise ValueError("report source facts do not match the immutable digest")
            facts_purpose = normalize_report_purpose(source_facts.get("report_purpose"))
            manifest_purpose = normalize_report_purpose(manifest.report_purpose)
            if facts_purpose != manifest_purpose:
                raise ValueError("report purpose does not match the frozen source facts")
            source_schema = str(source_facts.get("schema_version") or "")
            if source_schema in {
                "patrol-report-source/v3",
                "patrol-report-source/v4",
                "patrol-report-source/v5",
            }:
                authority_context = dict(source_facts.get("report_authority_context") or {})
                if not report_authority_context_is_valid(
                    authority_context,
                    expected_purpose=manifest_purpose,
                ):
                    raise ValueError("report authority context is invalid or inconsistent")
                if not authority_context.get("purpose_source_consistent"):
                    raise ValueError("report purpose is inconsistent with source evidence")
                frozen_authority_digests = {
                    "target_manifest_digest": str(manifest.target_manifest_digest or ""),
                    "runtime_manifest_digest": str(manifest.runtime_manifest_digest or ""),
                    "inventory_snapshot_digest": str(
                        source_facts.get("inventory_snapshot_digest") or ""
                    ),
                }
                if any(
                    str(authority_context.get(key) or "") != value
                    for key, value in frozen_authority_digests.items()
                ):
                    raise ValueError("report authority digests do not match frozen facts")
                if authority_context.get("source_kind") == (ATTESTED_OFFLINE_CURRENT):
                    batch = db.get(
                        PatrolBatchRecord,
                        str(manifest.patrol_batch_id),
                    )
                    qualification = (
                        db.get(
                            PatrolSourceQualificationRecord,
                            batch.source_qualification_id,
                        )
                        if batch is not None and batch.source_qualification_id
                        else None
                    )
                    qualification_payload = (
                        dict(qualification.qualification_json or {})
                        if qualification is not None
                        else {}
                    )
                    expected_qualification_digest = str(
                        authority_context.get("source_qualification_digest") or ""
                    )
                    if (
                        batch is None
                        or qualification is None
                        or str(qualification.status) != "approved"
                        or str(qualification.digest) != expected_qualification_digest
                        or str(batch.source_qualification_digest or "")
                        != expected_qualification_digest
                        or str(manifest.source_qualification_digest or "")
                        != expected_qualification_digest
                        or not source_qualification_is_valid(
                            qualification_payload,
                            expected_patrol_batch_id=str(manifest.patrol_batch_id),
                            expected_scope_contract_digest=str(
                                manifest.scope_contract_digest or ""
                            ),
                            expected_intended_target_matrix_digest=str(
                                manifest.intended_target_matrix_digest or ""
                            ),
                        )
                    ):
                        raise ValueError(
                            "offline current source qualification is no "
                            "longer valid at report release"
                        )
            if source_schema in {
                "patrol-report-source/v4",
                "patrol-report-source/v5",
            }:
                frozen_permission = dict(source_facts.get("conclusion_permission") or {})
                if not report_conclusion_permission_is_valid(frozen_permission):
                    raise ValueError("report conclusion permission digest is invalid")
                expected_permission = build_report_conclusion_permission_from_source_facts(
                    source_facts
                )
                if frozen_permission != expected_permission:
                    raise ValueError(
                        "report conclusion permission does not match the frozen source facts"
                    )
            claim_ledger_digest = ""
            if source_schema == "patrol-report-source/v5":
                validate_report_claim_ledger(source_facts)
                ledger = dict(source_facts.get("claim_ledger") or {})
                claim_ledger_digest = str(ledger.get("claim_ledger_digest") or "")
                if (
                    len(claim_ledger_digest) != 64
                    or str(manifest.claim_ledger_digest or "") != claim_ledger_digest
                ):
                    raise ValueError("report claim ledger digest does not match manifest")
                persisted_claims = (
                    db.query(ReportClaimRecord)
                    .filter(ReportClaimRecord.report_manifest_id == manifest.id)
                    .order_by(ReportClaimRecord.claim_code)
                    .all()
                )
                frozen_claims = list(ledger.get("claims") or [])
                if (
                    len(persisted_claims) != len(frozen_claims)
                    or len(persisted_claims) != int(manifest.claim_count or 0)
                    or [str(item.claim_digest) for item in persisted_claims]
                    != [str(item.get("claim_digest") or "") for item in frozen_claims]
                ):
                    raise ValueError("persisted report claims do not match the frozen ledger")
            if str(source_facts.get("raw_evidence_set_digest") or "") != str(
                manifest.raw_evidence_set_digest or ""
            ):
                raise ValueError("raw evidence digest does not match the frozen source facts")
            attached = {
                str(name): str(digest).lower()
                for name, digest in dict(manifest.artifact_sha256_json or {}).items()
            }
            object_keys = dict(manifest.artifact_object_keys_json or {})
            if not attached or set(attached) != set(object_keys):
                raise ValueError("report artifacts must be attached before release")
            if verified != attached:
                raise ValueError("verified artifact SHA-256 values do not match the manifest")
            if any(
                len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
                for value in verified.values()
            ):
                raise ValueError("report release requires valid SHA-256 values")
            content_review: dict[str, Any] | None = None
            if existing is None or existing.content_review_id:
                content_review = ReportContentReviewService(
                    self._session_scope,
                    object_storage=self._object_storage,
                ).validate_for_release_in_session(db, str(manifest.id))
                if actor_matches_any(
                    actor,
                    [content_review.get("accepted_by")],
                ):
                    raise ValueError(
                        "report release requires a signer different from "
                        "the content acceptance reviewer"
                    )
                if existing is not None and (
                    str(existing.content_review_id or "") != str(content_review.get("id") or "")
                    or str(existing.content_acceptance_digest or "")
                    != str(content_review.get("acceptance_digest") or "")
                ):
                    raise ValueError("report release content acceptance no longer matches")
            if existing is not None:
                if (
                    dict(existing.artifact_sha256_json or {}) != verified
                    or str(existing.source_facts_digest) != str(manifest.source_digest)
                    or str(existing.claim_ledger_digest or "") != claim_ledger_digest
                ):
                    raise ValueError("report manifest already has a different immutable release")
                return existing.to_dict()
            occurred_at = signed_at or datetime.now(timezone.utc)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
            payload = {
                "report_manifest_id": str(manifest.id),
                "patrol_batch_id": str(manifest.patrol_batch_id),
                "report_purpose": str(manifest.report_purpose),
                "source_facts_digest": str(manifest.source_digest),
                "claim_ledger_digest": claim_ledger_digest,
                "raw_evidence_set_digest": str(manifest.raw_evidence_set_digest),
                "content_review_id": str((content_review or {}).get("id") or ""),
                "content_acceptance_digest": str(
                    (content_review or {}).get("acceptance_digest") or ""
                ),
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
                content_review_id=str(content_review["id"]),
                content_acceptance_digest=str(content_review["acceptance_digest"]),
                patrol_batch_id=manifest.patrol_batch_id,
                status="released",
                source_facts_digest=manifest.source_digest,
                claim_ledger_digest=(claim_ledger_digest or None),
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
                .filter(ReportReleaseRecord.report_manifest_id == report_manifest_id)
                .first()
            )
            return row.to_dict() if row is not None else None

    def list_for_batch(
        self,
        patrol_batch_id: str,
    ) -> list[dict[str, Any]]:
        with self._session_scope() as db:
            rows = (
                db.query(ReportReleaseRecord)
                .filter(ReportReleaseRecord.patrol_batch_id == patrol_batch_id)
                .order_by(
                    ReportReleaseRecord.signed_at.desc(),
                    ReportReleaseRecord.id.desc(),
                )
                .all()
            )
            return [row.to_dict() for row in rows]

    def latest_for_batch(
        self,
        patrol_batch_id: str,
    ) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = (
                db.query(ReportReleaseRecord)
                .filter(ReportReleaseRecord.patrol_batch_id == patrol_batch_id)
                .order_by(
                    ReportReleaseRecord.signed_at.desc(),
                    ReportReleaseRecord.id.desc(),
                )
                .first()
            )
            return row.to_dict() if row is not None else None
