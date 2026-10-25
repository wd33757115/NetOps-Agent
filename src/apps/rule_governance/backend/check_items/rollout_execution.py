"""Trusted, isolated execution for rollout stage comparisons."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_

from src.apps.rule_governance.backend.check_items.assessment_policy_assets import (
    AssessmentPolicyAssetService,
)
from src.apps.rule_governance.backend.check_items.catalog import (
    CollectionCommand,
    primary_collection_commands,
)
from src.apps.rule_governance.backend.check_items.production_feedback import (
    aggregate_feedback_metrics,
)
from src.apps.rule_governance.backend.check_items.rollout_capture import (
    ProductionPatrolCaptureAdapter,
    RolloutCaptureAdapter,
    RolloutCaptureError,
)
from src.apps.rule_governance.backend.check_items.rollout_legacy import (
    LegacyRouteError,
    RolloutLegacyRouteService,
)
from src.apps.rule_governance.backend.check_items.runtime_bundle import (
    CheckItemRuntimeBundleService,
)
from src.apps.rule_governance.backend.parser.identity import DeviceIdentity
from src.apps.rule_governance.backend.parser.policy import ParserPolicy
from src.apps.rule_governance.backend.parser.runtime import ParserRuntime
from src.apps.rule_governance.backend.parser.runtime_bundle import ParserRuntimeBundle
from src.apps.rule_governance.backend.parser.runtime_repository import ParserRuntimeRepository
from src.core.check_items.assessment import (
    CheckAssessmentInput,
    CheckAssessmentService,
)
from src.core.check_items.assessment_policy import (
    build_target_assessment_policy_snapshot,
    validate_assessment_policy_snapshot,
)
from src.core.check_items.current_state import (
    CurrentStateEvaluationInput,
    CurrentStateEvaluationService,
)
from src.core.patrol.change_detector import (
    _detect_observation_changes_against_history_impl,
)
from src.core.patrol.event_builder import build_events_from_changes
from src.core.patrol.event_rules import event_rules_from_snapshot
from src.core.state.normalizer import observations_from_snapshot
from src.infrastructure.db.models import (
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationVersionRecord,
    CommandSnapshot,
    EventRuleRecord,
    EventRuleVersionRecord,
    NetworkDevice,
    ProductionFeedbackRecord,
    RolloutCampaignRecord,
    RolloutComparisonEvidenceRecord,
    RolloutOutboxRecord,
    RolloutProjectionRecord,
    RolloutStageAttemptRecord,
    RolloutStageExecutionRecord,
)
from src.infrastructure.db.postgres import get_db_session

ROLLOUT_OUTBOX_EVENT = "rollout.stage.execution.requested"
ROLLOUT_EVIDENCE_SCHEMA = "rollout-comparison-evidence/v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime:
    value = value or _utcnow()
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


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


def _iso(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    return str(value or "")




def _observation_payload(item: Any) -> dict[str, Any]:
    return item.to_dict() if hasattr(item, "to_dict") else dict(item)


def _canonical_observation(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "device_id": str(item.get("device_id") or ""),
        "entity_type": str(item.get("entity_type") or ""),
        "entity_key": str(item.get("entity_key") or ""),
        "field_name": str(item.get("field_name") or ""),
        "observation_type": str(item.get("observation_type") or ""),
        "semantic_type": str(item.get("semantic_type") or ""),
        "unit": str(item.get("unit") or ""),
        "dimensions": dict(item.get("dimensions") or {}),
        "value": item.get("value"),
    }


def _canonical_change(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "device_id": str(item.get("device_id") or ""),
        "entity_type": str(item.get("entity_type") or ""),
        "entity_key": str(item.get("entity_key") or ""),
        "field": str(item.get("field") or ""),
        "observation_type": str(item.get("observation_type") or ""),
        "change_type": str(item.get("change_type") or ""),
        "old": item.get("old"),
        "new": item.get("new"),
        "dimensions": dict(item.get("dimensions") or {}),
    }


def _canonical_event(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(item.get("payload") or {})
    return {
        "event_type": str(item.get("event_type") or ""),
        "severity": str(item.get("severity") or ""),
        "device_id": str(item.get("device_id") or ""),
        "entity_type": str(item.get("entity_type") or ""),
        "entity_key": str(item.get("entity_key") or ""),
        "rule_id": str(payload.get("rule_id") or ""),
        "event_class": str(payload.get("event_class") or ""),
    }


def _canonical_finding(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "device_id": str(item.get("device_id") or ""),
        "entity_type": str(item.get("entity_type") or ""),
        "entity_key": str(item.get("entity_key") or ""),
        "dimensions": dict(item.get("dimensions_json") or {}),
        "observation_type": str(item.get("observation_type") or ""),
        "actual_value": item.get("actual_value_json"),
        "reference_value": item.get("reference_value_json"),
        "reference_source": str(item.get("reference_source") or ""),
        "unit": str(item.get("unit") or ""),
        "severity": str(item.get("severity") or ""),
        "event_type": str(item.get("event_type") or ""),
    }


def _sorted_payloads(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(item) for item in values),
        key=lambda item: json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )


def _target_implementation_version_id(
    target: Mapping[str, Any],
    track: str,
) -> str:
    if track == "candidate":
        return str(
            target.get("candidate_implementation_version_id")
            or target.get("implementation_version_id")
            or ""
        )
    route = dict(target.get("baseline_route") or {})
    if str(route.get("mode") or "") == "no_prior_production":
        return ""
    return str(
        route.get("implementation_version_id")
        or target.get("implementation_version_id")
        or ""
    )


def _target_commands_by_track(
    target: Mapping[str, Any],
    commands_by_implementation: Mapping[str, tuple[str, ...]],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for track in ("baseline", "candidate"):
        implementation_version_id = _target_implementation_version_id(
            target,
            track,
        )
        result[track] = (
            list(commands_by_implementation[implementation_version_id])
            if implementation_version_id
            else []
        )
    return result


class RolloutStageExecutionError(RuntimeError):
    """Raised when trusted rollout execution cannot produce evidence."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


class RolloutOutboxRepository:
    """Lease and publish rollout execution requests after transaction commit."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (
            get_db_session
        ),
    ) -> None:
        self._session_scope = session_scope

    def claim_pending(
        self,
        *,
        limit: int = 100,
        lease_seconds: int = 120,
        max_attempts: int = 10,
    ) -> list[dict[str, Any]]:
        now = _utcnow()
        with self._session_scope() as db:
            rows = (
                db.query(RolloutOutboxRecord)
                .filter(
                    RolloutOutboxRecord.available_at <= now,
                    or_(
                        RolloutOutboxRecord.status.in_(
                            ["pending", "retry_wait"]
                        ),
                        and_(
                            RolloutOutboxRecord.status == "publishing",
                            RolloutOutboxRecord.lease_until < now,
                        ),
                    ),
                    RolloutOutboxRecord.attempts < int(max_attempts),
                )
                .order_by(
                    RolloutOutboxRecord.available_at,
                    RolloutOutboxRecord.created_at,
                    RolloutOutboxRecord.id,
                )
                .limit(max(1, min(int(limit), 500)))
                .with_for_update(skip_locked=True)
                .all()
            )
            detached: list[dict[str, Any]] = []
            for row in rows:
                row.status = "publishing"
                row.claim_token = uuid.uuid4().hex
                row.lease_until = now + timedelta(
                    seconds=max(30, int(lease_seconds))
                )
                row.attempts = int(row.attempts or 0) + 1
                row.last_error = None
                detached.append(row.to_dict())
            db.flush()
            return detached

    def mark_published(
        self,
        outbox_id: str,
        *,
        claim_token: str,
    ) -> None:
        with self._session_scope() as db:
            updated = (
                db.query(RolloutOutboxRecord)
                .filter(
                    RolloutOutboxRecord.id == outbox_id,
                    RolloutOutboxRecord.status == "publishing",
                    RolloutOutboxRecord.claim_token == claim_token,
                )
                .update(
                    {
                        RolloutOutboxRecord.status: "published",
                        RolloutOutboxRecord.published_at: _utcnow(),
                        RolloutOutboxRecord.claim_token: None,
                        RolloutOutboxRecord.lease_until: None,
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise RolloutStageExecutionError(
                    "outbox_fenced",
                    f"rollout outbox claim was fenced: {outbox_id}",
                )

    def release(
        self,
        outbox_id: str,
        *,
        claim_token: str,
        error: str,
        retry_seconds: int = 30,
        max_attempts: int = 10,
    ) -> None:
        now = _utcnow()
        with self._session_scope() as db:
            row = (
                db.query(RolloutOutboxRecord)
                .filter(
                    RolloutOutboxRecord.id == outbox_id,
                    RolloutOutboxRecord.status == "publishing",
                    RolloutOutboxRecord.claim_token == claim_token,
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise RolloutStageExecutionError(
                    "outbox_fenced",
                    f"rollout outbox claim was fenced: {outbox_id}",
                )
            row.status = (
                "dead_letter"
                if int(row.attempts or 0) >= int(max_attempts)
                else "retry_wait"
            )
            row.available_at = now + timedelta(
                seconds=max(1, int(retry_seconds))
            )
            row.claim_token = None
            row.lease_until = None
            row.last_error = str(error)[:4000]


class RolloutProjectionEngine:
    """Run a pinned Parser and rule bundle without writing production facts."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (
            get_db_session
        ),
        *,
        legacy_route_service: RolloutLegacyRouteService | None = None,
    ) -> None:
        self._session_scope = session_scope
        self._legacy_routes = (
            legacy_route_service
            or RolloutLegacyRouteService(session_scope)
        )

    def project(
        self,
        *,
        execution_id: str,
        track: str,
        target: Mapping[str, Any],
        snapshots: list[dict[str, Any]],
        periods: list[dict[str, Any]] | None = None,
        route: Mapping[str, Any],
    ) -> dict[str, Any]:
        if track not in {"baseline", "candidate"}:
            raise RolloutStageExecutionError(
                "invalid_track",
                f"unsupported rollout projection track: {track}",
            )
        route_mode = str(route.get("mode") or "")
        if route_mode == "no_prior_production":
            return self._no_prior_projection(
                execution_id=execution_id,
                track=track,
                target=target,
                snapshots=snapshots,
            )
        if route_mode == "legacy_route":
            try:
                descriptor = self._legacy_routes.resolve(
                    str(route.get("route_id") or ""),
                    str(route.get("digest") or ""),
                )
            except LegacyRouteError as exc:
                raise RolloutStageExecutionError(
                    exc.code,
                    str(exc),
                ) from exc
            facts = self.project(
                execution_id=execution_id,
                track=track,
                target=target,
                snapshots=snapshots,
                periods=periods,
                route=dict(descriptor["projection_route"]),
            )
            return {
                **facts,
                "route_mode": "legacy_route",
                "legacy_route_id": str(descriptor["id"]),
                "legacy_route_digest": str(descriptor["digest"]),
                "legacy_adapter_kind": str(
                    descriptor["adapter_kind"]
                ),
                "legacy_adapter_version": str(
                    descriptor["adapter_version"]
                ),
            }
        if route_mode != "runtime_bundle":
            raise RolloutStageExecutionError(
                "baseline_adapter_unavailable",
                "trusted rollout execution currently requires an immutable "
                "RuntimeBundle baseline",
            )
        version_id = str(
            route.get("runtime_bundle_version_id") or ""
        ).strip()
        bundle = CheckItemRuntimeBundleService(
            self._session_scope
        ).get_version(version_id)
        if bundle is None:
            raise RolloutStageExecutionError(
                "runtime_bundle_missing",
                f"RuntimeBundle not found: {version_id}",
            )
        expected_bundle_digest = str(
            route.get("expected_runtime_bundle_digest") or ""
        ).strip()
        if (
            expected_bundle_digest
            and expected_bundle_digest != str(bundle["digest"])
        ):
            raise RolloutStageExecutionError(
                "runtime_bundle_digest_mismatch",
                "RuntimeBundle digest differs from the frozen route",
            )
        if str(bundle["check_item_version_id"]) != str(
            target["check_item_version_id"]
        ):
            raise RolloutStageExecutionError(
                "runtime_bundle_scope_mismatch",
                "RuntimeBundle check-item version differs from rollout target",
            )
        expected_implementation_version_id = str(
            route.get("implementation_version_id")
            or _target_implementation_version_id(target, track)
            or ""
        )
        if str(bundle["implementation_version_id"]) != str(
            expected_implementation_version_id
        ):
            raise RolloutStageExecutionError(
                "runtime_bundle_scope_mismatch",
                "RuntimeBundle implementation differs from rollout target",
            )
        target = {
            **dict(target),
            "implementation_version_id": str(
                bundle["implementation_version_id"]
            ),
        }
        frozen_policy_contract = dict(
            dict(bundle.get("compatibility") or {}).get(
                "assessment_policy"
            )
            or {}
        )
        assessment_policy_snapshot = dict(
            target.get("assessment_policy_snapshot") or {}
        )
        assessment_policy_snapshot_digest = str(
            target.get("assessment_policy_snapshot_digest") or ""
        ).strip()
        if frozen_policy_contract:
            if (
                not assessment_policy_snapshot
                or not assessment_policy_snapshot_digest
            ):
                raise RolloutStageExecutionError(
                    "assessment_policy_snapshot_missing",
                    "rollout target has no frozen assessment-policy snapshot",
                )
            if (
                not validate_assessment_policy_snapshot(
                    assessment_policy_snapshot
                )
                or str(assessment_policy_snapshot.get("digest") or "")
                != assessment_policy_snapshot_digest
            ):
                raise RolloutStageExecutionError(
                    "assessment_policy_snapshot_invalid",
                    "rollout target assessment-policy snapshot is invalid",
                )
            if str(
                assessment_policy_snapshot.get("contract_digest") or ""
            ) != str(frozen_policy_contract.get("digest") or ""):
                raise RolloutStageExecutionError(
                    "assessment_policy_snapshot_mismatch",
                    "rollout target policy contract differs from RuntimeBundle",
                )

        (
            contracts,
            policies,
            event_rules,
            current_state_rules,
            runtime_assets,
            check_item,
        ) = self._load_assets(bundle)
        uses_direct_projection = any(
            str(item.get("asset_type") or "") == "command_adapter_projection"
            for item in runtime_assets
        )
        parsed: list[dict[str, Any]] = []
        observations_by_snapshot: dict[str, list[dict[str, Any]]] = {}
        for ordinal, snapshot in enumerate(
            sorted(
                snapshots,
                key=lambda item: (
                    _iso(item.get("observed_at")),
                    str(item.get("id") or ""),
                ),
            ),
            start=1,
        ):
            identity = DeviceIdentity(
                vendor=str(snapshot.get("vendor") or "") or None,
                model=str(snapshot.get("model") or "") or None,
                family=str(snapshot.get("family") or "") or None,
                confidence=1.0,
                evidence_text="frozen inventory and snapshot identity",
                source="rollout_frozen_snapshot",
            )
            parser_bundle = ParserRuntimeBundle.from_dict(
                {
                    "vendor": identity.vendor or "",
                    "model": identity.model or "",
                    "family": identity.family,
                    "templates": dict(bundle["parser_bundle"]),
                    "output_quality_profiles": dict(
                        dict(bundle.get("compatibility") or {}).get(
                            "output_quality_profiles"
                        )
                        or {}
                    ),
                }
            )
            result = ParserRuntime(
                registry=ParserRuntimeRepository(
                    session_scope=self._session_scope
                ),
                runtime_bundle=parser_bundle,
            ).parse(
                identity=identity,
                command=str(
                    snapshot.get("canonical_command")
                    or snapshot.get("command")
                    or ""
                ),
                raw_text=str(snapshot.get("raw_text") or ""),
            )
            parse_row = {
                "snapshot_id": str(snapshot["id"]),
                "raw_text_hash": str(snapshot["raw_text_hash"]),
                "observed_at": _iso(snapshot.get("observed_at")),
                "canonical_command": str(result.canonical_command),
                "parser_status": str(result.parser_status),
                "failure_code": result.failure_code,
                "template_id": result.template_id,
                "template_version": result.template_version,
                "output_status": result.output_status,
                "record_count": (
                    sum(
                        len(values)
                        for values in dict(
                            dict(result.structured_data or {}).get(
                                "record_sets"
                            )
                            or {}
                        ).values()
                    )
                    if isinstance(result.structured_data, dict)
                    and result.structured_data.get("record_sets")
                    else len(result.structured_data or [])
                    if isinstance(result.structured_data, list)
                    else 0
                ),
            }
            parsed.append(parse_row)
            if result.parser_status not in {"success", "empty"}:
                observations_by_snapshot[str(snapshot["id"])] = []
                continue
            contract = contracts.get(str(result.template_id or ""))
            if not uses_direct_projection and contract is None:
                raise RolloutStageExecutionError(
                    "observation_contract_missing",
                    "frozen RuntimeBundle has no ObservationContract for "
                    f"Parser {result.template_id}",
                )
            projected_snapshot_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/rollout-projection/{execution_id}/{track}/"
                    f"{snapshot['id']}/{version_id}",
                )
            )
            projected_snapshot = {
                **snapshot,
                "id": projected_snapshot_id,
                "run_id": (
                    f"rollout:{execution_id}:{track}:"
                    f"{target['target_key']}"
                ),
                "structured_json": result.structured_data,
                "parser_template_id": result.template_id,
                "parser_version": result.template_version,
                "implementation_version_id": str(
                    bundle["implementation_version_id"]
                ),
                "contract_version": (
                    contract["contract_version"] if contract is not None else None
                ),
                "execution_item_id": (
                    f"rollout:{execution_id}:{track}:{ordinal}"
                ),
                "metadata_json": {
                    **dict(snapshot.get("metadata_json") or {}),
                    "rollout_stage_execution_id": execution_id,
                    "rollout_track": track,
                    "command_ordinal": ordinal,
                    "historical_backfill": True,
                },
            }
            observations = (
                observations_from_snapshot(
                    projected_snapshot,
                    mode="parser_validation",
                    source="rollout_snapshot_replay",
                    dataset_id=str(snapshot.get("dataset_id") or "") or None,
                )
                if uses_direct_projection
                else observations_from_snapshot(
                    projected_snapshot,
                    mode="parser_validation",
                    source="rollout_snapshot_replay",
                    dataset_id=str(snapshot.get("dataset_id") or "") or None,
                    observation_contract_version=int(contract["contract_version"]),
                    _contract_override=contract,
                )
            )
            observations_by_snapshot[str(snapshot["id"])] = [
                _observation_payload(item) for item in observations
            ]

        period_manifest = list(periods or [])
        if not period_manifest:
            period_manifest = [
                {
                    "period_key": str(snapshot.get("run_id") or snapshot["id"]),
                    "snapshot_ids": [str(snapshot["id"])],
                }
                for snapshot in sorted(
                    snapshots,
                    key=lambda item: (
                        _iso(item.get("observed_at")),
                        str(item.get("id") or ""),
                    ),
                )
            ]
        observations_by_period = [
            [
                observation
                for snapshot_id in list(period.get("snapshot_ids") or [])
                for observation in observations_by_snapshot.get(
                    str(snapshot_id),
                    [],
                )
            ]
            for period in period_manifest
        ]
        current_observations = (
            observations_by_period[-1]
            if observations_by_period
            else []
        )
        prior_observations = (
            observations_by_period[-2]
            if len(observations_by_period) > 1
            else []
        )
        analysis_revision = (
            f"rollout:{execution_id}:{track}:{target['target_key']}"
        )
        changes = _detect_observation_changes_against_history_impl(
            current_run_id=f"{analysis_revision}:current",
            previous_run_id=(
                f"{analysis_revision}:baseline"
                if prior_observations
                else None
            ),
            persist=False,
            policy_bundle=policies,
            current_observations=current_observations,
            prior_observations=prior_observations,
            analysis_revision=analysis_revision,
            rule_bundle_digest=str(bundle["digest"]),
        )
        compiled_rules = event_rules_from_snapshot(event_rules)
        period_events = [
            item.to_dict()
            for item in build_events_from_changes(
                list(changes.get("changes") or []),
                rules=compiled_rules,
            )
        ]
        parse_success = all(
            item["parser_status"] in {"success", "empty"}
            for item in parsed
        )
        all_observations = [
            item
            for snapshot_rows in observations_by_period
            for item in snapshot_rows
        ]
        observed_entities = {
            (
                str(item.get("entity_type") or ""),
                str(item.get("entity_key") or ""),
            )
            for item in current_observations
        }
        canonical_observations = _sorted_payloads(
            _canonical_observation(item) for item in all_observations
        )
        canonical_changes = _sorted_payloads(
            _canonical_change(item)
            for item in list(changes.get("changes") or [])
        )
        canonical_events = _sorted_payloads(
            _canonical_event(item) for item in period_events
        )
        assessed_at = _utc(
            snapshots[-1].get("observed_at") if snapshots else None
        )
        finding_result = CurrentStateEvaluationService.evaluate(
            CurrentStateEvaluationInput(
                patrol_batch_id=(
                    f"rollout:{execution_id}:{track}"
                ),
                check_execution_id=(
                    f"rollout:{execution_id}:{track}:"
                    f"{target['target_key']}"
                ),
                device_id=str(target["device_id"]),
                check_item_id=str(check_item["check_item_id"]),
                check_item_version_id=str(
                    target["check_item_version_id"]
                ),
                implementation_version_id=str(
                    target["implementation_version_id"]
                ),
                runtime_bundle_version_id=str(bundle["version_id"]),
                runtime_bundle_digest=str(bundle["digest"]),
                observations=tuple(current_observations),
                rules=tuple(current_state_rules),
                runtime_assets=tuple(runtime_assets),
                assessment_policy_snapshot=assessment_policy_snapshot,
                baseline_observations=tuple(prior_observations),
                assessed_at=assessed_at,
            )
        )
        findings = _sorted_payloads(
            _canonical_finding(item)
            for item in finding_result.finding_values
        )
        evaluation_coverage = dict(finding_result.coverage or {})
        evaluated_entity_count = int(
            evaluation_coverage.get("evaluated_entity_count") or 0
        )
        threshold_unknown_count = int(
            evaluation_coverage.get(
                "threshold_unknown_observation_count"
            )
            or 0
        )
        partial_threshold_count = int(
            evaluation_coverage.get(
                "partial_threshold_observation_count"
            )
            or 0
        )
        all_empty = bool(parsed) and all(
            item["parser_status"] == "empty" for item in parsed
        )
        output_quality_status = (
            "empty"
            if all_empty
            else "complete"
            if parse_success
            else "failed"
        )
        empty_result_classification = (
            str(
                dict(check_item.get("empty_result_policy") or {}).get(
                    "classification"
                )
                or "not_evaluated"
            )
            if all_empty
            else "not_evaluated"
        )
        coverage_status = (
            "complete"
            if (
                bool(current_observations)
                and evaluated_entity_count >= len(observed_entities)
            )
            else "empty"
            if all_empty
            else "unknown"
        )
        assessment_decision = CheckAssessmentService.assess(
            CheckAssessmentInput(
                execution_id=(
                    f"rollout:{execution_id}:{track}:"
                    f"{target['target_key']}"
                ),
                patrol_batch_id=f"rollout:{execution_id}:{track}",
                device_id=str(target["device_id"]),
                check_item_version_id=str(
                    target["check_item_version_id"]
                ),
                execution_status=(
                    "completed" if parse_success else "failed"
                ),
                assessment_mode=str(
                    check_item.get("assessment_mode") or "health"
                ),
                comparison_status=(
                    "comparable"
                    if prior_observations
                    else "first_observation"
                ),
                observation_count=len(current_observations),
                observed_entity_count=len(observed_entities),
                evaluated_entity_count=evaluated_entity_count,
                threshold_unknown_count=threshold_unknown_count,
                partial_threshold_count=partial_threshold_count,
                current_finding_ids=tuple(
                    str(item["id"])
                    for item in finding_result.finding_values
                ),
                rule_assets_ready=bool(current_state_rules),
                evidence_qualification_id=(
                    f"rollout-evidence:{execution_id}:{track}"
                    if parse_success
                    else None
                ),
                coverage_decision_id=(
                    f"rollout-coverage:{execution_id}:{track}"
                    if coverage_status == "complete"
                    else None
                ),
                source_identity_status="confirmed",
                output_quality_status=output_quality_status,
                freshness_status="fresh",
                empty_result_classification=(
                    empty_result_classification
                ),
                coverage_status=coverage_status,
                coverage_basis="rollout_observed_entity_set",
                blocking_reason_codes=tuple(
                    evaluation_coverage.get(
                        "assessment_policy_blocking_reason_codes"
                    )
                    or []
                ),
                evidence_refs=tuple(
                    [
                        *(str(item["id"]) for item in snapshots),
                        str(bundle["version_id"]),
                    ]
                ),
                assessment_rule_bundle_digest=str(bundle["digest"]),
                runtime_bundle_version_id=str(bundle["version_id"]),
                assessment_policy_snapshot_digest=(
                    assessment_policy_snapshot_digest or None
                ),
                assessment_basis=dict(
                    finding_result.assessment_basis or {}
                ),
                assessed_at=assessed_at,
            )
        )
        assessment = {
            "formation_status": "completed",
            "parse_success": parse_success,
            "assessment_status": (
                assessment_decision.assessment_status
            ),
            "completeness_status": str(
                assessment_decision.values["completeness_status"]
            ),
            "comparison_status": str(
                assessment_decision.values["comparison_status"]
            ),
            "blocking_reason_codes": list(
                assessment_decision.blocking_reason_codes
            ),
            "observation_count": len(current_observations),
            "observed_entity_count": len(observed_entities),
            "evaluated_entity_count": evaluated_entity_count,
            "threshold_unknown_count": threshold_unknown_count,
            "partial_threshold_count": partial_threshold_count,
            "finding_count": len(findings),
            "assessment_policy_snapshot_digest": (
                assessment_policy_snapshot_digest or None
            ),
            "assessment_basis": dict(
                finding_result.assessment_basis or {}
            ),
            "assessment_basis_digest": dict(
                finding_result.assessment_basis or {}
            ).get("digest"),
        }
        return {
            "schema_version": "rollout-projection/v1",
            "execution_id": execution_id,
            "track": track,
            "target_key": str(target["target_key"]),
            "device_id": str(target["device_id"]),
            "check_item_version_id": str(
                target["check_item_version_id"]
            ),
            "implementation_version_id": (
                _target_implementation_version_id(target, track) or None
            ),
            "runtime_bundle_version_id": str(bundle["version_id"]),
            "runtime_bundle_digest": str(bundle["digest"]),
            "assessment_policy_snapshot_digest": (
                assessment_policy_snapshot_digest or None
            ),
            "snapshot_ids": [str(item["id"]) for item in snapshots],
            "periods": period_manifest,
            "snapshot_set_digest": _digest(
                [
                    {
                        "id": str(item["id"]),
                        "raw_text_hash": str(item["raw_text_hash"]),
                    }
                    for item in snapshots
                ]
            ),
            "parse_results": parsed,
            "observations": canonical_observations,
            "changes": canonical_changes,
            "events": canonical_events,
            "findings": findings,
            "identity_bindings": sorted(
                [
                    {
                        "device_id": str(target["device_id"]),
                        "entity_type": entity_type,
                        "entity_key": entity_key,
                    }
                    for entity_type, entity_key in observed_entities
                ],
                key=lambda item: (
                    item["entity_type"],
                    item["entity_key"],
                ),
            ),
            "assessment": assessment,
            "report_projection": {
                "status": assessment_decision.assessment_status,
                "completeness_status": str(
                    assessment_decision.values["completeness_status"]
                ),
                "observation_count": len(current_observations),
                "change_count": len(canonical_changes),
                "event_count": len(canonical_events),
                "finding_count": len(findings),
                "risk_count": len(findings),
                "blocking_reason_codes": list(
                    assessment_decision.blocking_reason_codes
                ),
            },
            "raw_cli_included": False,
        }

    @staticmethod
    def _no_prior_projection(
        *,
        execution_id: str,
        track: str,
        target: Mapping[str, Any],
        snapshots: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": "rollout-projection/v1",
            "execution_id": execution_id,
            "track": track,
            "target_key": str(target["target_key"]),
            "device_id": str(target["device_id"]),
            "check_item_version_id": str(
                target["check_item_version_id"]
            ),
            "implementation_version_id": (
                _target_implementation_version_id(target, track) or None
            ),
            "runtime_bundle_version_id": None,
            "snapshot_ids": [str(item["id"]) for item in snapshots],
            "parse_results": [],
            "observations": [],
            "changes": [],
            "events": [],
            "findings": [],
            "identity_bindings": [],
            "assessment": {
                "formation_status": "not_applicable",
                "parse_success": None,
                "assessment_status": "not_applicable",
                "completeness_status": "complete",
                "blocking_reason_codes": [],
                "observation_count": 0,
                "observed_entity_count": 0,
                "evaluated_entity_count": 0,
                "finding_count": 0,
            },
            "report_projection": {
                "status": "not_applicable",
                "completeness_status": "complete",
                "observation_count": 0,
                "change_count": 0,
                "event_count": 0,
                "finding_count": 0,
                "risk_count": 0,
                "blocking_reason_codes": [],
            },
            "baseline_not_applicable": True,
            "raw_cli_included": False,
        }

    def _load_assets(
        self,
        bundle: Mapping[str, Any],
    ) -> tuple[
        dict[str, dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        assets = list(bundle.get("assets") or [])
        contract_ids = {
            str(item["asset_version_id"])
            for item in assets
            if item["asset_type"] == "observation_contract"
        }
        projection_assets = [
            dict(item)
            for item in assets
            if str(item.get("asset_type") or "") == "command_adapter_projection"
        ]
        if projection_assets and contract_ids:
            raise RolloutStageExecutionError(
                "runtime_projection_ambiguous",
                "a frozen RuntimeBundle cannot contain both CommandAdapter "
                "projection and ObservationContract",
            )
        if len(projection_assets) > 1:
            raise RolloutStageExecutionError(
                "runtime_projection_ambiguous",
                "a frozen RuntimeBundle must contain exactly one CommandAdapter projection",
            )
        if projection_assets and str(projection_assets[0].get("asset_version_id") or "") != str(
            bundle.get("implementation_version_id") or ""
        ):
            raise RolloutStageExecutionError(
                "runtime_projection_mismatch",
                "the frozen CommandAdapter projection does not match the "
                "bundle implementation version",
            )
        policy_ids = {
            str(item["asset_version_id"])
            for item in assets
            if item["asset_type"] == "change_policy"
        }
        event_rule_ids = {
            str(item["asset_version_id"])
            for item in assets
            if item["asset_type"] == "event_rule"
        }
        current_state_rule_ids = {
            str(item["asset_version_id"])
            for item in assets
            if item["asset_type"] == "current_state_rule"
        }
        all_rule_ids = event_rule_ids | current_state_rule_ids
        with self._session_scope() as db:
            check_version = db.get(
                CheckItemVersionRecord,
                str(bundle["check_item_version_id"]),
            )
            if check_version is None:
                raise RolloutStageExecutionError(
                    "check_item_version_missing",
                    "frozen CheckItem version is missing",
                )
            if contract_ids:
                raise RolloutStageExecutionError("legacy_bundle_retired", "旧数据契约运行包已退役，请重新装配 V2 运行包")
            contract_by_parser = {}

            policy_rows = (
                db.query(
                    ChangePolicyRecord,
                    ChangePolicyVersionRecord,
                )
                .join(
                    ChangePolicyVersionRecord,
                    ChangePolicyVersionRecord.policy_id
                    == ChangePolicyRecord.id,
                )
                .filter(
                    ChangePolicyVersionRecord.id.in_(sorted(policy_ids))
                )
                .all()
                if policy_ids
                else []
            )
            if len(policy_rows) != len(policy_ids):
                raise RolloutStageExecutionError(
                    "change_policy_missing",
                    "one or more frozen ChangePolicies are missing",
                )
            policies = [
                {
                    **policy.to_dict(),
                    "policy_id": str(policy.id),
                    "policy_name": str(policy.name),
                    "version_id": str(version.id),
                    "version": int(version.version),
                    "observation_type": str(version.observation_type),
                    "semantic_type": str(version.semantic_type),
                    "compare_mode": str(version.compare_mode),
                    "policy": dict(version.policy_json or {}),
                    "digest": str(version.digest or ""),
                }
                for policy, version in policy_rows
            ]

            rule_rows = (
                db.query(EventRuleVersionRecord, EventRuleRecord)
                .join(
                    EventRuleRecord,
                    EventRuleRecord.id == EventRuleVersionRecord.rule_id,
                )
                .filter(EventRuleVersionRecord.id.in_(sorted(all_rule_ids)))
                .all()
                if all_rule_ids
                else []
            )
            if len(rule_rows) != len(all_rule_ids):
                raise RolloutStageExecutionError(
                    "event_rule_missing",
                    "one or more frozen EventRules are missing",
                )
            all_rules = [
                {
                    "rule_id": str(rule.id),
                    "rule_name": str(rule.name),
                    "version_id": str(version.id),
                    "version": int(version.version),
                    "event_type": str(version.event_type),
                    "severity": str(version.severity),
                    "condition": dict(version.condition_json or {}),
                    "priority": int(version.priority or 100),
                    "exclusive": bool(version.exclusive),
                    "event_class": str(version.event_class or "fault"),
                    "evaluation_scope": str(
                        version.evaluation_scope or "both"
                    ),
                    "recovery": dict(version.recovery_json or {}),
                    "correlation_keys": list(
                        version.correlation_key_json or []
                    ),
                    "digest": str(version.digest or ""),
                }
                for version, rule in rule_rows
            ]
        event_rules = [
            item
            for item in all_rules
            if str(item["version_id"]) in event_rule_ids
        ]
        current_state_rules = [
            item
            for item in all_rules
            if str(item["version_id"]) in current_state_rule_ids
        ]
        return (
            contract_by_parser,
            policies,
            event_rules,
            current_state_rules,
            [dict(item) for item in assets],
            {
                "check_item_id": str(check_version.check_item_id),
                "assessment_mode": str(check_version.assessment_mode),
                "empty_result_policy": dict(
                    check_version.empty_result_policy_json or {}
                ),
                "coverage_policy": dict(
                    check_version.coverage_policy_json or {}
                ),
            },
        )


class RolloutStageExecutionService:
    """Prepare, run and evaluate one rollout stage from persisted evidence."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (
            get_db_session
        ),
        *,
        object_storage: Any | None = None,
        projection_engine: RolloutProjectionEngine | None = None,
        capture_adapter: RolloutCaptureAdapter | None = None,
    ) -> None:
        self._session_scope = session_scope
        self._object_storage = object_storage
        self._projection_engine = projection_engine or RolloutProjectionEngine(
            session_scope
        )
        self._capture_adapter = (
            capture_adapter
            or ProductionPatrolCaptureAdapter(session_scope)
        )

    def prepare_in_transaction(
        self,
        db: Any,
        *,
        campaign: RolloutCampaignRecord,
        attempt: RolloutStageAttemptRecord,
        actor: str,
        actor_user_id: str | None = None,
        execution_mode: str = "snapshot_replay",
    ) -> dict[str, Any]:
        execution_mode = str(execution_mode or "").strip()
        if execution_mode not in {"snapshot_replay", "live_capture"}:
            raise RolloutStageExecutionError(
                "execution_mode_invalid",
                "execution_mode must be snapshot_replay or live_capture",
            )
        existing = (
            db.query(RolloutStageExecutionRecord)
            .filter(
                RolloutStageExecutionRecord.stage_attempt_id == attempt.id
            )
            .one_or_none()
        )
        if existing is not None:
            return existing.to_dict()
        target_manifest = dict(campaign.target_manifest_json or {})
        roster = dict(
            dict(target_manifest.get("stage_rosters") or {}).get(
                str(attempt.stage),
                {},
            )
        )
        target_keys = [
            str(item) for item in roster.get("target_keys") or []
        ]
        rows_by_key = {
            str(item.get("target_key") or ""): dict(item)
            for item in target_manifest.get("matrix") or []
        }
        selected = [
            rows_by_key[item]
            for item in target_keys
            if item in rows_by_key
        ]
        if not selected or len(selected) != len(target_keys):
            raise RolloutStageExecutionError(
                "selection_manifest_invalid",
                "frozen stage roster does not map exactly to target rows",
            )
        commands_by_implementation = self._implementation_commands(
            db,
            selected=selected,
        )
        device_ids = sorted({str(item["device_id"]) for item in selected})
        check_item_version_ids = sorted(
            {str(item["check_item_version_id"]) for item in selected}
        )
        device_by_id = {
            str(item.id): item
            for item in db.query(NetworkDevice)
            .filter(NetworkDevice.id.in_(device_ids))
            .all()
        }
        check_version_by_id = {
            str(item.id): item
            for item in db.query(CheckItemVersionRecord)
            .filter(CheckItemVersionRecord.id.in_(check_item_version_ids))
            .all()
        }
        if set(device_by_id) != set(device_ids):
            raise RolloutStageExecutionError(
                "assessment_policy_target_missing",
                "rollout selection contains a device missing from inventory",
            )
        if set(check_version_by_id) != set(check_item_version_ids):
            raise RolloutStageExecutionError(
                "assessment_policy_target_missing",
                "rollout selection contains a missing check-item version",
            )
        governed_policy_sources = AssessmentPolicyAssetService(
            self._session_scope
        ).list_rendered_sources(
            check_item_ids={
                str(row.check_item_id) for row in check_version_by_id.values()
            },
            session=db,
        )
        selection_targets: list[dict[str, Any]] = []
        for item in selected:
            check_version = check_version_by_id[
                str(item["check_item_version_id"])
            ]
            assessment_policy_snapshot = (
                build_target_assessment_policy_snapshot(
                    check_item_id=str(check_version.check_item_id),
                    check_item_version_id=str(item["check_item_version_id"]),
                    canonical_contract=dict(
                        check_version.canonical_contract_json or {}
                    ),
                    device=device_by_id[str(item["device_id"])].to_dict(),
                    governed_sources=governed_policy_sources.get(
                        str(check_version.check_item_id),
                        (),
                    ),
                    applicability_requirement=dict(
                        check_version.applicability_json or {}
                    ),
                    policy_effective_at=(
                        attempt.created_at or _utcnow()
                    ),
                )
            )
            commands_by_track = _target_commands_by_track(
                item,
                commands_by_implementation,
            )
            all_commands = list(
                dict.fromkeys(
                    [
                        *commands_by_track["baseline"],
                        *commands_by_track["candidate"],
                    ]
                )
            )
            selection_targets.append(
                {
                    **item,
                    "commands": all_commands,
                    "commands_by_track": commands_by_track,
                    "assessment_policy_snapshot": assessment_policy_snapshot,
                    "assessment_policy_snapshot_digest": (
                        assessment_policy_snapshot["digest"]
                    ),
                }
            )
        selection_basis = {
            "schema_version": "rollout-stage-selection-basis/v1",
            "campaign_id": str(campaign.id),
            "stage_attempt_id": str(attempt.id),
            "stage": str(attempt.stage),
            "roster_digest": str(roster.get("digest") or ""),
            "execution_mode": execution_mode,
            "targets": selection_targets,
        }
        selection_basis_digest = _digest(selection_basis)
        snapshot_rows: list[CommandSnapshot] = []
        if execution_mode == "snapshot_replay":
            snapshot_bindings, snapshot_rows = self._select_snapshots(
                db,
                selected=selected,
                baseline_window=dict(campaign.baseline_window_json or {}),
                commands_by_implementation=commands_by_implementation,
            )
            manifest_targets = [
                {
                    **item,
                    "snapshot_ids": list(
                        snapshot_bindings[
                            str(item["target_key"])
                        ]["snapshot_ids"]
                    ),
                    "periods": list(
                        snapshot_bindings[
                            str(item["target_key"])
                        ]["periods"]
                    ),
                    "snapshot_ids_by_track": dict(
                        snapshot_bindings[
                            str(item["target_key"])
                        ]["snapshot_ids_by_track"]
                    ),
                    "periods_by_track": dict(
                        snapshot_bindings[
                            str(item["target_key"])
                        ]["periods_by_track"]
                    ),
                }
                for item in selection_targets
            ]
        else:
            manifest_targets = selection_targets
        selection_manifest = {
            "schema_version": "rollout-stage-selection/v3",
            "execution_user_id": actor_user_id,
            **{
                key: value
                for key, value in selection_basis.items()
                if key not in {"schema_version", "targets"}
            },
            "selection_basis_digest": selection_basis_digest,
            "targets": manifest_targets,
        }
        selection_digest = _digest(selection_manifest)
        snapshot_manifest = [
            {
                "id": str(item.id),
                "raw_text_hash": str(item.raw_text_hash),
                "observed_at": _iso(item.observed_at),
                "created_at": _iso(item.created_at),
            }
            for item in snapshot_rows
        ]
        snapshot_set_digest = _digest(snapshot_manifest)
        high_watermark = max(
            (
                _utc(item.created_at or item.observed_at)
                for item in snapshot_rows
            ),
            default=_utcnow(),
        )
        execution_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/rollout-stage-execution/{attempt.id}/"
                f"{execution_mode}/{selection_digest}/{snapshot_set_digest}",
            )
        )
        now = _utcnow()
        execution = RolloutStageExecutionRecord(
            id=execution_id,
            campaign_id=str(campaign.id),
            stage_attempt_id=str(attempt.id),
            execution_mode=execution_mode,
            status="queued",
            selection_manifest_json=selection_manifest,
            selection_manifest_digest=selection_digest,
            source_snapshot_ids_json=[
                str(item["id"]) for item in snapshot_manifest
            ],
            snapshot_set_digest=snapshot_set_digest,
            fact_high_watermark=high_watermark,
            fencing_token=0,
            attempt_count=0,
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        db.add(execution)
        # The outbox references this row by FK but the models intentionally do
        # not own an ORM relationship. Flush the aggregate first so PostgreSQL
        # can enforce the FK while both writes remain in the same transaction.
        db.flush()
        outbox_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/rollout-outbox/{execution_id}",
            )
        )
        db.add(
            RolloutOutboxRecord(
                id=outbox_id,
                event_type=ROLLOUT_OUTBOX_EVENT,
                aggregate_id=execution_id,
                payload_json={
                    "stage_execution_id": execution_id,
                    "campaign_id": str(campaign.id),
                    "stage_attempt_id": str(attempt.id),
                    "selection_manifest_digest": selection_digest,
                    "snapshot_set_digest": snapshot_set_digest,
                    "execution_mode": execution_mode,
                },
                status="pending",
                attempts=0,
                available_at=now,
                created_at=now,
            )
        )
        db.flush()
        return execution.to_dict()

    @staticmethod
    def _implementation_commands(
        db: Any,
        *,
        selected: list[dict[str, Any]],
    ) -> dict[str, tuple[str, ...]]:
        implementation_ids = {
            implementation_version_id
            for item in selected
            for implementation_version_id in (
                _target_implementation_version_id(item, "baseline"),
                _target_implementation_version_id(item, "candidate"),
            )
            if implementation_version_id
        }
        implementations = (
            db.query(CollectionImplementationVersionRecord)
            .filter(
                CollectionImplementationVersionRecord.id.in_(
                    sorted(implementation_ids)
                )
            )
            .all()
        )
        implementation_by_id = {
            str(item.id): item for item in implementations
        }
        if set(implementation_by_id) != implementation_ids:
            raise RolloutStageExecutionError(
                "implementation_missing",
                "one or more frozen collection implementations are missing",
            )
        result: dict[str, tuple[str, ...]] = {}
        for implementation_id, implementation in (
            implementation_by_id.items()
        ):
            primary = primary_collection_commands(
                (
                    CollectionCommand(
                        command=str(
                            dict(command).get("command")
                            if isinstance(command, Mapping)
                            else command
                        ),
                        priority=(
                            int(dict(command).get("priority") or 0)
                            if isinstance(command, Mapping)
                            else 0
                        ),
                    )
                    for command in list(implementation.commands_json or [])
                ),
                implementation_id=str(implementation.implementation_id),
                version_id=str(implementation.id),
            )
            commands = tuple(
                item
                for item in (
                    ParserPolicy._basic_normalize(command.command)
                    for command in primary
                )
                if item
            )
            if not commands:
                raise RolloutStageExecutionError(
                    "implementation_commands_missing",
                    "frozen collection implementation has no commands: "
                    f"{implementation_id}",
                )
            result[implementation_id] = tuple(
                dict.fromkeys(commands)
            )
        return result

    def _select_snapshots(
        self,
        db: Any,
        *,
        selected: list[dict[str, Any]],
        baseline_window: Mapping[str, Any],
        commands_by_implementation: Mapping[str, tuple[str, ...]]
        | None = None,
    ) -> tuple[dict[str, dict[str, Any]], list[CommandSnapshot]]:
        try:
            start = datetime.fromisoformat(
                str(baseline_window.get("start_at") or "").replace(
                    "Z",
                    "+00:00",
                )
            )
            end = datetime.fromisoformat(
                str(baseline_window.get("end_at") or "").replace(
                    "Z",
                    "+00:00",
                )
            )
        except ValueError as exc:
            raise RolloutStageExecutionError(
                "baseline_window_invalid",
                "frozen rollout baseline window is invalid",
            ) from exc
        start, end = _utc(start), _utc(end)
        commands_by_implementation = (
            dict(commands_by_implementation)
            if commands_by_implementation is not None
            else self._implementation_commands(db, selected=selected)
        )
        selected_rows: list[CommandSnapshot] = []
        bindings: dict[str, dict[str, Any]] = {}
        blockers: list[str] = []
        for target in selected:
            commands_by_track = _target_commands_by_track(
                target,
                commands_by_implementation,
            )
            snapshot_ids_by_track: dict[str, list[str]] = {}
            periods_by_track: dict[str, list[dict[str, Any]]] = {}
            chosen: list[CommandSnapshot] = []
            target_blocked = False
            for track in ("baseline", "candidate"):
                ordered_commands = commands_by_track[track]
                if not ordered_commands:
                    snapshot_ids_by_track[track] = []
                    periods_by_track[track] = []
                    continue
                candidates = (
                    db.query(CommandSnapshot)
                    .filter(
                        or_(
                            CommandSnapshot.inventory_device_id
                            == str(target["device_id"]),
                            CommandSnapshot.device_id
                            == str(target["device_id"]),
                        ),
                        CommandSnapshot.observed_at >= start,
                        CommandSnapshot.observed_at <= end,
                        CommandSnapshot.canonical_command.in_(
                            ordered_commands
                        ),
                    )
                    .order_by(
                        CommandSnapshot.observed_at,
                        CommandSnapshot.created_at,
                        CommandSnapshot.id,
                    )
                    .all()
                )
                by_run: dict[str, dict[str, CommandSnapshot]] = {}
                for snapshot in candidates:
                    command = ParserPolicy._basic_normalize(
                        str(snapshot.canonical_command)
                    )
                    run_rows = by_run.setdefault(str(snapshot.run_id), {})
                    existing = run_rows.get(command)
                    if existing is None or (
                        _utc(snapshot.observed_at),
                        str(snapshot.id),
                    ) > (
                        _utc(existing.observed_at),
                        str(existing.id),
                    ):
                        run_rows[command] = snapshot
                complete_periods: list[dict[str, Any]] = []
                for run_id, run_rows in by_run.items():
                    if any(
                        command not in run_rows
                        for command in ordered_commands
                    ):
                        continue
                    rows = [run_rows[command] for command in ordered_commands]
                    complete_periods.append(
                        {
                            "period_key": run_id,
                            "observed_at": max(
                                _utc(item.observed_at) for item in rows
                            ),
                            "snapshots": rows,
                        }
                    )
                complete_periods.sort(
                    key=lambda item: (
                        item["observed_at"],
                        item["period_key"],
                    )
                )
                chosen_periods = complete_periods[-2:]
                if not chosen_periods:
                    blockers.append(
                        "no complete immutable raw Snapshot period in the "
                        f"frozen window for {track} "
                        f"{target['device_id']}/"
                        f"{target['check_item_version_id']}"
                    )
                    target_blocked = True
                    break
                track_chosen = [
                    snapshot
                    for period in chosen_periods
                    for snapshot in period["snapshots"]
                ]
                snapshot_ids_by_track[track] = [
                    str(item.id) for item in track_chosen
                ]
                periods_by_track[track] = [
                    {
                        "period_key": str(period["period_key"]),
                        "observed_at": _iso(period["observed_at"]),
                        "snapshot_ids": [
                            str(item.id)
                            for item in period["snapshots"]
                        ],
                    }
                    for period in chosen_periods
                ]
                chosen.extend(track_chosen)
            if target_blocked:
                continue
            unique_chosen = {
                str(item.id): item for item in chosen
            }
            chosen = list(unique_chosen.values())
            bindings[str(target["target_key"])] = {
                "snapshot_ids": [
                    str(item.id) for item in chosen
                ],
                "periods": list(periods_by_track["candidate"]),
                "snapshot_ids_by_track": snapshot_ids_by_track,
                "periods_by_track": periods_by_track,
            }
            selected_rows.extend(chosen)
        if blockers:
            raise RolloutStageExecutionError(
                "source_evidence_unavailable",
                "; ".join(blockers),
            )
        unique = {
            str(item.id): item for item in selected_rows
        }
        rows = sorted(
            unique.values(),
            key=lambda item: (
                _utc(item.observed_at),
                str(item.id),
            ),
        )
        return bindings, rows

    def get(self, execution_id: str) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = db.get(RolloutStageExecutionRecord, execution_id)
            if row is None:
                return None
            projections = (
                db.query(RolloutProjectionRecord)
                .filter(
                    RolloutProjectionRecord.stage_execution_id
                    == execution_id
                )
                .order_by(
                    RolloutProjectionRecord.target_key,
                    RolloutProjectionRecord.track,
                )
                .all()
            )
            evidence = (
                db.get(
                    RolloutComparisonEvidenceRecord,
                    row.comparison_manifest_id,
                )
                if row.comparison_manifest_id
                else None
            )
            return {
                **row.to_dict(),
                "projections": [item.to_dict() for item in projections],
                "comparison_evidence": (
                    evidence.to_dict() if evidence is not None else None
                ),
            }

    def list_for_campaign(
        self,
        campaign_id: str,
    ) -> list[dict[str, Any]]:
        with self._session_scope() as db:
            rows = (
                db.query(RolloutStageExecutionRecord)
                .filter(
                    RolloutStageExecutionRecord.campaign_id == campaign_id
                )
                .order_by(
                    RolloutStageExecutionRecord.created_at,
                    RolloutStageExecutionRecord.id,
                )
                .all()
            )
            return [item.to_dict() for item in rows]

    def execute(
        self,
        execution_id: str,
        *,
        worker_owner: str,
        worker_task_id: str | None = None,
        stale_after_seconds: int = 300,
    ) -> dict[str, Any]:
        from src.apps.rule_governance.backend.access import authorize
        record = self.get(execution_id) or {}
        authorize((record.get("selection_manifest_json") or {}).get("execution_user_id"))
        claim = self._claim(
            execution_id,
            worker_owner=worker_owner,
            worker_task_id=worker_task_id,
            stale_after_seconds=stale_after_seconds,
        )
        if claim.get("terminal"):
            return self._finalize_completed_execution(execution_id)
        if claim.get("already_running"):
            return claim
        token = int(claim["fencing_token"])
        try:
            if (
                str(claim.get("execution_mode") or "")
                == "live_capture"
                and not claim.get("capture_manifest_digest")
            ):
                return self._start_live_capture(
                    execution_id,
                    fencing_token=token,
                )
            detached = self._load_execution_context(execution_id)
            snapshots_by_id = {
                str(item["id"]): item
                for item in detached["snapshots"]
            }
            projection_ids: list[str] = []
            for target in detached["selection_manifest"]["targets"]:
                baseline_route = dict(target["baseline_route"])
                candidate_route = {
                    "mode": "runtime_bundle",
                    "runtime_bundle_version_id": str(
                        target[
                            "candidate_runtime_bundle_version_id"
                        ]
                    ),
                    "implementation_version_id": (
                        _target_implementation_version_id(
                            target,
                            "candidate",
                        )
                    ),
                }
                for track, route in (
                    ("baseline", baseline_route),
                    ("candidate", candidate_route),
                ):
                    snapshot_ids = [
                        str(item)
                        for item in dict(
                            target.get("snapshot_ids_by_track") or {}
                        ).get(track, target.get("snapshot_ids") or [])
                    ]
                    snapshots = [
                        snapshots_by_id[item] for item in snapshot_ids
                    ]
                    periods = [
                        dict(item)
                        for item in dict(
                            target.get("periods_by_track") or {}
                        ).get(track, target.get("periods") or [])
                    ]
                    projection = self._run_projection(
                        execution_id=execution_id,
                        fencing_token=token,
                        track=track,
                        target=target,
                        snapshots=snapshots,
                        periods=periods,
                        route=route,
                    )
                    projection_ids.append(str(projection["id"]))
                    self._heartbeat(execution_id, token)
            self._mark_evaluating(execution_id, token)
            result = self._evaluate_and_persist(
                execution_id,
                fencing_token=token,
                projection_ids=projection_ids,
            )
            return result
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, RolloutStageExecutionError)
                else "rollout_stage_execution_failed"
            )
            self._mark_failed(
                execution_id,
                fencing_token=token,
                failure_code=code,
                error=str(exc),
            )
            raise

    def _start_live_capture(
        self,
        execution_id: str,
        *,
        fencing_token: int,
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            row = self._assert_fence(db, execution_id, fencing_token)
            if row.execution_mode != "live_capture":
                raise RolloutStageExecutionError(
                    "execution_mode_invalid",
                    "only live_capture executions can start capture",
                )
            selection_manifest = dict(
                row.selection_manifest_json or {}
            )
        try:
            capture = self._capture_adapter.start(
                execution_id=execution_id,
                selection_manifest=selection_manifest,
            )
        except RolloutCaptureError as exc:
            raise RolloutStageExecutionError(
                exc.code,
                str(exc),
            ) from exc
        capture_batch_id = str(
            capture.get("capture_batch_id") or ""
        ).strip()
        if not capture_batch_id:
            raise RolloutStageExecutionError(
                "capture_batch_missing",
                "live-capture adapter did not return a batch id",
            )
        with self._session_scope() as db:
            row = self._assert_fence(db, execution_id, fencing_token)
            row.status = "capturing"
            row.capture_batch_id = capture_batch_id
            row.capture_requested_at = row.capture_requested_at or _utcnow()
            row.heartbeat_at = _utcnow()
            row.updated_at = _utcnow()
            db.flush()
            return {
                **row.to_dict(),
                "capture": dict(capture),
                "awaiting_capture": True,
            }

    def pending_live_capture_ids(
        self,
        *,
        limit: int = 100,
    ) -> list[str]:
        with self._session_scope() as db:
            return [
                str(value)
                for (value,) in db.query(RolloutStageExecutionRecord.id)
                .filter(
                    RolloutStageExecutionRecord.execution_mode
                    == "live_capture",
                    RolloutStageExecutionRecord.status == "capturing",
                )
                .order_by(
                    RolloutStageExecutionRecord.capture_requested_at,
                    RolloutStageExecutionRecord.id,
                )
                .limit(max(1, min(int(limit), 500)))
                .all()
            ]

    def reconcile_live_capture(
        self,
        execution_id: str,
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            row = db.get(RolloutStageExecutionRecord, execution_id)
            if row is None:
                raise RolloutStageExecutionError(
                    "not_found",
                    f"rollout stage execution not found: {execution_id}",
                )
            if row.status != "capturing":
                return {
                    **row.to_dict(),
                    "reconciled": False,
                }
            capture_batch_id = str(row.capture_batch_id or "")
            selection_manifest = dict(
                row.selection_manifest_json or {}
            )
        from src.apps.rule_governance.backend.access import authorize
        authorize(selection_manifest.get("execution_user_id"))
        try:
            capture = self._capture_adapter.poll(
                capture_batch_id=capture_batch_id,
                selection_manifest=selection_manifest,
            )
        except RolloutCaptureError as exc:
            capture = {
                "status": "failed",
                "failure_code": exc.code,
                "error": str(exc),
            }
        status = str(capture.get("status") or "")
        if status == "capturing":
            with self._session_scope() as db:
                row = (
                    db.query(RolloutStageExecutionRecord)
                    .filter(
                        RolloutStageExecutionRecord.id == execution_id,
                        RolloutStageExecutionRecord.status
                        == "capturing",
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if row is None:
                    current = db.get(
                        RolloutStageExecutionRecord,
                        execution_id,
                    )
                    return {
                        **(current.to_dict() if current else {}),
                        "reconciled": False,
                    }
                row.heartbeat_at = _utcnow()
                row.updated_at = _utcnow()
                db.flush()
                return {
                    **row.to_dict(),
                    "capture": dict(capture),
                    "reconciled": True,
                }
        if status == "failed":
            with self._session_scope() as db:
                row = (
                    db.query(RolloutStageExecutionRecord)
                    .filter(
                        RolloutStageExecutionRecord.id == execution_id,
                        RolloutStageExecutionRecord.status
                        == "capturing",
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if row is not None:
                    row.status = "failed"
                    row.failure_code = str(
                        capture.get("failure_code")
                        or "capture_failed"
                    )[:128]
                    row.error = str(
                        capture.get("error")
                        or "live capture failed"
                    )[:4000]
                    row.completed_at = _utcnow()
                    row.updated_at = _utcnow()
                    db.flush()
                    return {
                        **row.to_dict(),
                        "capture": dict(capture),
                        "reconciled": True,
                    }
            return {"reconciled": False, "status": "failed"}
        if status != "completed":
            raise RolloutStageExecutionError(
                "capture_result_invalid",
                "live-capture adapter returned an invalid status",
            )

        capture_manifest = {
            "schema_version": "rollout-live-capture/v1",
            "capture_batch_id": capture_batch_id,
            "selection_basis_digest": str(
                selection_manifest.get("selection_basis_digest") or ""
            ),
            "snapshot_bindings": dict(
                capture.get("snapshot_bindings") or {}
            ),
            "snapshot_ids": sorted(
                {
                    str(item)
                    for item in capture.get("snapshot_ids") or []
                    if str(item)
                }
            ),
            "snapshot_run_ids": sorted(
                {
                    str(item)
                    for item in capture.get("snapshot_run_ids") or []
                    if str(item)
                }
            ),
            "workflow_run_ids": sorted(
                {
                    str(item)
                    for item in capture.get("workflow_run_ids") or []
                    if str(item)
                }
            ),
        }
        if not capture_manifest["snapshot_ids"]:
            raise RolloutStageExecutionError(
                "capture_evidence_incomplete",
                "completed live capture contains no raw Snapshots",
            )
        with self._session_scope() as db:
            snapshots = (
                db.query(CommandSnapshot)
                .filter(
                    CommandSnapshot.id.in_(
                        capture_manifest["snapshot_ids"]
                    )
                )
                .all()
            )
            if len(snapshots) != len(capture_manifest["snapshot_ids"]):
                raise RolloutStageExecutionError(
                    "capture_snapshot_missing",
                    "one or more captured raw Snapshots are missing",
                )
            snapshot_manifest = sorted(
                [
                    {
                        "id": str(item.id),
                        "raw_text_hash": str(item.raw_text_hash),
                        "observed_at": _iso(item.observed_at),
                        "created_at": _iso(item.created_at),
                    }
                    for item in snapshots
                ],
                key=lambda item: item["id"],
            )
            snapshot_set_digest = _digest(snapshot_manifest)
            capture_manifest_digest = _digest(capture_manifest)
            row = (
                db.query(RolloutStageExecutionRecord)
                .filter(
                    RolloutStageExecutionRecord.id == execution_id,
                    RolloutStageExecutionRecord.status == "capturing",
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                current = db.get(RolloutStageExecutionRecord, execution_id)
                return {
                    **(current.to_dict() if current else {}),
                    "reconciled": False,
                }
            row.capture_manifest_json = capture_manifest
            row.capture_manifest_digest = capture_manifest_digest
            row.capture_completed_at = _utcnow()
            row.source_snapshot_ids_json = capture_manifest["snapshot_ids"]
            row.snapshot_set_digest = snapshot_set_digest
            row.fact_high_watermark = max(
                _utc(item.created_at or item.observed_at)
                for item in snapshots
            )
            row.status = "queued"
            row.worker_owner = None
            row.worker_task_id = None
            row.heartbeat_at = None
            row.updated_at = _utcnow()
            outbox_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "netops/rollout-outbox/capture-completed/"
                    f"{execution_id}/{capture_manifest_digest}",
                )
            )
            if db.get(RolloutOutboxRecord, outbox_id) is None:
                db.add(
                    RolloutOutboxRecord(
                        id=outbox_id,
                        event_type=ROLLOUT_OUTBOX_EVENT,
                        aggregate_id=execution_id,
                        payload_json={
                            "stage_execution_id": execution_id,
                            "capture_batch_id": capture_batch_id,
                            "capture_manifest_digest": (
                                capture_manifest_digest
                            ),
                            "snapshot_set_digest": snapshot_set_digest,
                        },
                        status="pending",
                        attempts=0,
                        available_at=_utcnow(),
                        created_at=_utcnow(),
                    )
                )
            db.flush()
            return {
                **row.to_dict(),
                "capture": dict(capture),
                "reconciled": True,
                "projection_queued": True,
            }

    def _claim(
        self,
        execution_id: str,
        *,
        worker_owner: str,
        worker_task_id: str | None,
        stale_after_seconds: int,
    ) -> dict[str, Any]:
        now = _utcnow()
        with self._session_scope() as db:
            row = (
                db.query(RolloutStageExecutionRecord)
                .filter(RolloutStageExecutionRecord.id == execution_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise RolloutStageExecutionError(
                    "not_found",
                    f"rollout stage execution not found: {execution_id}",
                )
            if row.status == "completed":
                return {
                    **row.to_dict(),
                    "terminal": True,
                }
            if row.status == "cancelled":
                raise RolloutStageExecutionError(
                    "cancelled",
                    "rollout stage execution was cancelled",
                )
            if row.status == "capturing":
                return {
                    **row.to_dict(),
                    "terminal": False,
                    "already_running": True,
                    "awaiting_capture": True,
                }
            stale_before = now - timedelta(
                seconds=max(30, int(stale_after_seconds))
            )
            if (
                row.status in {"running", "evaluating"}
                and row.heartbeat_at is not None
                and _utc(row.heartbeat_at) >= stale_before
            ):
                return {
                    **row.to_dict(),
                    "terminal": False,
                    "already_running": True,
                }
            row.status = "running"
            row.fencing_token = int(row.fencing_token or 0) + 1
            row.attempt_count = int(row.attempt_count or 0) + 1
            row.worker_owner = str(worker_owner or "unknown")
            row.worker_task_id = str(worker_task_id or "") or None
            row.heartbeat_at = now
            row.started_at = row.started_at or now
            row.updated_at = now
            row.failure_code = None
            row.error = None
            db.flush()
            return {
                **row.to_dict(),
                "terminal": False,
                "already_running": False,
            }

    def _load_execution_context(
        self,
        execution_id: str,
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            execution = db.get(RolloutStageExecutionRecord, execution_id)
            if execution is None:
                raise RolloutStageExecutionError(
                    "not_found",
                    "rollout stage execution disappeared",
                )
            campaign = db.get(
                RolloutCampaignRecord,
                execution.campaign_id,
            )
            attempt = db.get(
                RolloutStageAttemptRecord,
                execution.stage_attempt_id,
            )
            if campaign is None or attempt is None:
                raise RolloutStageExecutionError(
                    "frozen_control_state_missing",
                    "rollout campaign or stage attempt is missing",
                )
            snapshots = (
                db.query(CommandSnapshot)
                .filter(
                    CommandSnapshot.id.in_(
                        list(execution.source_snapshot_ids_json or [])
                    )
                )
                .all()
            )
            if len(snapshots) != len(
                set(execution.source_snapshot_ids_json or [])
            ):
                raise RolloutStageExecutionError(
                    "source_snapshot_missing",
                    "one or more frozen raw Snapshots are missing",
                )
            snapshot_payloads = [
                {
                    **item.to_dict(),
                    "family": (
                        db.get(NetworkDevice, item.inventory_device_id).family
                        if item.inventory_device_id
                        and db.get(NetworkDevice, item.inventory_device_id)
                        is not None
                        else None
                    ),
                }
                for item in snapshots
            ]
            selection_manifest = dict(
                execution.selection_manifest_json or {}
            )
            if execution.execution_mode == "live_capture":
                capture_manifest = dict(
                    execution.capture_manifest_json or {}
                )
                bindings = dict(
                    capture_manifest.get("snapshot_bindings") or {}
                )
                if not bindings:
                    raise RolloutStageExecutionError(
                        "capture_evidence_incomplete",
                        "live capture has no frozen Snapshot bindings",
                    )
                targets = []
                for target in selection_manifest.get("targets") or []:
                    target = dict(target)
                    binding = dict(
                        bindings.get(str(target.get("target_key") or ""))
                        or {}
                    )
                    if not binding:
                        raise RolloutStageExecutionError(
                            "capture_evidence_incomplete",
                            "live capture is missing a frozen target binding",
                        )
                    targets.append(
                        {
                            **target,
                            "snapshot_ids": list(
                                binding.get("snapshot_ids") or []
                            ),
                            "periods": list(
                                binding.get("periods") or []
                            ),
                            "snapshot_ids_by_track": dict(
                                binding.get("snapshot_ids_by_track") or {}
                            ),
                            "periods_by_track": dict(
                                binding.get("periods_by_track") or {}
                            ),
                        }
                    )
                selection_manifest = {
                    **selection_manifest,
                    "targets": targets,
                    "capture_manifest_digest": str(
                        execution.capture_manifest_digest or ""
                    ),
                }
            return {
                "execution": execution.to_dict(),
                "campaign": campaign.to_dict(),
                "attempt": attempt.to_dict(),
                "selection_manifest": selection_manifest,
                "snapshots": snapshot_payloads,
            }

    def _run_projection(
        self,
        *,
        execution_id: str,
        fencing_token: int,
        track: str,
        target: Mapping[str, Any],
        snapshots: list[dict[str, Any]],
        periods: list[dict[str, Any]],
        route: Mapping[str, Any],
    ) -> dict[str, Any]:
        target_key = str(target["target_key"])
        projection_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/rollout-projection/{execution_id}/{track}/"
                f"{target_key}",
            )
        )
        snapshot_ids = [str(item["id"]) for item in snapshots]
        snapshot_set_digest = _digest(
            [
                {
                    "id": str(item["id"]),
                    "raw_text_hash": str(item["raw_text_hash"]),
                }
                for item in snapshots
            ]
        )
        with self._session_scope() as db:
            self._assert_fence(db, execution_id, fencing_token)
            existing = db.get(RolloutProjectionRecord, projection_id)
            if (
                existing is not None
                and existing.status == "completed"
                and str(existing.snapshot_set_digest)
                == snapshot_set_digest
            ):
                return existing.to_dict()
            if existing is None:
                existing = RolloutProjectionRecord(
                    id=projection_id,
                    stage_execution_id=execution_id,
                    track=track,
                    target_key=target_key,
                    route_json=dict(route),
                    runtime_bundle_version_id=(
                        str(route.get("runtime_bundle_version_id") or "")
                        or None
                    ),
                    snapshot_ids_json=snapshot_ids,
                    snapshot_set_digest=snapshot_set_digest,
                    status="running",
                    created_at=_utcnow(),
                )
                db.add(existing)
            else:
                existing.status = "running"
                existing.failure_code = None
                existing.error = None
            db.flush()
        try:
            facts = self._projection_engine.project(
                execution_id=execution_id,
                track=track,
                target=target,
                snapshots=snapshots,
                periods=periods,
                route=route,
            )
            facts_digest = _digest(facts)
        except Exception as exc:
            with self._session_scope() as db:
                self._assert_fence(db, execution_id, fencing_token)
                row = db.get(RolloutProjectionRecord, projection_id)
                if row is not None:
                    row.status = "failed"
                    row.failure_code = (
                        exc.code
                        if isinstance(exc, RolloutStageExecutionError)
                        else "projection_failed"
                    )
                    row.error = str(exc)[:4000]
                    row.completed_at = _utcnow()
            raise
        with self._session_scope() as db:
            self._assert_fence(db, execution_id, fencing_token)
            row = db.get(RolloutProjectionRecord, projection_id)
            if row is None:
                raise RolloutStageExecutionError(
                    "projection_missing",
                    "rollout projection disappeared before commit",
                )
            row.facts_json = facts
            row.facts_digest = facts_digest
            row.status = "completed"
            row.completed_at = _utcnow()
            db.flush()
            return row.to_dict()

    def _evaluate_and_persist(
        self,
        execution_id: str,
        *,
        fencing_token: int,
        projection_ids: list[str],
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            self._assert_fence(db, execution_id, fencing_token)
            execution = db.get(RolloutStageExecutionRecord, execution_id)
            campaign = db.get(
                RolloutCampaignRecord,
                execution.campaign_id,
            )
            attempt = db.get(
                RolloutStageAttemptRecord,
                execution.stage_attempt_id,
            )
            projections = (
                db.query(RolloutProjectionRecord)
                .filter(RolloutProjectionRecord.id.in_(projection_ids))
                .order_by(
                    RolloutProjectionRecord.target_key,
                    RolloutProjectionRecord.track,
                )
                .all()
            )
            if (
                campaign is None
                or attempt is None
                or len(projections) != len(set(projection_ids))
            ):
                raise RolloutStageExecutionError(
                    "comparison_input_missing",
                    "rollout comparison inputs are incomplete",
                )
            candidate_bundle_ids = {
                str(item.get("version_id") or "")
                for item in list(campaign.runtime_bundle_refs_json or [])
                if str(item.get("role") or "") == "candidate"
                and str(item.get("version_id") or "")
            }
            target_pairs = {
                (
                    str(item.get("device_id") or ""),
                    str(item.get("check_item_version_id") or ""),
                )
                for item in list(
                    dict(campaign.target_manifest_json or {}).get("matrix") or []
                )
            }
            feedback_query = db.query(ProductionFeedbackRecord).filter(
                ProductionFeedbackRecord.triage_outcome == "confirmed"
            )
            if candidate_bundle_ids:
                feedback_query = feedback_query.filter(
                    ProductionFeedbackRecord.runtime_bundle_version_id.in_(
                        candidate_bundle_ids
                    )
                )
            else:
                feedback_query = feedback_query.filter(False)
            if attempt.started_at is not None:
                feedback_query = feedback_query.filter(
                    ProductionFeedbackRecord.created_at >= attempt.started_at
                )
            feedback_rows = [
                row
                for row in feedback_query.order_by(
                    ProductionFeedbackRecord.created_at,
                    ProductionFeedbackRecord.id,
                ).all()
                if (str(row.device_id), str(row.check_item_version_id))
                in target_pairs
            ]
            detached = {
                "execution": execution.to_dict(),
                "campaign": campaign.to_dict(),
                "attempt": attempt.to_dict(),
                "projections": [item.to_dict() for item in projections],
                "feedback": [
                    {
                        "id": str(row.id),
                        "feedback_type": str(row.feedback_type),
                        "triage_outcome": str(row.triage_outcome),
                        "execution_id": str(row.execution_id),
                        "assessment_id": str(row.assessment_id),
                        "device_id": str(row.device_id),
                        "check_item_version_id": str(row.check_item_version_id),
                        "runtime_bundle_version_id": str(
                            row.runtime_bundle_version_id
                        ),
                        "runtime_bundle_digest": str(row.runtime_bundle_digest),
                        "evidence_digest": str(row.evidence_digest),
                        "source_context_digest": _digest(row.source_context_json),
                        "created_at": row.created_at,
                        "triaged_at": row.triaged_at,
                    }
                    for row in feedback_rows
                ],
            }

        manifest, metrics, comparisons = self._evaluate(detached)
        manifest_digest = _digest(manifest)
        content = json.dumps(
            {
                **manifest,
                "manifest_digest": manifest_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=str,
        ).encode("utf-8")
        artifact_sha256 = hashlib.sha256(content).hexdigest()
        object_key = (
            f"rollout/evidence/{detached['campaign']['id']}/"
            f"{detached['attempt']['id']}/{manifest_digest}.json"
        )
        storage = self._object_storage
        if storage is None:
            from src.infrastructure.storage.minio_client import (
                get_minio_storage,
            )

            storage = get_minio_storage()
        if not storage.upload_file(
            object_key,
            content,
            content_type="application/json",
        ):
            raise RolloutStageExecutionError(
                "comparison_evidence_upload_failed",
                "failed to persist immutable rollout comparison evidence",
            )

        evidence_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/rollout-comparison-evidence/{manifest_digest}",
            )
        )
        with self._session_scope() as db:
            self._assert_fence(db, execution_id, fencing_token)
            execution = db.get(RolloutStageExecutionRecord, execution_id)
            existing = (
                db.query(RolloutComparisonEvidenceRecord)
                .filter(
                    RolloutComparisonEvidenceRecord.stage_execution_id
                    == execution_id
                )
                .one_or_none()
            )
            if existing is None:
                existing = RolloutComparisonEvidenceRecord(
                    id=evidence_id,
                    stage_execution_id=execution_id,
                    campaign_id=str(execution.campaign_id),
                    stage_attempt_id=str(execution.stage_attempt_id),
                    schema_version=ROLLOUT_EVIDENCE_SCHEMA,
                    manifest_json=manifest,
                    manifest_digest=manifest_digest,
                    artifact_object_key=object_key,
                    artifact_sha256=artifact_sha256,
                    fact_high_watermark=execution.fact_high_watermark,
                    created_at=_utcnow(),
                )
                db.add(existing)
            elif (
                str(existing.manifest_digest) != manifest_digest
                or str(existing.artifact_sha256) != artifact_sha256
            ):
                raise RolloutStageExecutionError(
                    "comparison_evidence_conflict",
                    "stage execution already has different immutable evidence",
                )
            baseline = [
                str(item.get("facts_digest") or "")
                for item in detached["projections"]
                if item["track"] == "baseline"
            ]
            candidate = [
                str(item.get("facts_digest") or "")
                for item in detached["projections"]
                if item["track"] == "candidate"
            ]
            execution.baseline_projection_digest = _digest(baseline)
            execution.candidate_projection_digest = _digest(candidate)
            execution.comparison_manifest_id = evidence_id
            execution.decision_digest = _digest(
                {
                    "metrics": metrics,
                    "comparisons": comparisons,
                    "manifest_digest": manifest_digest,
                }
            )
            execution.status = "completed"
            execution.completed_at = _utcnow()
            execution.heartbeat_at = execution.completed_at
            execution.updated_at = execution.completed_at
            db.flush()

        return self._finalize_completed_execution(execution_id)

    def _finalize_completed_execution(
        self,
        execution_id: str,
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            execution = db.get(RolloutStageExecutionRecord, execution_id)
            if (
                execution is None
                or execution.status != "completed"
                or not execution.comparison_manifest_id
            ):
                raise RolloutStageExecutionError(
                    "trusted_evidence_incomplete",
                    "completed rollout execution evidence is unavailable",
                )
            campaign = db.get(
                RolloutCampaignRecord,
                execution.campaign_id,
            )
            evidence = db.get(
                RolloutComparisonEvidenceRecord,
                execution.comparison_manifest_id,
            )
            if campaign is None or evidence is None:
                raise RolloutStageExecutionError(
                    "trusted_evidence_incomplete",
                    "rollout campaign or comparison evidence is unavailable",
                )
            detached = {
                "execution": execution.to_dict(),
                "campaign": campaign.to_dict(),
                "evidence": evidence.to_dict(),
            }
        manifest = dict(detached["evidence"]["manifest_json"] or {})
        metrics = dict(manifest.get("metrics") or {})
        comparisons = dict(manifest.get("comparisons") or {})
        from src.apps.rule_governance.backend.check_items.rollout import RolloutCampaignService

        campaign_result = RolloutCampaignService(
            self._session_scope,
            stage_execution_preparer=None,
        ).record_stage_result(
            str(detached["campaign"]["id"]),
            metrics=metrics,
            comparisons=comparisons,
            evidence_refs=[
                {
                    "type": "rollout_comparison_evidence",
                    "id": str(detached["evidence"]["id"]),
                    "object_key": str(
                        detached["evidence"]["artifact_object_key"]
                    ),
                    "sha256": str(
                        detached["evidence"]["artifact_sha256"]
                    ),
                }
            ],
            observed_digests={
                "target_manifest_digest": str(
                    detached["campaign"]["target_manifest_digest"]
                ),
                "runtime_bundle_digest": str(
                    detached["campaign"]["runtime_bundle_digest"]
                ),
                "comparison_context_digest": str(
                    detached["campaign"]["comparison_context_digest"]
                ),
            },
            actor="system:rollout-evaluator",
            reason=(
                "trusted evaluator derived metrics from persisted "
                "baseline/candidate facts"
            ),
            idempotency_key=f"evaluate-{execution_id}",
            trusted=True,
        )
        return {
            "success": True,
            "stage_execution_id": execution_id,
            "comparison_evidence_id": str(detached["evidence"]["id"]),
            "manifest_digest": str(
                detached["evidence"]["manifest_digest"]
            ),
            "artifact_object_key": str(
                detached["evidence"]["artifact_object_key"]
            ),
            "artifact_sha256": str(
                detached["evidence"]["artifact_sha256"]
            ),
            "metrics": metrics,
            "comparisons": comparisons,
            "campaign": campaign_result,
        }

    @staticmethod
    def _evaluate(
        detached: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        projections = list(detached["projections"])
        by_target: dict[str, dict[str, dict[str, Any]]] = {}
        for row in projections:
            by_target.setdefault(str(row["target_key"]), {})[
                str(row["track"])
            ] = dict(row)
        comparison_rows: list[dict[str, Any]] = []
        domains = (
            "plan",
            "observation",
            "finding",
            "assessment",
            "change",
            "event",
            "risk_case",
            "report",
            "identity_binding",
            "output_quality",
            "coverage_basis",
        )
        approved = dict(
            dict(detached["campaign"].get("thresholds_json") or {}).get(
                "approved_difference_digests"
            )
            or {}
        )
        domain_differences: dict[str, list[str]] = {
            domain: [] for domain in domains
        }
        domain_status_counts: dict[str, Counter[str]] = {
            domain: Counter() for domain in domains
        }
        candidate_success = 0
        baseline_success = 0
        candidate_assessed = 0
        baseline_assessed = 0
        candidate_unevaluable = 0
        baseline_unevaluable = 0
        baseline_applicable = 0
        event_collisions = 0
        historical_loss = 0
        unexplained_p0_p1 = 0
        candidate_event_count = 0
        for target_key, tracks in sorted(by_target.items()):
            baseline_row = tracks.get("baseline")
            candidate_row = tracks.get("candidate")
            if baseline_row is None or candidate_row is None:
                raise RolloutStageExecutionError(
                    "projection_pair_missing",
                    f"baseline/candidate projection pair missing: {target_key}",
                )
            if (
                str(baseline_row["snapshot_set_digest"])
                != str(candidate_row["snapshot_set_digest"])
            ):
                historical_loss += 1
            baseline = dict(baseline_row.get("facts_json") or {})
            candidate = dict(candidate_row.get("facts_json") or {})
            baseline_not_applicable = bool(
                baseline.get("baseline_not_applicable")
            )
            baseline_assessment = dict(
                baseline.get("assessment") or {}
            )
            candidate_assessment = dict(
                candidate.get("assessment") or {}
            )
            baseline_ok = (
                baseline_assessment.get("parse_success") is True
            )
            candidate_ok = candidate_assessment.get("parse_success") is True
            baseline_applicable += int(not baseline_not_applicable)
            baseline_success += int(
                baseline_ok and not baseline_not_applicable
            )
            candidate_success += int(candidate_ok)
            baseline_completed = (
                baseline_assessment.get("formation_status")
                or baseline_assessment.get("status")
            ) == "completed"
            candidate_completed = (
                candidate_assessment.get("formation_status")
                or candidate_assessment.get("status")
            ) == "completed"
            baseline_is_unevaluable = (
                not baseline_not_applicable
                and (
                    not baseline_completed
                    or baseline_assessment.get("assessment_status")
                    == "unable_to_determine"
                )
            )
            candidate_is_unevaluable = (
                not candidate_completed
                or candidate_assessment.get("assessment_status")
                == "unable_to_determine"
            )
            baseline_assessed += int(
                baseline_completed and not baseline_not_applicable
            )
            candidate_assessed += int(candidate_completed)
            baseline_unevaluable += int(baseline_is_unevaluable)
            candidate_unevaluable += int(candidate_is_unevaluable)
            target_comparisons: dict[str, Any] = {}
            domain_values = {
                "plan": (
                    baseline.get("snapshot_ids"),
                    candidate.get("snapshot_ids"),
                ),
                "observation": (
                    baseline.get("observations"),
                    candidate.get("observations"),
                ),
                "finding": (
                    baseline.get("findings"),
                    candidate.get("findings"),
                ),
                "assessment": (
                    baseline.get("assessment"),
                    candidate.get("assessment"),
                ),
                "change": (
                    baseline.get("changes"),
                    candidate.get("changes"),
                ),
                "event": (
                    baseline.get("events"),
                    candidate.get("events"),
                ),
                "risk_case": (
                    dict(baseline.get("report_projection") or {}).get(
                        "risk_count"
                    ),
                    dict(candidate.get("report_projection") or {}).get(
                        "risk_count"
                    ),
                ),
                "report": (
                    baseline.get("report_projection"),
                    candidate.get("report_projection"),
                ),
                "identity_binding": (
                    baseline.get("identity_bindings"),
                    candidate.get("identity_bindings"),
                ),
                "output_quality": (
                    [
                        {
                            key: item.get(key)
                            for key in (
                                "snapshot_id",
                                "raw_text_hash",
                                "canonical_command",
                                "parser_status",
                                "failure_code",
                                "output_status",
                                "record_count",
                            )
                        }
                        for item in baseline.get("parse_results") or []
                    ],
                    [
                        {
                            key: item.get(key)
                            for key in (
                                "snapshot_id",
                                "raw_text_hash",
                                "canonical_command",
                                "parser_status",
                                "failure_code",
                                "output_status",
                                "record_count",
                            )
                        }
                        for item in candidate.get("parse_results") or []
                    ],
                ),
                "coverage_basis": (
                    {
                        "observed_entity_count": baseline_assessment.get(
                            "observed_entity_count"
                        ),
                        "observation_count": baseline_assessment.get(
                            "observation_count"
                        ),
                    },
                    {
                        "observed_entity_count": candidate_assessment.get(
                            "observed_entity_count"
                        ),
                        "observation_count": candidate_assessment.get(
                            "observation_count"
                        ),
                    },
                ),
            }
            for domain, (before, after) in domain_values.items():
                difference_digest = _digest(
                    {
                        "target_key": target_key,
                        "domain": domain,
                        "baseline": before,
                        "candidate": after,
                    }
                )
                if baseline_not_applicable:
                    status = "not_applicable"
                elif _digest(before) == _digest(after):
                    status = "matched"
                elif difference_digest in {
                    str(item) for item in approved.get(domain) or []
                }:
                    status = "expected_change"
                else:
                    status = "unexplained"
                    domain_differences[domain].append(difference_digest)
                target_comparisons[domain] = {
                    "status": status,
                    "difference_digest": difference_digest,
                    "baseline_digest": _digest(before),
                    "candidate_digest": _digest(after),
                }
                domain_status_counts[domain][status] += 1
            candidate_event_signatures = [
                _digest(item) for item in candidate.get("events") or []
            ]
            candidate_event_count += len(candidate_event_signatures)
            event_collisions += sum(
                count - 1
                for count in Counter(candidate_event_signatures).values()
                if count > 1
            )
            if (
                target_comparisons["event"]["status"] == "unexplained"
            ):
                baseline_severe = {
                    _digest(item)
                    for item in baseline.get("events") or []
                    if str(item.get("severity") or "").casefold()
                    in {"critical", "major", "p0", "p1"}
                }
                candidate_severe = {
                    _digest(item)
                    for item in candidate.get("events") or []
                    if str(item.get("severity") or "").casefold()
                    in {"critical", "major", "p0", "p1"}
                }
                unexplained_p0_p1 += len(
                    baseline_severe.symmetric_difference(candidate_severe)
                )
            comparison_rows.append(
                {
                    "target_key": target_key,
                    "snapshot_set_digest": str(
                        candidate_row["snapshot_set_digest"]
                    ),
                    "domains": target_comparisons,
                }
            )

        domain_results: dict[str, Any] = {}
        for domain in domains:
            differences = sorted(domain_differences[domain])
            status_counts = domain_status_counts[domain]
            aggregate_status = (
                "unexplained"
                if differences
                else "expected_change"
                if status_counts["expected_change"]
                else "not_applicable"
                if status_counts["not_applicable"] == len(by_target)
                else "matched"
            )
            domain_results[domain] = {
                "status": aggregate_status,
                "unexplained_difference_count": len(differences),
                "difference_digests": differences,
            }
        total = len(by_target)
        unexplained_events = len(domain_differences["event"])
        feedback_metrics = aggregate_feedback_metrics(
            list(detached.get("feedback") or []),
            candidate_event_count=candidate_event_count,
        )
        metrics = {
            "runtime_bundle_coverage_rate": (
                len(
                    [
                        item
                        for item in projections
                        if item["track"] == "candidate"
                        and item.get("runtime_bundle_version_id")
                    ]
                )
                / total
                if total
                else 0.0
            ),
            "execution_success_rate": (
                candidate_success / total if total else 0.0
            ),
            "baseline_execution_success_rate": (
                baseline_success / baseline_applicable
                if baseline_applicable
                else 0.0
            ),
            "assessment_coverage_rate": (
                candidate_assessed / total if total else 0.0
            ),
            "baseline_assessment_coverage_rate": (
                baseline_assessed / baseline_applicable
                if baseline_applicable
                else 0.0
            ),
            "unevaluable_rate": (
                candidate_unevaluable / total if total else 1.0
            ),
            "baseline_unevaluable_rate": (
                baseline_unevaluable / baseline_applicable
                if baseline_applicable
                else 0.0
            ),
            "unexplained_p0_p1_count": unexplained_p0_p1,
            "historical_evidence_loss_count": historical_loss,
            "unexplained_event_difference_count": unexplained_events,
            "event_collision_count": event_collisions,
            "report_reconciled": all(
                dict(item.get("facts_json") or {})
                .get("report_projection", {})
                .get("status")
                == dict(item.get("facts_json") or {})
                .get("assessment", {})
                .get(
                    "assessment_status",
                    dict(item.get("facts_json") or {})
                    .get("assessment", {})
                    .get("status"),
                )
                for item in projections
            ),
            "baseline_applicable_target_count": baseline_applicable,
            "rollback_verified": bool(
                dict(
                    detached["campaign"].get("rollback_plan_json") or {}
                ).get("verified")
            ),
            **feedback_metrics,
        }
        manifest = {
            "schema_version": ROLLOUT_EVIDENCE_SCHEMA,
            "campaign_id": str(detached["campaign"]["id"]),
            "stage_attempt_id": str(detached["attempt"]["id"]),
            "stage_execution_id": str(detached["execution"]["id"]),
            "stage": str(detached["attempt"]["stage"]),
            "execution_mode": str(
                detached["execution"].get(
                    "execution_mode",
                    "snapshot_replay",
                )
            ),
            "selection_manifest_digest": str(
                detached["execution"]["selection_manifest_digest"]
            ),
            "capture_batch_id": detached["execution"].get(
                "capture_batch_id"
            ),
            "capture_manifest_digest": detached["execution"].get(
                "capture_manifest_digest"
            ),
            "snapshot_set_digest": str(
                detached["execution"]["snapshot_set_digest"]
            ),
            "target_manifest_digest": str(
                detached["campaign"]["target_manifest_digest"]
            ),
            "runtime_bundle_digest": str(
                detached["campaign"]["runtime_bundle_digest"]
            ),
            "comparison_context_digest": str(
                detached["campaign"]["comparison_context_digest"]
            ),
            "fact_high_watermark": _iso(
                detached["execution"]["fact_high_watermark"]
            ),
            "baseline_projection_digest": _digest(
                [
                    str(item.get("facts_digest") or "")
                    for item in projections
                    if item["track"] == "baseline"
                ]
            ),
            "candidate_projection_digest": _digest(
                [
                    str(item.get("facts_digest") or "")
                    for item in projections
                    if item["track"] == "candidate"
                ]
            ),
            "metrics": metrics,
            "comparisons": domain_results,
            "target_comparisons": comparison_rows,
            "projection_refs": [
                {
                    "id": str(item["id"]),
                    "track": str(item["track"]),
                    "target_key": str(item["target_key"]),
                    "facts_digest": str(item.get("facts_digest") or ""),
                }
                for item in projections
            ],
            "production_feedback_refs": [
                {
                    "id": str(item["id"]),
                    "feedback_type": str(item["feedback_type"]),
                    "execution_id": str(item["execution_id"]),
                    "assessment_id": str(item["assessment_id"]),
                    "runtime_bundle_version_id": str(
                        item["runtime_bundle_version_id"]
                    ),
                    "evidence_digest": str(item["evidence_digest"]),
                    "source_context_digest": str(item["source_context_digest"]),
                }
                for item in list(detached.get("feedback") or [])
            ],
            "raw_cli_included": False,
            "generated_at": _utcnow().isoformat(),
        }
        return manifest, metrics, domain_results

    def _heartbeat(
        self,
        execution_id: str,
        fencing_token: int,
    ) -> None:
        with self._session_scope() as db:
            updated = (
                db.query(RolloutStageExecutionRecord)
                .filter(
                    RolloutStageExecutionRecord.id == execution_id,
                    RolloutStageExecutionRecord.fencing_token
                    == int(fencing_token),
                    RolloutStageExecutionRecord.status.in_(
                        ["running", "evaluating"]
                    ),
                )
                .update(
                    {
                        RolloutStageExecutionRecord.heartbeat_at: _utcnow(),
                        RolloutStageExecutionRecord.updated_at: _utcnow(),
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise RolloutStageExecutionError(
                    "worker_fenced",
                    "rollout Worker no longer owns the execution lease",
                )

    def _mark_evaluating(
        self,
        execution_id: str,
        fencing_token: int,
    ) -> None:
        with self._session_scope() as db:
            row = self._assert_fence(db, execution_id, fencing_token)
            row.status = "evaluating"
            row.heartbeat_at = _utcnow()
            row.updated_at = row.heartbeat_at

    def _mark_failed(
        self,
        execution_id: str,
        *,
        fencing_token: int,
        failure_code: str,
        error: str,
    ) -> None:
        with self._session_scope() as db:
            row = (
                db.query(RolloutStageExecutionRecord)
                .filter(
                    RolloutStageExecutionRecord.id == execution_id,
                    RolloutStageExecutionRecord.fencing_token
                    == int(fencing_token),
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None or row.status == "completed":
                return
            row.status = "failed"
            row.failure_code = str(failure_code)[:128]
            row.error = str(error)[:4000]
            row.completed_at = _utcnow()
            row.updated_at = row.completed_at

    def pause_campaign_after_failure(
        self,
        execution_id: str,
        *,
        reason: str,
    ) -> None:
        with self._session_scope() as db:
            execution = db.get(RolloutStageExecutionRecord, execution_id)
            campaign_id = (
                str(execution.campaign_id) if execution is not None else ""
            )
        if not campaign_id:
            return
        from src.apps.rule_governance.backend.check_items.rollout import (
            RolloutCampaignService,
            RolloutValidationError,
        )

        try:
            RolloutCampaignService(
                self._session_scope,
                stage_execution_preparer=None,
            ).pause(
                campaign_id,
                actor="system:rollout-worker",
                reason=str(reason)[:2000],
                idempotency_key=f"worker-failure-{execution_id}",
            )
        except RolloutValidationError:
            return

    @staticmethod
    def _assert_fence(
        db: Any,
        execution_id: str,
        fencing_token: int,
    ) -> RolloutStageExecutionRecord:
        row = (
            db.query(RolloutStageExecutionRecord)
            .filter(
                RolloutStageExecutionRecord.id == execution_id,
                RolloutStageExecutionRecord.fencing_token
                == int(fencing_token),
                RolloutStageExecutionRecord.status.in_(
                    ["running", "evaluating"]
                ),
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            raise RolloutStageExecutionError(
                "worker_fenced",
                "rollout Worker no longer owns the execution lease",
            )
        from src.apps.rule_governance.backend.access import authorize
        authorize((row.selection_manifest_json or {}).get("execution_user_id"))
        return row
