"""Asset-gap work queue that cannot invoke model generation."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_

from src.infrastructure.db.models import AssetGapRecord
from src.infrastructure.db.postgres import get_db_session


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


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class AssetGapRepository:
    """Store governance work without dispatching a Parser or rule-generation job."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def record(
        self,
        *,
        check_item_id: str,
        gap_type: str,
        reason_code: str,
        purpose: str = "production_patrol",
        patrol_batch_id: str | None = None,
        check_execution_id: str | None = None,
        check_item_version_id: str | None = None,
        implementation_version_id: str | None = None,
        canonical_command: str | None = None,
        output_signature: str | None = None,
        sanitized_sample_refs: Iterable[str] = (),
        evidence_refs: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = observed_at or datetime.now(timezone.utc)
        identity = {
            "check_item_id": check_item_id,
            "check_item_version_id": check_item_version_id,
            "implementation_version_id": implementation_version_id,
            "canonical_command": canonical_command,
            "output_signature": output_signature,
            "gap_type": gap_type,
            "purpose": purpose,
            "reason_code": reason_code,
        }
        idempotency_key = _digest(identity)
        with self._session_scope() as db:
            row = (
                db.query(AssetGapRecord)
                .filter(AssetGapRecord.idempotency_key == idempotency_key)
                .with_for_update()
                .first()
            )
            samples = sorted(
                set(str(item) for item in sanitized_sample_refs if str(item))
            )
            evidence = sorted(set(str(item) for item in evidence_refs if str(item)))
            if row is None:
                row = AssetGapRecord(
                    id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"netops/asset-gap/{idempotency_key}",
                        )
                    ),
                    idempotency_key=idempotency_key,
                    patrol_batch_id=patrol_batch_id,
                    check_execution_id=check_execution_id,
                    check_item_id=check_item_id,
                    check_item_version_id=check_item_version_id,
                    implementation_version_id=implementation_version_id,
                    canonical_command=canonical_command,
                    output_signature=output_signature,
                    gap_type=gap_type,
                    purpose=purpose,
                    status="open",
                    reason_code=reason_code,
                    sanitized_sample_refs_json=samples,
                    evidence_refs_json=evidence,
                    occurrence_count=1,
                    first_seen_at=now,
                    last_seen_at=now,
                    metadata_json=dict(metadata or {}),
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.patrol_batch_id = patrol_batch_id or row.patrol_batch_id
                row.check_execution_id = check_execution_id or row.check_execution_id
                row.status = "open"
                row.resolved_at = None
                row.resolved_by = None
                row.resolution_note = None
                row.occurrence_count = int(row.occurrence_count or 0) + 1
                row.last_seen_at = max(_utc(row.last_seen_at), _utc(now))
                row.sanitized_sample_refs_json = sorted(
                    {
                        *(row.sanitized_sample_refs_json or []),
                        *samples,
                    }
                )
                row.evidence_refs_json = sorted(
                    {
                        *(row.evidence_refs_json or []),
                        *evidence,
                    }
                )
                row.metadata_json = {
                    **dict(row.metadata_json or {}),
                    **dict(metadata or {}),
                }
                row.updated_at = datetime.now(timezone.utc)
            db.flush()
            return row.to_dict()

    def resolve(
        self,
        gap_id: str,
        *,
        actor: str,
        note: str,
    ) -> dict[str, Any]:
        if not actor.strip() or not note.strip():
            raise ValueError("asset-gap resolution requires actor and note")
        with self._session_scope() as db:
            row = db.get(AssetGapRecord, gap_id)
            if row is None:
                raise ValueError("asset gap not found")
            row.status = "resolved"
            row.resolved_at = datetime.now(timezone.utc)
            row.resolved_by = actor
            row.resolution_note = note
            row.updated_at = row.resolved_at
            db.flush()
            return row.to_dict()

    def get(self, gap_id: str) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = db.get(AssetGapRecord, gap_id)
            return row.to_dict() if row is not None else None

    def list_page(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        check_item_id: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        page_limit = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with self._session_scope() as db:
            query = db.query(AssetGapRecord)
            if status:
                query = query.filter(AssetGapRecord.status == status)
            if check_item_id:
                query = query.filter(
                    AssetGapRecord.check_item_id == check_item_id
                )
            normalized_search = str(search or "").strip()
            if normalized_search:
                pattern = f"%{normalized_search}%"
                query = query.filter(
                    or_(
                        AssetGapRecord.check_item_id.ilike(pattern),
                        AssetGapRecord.canonical_command.ilike(pattern),
                        AssetGapRecord.reason_code.ilike(pattern),
                        AssetGapRecord.gap_type.ilike(pattern),
                    )
                )
            total = query.count()
            rows = (
                query.order_by(
                    AssetGapRecord.last_seen_at.desc(),
                    AssetGapRecord.id,
                )
                .offset(page_offset)
                .limit(page_limit)
                .all()
            )
            return {
                "items": [item.to_dict() for item in rows],
                "total": total,
                "limit": page_limit,
                "offset": page_offset,
            }
