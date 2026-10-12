"""Transactional approval service for generated rule assets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.apps.rule_governance.backend.rule_control.asset_requirements import (
    evaluate_requirement_replay_gate,
    normalize_rule_asset_requirements,
    rule_asset_composition_errors,
)
from src.apps.rule_governance.backend.rule_control.jobs import RuleGenerationJobStateError
from src.apps.rule_governance.backend.rule_control.source_scope import (
    require_current_rule_generation_context,
)
from src.common.governance import actor_matches_any
from src.core.patrol.event_rules import event_rule_logical_key
from src.infrastructure.db.models import (
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _merge_governance(
    report: dict[str, Any] | None,
    governance: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(report or {})
    current = dict(merged.get("governance") or {})
    current.update({key: value for key, value in governance.items() if value is not None})
    if current:
        merged["governance"] = current
    return merged


def _event_rule_payload(
    rule: EventRuleRecord,
    version: EventRuleVersionRecord,
) -> dict[str, Any]:
    return {
        **rule.to_dict(),
        "rule_id": rule.id,
        "rule_name": rule.name,
        "version_id": version.id,
        "version": version.version,
        "event_type": version.event_type,
        "severity": version.severity,
        "condition": version.condition_json,
        "evaluation_scope": version.evaluation_scope,
        "version_status": version.status,
        "activated_at": version.activated_at,
        "deprecated_at": version.deprecated_at,
    }


def _event_rule_identity(
    rule: EventRuleRecord,
    version: EventRuleVersionRecord,
) -> str:
    return event_rule_logical_key(
        name=str(rule.name or ""),
        event_type=str(version.event_type or ""),
        condition=dict(version.condition_json or {}),
        event_class=str(version.event_class or "fault"),
    )


def _active_event_rule_predecessors(
    db: Any,
    *,
    candidate_rule: EventRuleRecord,
    candidate_version: EventRuleVersionRecord,
) -> tuple[str, list[tuple[EventRuleRecord, EventRuleVersionRecord]]]:
    logical_key = _event_rule_identity(candidate_rule, candidate_version)
    active_rows = (
        db.query(EventRuleRecord, EventRuleVersionRecord)
        .join(
            EventRuleVersionRecord,
            EventRuleVersionRecord.rule_id == EventRuleRecord.id,
        )
        .filter(
            EventRuleVersionRecord.status == "active",
            EventRuleVersionRecord.id != candidate_version.id,
        )
        .order_by(EventRuleRecord.name, EventRuleVersionRecord.version)
        .with_for_update()
        .all()
    )
    predecessors = [
        (rule, version)
        for rule, version in active_rows
        if rule.id == candidate_rule.id or _event_rule_identity(rule, version) == logical_key
    ]
    return logical_key, predecessors


def _job_asset_is_active(db: Any, job: RuleGenerationJobRecord) -> bool:
    model = {
        "CHANGE_POLICY": ChangePolicyVersionRecord,
        "EVENT_RULE": EventRuleVersionRecord,
    }.get(str(job.job_type or ""))
    if model is None or not job.asset_version_id:
        return False
    row = db.get(model, str(job.asset_version_id))
    return row is not None and str(row.status or "") == "active"


def _bundle_id_from_source_ref(source_ref: str | None) -> str | None:
    prefix = "rule_asset_bundle:"
    value = str(source_ref or "")
    return value[len(prefix) :] if value.startswith(prefix) else None


def _supersede_replaced_jobs(
    db: Any,
    *,
    source_job_ids: set[str],
    replacement_bundle_id: str,
    now: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Keep control-plane job state aligned with the runtime assets it governs."""

    job_ids = sorted({str(item) for item in source_job_ids if str(item)})
    jobs = (
        db.query(RuleGenerationJobRecord)
        .filter(RuleGenerationJobRecord.id.in_(job_ids))
        .order_by(RuleGenerationJobRecord.id)
        .with_for_update()
        .all()
        if job_ids
        else []
    )
    changed_jobs: list[dict[str, Any]] = []
    parent_bundle_ids: set[str] = set()
    for job in jobs:
        parent_id = _bundle_id_from_source_ref(job.source_ref)
        if parent_id and parent_id != replacement_bundle_id:
            parent_bundle_ids.add(parent_id)
        if job.status == "active" and not _job_asset_is_active(db, job):
            changed_jobs.append({"job_id": job.id, "status_before": job.status})
            job.status = "deprecated"
            job.validation_report_json = _merge_governance(
                dict(job.validation_report_json or {}),
                {
                    "superseded_by_bundle_id": replacement_bundle_id,
                    "superseded_at": now.isoformat(),
                },
            )
            job.updated_at = now
            job.completed_at = now

    changed_bundles: list[dict[str, Any]] = []
    for parent_id in sorted(parent_bundle_ids):
        parent = (
            db.query(RuleGenerationJobRecord)
            .filter(RuleGenerationJobRecord.id == parent_id)
            .with_for_update()
            .first()
        )
        if parent is None or parent.status != "active":
            continue
        child_ids = [
            str(item)
            for item in dict(parent.payload_json or {}).get("child_job_ids") or []
            if str(item)
        ]
        child_jobs = (
            db.query(RuleGenerationJobRecord)
            .filter(RuleGenerationJobRecord.id.in_(child_ids))
            .order_by(RuleGenerationJobRecord.id)
            .with_for_update()
            .all()
            if child_ids
            else []
        )
        if any(_job_asset_is_active(db, child) for child in child_jobs):
            continue
        for child in child_jobs:
            if child.status != "active":
                continue
            changed_jobs.append({"job_id": child.id, "status_before": child.status})
            child.status = "deprecated"
            child.validation_report_json = _merge_governance(
                dict(child.validation_report_json or {}),
                {
                    "superseded_by_bundle_id": replacement_bundle_id,
                    "superseded_at": now.isoformat(),
                },
            )
            child.updated_at = now
            child.completed_at = now
        changed_bundles.append({"bundle_id": parent.id, "status_before": parent.status})
        parent.status = "deprecated"
        parent.validation_report_json = _merge_governance(
            dict(parent.validation_report_json or {}),
            {
                "superseded_by_bundle_id": replacement_bundle_id,
                "superseded_at": now.isoformat(),
            },
        )
        parent.updated_at = now
        parent.completed_at = now
    return {
        "jobs": list({item["job_id"]: item for item in changed_jobs}.values()),
        "bundles": changed_bundles,
    }


def _restore_superseded_jobs(
    db: Any,
    *,
    activation_snapshot: dict[str, Any],
    rolled_back_bundle_id: str,
    actor: str | None,
    now: datetime,
) -> dict[str, list[str]]:
    restored_jobs: list[str] = []
    for item in activation_snapshot.get("superseded_jobs") or []:
        if not isinstance(item, dict) or not item.get("job_id"):
            continue
        job = (
            db.query(RuleGenerationJobRecord)
            .filter(RuleGenerationJobRecord.id == str(item["job_id"]))
            .with_for_update()
            .first()
        )
        if job is None or not _job_asset_is_active(db, job):
            continue
        job.status = str(item.get("status_before") or "active")
        job.validation_report_json = _merge_governance(
            dict(job.validation_report_json or {}),
            {
                "restored_by_bundle_rollback": rolled_back_bundle_id,
                "restored_by": actor,
                "restored_at": now.isoformat(),
            },
        )
        job.updated_at = now
        restored_jobs.append(job.id)

    restored_bundles: list[str] = []
    for item in activation_snapshot.get("superseded_bundles") or []:
        if not isinstance(item, dict) or not item.get("bundle_id"):
            continue
        parent = (
            db.query(RuleGenerationJobRecord)
            .filter(RuleGenerationJobRecord.id == str(item["bundle_id"]))
            .with_for_update()
            .first()
        )
        if parent is None:
            continue
        child_ids = [
            str(value)
            for value in dict(parent.payload_json or {}).get("child_job_ids") or []
            if str(value)
        ]
        child_jobs = (
            db.query(RuleGenerationJobRecord)
            .filter(RuleGenerationJobRecord.id.in_(child_ids))
            .all()
            if child_ids
            else []
        )
        if not child_jobs or not all(_job_asset_is_active(db, child) for child in child_jobs):
            continue
        parent.status = str(item.get("status_before") or "active")
        parent.validation_report_json = _merge_governance(
            dict(parent.validation_report_json or {}),
            {
                "restored_by_bundle_rollback": rolled_back_bundle_id,
                "restored_by": actor,
                "restored_at": now.isoformat(),
            },
        )
        parent.updated_at = now
        restored_bundles.append(parent.id)
    return {"jobs": restored_jobs, "bundles": restored_bundles}


class RuleAssetApprovalService:
    """Approve or deprecate generated assets and their governance job atomically."""

    @staticmethod
    def _event_rule_version_id(job: RuleGenerationJobRecord) -> str | None:
        if job.asset_type == "EVENT_RULE" and job.asset_version_id:
            return str(job.asset_version_id)
        asset_ref = job.asset_ref
        if not asset_ref:
            return None
        prefix = "event_rule_version:"
        if asset_ref.startswith(prefix):
            return asset_ref[len(prefix) :]
        return None

    @staticmethod
    def _change_policy_version_id(job: RuleGenerationJobRecord) -> str | None:
        if job.asset_type == "CHANGE_POLICY" and job.asset_version_id:
            return str(job.asset_version_id)
        asset_ref = job.asset_ref
        if not asset_ref:
            return None
        prefix = "change_policy_version:"
        if asset_ref.startswith(prefix):
            return asset_ref[len(prefix) :]
        return None

    @staticmethod
    def _bundle_child_ids(job: RuleGenerationJobRecord) -> list[str]:
        payload = dict(job.payload_json or {})
        return [str(item) for item in payload.get("child_job_ids") or [] if str(item)]

    @staticmethod
    def _is_bundle_child(job: RuleGenerationJobRecord) -> bool:
        return str(job.source_ref or "").startswith("rule_asset_bundle:")

    @classmethod
    def _assert_direct_action_allowed(cls, job: RuleGenerationJobRecord) -> None:
        if cls._is_bundle_child(job):
            bundle_id = str(job.source_ref).split(":", 1)[1]
            raise RuleGenerationJobStateError(
                f"bundled rule assets must be governed through bundle {bundle_id}"
            )

    @staticmethod
    def _mark_job_active(
        job: RuleGenerationJobRecord,
        *,
        actor: str | None,
        reason: str | None,
        now: datetime,
    ) -> None:
        job.status = "active"
        job.validation_report_json = _merge_governance(
            dict(job.validation_report_json or {}),
            {
                "approved_by": actor,
                "approved_at": now.isoformat(),
                "approval_reason": reason,
            },
        )
        job.updated_at = now
        job.completed_at = now

    def _approve_bundle(
        self,
        db: Any,
        bundle: RuleGenerationJobRecord,
        *,
        actor: str | None,
        reason: str | None,
        now: datetime,
        allow_same_actor: bool = False,
        automation_authorization: tuple[str, str] | None = None,
        released_with_check_item: str | None = None,
    ) -> dict[str, Any]:
        if bundle.status != "pending_approval":
            raise RuleGenerationJobStateError(
                "rule asset bundle must be pending_approval before approval"
            )
        report = dict(bundle.validation_report_json or {})
        # Rules compiled from a check item are approved with the check item
        # itself: the catalog change that publishes it is their one review.
        if released_with_check_item and (bundle.payload_json or {}).get("flow") != "check_item_semantics":
            raise RuleGenerationJobStateError("only rules compiled from a CheckItem are released with it")
        automated = None
        if automation_authorization:
            from src.apps.rule_governance.backend.rule_control.autopilot_authorization import (
                require_autopilot_release,
            )
            automated = require_autopilot_release(db, automation_authorization, kind="rules", asset_id=bundle.id)
            if (bundle.payload_json or {}).get("flow") != "check_item_semantics":
                raise RuleGenerationJobStateError("automatic release requires human-owned CheckItem semantics")
        gate = dict(report.get("bundle_gate") or {})
        context_gate = dict(report.get("context_gate") or {})
        replay = dict(report.get("historical_replay") or {})
        requirements = normalize_rule_asset_requirements(
            gate.get("asset_requirements")
            or report.get("asset_requirements")
            or (bundle.payload_json or {}).get("asset_requirements")
        )
        replay_gate = evaluate_requirement_replay_gate(
            requirements=requirements,
            replay=replay,
            context_gate=context_gate,
        )
        engineering_review = dict((report.get("governance") or {}).get("engineering_review") or {})
        if (
            report.get("success") is not True
            or gate.get("success") is not True
            or context_gate.get("success") is not True
            or replay_gate.get("success") is not True
            or (
                engineering_review.get("decision") != "accepted"
                and not automated
                and not released_with_check_item
            )
        ):
            raise RuleGenerationJobStateError(
                "rule asset bundle cannot be approved before validation, replay, "
                "engineering review and all release gates pass"
            )
        source_integrity: dict[str, Any] | None = None
        context_mode = str(context_gate.get("mode") or "")
        if context_mode == "immutable_dataset_context":
            gate_context_id = str(context_gate.get("context_id") or "").strip()
            payload_context_id = str(
                (bundle.payload_json or {}).get("context_id") or ""
            ).strip()
            if (
                not gate_context_id
                or not payload_context_id
                or gate_context_id != payload_context_id
            ):
                raise RuleGenerationJobStateError(
                    "rule asset bundle immutable context identity is inconsistent"
                )
            context = (
                db.query(RuleGenerationContextRecord)
                .filter(RuleGenerationContextRecord.id == gate_context_id)
                .with_for_update()
                .one_or_none()
            )
            if context is None:
                raise RuleGenerationJobStateError(
                    "rule asset bundle immutable context no longer exists"
                )
            try:
                source_integrity = require_current_rule_generation_context(
                    db,
                    context,
                    expected_context_digest=str(
                        context_gate.get("context_digest")
                        or (bundle.payload_json or {}).get("context_digest")
                        or ""
                    ),
                )
            except (KeyError, ValueError) as exc:
                raise RuleGenerationJobStateError(
                    f"rule asset bundle source evidence is no longer releasable: {exc}"
                ) from exc
        elif context_mode not in {"legacy_manual", "check_item_semantics"}:
            raise RuleGenerationJobStateError(
                "rule asset bundle has no governed release context"
            )
        child_ids = self._bundle_child_ids(bundle)
        if not child_ids:
            raise RuleGenerationJobStateError("rule asset bundle has no child jobs")
        if len(child_ids) != len(set(child_ids)):
            raise RuleGenerationJobStateError("rule asset bundle contains duplicate child jobs")
        children = (
            db.query(RuleGenerationJobRecord)
            .filter(RuleGenerationJobRecord.id.in_(child_ids))
            .order_by(RuleGenerationJobRecord.id)
            .with_for_update()
            .all()
        )
        if len(children) != len(set(child_ids)):
            raise RuleGenerationJobStateError("rule asset bundle child jobs are incomplete")
        same_as_creator = actor_matches_any(
            actor,
            [bundle.created_by, *(child.created_by for child in children)],
        )
        if same_as_creator and not allow_same_actor:
            raise RuleGenerationJobStateError(
                "rule asset approval requires an approver different from "
                "the candidate creator"
            )
        same_as_engineering_reviewer = actor_matches_any(
            actor, [engineering_review.get("actor")]
        )
        if same_as_engineering_reviewer and not allow_same_actor:
            raise RuleGenerationJobStateError(
                "rule asset approval requires an approver different from "
                "the engineering reviewer"
            )
        expected_source = f"rule_asset_bundle:{bundle.id}"
        for child in children:
            if child.source_ref != expected_source:
                raise RuleGenerationJobStateError("rule asset bundle child ownership mismatch")
            if child.status != "pending_approval":
                raise RuleGenerationJobStateError(
                    f"bundle child {child.id} is not pending_approval"
                )
            if (child.validation_report_json or {}).get("success") is not True:
                raise RuleGenerationJobStateError(
                    f"bundle child {child.id} validation has not succeeded"
                )

        by_type: dict[str, list[RuleGenerationJobRecord]] = {}
        for child in children:
            by_type.setdefault(str(child.job_type), []).append(child)
        event_version_ids = [
            str(child.asset_version_id)
            for child in by_type.get("EVENT_RULE") or []
            if child.asset_version_id
        ]
        event_versions = (
            db.query(EventRuleVersionRecord)
            .filter(EventRuleVersionRecord.id.in_(event_version_ids))
            .all()
            if event_version_ids
            else []
        )
        composition_errors = rule_asset_composition_errors(
            counts={
                asset_type: len(items)
                for asset_type, items in by_type.items()
            },
            requirements=requirements,
            change_policy_roles=[
                dict((child.payload_json or {}).get("policy") or {})
                for child in by_type.get("CHANGE_POLICY") or []
            ],
            event_rule_roles=[
                {
                    "evaluation_scope": item.evaluation_scope,
                    "event_class": item.event_class,
                    "event_type": item.event_type,
                    "observation_types": list(
                        dict(item.condition_json or {}).get(
                            "observation_types"
                        )
                        or []
                    ),
                }
                for item in event_versions
            ],
        )
        if composition_errors:
            raise RuleGenerationJobStateError(
                "; ".join(composition_errors)
            )

        activations: list[dict[str, Any]] = []
        activation_snapshot: dict[str, Any] = {
            "captured_at": now.isoformat(),
            "bundle_id": bundle.id,
            "assets": [],
            "source_integrity": source_integrity,
            "asset_requirements": requirements,
        }
        replaced_source_job_ids: set[str] = set()
        if by_type.get("OBSERVATION_CONTRACT"):
            raise RuleGenerationJobStateError("旧数据契约候选已退役，请从检查项重新制作规则")

        for policy_job in by_type.get("CHANGE_POLICY") or []:
            policy_version = (
                db.query(ChangePolicyVersionRecord)
                .filter(ChangePolicyVersionRecord.id == policy_job.asset_version_id)
                .with_for_update()
                .first()
            )
            if policy_version is None:
                raise ValueError("change policy version not found")
            if str(policy_version.digest or "") != str(policy_job.asset_digest or ""):
                raise RuleGenerationJobStateError(
                    "change policy digest no longer matches the approved candidate"
                )
            if policy_version.status not in {"draft", "pending_approval", "active"}:
                raise RuleGenerationJobStateError(
                    f"change policy is not releasable from status {policy_version.status}"
                )
            policy = (
                db.query(ChangePolicyRecord)
                .filter(ChangePolicyRecord.id == policy_version.policy_id)
                .with_for_update()
                .first()
            )
            if policy is None:
                raise ValueError("change policy not found")
            if str(policy.id) != str(policy_job.asset_id or ""):
                raise RuleGenerationJobStateError("change policy asset identity mismatch")
            previous_policy_versions = (
                db.query(ChangePolicyVersionRecord)
                .filter(
                    ChangePolicyVersionRecord.observation_type == policy_version.observation_type,
                    ChangePolicyVersionRecord.status == "active",
                    ChangePolicyVersionRecord.id != policy_version.id,
                )
                .order_by(ChangePolicyVersionRecord.version)
                .with_for_update()
                .all()
            )
            activation_snapshot["assets"].append(
                {
                    "asset_type": "CHANGE_POLICY",
                    "candidate_version_id": policy_version.id,
                    "candidate_status_before": policy_version.status,
                    "logical_key": policy_version.observation_type,
                    "previous_active_version_ids": [item.id for item in previous_policy_versions],
                }
            )
            for previous in previous_policy_versions:
                if previous.source_job_id:
                    replaced_source_job_ids.add(str(previous.source_job_id))
                previous.status = "deprecated"
                previous.deprecated_at = now
                previous_policy = db.get(ChangePolicyRecord, previous.policy_id)
                if previous_policy is not None and int(previous_policy.current_version or 0) == int(
                    previous.version
                ):
                    previous_policy.current_version = 0
                    previous_policy.status = "deprecated"
                    previous_policy.updated_at = now
            if previous_policy_versions:
                db.flush()
            policy_version.status = "active"
            policy_version.activated_at = now
            policy_version.deprecated_at = None
            policy.status = "active"
            policy.current_version = int(policy_version.version)
            activations.append(
                {
                    "asset_type": "CHANGE_POLICY",
                    "asset_id": policy.id,
                    "version_id": policy_version.id,
                    "version": policy_version.version,
                }
            )

        candidate_event_logical_keys: set[str] = set()
        for event_job in by_type.get("EVENT_RULE") or []:
            version = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.id == event_job.asset_version_id)
                .with_for_update()
                .first()
            )
            if version is None:
                raise ValueError("event rule version not found")
            if str(version.digest or "") != str(event_job.asset_digest or ""):
                raise RuleGenerationJobStateError(
                    "event rule digest no longer matches the approved candidate"
                )
            if version.status not in {"draft", "pending_approval", "active"}:
                raise RuleGenerationJobStateError(
                    f"event rule is not releasable from status {version.status}"
                )
            rule = (
                db.query(EventRuleRecord)
                .filter(EventRuleRecord.id == version.rule_id)
                .with_for_update()
                .first()
            )
            if rule is None:
                raise ValueError("event rule not found")
            if str(rule.id) != str(event_job.asset_id or ""):
                raise RuleGenerationJobStateError("event rule asset identity mismatch")
            logical_key, previous_rule_rows = _active_event_rule_predecessors(
                db,
                candidate_rule=rule,
                candidate_version=version,
            )
            if logical_key in candidate_event_logical_keys:
                raise RuleGenerationJobStateError(
                    f"bundle contains duplicate EventRule logical slot {logical_key}"
                )
            candidate_event_logical_keys.add(logical_key)
            activation_snapshot["assets"].append(
                {
                    "asset_type": "EVENT_RULE",
                    "candidate_version_id": version.id,
                    "candidate_status_before": version.status,
                    "logical_key": logical_key,
                    "previous_active_version_ids": [
                        previous_version.id for _, previous_version in previous_rule_rows
                    ],
                    "previous_active_assets": [
                        {
                            "rule_id": previous_rule.id,
                            "rule_name": previous_rule.name,
                            "version_id": previous_version.id,
                            "version": previous_version.version,
                        }
                        for previous_rule, previous_version in previous_rule_rows
                    ],
                }
            )
            for previous_rule, previous_version in previous_rule_rows:
                if previous_version.source_job_id:
                    replaced_source_job_ids.add(str(previous_version.source_job_id))
                previous_version.status = "deprecated"
                previous_version.deprecated_at = now
                if previous_rule.id != rule.id:
                    previous_rule.current_version = 0
                    previous_rule.status = "deprecated"
                    previous_rule.updated_at = now
            if previous_rule_rows:
                db.flush()
            version.status = "active"
            version.activated_at = now
            version.deprecated_at = None
            rule.status = "active"
            rule.current_version = int(version.version)
            rule.updated_at = now
            activations.append(
                {
                    "asset_type": "EVENT_RULE",
                    "asset_id": rule.id,
                    "version_id": version.id,
                    "version": version.version,
                    "event_type": version.event_type,
                    "logical_key": logical_key,
                }
            )

        superseded = _supersede_replaced_jobs(
            db,
            source_job_ids=replaced_source_job_ids,
            replacement_bundle_id=bundle.id,
            now=now,
        )
        activation_snapshot["superseded_jobs"] = superseded["jobs"]
        activation_snapshot["superseded_bundles"] = superseded["bundles"]

        for child in children:
            self._mark_job_active(child, actor=actor, reason=reason, now=now)
        bundle.validation_report_json = _merge_governance(
            dict(bundle.validation_report_json or {}),
            {
                "activation_snapshot": activation_snapshot,
                "automated_approval": automated,
                "released_with_check_item_change": released_with_check_item,
                "separation_of_duties_override": bool(
                    allow_same_actor
                    and (same_as_creator or same_as_engineering_reviewer)
                ),
            },
        )
        self._mark_job_active(bundle, actor=actor, reason=reason, now=now)
        db.flush()
        return {
            "success": True,
            "job": bundle.to_dict(),
            "bundle_id": bundle.id,
            "child_job_ids": child_ids,
            "asset_activations": activations,
            "activation_snapshot": activation_snapshot,
            "atomic": True,
        }

    def _deprecate_bundle(
        self,
        db: Any,
        bundle: RuleGenerationJobRecord,
        *,
        actor: str | None,
        reason: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        if bundle.status not in {"pending_approval", "active", "rejected"}:
            raise RuleGenerationJobStateError(
                f"invalid rule generation transition: {bundle.status} -> deprecated"
            )
        child_ids = self._bundle_child_ids(bundle)
        children = (
            db.query(RuleGenerationJobRecord)
            .filter(RuleGenerationJobRecord.id.in_(child_ids))
            .order_by(RuleGenerationJobRecord.id)
            .with_for_update()
            .all()
            if child_ids
            else []
        )
        report = dict(bundle.validation_report_json or {})
        governance = dict(report.get("governance") or {})
        activation_snapshot = dict(governance.get("activation_snapshot") or {})
        snapshot_entries = {
            (str(item.get("asset_type") or ""), str(item.get("candidate_version_id") or "")): item
            for item in activation_snapshot.get("assets") or []
            if isinstance(item, dict)
        }
        if bundle.status == "active" and not snapshot_entries:
            raise RuleGenerationJobStateError(
                "active rule asset bundle cannot be rolled back without an activation snapshot"
            )

        def snapshot_entry(asset_type: str, version_id: str) -> dict[str, Any]:
            entry = dict(snapshot_entries.get((asset_type, version_id)) or {})
            if bundle.status == "active" and not entry:
                raise RuleGenerationJobStateError(
                    f"activation snapshot is missing {asset_type} {version_id}"
                )
            return entry

        restored: list[dict[str, Any]] = []
        for child in children:
            if child.job_type == "CHANGE_POLICY" and child.asset_version_id:
                version = (
                    db.query(ChangePolicyVersionRecord)
                    .filter(ChangePolicyVersionRecord.id == child.asset_version_id)
                    .with_for_update()
                    .first()
                )
                if version is not None and version.status != "deleted":
                    entry = snapshot_entry("CHANGE_POLICY", version.id)
                    was_active = version.status == "active"
                    was_active_before = entry.get("candidate_status_before") == "active"
                    if not (was_active and was_active_before):
                        version.status = "deprecated"
                        version.deprecated_at = now
                    policy = (
                        db.query(ChangePolicyRecord)
                        .filter(ChangePolicyRecord.id == version.policy_id)
                        .with_for_update()
                        .first()
                    )
                    previous_rows: list[ChangePolicyVersionRecord] = []
                    if was_active and not was_active_before:
                        previous_ids = [
                            str(item) for item in entry.get("previous_active_version_ids") or []
                        ]
                        previous_rows = (
                            db.query(ChangePolicyVersionRecord)
                            .filter(
                                ChangePolicyVersionRecord.id.in_(previous_ids),
                            )
                            .with_for_update()
                            .all()
                            if previous_ids
                            else []
                        )
                        if len(previous_rows) != len(set(previous_ids)):
                            raise RuleGenerationJobStateError(
                                "rollback snapshot references a missing change policy"
                            )
                        db.flush()
                    for previous in previous_rows:
                        if previous.status == "deleted":
                            raise RuleGenerationJobStateError("历史资产已删除，不能回滚恢复")
                        previous.status = "active"
                        previous.activated_at = now
                        previous.deprecated_at = None
                        restored.append(
                            {
                                "asset_type": "CHANGE_POLICY",
                                "asset_version_id": previous.id,
                            }
                        )
                        previous_policy = db.get(ChangePolicyRecord, previous.policy_id)
                        if previous_policy is not None:
                            previous_policy.current_version = int(previous.version)
                            previous_policy.status = "active"
                            previous_policy.updated_at = now
                    if (
                        policy is not None
                        and was_active
                        and not was_active_before
                        and not previous_rows
                    ):
                        policy.current_version = 0
                        policy.status = "deprecated"
                        policy.updated_at = now
            elif child.job_type == "EVENT_RULE" and child.asset_version_id:
                version = (
                    db.query(EventRuleVersionRecord)
                    .filter(EventRuleVersionRecord.id == child.asset_version_id)
                    .with_for_update()
                    .first()
                )
                if version is not None and version.status != "deleted":
                    entry = snapshot_entry("EVENT_RULE", version.id)
                    was_active = version.status == "active"
                    was_active_before = entry.get("candidate_status_before") == "active"
                    if not (was_active and was_active_before):
                        version.status = "deprecated"
                        version.deprecated_at = now
                    rule = (
                        db.query(EventRuleRecord)
                        .filter(EventRuleRecord.id == version.rule_id)
                        .with_for_update()
                        .first()
                    )
                    previous_rows: list[EventRuleVersionRecord] = []
                    if was_active and not was_active_before:
                        previous_ids = [
                            str(item) for item in entry.get("previous_active_version_ids") or []
                        ]
                        previous_rows = (
                            db.query(EventRuleVersionRecord)
                            .filter(
                                EventRuleVersionRecord.id.in_(previous_ids),
                            )
                            .with_for_update()
                            .all()
                            if previous_ids
                            else []
                        )
                        if len(previous_rows) != len(set(previous_ids)):
                            raise RuleGenerationJobStateError(
                                "rollback snapshot references a missing event rule"
                            )
                        db.flush()
                    for previous in previous_rows:
                        if previous.status == "deleted":
                            raise RuleGenerationJobStateError("历史资产已删除，不能回滚恢复")
                        previous.status = "active"
                        previous.activated_at = now
                        previous.deprecated_at = None
                        previous_rule = (
                            db.query(EventRuleRecord)
                            .filter(EventRuleRecord.id == previous.rule_id)
                            .with_for_update()
                            .first()
                        )
                        if previous_rule is None:
                            raise RuleGenerationJobStateError(
                                "rollback snapshot references an event rule without metadata"
                            )
                        previous_rule.current_version = int(previous.version)
                        previous_rule.status = "active"
                        previous_rule.updated_at = now
                        restored.append(
                            {
                                "asset_type": "EVENT_RULE",
                                "asset_version_id": previous.id,
                                "asset_id": previous.rule_id,
                            }
                        )
                    if rule is not None and was_active and not was_active_before:
                        same_rule_predecessors = [
                            previous for previous in previous_rows if previous.rule_id == rule.id
                        ]
                        if same_rule_predecessors:
                            previous = max(
                                same_rule_predecessors,
                                key=lambda item: item.version,
                            )
                            rule.current_version = int(previous.version)
                            rule.status = "active"
                        else:
                            rule.current_version = 0
                            rule.status = "deprecated"
                        rule.updated_at = now
            child.status = "deprecated"
            child.validation_report_json = _merge_governance(
                dict(child.validation_report_json or {}),
                {
                    "deprecated_by": actor,
                    "deprecated_at": now.isoformat(),
                    "reason": reason,
                    "bundle_id": bundle.id,
                },
            )
            child.updated_at = now
            child.completed_at = now
        restored_governance = _restore_superseded_jobs(
            db,
            activation_snapshot=activation_snapshot,
            rolled_back_bundle_id=bundle.id,
            actor=actor,
            now=now,
        )
        bundle.status = "deprecated"
        bundle.validation_report_json = _merge_governance(
            dict(bundle.validation_report_json or {}),
            {
                "deprecated_by": actor,
                "deprecated_at": now.isoformat(),
                "reason": reason,
                "restored_assets": restored,
                "restored_governance": restored_governance,
            },
        )
        bundle.updated_at = now
        bundle.completed_at = now
        db.flush()
        return {
            "success": True,
            "job": bundle.to_dict(),
            "bundle_id": bundle.id,
            "child_job_ids": child_ids,
            "restored_assets": restored,
            "restored_governance": restored_governance,
            "atomic": True,
        }

    def approve(
        self,
        job_id: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
        allow_same_actor: bool = False,
        automation_authorization: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        now = _utcnow()
        with get_db_session() as db:
            job = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.id == job_id)
                .with_for_update()
                .first()
            )
            if job is None:
                raise KeyError("rule generation job not found")
            if job.job_type == "RULE_ASSET_BUNDLE":
                return self._approve_bundle(
                    db,
                    job,
                    actor=actor,
                    reason=reason,
                    now=now,
                    allow_same_actor=allow_same_actor,
                    automation_authorization=automation_authorization,
                )
            self._assert_direct_action_allowed(job)
            if (
                actor_matches_any(actor, [job.created_by])
                and not allow_same_actor
            ):
                raise RuleGenerationJobStateError(
                    "rule asset approval requires an approver different from "
                    "the candidate creator"
                )
            if job.status != "pending_approval":
                raise RuleGenerationJobStateError(
                    "rule generation job must be pending_approval before approval"
                )
            if not (job.asset_version_id or job.asset_ref):
                raise RuleGenerationJobStateError(
                    "rule asset cannot be approved without asset_ref or asset_version_id"
                )
            validation_report = dict(job.validation_report_json or {})
            if validation_report.get("success") is not True:
                raise RuleGenerationJobStateError(
                    "rule asset cannot be approved without successful validation"
                )

            asset_activation = None
            if job.job_type == "EVENT_RULE":
                version_id = self._event_rule_version_id(job)
                if not version_id:
                    raise RuleGenerationJobStateError(
                        "EVENT_RULE jobs require asset_version_id or asset_ref "
                        "event_rule_version:<id>"
                    )
                version = (
                    db.query(EventRuleVersionRecord)
                    .filter(EventRuleVersionRecord.id == version_id)
                    .with_for_update()
                    .first()
                )
                if version is None:
                    raise ValueError("event rule version not found")
                if version.status == "deleted":
                    raise RuleGenerationJobStateError("事件规则已删除")
                rule = (
                    db.query(EventRuleRecord)
                    .filter(EventRuleRecord.id == version.rule_id)
                    .with_for_update()
                    .first()
                )
                if rule is None:
                    raise ValueError("event rule not found")
                _, predecessors = _active_event_rule_predecessors(
                    db,
                    candidate_rule=rule,
                    candidate_version=version,
                )
                for previous_rule, previous_version in predecessors:
                    previous_version.status = "deprecated"
                    previous_version.deprecated_at = now
                    if previous_rule.id != rule.id:
                        previous_rule.current_version = 0
                        previous_rule.status = "deprecated"
                        previous_rule.updated_at = now
                if predecessors:
                    db.flush()
                version.status = "active"
                version.activated_at = now
                version.deprecated_at = None
                rule.status = "active"
                rule.current_version = int(version.version)
                rule.updated_at = now
                asset_activation = _event_rule_payload(rule, version)
            elif job.job_type == "CHANGE_POLICY":
                version_id = self._change_policy_version_id(job)
                if not version_id:
                    raise RuleGenerationJobStateError(
                        "CHANGE_POLICY jobs require asset_version_id or asset_ref "
                        "change_policy_version:<id>"
                    )
                version = (
                    db.query(ChangePolicyVersionRecord)
                    .filter(ChangePolicyVersionRecord.id == version_id)
                    .with_for_update()
                    .first()
                )
                if version is None:
                    raise ValueError("change policy version not found")
                if version.status == "deleted":
                    raise RuleGenerationJobStateError("变化策略已删除")
                policy = (
                    db.query(ChangePolicyRecord)
                    .filter(ChangePolicyRecord.id == version.policy_id)
                    .with_for_update()
                    .first()
                )
                if policy is None:
                    raise ValueError("change policy not found")
                (
                    db.query(ChangePolicyVersionRecord)
                    .filter(ChangePolicyVersionRecord.policy_id == policy.id)
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
                asset_activation = {
                    **policy.to_dict(),
                    "policy_id": policy.id,
                    "policy_name": policy.name,
                    "version_id": version.id,
                    "version": version.version,
                    "policy": version.policy_json,
                    "version_status": version.status,
                    "digest": version.digest,
                }

            job.status = "active"
            if asset_activation is not None:
                job.asset_type = str(job.job_type)
                job.asset_id = str(
                    asset_activation.get("rule_id") or asset_activation.get("policy_id") or ""
                )
                job.asset_version_id = str(asset_activation.get("version_id") or "")
                if getattr(version, "digest", None):
                    job.asset_digest = version.digest
            job.validation_report_json = _merge_governance(
                validation_report,
                {
                    "approved_by": actor,
                    "approved_at": now.isoformat(),
                    "approval_reason": reason,
                },
            )
            job.updated_at = now
            job.completed_at = now
            db.flush()
            return {
                "success": True,
                "job": job.to_dict(),
                "asset_activation": asset_activation,
            }

    def reject(
        self,
        job_id: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        now = _utcnow()
        with get_db_session() as db:
            job = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.id == job_id)
                .with_for_update()
                .first()
            )
            if job is None:
                raise KeyError("rule generation job not found")
            if job.job_type == "RULE_ASSET_BUNDLE":
                if job.status not in {"draft", "validating", "pending_approval", "rejected"}:
                    raise RuleGenerationJobStateError(
                        f"invalid rule generation transition: {job.status} -> rejected"
                    )
                child_ids = self._bundle_child_ids(job)
                children = (
                    db.query(RuleGenerationJobRecord)
                    .filter(RuleGenerationJobRecord.id.in_(child_ids))
                    .with_for_update()
                    .all()
                    if child_ids
                    else []
                )
                for child in children:
                    if child.status in {"draft", "validating", "pending_approval", "rejected"}:
                        child.status = "rejected"
                        child.validation_report_json = _merge_governance(
                            dict(child.validation_report_json or {}),
                            {
                                "rejected_by": actor,
                                "rejected_at": now.isoformat(),
                                "reason": reason,
                                "bundle_id": job.id,
                            },
                        )
                        child.updated_at = now
                        child.completed_at = now
                job.status = "rejected"
                job.validation_report_json = _merge_governance(
                    dict(job.validation_report_json or {}),
                    {"rejected_by": actor, "rejected_at": now.isoformat(), "reason": reason},
                )
                job.updated_at = now
                job.completed_at = now
                db.flush()
                return {
                    "success": True,
                    "job": job.to_dict(),
                    "child_job_ids": child_ids,
                    "atomic": True,
                }
            self._assert_direct_action_allowed(job)
            if job.status not in {"draft", "validating", "pending_approval", "rejected"}:
                raise RuleGenerationJobStateError(
                    f"invalid rule generation transition: {job.status} -> rejected"
                )
            job.status = "rejected"
            job.validation_report_json = _merge_governance(
                dict(job.validation_report_json or {}),
                {"rejected_by": actor, "rejected_at": now.isoformat(), "reason": reason},
            )
            job.updated_at = now
            job.completed_at = now
            db.flush()
            return {"success": True, "job": job.to_dict()}

    def deprecate(
        self,
        job_id: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        now = _utcnow()
        with get_db_session() as db:
            job = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.id == job_id)
                .with_for_update()
                .first()
            )
            if job is None:
                raise KeyError("rule generation job not found")
            if job.job_type == "RULE_ASSET_BUNDLE":
                return self._deprecate_bundle(
                    db,
                    job,
                    actor=actor,
                    reason=reason,
                    now=now,
                )
            self._assert_direct_action_allowed(job)
            if job.status not in {"pending_approval", "active", "rejected"}:
                raise RuleGenerationJobStateError(
                    f"invalid rule generation transition: {job.status} -> deprecated"
                )

            asset_deprecation = None
            if job.job_type == "EVENT_RULE":
                version_id = self._event_rule_version_id(job)
                if version_id:
                    version = (
                        db.query(EventRuleVersionRecord)
                        .filter(EventRuleVersionRecord.id == version_id)
                        .with_for_update()
                        .first()
                    )
                    if version is not None and version.status != "deleted":
                        rule = (
                            db.query(EventRuleRecord)
                            .filter(EventRuleRecord.id == version.rule_id)
                            .with_for_update()
                            .first()
                        )
                        if rule is None:
                            raise ValueError("event rule not found")
                        version.status = "deprecated"
                        version.deprecated_at = now
                        if int(rule.current_version or 0) == int(version.version):
                            active = (
                                db.query(EventRuleVersionRecord)
                                .filter(EventRuleVersionRecord.rule_id == rule.id)
                                .filter(EventRuleVersionRecord.status == "active")
                                .filter(EventRuleVersionRecord.id != version.id)
                                .first()
                            )
                            rule.current_version = int(active.version) if active else 0
                            rule.status = "active" if active else "deprecated"
                        asset_deprecation = _event_rule_payload(rule, version)
            elif job.job_type == "CHANGE_POLICY":
                version_id = self._change_policy_version_id(job)
                if version_id:
                    version = (
                        db.query(ChangePolicyVersionRecord)
                        .filter(ChangePolicyVersionRecord.id == version_id)
                        .with_for_update()
                        .first()
                    )
                    if version is not None and version.status != "deleted":
                        policy = (
                            db.query(ChangePolicyRecord)
                            .filter(ChangePolicyRecord.id == version.policy_id)
                            .with_for_update()
                            .first()
                        )
                        if policy is None:
                            raise ValueError("change policy not found")
                        version.status = "deprecated"
                        version.deprecated_at = now
                        if int(policy.current_version or 0) == int(version.version):
                            active = (
                                db.query(ChangePolicyVersionRecord)
                                .filter(ChangePolicyVersionRecord.policy_id == policy.id)
                                .filter(ChangePolicyVersionRecord.status == "active")
                                .filter(ChangePolicyVersionRecord.id != version.id)
                                .first()
                            )
                            policy.current_version = int(active.version) if active else 0
                            policy.status = "active" if active else "deprecated"
                        asset_deprecation = {
                            **policy.to_dict(),
                            "policy_id": policy.id,
                            "policy_name": policy.name,
                            "version_id": version.id,
                            "version": version.version,
                            "version_status": version.status,
                            "digest": version.digest,
                        }

            job.status = "deprecated"
            job.validation_report_json = _merge_governance(
                dict(job.validation_report_json or {}),
                {
                    "deprecated_by": actor,
                    "deprecated_at": now.isoformat(),
                    "reason": reason,
                },
            )
            job.updated_at = now
            job.completed_at = now
            db.flush()
            return {
                "success": True,
                "job": job.to_dict(),
                "asset_deprecation": asset_deprecation,
            }
