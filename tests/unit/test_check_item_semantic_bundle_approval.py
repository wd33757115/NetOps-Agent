from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src.core.rule_control.approval import RuleAssetApprovalService
from src.infrastructure.db.models import (
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


def test_check_item_semantic_bundle_activates_policy_and_rule_without_contract():
    """A B-driven rule bundle must use the normal atomic approval path."""

    suffix = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    observation_type = f"pytest.check_item.cpu.{suffix}"
    policy_id = f"policy-semantic-{suffix}"
    policy_version_id = f"policy-semantic-v1-{suffix}"
    rule_id = f"rule-semantic-{suffix}"
    rule_version_id = f"rule-semantic-v1-{suffix}"
    bundle_id = f"bundle-semantic-{suffix}"
    policy_job_id = f"job-policy-semantic-{suffix}"
    rule_job_id = f"job-rule-semantic-{suffix}"
    requirements = {
        "schema_version": "rule-asset-requirements/v1",
        "requires_observation_contract": False,
        "requires_change_policy": True,
        "requires_current_state_rule": True,
        "requires_event_rule": False,
        "requires_event_rule_assets": True,
        "requires_historical_replay": False,
        "change_policy_observation_types": [observation_type],
        "current_state_observation_types": [observation_type],
    }

    try:
        with get_db_session() as db:
            db.add_all(
                [
                    ChangePolicyRecord(
                        id=policy_id,
                        name=f"pytest.semantic.policy.{suffix}",
                        observation_type=observation_type,
                        status="draft",
                        current_version=0,
                        created_at=now,
                        updated_at=now,
                    ),
                    ChangePolicyVersionRecord(
                        id=policy_version_id,
                        policy_id=policy_id,
                        version=1,
                        observation_type=observation_type,
                        semantic_type="gauge",
                        compare_mode="numeric_delta",
                        policy_json={"absolute_delta": 5},
                        validation_report_json={"success": True},
                        status="draft",
                        digest="a" * 64,
                        created_by="candidate-creator",
                        created_at=now,
                    ),
                    EventRuleRecord(
                        id=rule_id,
                        name=f"pytest.semantic.rule.{suffix}",
                        status="draft",
                        current_version=0,
                        created_at=now,
                        updated_at=now,
                    ),
                    EventRuleVersionRecord(
                        id=rule_version_id,
                        rule_id=rule_id,
                        version=1,
                        event_type="PytestCpuHigh",
                        severity="warning",
                        condition_json={
                            "kind": "threshold",
                            "observation_types": [observation_type],
                        },
                        event_class="fault",
                        evaluation_scope="current_state",
                        recovery_json={},
                        correlation_key_json=["device_id", "entity_key"],
                        status="draft",
                        validation_report_json={"success": True},
                        digest="b" * 64,
                        created_by="candidate-creator",
                        created_at=now,
                    ),
                    RuleGenerationJobRecord(
                        id=policy_job_id,
                        job_type="CHANGE_POLICY",
                        status="pending_approval",
                        source_ref=f"rule_asset_bundle:{bundle_id}",
                        asset_type="CHANGE_POLICY",
                        asset_id=policy_id,
                        asset_version_id=policy_version_id,
                        asset_digest="a" * 64,
                        payload_json={
                            "policy": {"observation_type": observation_type}
                        },
                        validation_report_json={"success": True},
                        created_by="candidate-creator",
                        created_at=now,
                        updated_at=now,
                    ),
                    RuleGenerationJobRecord(
                        id=rule_job_id,
                        job_type="EVENT_RULE",
                        status="pending_approval",
                        source_ref=f"rule_asset_bundle:{bundle_id}",
                        asset_type="EVENT_RULE",
                        asset_id=rule_id,
                        asset_version_id=rule_version_id,
                        asset_digest="b" * 64,
                        payload_json={"rule": {"event_type": "PytestCpuHigh"}},
                        validation_report_json={"success": True},
                        created_by="candidate-creator",
                        created_at=now,
                        updated_at=now,
                    ),
                    RuleGenerationJobRecord(
                        id=bundle_id,
                        job_type="RULE_ASSET_BUNDLE",
                        status="pending_approval",
                        source_ref="check_item_rule_bundle:pytest",
                        asset_type="CHECK_ITEM_RULES",
                        payload_json={
                            "child_job_ids": [policy_job_id, rule_job_id],
                            "asset_requirements": requirements,
                            "observation_type": observation_type,
                        },
                        validation_report_json={
                            "success": True,
                            "context_gate": {
                                "success": True,
                                "mode": "check_item_semantics",
                                "context_digest": f"semantic-{suffix}",
                            },
                            "historical_replay": {"success": True, "skipped": True},
                            "bundle_gate": {
                                "success": True,
                                "asset_requirements": requirements,
                                "asset_counts": {
                                    "CHANGE_POLICY": 1,
                                    "EVENT_RULE": 1,
                                },
                            },
                            "governance": {
                                "engineering_review": {
                                    "decision": "accepted",
                                    "actor": "engineering-reviewer",
                                }
                            },
                        },
                        created_by="candidate-creator",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

        result = RuleAssetApprovalService().approve(
            bundle_id,
            actor="release-approver",
            reason="pytest semantic bundle approval",
        )

        assert result["success"] is True
        assert result["atomic"] is True
        assert {item["asset_type"] for item in result["asset_activations"]} == {
            "CHANGE_POLICY",
            "EVENT_RULE",
        }
        with get_db_session() as db:
            assert db.get(ChangePolicyVersionRecord, policy_version_id).status == "active"
            assert db.get(EventRuleVersionRecord, rule_version_id).status == "active"
            assert db.get(RuleGenerationJobRecord, bundle_id).status == "active"
            assert db.get(RuleGenerationJobRecord, policy_job_id).status == "active"
            assert db.get(RuleGenerationJobRecord, rule_job_id).status == "active"
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_([bundle_id, policy_job_id, rule_job_id])
            ).delete(synchronize_session=False)
            db.query(EventRuleVersionRecord).filter(
                EventRuleVersionRecord.id == rule_version_id
            ).delete(synchronize_session=False)
            db.query(EventRuleRecord).filter(EventRuleRecord.id == rule_id).delete(
                synchronize_session=False
            )
            db.query(ChangePolicyVersionRecord).filter(
                ChangePolicyVersionRecord.id == policy_version_id
            ).delete(synchronize_session=False)
            db.query(ChangePolicyRecord).filter(ChangePolicyRecord.id == policy_id).delete(
                synchronize_session=False
            )
