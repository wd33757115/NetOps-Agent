from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items.assessment_policy import assessment_policy_source_digest
from src.core.check_items.assessment_policy_assets import AssessmentPolicyAssetService
from src.core.check_items.assessment_policy_migration import (
    AssessmentPolicyMigrationService,
)
from src.core.device_ops.repository import reject_new_embedded_assessment_policies
from src.infrastructure.db.models import (
    AssessmentPolicyBindingRecord,
    AssessmentPolicyCutoverRecord,
    AssessmentPolicyDecisionRecord,
    AssessmentPolicyMigrationSourceRecord,
    AssessmentPolicyProfileRecord,
    AssessmentPolicyShadowResultRecord,
    AssessmentPolicyVersionRecord,
    Base,
    CheckItemRecord,
    CheckItemVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    NetworkDevice,
)


@contextmanager
def _scope(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture()
def migration_runtime(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'policy-migration.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            CheckItemRecord.__table__,
            CheckItemVersionRecord.__table__,
            NetworkDevice.__table__,
            EventRuleRecord.__table__,
            EventRuleVersionRecord.__table__,
            AssessmentPolicyProfileRecord.__table__,
            AssessmentPolicyVersionRecord.__table__,
            AssessmentPolicyDecisionRecord.__table__,
            AssessmentPolicyBindingRecord.__table__,
            AssessmentPolicyMigrationSourceRecord.__table__,
            AssessmentPolicyShadowResultRecord.__table__,
            AssessmentPolicyCutoverRecord.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)

    def session_scope():
        return _scope(factory)

    now = datetime.now(timezone.utc)
    embedded_policy = {
        "policy_id": "legacy-cpu-policy",
        "version": "1",
        "check_item_id": "system.cpu_usage",
        "observation_type": "system.cpu.usage",
        "policy_role": "high",
        "status": "approved",
        "approved_by": "legacy-admin",
        "approved_at": now.isoformat(),
        "scope": {"device_ids": ["device-1"]},
        "thresholds": {
            "high": {
                "value": 75,
                "unit": "percent",
            }
        },
        "evidence_refs": ["legacy-policy-file:cpu"],
    }
    embedded_policy["digest"] = assessment_policy_source_digest(embedded_policy)
    with session_scope() as db:
        db.add(
            CheckItemRecord(
                id="system.cpu_usage",
                name="CPU 使用率",
                category="system",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            CheckItemVersionRecord(
                id="check-version-cpu-v1",
                check_item_id="system.cpu_usage",
                version=1,
                result_mode="structured",
                assessment_mode="health",
                canonical_contract_json={
                    "observations": [
                        {
                            "observation_type": "system.cpu.usage",
                            "unit": "percent",
                        }
                    ],
                    "assessment_policy": {
                        "thresholds": {
                            "system.cpu.usage": {
                                "required": True,
                                "unit": "percent",
                                "allow_catalog_default": True,
                                "defaults": {
                                    "high": {
                                        "value": 80,
                                        "unit": "percent",
                                    }
                                },
                            }
                        }
                    },
                },
                report_projection="system.cpu_usage",
                applicability_json={},
                empty_result_policy_json={
                    "classification": "expected_empty"
                },
                status="active",
                digest="c" * 64,
                created_by="catalog-owner",
                created_at=now,
                activated_at=now,
            )
        )
        db.add(
            NetworkDevice(
                id="device-1",
                device_name="SW1",
                management_ip="10.0.0.1",
                vendor="H3C",
                model="S6850",
                site="xian",
                role="core",
                status="active",
                attributes_json={
                    "capabilities": ["cpu.telemetry"],
                    "assessment_policy_bindings": [
                        embedded_policy,
                        {"check_item_id": "unknown.item"},
                    ],
                },
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            EventRuleRecord(
                id="rule-cpu-high",
                name="system.cpu_usage.high",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            EventRuleVersionRecord(
                id="rule-cpu-high-v1",
                rule_id="rule-cpu-high",
                version=1,
                event_type="CpuUtilizationHigh",
                severity="warning",
                condition_json={
                    "kind": "threshold",
                    "observation_types": ["system.cpu.usage"],
                    "entity_types": ["cpu_scope"],
                    "gte": 90,
                },
                priority=100,
                exclusive=True,
                event_class="fault",
                evaluation_scope="both",
                recovery_json={},
                correlation_key_json=["device_id", "entity_key"],
                status="active",
                validation_report_json={"passed": True},
                digest="r" * 64,
                created_by="test",
                created_at=now,
                activated_at=now,
            )
        )
    return factory, session_scope


def _approve_and_bind(service, draft, *, binding_key, scope=None):
    version_id = draft["id"]
    service.submit(
        version_id,
        actor="rule-engineer",
        reason="提交历史迁移策略语义复核",
        idempotency_key=f"submit-{binding_key}",
    )
    service.semantic_review(
        version_id,
        decision="approved",
        actor="network-sme",
        reason="确认阈值与适用范围",
        idempotency_key=f"semantic-{binding_key}",
    )
    approved = service.publication_review(
        version_id,
        decision="approved",
        actor="asset-publisher",
        reason="批准进入受控绑定",
        idempotency_key=f"publish-{binding_key}",
    )
    service.bind(
        version_id,
        binding_type="inventory_profile",
        scope=scope or {"device_ids": ["device-1"]},
        actor="asset-publisher",
        reason="绑定影子验证目标",
        idempotency_key=f"binding-{binding_key}",
    )
    return approved


def test_inventory_is_idempotent_and_conversion_only_creates_a_draft(
    migration_runtime,
):
    _factory, session_scope = migration_runtime
    service = AssessmentPolicyMigrationService(session_scope)

    first = service.inventory_legacy_sources(
        actor="migration-admin",
        include_execution_snapshots=False,
    )
    second = service.inventory_legacy_sources(
        actor="migration-admin",
        include_execution_snapshots=False,
    )

    assert first["counts"]["inventoried"] >= 3
    assert first["counts"]["review_required"] >= 2
    assert second["counts"]["unchanged"] == len(first["touched_source_ids"])
    with session_scope() as db:
        source_count = db.query(AssessmentPolicyMigrationSourceRecord).count()
        assert source_count == len(first["touched_source_ids"])
        source = (
            db.query(AssessmentPolicyMigrationSourceRecord)
            .filter(
                AssessmentPolicyMigrationSourceRecord.source_type
                == "inventory_attribute",
                AssessmentPolicyMigrationSourceRecord.conversion_status
                == "inventoried",
            )
            .one()
        )
        source_id = str(source.id)

    converted = service.create_draft_from_source(
        source_id,
        actor="rule-engineer",
    )
    replayed = service.create_draft_from_source(
        source_id,
        actor="rule-engineer",
    )

    assert converted["draft"]["status"] == "draft"
    assert replayed["draft"]["id"] == converted["draft"]["id"]
    assert converted["source"]["conversion_status"] == "draft_created"
    with session_scope() as db:
        assert db.query(AssessmentPolicyVersionRecord).count() == 1
        assert db.query(AssessmentPolicyDecisionRecord).count() == 0
        assert db.query(AssessmentPolicyBindingRecord).count() == 0


def test_unconvertible_legacy_source_stays_in_engineering_review(
    migration_runtime,
):
    _factory, session_scope = migration_runtime
    service = AssessmentPolicyMigrationService(session_scope)
    service.inventory_legacy_sources(
        actor="migration-admin",
        include_execution_snapshots=False,
    )
    with session_scope() as db:
        source = (
            db.query(AssessmentPolicyMigrationSourceRecord)
            .filter(
                AssessmentPolicyMigrationSourceRecord.source_type
                == "check_item_empty_policy"
            )
            .one()
        )
        source_id = str(source.id)

    result = service.create_draft_from_source(
        source_id,
        actor="rule-engineer",
    )

    assert result["draft"] is None
    assert result["source"]["conversion_status"] == "review_required"
    assert "policy_migration_review_required" in result["source"][
        "reason_codes_json"
    ]


def test_inventory_attributes_reject_new_embedded_policy_bodies_but_allow_removal():
    legacy = {
        "policy_id": "legacy-cpu-policy",
        "status": "approved",
        "thresholds": {"high": {"value": 80}},
    }
    existing = {"assessment_policy_bindings": [legacy], "owner": "netops"}

    reject_new_embedded_assessment_policies(
        {"assessment_policy_bindings": [legacy], "owner": "changed"},
        existing_attributes=existing,
    )
    reject_new_embedded_assessment_policies(
        {"owner": "changed"},
        existing_attributes=existing,
    )
    with pytest.raises(ValueError, match="governed AssessmentPolicy"):
        reject_new_embedded_assessment_policies(
            {
                "assessment_policy_bindings": [
                    {**legacy, "thresholds": {"high": {"value": 90}}}
                ]
            },
            existing_attributes=existing,
        )


def test_shadow_comparison_persists_legacy_and_governed_difference(
    migration_runtime,
):
    _factory, session_scope = migration_runtime
    assets = AssessmentPolicyAssetService(session_scope)
    applicability = assets.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="*",
        policy_role="applicability",
        name="CPU 检查适用性",
        policy={
            "mode": "explicit",
            "decision": "applicable",
            "reason_code": "cpu_check_supported",
        },
        evidence_refs=["customer-policy:cpu-applicability"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="create-shadow-applicability",
    )
    _approve_and_bind(
        assets,
        applicability,
        binding_key="shadow-applicability",
    )
    threshold = assets.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="system.cpu.usage",
        policy_role="high",
        name="CPU 高使用率阈值",
        policy={"value": 85, "unit": "percent", "operator": "gte"},
        evidence_refs=["customer-policy:cpu-threshold"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="create-shadow-threshold",
    )
    _approve_and_bind(
        assets,
        threshold,
        binding_key="shadow-threshold",
    )
    migration = AssessmentPolicyMigrationService(session_scope)
    request = {
        "check_item_version_id": "check-version-cpu-v1",
        "device": {"device_id": "device-1"},
        "observation": {
            "id": "observation-cpu-1",
            "observation_type": "system.cpu.usage",
            "device_id": "device-1",
            "entity_type": "cpu_scope",
            "entity_key": "device",
            "value": 82,
            "unit": "percent",
        },
        "rule": {
            "rule_id": "rule-cpu-high",
            "version_id": "rule-cpu-high-v1",
            "digest": "r" * 64,
            "condition": {
                "kind": "threshold",
                "observation_types": ["system.cpu.usage"],
                "gte": 99,
            },
        },
        "actor": "migration-reviewer",
        "policy_effective_at": datetime.now(timezone.utc),
    }

    result = migration.compare_shadow(**request)
    replay = migration.compare_shadow(**request)

    assert result["id"] == replay["id"]
    assert result["comparison_status"] == "different"
    assert result["legacy_result_json"]["threshold"]["value"] == 75.0
    assert result["governed_result_json"]["threshold"]["value"] == 85.0
    assert result["legacy_snapshot"]["legacy_sources_allowed"] is True
    assert result["governed_snapshot"]["legacy_sources_allowed"] is False
    assert "effective_condition" in result["difference_json"]
    with session_scope() as db:
        assert db.query(AssessmentPolicyShadowResultRecord).count() == 1


def test_matched_shadow_can_cut_over_one_target_and_append_a_rollback(
    migration_runtime,
):
    _factory, session_scope = migration_runtime
    assets = AssessmentPolicyAssetService(session_scope)
    applicability = assets.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="*",
        policy_role="applicability",
        name="CPU 检查适用性",
        policy={
            "mode": "explicit",
            "decision": "applicable",
            "reason_code": "cpu_check_supported",
        },
        evidence_refs=["customer-policy:cpu-applicability"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="create-cutover-applicability",
    )
    _approve_and_bind(
        assets,
        applicability,
        binding_key="cutover-applicability",
    )
    threshold = assets.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="system.cpu.usage",
        policy_role="high",
        name="CPU 高使用率阈值",
        policy={"value": 75, "unit": "percent", "operator": "gte"},
        evidence_refs=["customer-policy:cpu-threshold"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="create-cutover-threshold",
    )
    _approve_and_bind(
        assets,
        threshold,
        binding_key="cutover-threshold",
    )
    migration = AssessmentPolicyMigrationService(session_scope)
    shadow = migration.compare_shadow(
        check_item_version_id="check-version-cpu-v1",
        device={"device_id": "device-1"},
        observation={
            "id": "observation-cpu-cutover",
            "observation_type": "system.cpu.usage",
            "device_id": "device-1",
            "entity_type": "cpu_scope",
            "entity_key": "device",
            "value": 82,
            "unit": "percent",
        },
        rule={
            "rule_id": "rule-cpu-high",
            "version_id": "rule-cpu-high-v1",
            "digest": "r" * 64,
            "condition": {
                "kind": "threshold",
                "observation_types": ["system.cpu.usage"],
                "gte": 99,
            },
        },
        actor="migration-reviewer",
        policy_effective_at=datetime.now(timezone.utc),
    )

    assert shadow["comparison_status"] == "matched"
    assert "provenance_transition" in shadow["difference_json"]
    cutover = migration.activate_cutover(
        check_item_version_id="check-version-cpu-v1",
        device_id="device-1",
        shadow_result_ids=[shadow["id"]],
        actor="migration-publisher",
        reason="影子语义一致，批准目标级治理读取",
        idempotency_key="activate-cutover-device-1",
    )
    replay = migration.activate_cutover(
        check_item_version_id="check-version-cpu-v1",
        device_id="device-1",
        shadow_result_ids=[shadow["id"]],
        actor="migration-publisher",
        reason="影子语义一致，批准目标级治理读取",
        idempotency_key="activate-cutover-device-1",
    )

    assert cutover["resolver_mode"] == "governed_only"
    assert len(cutover["binding_ids_json"]) == 2
    assert replay["id"] == cutover["id"]
    assert replay["idempotent_replay"] is True
    assert migration.effective_cutover_modes(
        [("check-version-cpu-v1", "device-1")]
    )[("check-version-cpu-v1", "device-1")]["resolver_mode"] == "governed_only"

    rollback = migration.rollback_cutover(
        cutover["id"],
        actor="migration-publisher",
        reason="回滚演练验证旧读取路径可恢复",
        idempotency_key="rollback-cutover-device-1",
    )

    assert rollback["resolver_mode"] == "legacy_compat"
    assert rollback["supersedes_id"] == cutover["id"]
    assert migration.effective_cutover_modes(
        [("check-version-cpu-v1", "device-1")]
    )[("check-version-cpu-v1", "device-1")]["resolver_mode"] == "legacy_compat"
    with session_scope() as db:
        rows = db.query(AssessmentPolicyCutoverRecord).order_by(
            AssessmentPolicyCutoverRecord.created_at
        ).all()
        assert [row.status for row in rows] == ["superseded", "active"]


def test_shadow_semantics_treat_integer_and_float_thresholds_as_equal():
    legacy = {
        "status": "resolved",
        "blocker_code": None,
        "should_evaluate": True,
        "effective_condition": {"kind": "threshold", "gte": 80},
        "applicability": {"status": "applicable"},
        "threshold": {
            "status": "resolved",
            "value": 80,
            "unit": "percent",
            "hysteresis": 5,
            "threshold_role": "high",
        },
        "expected_state": {"status": "not_required"},
    }
    governed = {
        **legacy,
        "effective_condition": {"kind": "threshold", "gte": 80.0},
        "threshold": {
            **legacy["threshold"],
            "value": 80.0,
            "hysteresis": 5.0,
        },
    }

    differences = AssessmentPolicyMigrationService._differences(
        legacy,
        governed,
    )

    assert set(differences) == {"provenance_transition"}
    assert AssessmentPolicyMigrationService._comparison_status(
        legacy,
        governed,
        differences,
    ) == "matched"
