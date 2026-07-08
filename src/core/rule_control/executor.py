"""Execute Agent Loop revision jobs without changing production rule assets."""

from __future__ import annotations

from typing import Any

from src.core.rule_control.jobs import RuleGenerationJobStateError
from src.core.rule_control.repository import RuleGenerationJobRepository
from src.core.skills.executor import SkillExecutionError, execute_skill


class AgentLoopRevisionExecutor:
    def __init__(self, repository: RuleGenerationJobRepository | None = None) -> None:
        self.repository = repository or RuleGenerationJobRepository()

    def execute(self, job_id: str, *, actor: str | None = None) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError("rule generation job not found")
        payload = dict(job.get("payload_json") or {})
        if not payload.get("parent_job_id"):
            raise RuleGenerationJobStateError(
                "only agent loop revision jobs can be executed by this executor"
            )
        if job.get("status") == "draft":
            job = self.repository.transition_job(job_id, status="validating")
        elif job.get("status") != "validating":
            raise RuleGenerationJobStateError(
                "agent loop revision job must be draft or validating"
            )

        skill_name, skill_params = self._skill_invocation(job["job_type"], payload)
        try:
            result = execute_skill(skill_name, skill_params)
        except SkillExecutionError as exc:
            report = self._validation_report(
                job=job,
                skill_name=skill_name,
                skill_params=skill_params,
                result=None,
                success=False,
                actor=actor,
                error=str(exc),
            )
            self.repository.attach_validation_report(job_id, validation_report=report)
            rejected = self.repository.transition_job(job_id, status="rejected")
            return {
                "success": False,
                "job": rejected,
                "skill_name": skill_name,
                "error": str(exc),
            }

        success = result.get("success") is True
        report = self._validation_report(
            job=job,
            skill_name=skill_name,
            skill_params=skill_params,
            result=result,
            success=success,
            actor=actor,
        )
        updated = self.repository.attach_validation_report(
            job_id,
            validation_report=report,
        )
        if not success:
            updated = self.repository.transition_job(job_id, status="rejected")
        return {
            "success": success,
            "job": updated,
            "skill_name": skill_name,
            "result": result,
        }

    @staticmethod
    def _skill_invocation(
        job_type: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        params = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "parent_job_id",
                "parent_job_type",
                "agent_loop_round",
                "revision_request",
            }
        }
        params["agent_loop"] = True
        if job_type == "OBSERVATION_CONTRACT":
            return "observation-contract-generator", params
        if job_type == "CHANGE_POLICY":
            return "change-policy-generator", params
        if job_type == "EVENT_RULE":
            if isinstance(params.get("contract"), dict) and isinstance(
                params.get("change_policy"), dict
            ):
                return "event-rule-generator", params
            candidate_rules = params.get("candidate_rules") or params.get("rules")
            if not isinstance(candidate_rules, list):
                rule = params.get("rule") or params.get("candidate_rule")
                candidate_rules = [rule] if isinstance(rule, dict) else []
            return (
                "event-rule-validator",
                {
                    "mode": "candidates",
                    "candidate_rules": candidate_rules,
                    "agent_loop": True,
                },
            )
        if job_type == "TEXTFSM_TEMPLATE":
            return "textfsm-generator", params
        raise ValueError(f"unsupported revision job type: {job_type}")

    @staticmethod
    def _validation_report(
        *,
        job: dict[str, Any],
        skill_name: str,
        skill_params: dict[str, Any],
        result: dict[str, Any] | None,
        success: bool,
        actor: str | None,
        error: str | None = None,
    ) -> dict[str, Any]:
        result = dict(result or {})
        return {
            "success": success,
            "generator": skill_name,
            "agent_loop": result.get("agent_loop"),
            "revision_execution": {
                "parent_job_id": (job.get("payload_json") or {}).get("parent_job_id"),
                "agent_loop_round": (job.get("payload_json") or {}).get(
                    "agent_loop_round"
                ),
                "executed_by": actor,
                "skill_name": skill_name,
                "input_summary": {
                    key: value
                    for key, value in {
                        "mode": skill_params.get("mode"),
                        "command": skill_params.get("command"),
                        "observation_type": skill_params.get("observation_type"),
                        "field_name": skill_params.get("field_name"),
                        "candidate_rule_count": len(skill_params.get("candidate_rules") or []),
                    }.items()
                    if value not in (None, "", [], {})
                },
                "error": error,
            },
            "skill_result": result,
        }
