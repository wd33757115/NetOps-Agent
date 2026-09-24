"""Deterministic, immutable baseline selection for check-item comparisons."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, inspect

from src.core.check_items.assessment_policy import (
    validate_assessment_basis,
    validate_assessment_policy_snapshot,
)
from src.core.check_items.source_qualification import (
    source_qualification_is_valid,
)
from src.infrastructure.db.models import (
    ChangePolicyVersionRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemRuntimeManifestRecord,
    CheckItemVersionRecord,
    CollectionImplementationVersionRecord,
    CommandSnapshot,
    ComparisonContextRecord,
    EntityIdentityPolicyVersionRecord,
    EventRuleVersionRecord,
    EvidenceQualificationRecord,
    ParserTemplate,
    PatrolBatchRecord,
    PatrolSourceQualificationRecord,
    RuntimeBundleAssetRecord,
    StateObservationRecord,
    TextComparisonProfileVersionRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    aware = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc)


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


def build_comparison_fact_contract(
    *,
    check_item_version_id: str,
    check_item_digest: str,
    result_mode: str,
    assessment_mode: str,
    implementation_version_id: str | None,
    canonical_catalog_version: int | str | None,
    canonical_catalog_digest: str | None,
    entity_identity_policy_version_id: str | None,
    dependency_digests: Mapping[str, Any] | None,
    comparison_profile_version_id: str | None,
) -> dict[str, Any]:
    """Freeze only semantics that affect whether two facts are comparable."""

    dependencies = dict(dependency_digests or {})
    mode = str(result_mode or "").strip()
    semantic_contract: dict[str, Any] = {
        "check_item_version_id": str(check_item_version_id),
        "check_item_digest": str(check_item_digest or ""),
        "result_mode": mode,
        "assessment_mode": str(assessment_mode or ""),
        "entity_identity_policy_version_id": str(
            entity_identity_policy_version_id or ""
        ),
        "entity_identity_policy_digest": str(
            dependencies.get("identity_policy") or ""
        ),
    }
    if mode == "structured":
        projection_dependency = dict(
            dependencies.get("command_adapter_projection") or {}
        )
        semantic_contract.update(
            {
                "canonical_catalog_version": canonical_catalog_version,
                "canonical_catalog_digest": str(
                    canonical_catalog_digest or ""
                ),
                **(
                    {"command_adapter_projection": projection_dependency}
                    if projection_dependency
                    else {
                        "observation_contract_digests": dict(
                            dependencies.get("observation_contracts") or {}
                        )
                    }
                ),
            }
        )
    elif mode in {"normalized_diff", "hash_only"}:
        semantic_contract.update(
            {
                "implementation_version_id": str(
                    implementation_version_id or ""
                ),
                "comparison_profile_version_id": str(
                    comparison_profile_version_id or ""
                ),
                "comparison_profile_digests": dict(
                    dependencies.get("comparison_profile") or {}
                ),
            }
        )
    payload = {
        "schema_version": "comparison-fact-contract/v1",
        "semantic_contract": semantic_contract,
    }
    return {**payload, "digest": _canonical_digest(payload)}


def _fact_contract_is_valid(value: Mapping[str, Any] | None) -> bool:
    payload = dict(value or {})
    supplied = str(payload.pop("digest", "") or "")
    return bool(
        supplied
        and payload.get("schema_version") == "comparison-fact-contract/v1"
        and _canonical_digest(payload) == supplied
    )


def _comparison_context_is_valid(row: ComparisonContextRecord | None) -> bool:
    if row is None:
        return False
    metadata = dict(row.metadata_json or {})
    comparison_status = str(metadata.pop("comparison_status", "") or "")
    payload = {
        "mode": str(row.mode),
        "comparison_status": comparison_status,
        "device_id": str(row.device_id),
        "check_item_version_id": str(row.check_item_version_id),
        "current_observed_at": (
            _utc(row.current_observed_at).isoformat()
            if row.current_observed_at
            else None
        ),
        "baseline_observed_at": (
            _utc(row.baseline_observed_at).isoformat()
            if row.baseline_observed_at
            else None
        ),
        "baseline_run_id": row.baseline_run_id,
        "baseline_snapshot_ids": sorted(
            set(row.baseline_snapshot_ids_json or [])
        ),
        "max_baseline_age_seconds": row.max_baseline_age_seconds,
        "canonical_schema_version": row.canonical_schema_version,
        "entity_identity_policy_version": (
            row.entity_identity_policy_version
        ),
        "selection_reason": str(row.selection_reason),
        "metadata": metadata,
    }
    return bool(comparison_status and _canonical_digest(payload) == str(row.digest))


def _runtime_manifest_is_valid(
    row: CheckItemRuntimeManifestRecord | None,
    batch: PatrolBatchRecord | None,
) -> bool:
    if row is None or batch is None or str(row.status) != "frozen":
        return False
    payload = dict(row.manifest_json or {})
    supplied = str(payload.pop("digest", "") or "")
    return bool(
        str(row.patrol_batch_id) == str(batch.patrol_batch_id)
        and str(batch.runtime_manifest_id or "") == str(row.id)
        and str(batch.runtime_manifest_digest or "") == str(row.digest)
        and supplied == str(row.digest)
        and _canonical_digest(payload) == str(row.digest)
        and str(row.target_manifest_digest)
        == str(batch.target_manifest_digest or "")
        and str(row.inventory_snapshot_digest)
        == str(batch.inventory_snapshot_digest or "")
    )


def _runtime_bundle_is_valid(
    row: CheckItemRuntimeBundleVersionRecord | None,
    assets: Iterable[RuntimeBundleAssetRecord],
    actual_asset_digests: Mapping[tuple[str, str], str],
) -> bool:
    if row is None:
        return False
    event_rule_bindings: dict[str, set[str]] = {}
    for asset in assets:
        if str(actual_asset_digests.get(
            (str(asset.asset_type), str(asset.asset_version_id)),
            "",
        )) != str(asset.asset_digest or ""):
            return False
        if (
            str(asset.asset_type) not in {"current_state_rule", "event_rule"}
            or not asset.observation_type
        ):
            continue
        event_rule_bindings.setdefault(
            str(asset.asset_version_id), set()
        ).add(str(asset.observation_type))
    payload = {
        "bundle_id": row.bundle_id,
        "check_item_version_id": row.check_item_version_id,
        "implementation_version_id": row.implementation_version_id,
        "parser_bundle": row.parser_bundle_json,
        "canonical_catalog_version": row.canonical_catalog_version,
        "canonical_catalog_digest": row.canonical_catalog_digest,
        "entity_identity_policy_version_id": (
            row.entity_identity_policy_version_id
        ),
        "comparison_profile_version_id": row.comparison_profile_version_id,
        "dependency_digests": row.dependency_digests_json,
        "event_rule_bindings": {
            version_id: sorted(observation_types)
            for version_id, observation_types in sorted(
                event_rule_bindings.items()
            )
        },
        "compatibility": row.compatibility_json,
    }
    return _canonical_digest(payload) == str(row.digest)


def _evidence_qualification_is_valid(
    row: EvidenceQualificationRecord | None,
) -> bool:
    if row is None:
        return False
    policy = dict(row.policy_snapshot_json or {})
    if _canonical_digest(policy) != str(row.policy_digest):
        return False
    payload = {
        "check_execution_id": row.check_execution_id,
        "patrol_batch_id": row.patrol_batch_id,
        "device_id": row.device_id,
        "check_item_version_id": row.check_item_version_id,
        "implementation_version_id": row.implementation_version_id,
        "runtime_bundle_version_id": row.runtime_bundle_version_id,
        "runtime_bundle_digest": row.runtime_bundle_digest,
        "source_identity_status": row.source_identity_status,
        "output_quality_status": row.output_quality_status,
        "freshness_status": row.freshness_status,
        "empty_result_classification": row.empty_result_classification,
        "snapshot_ids": sorted(set(row.snapshot_ids_json or [])),
        "observation_min_at": (
            _utc(row.observation_min_at).isoformat()
            if row.observation_min_at
            else None
        ),
        "observation_max_at": (
            _utc(row.observation_max_at).isoformat()
            if row.observation_max_at
            else None
        ),
        "qualification_reason_codes": list(
            row.qualification_reason_codes_json or []
        ),
        "policy_digest": row.policy_digest,
        "profile_version_id": row.profile_version_id,
        "profile_digest": row.profile_digest,
        "evidence_refs": sorted(set(row.evidence_refs_json or [])),
        "qualified_at": _utc(row.qualified_at).isoformat(),
    }
    return _canonical_digest(payload) == str(row.digest)


@dataclass(frozen=True, slots=True)
class BaselineCandidate:
    run_id: str
    observed_at: datetime
    snapshot_ids: tuple[str, ...] = ()
    canonical_schema_version: str | None = None
    entity_identity_policy_version: str | None = None
    successful: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class ProductionBaselineCandidateRepository:
    """Load prior target facts in bounded, bulk queries and qualify them fail closed."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
        *,
        max_candidates_per_target: int = 16,
    ) -> None:
        self._session_scope = session_scope
        self._max_candidates_per_target = max(
            1, min(int(max_candidates_per_target), 64)
        )

    def load_many(
        self,
        *,
        targets: Iterable[tuple[str, str]],
        current_observed_at: datetime | str,
        current_patrol_batch_id: str | None = None,
    ) -> dict[tuple[str, str], tuple[BaselineCandidate, ...]]:
        requested = {
            (str(device_id), str(check_item_version_id))
            for device_id, check_item_version_id in targets
            if str(device_id) and str(check_item_version_id)
        }
        if not requested:
            return {}
        current = _utc(current_observed_at)
        device_ids = sorted({item[0] for item in requested})
        check_item_version_ids = sorted({item[1] for item in requested})
        with self._session_scope() as db:
            fact_time = func.coalesce(
                CheckExecutionRecord.observation_max_at,
                CheckExecutionRecord.completed_at,
                CheckExecutionRecord.updated_at,
            )
            purpose_priority = case(
                (
                    PatrolBatchRecord.report_purpose
                    == "production_current",
                    1,
                ),
                else_=0,
            )
            ranked_query = db.query(
                CheckExecutionRecord.id.label("execution_id"),
                func.row_number()
                .over(
                    partition_by=(
                        CheckExecutionRecord.device_id,
                        CheckExecutionRecord.check_item_version_id,
                    ),
                    order_by=(
                        purpose_priority.desc(),
                        fact_time.desc(),
                        CheckExecutionRecord.id.desc(),
                    ),
                )
                .label("candidate_rank"),
            ).outerjoin(
                PatrolBatchRecord,
                PatrolBatchRecord.patrol_batch_id
                == CheckExecutionRecord.patrol_batch_id,
            ).filter(
                CheckExecutionRecord.device_id.in_(device_ids),
                CheckExecutionRecord.check_item_version_id.in_(
                    check_item_version_ids
                ),
                fact_time < current,
            )
            if current_patrol_batch_id:
                ranked_query = ranked_query.filter(
                    CheckExecutionRecord.patrol_batch_id
                    != str(current_patrol_batch_id)
                )
            ranked = ranked_query.subquery()
            rows = (
                db.query(
                    CheckExecutionRecord,
                    PatrolBatchRecord,
                    CheckAssessmentRecord,
                    EvidenceQualificationRecord,
                    ComparisonContextRecord,
                    CheckItemRuntimeManifestRecord,
                    CheckItemRuntimeBundleVersionRecord,
                    CheckItemVersionRecord,
                )
                .join(
                    ranked,
                    ranked.c.execution_id == CheckExecutionRecord.id,
                )
                .outerjoin(
                    PatrolBatchRecord,
                    PatrolBatchRecord.patrol_batch_id
                    == CheckExecutionRecord.patrol_batch_id,
                )
                .outerjoin(
                    CheckAssessmentRecord,
                    CheckAssessmentRecord.execution_id
                    == CheckExecutionRecord.id,
                )
                .outerjoin(
                    EvidenceQualificationRecord,
                    EvidenceQualificationRecord.check_execution_id
                    == CheckExecutionRecord.id,
                )
                .outerjoin(
                    ComparisonContextRecord,
                    ComparisonContextRecord.id
                    == CheckExecutionRecord.comparison_context_id,
                )
                .outerjoin(
                    CheckItemRuntimeManifestRecord,
                    CheckItemRuntimeManifestRecord.patrol_batch_id
                    == CheckExecutionRecord.patrol_batch_id,
                )
                .outerjoin(
                    CheckItemRuntimeBundleVersionRecord,
                    CheckItemRuntimeBundleVersionRecord.id
                    == CheckExecutionRecord.runtime_bundle_version_id,
                )
                .outerjoin(
                    CheckItemVersionRecord,
                    CheckItemVersionRecord.id
                    == CheckExecutionRecord.check_item_version_id,
                )
                .filter(
                    ranked.c.candidate_rank
                    <= self._max_candidates_per_target
                )
                .order_by(
                    CheckExecutionRecord.device_id,
                    CheckExecutionRecord.check_item_version_id,
                    fact_time.desc(),
                    CheckExecutionRecord.id.desc(),
                )
                .all()
            )
            rows = [
                item
                for item in rows
                if (
                    str(item[0].device_id),
                    str(item[0].check_item_version_id),
                )
                in requested
            ]
            snapshot_ids = {
                str(snapshot_id)
                for item in rows
                for snapshot_id in (
                    item[3].snapshot_ids_json if item[3] is not None else []
                )
                if str(snapshot_id)
            }
            snapshots = self._load_snapshots(db, snapshot_ids)
            state_observations = self._load_state_observations(
                db, snapshot_ids
            )
            bundle_version_ids = {
                str(item[6].id) for item in rows if item[6] is not None
            }
            bundle_assets: dict[str, list[RuntimeBundleAssetRecord]] = (
                defaultdict(list)
            )
            for asset in self._load_bundle_assets(db, bundle_version_ids):
                bundle_assets[str(asset.bundle_version_id)].append(asset)
            actual_asset_digests = self._load_actual_asset_digests(
                db,
                (
                    asset
                    for values in bundle_assets.values()
                    for asset in values
                ),
            )
            source_qualification_ids = {
                str(item[1].source_qualification_id)
                for item in rows
                if item[1] is not None
                and item[1].source_qualification_id
            }
            source_qualifications = self._load_source_qualifications(
                db, source_qualification_ids
            )

            result: dict[tuple[str, str], list[BaselineCandidate]] = (
                defaultdict(list)
            )
            for item in rows:
                candidate = self._candidate(
                    execution=item[0],
                    batch=item[1],
                    assessment=item[2],
                    evidence=item[3],
                    comparison_context=item[4],
                    runtime_manifest=item[5],
                    runtime_bundle=item[6],
                    check_item_version=item[7],
                    snapshots=snapshots,
                    state_observations=state_observations,
                    bundle_assets=bundle_assets,
                    actual_asset_digests=actual_asset_digests,
                    source_qualifications=source_qualifications,
                )
                key = (
                    str(item[0].device_id),
                    str(item[0].check_item_version_id),
                )
                result[key].append(candidate)
            return {
                key: tuple(
                    sorted(
                        values,
                        key=lambda candidate: (
                            candidate.observed_at,
                            candidate.run_id,
                        ),
                        reverse=True,
                    )
                )
                for key, values in result.items()
            }

    @staticmethod
    def _chunks(values: Iterable[str], size: int = 400) -> Iterable[list[str]]:
        rows = sorted(set(str(item) for item in values if str(item)))
        for index in range(0, len(rows), size):
            yield rows[index : index + size]

    @classmethod
    def _load_snapshots(
        cls,
        db: Any,
        snapshot_ids: Iterable[str],
    ) -> dict[str, CommandSnapshot]:
        result: dict[str, CommandSnapshot] = {}
        for chunk in cls._chunks(snapshot_ids):
            for row in db.query(CommandSnapshot).filter(
                CommandSnapshot.id.in_(chunk)
            ):
                result[str(row.id)] = row
        return result

    @classmethod
    def _load_state_observations(
        cls,
        db: Any,
        snapshot_ids: Iterable[str],
    ) -> dict[str, list[StateObservationRecord]]:
        result: dict[str, list[StateObservationRecord]] = defaultdict(list)
        for chunk in cls._chunks(snapshot_ids):
            for row in db.query(StateObservationRecord).filter(
                StateObservationRecord.snapshot_id.in_(chunk)
            ):
                result[str(row.snapshot_id)].append(row)
        return result

    @classmethod
    def _load_bundle_assets(
        cls,
        db: Any,
        bundle_version_ids: Iterable[str],
    ) -> list[RuntimeBundleAssetRecord]:
        result: list[RuntimeBundleAssetRecord] = []
        for chunk in cls._chunks(bundle_version_ids):
            result.extend(
                db.query(RuntimeBundleAssetRecord)
                .filter(RuntimeBundleAssetRecord.bundle_version_id.in_(chunk))
                .all()
            )
        return result

    @classmethod
    def _load_actual_asset_digests(
        cls,
        db: Any,
        assets: Iterable[RuntimeBundleAssetRecord],
    ) -> dict[tuple[str, str], str]:
        requested: dict[str, set[str]] = defaultdict(set)
        for asset in assets:
            requested[str(asset.asset_type)].add(str(asset.asset_version_id))
        model_by_type = {
            "parser_template": (ParserTemplate, "sha256"),
            "command_adapter_projection": (
                CollectionImplementationVersionRecord,
                "digest",
            ),
            "change_policy": (ChangePolicyVersionRecord, "digest"),
            "current_state_rule": (EventRuleVersionRecord, "digest"),
            "event_rule": (EventRuleVersionRecord, "digest"),
            "identity_policy": (
                EntityIdentityPolicyVersionRecord,
                "digest",
            ),
            "comparison_profile": (
                TextComparisonProfileVersionRecord,
                "digest",
            ),
        }
        result: dict[tuple[str, str], str] = {}
        for asset_type, ids in requested.items():
            model_and_field = model_by_type.get(asset_type)
            if model_and_field is None:
                continue
            model, digest_field = model_and_field
            for chunk in cls._chunks(ids):
                rows = db.query(model).filter(model.id.in_(chunk)).all()
                for row in rows:
                    result[(asset_type, str(row.id))] = str(
                        getattr(row, digest_field) or ""
                    )
        return result

    @classmethod
    def _load_source_qualifications(
        cls,
        db: Any,
        qualification_ids: Iterable[str],
    ) -> dict[str, PatrolSourceQualificationRecord]:
        values = sorted(set(str(item) for item in qualification_ids if str(item)))
        if not values or not inspect(db.get_bind()).has_table(
            PatrolSourceQualificationRecord.__tablename__
        ):
            return {}
        result: dict[str, PatrolSourceQualificationRecord] = {}
        for chunk in cls._chunks(values):
            for row in db.query(PatrolSourceQualificationRecord).filter(
                PatrolSourceQualificationRecord.id.in_(chunk)
            ):
                result[str(row.id)] = row
        return result

    @staticmethod
    def _candidate(
        *,
        execution: CheckExecutionRecord,
        batch: PatrolBatchRecord | None,
        assessment: CheckAssessmentRecord | None,
        evidence: EvidenceQualificationRecord | None,
        comparison_context: ComparisonContextRecord | None,
        runtime_manifest: CheckItemRuntimeManifestRecord | None,
        runtime_bundle: CheckItemRuntimeBundleVersionRecord | None,
        check_item_version: CheckItemVersionRecord | None,
        snapshots: Mapping[str, CommandSnapshot],
        state_observations: Mapping[str, list[StateObservationRecord]],
        bundle_assets: Mapping[str, list[RuntimeBundleAssetRecord]],
        actual_asset_digests: Mapping[tuple[str, str], str],
        source_qualifications: Mapping[str, PatrolSourceQualificationRecord],
    ) -> BaselineCandidate:
        rejection_codes: list[str] = []

        def reject(code: str, condition: bool) -> None:
            if condition:
                rejection_codes.append(code)

        reject("patrol_batch_missing", batch is None)
        reject(
            "acquisition_purpose_ineligible",
            batch is not None
            and str(batch.report_purpose or "") != "production_current",
        )
        reject(
            "execution_not_completed",
            str(execution.execution_status or "") != "completed",
        )
        execution_policy_snapshot = dict(
            execution.assessment_policy_snapshot_json or {}
        )
        reject(
            "assessment_policy_snapshot_invalid",
            not validate_assessment_policy_snapshot(
                execution_policy_snapshot
            )
            or str(execution.assessment_policy_snapshot_digest or "")
            != str(execution_policy_snapshot.get("digest") or ""),
        )
        reject("assessment_missing", assessment is None)
        if assessment is not None:
            reject(
                "assessment_not_baseline_eligible",
                str(assessment.assessment_status or "")
                not in {"healthy", "at_risk", "informational"},
            )
            reject(
                "assessment_incomplete",
                str(assessment.completeness_status or "") != "complete",
            )
            reject(
                "assessment_scope_mismatch",
                str(assessment.patrol_batch_id)
                != str(execution.patrol_batch_id)
                or str(assessment.device_id) != str(execution.device_id)
                or str(assessment.check_item_version_id)
                != str(execution.check_item_version_id),
            )
            reject(
                "assessment_reference_mismatch",
                str(execution.assessment_id or "") != str(assessment.id),
            )
            basis = dict(assessment.assessment_basis_json or {})
            reject(
                "assessment_basis_invalid",
                not validate_assessment_basis(basis)
                or str(assessment.assessment_basis_digest or "")
                != str(basis.get("digest") or "")
                or str(basis.get("runtime_bundle_version_id") or "")
                != str(execution.runtime_bundle_version_id or "")
                or str(basis.get("runtime_bundle_digest") or "")
                != str(execution.runtime_bundle_digest or "")
                or str(basis.get("policy_snapshot_digest") or "")
                != str(execution.assessment_policy_snapshot_digest or ""),
            )
            reject(
                "assessment_policy_snapshot_mismatch",
                str(assessment.assessment_policy_snapshot_digest or "")
                != str(execution.assessment_policy_snapshot_digest or ""),
            )

        reject("evidence_qualification_missing", evidence is None)
        if evidence is not None:
            reject(
                "evidence_qualification_integrity_failed",
                not _evidence_qualification_is_valid(evidence),
            )
            reject(
                "source_identity_unconfirmed",
                str(evidence.source_identity_status or "") != "confirmed",
            )
            output_eligible = str(evidence.output_quality_status or "") == (
                "complete"
            ) or (
                str(evidence.output_quality_status or "") == "empty"
                and str(evidence.empty_result_classification or "")
                == "expected_empty"
            )
            reject("output_not_baseline_eligible", not output_eligible)
            reject(
                "evidence_not_fresh",
                str(evidence.freshness_status or "") != "fresh",
            )
            reject(
                "evidence_scope_mismatch",
                str(evidence.patrol_batch_id)
                != str(execution.patrol_batch_id)
                or str(evidence.device_id) != str(execution.device_id)
                or str(evidence.check_item_version_id)
                != str(execution.check_item_version_id),
            )
            reject(
                "evidence_reference_mismatch",
                str(execution.evidence_qualification_id or "")
                != str(evidence.id),
            )

        reject(
            "comparison_context_integrity_failed",
            not _comparison_context_is_valid(comparison_context),
        )
        if comparison_context is not None:
            reject(
                "comparison_context_scope_mismatch",
                str(comparison_context.device_id) != str(execution.device_id)
                or str(comparison_context.check_item_version_id)
                != str(execution.check_item_version_id),
            )
            if assessment is not None:
                reject(
                    "assessment_comparison_context_mismatch",
                    str(assessment.comparison_context_digest or "")
                    != str(comparison_context.digest or ""),
                )

        reject(
            "runtime_manifest_integrity_failed",
            not _runtime_manifest_is_valid(runtime_manifest, batch),
        )
        if runtime_manifest is not None:
            manifest = dict(runtime_manifest.manifest_json or {})
            manifest_contexts = {
                str(item.get("id")): str(item.get("digest") or "")
                for item in manifest.get("comparison_contexts") or []
                if isinstance(item, Mapping) and item.get("id")
            }
            reject(
                "runtime_manifest_context_mismatch",
                comparison_context is None
                or manifest_contexts.get(str(comparison_context.id))
                != str(comparison_context.digest),
            )
            reject(
                "runtime_manifest_check_item_missing",
                str(execution.check_item_version_id)
                not in {
                    str(item)
                    for item in runtime_manifest.check_item_versions_json or []
                },
            )
            matching_bundle_refs = [
                item
                for item in manifest.get("runtime_bundle_refs") or []
                if isinstance(item, Mapping)
                and str(item.get("device_id") or "")
                == str(execution.device_id)
                and str(item.get("check_item_version_id") or "")
                == str(execution.check_item_version_id)
                and str(item.get("implementation_version_id") or "")
                == str(execution.implementation_version_id or "")
                and str(
                    item.get("bundle_version_id")
                    or item.get("runtime_bundle_version_id")
                    or ""
                )
                == str(execution.runtime_bundle_version_id or "")
                and str(item.get("digest") or "")
                == str(execution.runtime_bundle_digest or "")
            ]
            reject(
                "runtime_manifest_bundle_reference_mismatch",
                len(matching_bundle_refs) != 1,
            )

        runtime_assets = bundle_assets.get(
            str(runtime_bundle.id) if runtime_bundle is not None else "",
            [],
        )
        reject(
            "runtime_bundle_integrity_failed",
            not _runtime_bundle_is_valid(
                runtime_bundle,
                runtime_assets,
                actual_asset_digests,
            ),
        )
        if runtime_bundle is not None:
            reject(
                "runtime_bundle_scope_mismatch",
                str(runtime_bundle.check_item_version_id)
                != str(execution.check_item_version_id)
                or str(runtime_bundle.implementation_version_id)
                != str(execution.implementation_version_id or ""),
            )
            reject(
                "runtime_bundle_reference_mismatch",
                str(execution.runtime_bundle_digest or "")
                != str(runtime_bundle.digest or "")
                or str(execution.asset_manifest_digest or "")
                != str(runtime_bundle.digest or ""),
            )
            reject(
                "runtime_bundle_root_reference_mismatch",
                str(execution.runtime_bundle_id or "")
                != str(runtime_bundle.bundle_id or ""),
            )

        reject("check_item_version_missing", check_item_version is None)
        if batch is not None and batch.source_offline_job_id:
            qualification = source_qualifications.get(
                str(batch.source_qualification_id or "")
            )
            qualification_payload = (
                dict(qualification.qualification_json or {})
                if qualification is not None
                else {}
            )
            reject(
                "source_qualification_invalid",
                qualification is None
                or str(batch.source_qualification_digest or "")
                != str(qualification.digest or "")
                or not source_qualification_is_valid(
                    qualification_payload,
                    expected_patrol_batch_id=str(batch.patrol_batch_id),
                    expected_scope_contract_digest=str(
                        batch.scope_contract_digest or ""
                    ),
                    expected_intended_target_matrix_digest=str(
                        batch.intended_target_matrix_digest or ""
                    ),
                ),
            )

        expected_snapshot_ids = tuple(
            sorted(
                set(
                    str(item)
                    for item in (
                        evidence.snapshot_ids_json if evidence is not None else []
                    )
                    if str(item)
                )
            )
        )
        selected_snapshots = [
            snapshots[item]
            for item in expected_snapshot_ids
            if item in snapshots
        ]
        reject("snapshot_evidence_missing", not expected_snapshot_ids)
        reject(
            "snapshot_evidence_incomplete",
            len(selected_snapshots) != len(expected_snapshot_ids),
        )
        reject(
            "snapshot_content_hash_mismatch",
            any(
                hashlib.sha256(
                    str(snapshot.raw_text or "").encode("utf-8")
                ).hexdigest()
                != str(snapshot.raw_text_hash or "")
                for snapshot in selected_snapshots
            ),
        )
        reject(
            "snapshot_identity_mismatch",
            any(
                str(snapshot.inventory_device_id or snapshot.device_id or "")
                != str(execution.device_id)
                for snapshot in selected_snapshots
            ),
        )
        reject(
            "snapshot_implementation_mismatch",
            any(
                snapshot.implementation_version_id
                and str(snapshot.implementation_version_id)
                != str(execution.implementation_version_id or "")
                for snapshot in selected_snapshots
            ),
        )
        run_ids = {
            str(snapshot.run_id)
            for snapshot in selected_snapshots
            if snapshot.run_id
        }
        reject("snapshot_run_not_unique", len(run_ids) != 1)

        selected_observations = [
            observation
            for snapshot_id in expected_snapshot_ids
            for observation in state_observations.get(snapshot_id, [])
        ]
        if (
            check_item_version is not None
            and str(check_item_version.result_mode) == "structured"
        ):
            expected_observation_count = int(
                execution.observation_count or 0
            )
            approved_empty = bool(
                evidence is not None
                and str(evidence.output_quality_status or "") == "empty"
                and str(evidence.empty_result_classification or "")
                == "expected_empty"
            )
            reject(
                "state_observation_count_mismatch",
                len(selected_observations) != expected_observation_count
                or (not approved_empty and not selected_observations),
            )
            reject(
                "state_observation_scope_mismatch",
                any(
                    str(observation.device_id) != str(execution.device_id)
                    or str(observation.run_id) not in run_ids
                    or str(observation.snapshot_id)
                    not in expected_snapshot_ids
                    for observation in selected_observations
                ),
            )
            expected_observation_types = set(
                str(item)
                for item in dict(
                    runtime_bundle.compatibility_json
                    if runtime_bundle is not None
                    else {}
                ).get("expected_observation_types")
                or []
                if str(item)
            )
            observed_types = {
                str(observation.observation_type or "")
                for observation in selected_observations
            }
            reject(
                "state_observation_contract_mismatch",
                bool(observed_types - expected_observation_types)
                or (bool(selected_observations) and "" in observed_types),
            )

        observed_at = (
            evidence.observation_max_at
            if evidence is not None and evidence.observation_max_at
            else execution.observation_max_at
            or execution.completed_at
            or execution.updated_at
        )
        if observed_at is None:
            rejection_codes.append("observation_time_missing")
            observed_at = datetime.fromtimestamp(0, tz=timezone.utc)
        if evidence is not None and execution.observation_max_at:
            reject(
                "observation_time_mismatch",
                evidence.observation_max_at is None
                or _utc(evidence.observation_max_at)
                != _utc(execution.observation_max_at),
            )

        fact_contract: dict[str, Any] = {}
        if check_item_version is not None and runtime_bundle is not None:
            fact_contract = build_comparison_fact_contract(
                check_item_version_id=str(check_item_version.id),
                check_item_digest=str(check_item_version.digest or ""),
                result_mode=str(check_item_version.result_mode or ""),
                assessment_mode=str(check_item_version.assessment_mode or ""),
                implementation_version_id=str(
                    runtime_bundle.implementation_version_id or ""
                ),
                canonical_catalog_version=(
                    runtime_bundle.canonical_catalog_version
                ),
                canonical_catalog_digest=str(
                    runtime_bundle.canonical_catalog_digest or ""
                ),
                entity_identity_policy_version_id=str(
                    runtime_bundle.entity_identity_policy_version_id or ""
                ),
                dependency_digests=dict(
                    runtime_bundle.dependency_digests_json or {}
                ),
                comparison_profile_version_id=(
                    runtime_bundle.comparison_profile_version_id
                ),
            )
        reject(
            "fact_contract_invalid",
            not _fact_contract_is_valid(fact_contract),
        )
        rejection_codes = list(dict.fromkeys(rejection_codes))
        metadata = {
            "schema_version": "baseline-candidate-evidence/v1",
            "patrol_batch_id": str(execution.patrol_batch_id),
            "execution_id": str(execution.id),
            "assessment_id": str(assessment.id) if assessment else None,
            "evidence_qualification_id": (
                str(evidence.id) if evidence else None
            ),
            "evidence_qualification_digest": (
                str(evidence.digest) if evidence else None
            ),
            "comparison_context_id": (
                str(comparison_context.id) if comparison_context else None
            ),
            "comparison_context_digest": (
                str(comparison_context.digest) if comparison_context else None
            ),
            "runtime_manifest_id": (
                str(runtime_manifest.id) if runtime_manifest else None
            ),
            "runtime_manifest_digest": (
                str(runtime_manifest.digest) if runtime_manifest else None
            ),
            "runtime_bundle_version_id": (
                str(runtime_bundle.id) if runtime_bundle else None
            ),
            "runtime_bundle_digest": (
                str(runtime_bundle.digest) if runtime_bundle else None
            ),
            "implementation_version_id": str(
                execution.implementation_version_id or ""
            ),
            "report_purpose": (
                str(batch.report_purpose) if batch else None
            ),
            "fact_contract": fact_contract,
            "rejection_codes": rejection_codes,
        }
        return BaselineCandidate(
            run_id=(
                next(iter(run_ids))
                if len(run_ids) == 1
                else f"invalid:{execution.id}"
            ),
            observed_at=_utc(observed_at),
            snapshot_ids=expected_snapshot_ids,
            canonical_schema_version=(
                str(comparison_context.canonical_schema_version)
                if comparison_context is not None
                else None
            ),
            entity_identity_policy_version=(
                str(comparison_context.entity_identity_policy_version)
                if comparison_context is not None
                else None
            ),
            successful=not rejection_codes,
            metadata=metadata,
        )


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
        current_fact_contract: Mapping[str, Any] | None = None,
    ) -> ComparisonDecision:
        current = _utc(current_observed_at)
        fact_contract = dict(current_fact_contract or {})
        if fact_contract and not _fact_contract_is_valid(fact_contract):
            raise ValueError("current comparison fact contract is invalid")
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
            decision_metadata = {
                "current_fact_contract": fact_contract,
                "candidate_count": 0,
            }
        elif not prior:
            status, reason = "baseline_missing", "prior_facts_failed_qualification"
            rejection_counts = Counter(
                str(code)
                for item in rows
                for code in item.metadata.get("rejection_codes") or []
                if str(code)
            )
            decision_metadata = {
                "current_fact_contract": fact_contract,
                "candidate_count": len(rows),
                "candidate_rejection_counts": dict(
                    sorted(rejection_counts.items())
                ),
                "candidate_execution_ids": sorted(
                    {
                        str(item.metadata.get("execution_id"))
                        for item in rows
                        if item.metadata.get("execution_id")
                    }
                ),
            }
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
                decision_metadata = {
                    "current_fact_contract": fact_contract,
                    "candidate_count": len(rows),
                    "successful_candidate_count": len(prior),
                }
            else:
                contract_compatible = [
                    item
                    for item in schema_compatible
                    if not fact_contract
                    or (
                        _fact_contract_is_valid(
                            item.metadata.get("fact_contract")
                        )
                        and str(
                            dict(item.metadata.get("fact_contract") or {}).get(
                                "digest"
                            )
                            or ""
                        )
                        == str(fact_contract.get("digest") or "")
                    )
                ]
                if not contract_compatible:
                    return self._decision(
                        mode="no_baseline",
                        comparison_status="schema_incompatible",
                        device_id=device_id,
                        check_item_version_id=check_item_version_id,
                        current_observed_at=current,
                        baseline=None,
                        max_baseline_age_seconds=max_baseline_age_seconds,
                        canonical_schema_version=canonical_schema_version,
                        entity_identity_policy_version=(
                            entity_identity_policy_version
                        ),
                        selection_reason="no_compatible_fact_contract",
                        metadata={
                            "current_fact_contract": fact_contract,
                            "candidate_count": len(rows),
                            "schema_compatible_candidate_count": len(
                                schema_compatible
                            ),
                        },
                    )
                baseline = contract_compatible[0]
                age_seconds = int((current - baseline.observed_at).total_seconds())
                if (
                    max_baseline_age_seconds is not None
                    and age_seconds > max_baseline_age_seconds
                ):
                    status, reason = "baseline_stale", "latest_compatible_baseline_expired"
                    decision_metadata = {
                        "current_fact_contract": fact_contract,
                        "candidate_count": len(rows),
                        "baseline_age_seconds": age_seconds,
                        "stale_candidate": dict(baseline.metadata),
                    }
                elif len(
                    [
                        item
                        for item in contract_compatible
                        if item.observed_at == baseline.observed_at
                    ]
                ) > 1:
                    status = "baseline_missing"
                    reason = "multiple_equally_recent_compatible_baselines"
                    decision_metadata = {
                        "current_fact_contract": fact_contract,
                        "candidate_count": len(rows),
                        "ambiguous_candidates": [
                            dict(item.metadata)
                            for item in contract_compatible
                            if item.observed_at == baseline.observed_at
                        ],
                    }
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
                        metadata={
                            "current_fact_contract": fact_contract,
                            "baseline_age_seconds": age_seconds,
                            "selected_baseline": dict(baseline.metadata),
                        },
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
            metadata=decision_metadata,
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
        current_fact_contract: Mapping[str, Any] | None = None,
    ) -> ComparisonDecision:
        current = _utc(current_observed_at)
        fact_contract = dict(current_fact_contract or {})
        if fact_contract and not _fact_contract_is_valid(fact_contract):
            raise ValueError("current comparison fact contract is invalid")
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
        elif fact_contract and (
            not _fact_contract_is_valid(baseline.metadata.get("fact_contract"))
            or str(
                dict(baseline.metadata.get("fact_contract") or {}).get(
                    "digest"
                )
                or ""
            )
            != str(fact_contract.get("digest") or "")
        ):
            status, reason = "schema_incompatible", "explicit_fact_contract_incompatible"
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
                metadata={
                    "current_fact_contract": fact_contract,
                    "selected_baseline": dict(baseline.metadata),
                },
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
            metadata={
                "current_fact_contract": fact_contract,
                "candidate_rejection_codes": list(
                    baseline.metadata.get("rejection_codes") or []
                ),
            },
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
