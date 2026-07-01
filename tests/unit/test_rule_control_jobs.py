from __future__ import annotations

import uuid

import pytest

from src.core.rule_control.jobs import RuleGenerationJob
from src.core.rule_control.jobs import RuleGenerationJobStateError
from src.core.rule_control.jobs import transition_job
from src.core.rule_control.repository import RuleGenerationJobRepository
from src.infrastructure.db.models import RuleGenerationJobRecord
from src.infrastructure.db.postgres import get_db_session


def test_rule_generation_job_supports_planned_asset_types():
    for job_type in {
        "TEXTFSM_TEMPLATE",
        "OBSERVATION_CONTRACT",
        "CHANGE_POLICY",
        "EVENT_RULE",
    }:
        job = RuleGenerationJob(job_type=job_type)
        assert job.status == "draft"


def test_rule_generation_job_state_machine():
    job = RuleGenerationJob(job_type="EVENT_RULE")
    validating = transition_job(job, "validating")
    pending = transition_job(validating, "pending_approval")
    active = transition_job(pending, "active")
    deprecated = transition_job(active, "deprecated")

    assert deprecated.status == "deprecated"
    assert deprecated.job_id == job.job_id


def test_rule_generation_job_can_deprecate_pending_approval_candidate():
    job = RuleGenerationJob(job_type="EVENT_RULE")
    pending = transition_job(transition_job(job, "validating"), "pending_approval")

    deprecated = transition_job(pending, "deprecated")

    assert deprecated.status == "deprecated"


def test_rule_generation_job_rejects_invalid_transition():
    job = RuleGenerationJob(job_type="CHANGE_POLICY")

    with pytest.raises(RuleGenerationJobStateError):
        transition_job(job, "active")


def test_rule_generation_job_repository_roundtrip():
    repo = RuleGenerationJobRepository()
    job = repo.create_job(
        job_type="CHANGE_POLICY",
        source_ref=f"unit:{uuid.uuid4().hex}",
        payload={"observation_type": "cpu_usage"},
        created_by="pytest",
    )

    try:
        loaded = repo.get_job(job["id"])
        assert loaded is not None
        assert loaded["payload_json"]["observation_type"] == "cpu_usage"

        validating = repo.transition_job(job["id"], status="validating")
        assert validating["status"] == "validating"

        reported = repo.attach_validation_report(
            job["id"],
            validation_report={"success": True, "cases": 3},
        )
        assert reported["validation_report_json"]["success"] is True

        pending = repo.transition_job(job["id"], status="pending_approval")
        assert pending["status"] == "pending_approval"

        approved = repo.approve_job(job["id"], approved_by="pytest")
        assert approved["status"] == "active"
        assert approved["validation_report_json"]["success"] is True
        assert approved["validation_report_json"]["governance"]["approved_by"] == "pytest"
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()
