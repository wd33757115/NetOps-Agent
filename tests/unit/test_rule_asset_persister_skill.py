from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

from src.core.rule_control.approval import RuleAssetApprovalService
from src.core.rule_control.engineering_revision import RuleAssetEngineeringRevisionService
from src.core.rule_control.jobs import RuleGenerationJobStateError
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


def test_persister_links_latest_bundle_to_generation_seed():
    updates = []

    class FakeRepository:
        @staticmethod
        def get_job(job_id):
            assert job_id == "seed-1"
            return {
                "id": job_id,
                "source_ref": "parser_template:template-1",
                "payload_json": {"workflow": "rule-asset-generation"},
            }

        @staticmethod
        def update_payload(job_id, *, payload):
            updates.append((job_id, payload))

    MODULE._link_seed_to_bundle(
        FakeRepository(),
        {
            "rule_generation_seed_job_id": "seed-1",
            "rule_generation_context_id": "context-1",
            "source_ref": "parser_template:template-1",
        },
        {"id": "bundle-1", "status": "rejected"},
    )

    assert updates == [
        (
            "seed-1",
            {
                "workflow": "rule-asset-generation",
                "latest_bundle_id": "bundle-1",
                "latest_bundle_status": "rejected",
                "latest_bundle_context_id": "context-1",
            },
        )
    ]


def _accept_engineering_review(bundle_id: str) -> None:
    RuleAssetEngineeringRevisionService().review(
        bundle_id,
        decision="accepted",
        actor="rule-engineer",
        reason="candidate semantics and replay evidence verified",
    )


def test_parser_template_source_is_the_stable_contract_identity():
    params = {
        "source_ref": "parser_template:template-123",
        "vendor": "Vendor-A",
        "model": "Model-A",
        "command": "display something",
    }

    assert MODULE._source_template_id(params) == "template-123"
    assert MODULE._schema_id(params, {"entity_type": "sensor"}) == "template-123"

    changed_platform = {
        **params,
        "vendor": "Vendor-B",
        "model": "Model-B",
        "command": "show something else",
    }
    assert MODULE._schema_id(changed_platform, {"entity_type": "different"}) == "template-123"


def test_rule_asset_persister_rejects_implicit_vendor_model_command_identity():
    with pytest.raises(ValueError, match="stable schema_id is required"):
        MODULE._schema_id(
            {
                "source_ref": "workflow:ad-hoc",
                "vendor": "Vendor-A",
                "model": "Model-A",
                "command": "display something",
            },
            {"entity_type": "sensor"},
        )


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
                "canonical_entity_type": "cpu_scope",
                "entity_identity_fields": ["scope", "scope_id"],
                "entity_dimensions": {
                    "scope": {"literal": "slot"},
                    "scope_id": {"field": "slot"},
                },
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
        assert result["bundle"]["status"] == "pending_approval"
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
        change_job = next(job for job in result["jobs"] if job["job_type"] == "CHANGE_POLICY")
        event_job = next(job for job in result["jobs"] if job["job_type"] == "EVENT_RULE")
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
            assert contract.canonical_entity_type == "cpu_scope"
            assert contract.entity_identity_fields_json == ["scope", "scope_id"]
            assert contract.entity_dimensions_json == {
                "scope": {"literal": "slot"},
                "scope_id": {"field": "slot"},
            }
            policy_version_id = next(
                job["asset_version_id"]
                for job in result["jobs"]
                if job["job_type"] == "CHANGE_POLICY"
            )
            event_version_id = next(
                job["asset_version_id"] for job in result["jobs"] if job["job_type"] == "EVENT_RULE"
            )
            assert db.get(ChangePolicyVersionRecord, policy_version_id).status == "draft"
            assert db.get(EventRuleVersionRecord, event_version_id).status == "draft"

        with pytest.raises(RuleGenerationJobStateError, match="governed through bundle"):
            RuleAssetApprovalService().approve(contract_job["id"], actor="pytest")
        _accept_engineering_review(result["bundle"]["id"])
        activated = RuleAssetApprovalService().approve(
            result["bundle"]["id"],
            actor="pytest",
        )
        assert activated["atomic"] is True
        assert len(activated["asset_activations"]) == 3
        with get_db_session() as db:
            assert (
                db.get(ObservationContractRecord, contract_job["asset_version_id"]).status
                == "active"
            )
            assert (
                db.get(ChangePolicyVersionRecord, change_job["asset_version_id"]).status == "active"
            )
            assert db.get(EventRuleVersionRecord, event_job["asset_version_id"]).status == "active"
    finally:
        job_ids = [job["id"] for job in result.get("jobs", [])]
        bundle_id = (result.get("bundle") or {}).get("id")
        all_job_ids = [*job_ids, *([bundle_id] if bundle_id else [])]
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_(all_job_ids)
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
                db.query(EventRuleRecord).filter(EventRuleRecord.id.in_(rule_ids)).delete(
                    synchronize_session=False
                )


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
        assert job["validation_report_json"]["historical_replay"]["collision_count"] == 1
    finally:
        job_ids = [job["id"] for job in result.get("jobs", [])]
        bundle_id = (result.get("bundle") or {}).get("id")
        all_job_ids = [*job_ids, *([bundle_id] if bundle_id else [])]
        with get_db_session() as db:
            version_rows = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.source_job_id.in_(job_ids))
                .all()
            )
            rule_ids = [row.rule_id for row in version_rows]
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_(all_job_ids)
            ).delete(synchronize_session=False)
            db.query(EventRuleVersionRecord).filter(
                EventRuleVersionRecord.source_job_id.in_(job_ids)
            ).delete(synchronize_session=False)
            if rule_ids:
                db.query(EventRuleRecord).filter(EventRuleRecord.id.in_(rule_ids)).delete(
                    synchronize_session=False
                )


def test_rule_asset_bundle_atomically_activates_multiple_change_policies():
    suffix = uuid.uuid4().hex
    schema_id = f"cli:H3C:LAB:display transceiver:{suffix}"
    rx_observation_type = f"pytest.interface.optical.rx_power.{suffix}"
    tx_observation_type = f"pytest.interface.optical.tx_power.{suffix}"
    params = {
        "source_ref": f"unit:multi-policy:{suffix}",
        "created_by": "pytest",
        "schema_id": schema_id,
        "command": "display transceiver diagnosis interface",
        "vendor": "H3C",
        "model": "LAB",
        "contract_result": {
            "success": True,
            "proposal": {
                "category": "interface_optical",
                "entity_type": "interface_optical",
                "primary_keys": ["interface"],
                "required_fields": ["interface"],
                "optional_fields": ["rx_power_dbm", "tx_power_dbm"],
                "state_semantics": {
                    "interface": {"semantic_type": "identity"},
                    "rx_power_dbm": {
                        "semantic_type": "gauge",
                        "observation_type": rx_observation_type,
                    },
                    "tx_power_dbm": {
                        "semantic_type": "gauge",
                        "observation_type": tx_observation_type,
                    },
                },
            },
        },
        "change_policy_result": {
            "success": True,
            "generation_provider": "builtin",
            "results": [
                {
                    "success": True,
                    "policy": {
                        "observation_type": rx_observation_type,
                        "semantic_type": "gauge",
                        "compare_mode": "threshold_low_and_deadband",
                        "thresholds": {"low": -17},
                        "recovery_threshold": -15,
                    },
                    "validation": {"success": True},
                },
                {
                    "success": True,
                    "policy": {
                        "observation_type": tx_observation_type,
                        "semantic_type": "gauge",
                        "compare_mode": "threshold_low_and_deadband",
                        "thresholds": {"low": -17},
                        "recovery_threshold": -15,
                    },
                    "validation": {"success": True},
                },
            ],
        },
        "event_rule_result": {
            "success": True,
            "rules": [
                {
                    "name": f"pytest-optical-low-{suffix}",
                    "event_type": "OpticalRxPowerLow",
                    "severity": "warning",
                    "condition": {
                        "kind": "threshold_lte",
                        "fields": ["rx_power_dbm"],
                        "lte": -17,
                        "entity_types": ["interface_optical"],
                    },
                }
            ],
        },
        "event_rule_validation": {"success": True},
        "event_rule_replay": {
            "success": True,
            "collision_count": 0,
            "sampled_change_count": 1,
            "total_change_count": 1,
            "matched_change_count": 1,
        },
    }

    result = run_persist(params)
    try:
        policy_jobs = [job for job in result["jobs"] if job["job_type"] == "CHANGE_POLICY"]
        assert len(policy_jobs) == 2
        assert result["bundle"]["status"] == "pending_approval"

        _accept_engineering_review(result["bundle"]["id"])
        activated = RuleAssetApprovalService().approve(result["bundle"]["id"], actor="pytest")

        assert activated["atomic"] is True
        assert (
            sum(item["asset_type"] == "CHANGE_POLICY" for item in activated["asset_activations"])
            == 2
        )
        with get_db_session() as db:
            assert all(
                db.get(ChangePolicyVersionRecord, job["asset_version_id"]).status == "active"
                for job in policy_jobs
            )
    finally:
        job_ids = [job["id"] for job in result.get("jobs", [])]
        bundle_id = (result.get("bundle") or {}).get("id")
        with get_db_session() as db:
            event_versions = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.source_job_id.in_(job_ids))
                .all()
            )
            event_rule_ids = [item.rule_id for item in event_versions]
            policy_versions = (
                db.query(ChangePolicyVersionRecord)
                .filter(ChangePolicyVersionRecord.source_job_id.in_(job_ids))
                .all()
            )
            policy_ids = [item.policy_id for item in policy_versions]
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_([*job_ids, bundle_id])
            ).delete(synchronize_session=False)
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.schema_id == schema_id
            ).delete(synchronize_session=False)
            db.query(ChangePolicyVersionRecord).filter(
                ChangePolicyVersionRecord.source_job_id.in_(job_ids)
            ).delete(synchronize_session=False)
            if policy_ids:
                db.query(ChangePolicyRecord).filter(ChangePolicyRecord.id.in_(policy_ids)).delete(
                    synchronize_session=False
                )
            db.query(EventRuleVersionRecord).filter(
                EventRuleVersionRecord.source_job_id.in_(job_ids)
            ).delete(synchronize_session=False)
            if event_rule_ids:
                db.query(EventRuleRecord).filter(EventRuleRecord.id.in_(event_rule_ids)).delete(
                    synchronize_session=False
                )


def _versioned_bundle_params(suffix: str, revision: int) -> dict:
    observation_type = f"pytest.interface.signal.{suffix}"
    return {
        "source_ref": f"unit:rollback:{suffix}",
        "created_by": "pytest",
        "schema_id": f"cli:Generic:LAB:display signal:{suffix}",
        "command": "display signal",
        "vendor": "Generic",
        "model": "LAB",
        "change_policy_name": f"policy:{observation_type}",
        "contract_result": {
            "success": True,
            "proposal": {
                "category": "interface_signal",
                "entity_type": "interface_signal",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "signal_dbm"],
                "optional_fields": [f"revision_{revision}"],
                "state_semantics": {
                    "interface": {"semantic_type": "identity"},
                    "signal_dbm": {
                        "semantic_type": "gauge",
                        "observation_type": observation_type,
                        "unit": "dbm",
                    },
                },
            },
        },
        "change_policy_result": {
            "success": True,
            "policy": {
                "observation_type": observation_type,
                "semantic_type": "gauge",
                "compare_mode": "threshold_low_and_deadband",
                "thresholds": {"low": -10 - revision},
                "deadband": 0.5,
            },
            "validation": {"success": True},
        },
        "event_rule_result": {
            "success": True,
            "rules": [
                {
                    "name": f"pytest-signal-{suffix}.weak-model-low-v{revision}",
                    "event_type": f"SignalLowRevision{revision}",
                    "severity": "warning",
                    "condition": {
                        "kind": "threshold_lte",
                        "fields": ["signal_dbm"],
                        "lte": -10 - revision,
                        "entity_types": ["interface_signal"],
                    },
                }
            ],
        },
        "event_rule_validation": {"success": True},
        "event_rule_replay": {
            "success": True,
            "collision_count": 0,
            "sampled_change_count": 1,
            "total_change_count": 1,
            "matched_change_count": 1,
        },
    }


def test_bundle_rollback_restores_exact_activation_snapshot():
    suffix = uuid.uuid4().hex
    first = run_persist(_versioned_bundle_params(suffix, 1))
    second = None
    third = None
    try:
        _accept_engineering_review(first["bundle"]["id"])
        RuleAssetApprovalService().approve(first["bundle"]["id"], actor="pytest")
        first_versions = {job["job_type"]: job["asset_version_id"] for job in first["jobs"]}

        second = run_persist(_versioned_bundle_params(suffix, 2))
        RuleAssetApprovalService().deprecate(
            second["bundle"]["id"],
            actor="pytest",
            reason="abandon unapproved candidate",
        )
        with get_db_session() as db:
            assert (
                db.get(
                    ObservationContractRecord,
                    first_versions["OBSERVATION_CONTRACT"],
                ).status
                == "active"
            )
            assert (
                db.get(
                    ChangePolicyVersionRecord,
                    first_versions["CHANGE_POLICY"],
                ).status
                == "active"
            )
            assert (
                db.get(
                    EventRuleVersionRecord,
                    first_versions["EVENT_RULE"],
                ).status
                == "active"
            )

        third = run_persist(_versioned_bundle_params(suffix, 3))
        _accept_engineering_review(third["bundle"]["id"])
        activated = RuleAssetApprovalService().approve(third["bundle"]["id"], actor="pytest")
        snapshot = activated["activation_snapshot"]
        previous_ids = {
            item["asset_type"]: item["previous_active_version_ids"] for item in snapshot["assets"]
        }
        assert previous_ids == {
            asset_type: [version_id] for asset_type, version_id in first_versions.items()
        }
        assert {item["job_id"] for item in snapshot["superseded_jobs"]} == {
            job["id"] for job in first["jobs"]
        }
        assert snapshot["superseded_bundles"] == [
            {"bundle_id": first["bundle"]["id"], "status_before": "active"}
        ]
        with get_db_session() as db:
            first_event = db.get(
                EventRuleVersionRecord,
                first_versions["EVENT_RULE"],
            )
            third_event_job = next(job for job in third["jobs"] if job["job_type"] == "EVENT_RULE")
            third_event = db.get(
                EventRuleVersionRecord,
                third_event_job["asset_version_id"],
            )
            assert first_event.rule_id != third_event.rule_id
            assert db.get(EventRuleRecord, first_event.rule_id).status == "deprecated"
            assert db.get(EventRuleRecord, third_event.rule_id).status == "active"
            assert db.get(RuleGenerationJobRecord, first["bundle"]["id"]).status == ("deprecated")
            assert all(
                db.get(RuleGenerationJobRecord, job["id"]).status == "deprecated"
                for job in first["jobs"]
            )

        rolled_back = RuleAssetApprovalService().deprecate(
            third["bundle"]["id"],
            actor="pytest",
            reason="rollback rehearsal",
        )
        assert {item["asset_version_id"] for item in rolled_back["restored_assets"]} == set(
            first_versions.values()
        )
        assert set(rolled_back["restored_governance"]["jobs"]) == {
            job["id"] for job in first["jobs"]
        }
        assert rolled_back["restored_governance"]["bundles"] == [first["bundle"]["id"]]
        with get_db_session() as db:
            for asset_type, model in (
                ("OBSERVATION_CONTRACT", ObservationContractRecord),
                ("CHANGE_POLICY", ChangePolicyVersionRecord),
                ("EVENT_RULE", EventRuleVersionRecord),
            ):
                assert db.get(model, first_versions[asset_type]).status == "active"
                second_job = next(job for job in second["jobs"] if job["job_type"] == asset_type)
                assert db.get(model, second_job["asset_version_id"]).status == "deprecated"
            first_event = db.get(
                EventRuleVersionRecord,
                first_versions["EVENT_RULE"],
            )
            third_event_job = next(job for job in third["jobs"] if job["job_type"] == "EVENT_RULE")
            third_event = db.get(
                EventRuleVersionRecord,
                third_event_job["asset_version_id"],
            )
            assert db.get(EventRuleRecord, first_event.rule_id).status == "active"
            assert db.get(EventRuleRecord, first_event.rule_id).current_version == int(
                first_event.version
            )
            assert db.get(EventRuleRecord, third_event.rule_id).status == "deprecated"
            assert db.get(EventRuleRecord, third_event.rule_id).current_version == 0
            assert db.get(RuleGenerationJobRecord, first["bundle"]["id"]).status == "active"
            assert all(
                db.get(RuleGenerationJobRecord, job["id"]).status == "active"
                for job in first["jobs"]
            )
    finally:
        results = [item for item in (first, second, third) if item]
        job_ids = [job["id"] for result in results for job in result["jobs"]]
        bundle_ids = [result["bundle"]["id"] for result in results]
        schema_id = f"cli:Generic:LAB:display signal:{suffix}"
        with get_db_session() as db:
            policy_versions = (
                db.query(ChangePolicyVersionRecord)
                .filter(ChangePolicyVersionRecord.source_job_id.in_(job_ids))
                .all()
            )
            policy_ids = {item.policy_id for item in policy_versions}
            event_versions = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.source_job_id.in_(job_ids))
                .all()
            )
            event_rule_ids = {item.rule_id for item in event_versions}
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.schema_id == schema_id
            ).delete(synchronize_session=False)
            db.query(ChangePolicyVersionRecord).filter(
                ChangePolicyVersionRecord.source_job_id.in_(job_ids)
            ).delete(synchronize_session=False)
            if policy_ids:
                db.query(ChangePolicyRecord).filter(ChangePolicyRecord.id.in_(policy_ids)).delete(
                    synchronize_session=False
                )
            db.query(EventRuleVersionRecord).filter(
                EventRuleVersionRecord.source_job_id.in_(job_ids)
            ).delete(synchronize_session=False)
            if event_rule_ids:
                db.query(EventRuleRecord).filter(EventRuleRecord.id.in_(event_rule_ids)).delete(
                    synchronize_session=False
                )
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_([*job_ids, *bundle_ids])
            ).delete(synchronize_session=False)
