"""Deterministic, immutable baseline selection for check-item comparisons."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from src.infrastructure.db.models import ComparisonContextRecord
from src.infrastructure.db.postgres import get_db_session


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _canonical_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class BaselineCandidate:
    run_id: str
    observed_at: datetime
    snapshot_ids: tuple[str, ...] = ()
    canonical_schema_version: str | None = None
    entity_identity_policy_version: str | None = None
    successful: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ComparisonDecision:
    id: str
    digest: str
    mode: str
    comparison_status: str
    device_id: str
    check_item_version_id: str
    current_observed_at: datetime
    baseline_observed_at: datetime | None
    baseline_run_id: str | None
    baseline_snapshot_ids: tuple[str, ...]
    max_baseline_age_seconds: int | None
    canonical_schema_version: str | None
    entity_identity_policy_version: str | None
    selection_reason: str
    metadata: dict[str, Any]

    def to_record_values(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "digest": self.digest,
            "mode": self.mode,
            "device_id": self.device_id,
            "check_item_version_id": self.check_item_version_id,
            "current_observed_at": self.current_observed_at,
            "baseline_observed_at": self.baseline_observed_at,
            "baseline_run_id": self.baseline_run_id,
            "baseline_snapshot_ids_json": list(self.baseline_snapshot_ids),
            "max_baseline_age_seconds": self.max_baseline_age_seconds,
            "canonical_schema_version": self.canonical_schema_version,
            "entity_identity_policy_version": self.entity_identity_policy_version,
            "selection_reason": self.selection_reason,
            "metadata_json": {
                **self.metadata,
                "comparison_status": self.comparison_status,
            },
        }


class ComparisonContextService:
    """Select a baseline without inventing changes when no comparable fact exists."""

    @staticmethod
    def _decision(
        *,
        mode: str,
        comparison_status: str,
        device_id: str,
        check_item_version_id: str,
        current_observed_at: datetime,
        baseline: BaselineCandidate | None,
        max_baseline_age_seconds: int | None,
        canonical_schema_version: str | None,
        entity_identity_policy_version: str | None,
        selection_reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> ComparisonDecision:
        payload = {
            "mode": mode,
            "comparison_status": comparison_status,
            "device_id": device_id,
            "check_item_version_id": check_item_version_id,
            "current_observed_at": current_observed_at.isoformat(),
            "baseline_observed_at": (
                baseline.observed_at.isoformat() if baseline is not None else None
            ),
            "baseline_run_id": baseline.run_id if baseline is not None else None,
            "baseline_snapshot_ids": (
                sorted(set(baseline.snapshot_ids)) if baseline is not None else []
            ),
            "max_baseline_age_seconds": max_baseline_age_seconds,
            "canonical_schema_version": canonical_schema_version,
            "entity_identity_policy_version": entity_identity_policy_version,
            "selection_reason": selection_reason,
            "metadata": dict(metadata or {}),
        }
        digest = _canonical_digest(payload)
        return ComparisonDecision(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"netops/comparison/{digest}")),
            digest=digest,
            mode=mode,
            comparison_status=comparison_status,
            device_id=device_id,
            check_item_version_id=check_item_version_id,
            current_observed_at=current_observed_at,
            baseline_observed_at=baseline.observed_at if baseline else None,
            baseline_run_id=baseline.run_id if baseline else None,
            baseline_snapshot_ids=tuple(
                sorted(set(baseline.snapshot_ids)) if baseline else ()
            ),
            max_baseline_age_seconds=max_baseline_age_seconds,
            canonical_schema_version=canonical_schema_version,
            entity_identity_policy_version=entity_identity_policy_version,
            selection_reason=selection_reason,
            metadata=dict(metadata or {}),
        )

    def select_online(
        self,
        *,
        device_id: str,
        check_item_version_id: str,
        current_observed_at: datetime | str,
        candidates: Iterable[BaselineCandidate],
        max_baseline_age_seconds: int | None,
        canonical_schema_version: str | None,
        entity_identity_policy_version: str | None,
    ) -> ComparisonDecision:
        current = _utc(current_observed_at)
        rows = sorted(
            (
                BaselineCandidate(
                    run_id=item.run_id,
                    observed_at=_utc(item.observed_at),
                    snapshot_ids=tuple(item.snapshot_ids),
                    canonical_schema_version=item.canonical_schema_version,
                    entity_identity_policy_version=item.entity_identity_policy_version,
                    successful=item.successful,
                    metadata=dict(item.metadata),
                )
                for item in candidates
            ),
            key=lambda item: (item.observed_at, item.run_id),
            reverse=True,
        )
        prior = [item for item in rows if item.successful and item.observed_at < current]
        if not rows:
            status, reason = "first_observation", "first_observation"
        elif not prior:
            status, reason = "baseline_missing", "no_prior_successful_fact"
        else:
            schema_compatible = [
                item
                for item in prior
                if item.canonical_schema_version == canonical_schema_version
                and item.entity_identity_policy_version
                == entity_identity_policy_version
            ]
            if not schema_compatible:
                status, reason = "schema_incompatible", "no_compatible_schema_or_identity"
            else:
                baseline = schema_compatible[0]
                age_seconds = int((current - baseline.observed_at).total_seconds())
                if (
                    max_baseline_age_seconds is not None
                    and age_seconds > max_baseline_age_seconds
                ):
                    status, reason = "baseline_stale", "latest_compatible_baseline_expired"
                else:
                    return self._decision(
                        mode="online_previous_success",
                        comparison_status="comparable",
                        device_id=device_id,
                        check_item_version_id=check_item_version_id,
                        current_observed_at=current,
                        baseline=baseline,
                        max_baseline_age_seconds=max_baseline_age_seconds,
                        canonical_schema_version=canonical_schema_version,
                        entity_identity_policy_version=entity_identity_policy_version,
                        selection_reason="latest_compatible_success",
                        metadata={"baseline_age_seconds": age_seconds},
                    )
        return self._decision(
            mode="no_baseline",
            comparison_status=status,
            device_id=device_id,
            check_item_version_id=check_item_version_id,
            current_observed_at=current,
            baseline=None,
            max_baseline_age_seconds=max_baseline_age_seconds,
            canonical_schema_version=canonical_schema_version,
            entity_identity_policy_version=entity_identity_policy_version,
            selection_reason=reason,
        )

    def select_explicit(
        self,
        *,
        device_id: str,
        check_item_version_id: str,
        current_observed_at: datetime | str,
        baseline: BaselineCandidate,
        canonical_schema_version: str | None,
        entity_identity_policy_version: str | None,
    ) -> ComparisonDecision:
        current = _utc(current_observed_at)
        baseline = BaselineCandidate(
            run_id=baseline.run_id,
            observed_at=_utc(baseline.observed_at),
            snapshot_ids=tuple(baseline.snapshot_ids),
            canonical_schema_version=baseline.canonical_schema_version,
            entity_identity_policy_version=baseline.entity_identity_policy_version,
            successful=baseline.successful,
            metadata=dict(baseline.metadata),
        )
        if baseline.observed_at >= current:
            raise ValueError("explicit baseline must be older than current period")
        if not baseline.successful:
            status, reason = "baseline_missing", "explicit_baseline_not_successful"
            selected = None
        elif (
            baseline.canonical_schema_version != canonical_schema_version
            or baseline.entity_identity_policy_version
            != entity_identity_policy_version
        ):
            status, reason = "schema_incompatible", "explicit_baseline_incompatible"
            selected = None
        else:
            return self._decision(
                mode="explicit_periods",
                comparison_status="comparable",
                device_id=device_id,
                check_item_version_id=check_item_version_id,
                current_observed_at=current,
                baseline=baseline,
                max_baseline_age_seconds=None,
                canonical_schema_version=canonical_schema_version,
                entity_identity_policy_version=entity_identity_policy_version,
                selection_reason="explicit_periods",
                metadata=baseline.metadata,
            )
        return self._decision(
            mode="no_baseline",
            comparison_status=status,
            device_id=device_id,
            check_item_version_id=check_item_version_id,
            current_observed_at=current,
            baseline=selected,
            max_baseline_age_seconds=None,
            canonical_schema_version=canonical_schema_version,
            entity_identity_policy_version=entity_identity_policy_version,
            selection_reason=reason,
        )


class ComparisonContextRepository:
    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def save(self, decision: ComparisonDecision) -> dict[str, Any]:
        with self._session_scope() as db:
            existing = (
                db.query(ComparisonContextRecord)
                .filter(ComparisonContextRecord.digest == decision.digest)
                .first()
            )
            if existing is not None:
                return existing.to_dict()
            row = ComparisonContextRecord(
                **decision.to_record_values(),
                created_at=datetime.now(timezone.utc),
            )
            db.add(row)
            db.flush()
            return row.to_dict()
