"""Governed association of risk episodes for leadership-level issue counting."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_

from src.infrastructure.db.models import (
    RiskCaseRecord,
    RiskEpisodeAssociationRecord,
)
from src.infrastructure.db.postgres import get_db_session

_ACTIVE_RISK_STATUSES = {"open", "acknowledged", "recovering"}
_RELATION_TYPES = {"same_root_cause", "causal_symptom", "shared_incident"}
_SOURCE_TYPES = {"manual", "approved_policy"}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class RiskAssociationConflictError(ValueError):
    """The requested association conflicts with current risk governance."""


class RiskEpisodeAssociationService:
    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def list_associations(
        self,
        *,
        risk_case_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        with self._session_scope() as db:
            query = db.query(RiskEpisodeAssociationRecord)
            if risk_case_id:
                query = query.filter(
                    or_(
                        RiskEpisodeAssociationRecord.primary_risk_case_id == risk_case_id,
                        RiskEpisodeAssociationRecord.related_risk_case_id == risk_case_id,
                    )
                )
            if status:
                query = query.filter(RiskEpisodeAssociationRecord.status == status)
            total = int(query.count())
            rows = (
                query.order_by(
                    RiskEpisodeAssociationRecord.created_at.desc(),
                    RiskEpisodeAssociationRecord.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            case_ids = {
                str(case_id)
                for row in rows
                for case_id in (
                    row.primary_risk_case_id,
                    row.related_risk_case_id,
                )
            }
            cases = {
                str(row.id): row
                for row in (
                    db.query(RiskCaseRecord).filter(RiskCaseRecord.id.in_(case_ids)).all()
                    if case_ids
                    else []
                )
            }
            return {
                "items": [
                    self._serialize(
                        row,
                        primary=cases.get(str(row.primary_risk_case_id)),
                        related=cases.get(str(row.related_risk_case_id)),
                    )
                    for row in rows
                ],
                "page": page,
                "page_size": page_size,
                "total": total,
                "page_count": (total + page_size - 1) // page_size,
            }

    def propose(
        self,
        *,
        primary_risk_case_id: str,
        related_risk_case_id: str,
        relation_type: str,
        reason: str,
        evidence_refs: Iterable[str],
        proposed_by: str,
        idempotency_key: str,
        source_type: str = "manual",
        policy_ref: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        primary_id = str(primary_risk_case_id).strip()
        related_id = str(related_risk_case_id).strip()
        actor = str(proposed_by).strip()
        idempotency = str(idempotency_key).strip()
        relation = str(relation_type).strip()
        source = str(source_type).strip()
        rationale = str(reason).strip()
        refs = sorted({str(item).strip() for item in evidence_refs if str(item).strip()})
        policy = dict(policy_ref or {})
        if not primary_id or not related_id:
            raise ValueError("both primary and related risk cases are required")
        if primary_id == related_id:
            raise RiskAssociationConflictError("a risk episode cannot be associated to itself")
        if relation not in _RELATION_TYPES:
            raise ValueError("unsupported risk episode relation type")
        if source not in _SOURCE_TYPES:
            raise ValueError("unsupported risk association source type")
        if len(rationale) < 3:
            raise ValueError("association reason must contain at least 3 characters")
        if not actor:
            raise ValueError("association proposer is required")
        if len(idempotency) < 8:
            raise ValueError("idempotency key must contain at least 8 characters")
        if source == "approved_policy":
            required = {"policy_id", "policy_version", "policy_digest", "approval_digest"}
            if not required.issubset({key for key, value in policy.items() if value}):
                raise ValueError(
                    "approved policy association requires immutable policy approval refs"
                )

        request = {
            "primary_risk_case_id": primary_id,
            "related_risk_case_id": related_id,
            "relation_type": relation,
            "source_type": source,
            "reason": rationale,
            "evidence_refs": refs,
            "policy_ref": policy,
            "proposed_by": actor,
        }
        request_digest = _digest(request)
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            existing = (
                db.query(RiskEpisodeAssociationRecord)
                .filter(RiskEpisodeAssociationRecord.idempotency_key == idempotency)
                .with_for_update()
                .first()
            )
            if existing is not None:
                if str(existing.request_digest) != request_digest:
                    raise RiskAssociationConflictError(
                        "idempotency key was already used for a different association request"
                    )
                primary = db.get(RiskCaseRecord, existing.primary_risk_case_id)
                related = db.get(RiskCaseRecord, existing.related_risk_case_id)
                return self._serialize(existing, primary=primary, related=related)

            primary, related = self._lock_cases(
                db,
                primary_id=primary_id,
                related_id=related_id,
            )
            self._require_active_episode(primary, role="primary")
            self._require_active_episode(related, role="related")
            live_pair = (
                db.query(RiskEpisodeAssociationRecord)
                .filter(
                    RiskEpisodeAssociationRecord.primary_risk_episode_id == primary.episode_id,
                    RiskEpisodeAssociationRecord.related_risk_episode_id == related.episode_id,
                    RiskEpisodeAssociationRecord.status.in_(["pending_review", "approved"]),
                )
                .with_for_update()
                .first()
            )
            if live_pair is not None:
                raise RiskAssociationConflictError(
                    "the current risk episodes already have a live association"
                )
            record = RiskEpisodeAssociationRecord(
                id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/risk-episode-association/{idempotency}",
                    )
                ),
                idempotency_key=idempotency,
                request_digest=request_digest,
                primary_risk_case_id=primary.id,
                primary_risk_episode_id=primary.episode_id,
                related_risk_case_id=related.id,
                related_risk_episode_id=related.episode_id,
                relation_type=relation,
                status="pending_review",
                source_type=source,
                policy_ref_json=policy or None,
                evidence_refs_json=refs,
                reason=rationale,
                proposed_by=actor,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.flush()
            return self._serialize(record, primary=primary, related=related)

    def review(
        self,
        association_id: str,
        *,
        decision: str,
        reviewed_by: str,
        reason: str,
    ) -> dict[str, Any]:
        normalized_decision = str(decision).strip().lower()
        actor = str(reviewed_by).strip()
        rationale = str(reason).strip()
        if normalized_decision not in {"approved", "rejected"}:
            raise ValueError("association review decision must be approved or rejected")
        if not actor:
            raise ValueError("association reviewer is required")
        if len(rationale) < 3:
            raise ValueError("review reason must contain at least 3 characters")
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            association = (
                db.query(RiskEpisodeAssociationRecord)
                .filter(RiskEpisodeAssociationRecord.id == association_id)
                .with_for_update()
                .first()
            )
            if association is None:
                raise ValueError("risk episode association not found")
            primary, related = self._lock_cases(
                db,
                primary_id=str(association.primary_risk_case_id),
                related_id=str(association.related_risk_case_id),
            )
            if association.status == normalized_decision:
                return self._serialize(association, primary=primary, related=related)
            if association.status != "pending_review":
                raise RiskAssociationConflictError(
                    "only a pending risk episode association can be reviewed"
                )
            if actor == str(association.proposed_by):
                raise RiskAssociationConflictError(
                    "association proposer cannot approve or reject the same association"
                )
            if normalized_decision == "approved":
                self._require_current_episode(
                    primary,
                    association.primary_risk_episode_id,
                    role="primary",
                )
                self._require_current_episode(
                    related,
                    association.related_risk_episode_id,
                    role="related",
                )
                self._require_star_topology(db, association=association)
            association.status = normalized_decision
            association.reviewed_by = actor
            association.review_reason = rationale
            association.reviewed_at = now
            association.updated_at = now
            db.flush()
            return self._serialize(association, primary=primary, related=related)

    def revoke(
        self,
        association_id: str,
        *,
        revoked_by: str,
        reason: str,
    ) -> dict[str, Any]:
        actor = str(revoked_by).strip()
        rationale = str(reason).strip()
        if not actor:
            raise ValueError("association revoker is required")
        if len(rationale) < 3:
            raise ValueError("revoke reason must contain at least 3 characters")
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            association = (
                db.query(RiskEpisodeAssociationRecord)
                .filter(RiskEpisodeAssociationRecord.id == association_id)
                .with_for_update()
                .first()
            )
            if association is None:
                raise ValueError("risk episode association not found")
            primary = db.get(RiskCaseRecord, association.primary_risk_case_id)
            related = db.get(RiskCaseRecord, association.related_risk_case_id)
            if association.status == "revoked":
                return self._serialize(association, primary=primary, related=related)
            if association.status != "approved":
                raise RiskAssociationConflictError(
                    "only an approved risk episode association can be revoked"
                )
            association.status = "revoked"
            association.revoked_by = actor
            association.revoke_reason = rationale
            association.revoked_at = now
            association.updated_at = now
            db.flush()
            return self._serialize(association, primary=primary, related=related)

    @staticmethod
    def _lock_cases(
        db: Any,
        *,
        primary_id: str,
        related_id: str,
    ) -> tuple[RiskCaseRecord, RiskCaseRecord]:
        rows = (
            db.query(RiskCaseRecord)
            .filter(RiskCaseRecord.id.in_(sorted({primary_id, related_id})))
            .order_by(RiskCaseRecord.id)
            .with_for_update()
            .all()
        )
        by_id = {str(row.id): row for row in rows}
        primary = by_id.get(primary_id)
        related = by_id.get(related_id)
        if primary is None or related is None:
            raise ValueError("one or more risk cases do not exist")
        return primary, related

    @staticmethod
    def _require_active_episode(case: RiskCaseRecord, *, role: str) -> None:
        if str(case.status) not in _ACTIVE_RISK_STATUSES:
            raise RiskAssociationConflictError(f"{role} risk case does not have an active episode")
        if not str(case.episode_id or ""):
            raise RiskAssociationConflictError(f"{role} risk case episode identity is missing")

    @classmethod
    def _require_current_episode(
        cls,
        case: RiskCaseRecord,
        expected_episode_id: str,
        *,
        role: str,
    ) -> None:
        cls._require_active_episode(case, role=role)
        if str(case.episode_id) != str(expected_episode_id):
            raise RiskAssociationConflictError(
                f"{role} risk case has entered a new episode; create a new association"
            )

    @staticmethod
    def _require_star_topology(
        db: Any,
        *,
        association: RiskEpisodeAssociationRecord,
    ) -> None:
        primary_episode = str(association.primary_risk_episode_id)
        related_episode = str(association.related_risk_episode_id)
        conflicts = (
            db.query(RiskEpisodeAssociationRecord)
            .filter(
                RiskEpisodeAssociationRecord.id != association.id,
                RiskEpisodeAssociationRecord.status == "approved",
                or_(
                    RiskEpisodeAssociationRecord.related_risk_episode_id == primary_episode,
                    RiskEpisodeAssociationRecord.primary_risk_episode_id == related_episode,
                    RiskEpisodeAssociationRecord.related_risk_episode_id == related_episode,
                ),
            )
            .with_for_update()
            .all()
        )
        if conflicts:
            raise RiskAssociationConflictError(
                "association would create a cycle, chain, or multiple primary issues"
            )

    @staticmethod
    def _serialize(
        association: RiskEpisodeAssociationRecord,
        *,
        primary: RiskCaseRecord | None,
        related: RiskCaseRecord | None,
    ) -> dict[str, Any]:
        primary_current = bool(
            primary is not None
            and str(primary.episode_id) == str(association.primary_risk_episode_id)
        )
        related_current = bool(
            related is not None
            and str(related.episode_id) == str(association.related_risk_episode_id)
        )
        effective = bool(
            association.status == "approved"
            and primary_current
            and related_current
            and primary is not None
            and related is not None
            and str(primary.status) in _ACTIVE_RISK_STATUSES
            and str(related.status) in _ACTIVE_RISK_STATUSES
        )
        return {
            **association.to_dict(),
            "effective": effective,
            "stale": bool(association.status == "approved" and not effective),
            "primary_risk": RiskEpisodeAssociationService._case_summary(primary),
            "related_risk": RiskEpisodeAssociationService._case_summary(related),
        }

    @staticmethod
    def _case_summary(case: RiskCaseRecord | None) -> dict[str, Any] | None:
        if case is None:
            return None
        return {
            "id": str(case.id),
            "episode_id": str(case.episode_id),
            "check_item_id": str(case.check_item_id),
            "device_id": str(case.device_id),
            "entity_type": str(case.entity_type),
            "entity_key": str(case.entity_key),
            "risk_type": str(case.risk_type),
            "severity": str(case.severity),
            "status": str(case.status),
        }


__all__ = [
    "RiskAssociationConflictError",
    "RiskEpisodeAssociationService",
]
