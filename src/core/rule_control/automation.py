"""Unattended orchestration for governed rule-asset production."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func

from src.core.celery_tasks.beat_health import (
    CeleryBeatHeartbeatUnavailableError,
    read_celery_beat_heartbeat,
)
from src.core.rule_control.approval import RuleAssetApprovalService
from src.core.rule_control.dataset_context import (
    DatasetRuleGenerationContextService,
    assess_context_quality,
)
from src.core.rule_control.engineering_revision import (
    RuleAssetEngineeringRevisionService,
)
from src.core.rule_control.replay_gate import evaluate_historical_replay_gate
from src.core.rule_control.template_activation import (
    RuleAssetTemplateActivationService,
)
from src.infrastructure.db.models import (
    AuditLogRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    ParserControlSetting,
    RuleAssetCommandUnitRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session

AUTOMATION_SETTING_KEY = "rule_asset_unattended"
AUTOMATION_MODES = {"disabled", "generate_only", "auto_publish"}
ACTIVE_WORKFLOW_STATUSES = {"pending", "queued", "dispatching", "running", "in_progress"}
AUTOMATION_ACTOR = "rule-asset-unattended"

DEFAULT_AUTOMATION_SETTINGS: dict[str, Any] = {
    "mode": "disabled",
    "max_concurrency": 1,
    "max_generation_attempts": 3,
    "retry_delay_seconds": 300,
    "stale_run_seconds": 1800,
    "min_period_count": 2,
    "min_device_count": 2,
    "min_record_count": 10,
    "max_estimated_daily_events": 500,
    "allow_critical": False,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _positive_int(
    value: Any,
    *,
    default: int,
    maximum: int,
    minimum: int = 1,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def validate_automation_settings(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize the persisted control-plane policy."""

    merged = {**DEFAULT_AUTOMATION_SETTINGS, **dict(value or {})}
    mode = str(merged.get("mode") or "disabled").strip().casefold()
    if mode not in AUTOMATION_MODES:
        raise ValueError(f"unsupported rule asset automation mode: {mode}")
    return {
        "mode": mode,
        "max_concurrency": _positive_int(
            merged.get("max_concurrency"), default=1, maximum=10
        ),
        "max_generation_attempts": _positive_int(
            merged.get("max_generation_attempts"), default=3, maximum=5
        ),
        "retry_delay_seconds": _positive_int(
            merged.get("retry_delay_seconds"), default=300, maximum=86_400
        ),
        "stale_run_seconds": _positive_int(
            merged.get("stale_run_seconds"),
            default=1800,
            maximum=86_400,
            minimum=900,
        ),
        "min_period_count": _positive_int(
            merged.get("min_period_count"),
            default=2,
            maximum=10,
            minimum=2,
        ),
        "min_device_count": _positive_int(
            merged.get("min_device_count"), default=2, maximum=10_000
        ),
        "min_record_count": _positive_int(
            merged.get("min_record_count"), default=10, maximum=500
        ),
        "max_estimated_daily_events": _positive_int(
            merged.get("max_estimated_daily_events"), default=500, maximum=1_000_000
        ),
        "allow_critical": bool(merged.get("allow_critical")),
    }


class RuleAssetAutomationSettingsRepository:
    """Persist runtime automation policy and auditable scheduler outcomes."""

    def get(self) -> dict[str, Any]:
        with get_db_session() as db:
            row = db.get(ParserControlSetting, AUTOMATION_SETTING_KEY)
            payload = dict(row.value_json or {}) if row else {}
        settings = validate_automation_settings(payload)
        return {
            **settings,
            "updated_at": payload.get("updated_at"),
            "updated_by": payload.get("updated_by"),
            "last_run": dict(payload.get("last_run") or {}),
        }

    def set(
        self,
        value: dict[str, Any],
        *,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        if not str(actor or "").strip():
            raise ValueError("actor is required")
        if len(str(reason or "").strip()) < 5:
            raise ValueError("automation policy reason must contain at least 5 characters")
        normalized = validate_automation_settings(value)
        now = _now()
        with get_db_session() as db:
            row = db.get(ParserControlSetting, AUTOMATION_SETTING_KEY)
            previous = validate_automation_settings(dict(row.value_json or {}) if row else {})
            last_run = dict((row.value_json or {}).get("last_run") or {}) if row else {}
            payload = {
                **normalized,
                "updated_at": now.isoformat(),
                "updated_by": str(actor),
                "last_run": last_run,
            }
            if row is None:
                row = ParserControlSetting(
                    key=AUTOMATION_SETTING_KEY,
                    value_json=payload,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.value_json = payload
                row.updated_at = now
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=str(actor),
                    username=str(actor),
                    action="rule_asset_automation_changed",
                    resource_type="parser_control_setting",
                    resource_id=AUTOMATION_SETTING_KEY,
                    detail={
                        "reason": str(reason).strip(),
                        "from": previous,
                        "to": normalized,
                    },
                    status="success",
                    created_at=now,
                )
            )
        return self.get()

    def record_run(self, result: dict[str, Any]) -> None:
        now = _now()
        with get_db_session() as db:
            row = db.get(ParserControlSetting, AUTOMATION_SETTING_KEY)
            payload = dict(row.value_json or {}) if row else dict(DEFAULT_AUTOMATION_SETTINGS)
            payload["last_run"] = {
                "ran_at": now.isoformat(),
                "success": result.get("success") is True,
                "mode": result.get("mode"),
                "dispatched_count": int(result.get("dispatched_count") or 0),
                "published_count": int(result.get("published_count") or 0),
                "review_required_count": int(result.get("review_required_count") or 0),
                "failed_count": int(result.get("failed_count") or 0),
                "summary": str(result.get("summary") or "")[:500],
            }
            if row is None:
                row = ParserControlSetting(
                    key=AUTOMATION_SETTING_KEY,
                    value_json=payload,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.value_json = payload
                row.updated_at = now

    def status(self) -> dict[str, Any]:
        settings = self.get()
        try:
            scheduler = read_celery_beat_heartbeat().to_dict()
        except CeleryBeatHeartbeatUnavailableError as exc:
            scheduler = {
                "status": "unavailable",
                "recorded_at": None,
                "age_seconds": None,
                "worker_id": None,
                "error": str(exc),
            }
        with get_db_session() as db:
            unit_counts = {
                str(status): int(count)
                for status, count in db.query(
                    RuleAssetCommandUnitRecord.status,
                    func.count(RuleAssetCommandUnitRecord.id),
                )
                .group_by(RuleAssetCommandUnitRecord.status)
                .all()
            }
            active_runs = (
                db.query(WorkflowRun)
                .filter(
                    WorkflowRun.template_name == "rule-asset-generation",
                    WorkflowRun.status.in_(ACTIVE_WORKFLOW_STATUSES),
                )
                .count()
            )
        return {
            "settings": settings,
            "scheduler": scheduler,
            "active_generation_runs": int(active_runs),
            "command_units": unit_counts,
        }


def build_unattended_intents(context: dict[str, Any]) -> dict[str, str]:
    """Provide generic operational intent without vendor or command hard-coding."""

    profiles = dict(context.get("field_profiles") or {})
    numeric_fields = sorted(
        field
        for field, profile in profiles.items()
        if isinstance(profile, dict) and int(profile.get("numeric_count") or 0) > 0
    )
    varying_fields = sorted(
        field
        for field, profile in profiles.items()
        if isinstance(profile, dict) and int(profile.get("distinct_count") or 0) > 1
    )
    evidence = (
        f"Numeric fields: {', '.join(numeric_fields[:20]) or 'none'}. "
        f"Fields varying across samples: {', '.join(varying_fields[:20]) or 'none'}."
    )
    return {
        "policy_intent": (
            "Generate a ChangePolicy for every planner-approved monitored observation. "
            "Use device-provided structured threshold references first. Otherwise use "
            "historical distributions only when the records contain enough variation; "
            "include hysteresis for threshold recovery and avoid inventing physical limits. "
            + evidence
        ),
        "event_intent": (
            "For every planner-approved event target, decide its operational fault "
            "direction from the validated semantic type and ChangePolicy, then generate "
            "a fault rule and a matching recovery rule. High utilization/usage/error "
            "measurements, low remaining/free/idle/health measurements, unhealthy state "
            "transitions, and breached structured warning/alarm references are actionable. "
            "Unexpected software, firmware, boot-image, or hardware version transitions are "
            "actionable lifecycle-change events and do not require a recovery pair. Stable "
            "inventory, descriptive metadata, identifiers, and ordinary change-only fields "
            "are not events. Event types must be stable English identifiers. Never invent "
            "thresholds that disagree with the ChangePolicy. "
            + evidence
        ),
    }


def evaluate_unattended_publish_gate(
    *,
    bundle: RuleGenerationJobRecord,
    context: RuleGenerationContextRecord,
    seed: RuleGenerationJobRecord,
    unit: RuleAssetCommandUnitRecord | None,
    settings: dict[str, Any],
    children: list[RuleGenerationJobRecord],
) -> dict[str, Any]:
    """Apply stricter gates than interactive approval before automatic release."""

    report = dict(bundle.validation_report_json or {})
    bundle_gate = dict(report.get("bundle_gate") or {})
    context_gate = dict(report.get("context_gate") or {})
    replay = dict(report.get("historical_replay") or {})
    replay_gate = evaluate_historical_replay_gate(replay, context_gate)
    privacy = dict(context.privacy_report_json or {})
    counts = dict(bundle_gate.get("asset_counts") or {})
    blockers: list[str] = []

    if settings.get("mode") != "auto_publish":
        blockers.append("automation mode does not permit automatic publishing")
    if bundle.status != "pending_approval":
        blockers.append("bundle is not pending approval")
    if report.get("success") is not True or bundle_gate.get("success") is not True:
        blockers.append("bundle deterministic validation failed")
    if context_gate.get("success") is not True:
        blockers.append("immutable context gate failed")
    if replay_gate.get("success") is not True:
        blockers.extend(str(item) for item in replay_gate.get("errors") or [])
    if privacy.get("success") is not True or privacy.get("raw_cli_included") is not False:
        blockers.append("privacy report did not prove a raw-CLI-free context")
    if context.offline_job_id is None or privacy.get("scope_type") != (
        "offline_period_authoritative_runs"
    ):
        blockers.append("context is not backed by an offline patrol asset-source job")
    if len(context.dataset_ids_json or []) < int(settings["min_period_count"]):
        blockers.append("insufficient independent patrol periods")
    if int(privacy.get("sampled_record_count") or 0) < int(settings["min_record_count"]):
        blockers.append("insufficient sanitized structured records")
    if unit is None:
        blockers.append("command production unit is missing")
    elif int(unit.device_count or 0) < int(settings["min_device_count"]):
        blockers.append("insufficient device coverage")
    if int(counts.get("OBSERVATION_CONTRACT") or 0) != 1:
        blockers.append("bundle must contain exactly one ObservationContract")
    if int(counts.get("CHANGE_POLICY") or 0) < 1:
        blockers.append("bundle contains no ChangePolicy")
    if int(counts.get("EVENT_RULE") or 0) < 1:
        blockers.append("bundle contains no EventRule")
    if int(replay.get("matched_change_count") or 0) < 1:
        blockers.append("historical replay matched no Change")
    if int(replay.get("collision_count") or 0) > 0:
        blockers.append("historical replay has collisions")
    estimated = float(replay.get("estimated_daily_event_count") or 0)
    if estimated > float(settings["max_estimated_daily_events"]):
        blockers.append("estimated daily event volume exceeds the automation limit")
    if not settings.get("allow_critical"):
        critical = [
            child.id
            for child in children
            if child.job_type == "EVENT_RULE"
            and str(((child.payload_json or {}).get("rule") or {}).get("severity") or "").casefold()
            == "critical"
        ]
        if critical:
            blockers.append("critical EventRules require human approval")

    automation = dict((seed.payload_json or {}).get("automation") or {})
    if automation.get("requested_mode") != "auto_publish":
        blockers.append("seed was not dispatched under auto-publish mode")
    return {
        "success": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "matched_change_count": int(replay.get("matched_change_count") or 0),
        "collision_count": int(replay.get("collision_count") or 0),
        "estimated_daily_event_count": estimated,
        "dataset_count": len(context.dataset_ids_json or []),
        "device_count": int(unit.device_count or 0) if unit else 0,
        "record_count": int(privacy.get("sampled_record_count") or 0),
    }


class RuleAssetAutomationService:
    """Continuously advance offline rule assets and preserve human takeover."""

    def __init__(
        self,
        *,
        settings_repository: RuleAssetAutomationSettingsRepository | None = None,
        context_service: DatasetRuleGenerationContextService | None = None,
        activation_service: RuleAssetTemplateActivationService | None = None,
        review_service: RuleAssetEngineeringRevisionService | None = None,
        approval_service: RuleAssetApprovalService | None = None,
    ) -> None:
        self.settings_repository = (
            settings_repository or RuleAssetAutomationSettingsRepository()
        )
        self.context_service = context_service or DatasetRuleGenerationContextService()
        self.activation_service = activation_service or RuleAssetTemplateActivationService()
        self.review_service = review_service or RuleAssetEngineeringRevisionService()
        self.approval_service = approval_service or RuleAssetApprovalService()

    def run_once(self, *, limit: int | None = None, force: bool = False) -> dict[str, Any]:
        settings = self.settings_repository.get()
        mode = str(settings["mode"])
        if mode == "disabled" and not force:
            result = {
                "success": True,
                "mode": mode,
                "skipped": True,
                "reason": "rule asset automation is disabled",
                "dispatched_count": 0,
                "published_count": 0,
                "review_required_count": 0,
                "failed_count": 0,
            }
            self.settings_repository.record_run(result)
            return result

        batch_limit = max(
            1,
            min(
                int(limit or settings["max_concurrency"]),
                int(settings["max_concurrency"]),
                10,
            ),
        )
        recovered = self._reconcile_stale_runs(settings=settings, limit=batch_limit * 4)
        advanced = self._advance_pending_bundles(settings=settings, limit=batch_limit)
        available_slots = max(0, batch_limit - int(advanced["published_count"]))
        dispatched = self._dispatch_ready_seeds(
            settings=settings,
            limit=available_slots,
        )
        failed_count = len(advanced["failed"]) + len(dispatched["failed"])
        review_required = [
            *advanced["review_required"],
            *dispatched["review_required"],
        ]
        result = {
            "success": failed_count == 0,
            "mode": mode,
            "dispatched": dispatched["dispatched"],
            "published": advanced["published"],
            "review_required": review_required,
            "recovered": recovered["recovered"],
            "failed": [*advanced["failed"], *dispatched["failed"]],
            "dispatched_count": len(dispatched["dispatched"]),
            "published_count": len(advanced["published"]),
            "review_required_count": len(review_required),
            "failed_count": failed_count,
            "summary": (
                f"dispatched={len(dispatched['dispatched'])}, "
                f"published={len(advanced['published'])}, "
                f"review_required={len(review_required)}, "
                f"recovered={len(recovered['recovered'])}, "
                f"failed={failed_count}"
            ),
        }
        self.settings_repository.record_run(result)
        return result

    @staticmethod
    def _reconcile_stale_runs(
        *,
        settings: dict[str, Any],
        limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        """Recover orphaned automation workflows without racing healthy LLM calls."""

        now = _now()
        stale_seconds = int(settings["stale_run_seconds"])
        cutoff = now - timedelta(seconds=stale_seconds)
        recovered: list[dict[str, Any]] = []
        offline_job_ids: set[str] = set()
        with get_db_session() as db:
            seeds = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.job_type == "OBSERVATION_CONTRACT")
                .order_by(
                    RuleGenerationJobRecord.updated_at,
                    RuleGenerationJobRecord.id,
                )
                .limit(max(20, min(limit * 20, 500)))
                .all()
            )
            for seed in seeds:
                payload = dict(seed.payload_json or {})
                automation = dict(payload.get("automation") or {})
                if automation.get("managed_by") != AUTOMATION_ACTOR:
                    continue
                run_id = str(payload.get("latest_generation_run_id") or "")
                if not run_id:
                    continue
                run = db.get(WorkflowRun, run_id)
                run_updated_at = _as_utc(run.updated_at) if run else None
                if (
                    run is None
                    or run.status not in ACTIVE_WORKFLOW_STATUSES
                    or run_updated_at is None
                    or run_updated_at >= cutoff
                ):
                    continue

                message = (
                    "Unattended rule asset workflow made no progress for more than "
                    f"{stale_seconds} seconds and was recovered"
                )
                previous_status = str(run.status or "unknown")
                previous_updated_at = run.updated_at
                run.status = "failed"
                run.error_message = message
                run.completed_at = now
                run.updated_at = now
                run.context = {
                    **dict(run.context or {}),
                    "failure_code": "rule_asset_workflow_heartbeat_expired",
                    "retryable": True,
                    "reconciled_at": now.isoformat(),
                    "stale_previous_status": previous_status,
                    "stale_last_updated_at": _iso(previous_updated_at),
                }
                active_steps = (
                    db.query(WorkflowStepRecord)
                    .filter(
                        WorkflowStepRecord.run_id == run_id,
                        WorkflowStepRecord.status.in_(["queued", "running"]),
                    )
                    .all()
                )
                for step in active_steps:
                    step.status = "failed"
                    step.error_message = message
                    step.completed_at = now

                attempts = int(automation.get("attempt_count") or 0)
                exhausted = attempts >= int(settings["max_generation_attempts"])
                delay = int(settings["retry_delay_seconds"]) * (
                    2 ** max(0, attempts - 1)
                )
                automation.update(
                    status="needs_review" if exhausted else "retry_scheduled",
                    last_error=message,
                    failed_at=now.isoformat(),
                    next_retry_at=None
                    if exhausted
                    else (now + timedelta(seconds=delay)).isoformat(),
                    lease_until=None,
                    claim_token=None,
                    blockers=[message],
                )
                payload["automation"] = automation
                seed.payload_json = payload
                seed.updated_at = now
                unit = (
                    db.query(RuleAssetCommandUnitRecord)
                    .filter(RuleAssetCommandUnitRecord.rule_seed_job_id == seed.id)
                    .first()
                )
                if unit and unit.offline_job_id:
                    offline_job_ids.add(str(unit.offline_job_id))
                recovered.append(
                    {
                        "seed_id": str(seed.id),
                        "run_id": run_id,
                        "previous_status": previous_status,
                        "next_action": "human_review" if exhausted else "retry",
                    }
                )
                if len(recovered) >= limit:
                    break

        if offline_job_ids:
            from src.core.rule_control.command_asset_units import (
                RuleAssetCommandUnitService,
            )

            service = RuleAssetCommandUnitService()
            for offline_job_id in sorted(offline_job_ids):
                service.reconcile(offline_job_id)
        return {"recovered": recovered}

    def _advance_pending_bundles(
        self,
        *,
        settings: dict[str, Any],
        limit: int,
    ) -> dict[str, list[Any] | int]:
        candidates = self._pending_bundle_candidates(limit=limit)
        published: list[str] = []
        review_required: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        for candidate in candidates:
            bundle_id = candidate["bundle_id"]
            if settings["mode"] != "auto_publish":
                review_required.append(
                    {
                        "bundle_id": bundle_id,
                        "reason": "automatic generation completed; human release approval required",
                    }
                )
                self._record_seed_state(
                    candidate["seed_id"],
                    status="awaiting_approval",
                    blockers=[],
                )
                continue
            gate = candidate["gate"]
            if not gate["success"]:
                review_required.append(
                    {
                        "bundle_id": bundle_id,
                        "reason": "automatic publish gate blocked",
                        "blockers": gate["blockers"],
                    }
                )
                self._record_seed_state(
                    candidate["seed_id"],
                    status="needs_review",
                    blockers=gate["blockers"],
                )
                continue
            try:
                self.review_service.review(
                    bundle_id,
                    decision="accepted",
                    actor=AUTOMATION_ACTOR,
                    reason=(
                        "无人值守工程复核：脱敏、完整性、历史回放、冲突和事件量门禁均已通过。"
                    ),
                )
                self.approval_service.approve(
                    bundle_id,
                    actor=AUTOMATION_ACTOR,
                    reason=(
                        "无人值守发布：双周期离线证据与严格自动发布门禁全部通过。"
                    ),
                )
                published.append(bundle_id)
                self._record_seed_state(
                    candidate["seed_id"],
                    status="published",
                    blockers=[],
                    bundle_id=bundle_id,
                )
                if candidate.get("offline_job_id"):
                    from src.core.rule_control.command_asset_units import (
                        RuleAssetCommandUnitService,
                    )

                    RuleAssetCommandUnitService().reconcile(
                        str(candidate["offline_job_id"])
                    )
            except Exception as exc:
                failed.append({"bundle_id": bundle_id, "error": str(exc)})
                self._record_seed_state(
                    candidate["seed_id"],
                    status="needs_review",
                    blockers=[str(exc)],
                )
        return {
            "published": published,
            "review_required": review_required,
            "failed": failed,
            "published_count": len(published),
        }

    def _pending_bundle_candidates(self, *, limit: int) -> list[dict[str, Any]]:
        from sqlalchemy.orm import aliased

        Seed = aliased(RuleGenerationJobRecord)
        with get_db_session() as db:
            rows = (
                db.query(
                    RuleGenerationJobRecord,
                    RuleGenerationContextRecord,
                    Seed,
                    RuleAssetCommandUnitRecord,
                )
                .join(
                    RuleGenerationContextRecord,
                    RuleGenerationContextRecord.id
                    == RuleGenerationJobRecord.payload_json["context_id"].as_string(),
                )
                .join(Seed, Seed.id == RuleGenerationContextRecord.job_id)
                .outerjoin(
                    RuleAssetCommandUnitRecord,
                    RuleAssetCommandUnitRecord.rule_seed_job_id == Seed.id,
                )
                .filter(
                    RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE",
                    RuleGenerationJobRecord.status == "pending_approval",
                )
                .order_by(
                    RuleGenerationJobRecord.updated_at,
                    RuleGenerationJobRecord.id,
                )
                .limit(max(1, min(limit * 4, 40)))
                .all()
            )
            candidates: list[dict[str, Any]] = []
            seen: set[str] = set()
            settings = self.settings_repository.get()
            for bundle, context, seed, unit in rows:
                if bundle.id in seen:
                    continue
                automation = dict((seed.payload_json or {}).get("automation") or {})
                if automation.get("managed_by") != AUTOMATION_ACTOR:
                    continue
                child_ids = [
                    str(item)
                    for item in (bundle.payload_json or {}).get("child_job_ids") or []
                ]
                children = (
                    db.query(RuleGenerationJobRecord)
                    .filter(RuleGenerationJobRecord.id.in_(child_ids))
                    .all()
                    if child_ids
                    else []
                )
                gate = evaluate_unattended_publish_gate(
                    bundle=bundle,
                    context=context,
                    seed=seed,
                    unit=unit,
                    settings=settings,
                    children=children,
                )
                candidates.append(
                    {
                        "bundle_id": str(bundle.id),
                        "seed_id": str(seed.id),
                        "offline_job_id": str(context.offline_job_id or ""),
                        "gate": gate,
                    }
                )
                seen.add(str(bundle.id))
                if len(candidates) >= limit:
                    break
            return candidates

    def _dispatch_ready_seeds(
        self,
        *,
        settings: dict[str, Any],
        limit: int,
    ) -> dict[str, list[Any]]:
        if limit <= 0:
            return {"dispatched": [], "review_required": [], "failed": []}
        candidates = self._seed_candidates(limit=max(limit * 5, 10))
        dispatched: list[dict[str, Any]] = []
        review_required: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        for candidate in candidates:
            if len(dispatched) >= limit:
                break
            seed_id = candidate["seed_id"]
            claim = self._claim_seed(seed_id, settings=settings)
            if not claim:
                continue
            try:
                blockers = self._evidence_blockers(candidate, settings=settings)
                if blockers:
                    self._record_seed_state(
                        seed_id,
                        status="needs_evidence",
                        blockers=blockers,
                        release_claim=True,
                    )
                    review_required.append(
                        {
                            "seed_id": seed_id,
                            "offline_job_id": candidate["offline_job_id"],
                            "command": candidate["command"],
                            "reason": "generation evidence gate blocked",
                            "blockers": blockers,
                        }
                    )
                    continue
                prepared = self.context_service.prepare(
                    job_id=seed_id,
                    dataset_ids=candidate["dataset_ids"],
                    created_by=AUTOMATION_ACTOR,
                    max_records=120,
                )
                context = dict(prepared.get("context") or {})
                quality = assess_context_quality(
                    list(context.get("records") or []),
                    str(context.get("field_name") or ""),
                    min_records=int(settings["min_record_count"]),
                )
                if not quality["passed"]:
                    self._record_seed_state(
                        seed_id,
                        status="needs_evidence",
                        blockers=list(quality["reasons"]),
                        release_claim=True,
                    )
                    review_required.append(
                        {
                            "seed_id": seed_id,
                            "offline_job_id": candidate["offline_job_id"],
                            "command": candidate["command"],
                            "reason": "structured context quality gate blocked",
                            "blockers": list(quality["reasons"]),
                        }
                    )
                    continue
                intents = build_unattended_intents(context)
                result = self.activation_service.start_generation_workflow(
                    seed_id,
                    context_id=str(prepared["context_id"]),
                    context_overrides={
                        **intents,
                        "automation_mode": settings["mode"],
                    },
                    actor=AUTOMATION_ACTOR,
                )
                self._record_seed_dispatch(
                    seed_id,
                    run_id=str(result["run_id"]),
                    context_id=str(prepared["context_id"]),
                    requested_mode=str(settings["mode"]),
                )
                dispatched.append(
                    {
                        "seed_id": seed_id,
                        "run_id": result["run_id"],
                        "context_id": prepared["context_id"],
                        "offline_job_id": candidate["offline_job_id"],
                        "command": candidate["command"],
                    }
                )
            except Exception as exc:
                self._record_seed_failure(seed_id, error=str(exc), settings=settings)
                failed.append({"seed_id": seed_id, "error": str(exc)})
        return {
            "dispatched": dispatched,
            "review_required": review_required,
            "failed": failed,
        }

    @staticmethod
    def _seed_candidates(*, limit: int) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(
                    RuleGenerationJobRecord,
                    RuleAssetCommandUnitRecord,
                    OfflinePatrolJobRecord,
                )
                .join(
                    RuleAssetCommandUnitRecord,
                    RuleAssetCommandUnitRecord.rule_seed_job_id
                    == RuleGenerationJobRecord.id,
                )
                .join(
                    OfflinePatrolJobRecord,
                    OfflinePatrolJobRecord.id
                    == RuleAssetCommandUnitRecord.offline_job_id,
                )
                .filter(
                    RuleGenerationJobRecord.job_type == "OBSERVATION_CONTRACT",
                    RuleAssetCommandUnitRecord.asset_mode == "full_chain",
                    RuleAssetCommandUnitRecord.parser_template_id.isnot(None),
                    RuleAssetCommandUnitRecord.status.notin_(["completed", "running"]),
                    OfflinePatrolJobRecord.status == "completed",
                )
                .order_by(
                    RuleAssetCommandUnitRecord.updated_at,
                    RuleAssetCommandUnitRecord.id,
                )
                .limit(max(1, min(limit, 100)))
                .all()
            )
            output: list[dict[str, Any]] = []
            seen: set[str] = set()
            for seed, unit, source_job in rows:
                if seed.id in seen:
                    continue
                periods = (
                    db.query(OfflinePatrolPeriodRecord)
                    .filter(OfflinePatrolPeriodRecord.job_id == source_job.id)
                    .order_by(OfflinePatrolPeriodRecord.observed_at)
                    .all()
                )
                output.append(
                    {
                        "seed_id": str(seed.id),
                        "unit_id": str(unit.id),
                        "offline_job_id": str(source_job.id),
                        "command": str(unit.canonical_command),
                        "sample_count": int(unit.sample_count or 0),
                        "device_count": int(unit.device_count or 0),
                        "dataset_ids": [
                            str(period.dataset_id)
                            for period in periods
                            if period.dataset_id
                        ],
                    }
                )
                seen.add(str(seed.id))
            return output

    @staticmethod
    def _evidence_blockers(
        candidate: dict[str, Any],
        *,
        settings: dict[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        if len(candidate["dataset_ids"]) < int(settings["min_period_count"]):
            blockers.append("双周期 Change/Event 生成需要更多独立采集周期")
        if int(candidate["device_count"]) < int(settings["min_device_count"]):
            blockers.append("设备覆盖数量未达到无人值守门禁")
        if int(candidate["sample_count"]) < int(settings["min_period_count"]):
            blockers.append("命令输出样本数量未达到无人值守门禁")
        return blockers

    @staticmethod
    def _claim_seed(
        seed_id: str,
        *,
        settings: dict[str, Any],
    ) -> bool:
        now = _now()
        with get_db_session() as db:
            row = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.id == seed_id)
                .with_for_update(skip_locked=True)
                .one_or_none()
            )
            if row is None:
                return False
            payload = dict(row.payload_json or {})
            automation = dict(payload.get("automation") or {})
            lease_until = _parse_time(automation.get("lease_until"))
            if lease_until and lease_until > now:
                return False
            next_retry_at = _parse_time(automation.get("next_retry_at"))
            if next_retry_at and next_retry_at > now:
                return False
            attempts = int(automation.get("attempt_count") or 0)
            if attempts >= int(settings["max_generation_attempts"]):
                return False
            bundle_id = str(payload.get("latest_bundle_id") or "")
            if bundle_id:
                bundle = db.get(RuleGenerationJobRecord, bundle_id)
                if bundle and bundle.status in {"pending_approval", "active"}:
                    return False
            run_id = str(payload.get("latest_generation_run_id") or "")
            if run_id:
                run = db.get(WorkflowRun, run_id)
                if run and run.status in ACTIVE_WORKFLOW_STATUSES:
                    return False
            automation.update(
                managed_by=AUTOMATION_ACTOR,
                status="preparing",
                claim_token=str(uuid.uuid4()),
                claimed_at=now.isoformat(),
                lease_until=(now + timedelta(minutes=10)).isoformat(),
                blockers=[],
            )
            payload["automation"] = automation
            row.payload_json = payload
            row.updated_at = now
            return True

    @staticmethod
    def _record_seed_dispatch(
        seed_id: str,
        *,
        run_id: str,
        context_id: str,
        requested_mode: str,
    ) -> None:
        now = _now()
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, seed_id)
            if row is None:
                return
            payload = dict(row.payload_json or {})
            automation = dict(payload.get("automation") or {})
            automation.update(
                managed_by=AUTOMATION_ACTOR,
                requested_mode=requested_mode,
                status="generation_running",
                attempt_count=int(automation.get("attempt_count") or 0) + 1,
                latest_run_id=run_id,
                latest_context_id=context_id,
                dispatched_at=now.isoformat(),
                lease_until=None,
                claim_token=None,
                last_error=None,
                next_retry_at=None,
                blockers=[],
            )
            payload["automation"] = automation
            row.payload_json = payload
            row.updated_at = now

    @staticmethod
    def _record_seed_failure(
        seed_id: str,
        *,
        error: str,
        settings: dict[str, Any],
    ) -> None:
        now = _now()
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, seed_id)
            if row is None:
                return
            payload = dict(row.payload_json or {})
            automation = dict(payload.get("automation") or {})
            attempts = int(automation.get("attempt_count") or 0) + 1
            delay = int(settings["retry_delay_seconds"]) * (2 ** max(0, attempts - 1))
            exhausted = attempts >= int(settings["max_generation_attempts"])
            automation.update(
                managed_by=AUTOMATION_ACTOR,
                status="needs_review" if exhausted else "retry_scheduled",
                attempt_count=attempts,
                last_error=str(error)[:2000],
                failed_at=now.isoformat(),
                next_retry_at=None
                if exhausted
                else (now + timedelta(seconds=delay)).isoformat(),
                lease_until=None,
                claim_token=None,
                blockers=[str(error)[:500]],
            )
            payload["automation"] = automation
            row.payload_json = payload
            row.updated_at = now

    @staticmethod
    def _record_seed_state(
        seed_id: str,
        *,
        status: str,
        blockers: list[str],
        release_claim: bool = False,
        bundle_id: str | None = None,
    ) -> None:
        now = _now()
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, seed_id)
            if row is None:
                return
            payload = dict(row.payload_json or {})
            automation = dict(payload.get("automation") or {})
            automation.update(
                managed_by=AUTOMATION_ACTOR,
                status=status,
                blockers=[str(item)[:500] for item in blockers],
                updated_at=now.isoformat(),
            )
            if release_claim:
                automation["lease_until"] = None
                automation["claim_token"] = None
            if bundle_id:
                automation["published_bundle_id"] = bundle_id
            payload["automation"] = automation
            row.payload_json = payload
            row.updated_at = now
