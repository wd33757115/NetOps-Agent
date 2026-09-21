"""Submit validated revision candidates as reviewable rule assets."""

from __future__ import annotations

from typing import Any

from src.apps.rule_governance.backend.generators.runner import (
    GeneratorExecutionError,
    execute_generator,
)
from src.apps.rule_governance.backend.rule_control.jobs import RuleGenerationJobStateError
from src.apps.rule_governance.backend.rule_control.repository import RuleGenerationJobRepository


class RuleAssetSubmissionService:
    """Bridge successful generation jobs to the asset persister Skill.

    This service does not activate assets. It creates draft asset versions and
    governance jobs that can move to pending approval after deterministic
    validation, keeping approval as a separate control-plane step.
    """

    def __init__(self, repository: RuleGenerationJobRepository | None = None) -> None:
        self.repository = repository or RuleGenerationJobRepository()

    def submit_candidate(
        self,
        job_id: str,
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError("rule generation job not found")
        if job.get("status") not in {"validating", "pending_approval"}:
            raise RuleGenerationJobStateError(
                "rule generation job must be validating or pending_approval before submission"
            )
        report = (
            job.get("validation_report_json")
            if isinstance(job.get("validation_report_json"), dict)
            else {}
        )
        if report.get("success") is not True:
            raise RuleGenerationJobStateError(
                "rule generation job cannot be submitted without successful validation"
            )
        skill_result = (
            report.get("skill_result")
            if isinstance(report.get("skill_result"), dict)
            else {}
        )
        params = self._persister_params(
            job=job,
            report=report,
            skill_result=skill_result,
            actor=actor,
        )
        try:
            result = execute_generator("rule-asset-persister", params)
        except GeneratorExecutionError as exc:
            raise RuleGenerationJobStateError(str(exc)) from exc
        submitted = self._mark_submission(job_id, result, actor=actor)
        return {
            "success": bool(result.get("success")),
            "submitted_job": submitted,
            "persister_result": result,
        }

    def _persister_params(
        self,
        *,
        job: dict[str, Any],
        report: dict[str, Any],
        skill_result: dict[str, Any],
        actor: str | None,
    ) -> dict[str, Any]:
        payload = (
            job.get("payload_json")
            if isinstance(job.get("payload_json"), dict)
            else {}
        )
        base: dict[str, Any] = {
            "source_ref": f"rule_generation_job:{job['id']}",
            "created_by": actor or "rule-asset-submission",
            "vendor": payload.get("vendor"),
            "model": payload.get("model"),
            "command": payload.get("command") or payload.get("canonical_command"),
        }
        job_type = str(job.get("job_type") or "")
        if job_type == "OBSERVATION_CONTRACT":
            if "proposal" not in skill_result:
                raise RuleGenerationJobStateError(
                    "observation contract submission requires skill_result.proposal"
                )
            base["contract_result"] = skill_result
            return base
        if job_type == "CHANGE_POLICY":
            if "policy" not in skill_result:
                raise RuleGenerationJobStateError(
                    "change policy submission requires skill_result.policy"
                )
            base["change_policy_result"] = skill_result
            return base
        if job_type == "EVENT_RULE":
            if not isinstance(skill_result.get("rules"), list) or not skill_result.get("rules"):
                raise RuleGenerationJobStateError(
                    "event rule submission requires skill_result.rules"
                )
            replay = report.get("historical_replay")
            if not isinstance(replay, dict):
                replay = payload.get("historical_replay")
            base.update(
                {
                    "event_rule_result": skill_result,
                    "event_rule_validation": skill_result.get("validation"),
                    "event_rule_replay": replay,
                    "require_event_rule_replay": True,
                }
            )
            return base
        raise RuleGenerationJobStateError(
            f"unsupported rule generation job type for submission: {job_type}"
        )

    def _mark_submission(
        self,
        job_id: str,
        result: dict[str, Any],
        *,
        actor: str | None,
    ) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        report = dict((job or {}).get("validation_report_json") or {})
        governance = dict(report.get("governance") or {})
        governance["submitted_by"] = actor
        governance["submitted_asset_count"] = result.get("asset_count")
        governance["submitted_job_ids"] = [
            item.get("id")
            for item in result.get("jobs") or []
            if isinstance(item, dict) and item.get("id")
        ]
        report["governance"] = governance
        return self.repository.attach_validation_report(
            job_id,
            validation_report=report,
        )
