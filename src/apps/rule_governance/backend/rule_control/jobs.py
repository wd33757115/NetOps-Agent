"""Shared job state model for LLM-assisted rule asset generation."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

RULE_GENERATION_JOB_TYPES = {
    "TEXTFSM_TEMPLATE",
    "CHANGE_POLICY",
    "EVENT_RULE",
    "RULE_ASSET_BUNDLE",
}

RULE_GENERATION_STATUSES = {
    "draft",
    "validating",
    "pending_approval",
    "active",
    "rejected",
    "deprecated",
}

JOB_TRANSITIONS = {
    "draft": {"validating", "rejected"},
    "validating": {"pending_approval", "rejected"},
    "pending_approval": {"active", "rejected", "deprecated"},
    "active": {"deprecated"},
    "rejected": {"draft", "deprecated"},
    "deprecated": set(),
}


class RuleGenerationJobStateError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RuleGenerationJob:
    job_type: str
    status: str = "draft"
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_ref: str | None = None
    asset_ref: str | None = None
    validation_report: dict[str, Any] | None = None
    approval_required: bool = True
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.job_type not in RULE_GENERATION_JOB_TYPES:
            raise ValueError(f"unsupported rule generation job_type: {self.job_type}")
        if self.status not in RULE_GENERATION_STATUSES:
            raise ValueError(f"unsupported rule generation status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def transition_job(job: RuleGenerationJob, next_status: str) -> RuleGenerationJob:
    if next_status not in RULE_GENERATION_STATUSES:
        raise RuleGenerationJobStateError(f"unsupported target status: {next_status}")
    allowed = JOB_TRANSITIONS.get(job.status, set())
    if next_status not in allowed:
        raise RuleGenerationJobStateError(
            f"invalid rule generation transition: {job.status} -> {next_status}"
        )
    return RuleGenerationJob(
        job_type=job.job_type,
        status=next_status,
        job_id=job.job_id,
        source_ref=job.source_ref,
        asset_ref=job.asset_ref,
        validation_report=job.validation_report,
        approval_required=job.approval_required,
        created_at=job.created_at,
        updated_at=_now(),
    )
