from __future__ import annotations

import uuid

import pytest

from src.core.patrol.change_policy_repository import ChangePolicyRepository
from src.core.patrol.event_rules import EventRuleRepository
from src.core.rule_control.approval import RuleAssetApprovalService
from src.core.rule_control.executor import AgentLoopRevisionExecutor
from src.core.rule_control.jobs import (
    RuleGenerationJob,
    RuleGenerationJobStateError,
    transition_job,
)
from src.core.rule_control.repository import RuleGenerationJobRepository
from src.core.rule_control.revision import AgentLoopRevisionService
from src.core.rule_control.submission import RuleAssetSubmissionService
from src.infrastructure.db.models import (
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    RuleAgentLoopAttemptRecord,
    RuleApprovalAuditRecord,
    RuleGenerationJobRecord,
    RuleReplayReportRecord,
)
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
        asset_ref=f"change_policy_version:{uuid.uuid4().hex}",
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


def test_rule_generation_job_preview_summarizes_validation_and_redacts_private_payload():
    repo = RuleGenerationJobRepository()
    job = repo.create_job(
        job_type="EVENT_RULE",
        source_ref=f"unit:{uuid.uuid4().hex}",
        asset_type="EVENT_RULE",
        asset_id=f"rule-{uuid.uuid4().hex}",
        asset_version_id=f"rule-version-{uuid.uuid4().hex}",
        asset_digest="c" * 64,
        payload={
            "candidate_rules": [
                {
                    "event_type": "CPUHigh",
                    "condition": {"field": "cpu_5s", "operator": ">=", "value": 90},
                }
            ],
            "records": [{"device": "private-device", "cpu_5s": 95}],
        },
        validation_report={
            "success": True,
            "synthetic_validation": {"success": True, "case_count": 3},
            "historical_replay": {
                "success": True,
                "sampled_change_count": 10,
                "matched_change_count": 2,
                "collision_count": 0,
            },
            "skill_result": {
                "candidate_rules": [
                    {
                        "event_type": "CPUHigh",
                        "raw_text": "private cli output",
                    }
                ]
            },
        },
        created_by="pytest",
    )

    try:
        repo.transition_job(job["id"], status="validating")
        repo.transition_job(job["id"], status="pending_approval")

        preview = repo.preview_job(job["id"])

        assert preview["success"] is True
        assert preview["approval_readiness"]["can_approve"] is True
        assert preview["validation_summary"]["case_count"] == 3
        assert preview["historical_replay"]["matched_change_count"] == 2
        assert (
            preview["payload_summary"]["summary"]["records"]["reason"] == "private_or_large_payload"
        )
        candidate = preview["candidate_preview"]["skill_result.candidate_rules"][0]
        assert candidate["raw_text"]["redacted"] is True

        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, job["id"])
            report = dict(row.validation_report_json or {})
            report["historical_replay"] = {
                **dict(report.get("historical_replay") or {}),
                "success": True,
                "collision_count": 1,
            }
            row.validation_report_json = report

        blocked_preview = repo.preview_job(job["id"])
        assert blocked_preview["approval_readiness"]["can_approve"] is False
        assert (
            "historical replay has collisions" in blocked_preview["approval_readiness"]["blockers"]
        )
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()


def test_rule_generation_job_repository_lists_approval_audit_events():
    repo = RuleGenerationJobRepository()
    job = repo.create_job(
        job_type="CHANGE_POLICY",
        source_ref=f"unit:{uuid.uuid4().hex}",
        asset_type="CHANGE_POLICY",
        asset_id=f"policy-{uuid.uuid4().hex}",
        asset_version_id=f"policy-version-{uuid.uuid4().hex}",
        asset_digest="b" * 64,
        validation_report={"success": True},
        created_by="pytest",
    )

    try:
        repo.transition_job(job["id"], status="validating")
        repo.transition_job(job["id"], status="pending_approval")
        repo.approve_job(job["id"], approved_by="pytest-approver")

        events = repo.list_approval_audit(
            action="approved",
            asset_type="CHANGE_POLICY",
            actor="pytest-approver",
            limit=20,
        )

        event = next(item for item in events if item["job_id"] == job["id"])
        assert event["action"] == "approved"
        assert event["actor"] == "pytest-approver"
        assert event["asset_type"] == "CHANGE_POLICY"
        assert event["asset_version_id"] == job["asset_version_id"]
        assert event["asset_digest"] == "b" * 64
        assert event["validation_success"] is True
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()


def test_rule_generation_job_repository_lists_agent_loop_attempts():
    repo = RuleGenerationJobRepository()
    job = repo.create_job(
        job_type="EVENT_RULE",
        asset_type="EVENT_RULE",
        asset_id=f"rule-{uuid.uuid4().hex}",
        asset_version_id=f"version-{uuid.uuid4().hex}",
        validation_report={
            "success": False,
            "agent_loop": {
                "ready_for_approval": False,
                "next_action": "revise_candidate_rule",
                "attempts": [
                    {
                        "attempt": 1,
                        "stage": "event_rule_validation",
                        "generator": "event-rule-validator",
                        "mode": "candidates",
                        "success": False,
                        "input_summary": {"rule_count": 2},
                        "output_summary": {"failed_case_count": 1},
                        "validator_feedback": {"hints": ["narrow entity_types"]},
                        "revision_request": {
                            "target_generator": "event-rule-generator",
                            "required_fixes": ["narrow entity_types"],
                        },
                    }
                ],
            },
        },
        created_by="pytest",
    )

    try:
        attempts = repo.list_agent_loop_attempts(
            asset_type="EVENT_RULE",
            stage="event_rule_validation",
            success=False,
            limit=20,
        )

        attempt = next(item for item in attempts if item["job_id"] == job["id"])
        assert attempt["attempt"] == 1
        assert attempt["stage"] == "event_rule_validation"
        assert attempt["generator"] == "event-rule-validator"
        assert attempt["success"] is False
        assert attempt["ready_for_approval"] is False
        assert attempt["next_action"] == "revise_candidate_rule"
        assert attempt["input_summary"]["rule_count"] == 2
        assert attempt["validator_feedback"]["hints"] == ["narrow entity_types"]
        assert attempt["revision_request"]["target_generator"] == "event-rule-generator"
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()


def test_rule_job_history_uses_transactional_database_projections(monkeypatch):
    repo = RuleGenerationJobRepository()
    marker = f"projection-{uuid.uuid4().hex}"
    jobs = []
    for index in range(3):
        jobs.append(
            repo.create_job(
                job_type="EVENT_RULE",
                source_ref=f"{marker}:{index}",
                asset_ref=f"event_rule_version:{uuid.uuid4().hex}",
                asset_type="EVENT_RULE",
                asset_id=f"rule-{uuid.uuid4().hex}",
                asset_version_id=f"version-{uuid.uuid4().hex}",
                validation_report={
                    "success": True,
                    "historical_replay": {
                        "success": index != 1,
                        "sampled_change_count": 10 + index,
                        "matched_change_count": index,
                        "collision_count": 0,
                    },
                    "agent_loop": {
                        "ready_for_approval": False,
                        "next_action": "revise_candidate_rule",
                        "attempts": [
                            {
                                "attempt": 1,
                                "stage": "event_rule_validation",
                                "success": False,
                            },
                            {
                                "attempt": 2,
                                "stage": "event_rule_validation",
                                "success": True,
                            },
                        ],
                    },
                },
                created_by=marker,
            )
        )

    try:
        monkeypatch.setattr(
            repo,
            "_extract_historical_replay",
            lambda _report: (_ for _ in ()).throw(
                AssertionError("JSON replay extraction must not run during list queries")
            ),
        )
        first_page = repo.list_replay_reports_page(
            search=marker,
            limit=2,
            offset=0,
        )
        second_page = repo.list_replay_reports_page(
            search=marker,
            limit=2,
            offset=2,
        )

        assert first_page["total"] == 3
        assert len(first_page["items"]) == 2
        assert len(second_page["items"]) == 1
        assert {item["job_id"] for item in first_page["items"]}.isdisjoint(
            {item["job_id"] for item in second_page["items"]}
        )

        attempts = repo.list_agent_loop_attempts_page(
            asset_type="EVENT_RULE",
            stage="event_rule_validation",
            search=marker,
            limit=20,
            offset=0,
        )
        assert attempts["total"] == 6

        first_job = jobs[0]
        repo.attach_validation_report(
            first_job["id"],
            validation_report={
                "success": True,
                "historical_replay": {
                    "success": True,
                    "sampled_change_count": 99,
                    "matched_change_count": 9,
                    "collision_count": 0,
                },
                "agent_loop": {
                    "attempts": [
                        {
                            "attempt": 3,
                            "stage": "event_rule_validation",
                            "success": True,
                        }
                    ]
                },
            },
        )
        with get_db_session() as db:
            assert db.query(RuleReplayReportRecord).filter_by(job_id=first_job["id"]).count() == 1
            assert (
                db.query(RuleAgentLoopAttemptRecord).filter_by(job_id=first_job["id"]).count() == 3
            )

        repo.transition_job(first_job["id"], status="validating")
        repo.transition_job(first_job["id"], status="pending_approval")
        repo.approve_job(first_job["id"], approved_by=marker)
        audit = repo.list_approval_audit_page(
            action="approved",
            actor=marker,
            search=marker,
            limit=20,
            offset=0,
        )
        assert audit["total"] == 1
        assert audit["summary"]["total"] == 1
        assert audit["summary"]["approved"] == 1
        assert audit["summary"]["rejected"] == 0
        assert audit["summary"]["deprecated"] == 0
        assert audit["items"][0]["job_id"] == first_job["id"]
        with get_db_session() as db:
            assert (
                db.query(RuleApprovalAuditRecord)
                .filter_by(job_id=first_job["id"], action="approved")
                .count()
                == 1
            )
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_([job["id"] for job in jobs])
            ).delete(synchronize_session=False)


def test_agent_loop_revision_service_creates_idempotent_child_job():
    repo = RuleGenerationJobRepository()
    parent = repo.create_job(
        job_type="EVENT_RULE",
        source_ref=f"unit:{uuid.uuid4().hex}",
        payload={"candidate_rules": [{"event_type": "CPUHigh"}]},
        validation_report={
            "success": False,
            "agent_loop": {
                "ready_for_approval": False,
                "next_action": "revise_candidate_rule",
                "revision_request": {
                    "target_generator": "event-rule-generator",
                    "reason": ["unexpected CPUHigh"],
                    "required_fixes": ["narrow entity_types"],
                    "next_params_patch": {"agent_loop": True},
                },
                "attempts": [
                    {
                        "attempt": 1,
                        "stage": "event_rule_validation",
                        "success": False,
                    }
                ],
            },
        },
        created_by="pytest",
    )

    try:
        first = AgentLoopRevisionService(repo).create_revision_job(
            parent["id"],
            actor="pytest",
        )
        second = AgentLoopRevisionService(repo).create_revision_job(
            parent["id"],
            actor="pytest",
        )

        assert first["created"] is True
        assert second["created"] is False
        assert first["job"]["id"] == second["job"]["id"]
        assert first["job"]["job_type"] == "EVENT_RULE"
        assert first["job"]["source_ref"] == f"agent_loop_revision:{parent['id']}"
        assert first["job"]["payload_json"]["parent_job_id"] == parent["id"]
        assert first["job"]["payload_json"]["agent_loop_round"] == 2
        assert (
            first["job"]["payload_json"]["revision_request"]["target_generator"]
            == "event-rule-generator"
        )

        reloaded_parent = repo.get_job(parent["id"])
        assert reloaded_parent is not None
        governance = reloaded_parent["validation_report_json"]["governance"]
        assert governance["agent_loop_revision"]["child_job_id"] == first["job"]["id"]
    finally:
        child_id = None
        try:
            reloaded_parent = repo.get_job(parent["id"])
            child_id = (
                reloaded_parent.get("validation_report_json", {})
                .get("governance", {})
                .get("agent_loop_revision", {})
                .get("child_job_id")
                if reloaded_parent
                else None
            )
        finally:
            with get_db_session() as db:
                ids = [parent["id"]]
                if child_id:
                    ids.append(child_id)
                db.query(RuleGenerationJobRecord).filter(
                    RuleGenerationJobRecord.id.in_(ids)
                ).delete(synchronize_session=False)


def test_agent_loop_revision_service_requires_revision_request():
    repo = RuleGenerationJobRepository()
    parent = repo.create_job(
        job_type="CHANGE_POLICY",
        validation_report={"success": False},
        created_by="pytest",
    )
    try:
        with pytest.raises(ValueError, match="revision_request"):
            AgentLoopRevisionService(repo).create_revision_job(parent["id"])
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == parent["id"]
            ).delete()


def test_agent_loop_revision_executor_runs_validator_and_updates_report(monkeypatch):
    repo = RuleGenerationJobRepository()
    job = repo.create_job(
        job_type="EVENT_RULE",
        source_ref=f"agent_loop_revision:{uuid.uuid4().hex}",
        payload={
            "parent_job_id": f"parent-{uuid.uuid4().hex}",
            "agent_loop_round": 2,
            "rule": {
                "name": "cpu-high-candidate",
                "event_type": "CPUHigh",
                "severity": "warning",
                "condition": {
                    "kind": "threshold",
                    "entity_types": ["cpu"],
                    "fields": ["cpu_5s"],
                    "gte": 90,
                },
            },
            "revision_request": {"target_generator": "event-rule-generator"},
        },
        validation_report={"success": None},
        created_by="pytest",
    )

    def fake_execute_skill(skill_name, params):
        assert skill_name == "event-rule-validator"
        assert params["mode"] == "candidates"
        assert len(params["candidate_rules"]) == 1
        return {
            "success": True,
            "agent_loop": {
                "ready_for_approval": True,
                "next_action": "submit_for_approval",
                "attempts": [
                    {
                        "attempt": 1,
                        "stage": "event_rule_validation",
                        "success": True,
                    }
                ],
            },
        }

    monkeypatch.setattr("src.core.rule_control.executor.execute_skill", fake_execute_skill)

    try:
        result = AgentLoopRevisionExecutor(repo).execute(job["id"], actor="pytest")

        assert result["success"] is True
        assert result["job"]["status"] == "validating"
        updated = repo.get_job(job["id"])
        assert updated is not None
        report = updated["validation_report_json"]
        assert report["success"] is True
        assert report["generator"] == "event-rule-validator"
        assert report["revision_execution"]["agent_loop_round"] == 2
        assert report["agent_loop"]["ready_for_approval"] is True
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()


def test_agent_loop_revision_executor_rejects_failed_revision(monkeypatch):
    repo = RuleGenerationJobRepository()
    job = repo.create_job(
        job_type="CHANGE_POLICY",
        source_ref=f"agent_loop_revision:{uuid.uuid4().hex}",
        payload={
            "parent_job_id": f"parent-{uuid.uuid4().hex}",
            "agent_loop_round": 2,
            "observation_type": "device.cpu.utilization",
            "field_name": "cpu_5s",
            "field_semantics": {"semantic_type": "gauge", "unit": "percent"},
        },
        validation_report={"success": None},
        created_by="pytest",
    )

    def fake_execute_skill(skill_name, params):
        assert skill_name == "change-policy-generator"
        return {"success": False, "error": "invalid policy"}

    monkeypatch.setattr("src.core.rule_control.executor.execute_skill", fake_execute_skill)

    try:
        result = AgentLoopRevisionExecutor(repo).execute(job["id"], actor="pytest")

        assert result["success"] is False
        assert result["job"]["status"] == "rejected"
        updated = repo.get_job(job["id"])
        assert updated is not None
        assert updated["validation_report_json"]["success"] is False
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()


def test_rule_asset_submission_service_persists_successful_revision(monkeypatch):
    repo = RuleGenerationJobRepository()
    job = repo.create_job(
        job_type="CHANGE_POLICY",
        source_ref=f"agent_loop_revision:{uuid.uuid4().hex}",
        payload={
            "parent_job_id": f"parent-{uuid.uuid4().hex}",
            "observation_type": "device.cpu.utilization",
        },
        validation_report={
            "success": True,
            "skill_result": {
                "success": True,
                "policy": {
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "compare_mode": "threshold_and_deadband",
                    "thresholds": {"high": 90},
                    "recovery_threshold": 80,
                },
                "validation": {"success": True},
            },
        },
        created_by="pytest",
    )

    def fake_execute_skill(skill_name, params):
        assert skill_name == "rule-asset-persister"
        assert params["created_by"] == "pytest"
        assert params["change_policy_result"]["policy"]["compare_mode"] == (
            "threshold_and_deadband"
        )
        return {
            "success": True,
            "asset_count": 1,
            "jobs": [{"id": "generated-review-job"}],
        }

    monkeypatch.setattr("src.core.rule_control.submission.execute_skill", fake_execute_skill)

    try:
        repo.transition_job(job["id"], status="validating")

        result = RuleAssetSubmissionService(repo).submit_candidate(
            job["id"],
            actor="pytest",
        )

        assert result["success"] is True
        updated = repo.get_job(job["id"])
        assert updated is not None
        governance = updated["validation_report_json"]["governance"]
        assert governance["submitted_by"] == "pytest"
        assert governance["submitted_asset_count"] == 1
        assert governance["submitted_job_ids"] == ["generated-review-job"]
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()


def test_rule_asset_submission_service_requires_successful_validation():
    repo = RuleGenerationJobRepository()
    job = repo.create_job(
        job_type="CHANGE_POLICY",
        validation_report={
            "success": False,
            "skill_result": {"policy": {"observation_type": "cpu"}},
        },
        created_by="pytest",
    )

    try:
        repo.transition_job(job["id"], status="validating")
        with pytest.raises(RuleGenerationJobStateError, match="successful validation"):
            RuleAssetSubmissionService(repo).submit_candidate(job["id"], actor="pytest")
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()


def test_rule_generation_job_repository_rejects_non_draft_create():
    repo = RuleGenerationJobRepository()

    with pytest.raises(ValueError, match="created as draft"):
        repo.create_job(job_type="EVENT_RULE", status="pending_approval")


def test_rule_generation_job_repository_requires_validated_asset_for_approval():
    repo = RuleGenerationJobRepository()
    missing_asset = repo.create_job(
        job_type="EVENT_RULE",
        validation_report={"success": True},
    )
    failed_validation = repo.create_job(
        job_type="EVENT_RULE",
        asset_ref=f"event_rule_version:{uuid.uuid4().hex}",
        validation_report={"success": False},
    )

    try:
        repo.transition_job(missing_asset["id"], status="validating")
        repo.transition_job(missing_asset["id"], status="pending_approval")
        with pytest.raises(RuleGenerationJobStateError, match="asset_ref"):
            repo.approve_job(missing_asset["id"])

        repo.transition_job(failed_validation["id"], status="validating")
        repo.transition_job(failed_validation["id"], status="pending_approval")
        with pytest.raises(RuleGenerationJobStateError, match="successful validation"):
            repo.approve_job(failed_validation["id"])
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_([missing_asset["id"], failed_validation["id"]])
            ).delete(synchronize_session=False)


def test_rule_generation_job_repository_persists_structured_asset_reference():
    repo = RuleGenerationJobRepository()
    job = repo.create_job(
        job_type="CHANGE_POLICY",
        asset_type="CHANGE_POLICY",
        asset_id="policy-1",
        asset_version_id="version-1",
        asset_digest="a" * 64,
    )
    try:
        loaded = repo.get_job(job["id"])
        assert loaded["asset_type"] == "CHANGE_POLICY"
        assert loaded["asset_id"] == "policy-1"
        assert loaded["asset_version_id"] == "version-1"
        assert loaded["asset_digest"] == "a" * 64
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()


def test_rule_asset_approval_service_activates_event_rule_atomically():
    rule_name = f"pytest-rule-approval-{uuid.uuid4().hex}"
    version_id = EventRuleRepository().create_rule_version(
        name=rule_name,
        event_type="CPUHigh",
        severity="warning",
        condition={"kind": "threshold", "fields": ["cpu_5s"], "gte": 90},
        status="draft",
        created_by="pytest",
    )
    job = RuleGenerationJobRepository().create_job(
        job_type="EVENT_RULE",
        asset_ref=f"event_rule_version:{version_id}",
        validation_report={"success": True},
        created_by="pytest",
    )
    rule_id = None
    try:
        repo = RuleGenerationJobRepository()
        repo.transition_job(job["id"], status="validating")
        repo.transition_job(job["id"], status="pending_approval")

        result = RuleAssetApprovalService().approve(job["id"], actor="pytest")

        assert result["job"]["status"] == "active"
        assert result["asset_activation"]["version_status"] == "active"
        with get_db_session() as db:
            job_row = db.get(RuleGenerationJobRecord, job["id"])
            version = db.get(EventRuleVersionRecord, version_id)
            assert job_row.status == "active"
            assert version.status == "active"
            rule_id = version.rule_id
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()
            version = db.get(EventRuleVersionRecord, version_id)
            rule_id = rule_id or (version.rule_id if version else None)
            db.query(EventRuleVersionRecord).filter(
                EventRuleVersionRecord.id == version_id
            ).delete()
            if rule_id:
                db.query(EventRuleRecord).filter(EventRuleRecord.id == rule_id).delete()


def test_change_policy_repository_rejects_same_version_different_digest():
    name = f"pytest-change-policy-{uuid.uuid4().hex}"
    repo = ChangePolicyRepository()
    first = repo.create_policy_version(
        name=name,
        version=1,
        policy={
            "observation_type": "device.cpu.utilization",
            "semantic_type": "gauge",
            "compare_mode": "threshold_and_deadband",
            "thresholds": {"high": 90},
            "recovery_threshold": 85,
        },
        validation_report={"success": True},
        created_by="pytest",
    )
    try:
        same = repo.create_policy_version(
            name=name,
            version=1,
            policy={
                "observation_type": "device.cpu.utilization",
                "semantic_type": "gauge",
                "compare_mode": "threshold_and_deadband",
                "thresholds": {"high": 90},
                "recovery_threshold": 85,
            },
            validation_report={"success": True},
            created_by="pytest",
        )
        assert same["version_id"] == first["version_id"]

        with pytest.raises(ValueError, match="different digest"):
            repo.create_policy_version(
                name=name,
                version=1,
                policy={
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "compare_mode": "threshold_and_deadband",
                    "thresholds": {"high": 95},
                    "recovery_threshold": 90,
                },
                validation_report={"success": True},
                created_by="pytest",
            )
    finally:
        with get_db_session() as db:
            policy_id = first["policy_id"]
            db.query(ChangePolicyVersionRecord).filter(
                ChangePolicyVersionRecord.policy_id == policy_id
            ).delete(synchronize_session=False)
            db.query(ChangePolicyRecord).filter(ChangePolicyRecord.id == policy_id).delete()


def test_rule_asset_approval_service_activates_change_policy_atomically():
    name = f"pytest-change-policy-approval-{uuid.uuid4().hex}"
    policy_version = ChangePolicyRepository().create_policy_version(
        name=name,
        policy={
            "observation_type": "device.memory.utilization",
            "semantic_type": "gauge",
            "compare_mode": "threshold_and_deadband",
            "thresholds": {"high": 90},
            "recovery_threshold": 85,
        },
        validation_report={"success": True},
        created_by="pytest",
    )
    job = RuleGenerationJobRepository().create_job(
        job_type="CHANGE_POLICY",
        asset_ref=f"change_policy_version:{policy_version['version_id']}",
        asset_type="CHANGE_POLICY",
        asset_id=policy_version["policy_id"],
        asset_version_id=policy_version["version_id"],
        asset_digest=policy_version["digest"],
        validation_report={"success": True},
        created_by="pytest",
    )
    try:
        repo = RuleGenerationJobRepository()
        repo.transition_job(job["id"], status="validating")
        repo.transition_job(job["id"], status="pending_approval")

        result = RuleAssetApprovalService().approve(job["id"], actor="pytest")

        assert result["job"]["status"] == "active"
        assert result["asset_activation"]["version_status"] == "active"
        with get_db_session() as db:
            job_row = db.get(RuleGenerationJobRecord, job["id"])
            version = db.get(ChangePolicyVersionRecord, policy_version["version_id"])
            assert job_row.asset_version_id == policy_version["version_id"]
            assert job_row.asset_digest == policy_version["digest"]
            assert version.status == "active"
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete()
            db.query(ChangePolicyVersionRecord).filter(
                ChangePolicyVersionRecord.policy_id == policy_version["policy_id"]
            ).delete(synchronize_session=False)
            db.query(ChangePolicyRecord).filter(
                ChangePolicyRecord.id == policy_version["policy_id"]
            ).delete()
