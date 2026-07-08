"""Transactional approval service for generated rule assets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.core.rule_control.jobs import RuleGenerationJobStateError
from src.infrastructure.db.models import (
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
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
        "version_status": version.status,
        "activated_at": version.activated_at,
        "deprecated_at": version.deprecated_at,
    }


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
            return asset_ref[len(prefix):]
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
            return asset_ref[len(prefix):]
        return None

    def approve(self, job_id: str, *, actor: str | None = None) -> dict[str, Any]:
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
                        "EVENT_RULE jobs require asset_version_id or asset_ref event_rule_version:<id>"
                    )
                version = (
                    db.query(EventRuleVersionRecord)
                    .filter(EventRuleVersionRecord.id == version_id)
                    .with_for_update()
                    .first()
                )
                if version is None:
                    raise ValueError("event rule version not found")
                rule = (
                    db.query(EventRuleRecord)
                    .filter(EventRuleRecord.id == version.rule_id)
                    .with_for_update()
                    .first()
                )
                if rule is None:
                    raise ValueError("event rule not found")
                (
                    db.query(EventRuleVersionRecord)
                    .filter(EventRuleVersionRecord.rule_id == rule.id)
                    .filter(EventRuleVersionRecord.status == "active")
                    .filter(EventRuleVersionRecord.id != version.id)
                    .update(
                        {"status": "deprecated", "deprecated_at": now},
                        synchronize_session=False,
                    )
                )
                version.status = "active"
                version.activated_at = now
                version.deprecated_at = None
                rule.status = "active"
                rule.current_version = int(version.version)
                asset_activation = _event_rule_payload(rule, version)
            elif job.job_type == "CHANGE_POLICY":
                version_id = self._change_policy_version_id(job)
                if not version_id:
                    raise RuleGenerationJobStateError(
                        "CHANGE_POLICY jobs require asset_version_id or asset_ref change_policy_version:<id>"
                    )
                version = (
                    db.query(ChangePolicyVersionRecord)
                    .filter(ChangePolicyVersionRecord.id == version_id)
                    .with_for_update()
                    .first()
                )
                if version is None:
                    raise ValueError("change policy version not found")
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
                    asset_activation.get("rule_id")
                    or asset_activation.get("policy_id")
                    or ""
                )
                job.asset_version_id = str(asset_activation.get("version_id") or "")
                if getattr(version, "digest", None):
                    job.asset_digest = version.digest
            job.validation_report_json = _merge_governance(
                validation_report,
                {"approved_by": actor, "approved_at": now.isoformat()},
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
                    if version is not None:
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
                    if version is not None:
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
