from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from src.infrastructure.db.models import (
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    ObservationContractRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "skills"
    / "rule-asset-persister"
    / "scripts"
    / "run.py"
)
SPEC = importlib.util.spec_from_file_location("rule_asset_persister_run", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
run_persist = MODULE.run_persist


def test_rule_asset_persister_creates_pending_approval_assets():
    suffix = uuid.uuid4().hex
    schema_id = f"cli:Generic:LAB:display cpu:{suffix}"
    params = {
        "source_ref": f"unit:{suffix}",
        "created_by": "pytest",
        "schema_id": schema_id,
        "command": "display cpu",
        "vendor": "Generic",
        "model": "LAB",
        "observation_type": "device.cpu.utilization",
        "change_policy_name": f"policy:device.cpu.utilization:{suffix}",
        "contract_result": {
            "success": True,
            "proposal": {
                "category": "cpu",
                "entity_type": "cpu",
                "primary_keys": ["slot"],
                "required_fields": ["slot", "cpu_5s"],
                "optional_fields": ["cpu_1m", "cpu_5m"],
                "validators": {"cpu_5s": {"type": "percent"}},
                "state_semantics": {
                    "cpu_5s": {
                        "observation_type": "device.cpu.utilization",
                        "semantic_type": "gauge",
                        "unit": "percent",
                    }
                },
            },
            "validation": {"primary_key_status": "valid"},
            "agent_loop": {
                "ready_for_approval": True,
                "next_action": "submit_for_approval",
                "attempts": [
                    {
                        "attempt": 1,
                        "stage": "observation_contract_generation",
                        "generator": "observation-contract-generator",
                        "success": True,
                    }
                ],
            },
        },
        "change_policy_result": {
            "success": True,
            "policy": {
                "observation_type": "device.cpu.utilization",
                "semantic_type": "gauge",
                "compare_mode": "threshold_and_deadband",
                "thresholds": {"high": 90},
                "recovery_threshold": 85,
            },
            "validation": {"success": True},
            "historical_stats": {"count": 3},
            "agent_loop": {
                "ready_for_approval": True,
                "next_action": "submit_for_approval",
                "attempts": [
                    {
                        "attempt": 1,
                        "stage": "change_policy_generation",
                        "generator": "change-policy-generator",
                        "success": True,
                    }
                ],
            },
        },
        "event_rule_result": {
            "success": True,
            "rules": [
                {
                    "name": f"pytest-cpu-high-{suffix}",
                    "event_type": "CPUHigh",
                    "severity": "warning",
                    "condition": {
                        "kind": "threshold",
                        "fields": ["cpu_5s"],
                        "gte": 90,
                        "entity_types": ["cpu"],
                        "allow_initial_target": True,
                    },
                }
            ],
            "agent_loop": {
                "ready_for_approval": True,
                "next_action": "validate",
                "attempts": [
                    {
                        "attempt": 1,
                        "stage": "event_rule_generation",
                        "generator": "event-rule-generator",
                        "success": True,
                    }
                ],
            },
        },
        "event_rule_validation": {
            "success": True,
            "rule_count": 1,
            "collision_result": {"collision_count": 0},
            "agent_loop": {
                "ready_for_approval": True,
                "next_action": "submit_for_approval",
                "attempts": [
                    {
                        "attempt": 1,
                        "stage": "event_rule_validation",
                        "generator": "event-rule-validator",
                        "success": True,
                    }
                ],
            },
        },
        "event_rule_replay": {
            "success": True,
            "rule_count": 1,
            "sampled_change_count": 3,
            "matched_change_count": 1,
            "estimated_daily_event_count": 1,
            "collision_count": 0,
        },
    }

    result = run_persist(params)
    try:
        assert result["success"] is True
        assert result["asset_count"] == 3
        assert {job["job_type"] for job in result["jobs"]} == {
            "OBSERVATION_CONTRACT",
            "CHANGE_POLICY",
            "EVENT_RULE",
        }
        assert all(job["status"] == "pending_approval" for job in result["jobs"])
        assert all(job["asset_version_id"] for job in result["jobs"])
        assert all(job["asset_digest"] for job in result["jobs"])
        contract_job = next(
            job for job in result["jobs"] if job["job_type"] == "OBSERVATION_CONTRACT"
        )
        change_job = next(
            job for job in result["jobs"] if job["job_type"] == "CHANGE_POLICY"
        )
        event_job = next(
            job for job in result["jobs"] if job["job_type"] == "EVENT_RULE"
        )
        assert contract_job["validation_report_json"]["agent_loop"]["attempt_count"] == 1
        assert change_job["validation_report_json"]["agent_loop"]["attempt_count"] == 1
        assert event_job["validation_report_json"]["agent_loop"]["attempt_count"] == 2

        with get_db_session() as db:
            contract = (
                db.query(ObservationContractRecord)
                .filter(ObservationContractRecord.schema_id == schema_id)
                .first()
            )
            assert contract is not None
            assert contract.status == "draft"
            policy_version_id = next(
                job["asset_version_id"]
                for job in result["jobs"]
                if job["job_type"] == "CHANGE_POLICY"
            )
            event_version_id = next(
                job["asset_version_id"]
                for job in result["jobs"]
                if job["job_type"] == "EVENT_RULE"
            )
            assert db.get(ChangePolicyVersionRecord, policy_version_id).status == "draft"
            assert db.get(EventRuleVersionRecord, event_version_id).status == "draft"
    finally:
        job_ids = [job["id"] for job in result.get("jobs", [])]
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_(job_ids)
            ).delete(synchronize_session=False)
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.schema_id == schema_id
            ).delete(synchronize_session=False)
            db.query(ChangePolicyVersionRecord).filter(
                ChangePolicyVersionRecord.source_job_id.in_(job_ids)
            ).delete(synchronize_session=False)
            db.query(ChangePolicyRecord).filter(
                ChangePolicyRecord.name == f"policy:device.cpu.utilization:{suffix}"
            ).delete(synchronize_session=False)
            version_rows = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.source_job_id.in_(job_ids))
                .all()
            )
            rule_ids = [row.rule_id for row in version_rows]
            db.query(EventRuleVersionRecord).filter(
                EventRuleVersionRecord.source_job_id.in_(job_ids)
            ).delete(synchronize_session=False)
            if rule_ids:
                db.query(EventRuleRecord).filter(
                    EventRuleRecord.id.in_(rule_ids)
                ).delete(synchronize_session=False)


def test_rule_asset_persister_rejects_event_rules_when_replay_fails():
    suffix = uuid.uuid4().hex
    params = {
        "source_ref": f"unit:{suffix}",
        "created_by": "pytest",
        "event_rule_result": {
            "success": True,
            "rules": [
                {
                    "name": f"pytest-cpu-high-replay-fail-{suffix}",
                    "event_type": "CPUHigh",
                    "severity": "warning",
                    "condition": {
                        "kind": "threshold",
                        "fields": ["cpu_5s"],
                        "gte": 90,
                        "entity_types": ["cpu"],
                    },
                }
            ],
        },
        "event_rule_validation": {
            "success": True,
            "rule_count": 1,
            "collision_result": {"collision_count": 0},
        },
        "event_rule_replay": {
            "success": False,
            "rule_count": 1,
            "sampled_change_count": 2,
            "matched_change_count": 2,
            "estimated_daily_event_count": 2,
            "collision_count": 1,
            "unexpected_matches": [{"collision_type": "candidate_active"}],
        },
    }

    result = run_persist(params)
    try:
        assert result["success"] is True
        assert result["asset_count"] == 1
        job = result["jobs"][0]
        assert job["job_type"] == "EVENT_RULE"
        assert job["status"] == "rejected"
        assert job["validation_report_json"]["success"] is False
        assert (
            job["validation_report_json"]["historical_replay"]["collision_count"] == 1
        )
    finally:
        job_ids = [job["id"] for job in result.get("jobs", [])]
        with get_db_session() as db:
            version_rows = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.source_job_id.in_(job_ids))
                .all()
            )
            rule_ids = [row.rule_id for row in version_rows]
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_(job_ids)
            ).delete(synchronize_session=False)
            db.query(EventRuleVersionRecord).filter(
                EventRuleVersionRecord.source_job_id.in_(job_ids)
            ).delete(synchronize_session=False)
            if rule_ids:
                db.query(EventRuleRecord).filter(
                    EventRuleRecord.id.in_(rule_ids)
                ).delete(synchronize_session=False)
