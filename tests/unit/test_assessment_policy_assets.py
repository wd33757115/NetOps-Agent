from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items.assessment_policy import (
    assessment_policy_source_digest,
    build_target_assessment_policy_snapshot,
    resolve_assessment_basis,
)
from src.core.check_items.assessment_policy_assets import (
    AssessmentPolicyAssetService,
    AssessmentPolicyConflictError,
)
from src.infrastructure.db.models import (
    AssessmentPolicyBindingRecord,
    AssessmentPolicyDecisionRecord,
    AssessmentPolicyProfileRecord,
    AssessmentPolicyVersionRecord,
    Base,
    CheckItemRecord,
    CheckItemVersionRecord,
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
def policy_runtime(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'assessment-policy.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            CheckItemRecord.__table__,
            CheckItemVersionRecord.__table__,
            NetworkDevice.__table__,
            AssessmentPolicyProfileRecord.__table__,
            AssessmentPolicyVersionRecord.__table__,
            AssessmentPolicyDecisionRecord.__table__,
            AssessmentPolicyBindingRecord.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)

    def session_scope():
        return _scope(factory)

    now = datetime.now(timezone.utc)
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
                                "allow_catalog_default": False,
                            }
                        }
                    },
                },
                report_projection="system.cpu_usage",
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
                created_at=now,
                updated_at=now,
            )
        )
    return factory, session_scope, AssessmentPolicyAssetService(session_scope)


def _create_draft(service, *, key="policy-create-001", value=80, profile_id=None):
    return service.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="system.cpu.usage",
        policy_role="high",
        name="核心设备 CPU 高水位",
        policy={
            "value": value,
            "unit": "percent",
            "operator": "gte",
            "hysteresis": 5,
        },
        evidence_refs=["customer-policy:cpu-2026"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key=key,
        profile_id=profile_id,
    )


def _approve(service, version_id):
    service.submit(
        version_id,
        actor="rule-engineer",
        reason="提交策略语义复核",
        idempotency_key=f"submit-{version_id}",
    )
    service.semantic_review(
        version_id,
        decision="approved",
        actor="network-sme",
        reason="阈值与客户运维策略一致",
        idempotency_key=f"semantic-{version_id}",
    )
    return service.publication_review(
        version_id,
        decision="approved",
        actor="asset-publisher",
        reason="批准进入生产绑定",
        idempotency_key=f"publish-{version_id}",
    )


def test_policy_lifecycle_allows_app_user_to_complete_all_stages(policy_runtime):
    _factory, _session_scope, service = policy_runtime
    draft = _create_draft(service)
    version_id = draft["id"]
    service.submit(version_id, actor="rule-engineer", reason="提交语义复核", idempotency_key="policy-submit-001")
    service.semantic_review(version_id, decision="approved", actor="rule-engineer",
                            reason="语义和单位正确", idempotency_key="policy-semantic-001")
    approved = service.publication_review(version_id, decision="approved", actor="rule-engineer",
                                         reason="确认发布批准", idempotency_key="policy-publish-001")
    assert approved["status"] == "approved"
    assert [item["stage"] for item in approved["decisions"]] == ["submission", "semantic_review", "publication"]
    assert approved["approved_by"] == "rule-engineer"


def test_policy_creation_and_decisions_are_idempotent(policy_runtime):
    _factory, _session_scope, service = policy_runtime
    first = _create_draft(service)
    replay = _create_draft(service)
    assert replay["id"] == first["id"]

    with pytest.raises(AssessmentPolicyConflictError, match="different policy version"):
        _create_draft(service, value=90)

    submitted = service.submit(
        first["id"],
        actor="rule-engineer",
        reason="提交语义复核",
        idempotency_key="policy-submit-001",
    )
    replayed = service.submit(
        first["id"],
        actor="rule-engineer",
        reason="提交语义复核",
        idempotency_key="policy-submit-001",
    )
    assert submitted["status"] == replayed["status"] == "pending_review"


def test_approved_inventory_binding_renders_a_verified_snapshot_source(policy_runtime):
    _factory, _session_scope, service = policy_runtime
    draft = _create_draft(service)
    approved = _approve(service, draft["id"])
    binding = service.bind(
        approved["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-1"], "entity_types": ["cpu"]},
        actor="asset-publisher",
        reason="绑定核心设备 CPU 策略",
        idempotency_key="policy-binding-001",
    )

    rendered = service.list_rendered_sources(
        check_item_ids=["system.cpu_usage"]
    )["system.cpu_usage"][0]
    assert rendered["binding_id"] == binding["id"]
    assert rendered["digest"] == assessment_policy_source_digest(rendered)
    assert rendered["thresholds"]["high"]["value"] == 80.0

    snapshot = build_target_assessment_policy_snapshot(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        canonical_contract={
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
                        "allow_catalog_default": False,
                    }
                }
            },
        },
        device={"device_id": "device-1", "site": "xian", "role": "core"},
        governed_sources=[rendered],
        policy_effective_at=datetime.now(timezone.utc),
    )
    result = resolve_assessment_basis(
        rule={
            "rule_id": "cpu-high",
            "version_id": "rule-v1",
            "digest": "r" * 64,
            "condition": {
                "kind": "threshold",
                "threshold_role": "high",
                "gte": 99,
            },
        },
        observation={
            "id": "obs-1",
            "device_id": "device-1",
            "entity_type": "cpu",
            "entity_key": "slot-0",
            "observation_type": "system.cpu.usage",
            "value": 85,
            "unit": "percent",
        },
        policy_snapshot=snapshot,
        runtime_bundle_version_id="bundle-v1",
        runtime_bundle_digest="b" * 64,
    )

    assert result.status == "resolved"
    assert result.effective_condition["gte"] == 80.0
    assert result.basis["threshold_binding"]["source_kind"] == "inventory_profile"
    assert result.basis["threshold_binding"]["policy_version_id"] == approved["id"]


@pytest.mark.parametrize(
    ("tamper_kind", "expected_error"),
    [
        ("version", "policy_version_digest_mismatch"),
        ("binding", "policy_binding_scope_digest_mismatch"),
        ("approval", "policy_approval_digest_mismatch"),
    ],
)
def test_runtime_fails_closed_for_tampered_published_policy_assets(
    policy_runtime,
    tamper_kind,
    expected_error,
):
    _factory, session_scope, service = policy_runtime
    approved = _approve(service, _create_draft(service)["id"])
    binding = service.bind(
        approved["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-1"], "entity_types": ["cpu"]},
        actor="asset-publisher",
        reason="绑定核心设备 CPU 策略",
        idempotency_key="policy-binding-integrity-001",
    )
    with session_scope() as db:
        if tamper_kind == "version":
            row = db.get(AssessmentPolicyVersionRecord, approved["id"])
            policy = dict(row.policy_json or {})
            threshold = dict(policy.get("threshold") or {})
            threshold["value"] = 99.0
            row.policy_json = {**policy, "threshold": threshold}
        elif tamper_kind == "binding":
            row = db.get(AssessmentPolicyBindingRecord, binding["id"])
            row.scope_json = {
                **dict(row.scope_json or {}),
                "sites": ["xian"],
            }
        else:
            row = (
                db.query(AssessmentPolicyDecisionRecord)
                .filter(
                    AssessmentPolicyDecisionRecord.policy_version_id == approved["id"],
                    AssessmentPolicyDecisionRecord.stage == "publication",
                )
                .one()
            )
            row.reason = "tampered publication reason"

    rendered = service.list_rendered_sources(
        check_item_ids=["system.cpu_usage"]
    )["system.cpu_usage"][0]
    assert expected_error in rendered["integrity_errors"]
    assert rendered["digest"] == assessment_policy_source_digest(rendered)

    preview = service.preview_resolution(
        check_item_version_id="check-version-cpu-v1",
        device={"device_id": "device-1"},
        observation={
            "id": "obs-integrity",
            "device_id": "device-1",
            "entity_type": "cpu",
            "entity_key": "slot-0",
            "observation_type": "system.cpu.usage",
            "value": 85,
            "unit": "percent",
        },
        rule={
            "rule_id": "cpu-high",
            "version_id": "rule-v1",
            "digest": "r" * 64,
            "condition": {
                "kind": "threshold",
                "threshold_role": "high",
                "gte": 99,
            },
        },
    )
    resolution = preview["resolution"]
    assert resolution["status"] in {"unknown", "conflict"}
    assert resolution["should_evaluate"] is False
    invalid_sources = preview["snapshot"]["invalid_sources"]
    assert any(
        item["reason_code"] == "asset_integrity_mismatch"
        and expected_error in item["integrity_errors"]
        for item in invalid_sources
    )


def test_inventory_binding_checks_devices_without_publisher_role(policy_runtime):
    _factory, _session_scope, service = policy_runtime
    approved = _approve(service, _create_draft(service)["id"])
    binding = service.bind(approved["id"], binding_type="inventory_profile",
                           scope={"device_ids": ["device-1"]}, actor="network-sme",
                           reason="确认设备绑定", idempotency_key="policy-binding-app-user")
    assert binding["policy_version_id"] == approved["id"]
    with pytest.raises(ValueError, match="unknown device ids"):
        service.bind(
            approved["id"],
            binding_type="inventory_profile",
            scope={"device_ids": ["missing-device"]},
            actor="asset-publisher",
            reason="错误设备范围",
            idempotency_key="policy-binding-missing-device",
        )


def test_exact_scope_replacement_is_atomic_and_revocation_ends_binding(policy_runtime):
    _factory, _session_scope, service = policy_runtime
    first = _approve(service, _create_draft(service)["id"])
    first_binding = service.bind(
        first["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-1"]},
        actor="asset-publisher",
        reason="首次绑定",
        idempotency_key="policy-binding-first",
    )
    second_draft = _create_draft(
        service,
        key="policy-create-002",
        value=85,
        profile_id=first["profile_id"],
    )
    second = _approve(service, second_draft["id"])
    with pytest.raises(AssessmentPolicyConflictError, match="exact scope"):
        service.bind(
            second["id"],
            binding_type="inventory_profile",
            scope={"device_ids": ["device-1"]},
            actor="asset-publisher",
            reason="未声明替换",
            idempotency_key="policy-binding-conflict",
        )
    replacement = service.bind(
        second["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-1"]},
        actor="asset-publisher",
        reason="原子替换旧版本",
        idempotency_key="policy-binding-replacement",
        replaces_binding_id=first_binding["id"],
    )
    assert replacement["status"] == "active"
    service.revoke(
        second["id"],
        actor="security-admin",
        reason="策略证据失效，紧急撤销",
        idempotency_key="policy-revoke-002",
    )
    assert service.list_rendered_sources(check_item_ids=["system.cpu_usage"]) == {}


def test_binding_end_is_idempotent_and_key_cannot_be_reused(policy_runtime):
    _factory, _session_scope, service = policy_runtime
    approved = _approve(service, _create_draft(service)["id"])
    binding = service.bind(
        approved["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-1"]},
        actor="asset-publisher",
        reason="绑定待退役设备范围",
        idempotency_key="policy-binding-end-target",
    )

    ended = service.end_binding(
        binding["id"],
        actor="asset-publisher",
        reason="设备策略已由新配置接管",
        idempotency_key="policy-binding-end-001",
    )
    replay = service.end_binding(
        binding["id"],
        actor="asset-publisher",
        reason="设备策略已由新配置接管",
        idempotency_key="policy-binding-end-001",
    )

    assert ended["status"] == replay["status"] == "ended"
    assert ended["ended_at"] == replay["ended_at"]
    with pytest.raises(AssessmentPolicyConflictError, match="different binding end"):
        service.end_binding(
            binding["id"],
            actor="asset-publisher",
            reason="不同的终止请求",
            idempotency_key="policy-binding-end-001",
        )


def test_profile_list_filters_latest_governance_state_and_pages_versions(
    policy_runtime,
):
    _factory, _session_scope, service = policy_runtime
    first = _approve(service, _create_draft(service)["id"])
    service.bind(
        first["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-1"]},
        actor="asset-publisher",
        reason="绑定生产设备",
        idempotency_key="policy-binding-list-filter",
    )
    revision = _create_draft(
        service,
        key="policy-create-list-revision",
        value=85,
        profile_id=first["profile_id"],
    )

    drafts = service.list_profiles(status="draft")
    assert drafts["total"] == 1
    assert drafts["items"][0]["governance_status"] == "draft"
    assert drafts["items"][0]["production_status"] == "bound_active"
    assert service.list_profiles(status="bound_active")["total"] == 1
    assert service.list_profiles(status="approved_unbound")["total"] == 0

    versions = service.list_profile_versions(first["profile_id"], page_size=1)
    assert versions["total"] == 2
    assert versions["page_count"] == 2
    assert versions["items"][0]["id"] == revision["id"]


def test_resolution_preview_hydrates_inventory_scope_from_device_id(policy_runtime):
    _factory, _session_scope, service = policy_runtime
    approved = _approve(service, _create_draft(service)["id"])
    service.bind(
        approved["id"],
        binding_type="scope_override",
        scope={"sites": ["xian"], "roles": ["core"]},
        actor="asset-publisher",
        reason="绑定西安核心设备范围",
        idempotency_key="policy-binding-scope-preview",
    )

    snapshot = service.preview_snapshot(
        check_item_version_id="check-version-cpu-v1",
        device={"device_id": "device-1"},
    )

    assert snapshot["device_scope"] == {
        "device_id": "device-1",
        "site": "xian",
        "role": "core",
    }
    assert snapshot["sources"][0]["source_kind"] == "scope_override"

    preview = service.preview_resolution(
        check_item_version_id="check-version-cpu-v1",
        device={"device_id": "device-1"},
        observation={
            "id": "preview-observation",
            "device_id": "device-1",
            "entity_type": "cpu",
            "entity_key": "slot-0",
            "observation_type": "system.cpu.usage",
            "value": 85,
            "unit": "percent",
        },
        rule={
            "rule_id": "preview-rule",
            "version_id": "preview-rule-v1",
            "digest": "r" * 64,
            "condition": {
                "kind": "threshold",
                "threshold_role": "high",
                "gte": 99,
            },
        },
    )
    assert preview["resolution"]["status"] == "unknown"
    assert preview["resolution"]["blocker_code"] == (
        "assessment_applicability_unknown"
    )
    assert preview["resolution"]["should_evaluate"] is False
    assert preview["resolution"]["assessment_basis"]["policy_resolution"] == {
        "status": "unknown",
        "reason_code": "assessment_applicability_unknown",
    }


def test_expired_governed_source_is_preserved_as_invalid_evidence(policy_runtime):
    _factory, _session_scope, service = policy_runtime
    draft = service.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="system.cpu.usage",
        policy_role="high",
        name="已过期 CPU 策略",
        policy={"value": 80, "unit": "percent", "operator": "gte"},
        evidence_refs=["customer-policy:expired"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="policy-create-expired",
        valid_from=datetime.now(timezone.utc) - timedelta(days=5),
        valid_until=datetime.now(timezone.utc) - timedelta(days=1),
    )
    approved = _approve(service, draft["id"])
    service.bind(
        approved["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-1"]},
        actor="asset-publisher",
        reason="保留过期来源用于失败关闭验证",
        idempotency_key="policy-binding-expired",
    )
    snapshot = service.preview_snapshot(
        check_item_version_id="check-version-cpu-v1",
        device={"device_id": "device-1"},
        policy_effective_at=datetime.now(timezone.utc),
    )

    assert snapshot["sources"] == []
    assert snapshot["invalid_sources"][0]["reason_code"] == "policy_expired"


def test_governed_applicability_policy_freezes_capability_decision(policy_runtime):
    _factory, session_scope, service = policy_runtime
    with session_scope() as db:
        device = db.get(NetworkDevice, "device-1")
        device.attributes_json = {"capabilities": []}

    draft = service.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="*",
        policy_role="applicability",
        name="CPU 能力适用性",
        policy={
            "mode": "capability",
            "required_capabilities": ["cpu.telemetry"],
            "reason_code": "cpu_telemetry_explicitly_absent",
        },
        evidence_refs=["inventory-capability-contract:v1"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="applicability-create-001",
    )
    approved = _approve(service, draft["id"])
    service.bind(
        approved["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-1"]},
        actor="asset-publisher",
        reason="绑定设备能力合同",
        idempotency_key="applicability-binding-001",
    )

    snapshot = service.preview_snapshot(
        check_item_version_id="check-version-cpu-v1",
        device={"device_id": "device-1"},
    )

    source = snapshot["sources"][0]
    decision = snapshot["applicability_decision"]
    assert source["policy_role"] == "applicability"
    assert source["observation_type"] == "*"
    assert source["applicability"]["required_capabilities"] == [
        "cpu.telemetry"
    ]
    assert decision["status"] == "not_applicable"
    assert decision["reason_codes"] == ["cpu_telemetry_explicitly_absent"]
    assert decision["selected_policy"]["policy_version_id"] == approved["id"]
    assert decision["capability_evidence"]["source_present"] is True
    assert len(decision["digest"]) == 64


def test_applicability_policy_rejects_observation_scope_and_unknown_capability(
    policy_runtime,
):
    _factory, _session_scope, service = policy_runtime
    with pytest.raises(ValueError, match="check-item observation scope"):
        service.create_version(
            check_item_id="system.cpu_usage",
            check_item_version_id="check-version-cpu-v1",
            observation_type="system.cpu.usage",
            policy_role="applicability",
            name="错误范围适用性",
            policy={
                "mode": "capability",
                "required_capabilities": ["cpu.telemetry"],
            },
            evidence_refs=["inventory-capability-contract:v1"],
            provenance={"source": "customer_policy"},
            created_by="rule-engineer",
            idempotency_key="applicability-invalid-scope",
        )

    draft = service.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="*",
        policy_role="applicability",
        name="缺少能力资料",
        policy={
            "mode": "capability",
            "required_capabilities": ["cpu.telemetry"],
        },
        evidence_refs=["inventory-capability-contract:v1"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="applicability-create-unknown",
    )
    approved = _approve(service, draft["id"])
    service.bind(
        approved["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-1"]},
        actor="asset-publisher",
        reason="绑定设备能力合同",
        idempotency_key="applicability-binding-unknown",
    )

    snapshot = service.preview_snapshot(
        check_item_version_id="check-version-cpu-v1",
        device={"device_id": "device-1"},
    )
    assert snapshot["applicability_decision"]["status"] == "unknown"
    assert snapshot["applicability_decision"]["reason_codes"] == [
        "applicability_capability_unknown"
    ]


def test_applicability_policy_freezes_approved_legal_empty_classifier_evidence(
    policy_runtime,
):
    _factory, _session_scope, service = policy_runtime
    pattern = r"(?im)^\s*no\s+vpc\s+configured\s*$"
    draft = service.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="*",
        policy_role="applicability",
        name="合法空输出证据",
        policy={
            "mode": "explicit",
            "decision": "applicable",
            "reason_code": "vpc_check_supported",
            "empty_result_classification": "expected_empty",
            "legal_empty_patterns": [pattern],
        },
        evidence_refs=["output-quality-profile:vpc-v1"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="applicability-legal-empty-create",
    )
    approved = _approve(service, draft["id"])
    service.bind(
        approved["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-1"]},
        actor="asset-publisher",
        reason="绑定合法空输出分类证据",
        idempotency_key="applicability-legal-empty-bind",
    )

    snapshot = service.preview_snapshot(
        check_item_version_id="check-version-cpu-v1",
        device={"device_id": "device-1"},
    )

    selected = snapshot["applicability_decision"]["selected_policy"]
    assert selected["empty_result_classification"] == "expected_empty"
    assert selected["legal_empty_patterns"] == [pattern]


def test_applicability_policy_rejects_unproven_post_collection_empty(
    policy_runtime,
):
    _factory, _session_scope, service = policy_runtime
    with pytest.raises(ValueError, match="requires at least one"):
        service.create_version(
            check_item_id="system.cpu_usage",
            check_item_version_id="check-version-cpu-v1",
            observation_type="*",
            policy_role="applicability",
            name="缺少合法空输出证据",
            policy={
                "mode": "explicit",
                "decision": "applicable",
                "empty_result_classification": "expected_empty",
            },
            evidence_refs=["output-quality-profile:vpc-v1"],
            provenance={"source": "customer_policy"},
            created_by="rule-engineer",
            idempotency_key="applicability-unproven-empty-create",
        )


def test_same_priority_applicability_conflict_fails_closed(policy_runtime):
    _factory, _session_scope, service = policy_runtime
    first = service.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="*",
        policy_role="applicability",
        name="站点允许 CPU 检查",
        policy={
            "mode": "explicit",
            "decision": "applicable",
            "reason_code": "site_policy_allows_cpu_check",
        },
        evidence_refs=["customer-policy:site-cpu"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="applicability-conflict-create-001",
    )
    second = service.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="*",
        policy_role="applicability",
        name="角色禁止 CPU 检查",
        policy={
            "mode": "explicit",
            "decision": "not_applicable",
            "reason_code": "role_policy_disallows_cpu_check",
        },
        evidence_refs=["customer-policy:role-cpu"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="applicability-conflict-create-002",
    )
    first = _approve(service, first["id"])
    second = _approve(service, second["id"])
    service.bind(
        first["id"],
        binding_type="scope_override",
        scope={"sites": ["xian"]},
        actor="asset-publisher",
        reason="站点级适用性",
        idempotency_key="applicability-conflict-bind-001",
    )
    service.bind(
        second["id"],
        binding_type="scope_override",
        scope={"roles": ["core"]},
        actor="asset-publisher",
        reason="角色级适用性",
        idempotency_key="applicability-conflict-bind-002",
    )

    snapshot = service.preview_snapshot(
        check_item_version_id="check-version-cpu-v1",
        device={"device_id": "device-1"},
    )

    decision = snapshot["applicability_decision"]
    assert decision["status"] == "conflict"
    assert decision["reason_codes"] == ["assessment_applicability_conflict"]
    assert decision["selected_policy"] is None
    assert {
        candidate["policy_version_id"]
        for candidate in decision["considered_sources"]
    } == {first["id"], second["id"]}


def test_target_impact_preview_uses_inventory_without_mutating_bindings(
    policy_runtime,
):
    _factory, session_scope, service = policy_runtime
    draft = service.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="*",
        policy_role="applicability",
        name="CPU 显式不适用候选",
        policy={
            "mode": "explicit",
            "decision": "not_applicable",
            "reason_code": "customer_scope_excludes_cpu_check",
        },
        evidence_refs=["customer-policy:impact-preview"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="impact-preview-applicability-create",
    )

    result = service.preview_target_impact(
        draft["id"],
        binding_type="inventory_profile",
        proposed_scope={"device_ids": ["device-1"]},
        target_selector={"device_ids": ["device-1"]},
        page=1,
        page_size=25,
    )

    assert result["summary"] == {
        "target_count": 1,
        "counts": {"not_applicable": 1},
        "blocking_count": 0,
    }
    assert result["items"][0]["device_id"] == "device-1"
    assert result["items"][0]["impact_status"] == "not_applicable"
    assert result["items"][0]["candidate_matched"] is True
    assert len(result["digest"]) == 64
    with session_scope() as db:
        assert db.query(AssessmentPolicyBindingRecord).count() == 0
        assert db.get(AssessmentPolicyVersionRecord, draft["id"]).status == "draft"


def test_target_impact_preview_exposes_unit_incompatibility(policy_runtime):
    _factory, _session_scope, service = policy_runtime
    draft = service.create_version(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-version-cpu-v1",
        observation_type="system.cpu.usage",
        policy_role="high",
        name="错误单位 CPU 阈值候选",
        policy={"value": 80, "unit": "dBm", "operator": "gte"},
        evidence_refs=["customer-policy:invalid-unit-preview"],
        provenance={"source": "customer_policy"},
        created_by="rule-engineer",
        idempotency_key="impact-preview-unit-create",
    )

    result = service.preview_target_impact(
        draft["id"],
        binding_type="inventory_profile",
        proposed_scope={"device_ids": ["device-1"]},
        target_selector={"device_ids": ["device-1"]},
    )

    assert result["summary"]["counts"] == {"unit_incompatible": 1}
    assert result["summary"]["blocking_count"] == 1
    assert result["items"][0]["reason_codes"] == [
        "assessment_policy_unit_mismatch"
    ]
