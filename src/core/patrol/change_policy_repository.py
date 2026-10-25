"""PostgreSQL repository for versioned ChangePolicy assets."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_

from src.core.patrol.change_policy import (
    ChangePolicyProposal,
    policy_from_dict,
    validate_change_policy,
)
from src.infrastructure.db.models import (
    AuditLogRecord,
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
)
from src.infrastructure.db.postgres import get_db_session


def change_policy_digest(policy: dict[str, Any] | ChangePolicyProposal) -> str:
    payload = policy.to_dict() if isinstance(policy, ChangePolicyProposal) else dict(policy)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ChangePolicyRepository:
    """Persist immutable ChangePolicy versions and activate them explicitly."""

    def create_policy_version(
        self,
        *,
        name: str,
        policy: dict[str, Any] | ChangePolicyProposal,
        description: str | None = None,
        status: str = "draft",
        source_job_id: str | None = None,
        check_item_version_id: str | None = None,
        validation_report: dict[str, Any] | None = None,
        created_by: str | None = None,
        version: int | None = None,
        digest: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"draft", "pending_approval"}:
            raise ValueError("change policy version status must be draft or pending_approval")
        proposal = policy if isinstance(policy, ChangePolicyProposal) else policy_from_dict(policy)
        policy_json = proposal.to_dict()
        validation = validation_report or validate_change_policy(proposal)
        policy_digest = digest or change_policy_digest(policy_json)
        now = datetime.now(timezone.utc)

        with get_db_session() as db:
            record = (
                db.query(ChangePolicyRecord)
                .filter(ChangePolicyRecord.name == name)
                .first()
            )
            if record is None:
                record = ChangePolicyRecord(
                    id=str(uuid.uuid4()),
                    name=name,
                    observation_type=proposal.observation_type,
                    description=description,
                    status="draft",
                    current_version=0,
                    created_at=now,
                    updated_at=now,
                )
                db.add(record)
                db.flush()
            else:
                record.description = description or record.description
                record.observation_type = proposal.observation_type
                record.updated_at = now

            if version is None:
                latest = (
                    db.query(ChangePolicyVersionRecord)
                    .filter(ChangePolicyVersionRecord.policy_id == record.id)
                    .order_by(ChangePolicyVersionRecord.version.desc())
                    .first()
                )
                version = int(latest.version) + 1 if latest else 1

            existing = (
                db.query(ChangePolicyVersionRecord)
                .filter(ChangePolicyVersionRecord.policy_id == record.id)
                .filter(ChangePolicyVersionRecord.version == int(version))
                .first()
            )
            if existing is not None:
                if existing.digest == policy_digest:
                    return self._to_payload(record, existing)
                raise ValueError("change policy version already exists with different digest")

            version_row = ChangePolicyVersionRecord(
                id=str(uuid.uuid4()),
                policy_id=record.id,
                version=int(version),
                observation_type=proposal.observation_type,
                check_item_version_id=check_item_version_id,
                semantic_type=proposal.semantic_type,
                compare_mode=proposal.compare_mode,
                policy_json=policy_json,
                validation_report_json=validation,
                status=status,
                source_job_id=source_job_id,
                digest=policy_digest,
                created_by=created_by,
                created_at=now,
            )
            db.add(version_row)
            db.flush()
            return self._to_payload(record, version_row)

    def activate_policy_version(
        self,
        version_id: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            version = db.get(ChangePolicyVersionRecord, version_id)
            if version is None:
                raise ValueError("change policy version not found")
            policy = db.get(ChangePolicyRecord, version.policy_id)
            if policy is None:
                raise ValueError("change policy not found")
            (
                db.query(ChangePolicyVersionRecord)
                .filter(
                    ChangePolicyVersionRecord.observation_type
                    == version.observation_type
                )
                .filter(
                    ChangePolicyVersionRecord.check_item_version_id
                    == version.check_item_version_id
                    if version.check_item_version_id is not None
                    else ChangePolicyVersionRecord.check_item_version_id.is_(None)
                )
                .filter(ChangePolicyVersionRecord.status == "active")
                .filter(ChangePolicyVersionRecord.id != version.id)
                .update(
                    {"status": "deprecated", "deprecated_at": now},
                    synchronize_session=False,
                )
            )
            version.status = "active"
            version.activated_at = now
            version.deprecated_at = None
            policy.status = "active"
            policy.current_version = int(version.version)
            policy.updated_at = now
            if actor and reason:
                db.add(
                    AuditLogRecord(
                        id=str(uuid.uuid4()),
                        user_id=actor,
                        username=actor,
                        action="change_policy_builtin_seed_activate",
                        resource_type="change_policy_version",
                        resource_id=version.id,
                        detail={
                            "reason": reason,
                            "observation_type": version.observation_type,
                            "emergency_override": True,
                        },
                        status="success",
                        created_at=now,
                    )
                )
            db.flush()
            return self._to_payload(policy, version)

    def get_active_policy(
        self,
        observation_type: str,
        *,
        check_item_version_id: str | None = None,
    ) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = (
                db.query(ChangePolicyRecord, ChangePolicyVersionRecord)
                .join(
                    ChangePolicyVersionRecord,
                    ChangePolicyVersionRecord.policy_id == ChangePolicyRecord.id,
                )
                .filter(ChangePolicyVersionRecord.status == "active")
                .filter(ChangePolicyVersionRecord.observation_type == observation_type)
                .filter(
                    ChangePolicyVersionRecord.check_item_version_id
                    == check_item_version_id
                    if check_item_version_id is not None
                    else ChangePolicyVersionRecord.check_item_version_id.is_(None)
                )
                .order_by(ChangePolicyVersionRecord.activated_at.desc())
                .first()
            )
            if row is None:
                return None
            policy, version = row
            return self._to_payload(policy, version)

    def list_versions(
        self,
        *,
        status: str | None = None,
        observation_type: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(ChangePolicyRecord, ChangePolicyVersionRecord).join(
                ChangePolicyVersionRecord,
                ChangePolicyVersionRecord.policy_id == ChangePolicyRecord.id,
            )
            if status:
                query = query.filter(ChangePolicyVersionRecord.status == status)
            if observation_type:
                query = query.filter(
                    ChangePolicyVersionRecord.observation_type == observation_type
                )
            rows = (
                query.order_by(ChangePolicyVersionRecord.created_at.desc())
                .limit(max(1, min(int(limit), 1000)))
                .all()
            )
            return [self._to_payload(policy, version) for policy, version in rows]

    def list_versions_page(
        self,
        *,
        status: str | None = None,
        observation_type: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with get_db_session() as db:
            query = db.query(ChangePolicyRecord, ChangePolicyVersionRecord).join(
                ChangePolicyVersionRecord,
                ChangePolicyVersionRecord.policy_id == ChangePolicyRecord.id,
            )
            if status:
                query = query.filter(ChangePolicyVersionRecord.status == status)
            if observation_type:
                query = query.filter(
                    ChangePolicyVersionRecord.observation_type == observation_type
                )
            pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None
            if pattern:
                query = query.filter(
                    or_(
                        ChangePolicyRecord.id.ilike(pattern),
                        ChangePolicyRecord.name.ilike(pattern),
                        ChangePolicyRecord.description.ilike(pattern),
                        ChangePolicyVersionRecord.observation_type.ilike(pattern),
                        ChangePolicyVersionRecord.semantic_type.ilike(pattern),
                        ChangePolicyVersionRecord.compare_mode.ilike(pattern),
                    )
                )
            total = query.order_by(None).count()
            rows = (
                query.order_by(
                    ChangePolicyVersionRecord.created_at.desc(),
                    ChangePolicyVersionRecord.id.desc(),
                )
                .offset(page_offset)
                .limit(page_size)
                .all()
            )
            return {
                "items": [self._to_payload(policy, version) for policy, version in rows],
                "total": total,
                "limit": page_size,
                "offset": page_offset,
            }

    def list_active_policies(self) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(ChangePolicyRecord, ChangePolicyVersionRecord)
                .join(
                    ChangePolicyVersionRecord,
                    ChangePolicyVersionRecord.policy_id == ChangePolicyRecord.id,
                )
                .filter(ChangePolicyVersionRecord.status == "active")
                .order_by(
                    ChangePolicyVersionRecord.observation_type,
                    ChangePolicyVersionRecord.activated_at.desc(),
                    ChangePolicyVersionRecord.id,
                )
                .all()
            )
            seen: set[tuple[str | None, str]] = set()
            payloads: list[dict[str, Any]] = []
            for policy, version in rows:
                scope_key = (
                    version.check_item_version_id,
                    version.observation_type,
                )
                if scope_key in seen:
                    raise RuntimeError(
                        "multiple active ChangePolicies for observation_type "
                        f"{version.observation_type}"
                    )
                seen.add(scope_key)
                payloads.append(self._to_payload(policy, version))
            return payloads

    @staticmethod
    def _to_payload(
        policy: ChangePolicyRecord,
        version: ChangePolicyVersionRecord,
    ) -> dict[str, Any]:
        return {
            **policy.to_dict(),
            "policy_id": policy.id,
            "policy_name": policy.name,
            "version_id": version.id,
            "version": version.version,
            "observation_type": version.observation_type,
            "semantic_type": version.semantic_type,
            "compare_mode": version.compare_mode,
            "policy": version.policy_json,
            "validation_report": version.validation_report_json,
            "version_status": version.status,
            "source_job_id": version.source_job_id,
            "check_item_version_id": version.check_item_version_id,
            "digest": version.digest,
            "activated_at": version.activated_at,
            "deprecated_at": version.deprecated_at,
        }
