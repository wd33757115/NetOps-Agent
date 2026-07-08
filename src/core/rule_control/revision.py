"""Agent Loop revision helpers for rule generation jobs."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from src.core.rule_control.repository import RuleGenerationJobRepository
from src.infrastructure.db.models import RuleGenerationJobRecord
from src.infrastructure.db.postgres import get_db_session


GENERATOR_TO_JOB_TYPE = {
    "observation-contract-generator": "OBSERVATION_CONTRACT",
    "change-policy-generator": "CHANGE_POLICY",
    "event-rule-generator": "EVENT_RULE",
    "event-rule-validator": "EVENT_RULE",
    "textfsm-generator": "TEXTFSM_TEMPLATE",
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _copy_dict(value: Any) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _revision_request_from_loop(loop: dict[str, Any]) -> dict[str, Any] | None:
    request = loop.get("revision_request")
    if isinstance(request, dict):
        return request
    for attempt in reversed(loop.get("attempts") or []):
        if isinstance(attempt, dict) and isinstance(attempt.get("revision_request"), dict):
            return attempt["revision_request"]
    return None


def _find_revision_request(report: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for value in [
        report.get("agent_loop"),
        _as_dict(report.get("synthetic_validation")).get("agent_loop"),
        _as_dict(report.get("validation")).get("agent_loop"),
    ]:
        if isinstance(value, dict):
            candidates.append(value)
    governance = _as_dict(report.get("governance"))
    if isinstance(governance.get("agent_loop"), dict):
        candidates.append(governance["agent_loop"])
    for loop in reversed(candidates):
        request = _revision_request_from_loop(loop)
        if request:
            return request
    return None


class AgentLoopRevisionService:
    """Create auditable next-round rule generation jobs from revision requests."""

    def __init__(self, repository: RuleGenerationJobRepository | None = None) -> None:
        self.repository = repository or RuleGenerationJobRepository()

    def create_revision_job(
        self,
        job_id: str,
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        with get_db_session() as db:
            parent = db.get(RuleGenerationJobRecord, job_id)
            if parent is None:
                raise KeyError("rule generation job not found")
            parent_report = _copy_dict(parent.validation_report_json)
            governance = _copy_dict(parent_report.get("governance"))
            existing = _as_dict(governance.get("agent_loop_revision"))
            existing_child_id = existing.get("child_job_id")
            if existing_child_id:
                child = db.get(RuleGenerationJobRecord, str(existing_child_id))
                if child is not None:
                    return {
                        "success": True,
                        "created": False,
                        "parent_job_id": parent.id,
                        "revision_request": existing.get("revision_request") or {},
                        "job": child.to_dict(),
                    }

            revision_request = _find_revision_request(parent_report)
            if not revision_request:
                raise ValueError("agent loop revision_request is required")
            target_generator = str(revision_request.get("target_generator") or "").strip()
            child_job_type = GENERATOR_TO_JOB_TYPE.get(target_generator, parent.job_type)
            next_patch = _as_dict(revision_request.get("next_params_patch"))
            parent_payload = _as_dict(parent.payload_json)
            parent_round = int(parent_payload.get("agent_loop_round") or 1)
            child_payload = {
                **parent_payload,
                **next_patch,
                "parent_job_id": parent.id,
                "parent_job_type": parent.job_type,
                "agent_loop_round": parent_round + 1,
                "revision_request": revision_request,
            }
            child = self.repository.create_job(
                job_type=child_job_type,
                source_ref=f"agent_loop_revision:{parent.id}",
                asset_type=parent.asset_type or child_job_type,
                asset_id=parent.asset_id,
                asset_version_id=parent.asset_version_id,
                asset_digest=parent.asset_digest,
                payload=child_payload,
                validation_report={
                    "success": None,
                    "agent_loop": {
                        "ready_for_approval": False,
                        "next_action": "run_revision_candidate",
                        "revision_request": revision_request,
                        "attempt_count": 0,
                        "attempts": [],
                    },
                    "governance": {
                        "parent_job_id": parent.id,
                        "created_by": actor,
                        "created_at": _utcnow_iso(),
                    },
                },
                created_by=actor,
            )

            parent = db.get(RuleGenerationJobRecord, job_id)
            if parent is not None:
                report = _copy_dict(parent.validation_report_json)
                governance = _copy_dict(report.get("governance"))
                governance["agent_loop_revision"] = {
                    "child_job_id": child["id"],
                    "child_job_type": child_job_type,
                    "created_by": actor,
                    "created_at": _utcnow_iso(),
                    "revision_request": revision_request,
                }
                child_ids = list(governance.get("agent_loop_child_job_ids") or [])
                if child["id"] not in child_ids:
                    child_ids.append(child["id"])
                governance["agent_loop_child_job_ids"] = child_ids
                report["governance"] = governance
                parent.validation_report_json = report
                flag_modified(parent, "validation_report_json")
                parent.updated_at = datetime.now(timezone.utc)
                db.flush()

        return {
            "success": True,
            "created": True,
            "parent_job_id": job_id,
            "revision_request": revision_request,
            "job": child,
        }
