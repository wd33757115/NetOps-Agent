"""Persistence and read model for immutable patrol scope decisions."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.core.check_items.scope import ScopeResolution
from src.infrastructure.db.models import (
    PatrolBatchRecord,
    PatrolIntendedTargetMatrixRecord,
    PatrolScopeContractRecord,
    PatrolScopeContractVersionRecord,
    PatrolScopeDecisionRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _stable_id(namespace: str, *parts: object) -> str:
    payload = "/".join(str(part) for part in parts)
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"netops/{namespace}/{payload}",
        )
    )


@dataclass(frozen=True, slots=True)
class PersistedPatrolScope:
    scope_contract_version_id: str
    intended_target_matrix_id: str


class PatrolScopeRepository:
    """Persist scope facts in the same transaction as an effective plan."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (
            get_db_session
        ),
    ) -> None:
        self._session_scope = session_scope

    def persist_in_session(
        self,
        db: Any,
        *,
        batch: PatrolBatchRecord,
        resolution: ScopeResolution,
        inventory_snapshot_digest: str,
        actor: str,
        created_at: datetime | None = None,
    ) -> PersistedPatrolScope:
        now = created_at or datetime.now(timezone.utc)
        contract = dict(resolution.contract)
        contract_id = str(contract["scope_contract_id"])
        version = int(contract["version"])
        version_id = str(contract["scope_contract_version_id"])
        origin = str(contract.get("origin") or "explicit")
        lifecycle_status = (
            "frozen" if origin == "legacy_profile_selection" else "active"
        )

        contract_row = (
            db.query(PatrolScopeContractRecord)
            .filter(PatrolScopeContractRecord.id == contract_id)
            .with_for_update()
            .first()
        )
        if contract_row is None:
            contract_row = PatrolScopeContractRecord(
                id=contract_id,
                name=str(contract.get("name") or contract_id),
                status=lifecycle_status,
                current_version=version,
                created_at=now,
                updated_at=now,
            )
            db.add(contract_row)
        else:
            contract_row.current_version = max(
                int(contract_row.current_version or 0),
                version,
            )
            contract_row.updated_at = now
        db.flush()

        version_row = (
            db.query(PatrolScopeContractVersionRecord)
            .filter(
                PatrolScopeContractVersionRecord.scope_contract_id
                == contract_id,
                PatrolScopeContractVersionRecord.version == version,
            )
            .with_for_update()
            .first()
        )
        if version_row is not None:
            if (
                str(version_row.id) != version_id
                or str(version_row.digest) != resolution.contract_digest
            ):
                raise ValueError(
                    "scope contract version already exists with different "
                    "immutable content"
                )
        else:
            conflicting_id = (
                db.query(PatrolScopeContractVersionRecord)
                .filter(PatrolScopeContractVersionRecord.id == version_id)
                .first()
            )
            if conflicting_id is not None:
                raise ValueError(
                    "scope contract version id already belongs to different "
                    "immutable content"
                )
            version_row = PatrolScopeContractVersionRecord(
                id=version_id,
                scope_contract_id=contract_id,
                version=version,
                report_purpose=contract.get("report_purpose"),
                inventory_selector_json=dict(
                    contract.get("inventory_selector") or {}
                ),
                device_role_rules_json=list(
                    contract.get("device_role_rules") or []
                ),
                explicit_required_by_device_json=dict(
                    contract.get("explicit_required_by_device") or {}
                ),
                allowed_exclusion_codes_json=list(
                    contract.get("allowed_exclusion_codes") or []
                ),
                claim_boundary=str(contract["claim_boundary"]),
                origin=origin,
                contract_json=contract,
                digest=resolution.contract_digest,
                status=lifecycle_status,
                created_by=actor,
                created_at=now,
            )
            db.add(version_row)
            db.flush()

        matrix_id = _stable_id(
            "intended-target-matrix",
            batch.patrol_batch_id,
            resolution.intended_target_matrix_digest,
        )
        matrix_row = (
            db.query(PatrolIntendedTargetMatrixRecord)
            .filter(
                PatrolIntendedTargetMatrixRecord.patrol_batch_id
                == batch.patrol_batch_id
            )
            .with_for_update()
            .first()
        )
        if matrix_row is not None:
            if (
                str(matrix_row.id) != matrix_id
                or str(matrix_row.digest)
                != resolution.intended_target_matrix_digest
                or str(matrix_row.scope_contract_version_id) != version_id
            ):
                raise ValueError(
                    "patrol batch already has a different intended target "
                    "matrix"
                )
        else:
            matrix_row = PatrolIntendedTargetMatrixRecord(
                id=matrix_id,
                patrol_batch_id=str(batch.patrol_batch_id),
                scope_contract_version_id=version_id,
                inventory_snapshot_digest=inventory_snapshot_digest,
                matrix_json=dict(resolution.intended_target_matrix),
                digest=resolution.intended_target_matrix_digest,
                created_at=now,
            )
            db.add(matrix_row)
            db.flush()

        existing_decisions = {
            (
                str(row.device_id),
                str(row.check_item_version_id),
            ): row
            for row in (
                db.query(PatrolScopeDecisionRecord)
                .filter(
                    PatrolScopeDecisionRecord.patrol_batch_id
                    == batch.patrol_batch_id
                )
                .with_for_update()
                .all()
            )
        }
        expected_keys: set[tuple[str, str]] = set()
        for decision in resolution.decisions:
            device_id = str(decision["device_id"])
            check_item_version_id = str(
                decision["check_item_version_id"]
            )
            key = (device_id, check_item_version_id)
            expected_keys.add(key)
            existing = existing_decisions.get(key)
            if existing is not None:
                if (
                    str(existing.digest) != str(decision["digest"])
                    or str(existing.intended_target_matrix_id) != matrix_id
                ):
                    raise ValueError(
                        "patrol scope decision already exists with different "
                        "immutable content"
                    )
                continue
            db.add(
                PatrolScopeDecisionRecord(
                    id=_stable_id(
                        "scope-decision",
                        batch.patrol_batch_id,
                        device_id,
                        check_item_version_id,
                        decision["digest"],
                    ),
                    patrol_batch_id=str(batch.patrol_batch_id),
                    intended_target_matrix_id=matrix_id,
                    device_id=device_id,
                    check_item_version_id=check_item_version_id,
                    check_item_id=str(decision["check_item_id"]),
                    decision=str(decision["decision"]),
                    requirement=str(decision["requirement"]),
                    reason_codes_json=list(
                        decision.get("reason_codes") or []
                    ),
                    evidence_json=dict(decision.get("evidence") or {}),
                    digest=str(decision["digest"]),
                    decided_by=actor,
                    created_at=now,
                )
            )
        unexpected = set(existing_decisions) - expected_keys
        if unexpected:
            raise ValueError(
                "patrol batch contains scope decisions outside the frozen "
                "decision set"
            )

        batch.scope_contract_version_id = version_id
        batch.scope_contract_digest = resolution.contract_digest
        batch.intended_target_matrix_digest = (
            resolution.intended_target_matrix_digest
        )
        batch.scope_decision_digest = resolution.scope_decision_digest
        batch.updated_at = now
        db.flush()
        return PersistedPatrolScope(
            scope_contract_version_id=version_id,
            intended_target_matrix_id=matrix_id,
        )

    def get_for_batch(self, patrol_batch_id: str) -> dict[str, Any] | None:
        with self._session_scope() as db:
            batch = (
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == patrol_batch_id)
                .first()
            )
            if batch is None or not batch.scope_contract_version_id:
                return None
            version = (
                db.query(PatrolScopeContractVersionRecord)
                .filter(
                    PatrolScopeContractVersionRecord.id
                    == batch.scope_contract_version_id
                )
                .first()
            )
            matrix = (
                db.query(PatrolIntendedTargetMatrixRecord)
                .filter(
                    PatrolIntendedTargetMatrixRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .first()
            )
            decisions = (
                db.query(PatrolScopeDecisionRecord)
                .filter(
                    PatrolScopeDecisionRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .order_by(
                    PatrolScopeDecisionRecord.device_id,
                    PatrolScopeDecisionRecord.check_item_version_id,
                )
                .all()
            )
            return {
                "schema_version": "patrol-scope-read-model/v1",
                "patrol_batch_id": patrol_batch_id,
                "scope_contract": (
                    dict(version.contract_json or {}) if version else None
                ),
                "scope_contract_version_id": str(
                    batch.scope_contract_version_id
                ),
                "scope_contract_digest": str(
                    batch.scope_contract_digest or ""
                ),
                "intended_target_matrix": (
                    dict(matrix.matrix_json or {}) if matrix else None
                ),
                "intended_target_matrix_digest": str(
                    batch.intended_target_matrix_digest or ""
                ),
                "scope_decision_digest": str(
                    batch.scope_decision_digest or ""
                ),
                "scope_reconciled": bool(
                    matrix
                    and decisions
                    and not any(
                        str(row.decision) == "unresolved"
                        for row in decisions
                    )
                ),
                "decisions": [row.to_dict() for row in decisions],
            }


__all__ = [
    "PatrolScopeRepository",
    "PersistedPatrolScope",
]
