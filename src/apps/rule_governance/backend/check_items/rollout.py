"""Persistent, evidence-gated rollout campaigns for check-item runtime assets."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func

from src.common.governance import actor_matches_any
from src.infrastructure.db.models import (
    AuditLogRecord,
    CheckItemRuntimeBundleVersionRecord,
    ComparisonContextRecord,
    NetworkDevice,
    RolloutCampaignRecord,
    RolloutComparisonEvidenceRecord,
    RolloutLegacyRouteRecord,
    RolloutStageAttemptRecord,
    RolloutStageExecutionRecord,
)
from src.infrastructure.db.postgres import get_db_session

ROLLOUT_STAGES = (
    "shadow",
    "canary_5",
    "canary_20",
    "canary_50",
    "full_100",
    "observing",
)
STAGE_PERCENTAGES = {
    "shadow": 0,
    "canary_5": 5,
    "canary_20": 20,
    "canary_50": 50,
    "full_100": 100,
    "observing": 100,
}
NEXT_STAGE = {
    stage: ROLLOUT_STAGES[index + 1]
    for index, stage in enumerate(ROLLOUT_STAGES[:-1])
}
COMPARISON_DOMAINS = (
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
RUNTIME_BUNDLE_ROLES = ("baseline", "candidate")
DEFAULT_THRESHOLDS = {
    "min_runtime_bundle_coverage_rate": 1.0,
    "min_execution_success_rate": 0.99,
    "min_assessment_coverage_rate": 1.0,
    "max_unevaluable_rate": 0.01,
    "max_execution_success_rate_drop": 0.0,
    "max_assessment_coverage_rate_drop": 0.0,
    "max_unevaluable_rate_increase": 0.0,
    "max_unexplained_event_difference_count": 0,
    "max_event_collision_count": 0,
    "max_confirmed_false_positive_count": 0,
    "max_confirmed_false_positive_rate": 0.05,
    "minimum_feedback_rate_sample": 20,
    "max_confirmed_missed_detection_count": 0,
    "max_confirmed_wrong_severity_count": 0,
    "max_confirmed_wrong_scope_count": 0,
    "max_confirmed_evidence_quality_issue_count": 0,
    "max_unexplained_p0_p1": 0,
    "max_historical_evidence_loss_count": 0,
    "require_report_reconciliation": True,
    "require_rollback_verified": True,
    "auto_action": "pause",
}
_DEFAULT_STAGE_EXECUTION_PREPARER = object()
_DEFAULT_STAGE_RESULT_VERIFIER = object()


class RolloutValidationError(ValueError):
    """Raised when a campaign violates a rollout safety invariant."""

    def __init__(
        self,
        blockers: Iterable[str],
        *,
        code: str = "rollout_blocked",
    ) -> None:
        self.blockers = tuple(dict.fromkeys(str(item) for item in blockers))
        self.code = code
        super().__init__("; ".join(self.blockers))


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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: object, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            str(value or "").strip().replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RolloutValidationError(
            [f"{field_name} must be an ISO-8601 timestamp"]
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _action_key(
    campaign_id: str,
    action: str,
    idempotency_key: str,
) -> str:
    return "rollout:" + _digest(
        {
            "campaign_id": campaign_id,
            "action": action,
            "idempotency_key": idempotency_key,
        }
    )


class RolloutCampaignService:
    """Own rollout state transitions, routing and immutable stage evidence."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
        *,
        stage_execution_preparer: Any = _DEFAULT_STAGE_EXECUTION_PREPARER,
        stage_result_verifier: Any = _DEFAULT_STAGE_RESULT_VERIFIER,
    ) -> None:
        self._session_scope = session_scope
        self._stage_execution_preparer = (
            self._prepare_stage_execution
            if stage_execution_preparer is _DEFAULT_STAGE_EXECUTION_PREPARER
            else stage_execution_preparer
        )
        self._stage_result_verifier = (
            self._verify_stage_result
            if stage_result_verifier is _DEFAULT_STAGE_RESULT_VERIFIER
            else stage_result_verifier
        )

    def create(
        self,
        *,
        name: str,
        actor: str,
        idempotency_key: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        name = str(name or "").strip()
        actor = str(actor or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        if not name or not actor or not idempotency_key:
            raise RolloutValidationError(
                ["name, actor and idempotency_key are required"]
            )
        description = str(description or "").strip() or None
        request_digest = self._request_digest(
            "create",
            {
                "name": name,
                "description": description,
                "actor": actor,
            },
        )
        now = _utcnow()
        with self._session_scope() as db:
            existing = (
                db.query(RolloutCampaignRecord)
                .filter(
                    RolloutCampaignRecord.idempotency_key
                    == idempotency_key
                )
                .first()
            )
            if existing is not None:
                existing_digest = self._request_digest(
                    "create",
                    {
                        "name": str(existing.name),
                        "description": existing.description,
                        "actor": str(existing.created_by),
                    },
                )
                if existing_digest != request_digest:
                    raise RolloutValidationError(
                        [
                            "idempotency key was already used with a "
                            "different create request"
                        ],
                        code="idempotency_conflict",
                    )
                return self._serialize(db, existing)
            campaign = RolloutCampaignRecord(
                id=str(uuid.uuid4()),
                idempotency_key=idempotency_key,
                name=name,
                description=description,
                status="draft",
                current_stage=None,
                lock_version=1,
                created_by=actor,
                created_at=now,
                updated_at=now,
            )
            db.add(campaign)
            self._audit(
                db,
                campaign=campaign,
                action="rollout_campaign_created",
                actor=actor,
                idempotency_key=_action_key(
                    campaign.id,
                    "create",
                    idempotency_key,
                ),
                detail={
                    "name": name,
                    "request_digest": request_digest,
                },
            )
            db.flush()
            return self._serialize(db, campaign)

    def freeze_target(
        self,
        campaign_id: str,
        *,
        target_manifest: Mapping[str, Any],
        runtime_bundle_refs: Iterable[Mapping[str, Any]],
        comparison_context_ids: Iterable[str],
        baseline_window: Mapping[str, Any],
        thresholds: Mapping[str, Any] | None,
        rollback_plan: Mapping[str, Any],
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        actor, reason = self._actor_reason(actor, reason)
        runtime_bundle_refs = [
            dict(item) for item in runtime_bundle_refs
        ]
        comparison_context_ids = [
            str(item) for item in comparison_context_ids
        ]
        request_digest = self._request_digest(
            "freeze_target",
            {
                "target_manifest": dict(target_manifest or {}),
                "runtime_bundle_refs": runtime_bundle_refs,
                "comparison_context_ids": comparison_context_ids,
                "baseline_window": dict(baseline_window or {}),
                "thresholds": dict(thresholds or {}),
                "rollback_plan": dict(rollback_plan or {}),
                "actor": actor,
                "reason": reason,
            },
        )
        now = _utcnow()
        with self._session_scope() as db:
            campaign = self._locked_campaign(db, campaign_id)
            if self._action_replayed(
                db,
                campaign.id,
                "freeze_target",
                idempotency_key,
                request_digest,
            ):
                return self._serialize(db, campaign)
            if campaign.status != "draft":
                raise RolloutValidationError(
                    ["only a draft campaign can freeze its target"]
                )

            normalized_target = self._normalize_target_manifest(
                db,
                target_manifest,
                campaign_id=campaign.id,
            )
            target_digest = _digest(normalized_target)
            normalized_refs = self._validate_runtime_bundles(
                db,
                normalized_target["matrix"],
                runtime_bundle_refs,
                actor=actor,
            )
            runtime_digest = _digest(
                {
                    "schema_version": "rollout-runtime-bundles/v2",
                    "refs": normalized_refs,
                }
            )
            context_ids, context_digest = self._validate_contexts(
                db,
                normalized_target["matrix"],
                comparison_context_ids,
            )
            normalized_window = self._validate_baseline_window(
                baseline_window
            )
            normalized_thresholds = self._normalize_thresholds(
                thresholds or {}
            )
            normalized_rollback = self._validate_rollback_plan(
                rollback_plan
            )

            campaign.status = "target_frozen"
            campaign.current_stage = "shadow"
            campaign.target_manifest_json = normalized_target
            campaign.target_manifest_digest = target_digest
            campaign.runtime_bundle_refs_json = normalized_refs
            campaign.runtime_bundle_digest = runtime_digest
            campaign.comparison_context_ids_json = list(context_ids)
            campaign.comparison_context_digest = context_digest
            campaign.baseline_window_json = normalized_window
            campaign.thresholds_json = normalized_thresholds
            campaign.rollback_plan_json = normalized_rollback
            campaign.frozen_by = actor
            campaign.frozen_at = now
            campaign.updated_at = now
            campaign.lock_version = int(campaign.lock_version) + 1
            self._create_attempt(db, campaign, "shadow", now=now)
            self._audit(
                db,
                campaign=campaign,
                action="rollout_target_frozen",
                actor=actor,
                idempotency_key=_action_key(
                    campaign.id,
                    "freeze_target",
                    idempotency_key,
                ),
                detail={
                    "reason": reason,
                    "target_manifest_digest": target_digest,
                    "runtime_bundle_digest": runtime_digest,
                    "comparison_context_digest": context_digest,
                    "request_digest": request_digest,
                },
            )
            db.flush()
            return self._serialize(db, campaign)

    def start_stage(
        self,
        campaign_id: str,
        *,
        actor: str,
        actor_user_id: str | None = None,
        reason: str,
        idempotency_key: str,
        execution_mode: str = "snapshot_replay",
    ) -> dict[str, Any]:
        actor, reason = self._actor_reason(actor, reason)
        execution_mode = str(execution_mode or "").strip()
        if execution_mode not in {"snapshot_replay", "live_capture"}:
            raise RolloutValidationError(
                [
                    "execution_mode must be snapshot_replay or "
                    "live_capture"
                ],
                code="execution_mode_invalid",
            )
        request_digest = self._request_digest(
            "start_stage",
            {
                "actor": actor,
                "reason": reason,
                "execution_mode": execution_mode,
            },
        )
        now = _utcnow()
        with self._session_scope() as db:
            campaign = self._locked_campaign(db, campaign_id)
            if self._action_replayed(
                db,
                campaign.id,
                "start_stage",
                idempotency_key,
                request_digest,
            ):
                return self._serialize(db, campaign)
            if campaign.status in {
                "draft",
                "paused",
                "rolled_back",
                "completed",
            }:
                raise RolloutValidationError(
                    [f"campaign status {campaign.status} cannot start a stage"]
                )
            attempt = self._current_attempt(db, campaign)
            if attempt is None or attempt.status != "pending":
                raise RolloutValidationError(
                    ["the current rollout stage is not pending"]
                )
            self._assert_attempt_digests(campaign, attempt)
            live_blockers = self._live_digest_blockers(db, campaign)
            if live_blockers:
                attempt.status = "paused"
                self._pause_campaign(
                    campaign,
                    attempt,
                    actor=actor,
                    reason="; ".join(live_blockers),
                    now=now,
                    previous_attempt_status="pending",
                )
                self._audit(
                    db,
                    campaign=campaign,
                    action="rollout_stage_start_blocked",
                    actor=actor,
                    idempotency_key=_action_key(
                        campaign.id,
                        "start_stage",
                        idempotency_key,
                    ),
                    detail={
                        "stage": attempt.stage,
                        "reason": reason,
                        "blockers": live_blockers,
                        "new_campaign_required": True,
                        "request_digest": request_digest,
                    },
                )
                db.flush()
                return self._serialize(db, campaign)
            stage_execution = None
            if self._stage_execution_preparer is not None:
                stage_execution = self._stage_execution_preparer(
                    db,
                    campaign=campaign,
                    attempt=attempt,
                    actor=actor,
                    actor_user_id=actor_user_id,
                    execution_mode=execution_mode,
                )
            attempt.status = "running"
            attempt.started_by = actor
            attempt.started_at = now
            campaign.status = str(attempt.stage)
            campaign.current_stage = str(attempt.stage)
            campaign.updated_at = now
            campaign.lock_version = int(campaign.lock_version) + 1
            self._audit(
                db,
                campaign=campaign,
                action="rollout_stage_started",
                actor=actor,
                idempotency_key=_action_key(
                    campaign.id,
                    "start_stage",
                    idempotency_key,
                ),
                detail={
                    "stage": attempt.stage,
                    "attempt_number": attempt.attempt_number,
                    "stage_execution_id": (
                        str(stage_execution.get("id"))
                        if isinstance(stage_execution, Mapping)
                        and stage_execution.get("id")
                        else None
                    ),
                    "reason": reason,
                    "execution_mode": execution_mode,
                    "request_digest": request_digest,
                },
            )
            db.flush()
            return self._serialize(db, campaign)

    def record_stage_result(
        self,
        campaign_id: str,
        *,
        metrics: Mapping[str, Any],
        comparisons: Mapping[str, Any],
        evidence_refs: Iterable[Mapping[str, Any] | str],
        observed_digests: Mapping[str, str],
        actor: str,
        reason: str,
        idempotency_key: str,
        trusted: bool = False,
    ) -> dict[str, Any]:
        actor, reason = self._actor_reason(actor, reason)
        if not trusted or actor != "system:rollout-evaluator":
            raise RolloutValidationError(
                [
                    "stage results must be derived from persisted rollout "
                    "facts by the trusted evaluator"
                ],
                code="trusted_evaluator_required",
            )
        metric_values = dict(metrics or {})
        comparison_values = dict(comparisons or {})
        evidence = [
            dict(item) if isinstance(item, Mapping) else {"ref": str(item)}
            for item in evidence_refs
            if item
        ]
        observed_digest_values = dict(observed_digests or {})
        request_digest = self._request_digest(
            "record_stage_result",
            {
                "metrics": metric_values,
                "comparisons": comparison_values,
                "evidence_refs": evidence,
                "observed_digests": observed_digest_values,
                "actor": actor,
                "reason": reason,
            },
        )
        now = _utcnow()
        with self._session_scope() as db:
            campaign = self._locked_campaign(db, campaign_id)
            if self._action_replayed(
                db,
                campaign.id,
                "record_stage_result",
                idempotency_key,
                request_digest,
            ):
                return self._serialize(db, campaign)
            attempt = self._current_attempt(db, campaign)
            if attempt is None or attempt.status != "running":
                raise RolloutValidationError(
                    ["only a running stage can record a result"]
                )
            if self._stage_result_verifier is not None:
                self._stage_result_verifier(
                    db,
                    campaign=campaign,
                    attempt=attempt,
                    metrics=metric_values,
                    comparisons=comparison_values,
                    evidence_refs=evidence,
                )
            digest_blockers = self._digest_blockers(
                campaign,
                observed_digest_values,
            )
            digest_blockers.extend(
                self._live_digest_blockers(db, campaign)
            )
            digest_blockers = list(dict.fromkeys(digest_blockers))
            blockers = [
                *digest_blockers,
                *self._metric_blockers(
                    metric_values,
                    dict(attempt.thresholds_json or {}),
                ),
                *self._comparison_blockers(comparison_values),
            ]
            if not evidence:
                blockers.append("stage evidence references are required")

            attempt.metrics_json = metric_values
            attempt.comparison_result_json = comparison_values
            attempt.evidence_refs_json = evidence
            attempt.blockers_json = list(dict.fromkeys(blockers))
            attempt.completed_at = now
            attempt.decision_digest = _digest(
                {
                    "campaign_id": campaign.id,
                    "stage": attempt.stage,
                    "attempt_number": attempt.attempt_number,
                    "metrics": metric_values,
                    "comparisons": comparison_values,
                    "evidence_refs": evidence,
                    "blockers": attempt.blockers_json,
                    "frozen": {
                        "target_manifest_digest": attempt.target_manifest_digest,
                        "runtime_bundle_digest": attempt.runtime_bundle_digest,
                        "comparison_context_digest": (
                            attempt.comparison_context_digest
                        ),
                    },
                }
            )
            action = "awaiting_approval"
            if blockers:
                attempt.status = "failed"
                campaign.last_error = "; ".join(
                    str(item) for item in attempt.blockers_json
                )
                if digest_blockers:
                    self._pause_campaign(
                        campaign,
                        attempt,
                        actor=actor,
                        reason="asset_or_target_digest_drift_new_campaign_required",
                        now=now,
                        previous_attempt_status="failed",
                    )
                    action = "paused"
                elif (
                    dict(attempt.thresholds_json or {}).get("auto_action")
                    == "rollback"
                ):
                    self._roll_back_campaign(
                        campaign,
                        attempt,
                        actor=actor,
                        reason=campaign.last_error,
                        evidence=evidence,
                        now=now,
                    )
                    action = "rolled_back"
                else:
                    self._pause_campaign(
                        campaign,
                        attempt,
                        actor=actor,
                        reason=campaign.last_error,
                        now=now,
                        previous_attempt_status="failed",
                    )
                    action = "paused"
            else:
                attempt.status = "awaiting_approval"
                campaign.last_error = None
                campaign.updated_at = now
                campaign.lock_version = int(campaign.lock_version) + 1

            self._audit(
                db,
                campaign=campaign,
                action="rollout_stage_result_recorded",
                actor=actor,
                idempotency_key=_action_key(
                    campaign.id,
                    "record_stage_result",
                    idempotency_key,
                ),
                detail={
                    "stage": attempt.stage,
                    "attempt_number": attempt.attempt_number,
                    "decision_digest": attempt.decision_digest,
                    "outcome": action,
                    "reason": reason,
                    "blockers": list(attempt.blockers_json or []),
                    "request_digest": request_digest,
                },
            )
            db.flush()
            return self._serialize(db, campaign)

    def approve_stage(
        self,
        campaign_id: str,
        *,
        actor: str,
        comment: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        actor, comment = self._actor_reason(
            actor,
            comment,
            reason_label="approval comment",
        )
        request_digest = self._request_digest(
            "approve_stage",
            {
                "actor": actor,
                "comment": comment,
            },
        )
        now = _utcnow()
        with self._session_scope() as db:
            campaign = self._locked_campaign(db, campaign_id)
            if self._action_replayed(
                db,
                campaign.id,
                "approve_stage",
                idempotency_key,
                request_digest,
            ):
                return self._serialize(db, campaign)
            attempt = self._current_attempt(db, campaign)
            if attempt is None or attempt.status != "awaiting_approval":
                raise RolloutValidationError(
                    ["the current stage is not awaiting approval"]
                )
            if actor_matches_any(
                actor,
                [campaign.created_by, attempt.started_by],
            ):
                raise RolloutValidationError(
                    [
                        "stage approval requires an approver different from "
                        "the campaign creator and stage operator"
                    ],
                    code="separation_of_duties",
                )
            self._assert_attempt_digests(campaign, attempt)
            live_blockers = self._live_digest_blockers(db, campaign)
            if live_blockers:
                attempt.status = "paused"
                self._pause_campaign(
                    campaign,
                    attempt,
                    actor=actor,
                    reason="; ".join(live_blockers),
                    now=now,
                    previous_attempt_status="awaiting_approval",
                )
                self._audit(
                    db,
                    campaign=campaign,
                    action="rollout_stage_approval_blocked",
                    actor=actor,
                    idempotency_key=_action_key(
                        campaign.id,
                        "approve_stage",
                        idempotency_key,
                    ),
                    detail={
                        "stage": attempt.stage,
                        "blockers": live_blockers,
                        "new_campaign_required": True,
                        "request_digest": request_digest,
                    },
                )
                db.flush()
                return self._serialize(db, campaign)
            attempt.status = "passed"
            attempt.approved_by = actor
            attempt.approval_comment = comment
            attempt.approved_at = now
            if attempt.completed_at is None:
                attempt.completed_at = now
            if attempt.stage == "observing":
                campaign.status = "completed"
                campaign.current_stage = "observing"
                campaign.completed_by = actor
                campaign.completed_at = now
            else:
                next_stage = NEXT_STAGE[str(attempt.stage)]
                campaign.status = next_stage
                campaign.current_stage = next_stage
                self._create_attempt(db, campaign, next_stage, now=now)
            campaign.updated_at = now
            campaign.lock_version = int(campaign.lock_version) + 1
            self._audit(
                db,
                campaign=campaign,
                action="rollout_stage_approved",
                actor=actor,
                idempotency_key=_action_key(
                    campaign.id,
                    "approve_stage",
                    idempotency_key,
                ),
                detail={
                    "stage": attempt.stage,
                    "attempt_number": attempt.attempt_number,
                    "decision_digest": attempt.decision_digest,
                    "comment": comment,
                    "next_stage": (
                        None
                        if attempt.stage == "observing"
                        else NEXT_STAGE[str(attempt.stage)]
                    ),
                    "request_digest": request_digest,
                },
            )
            db.flush()
            return self._serialize(db, campaign)

    def pause(
        self,
        campaign_id: str,
        *,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        actor, reason = self._actor_reason(actor, reason)
        request_digest = self._request_digest(
            "pause",
            {
                "actor": actor,
                "reason": reason,
            },
        )
        now = _utcnow()
        with self._session_scope() as db:
            campaign = self._locked_campaign(db, campaign_id)
            if self._action_replayed(
                db,
                campaign.id,
                "pause",
                idempotency_key,
                request_digest,
            ):
                return self._serialize(db, campaign)
            if campaign.status in {
                "draft",
                "paused",
                "rolled_back",
                "completed",
            }:
                raise RolloutValidationError(
                    [f"campaign status {campaign.status} cannot be paused"]
                )
            attempt = self._current_attempt(db, campaign)
            if attempt is None:
                raise RolloutValidationError(
                    ["campaign has no current stage attempt"]
                )
            previous_status = str(attempt.status)
            attempt.status = "paused"
            self._pause_campaign(
                campaign,
                attempt,
                actor=actor,
                reason=reason,
                now=now,
                previous_attempt_status=previous_status,
            )
            self._audit(
                db,
                campaign=campaign,
                action="rollout_campaign_paused",
                actor=actor,
                idempotency_key=_action_key(
                    campaign.id,
                    "pause",
                    idempotency_key,
                ),
                detail={
                    "reason": reason,
                    "stage": attempt.stage,
                    "request_digest": request_digest,
                },
            )
            db.flush()
            return self._serialize(db, campaign)

    def resume(
        self,
        campaign_id: str,
        *,
        observed_digests: Mapping[str, str],
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        actor, reason = self._actor_reason(actor, reason)
        observed_digest_values = dict(observed_digests or {})
        request_digest = self._request_digest(
            "resume",
            {
                "observed_digests": observed_digest_values,
                "actor": actor,
                "reason": reason,
            },
        )
        now = _utcnow()
        with self._session_scope() as db:
            campaign = self._locked_campaign(db, campaign_id)
            if self._action_replayed(
                db,
                campaign.id,
                "resume",
                idempotency_key,
                request_digest,
            ):
                return self._serialize(db, campaign)
            if campaign.status != "paused":
                raise RolloutValidationError(
                    ["only a paused campaign can be resumed"]
                )
            blockers = self._digest_blockers(
                campaign,
                observed_digest_values,
            )
            blockers.extend(self._live_digest_blockers(db, campaign))
            blockers = list(dict.fromkeys(blockers))
            if blockers:
                raise RolloutValidationError(
                    [
                        *blockers,
                        "create a new campaign and restart from shadow",
                    ],
                    code="new_campaign_required",
                )
            attempt = self._current_attempt(db, campaign)
            if attempt is None:
                raise RolloutValidationError(
                    ["campaign has no current stage attempt"]
                )
            pause_context = dict(campaign.pause_context_json or {})
            previous_attempt_status = str(
                pause_context.get("attempt_status") or "pending"
            )
            if attempt.status == "failed":
                attempt = self._create_attempt(
                    db,
                    campaign,
                    str(campaign.current_stage),
                    now=now,
                )
                campaign.status = str(campaign.current_stage)
            else:
                attempt.status = (
                    previous_attempt_status
                    if previous_attempt_status
                    in {"pending", "running", "awaiting_approval"}
                    else "pending"
                )
                campaign.status = str(
                    campaign.paused_from_stage
                    or campaign.current_stage
                )
            campaign.paused_from_stage = None
            campaign.pause_context_json = None
            campaign.paused_at = None
            campaign.paused_by = None
            campaign.last_error = None
            campaign.updated_at = now
            campaign.lock_version = int(campaign.lock_version) + 1
            self._audit(
                db,
                campaign=campaign,
                action="rollout_campaign_resumed",
                actor=actor,
                idempotency_key=_action_key(
                    campaign.id,
                    "resume",
                    idempotency_key,
                ),
                detail={
                    "reason": reason,
                    "stage": campaign.current_stage,
                    "attempt_number": attempt.attempt_number,
                    "request_digest": request_digest,
                },
            )
            db.flush()
            return self._serialize(db, campaign)

    def rollback(
        self,
        campaign_id: str,
        *,
        evidence_refs: Iterable[Mapping[str, Any] | str],
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        actor, reason = self._actor_reason(actor, reason)
        now = _utcnow()
        evidence = [
            dict(item) if isinstance(item, Mapping) else {"ref": str(item)}
            for item in evidence_refs
            if item
        ]
        if not evidence:
            raise RolloutValidationError(
                ["rollback evidence references are required"]
            )
        request_digest = self._request_digest(
            "rollback",
            {
                "evidence_refs": evidence,
                "actor": actor,
                "reason": reason,
            },
        )
        with self._session_scope() as db:
            campaign = self._locked_campaign(db, campaign_id)
            if self._action_replayed(
                db,
                campaign.id,
                "rollback",
                idempotency_key,
                request_digest,
            ):
                return self._serialize(db, campaign)
            if campaign.status in {"draft", "rolled_back", "completed"}:
                raise RolloutValidationError(
                    [f"campaign status {campaign.status} cannot roll back"]
                )
            attempt = self._current_attempt(db, campaign)
            if attempt is None:
                raise RolloutValidationError(
                    ["campaign has no current stage attempt"]
                )
            self._roll_back_campaign(
                campaign,
                attempt,
                actor=actor,
                reason=reason,
                evidence=evidence,
                now=now,
            )
            self._audit(
                db,
                campaign=campaign,
                action="rollout_campaign_rolled_back",
                actor=actor,
                idempotency_key=_action_key(
                    campaign.id,
                    "rollback",
                    idempotency_key,
                ),
                detail={
                    "reason": reason,
                    "stage": attempt.stage,
                    "evidence_refs": evidence,
                    "request_digest": request_digest,
                },
            )
            db.flush()
            return self._serialize(db, campaign)

    def routing_decision(
        self,
        campaign_id: str,
        *,
        device_id: str,
        check_item_version_id: str,
        target_manifest_digest: str,
        runtime_bundle_digest: str,
        comparison_context_digest: str,
    ) -> dict[str, Any]:
        device_id = str(device_id or "").strip()
        check_item_version_id = str(check_item_version_id or "").strip()
        if not device_id or not check_item_version_id:
            raise RolloutValidationError(
                ["device_id and check_item_version_id are required"]
            )
        with self._session_scope() as db:
            campaign = self._locked_campaign(db, campaign_id)
            target_row = next(
                (
                    dict(item)
                    for item in dict(
                        campaign.target_manifest_json or {}
                    ).get("matrix", [])
                    if str(item.get("device_id") or "") == device_id
                    and str(item.get("check_item_version_id") or "")
                    == check_item_version_id
                ),
                None,
            )
            if target_row is None:
                return self._routing_payload(
                    campaign,
                    device_id=device_id,
                    check_item_version_id=check_item_version_id,
                    effective_stage=None,
                    percentage=0,
                    candidate_enabled=False,
                    authoritative=False,
                    reason_code="outside_frozen_target",
                    target_row=None,
                )
            blockers = self._digest_blockers(
                campaign,
                {
                    "target_manifest_digest": target_manifest_digest,
                    "runtime_bundle_digest": runtime_bundle_digest,
                    "comparison_context_digest": comparison_context_digest,
                },
            )
            blockers.extend(self._live_digest_blockers(db, campaign))
            blockers = list(dict.fromkeys(blockers))
            if blockers:
                attempt = self._current_attempt(db, campaign)
                if (
                    attempt is not None
                    and campaign.status not in {
                        "paused",
                        "rolled_back",
                        "completed",
                    }
                ):
                    previous_status = str(attempt.status)
                    attempt.status = "paused"
                    now = _utcnow()
                    self._pause_campaign(
                        campaign,
                        attempt,
                        actor="system:rollout-router",
                        reason="; ".join(blockers),
                        now=now,
                        previous_attempt_status=previous_status,
                    )
                    drift_key = _digest(
                        {
                            "blockers": blockers,
                            "target_row": target_row,
                        }
                    )
                    if not self._action_seen(
                        db,
                        campaign.id,
                        "routing_drift",
                        drift_key,
                    ):
                        self._audit(
                            db,
                            campaign=campaign,
                            action="rollout_routing_drift_detected",
                            actor="system:rollout-router",
                            idempotency_key=_action_key(
                                campaign.id,
                                "routing_drift",
                                drift_key,
                            ),
                            detail={
                                "blockers": blockers,
                                "new_campaign_required": True,
                            },
                        )
                    db.flush()
                return self._routing_payload(
                    campaign,
                    device_id=device_id,
                    check_item_version_id=check_item_version_id,
                    effective_stage=None,
                    percentage=0,
                    candidate_enabled=False,
                    authoritative=False,
                    reason_code="campaign_digest_drifted",
                    target_row=target_row,
                    blockers=[
                        *blockers,
                        "create a new campaign and restart from shadow",
                    ],
                )
            attempts = self._attempts(db, campaign.id)
            current = next(
                (
                    item
                    for item in reversed(attempts)
                    if item.stage == campaign.current_stage
                ),
                None,
            )
            effective_stage = None
            if (
                current is not None
                and current.status
                in {"running", "awaiting_approval"}
            ):
                effective_stage = str(current.stage)
            else:
                passed = [item for item in attempts if item.status == "passed"]
                if passed:
                    effective_stage = str(passed[-1].stage)

            status = str(campaign.status)
            candidate_enabled = False
            authoritative = False
            percentage = 0
            reason_code = "campaign_not_started"
            if status in {"paused", "rolled_back"}:
                reason_code = f"campaign_{status}"
            elif status == "completed":
                effective_stage = "observing"
                candidate_enabled = True
                authoritative = True
                percentage = 100
                reason_code = "campaign_completed"
            elif effective_stage == "shadow":
                candidate_enabled = True
                authoritative = False
                reason_code = "shadow_evidence_only"
            elif effective_stage in STAGE_PERCENTAGES:
                percentage = STAGE_PERCENTAGES[effective_stage]
                stage_roster = dict(
                    dict(campaign.target_manifest_json or {})
                    .get("stage_rosters", {})
                    .get(effective_stage, {})
                )
                selected = str(target_row.get("target_key") or "") in {
                    str(item)
                    for item in stage_roster.get("target_keys") or []
                }
                candidate_enabled = selected
                authoritative = selected
                reason_code = (
                    "selected_for_candidate"
                    if selected
                    else "retained_on_previous_path"
                )
            return self._routing_payload(
                campaign,
                device_id=device_id,
                check_item_version_id=check_item_version_id,
                effective_stage=effective_stage,
                percentage=percentage,
                candidate_enabled=candidate_enabled,
                authoritative=authoritative,
                reason_code=reason_code,
                target_row=target_row,
            )

    def get(self, campaign_id: str) -> dict[str, Any] | None:
        with self._session_scope() as db:
            campaign = db.get(RolloutCampaignRecord, campaign_id)
            return (
                self._serialize(db, campaign)
                if campaign is not None
                else None
            )

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        with self._session_scope() as db:
            base_query = db.query(RolloutCampaignRecord)
            if search:
                pattern = f"%{str(search).strip()}%"
                base_query = base_query.filter(
                    RolloutCampaignRecord.name.ilike(pattern)
                    | RolloutCampaignRecord.id.ilike(pattern)
                )
            status_rows = (
                base_query.with_entities(
                    RolloutCampaignRecord.status,
                    func.count(RolloutCampaignRecord.id),
                )
                .group_by(RolloutCampaignRecord.status)
                .all()
            )
            status_counts = {
                str(row_status): int(count)
                for row_status, count in status_rows
            }
            query = base_query
            if status:
                query = query.filter(RolloutCampaignRecord.status == status)
            total = query.count()
            rows = (
                query.order_by(
                    RolloutCampaignRecord.updated_at.desc(),
                    RolloutCampaignRecord.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return {
                "items": [self._serialize(db, item) for item in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
                "page_count": (
                    (total + page_size - 1) // page_size
                    if total
                    else 0
                ),
                "summary": {
                    "total": sum(status_counts.values()),
                    "status_counts": status_counts,
                    "active": sum(
                        status_counts.get(item, 0)
                        for item in ROLLOUT_STAGES
                    )
                    + status_counts.get("target_frozen", 0),
                    "awaiting_approval": (
                        base_query.join(
                            RolloutStageAttemptRecord,
                            RolloutStageAttemptRecord.campaign_id
                            == RolloutCampaignRecord.id,
                        )
                        .filter(
                            RolloutStageAttemptRecord.status
                            == "awaiting_approval"
                        )
                        .count()
                    ),
                    "paused": status_counts.get("paused", 0),
                    "rolled_back": status_counts.get("rolled_back", 0),
                },
            }

    @staticmethod
    def _routing_payload(
        campaign: RolloutCampaignRecord,
        *,
        device_id: str,
        check_item_version_id: str,
        effective_stage: str | None,
        percentage: int,
        candidate_enabled: bool,
        authoritative: bool,
        reason_code: str,
        target_row: Mapping[str, Any] | None,
        blockers: Iterable[str] = (),
    ) -> dict[str, Any]:
        blocker_list = list(dict.fromkeys(str(item) for item in blockers))
        routing_basis = {
            "campaign_id": campaign.id,
            "status": str(campaign.status),
            "effective_stage": effective_stage,
            "percentage": percentage,
            "device_id": device_id,
            "check_item_version_id": check_item_version_id,
            "candidate_enabled": candidate_enabled,
            "authoritative": authoritative,
            "target_row": dict(target_row) if target_row is not None else None,
            "blockers": blocker_list,
        }
        return {
            "campaign_id": campaign.id,
            "campaign_status": str(campaign.status),
            "effective_stage": effective_stage,
            "percentage": percentage,
            "device_id": device_id,
            "check_item_version_id": check_item_version_id,
            "candidate_enabled": candidate_enabled,
            "authoritative": authoritative,
            "reason_code": reason_code,
            "target_row": (
                dict(target_row) if target_row is not None else None
            ),
            "blockers": blocker_list,
            "new_campaign_required": reason_code
            == "campaign_digest_drifted",
            "target_manifest_digest": campaign.target_manifest_digest,
            "runtime_bundle_digest": campaign.runtime_bundle_digest,
            "comparison_context_digest": (
                campaign.comparison_context_digest
            ),
            "routing_digest": _digest(routing_basis),
        }

    @staticmethod
    def _actor_reason(
        actor: str,
        reason: str,
        *,
        reason_label: str = "reason",
    ) -> tuple[str, str]:
        actor = str(actor or "").strip()
        reason = str(reason or "").strip()
        if not actor or not reason:
            raise RolloutValidationError(
                [f"actor and {reason_label} are required"]
            )
        return actor, reason

    @classmethod
    def _normalize_target_manifest(
        cls,
        db: Any,
        target_manifest: Mapping[str, Any],
        *,
        campaign_id: str,
    ) -> dict[str, Any]:
        raw_manifest = dict(target_manifest or {})
        matrix = list(raw_manifest.get("matrix") or [])
        if not matrix:
            raise RolloutValidationError(
                ["target manifest requires a non-empty device/check-item matrix"]
            )
        device_ids = sorted(
            {
                str(dict(item).get("device_id") or "").strip()
                for item in matrix
                if str(dict(item).get("device_id") or "").strip()
            }
        )
        devices = (
            db.query(NetworkDevice)
            .filter(NetworkDevice.id.in_(device_ids))
            .all()
        )
        device_by_id = {str(item.id): item for item in devices}
        missing_devices = sorted(set(device_ids) - set(device_by_id))
        if missing_devices:
            raise RolloutValidationError(
                [
                    "rollout target must reference authoritative inventory "
                    "devices; missing: " + ", ".join(missing_devices)
                ]
            )

        bundle_version_ids = {
            str(dict(item).get("candidate_runtime_bundle_version_id") or "").strip()
            for item in matrix
        }
        bundle_version_ids.update(
            str(dict(dict(item).get("baseline_route") or {}).get(
                "runtime_bundle_version_id"
            ) or "").strip()
            for item in matrix
            if str(dict(dict(item).get("baseline_route") or {}).get("mode") or "")
            == "runtime_bundle"
        )
        bundle_version_ids.discard("")
        bundle_by_id = {
            str(item.id): item
            for item in db.query(CheckItemRuntimeBundleVersionRecord)
            .filter(
                CheckItemRuntimeBundleVersionRecord.id.in_(
                    sorted(bundle_version_ids)
                )
            )
            .all()
        }
        legacy_route_ids = {
            str(dict(dict(item).get("baseline_route") or {}).get("route_id") or "").strip()
            for item in matrix
            if str(dict(dict(item).get("baseline_route") or {}).get("mode") or "")
            == "legacy_route"
        }
        legacy_route_ids.discard("")
        legacy_route_by_id = {
            str(item.id): item
            for item in db.query(RolloutLegacyRouteRecord)
            .filter(RolloutLegacyRouteRecord.id.in_(sorted(legacy_route_ids)))
            .all()
        }

        normalized: list[dict[str, Any]] = []
        blockers: list[str] = []
        for raw_item in matrix:
            raw = dict(raw_item)
            device_id = str(raw.get("device_id") or "").strip()
            check_item_version_id = str(
                raw.get("check_item_version_id") or ""
            ).strip()
            candidate_version_id = str(
                raw.get("candidate_runtime_bundle_version_id") or ""
            ).strip()
            if (
                raw.get("runtime_bundle_version_id")
                and not candidate_version_id
            ):
                blockers.append(
                    "single runtime_bundle_version_id target rows are unsafe; "
                    "provide baseline_route and "
                    "candidate_runtime_bundle_version_id"
                )
                continue
            if not all(
                (
                    device_id,
                    check_item_version_id,
                    candidate_version_id,
                )
            ):
                blockers.append(
                    "every target row requires device, check item and "
                    "candidate runtime bundle versions"
                )
                continue
            device = device_by_id.get(device_id)
            if device is None:
                continue
            try:
                baseline_route = cls._normalize_baseline_route(
                    raw.get("baseline_route")
                )
            except RolloutValidationError as exc:
                blockers.extend(exc.blockers)
                continue
            candidate_bundle = bundle_by_id.get(candidate_version_id)
            if candidate_bundle is None:
                blockers.append(
                    f"candidate RuntimeBundle does not exist: {candidate_version_id}"
                )
                continue
            candidate_implementation_version_id = str(
                candidate_bundle.implementation_version_id
            )
            supplied_candidate_implementation = str(
                raw.get("candidate_implementation_version_id")
                or raw.get("implementation_version_id")
                or ""
            ).strip()
            if (
                supplied_candidate_implementation
                and supplied_candidate_implementation
                != candidate_implementation_version_id
            ):
                blockers.append(
                    "candidate implementation differs from its RuntimeBundle: "
                    f"{device_id}/{check_item_version_id}"
                )
                continue
            if baseline_route["mode"] == "runtime_bundle":
                baseline_bundle = bundle_by_id.get(
                    baseline_route["runtime_bundle_version_id"]
                )
                if baseline_bundle is None:
                    blockers.append(
                        "baseline RuntimeBundle does not exist: "
                        + baseline_route["runtime_bundle_version_id"]
                    )
                    continue
                baseline_implementation_version_id = str(
                    baseline_bundle.implementation_version_id
                )
            elif baseline_route["mode"] == "legacy_route":
                legacy_route = legacy_route_by_id.get(
                    baseline_route["route_id"]
                )
                if legacy_route is None:
                    blockers.append(
                        "legacy baseline route does not exist: "
                        + baseline_route["route_id"]
                    )
                    continue
                baseline_implementation_version_id = str(
                    legacy_route.implementation_version_id
                )
            else:
                baseline_implementation_version_id = ""
            if baseline_implementation_version_id:
                baseline_route = {
                    **baseline_route,
                    "implementation_version_id": (
                        baseline_implementation_version_id
                    ),
                }
            if (
                baseline_route["mode"] == "runtime_bundle"
                and baseline_route["runtime_bundle_version_id"]
                == candidate_version_id
            ):
                blockers.append(
                    "baseline and candidate RuntimeBundle versions must differ "
                    f"for {device_id}/{check_item_version_id}"
                )
                continue
            target_key = _digest(
                {
                    "device_id": device_id,
                    "check_item_version_id": check_item_version_id,
                }
            )
            normalized.append(
                {
                    "target_key": target_key,
                    "device_id": device_id,
                    "check_item_version_id": check_item_version_id,
                    "candidate_implementation_version_id": (
                        candidate_implementation_version_id
                    ),
                    "baseline_route": baseline_route,
                    "candidate_runtime_bundle_version_id": (
                        candidate_version_id
                    ),
                    "stratum": {
                        "site": str(device.site or "").strip() or "unknown",
                        "vendor": str(device.vendor or "").strip() or "unknown",
                        "model": str(device.model or "").strip() or "unknown",
                        "check_item_version_id": check_item_version_id,
                    },
                }
            )
        if blockers:
            raise RolloutValidationError(blockers)
        normalized.sort(
            key=lambda item: (
                item["device_id"],
                item["check_item_version_id"],
                item["candidate_implementation_version_id"],
            )
        )
        identities = {
            (
                item["device_id"],
                item["check_item_version_id"],
            )
            for item in normalized
        }
        if len(identities) != len(normalized):
            raise RolloutValidationError(
                ["target matrix contains duplicate device/check-item rows"]
            )
        stage_rosters = cls._build_stage_rosters(
            campaign_id,
            normalized,
        )
        return {
            "schema_version": "rollout-target/v3",
            "selector": dict(raw_manifest.get("selector") or {}),
            "selection_strategy": (
                "frozen_proportional_stratified_prefix/v1"
            ),
            "matrix": normalized,
            "stage_rosters": stage_rosters,
        }

    @classmethod
    def _validate_frozen_target_manifest(
        cls,
        target_manifest: Mapping[str, Any],
        *,
        campaign_id: str,
    ) -> dict[str, Any]:
        raw_manifest = dict(target_manifest or {})
        schema_version = str(raw_manifest.get("schema_version") or "")
        if schema_version not in {"rollout-target/v2", "rollout-target/v3"}:
            raise RolloutValidationError(
                ["frozen rollout target must use rollout-target/v2 or v3"]
            )
        is_v3 = schema_version == "rollout-target/v3"
        matrix = list(raw_manifest.get("matrix") or [])
        if not matrix:
            raise RolloutValidationError(
                ["frozen rollout target matrix is empty"]
            )
        normalized: list[dict[str, Any]] = []
        blockers: list[str] = []
        for raw_item in matrix:
            raw = dict(raw_item)
            identity_keys = [
                "target_key",
                "device_id",
                "check_item_version_id",
                (
                    "candidate_implementation_version_id"
                    if is_v3
                    else "implementation_version_id"
                ),
                "candidate_runtime_bundle_version_id",
            ]
            row = {
                key: str(raw.get(key) or "").strip()
                for key in identity_keys
            }
            if not all(row.values()):
                blockers.append(
                    "frozen target row is missing an immutable identity field"
                )
                continue
            expected_target_key = _digest(
                {
                    "device_id": row["device_id"],
                    "check_item_version_id": row[
                        "check_item_version_id"
                    ],
                }
            )
            if row["target_key"] != expected_target_key:
                blockers.append(
                    "frozen target key does not match device/check-item identity"
                )
                continue
            try:
                baseline_route = cls._normalize_baseline_route(
                    raw.get("baseline_route")
                )
            except RolloutValidationError as exc:
                blockers.extend(exc.blockers)
                continue
            if (
                is_v3
                and baseline_route["mode"] != "no_prior_production"
                and not baseline_route.get("implementation_version_id")
            ):
                blockers.append(
                    "frozen v3 baseline route is missing its implementation version"
                )
                continue
            stratum = {
                key: str(dict(raw.get("stratum") or {}).get(key) or "").strip()
                for key in (
                    "site",
                    "vendor",
                    "model",
                    "check_item_version_id",
                )
            }
            if not all(stratum.values()):
                blockers.append(
                    "frozen target row is missing rollout stratum fields"
                )
                continue
            if (
                stratum["check_item_version_id"]
                != row["check_item_version_id"]
            ):
                blockers.append(
                    "frozen target stratum differs from its check-item identity"
                )
                continue
            normalized.append(
                {
                    **row,
                    "baseline_route": baseline_route,
                    "stratum": stratum,
                }
            )
        if blockers:
            raise RolloutValidationError(blockers)
        normalized.sort(
            key=lambda item: (
                item["device_id"],
                item["check_item_version_id"],
                item.get("candidate_implementation_version_id")
                or item.get("implementation_version_id"),
            )
        )
        identities = {
            (
                item["device_id"],
                item["check_item_version_id"],
            )
            for item in normalized
        }
        if len(identities) != len(normalized):
            raise RolloutValidationError(
                ["frozen target matrix contains duplicate target identities"]
            )
        stage_rosters = cls._build_stage_rosters(
            campaign_id,
            normalized,
        )
        if dict(raw_manifest.get("stage_rosters") or {}) != stage_rosters:
            raise RolloutValidationError(
                ["frozen rollout stage roster no longer matches its matrix"]
            )
        return {
            "schema_version": schema_version,
            "selector": dict(raw_manifest.get("selector") or {}),
            "selection_strategy": (
                "frozen_proportional_stratified_prefix/v1"
            ),
            "matrix": normalized,
            "stage_rosters": stage_rosters,
        }

    @staticmethod
    def _normalize_baseline_route(value: object) -> dict[str, str]:
        raw = dict(value) if isinstance(value, Mapping) else {}
        mode = str(raw.get("mode") or "").strip()
        implementation_version_id = str(
            raw.get("implementation_version_id") or ""
        ).strip()
        if mode == "runtime_bundle":
            version_id = str(
                raw.get("runtime_bundle_version_id")
                or raw.get("version_id")
                or ""
            ).strip()
            if not version_id:
                raise RolloutValidationError(
                    ["runtime_bundle baseline route requires a version id"]
                )
            result = {
                "mode": mode,
                "runtime_bundle_version_id": version_id,
            }
            if implementation_version_id:
                result["implementation_version_id"] = (
                    implementation_version_id
                )
            return result
        if mode == "legacy_route":
            route_id = str(raw.get("route_id") or "").strip()
            digest = str(raw.get("digest") or "").strip()
            if not route_id or not digest:
                raise RolloutValidationError(
                    [
                        "legacy baseline route requires an immutable route_id "
                        "and digest"
                    ]
                )
            result = {
                "mode": mode,
                "route_id": route_id,
                "digest": digest,
            }
            if implementation_version_id:
                result["implementation_version_id"] = (
                    implementation_version_id
                )
            return result
        if mode == "no_prior_production":
            return {"mode": mode}
        raise RolloutValidationError(
            [
                "baseline_route.mode must be runtime_bundle, legacy_route "
                "or no_prior_production"
            ]
        )

    @classmethod
    def _build_stage_rosters(
        cls,
        campaign_id: str,
        matrix: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for row in matrix:
            stratum = dict(row["stratum"])
            key = (
                str(stratum["site"]).casefold(),
                str(stratum["vendor"]).casefold(),
                str(stratum["model"]).casefold(),
                str(stratum["check_item_version_id"]),
            )
            groups.setdefault(key, []).append(row)
        for key, rows in groups.items():
            rows.sort(
                key=lambda item: _digest(
                    {
                        "campaign_id": campaign_id,
                        "stratum": key,
                        "target_key": item["target_key"],
                    }
                )
            )

        selected_counts = {key: 0 for key in groups}
        order: list[str] = []
        while len(order) < len(matrix):
            available = [
                key
                for key, rows in groups.items()
                if selected_counts[key] < len(rows)
            ]
            group_key = min(
                available,
                key=lambda key: (
                    (selected_counts[key] + 1) / len(groups[key]),
                    _digest(
                        {
                            "campaign_id": campaign_id,
                            "stratum": key,
                        }
                    ),
                ),
            )
            row = groups[group_key][selected_counts[group_key]]
            selected_counts[group_key] += 1
            order.append(str(row["target_key"]))

        rosters: dict[str, dict[str, Any]] = {}
        total = len(order)
        for stage in ROLLOUT_STAGES:
            percentage = STAGE_PERCENTAGES[stage]
            if stage == "shadow" or percentage >= 100:
                target_keys = list(order)
            else:
                count = max(1, math.ceil(total * percentage / 100))
                target_keys = list(order[:count])
            roster_basis = {
                "stage": stage,
                "percentage": percentage,
                "target_keys": target_keys,
            }
            rosters[stage] = {
                **roster_basis,
                "target_count": len(target_keys),
                "digest": _digest(roster_basis),
            }
        return rosters

    @staticmethod
    def _validate_runtime_bundles(
        db: Any,
        matrix: list[dict[str, Any]],
        runtime_bundle_refs: Iterable[Mapping[str, Any]],
        *,
        actor: str,
    ) -> list[dict[str, str]]:
        refs = [
            {
                "role": str(item.get("role") or "").strip(),
                "version_id": str(item.get("version_id") or "").strip(),
                "digest": str(item.get("digest") or "").strip(),
            }
            for item in runtime_bundle_refs
        ]
        if not refs or any(
            item["role"] not in RUNTIME_BUNDLE_ROLES
            or not item["version_id"]
            or not item["digest"]
            for item in refs
        ):
            raise RolloutValidationError(
                [
                    "runtime bundle references require baseline/candidate role, "
                    "version_id and digest"
                ]
            )
        refs.sort(key=lambda item: (item["role"], item["version_id"]))
        if len(
            {(item["role"], item["version_id"]) for item in refs}
        ) != len(refs):
            raise RolloutValidationError(
                ["runtime bundle references contain duplicates"]
            )
        expected = {
            (
                "candidate",
                item["candidate_runtime_bundle_version_id"],
            )
            for item in matrix
        }
        expected.update(
            (
                "baseline",
                item["baseline_route"]["runtime_bundle_version_id"],
            )
            for item in matrix
            if item["baseline_route"]["mode"] == "runtime_bundle"
        )
        actual = {
            (item["role"], item["version_id"]) for item in refs
        }
        blockers: list[str] = []
        if expected != actual:
            blockers.append(
                "baseline/candidate RuntimeBundle references must cover exactly "
                "100% of the target matrix"
            )
        version_ids = sorted({item["version_id"] for item in refs})
        rows = (
            db.query(CheckItemRuntimeBundleVersionRecord)
            .filter(CheckItemRuntimeBundleVersionRecord.id.in_(version_ids))
            .all()
        )
        by_id = {str(item.id): item for item in rows}
        if set(by_id) != set(version_ids):
            blockers.append("one or more runtime bundle versions do not exist")
        ref_by_role_id = {
            (item["role"], item["version_id"]): item for item in refs
        }
        for role, version_id in sorted(actual):
            version = by_id.get(version_id)
            if version is None:
                continue
            if role == "baseline" and version.status != "active":
                blockers.append(
                    f"baseline RuntimeBundle is not active: {version_id}"
                )
            if role == "candidate" and version.status != "draft":
                blockers.append(
                    f"candidate RuntimeBundle must remain draft: {version_id}"
                )
            report = dict(version.validation_report_json or {})
            if role == "candidate" and not (
                report.get("success") is True
                or report.get("passed") is True
            ):
                blockers.append(
                    f"candidate RuntimeBundle validation did not pass: {version_id}"
                )
            if role == "candidate" and not str(
                version.created_by or ""
            ).strip():
                blockers.append(
                    f"candidate RuntimeBundle creator is missing: {version_id}"
                )
            if role == "candidate" and actor_matches_any(
                actor,
                [version.created_by],
            ):
                blockers.append(
                    "campaign freeze requires an approver different from "
                    f"candidate creator: {version_id}"
                )
            if (
                str(version.digest)
                != ref_by_role_id[(role, version_id)]["digest"]
            ):
                blockers.append(
                    f"{role} RuntimeBundle digest mismatch: {version_id}"
                )
        for item in matrix:
            scoped_versions = [
                (
                    "candidate",
                    item["candidate_runtime_bundle_version_id"],
                )
            ]
            if item["baseline_route"]["mode"] == "runtime_bundle":
                scoped_versions.append(
                    (
                        "baseline",
                        item["baseline_route"][
                            "runtime_bundle_version_id"
                        ],
                    )
                )
            for role, version_id in scoped_versions:
                version = by_id.get(version_id)
                if version is None:
                    continue
                expected_implementation_version_id = (
                    str(
                        item.get("candidate_implementation_version_id")
                        or item.get("implementation_version_id")
                        or ""
                    )
                    if role == "candidate"
                    else str(
                        item["baseline_route"].get(
                            "implementation_version_id"
                        )
                        or item.get("implementation_version_id")
                        or ""
                    )
                )
                if (
                    str(version.check_item_version_id)
                    != item["check_item_version_id"]
                    or str(version.implementation_version_id)
                    != expected_implementation_version_id
                ):
                    blockers.append(
                        f"{role} RuntimeBundle scope differs from target "
                        f"matrix: {version_id}"
                    )
        legacy_routes = {
            (
                str(item["baseline_route"]["route_id"]),
                str(item["baseline_route"]["digest"]),
            )
            for item in matrix
            if item["baseline_route"]["mode"] == "legacy_route"
        }
        legacy_by_id: dict[str, RolloutLegacyRouteRecord] = {}
        if legacy_routes:
            legacy_by_id = {
                str(item.id): item
                for item in db.query(RolloutLegacyRouteRecord)
                .filter(
                    RolloutLegacyRouteRecord.id.in_(
                        sorted(route_id for route_id, _ in legacy_routes)
                    )
                )
                .all()
            }
        for item in matrix:
            route = item["baseline_route"]
            if route["mode"] != "legacy_route":
                continue
            route_id = str(route["route_id"])
            row = legacy_by_id.get(route_id)
            if row is None:
                blockers.append(
                    f"legacy baseline route does not exist: {route_id}"
                )
                continue
            if str(row.status) != "active":
                blockers.append(
                    f"legacy baseline route is not active: {route_id}"
                )
            if str(row.digest) != str(route["digest"]):
                blockers.append(
                    f"legacy baseline route digest mismatch: {route_id}"
                )
            if (
                str(row.adapter_kind),
                str(row.adapter_version),
            ) != ("runtime_bundle_compat", "v1"):
                blockers.append(
                    f"legacy baseline adapter is not installed: {route_id}"
                )
            if (
                str(row.check_item_version_id)
                != item["check_item_version_id"]
                or str(row.implementation_version_id)
                != str(
                    route.get("implementation_version_id")
                    or item.get("implementation_version_id")
                    or ""
                )
            ):
                blockers.append(
                    f"legacy baseline route scope differs from target "
                    f"matrix: {route_id}"
                )
            if str(row.adapter_kind) == "runtime_bundle_compat":
                config = dict(row.config_json or {})
                dependency_id = str(
                    config.get("runtime_bundle_version_id") or ""
                )
                dependency = db.get(
                    CheckItemRuntimeBundleVersionRecord,
                    dependency_id,
                )
                if dependency is None:
                    blockers.append(
                        "legacy route RuntimeBundle is missing: "
                        f"{route_id}"
                    )
                elif (
                    str(dependency.status) != "active"
                    or str(dependency.digest)
                    != str(config.get("runtime_bundle_digest") or "")
                ):
                    blockers.append(
                        "legacy route RuntimeBundle is inactive or drifted: "
                        f"{route_id}"
                    )
        if blockers:
            raise RolloutValidationError(blockers)
        return refs

    @staticmethod
    def _validate_contexts(
        db: Any,
        matrix: list[dict[str, str]],
        comparison_context_ids: Iterable[str],
    ) -> tuple[tuple[str, ...], str]:
        context_ids = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in comparison_context_ids
                    if str(item).strip()
                }
            )
        )
        if not context_ids:
            raise RolloutValidationError(
                ["comparison contexts are required for every target row"]
            )
        rows = (
            db.query(ComparisonContextRecord)
            .filter(ComparisonContextRecord.id.in_(context_ids))
            .all()
        )
        if len(rows) != len(context_ids):
            raise RolloutValidationError(
                ["one or more comparison contexts do not exist"]
            )
        expected_pairs = {
            (
                item["device_id"],
                item["check_item_version_id"],
            )
            for item in matrix
        }
        actual_pairs = {
            (str(item.device_id), str(item.check_item_version_id))
            for item in rows
        }
        if actual_pairs != expected_pairs or len(rows) != len(expected_pairs):
            raise RolloutValidationError(
                [
                    "comparison context coverage must map exactly one context "
                    "to every target device/check-item row"
                ]
            )
        payload = {
            "schema_version": "rollout-comparison-contexts/v1",
            "contexts": sorted(
                (
                    {"id": str(item.id), "digest": str(item.digest)}
                    for item in rows
                ),
                key=lambda item: item["id"],
            ),
        }
        return context_ids, _digest(payload)

    @staticmethod
    def _validate_baseline_window(
        baseline_window: Mapping[str, Any],
    ) -> dict[str, str]:
        start = _parse_datetime(
            dict(baseline_window or {}).get("start_at"),
            field_name="baseline_window.start_at",
        )
        end = _parse_datetime(
            dict(baseline_window or {}).get("end_at"),
            field_name="baseline_window.end_at",
        )
        if start >= end:
            raise RolloutValidationError(
                ["baseline window start_at must be earlier than end_at"]
            )
        return {
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        }

    @staticmethod
    def _normalize_thresholds(
        thresholds: Mapping[str, Any],
    ) -> dict[str, Any]:
        values = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
        blockers: list[str] = []
        for key in (
            "min_runtime_bundle_coverage_rate",
            "min_execution_success_rate",
            "min_assessment_coverage_rate",
            "max_unevaluable_rate",
            "max_execution_success_rate_drop",
            "max_assessment_coverage_rate_drop",
            "max_unevaluable_rate_increase",
            "max_confirmed_false_positive_rate",
        ):
            try:
                values[key] = float(values[key])
            except (TypeError, ValueError):
                blockers.append(f"{key} must be numeric")
                continue
            if not 0 <= values[key] <= 1:
                blockers.append(f"{key} must be between 0 and 1")
        for key in (
            "max_unexplained_p0_p1",
            "max_historical_evidence_loss_count",
            "max_unexplained_event_difference_count",
            "max_event_collision_count",
            "max_confirmed_false_positive_count",
            "minimum_feedback_rate_sample",
            "max_confirmed_missed_detection_count",
            "max_confirmed_wrong_severity_count",
            "max_confirmed_wrong_scope_count",
            "max_confirmed_evidence_quality_issue_count",
        ):
            try:
                values[key] = int(values[key])
            except (TypeError, ValueError):
                blockers.append(f"{key} must be an integer")
                continue
            if values[key] < 0:
                blockers.append(f"{key} cannot be negative")
        for key in (
            "require_report_reconciliation",
            "require_rollback_verified",
        ):
            if not isinstance(values[key], bool):
                blockers.append(f"{key} must be a boolean")
        values["auto_action"] = str(values["auto_action"]).strip()
        if values["auto_action"] not in {"pause", "rollback"}:
            blockers.append("auto_action must be pause or rollback")
        if blockers:
            raise RolloutValidationError(blockers)
        return values

    @staticmethod
    def _validate_rollback_plan(
        rollback_plan: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = dict(rollback_plan or {})
        mode = str(value.get("mode") or "").strip()
        if mode not in {
            "legacy_route",
            "runtime_bundle",
            "no_prior_production",
        }:
            raise RolloutValidationError(
                [
                    "rollback plan mode must be legacy_route, "
                    "runtime_bundle or no_prior_production"
                ]
            )
        if value.get("verified") is not True:
            raise RolloutValidationError(
                ["rollback plan must have a verified rehearsal"]
            )
        evidence = [
            dict(item) if isinstance(item, Mapping) else {"ref": str(item)}
            for item in value.get("evidence_refs") or []
            if item
        ]
        if not evidence:
            raise RolloutValidationError(
                ["rollback plan requires rehearsal evidence"]
            )
        return {
            **value,
            "mode": mode,
            "verified": True,
            "evidence_refs": evidence,
        }

    @staticmethod
    def _metric_blockers(
        metrics: Mapping[str, Any],
        thresholds: Mapping[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        rate_keys = (
            "runtime_bundle_coverage_rate",
            "execution_success_rate",
            "baseline_execution_success_rate",
            "assessment_coverage_rate",
            "baseline_assessment_coverage_rate",
            "unevaluable_rate",
            "baseline_unevaluable_rate",
        )
        values: dict[str, float] = {}
        for key in rate_keys:
            try:
                values[key] = float(metrics[key])
            except (KeyError, TypeError, ValueError):
                blockers.append(f"stage metric is missing or invalid: {key}")
                continue
            if not 0 <= values[key] <= 1:
                blockers.append(f"stage metric must be between 0 and 1: {key}")
        integer_keys = (
            "unexplained_p0_p1_count",
            "historical_evidence_loss_count",
            "unexplained_event_difference_count",
            "event_collision_count",
        )
        integers: dict[str, int] = {}
        for key in integer_keys:
            try:
                integers[key] = int(metrics[key])
            except (KeyError, TypeError, ValueError):
                blockers.append(f"stage metric is missing or invalid: {key}")
        if blockers:
            return blockers
        if (
            values["runtime_bundle_coverage_rate"]
            < float(thresholds["min_runtime_bundle_coverage_rate"])
        ):
            blockers.append("runtime bundle coverage is below the frozen threshold")
        if (
            values["execution_success_rate"]
            < float(thresholds["min_execution_success_rate"])
        ):
            blockers.append("execution success rate is below the frozen threshold")
        if (
            values["assessment_coverage_rate"]
            < float(thresholds["min_assessment_coverage_rate"])
        ):
            blockers.append("assessment coverage is below the frozen threshold")
        for current_key, baseline_key, threshold_key, label in (
            (
                "execution_success_rate",
                "baseline_execution_success_rate",
                "max_execution_success_rate_drop",
                "execution success",
            ),
            (
                "assessment_coverage_rate",
                "baseline_assessment_coverage_rate",
                "max_assessment_coverage_rate_drop",
                "assessment coverage",
            ),
        ):
            if (
                values[current_key]
                + float(thresholds[threshold_key])
                < values[baseline_key]
            ):
                blockers.append(f"{label} regressed beyond the frozen threshold")
        if values["unevaluable_rate"] > float(
            thresholds["max_unevaluable_rate"]
        ):
            blockers.append("unevaluable rate is above the frozen threshold")
        if (
            values["unevaluable_rate"]
            > values["baseline_unevaluable_rate"]
            + float(thresholds["max_unevaluable_rate_increase"])
        ):
            blockers.append(
                "unevaluable rate regressed beyond the frozen threshold"
            )
        if integers["unexplained_p0_p1_count"] > int(
            thresholds["max_unexplained_p0_p1"]
        ):
            blockers.append("unexplained P0/P1 differences exceed the threshold")
        if integers["historical_evidence_loss_count"] > int(
            thresholds["max_historical_evidence_loss_count"]
        ):
            blockers.append("historical evidence loss exceeds the threshold")
        if integers["unexplained_event_difference_count"] > int(
            thresholds["max_unexplained_event_difference_count"]
        ):
            blockers.append(
                "unexplained event differences exceed the threshold"
            )
        if integers["event_collision_count"] > int(
            thresholds["max_event_collision_count"]
        ):
            blockers.append("event collisions exceed the threshold")
        feedback_integer_keys = (
            "confirmed_false_positive_count",
            "confirmed_missed_detection_count",
            "confirmed_wrong_severity_count",
            "confirmed_wrong_scope_count",
            "confirmed_evidence_quality_issue_count",
        )
        feedback_integers: dict[str, int] = {}
        for key in feedback_integer_keys:
            try:
                feedback_integers[key] = int(metrics.get(key, 0))
            except (TypeError, ValueError):
                blockers.append(f"stage metric is invalid: {key}")
        for metric_key, threshold_key, label in (
            (
                "confirmed_false_positive_count",
                "max_confirmed_false_positive_count",
                "confirmed false positives",
            ),
            (
                "confirmed_missed_detection_count",
                "max_confirmed_missed_detection_count",
                "confirmed missed detections",
            ),
            (
                "confirmed_wrong_severity_count",
                "max_confirmed_wrong_severity_count",
                "confirmed severity errors",
            ),
            (
                "confirmed_wrong_scope_count",
                "max_confirmed_wrong_scope_count",
                "confirmed scope errors",
            ),
            (
                "confirmed_evidence_quality_issue_count",
                "max_confirmed_evidence_quality_issue_count",
                "confirmed evidence quality issues",
            ),
        ):
            if feedback_integers.get(metric_key, 0) > int(thresholds[threshold_key]):
                blockers.append(f"{label} exceed the frozen threshold")
        if metrics.get("feedback_rate_sample_sufficient") is True:
            try:
                false_positive_rate = float(metrics["confirmed_false_positive_rate"])
            except (KeyError, TypeError, ValueError):
                blockers.append(
                    "confirmed false-positive rate is missing for a sufficient sample"
                )
            else:
                if false_positive_rate > float(
                    thresholds["max_confirmed_false_positive_rate"]
                ):
                    blockers.append(
                        "confirmed false-positive rate exceeds the frozen threshold"
                    )
        if (
            thresholds.get("require_report_reconciliation") is True
            and metrics.get("report_reconciled") is not True
        ):
            blockers.append("report fact reconciliation did not pass")
        if (
            thresholds.get("require_rollback_verified") is True
            and metrics.get("rollback_verified") is not True
        ):
            blockers.append("rollback verification did not pass")
        return blockers

    @staticmethod
    def _comparison_blockers(
        comparisons: Mapping[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        for domain in COMPARISON_DOMAINS:
            value = comparisons.get(domain)
            if value is True:
                continue
            if isinstance(value, Mapping) and str(
                value.get("status") or ""
            ) in {
                "matched",
                "explained",
                "explained_improvement",
                "expected_change",
                "not_applicable",
            }:
                continue
            blockers.append(f"unexplained rollout difference: {domain}")
        return blockers

    @staticmethod
    def _digest_blockers(
        campaign: RolloutCampaignRecord,
        observed: Mapping[str, str],
    ) -> list[str]:
        blockers = []
        for field in (
            "target_manifest_digest",
            "runtime_bundle_digest",
            "comparison_context_digest",
        ):
            if str(observed.get(field) or "") != str(
                getattr(campaign, field) or ""
            ):
                blockers.append(f"frozen rollout digest drifted: {field}")
        return blockers

    @classmethod
    def _live_digest_blockers(
        cls,
        db: Any,
        campaign: RolloutCampaignRecord,
    ) -> list[str]:
        blockers: list[str] = []
        try:
            target = cls._validate_frozen_target_manifest(
                dict(campaign.target_manifest_json or {}),
                campaign_id=str(campaign.id),
            )
        except RolloutValidationError as exc:
            return [
                f"frozen target manifest is no longer valid: {item}"
                for item in exc.blockers
            ]
        if _digest(target) != str(campaign.target_manifest_digest or ""):
            blockers.append("frozen target manifest content digest drifted")

        try:
            refs = cls._validate_runtime_bundles(
                db,
                target["matrix"],
                list(campaign.runtime_bundle_refs_json or []),
                actor=str(campaign.frozen_by or ""),
            )
        except RolloutValidationError as exc:
            blockers.extend(
                f"live runtime bundle validation failed: {item}"
                for item in exc.blockers
            )
        else:
            runtime_digest = _digest(
                {
                    "schema_version": "rollout-runtime-bundles/v2",
                    "refs": refs,
                }
            )
            if runtime_digest != str(campaign.runtime_bundle_digest or ""):
                blockers.append("frozen runtime bundle set digest drifted")

        try:
            _, context_digest = cls._validate_contexts(
                db,
                target["matrix"],
                list(campaign.comparison_context_ids_json or []),
            )
        except RolloutValidationError as exc:
            blockers.extend(
                f"live comparison context validation failed: {item}"
                for item in exc.blockers
            )
        else:
            if context_digest != str(
                campaign.comparison_context_digest or ""
            ):
                blockers.append("frozen comparison context set digest drifted")
        return list(dict.fromkeys(blockers))

    @staticmethod
    def _assert_attempt_digests(
        campaign: RolloutCampaignRecord,
        attempt: RolloutStageAttemptRecord,
    ) -> None:
        blockers = [
            field
            for field in (
                "target_manifest_digest",
                "runtime_bundle_digest",
                "comparison_context_digest",
            )
            if str(getattr(campaign, field) or "")
            != str(getattr(attempt, field) or "")
        ]
        if blockers:
            raise RolloutValidationError(
                [
                    "stage attempt differs from frozen campaign: "
                    + ", ".join(blockers)
                ],
                code="new_campaign_required",
            )

    def _create_attempt(
        self,
        db: Any,
        campaign: RolloutCampaignRecord,
        stage: str,
        *,
        now: datetime,
    ) -> RolloutStageAttemptRecord:
        if stage not in ROLLOUT_STAGES:
            raise RolloutValidationError([f"unsupported rollout stage: {stage}"])
        latest = (
            db.query(RolloutStageAttemptRecord.attempt_number)
            .filter(
                RolloutStageAttemptRecord.campaign_id == campaign.id,
                RolloutStageAttemptRecord.stage == stage,
            )
            .order_by(RolloutStageAttemptRecord.attempt_number.desc())
            .first()
        )
        attempt_number = int(latest[0]) + 1 if latest else 1
        attempt = RolloutStageAttemptRecord(
            id=str(uuid.uuid4()),
            campaign_id=campaign.id,
            stage=stage,
            attempt_number=attempt_number,
            percentage=STAGE_PERCENTAGES[stage],
            status="pending",
            target_manifest_digest=str(campaign.target_manifest_digest),
            runtime_bundle_digest=str(campaign.runtime_bundle_digest),
            comparison_context_digest=str(
                campaign.comparison_context_digest
            ),
            baseline_window_json=dict(campaign.baseline_window_json or {}),
            thresholds_json=dict(campaign.thresholds_json or {}),
            metrics_json=None,
            comparison_result_json=None,
            evidence_refs_json=[],
            blockers_json=[],
            created_at=now,
        )
        db.add(attempt)
        db.flush()
        return attempt

    @staticmethod
    def _pause_campaign(
        campaign: RolloutCampaignRecord,
        attempt: RolloutStageAttemptRecord,
        *,
        actor: str,
        reason: str,
        now: datetime,
        previous_attempt_status: str,
    ) -> None:
        campaign.paused_from_stage = str(attempt.stage)
        campaign.current_stage = str(attempt.stage)
        campaign.pause_context_json = {
            "attempt_id": attempt.id,
            "attempt_status": previous_attempt_status,
            "reason": reason,
        }
        campaign.status = "paused"
        campaign.paused_by = actor
        campaign.paused_at = now
        campaign.last_error = reason
        campaign.updated_at = now
        campaign.lock_version = int(campaign.lock_version) + 1

    @staticmethod
    def _roll_back_campaign(
        campaign: RolloutCampaignRecord,
        attempt: RolloutStageAttemptRecord,
        *,
        actor: str,
        reason: str,
        evidence: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        attempt.status = "rolled_back"
        attempt.blockers_json = list(
            dict.fromkeys(
                [
                    *(attempt.blockers_json or []),
                    reason,
                ]
            )
        )
        attempt.evidence_refs_json = [
            *(attempt.evidence_refs_json or []),
            *evidence,
        ]
        attempt.completed_at = now
        campaign.status = "rolled_back"
        campaign.current_stage = str(attempt.stage)
        campaign.rolled_back_at = now
        campaign.completed_by = actor
        campaign.last_error = reason
        campaign.updated_at = now
        campaign.lock_version = int(campaign.lock_version) + 1

    @staticmethod
    def _locked_campaign(db: Any, campaign_id: str) -> RolloutCampaignRecord:
        campaign = (
            db.query(RolloutCampaignRecord)
            .filter(RolloutCampaignRecord.id == campaign_id)
            .with_for_update()
            .one_or_none()
        )
        if campaign is None:
            raise RolloutValidationError(
                ["rollout campaign not found"],
                code="not_found",
            )
        return campaign

    @staticmethod
    def _attempts(
        db: Any,
        campaign_id: str,
    ) -> list[RolloutStageAttemptRecord]:
        return (
            db.query(RolloutStageAttemptRecord)
            .filter(RolloutStageAttemptRecord.campaign_id == campaign_id)
            .order_by(
                RolloutStageAttemptRecord.created_at,
                RolloutStageAttemptRecord.stage,
                RolloutStageAttemptRecord.attempt_number,
            )
            .all()
        )

    @staticmethod
    def _current_attempt(
        db: Any,
        campaign: RolloutCampaignRecord,
    ) -> RolloutStageAttemptRecord | None:
        if not campaign.current_stage:
            return None
        return (
            db.query(RolloutStageAttemptRecord)
            .filter(
                RolloutStageAttemptRecord.campaign_id == campaign.id,
                RolloutStageAttemptRecord.stage == campaign.current_stage,
            )
            .order_by(RolloutStageAttemptRecord.attempt_number.desc())
            .first()
        )

    @staticmethod
    def _action_seen(
        db: Any,
        campaign_id: str,
        action: str,
        idempotency_key: str,
    ) -> bool:
        if not str(idempotency_key or "").strip():
            raise RolloutValidationError(["idempotency_key is required"])
        return (
            db.query(AuditLogRecord.id)
            .filter(
                AuditLogRecord.idempotency_key
                == _action_key(campaign_id, action, idempotency_key)
            )
            .first()
            is not None
        )

    @staticmethod
    def _request_digest(
        action: str,
        payload: Mapping[str, Any],
    ) -> str:
        return _digest(
            {
                "schema_version": "rollout-request/v1",
                "action": action,
                "payload": dict(payload),
            }
        )

    @staticmethod
    def _action_replayed(
        db: Any,
        campaign_id: str,
        action: str,
        idempotency_key: str,
        request_digest: str,
    ) -> bool:
        if not str(idempotency_key or "").strip():
            raise RolloutValidationError(["idempotency_key is required"])
        row = (
            db.query(AuditLogRecord)
            .filter(
                AuditLogRecord.idempotency_key
                == _action_key(campaign_id, action, idempotency_key)
            )
            .one_or_none()
        )
        if row is None:
            return False
        recorded_digest = str(
            dict(row.detail or {}).get("request_digest") or ""
        )
        if not recorded_digest or recorded_digest != request_digest:
            raise RolloutValidationError(
                [
                    "idempotency key was already used with a different "
                    f"{action} request"
                ],
                code="idempotency_conflict",
            )
        return True

    @staticmethod
    def _audit(
        db: Any,
        *,
        campaign: RolloutCampaignRecord,
        action: str,
        actor: str,
        idempotency_key: str,
        detail: Mapping[str, Any],
    ) -> None:
        db.add(
            AuditLogRecord(
                id=str(uuid.uuid4()),
                idempotency_key=idempotency_key,
                user_id=actor,
                username=actor,
                action=action,
                resource_type="rollout_campaign",
                resource_id=campaign.id,
                detail=dict(detail),
                status="success",
                created_at=_utcnow(),
            )
        )

    def _serialize(
        self,
        db: Any,
        campaign: RolloutCampaignRecord,
    ) -> dict[str, Any]:
        attempts = self._attempts(db, campaign.id)
        executions = (
            db.query(RolloutStageExecutionRecord)
            .filter(
                RolloutStageExecutionRecord.campaign_id == campaign.id
            )
            .order_by(
                RolloutStageExecutionRecord.created_at,
                RolloutStageExecutionRecord.id,
            )
            .all()
        )
        current = self._current_attempt(db, campaign)
        next_action = "none"
        if campaign.status == "draft":
            next_action = "freeze_target"
        elif campaign.status == "paused":
            next_action = "resume_or_rollback"
        elif campaign.status not in {"rolled_back", "completed"}:
            if current is not None:
                next_action = {
                    "pending": "start_stage",
                    "running": "await_stage_execution",
                    "awaiting_approval": "approve_stage",
                    "failed": "resume_or_rollback",
                    "paused": "resume_or_rollback",
                }.get(str(current.status), "none")
        return {
            **campaign.to_dict(),
            "attempts": [item.to_dict() for item in attempts],
            "stage_executions": [item.to_dict() for item in executions],
            "current_attempt": (
                current.to_dict() if current is not None else None
            ),
            "next_action": next_action,
            "stage_sequence": list(ROLLOUT_STAGES),
        }

    def _prepare_stage_execution(
        self,
        db: Any,
        *,
        campaign: RolloutCampaignRecord,
        attempt: RolloutStageAttemptRecord,
        actor: str,
        actor_user_id: str | None = None,
        execution_mode: str = "snapshot_replay",
    ) -> dict[str, Any]:
        from src.apps.rule_governance.backend.check_items.rollout_execution import (
            RolloutStageExecutionError,
            RolloutStageExecutionService,
        )

        try:
            return RolloutStageExecutionService(
                self._session_scope
            ).prepare_in_transaction(
                db,
                campaign=campaign,
                attempt=attempt,
                actor=actor,
                actor_user_id=actor_user_id,
                execution_mode=execution_mode,
            )
        except RolloutStageExecutionError as exc:
            raise RolloutValidationError(
                [str(exc)],
                code=exc.code,
            ) from exc

    @staticmethod
    def _verify_stage_result(
        db: Any,
        *,
        campaign: RolloutCampaignRecord,
        attempt: RolloutStageAttemptRecord,
        metrics: Mapping[str, Any],
        comparisons: Mapping[str, Any],
        evidence_refs: Iterable[Mapping[str, Any]],
    ) -> None:
        execution = (
            db.query(RolloutStageExecutionRecord)
            .filter(
                RolloutStageExecutionRecord.stage_attempt_id == attempt.id
            )
            .one_or_none()
        )
        if (
            execution is None
            or execution.status != "completed"
            or not execution.comparison_manifest_id
        ):
            raise RolloutValidationError(
                [
                    "trusted stage execution has not completed with "
                    "comparison evidence"
                ],
                code="trusted_evidence_incomplete",
            )
        evidence = db.get(
            RolloutComparisonEvidenceRecord,
            execution.comparison_manifest_id,
        )
        if evidence is None:
            raise RolloutValidationError(
                ["trusted comparison evidence is missing"],
                code="trusted_evidence_incomplete",
            )
        manifest = dict(evidence.manifest_json or {})
        blockers: list[str] = []
        if _digest(manifest.get("metrics")) != _digest(dict(metrics)):
            blockers.append("stage metrics differ from immutable evidence")
        if _digest(manifest.get("comparisons")) != _digest(
            dict(comparisons)
        ):
            blockers.append(
                "stage comparisons differ from immutable evidence"
            )
        if manifest.get("raw_cli_included") is not False:
            blockers.append("rollout comparison evidence includes raw CLI")
        matching_ref = any(
            str(item.get("id") or "") == str(evidence.id)
            and str(item.get("sha256") or "") == str(evidence.artifact_sha256)
            and str(item.get("object_key") or "")
            == str(evidence.artifact_object_key)
            for item in evidence_refs
        )
        if not matching_ref:
            blockers.append(
                "stage evidence reference does not match immutable evidence"
            )
        expected_decision_digest = _digest(
            {
                "metrics": dict(metrics),
                "comparisons": dict(comparisons),
                "manifest_digest": str(evidence.manifest_digest),
            }
        )
        if str(execution.decision_digest or "") != expected_decision_digest:
            blockers.append(
                "stage decision digest differs from immutable evidence"
            )
        if blockers:
            raise RolloutValidationError(
                blockers,
                code="trusted_evidence_mismatch",
            )
