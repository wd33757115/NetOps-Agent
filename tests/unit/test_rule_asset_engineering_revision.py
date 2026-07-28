from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src.core.rule_control.engineering_revision import RuleAssetEngineeringRevisionService
from src.core.rule_control.repository import RuleGenerationJobRepository
from src.infrastructure.db.models import (
    ObservationContractRecord,
    OfflinePatrolJobRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _create_bundle() -> dict[str, str]:
    suffix = uuid.uuid4().hex[:12]
    ids = {
        "bundle": f"bundle-review-{suffix}",
        "contract_job": f"contract-job-{suffix}",
        "policy_job": f"policy-job-{suffix}",
        "rule_job": f"rule-job-{suffix}",
        "contract": f"contract-{suffix}",
        "context": f"context-{suffix}",
        "offline_job": f"offline-{suffix}",
    }
    now = datetime.now(timezone.utc)
    contract = {
        "category": "interface",
        "entity_type": "interface_l2",
        "primary_keys": ["interface"],
        "required_fields": ["interface", "status"],
        "optional_fields": [],
        "ignore_fields": [],
        "allow_empty": False,
        "validators": {},
        "state_semantics": {
            "status": {
                "semantic_type": "state",
                "observation_type": "interface.oper_status",
            }
        },
    }
    policy = {
        "observation_type": "interface.oper_status",
        "semantic_type": "state",
        "compare_mode": "state_transition",
        "reason": "engineer review test",
    }
    rule = {
        "name": f"interface-down-{suffix}",
        "event_type": "InterfaceDown",
        "severity": "warning",
        "condition": {
            "observation_types": ["interface.oper_status"],
            "field": "status",
            "operator": "changed_to",
            "value": "down",
        },
    }
    with get_db_session() as db:
        db.add(
            OfflinePatrolJobRecord(
                id=ids["offline_job"],
                idempotency_key=f"offline-idempotency-{suffix}",
                purpose="rule_asset_source",
                job_type="parser_validation",
                name="Engineering revision source",
                owner_id="pytest",
                status="completed",
                progress_json={"phase": "completed", "percent": 100},
                created_at=now,
                updated_at=now,
                completed_at=now,
            )
        )
        db.add_all(
            [
                RuleGenerationJobRecord(
                    id=ids["bundle"],
                    job_type="RULE_ASSET_BUNDLE",
                    status="pending_approval",
                    source_ref=f"parser_template:template-{suffix}",
                    asset_type="RULE_ASSET_BUNDLE",
                    asset_id=ids["bundle"],
                    asset_version_id=ids["context"],
                    asset_digest=f"bundle-digest-{suffix}",
                    payload_json={
                        "child_job_ids": [
                            ids["contract_job"],
                            ids["policy_job"],
                            ids["rule_job"],
                        ],
                        "context_id": ids["context"],
                        "command": "display interface brief",
                        "vendor": "H3C",
                        "model": "LAB",
                    },
                    validation_report_json={
                        "success": True,
                        "bundle_gate": {"success": True, "blockers": []},
                        "context_gate": {"success": True},
                        "historical_replay": {"success": True, "collision_count": 0},
                    },
                    created_by="pytest",
                    created_at=now,
                    updated_at=now,
                ),
                RuleGenerationJobRecord(
                    id=ids["contract_job"],
                    job_type="OBSERVATION_CONTRACT",
                    status="pending_approval",
                    source_ref=f"rule_asset_bundle:{ids['bundle']}",
                    asset_type="OBSERVATION_CONTRACT",
                    asset_id=ids["contract"],
                    asset_version_id=ids["contract"],
                    asset_digest=f"contract-digest-{suffix}",
                    payload_json={"proposal": contract},
                    validation_report_json={"success": True},
                    created_by="pytest",
                    created_at=now,
                    updated_at=now,
                ),
                RuleGenerationJobRecord(
                    id=ids["policy_job"],
                    job_type="CHANGE_POLICY",
                    status="pending_approval",
                    source_ref=f"rule_asset_bundle:{ids['bundle']}",
                    asset_type="CHANGE_POLICY",
                    payload_json={"policy": policy},
                    validation_report_json={"success": True},
                    created_by="pytest",
                    created_at=now,
                    updated_at=now,
                ),
                RuleGenerationJobRecord(
                    id=ids["rule_job"],
                    job_type="EVENT_RULE",
                    status="pending_approval",
                    source_ref=f"rule_asset_bundle:{ids['bundle']}",
                    asset_type="EVENT_RULE",
                    payload_json={"rule": rule},
                    validation_report_json={"success": True},
                    created_by="pytest",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.flush()
        db.add(
            RuleGenerationContextRecord(
                id=ids["context"],
                job_id=ids["bundle"],
                offline_job_id=ids["offline_job"],
                dataset_ids_json=[f"dataset-{suffix}"],
                context_json={
                    "records": [{"interface": "Gi1/0/1", "status": "up"}],
                    "record_groups": [],
                    "replay_run_ids": [f"run-{suffix}"],
                },
                context_digest=f"context-digest-{suffix}",
                privacy_report_json={
                    "success": True,
                    "raw_cli_included": False,
                    "scope_type": "offline_period_authoritative_runs",
                },
                created_by="pytest",
                created_at=now,
            )
        )
        db.add(
            ObservationContractRecord(
                id=ids["contract"],
                source_type="cli",
                schema_id=f"schema-{suffix}",
                schema_ref=f"parser_template:template-{suffix}",
                entity_type="interface_l2",
                primary_keys_json=["interface"],
                required_fields_json=["interface", "status"],
                optional_fields_json=[],
                validators_json={},
                ignore_fields_json=[],
                allow_empty=False,
                state_semantics_json=contract["state_semantics"],
                version=1,
                contract_sha256=f"contract-digest-{suffix}",
                status="draft",
                source_job_id=ids["contract_job"],
                created_at=now,
                updated_at=now,
            )
        )
    return ids


def _cleanup(ids: dict[str, str]) -> None:
    with get_db_session() as db:
        db.query(RuleGenerationContextRecord).filter(
            RuleGenerationContextRecord.id == ids["context"]
        ).delete(synchronize_session=False)
        db.query(ObservationContractRecord).filter(
            ObservationContractRecord.id == ids["contract"]
        ).delete(synchronize_session=False)
        db.query(RuleGenerationJobRecord).filter(
            RuleGenerationJobRecord.id.in_(
                [ids["bundle"], ids["contract_job"], ids["policy_job"], ids["rule_job"]]
            )
        ).delete(synchronize_session=False)
        db.query(OfflinePatrolJobRecord).filter(
            OfflinePatrolJobRecord.id == ids["offline_job"]
        ).delete(synchronize_session=False)


def test_engineering_review_exposes_candidates_and_records_acceptance():
    ids = _create_bundle()
    try:
        service = RuleAssetEngineeringRevisionService()
        draft = service.get_draft(ids["bundle"])

        assert draft["completeness"]["complete"] is True
        assert draft["contract"]["entity_type"] == "interface_l2"
        assert len(draft["change_policies"]) == 1
        assert len(draft["event_rules"]) == 1

        result = service.review(
            ids["bundle"],
            decision="accepted",
            actor="rule-engineer",
            reason="字段语义和事件条件符合样本",
        )

        assert result["review"]["decision"] == "accepted"
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, ids["bundle"])
            review = (row.validation_report_json["governance"])["engineering_review"]
            assert review["actor"] == "rule-engineer"
    finally:
        _cleanup(ids)


def test_engineering_revision_creates_replacement_bundle_after_validation(monkeypatch):
    ids = _create_bundle()
    created_bundle_ids: list[str] = []

    def fake_execute(skill_name, params):
        if skill_name == "observation-contract-generator":
            assert params["allow_semantic_overrides"] is True
            assert params["require_policy_targets"] is True
            return {
                "success": True,
                "proposal": params["proposal"],
                "validation": {"success": True},
            }
        if skill_name == "change-policy-generator":
            return {"success": True, "policy": params["proposal"], "validation": {"success": True}}
        if skill_name == "event-rule-validator":
            return {"success": True, "validation": {"success": True}}
        if skill_name == "event-rule-replay":
            return {
                "success": True,
                "collision_count": 0,
                "sampled_change_count": 1,
                "matched_change_count": 1,
                "scope": {
                    "mode": "run_ids",
                    "run_ids": params["run_ids"],
                    "run_id_count": len(params["run_ids"]),
                },
            }
        if skill_name == "rule-asset-persister":
            repo = RuleGenerationJobRepository()
            created = repo.create_job(
                job_type="RULE_ASSET_BUNDLE",
                source_ref=params["source_ref"],
                payload={"context_id": params["rule_generation_context_id"], "child_job_ids": []},
                validation_report={"success": True, "governance": {}},
                created_by=params["created_by"],
            )
            repo.transition_job(created["id"], status="validating")
            pending = repo.transition_job(created["id"], status="pending_approval")
            created_bundle_ids.append(created["id"])
            return {"success": True, "bundle": pending}
        raise AssertionError(f"unexpected skill: {skill_name}")

    monkeypatch.setattr(
        "src.core.rule_control.engineering_revision.execute_skill",
        fake_execute,
    )
    try:
        draft = RuleAssetEngineeringRevisionService().get_draft(ids["bundle"])
        result = RuleAssetEngineeringRevisionService().revise(
            ids["bundle"],
            contract=draft["contract"],
            change_policies=draft["change_policies"],
            event_rules=draft["event_rules"],
            actor="rule-engineer",
            reason="修正并重新验证候选资产",
            idempotency_key=f"revision-{uuid.uuid4().hex}",
        )

        assert result["created"] is True
        replacement_id = result["bundle"]["id"]
        with get_db_session() as db:
            replacement = db.get(RuleGenerationJobRecord, replacement_id)
            parent = db.get(RuleGenerationJobRecord, ids["bundle"])
            assert (
                replacement.payload_json["engineering_revision"]["parent_bundle_id"]
                == ids["bundle"]
            )
            assert (
                parent.validation_report_json["governance"]["engineering_review"][
                    "replacement_bundle_id"
                ]
                == replacement_id
            )
    finally:
        with get_db_session() as db:
            if created_bundle_ids:
                db.query(RuleGenerationJobRecord).filter(
                    RuleGenerationJobRecord.id.in_(created_bundle_ids)
                ).delete(synchronize_session=False)
        _cleanup(ids)
