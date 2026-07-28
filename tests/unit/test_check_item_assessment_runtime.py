from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items import (
    AssetGapRepository,
    BaselineCandidate,
    CheckAssessmentInput,
    CheckAssessmentService,
    CheckItemBatchResultService,
    CheckItemCatalogRepository,
    CheckItemEffectivePlanService,
    CheckItemExecutionService,
    CheckItemRunFinalizer,
    CheckItemRuntimeBundleService,
    ComparisonContextRepository,
    ComparisonContextService,
    CoverageDecisionInput,
    CoverageDecisionService,
    CurrentStateEvaluationInput,
    CurrentStateEvaluationService,
    CurrentStateFindingRepository,
    EffectivePlanRequest,
    EntityIdentityPolicyRepository,
    EvidenceQualificationInput,
    EvidenceQualificationService,
    PatrolProfileCheckItemRepository,
    ReportManifestService,
    ReportReleaseService,
    RiskCaseService,
    RuntimeBundleValidationError,
    StructuredExecutionResultInput,
    TextComparisonProfileRepository,
    TextExecutionResultInput,
    build_inventory_snapshot,
    build_target_manifest,
    compare_text,
    load_catalog,
    load_text_comparison_profiles,
)
from src.infrastructure.db.models import (
    AuditLogRecord,
    AssetGapRecord,
    Base,
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemCollectionActionRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemRuntimeManifestRecord,
    CheckItemVersionRecord,
    CollectionImplementationParserBindingRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    CommandProfileRecord,
    CommandProfileVersionRecord,
    CommandSnapshot,
    ComparisonContextRecord,
    CoverageDecisionRecord,
    CurrentStateFindingRecord,
    EntityIdentityPolicyRecord,
    EntityIdentityPolicyVersionRecord,
    EvidenceQualificationRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    NetworkChangeRecord,
    NetworkDevice,
    NetworkEventRecord,
    ObservationContractRecord,
    ParserTemplate,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolProfileCheckItemRecord,
    ReportManifestRecord,
    ReportReleaseRecord,
    RiskCaseRecord,
    RiskOccurrenceRecord,
    RiskRemediationRecord,
    RuntimeBundleAssetRecord,
    StateObservationRecord,
    TextComparisonProfileRecord,
    TextComparisonProfileVersionRecord,
    WorkflowRun,
)
from src.core.check_items.reporting import build_check_item_report_view


@contextmanager
def _session_scope(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _runtime(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'check-item-runtime.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            CheckItemRecord.__table__,
            NetworkDevice.__table__,
            CheckItemVersionRecord.__table__,
            CollectionImplementationRecord.__table__,
            CollectionImplementationVersionRecord.__table__,
            CheckItemImplementationLinkRecord.__table__,
            AuditLogRecord.__table__,
            ParserTemplate.__table__,
            CollectionImplementationParserBindingRecord.__table__,
            ObservationContractRecord.__table__,
            ChangePolicyRecord.__table__,
            ChangePolicyVersionRecord.__table__,
            EventRuleRecord.__table__,
            EventRuleVersionRecord.__table__,
            EntityIdentityPolicyRecord.__table__,
            EntityIdentityPolicyVersionRecord.__table__,
            CheckItemRuntimeBundleRecord.__table__,
            CheckItemRuntimeBundleVersionRecord.__table__,
            RuntimeBundleAssetRecord.__table__,
            ComparisonContextRecord.__table__,
            CheckExecutionRecord.__table__,
            CheckAssessmentRecord.__table__,
            EvidenceQualificationRecord.__table__,
            CoverageDecisionRecord.__table__,
            CommandSnapshot.__table__,
            StateObservationRecord.__table__,
            NetworkChangeRecord.__table__,
            NetworkEventRecord.__table__,
            PatrolBatchRecord.__table__,
            PatrolBatchDeviceRecord.__table__,
            CommandProfileRecord.__table__,
            CommandProfileVersionRecord.__table__,
            PatrolProfileCheckItemRecord.__table__,
            CheckItemCollectionActionRecord.__table__,
            CheckItemRuntimeManifestRecord.__table__,
            AssetGapRecord.__table__,
            TextComparisonProfileRecord.__table__,
            TextComparisonProfileVersionRecord.__table__,
            CurrentStateFindingRecord.__table__,
            RiskCaseRecord.__table__,
            RiskOccurrenceRecord.__table__,
            RiskRemediationRecord.__table__,
            ReportManifestRecord.__table__,
            ReportReleaseRecord.__table__,
            WorkflowRun.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)

    def scope():
        return _session_scope(session_factory)

    catalog_repository = CheckItemCatalogRepository(session_scope=scope)
    catalog_repository.bootstrap(load_catalog())
    return session_factory, scope, catalog_repository


def _runtime_manifest_fixture(
    *,
    patrol_batch_id,
    target_digest,
    inventory_digest,
    check_item_versions,
    collection_actions,
    runtime_bundle_refs,
    comparison_contexts,
    identity_versions,
):
    payload = {
        "schema_version": 2,
        "patrol_batch_id": patrol_batch_id,
        "profile_version_id": None,
        "target_manifest_digest": target_digest,
        "inventory_snapshot_digest": inventory_digest,
        "check_item_versions": list(check_item_versions),
        "decisions": [],
        "collection_actions": list(collection_actions),
        "runtime_bundle_refs": list(runtime_bundle_refs),
        "comparison_contexts": list(comparison_contexts),
        "entity_identity_policy_versions": list(identity_versions),
        "assessment_schema_version": "check-assessment/v1",
        "report_schema_version": "patrol-report/v2",
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {**payload, "digest": digest}, digest


def _seed_cpu_assets(session_factory, catalog_repository):
    catalog = catalog_repository.load_active()
    check_item = catalog.check_item("system.cpu_usage")
    implementation = next(
        item
        for item in catalog.implementations
        if item.id == "h3c-cpu-usage"
    )
    assert check_item is not None
    now = datetime.now(timezone.utc)
    with _session_scope(session_factory) as db:
        check_version = db.get(
            CheckItemVersionRecord,
            check_item.version_id,
        )
        implementation_version = db.get(
            CollectionImplementationVersionRecord,
            implementation.version_id,
        )
        check_version.freshness_policy_json = {
            "max_observation_age_seconds": 86400,
            "max_collection_span_seconds": 3600,
            "clock_source": "collector",
            "clock_skew_tolerance_seconds": 300,
        }
        check_version.empty_result_policy_json = {
            "classification": "invalid",
            "evidence_conditions": [],
        }
        check_version.coverage_policy_json = {
            "model": "fixed",
            "expected_count": 1,
            "allow_healthy_when_unbounded": False,
        }
        check_version.digest = hashlib.sha256(
            json.dumps(
                {
                    "previous": check_version.digest,
                    "freshness": check_version.freshness_policy_json,
                    "empty": check_version.empty_result_policy_json,
                    "coverage": check_version.coverage_policy_json,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        implementation_version.output_quality_policy_json = {
            "profile_version": 1,
            "accepted_statuses": ["data", "success", "valid_empty"],
        }
        implementation_version.digest = hashlib.sha256(
            json.dumps(
                {
                    "previous": implementation_version.digest,
                    "output_quality": (
                        implementation_version.output_quality_policy_json
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        db.add_all(
            [
                ParserTemplate(
                    id="parser-cpu-v1",
                    vendor="H3C",
                    model=None,
                    family="Comware7",
                    profile="H3C",
                    canonical_command="display cpu-usage",
                    category="cpu_usage",
                    version="1",
                    asset_path="database://parser-cpu-v1",
                    template_text=(
                        "Value cpu_usage (\\d+)\\n\\n"
                        "Start\\n ^CPU usage: ${cpu_usage}% -> Record\\n"
                    ),
                    sha256="a" * 64,
                    status="active",
                    validation_json={
                        "passed": True,
                        "unique_sample_count": 3,
                        "sample_manifest_sha256": "f" * 64,
                        "compatibility": {
                            "manifest_version": 2,
                            "scope": "vendor",
                            "vendor": "H3C",
                            "canonical_command": "display cpu-usage",
                            "structure_signatures": ["cpu-usage-v1"],
                        },
                    },
                    created_at=now,
                    updated_at=now,
                    activated_at=now,
                ),
                CollectionImplementationParserBindingRecord(
                    id="binding-cpu-v1",
                    implementation_version_id=implementation.version_id,
                    canonical_command="display cpu-usage",
                    parser_template_id="parser-cpu-v1",
                    record_set="default",
                    binding_role="primary",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                ObservationContractRecord(
                    id="contract-cpu-v1",
                    source_type="cli",
                    schema_id="h3c.cpu.usage",
                    source_template_id="parser-cpu-v1",
                    entity_type="cpu_scope",
                    primary_keys_json=["scope", "scope_id"],
                    required_fields_json=["cpu_usage"],
                    optional_fields_json=[],
                    validators_json={},
                    ignore_fields_json=[],
                    allow_empty=False,
                    empty_patterns_json=[],
                    state_semantics_json={
                        "cpu_usage": {
                            "observation_type": "device.cpu.utilization",
                            "semantic_type": "gauge",
                            "unit": "percent",
                            "dimensions": {"window": "instant"},
                        }
                    },
                    record_sets_json=[],
                    version=1,
                    contract_sha256="b" * 64,
                    status="active",
                    activated_at=now,
                ),
                ChangePolicyRecord(
                    id="policy-cpu",
                    name="policy:device.cpu.utilization",
                    observation_type="device.cpu.utilization",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
                ChangePolicyVersionRecord(
                    id="policy-cpu-v1",
                    policy_id="policy-cpu",
                    version=1,
                    observation_type="device.cpu.utilization",
                    semantic_type="gauge",
                    compare_mode="numeric_delta",
                    policy_json={"absolute_delta": 5},
                    validation_report_json={"passed": True},
                    status="active",
                    digest="c" * 64,
                    created_by="test",
                    created_at=now,
                    activated_at=now,
                ),
                EventRuleRecord(
                    id="rule-cpu-high",
                    name="system.cpu_usage.high",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
                EventRuleVersionRecord(
                    id="rule-cpu-high-v1",
                    rule_id="rule-cpu-high",
                    version=1,
                    event_type="CpuUtilizationHigh",
                    severity="major",
                    condition_json={
                        "kind": "threshold",
                        "observation_types": ["device.cpu.utilization"],
                        "entity_types": ["cpu_scope"],
                        "fields": ["cpu_usage"],
                        "gte": 80,
                    },
                    priority=100,
                    exclusive=True,
                    event_class="fault",
                    evaluation_scope="both",
                    recovery_json={},
                    correlation_key_json=[
                        "device_id",
                        "entity_type",
                        "entity_key",
                    ],
                    status="active",
                    validation_report_json={"passed": True},
                    digest="d" * 64,
                    created_by="test",
                    created_at=now,
                    activated_at=now,
                ),
            ]
        )
    return check_item, implementation


def _create_cpu_bundle(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    service = CheckItemRuntimeBundleService(scope)
    bundle = service.create_version(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        parser_template_ids=["parser-cpu-v1"],
        observation_contract_ids=["contract-cpu-v1"],
        change_policy_version_ids=["policy-cpu-v1"],
        event_rule_bindings={
            "rule-cpu-high-v1": "device.cpu.utilization",
        },
        actor="test",
    )
    bundle = service.activate(
        bundle["version_id"],
        actor="test",
        reason="unit test activation",
    )
    return (
        session_factory,
        scope,
        catalog_repository,
        check_item,
        implementation,
        service,
        bundle,
    )


def _create_configuration_bundle(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    catalog = catalog_repository.load_active()
    check_item = catalog.check_item("configuration.running")
    implementation = next(
        item
        for item in catalog.implementations
        if item.id == "h3c-running-configuration"
    )
    assert check_item is not None
    profiles = load_text_comparison_profiles()
    TextComparisonProfileRepository(scope).bootstrap(profiles)
    profile = next(
        item
        for item in profiles
        if item.id == "configuration.ordered_lines"
    )
    service = CheckItemRuntimeBundleService(scope)
    bundle = service.create_version(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        parser_template_ids=[],
        observation_contract_ids=[],
        change_policy_version_ids=[],
        event_rule_bindings={},
        comparison_profile_version_id=profile.version_id,
        actor="test",
    )
    bundle = service.activate(
        bundle["version_id"],
        actor="test",
        reason="unit test activation",
    )
    return (
        session_factory,
        scope,
        catalog_repository,
        check_item,
        implementation,
        profile,
        bundle,
    )


def test_comparison_context_never_invents_a_first_change(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item = catalog_repository.load_active().check_item("system.cpu_usage")
    assert check_item is not None
    service = ComparisonContextService()
    current = datetime(2026, 7, 28, 10, tzinfo=timezone.utc)

    first = service.select_online(
        device_id="device-1",
        check_item_version_id=check_item.version_id,
        current_observed_at=current,
        candidates=[],
        max_baseline_age_seconds=86400,
        canonical_schema_version="v1",
        entity_identity_policy_version="v1",
    )
    assert first.mode == "no_baseline"
    assert first.comparison_status == "first_observation"
    assert first.baseline_run_id is None

    comparable = service.select_online(
        device_id="device-1",
        check_item_version_id=check_item.version_id,
        current_observed_at=current,
        candidates=[
            BaselineCandidate(
                run_id="run-old",
                observed_at=current - timedelta(hours=2),
                snapshot_ids=("snapshot-old",),
                canonical_schema_version="v1",
                entity_identity_policy_version="v1",
            )
        ],
        max_baseline_age_seconds=86400,
        canonical_schema_version="v1",
        entity_identity_policy_version="v1",
    )
    assert comparable.comparison_status == "comparable"
    assert comparable.baseline_run_id == "run-old"

    stale = service.select_online(
        device_id="device-1",
        check_item_version_id=check_item.version_id,
        current_observed_at=current,
        candidates=[
            BaselineCandidate(
                run_id="run-stale",
                observed_at=current - timedelta(days=2),
                canonical_schema_version="v1",
                entity_identity_policy_version="v1",
            )
        ],
        max_baseline_age_seconds=86400,
        canonical_schema_version="v1",
        entity_identity_policy_version="v1",
    )
    assert stale.comparison_status == "baseline_stale"
    assert stale.baseline_run_id is None

    with pytest.raises(ValueError, match="older than current"):
        service.select_explicit(
            device_id="device-1",
            check_item_version_id=check_item.version_id,
            current_observed_at=current,
            baseline=BaselineCandidate(
                run_id="run-future",
                observed_at=current,
                canonical_schema_version="v1",
                entity_identity_policy_version="v1",
            ),
            canonical_schema_version="v1",
            entity_identity_policy_version="v1",
        )

    repository = ComparisonContextRepository(scope)
    assert repository.save(first)["id"] == first.id
    assert repository.save(first)["id"] == first.id
    with _session_scope(session_factory) as db:
        assert db.query(ComparisonContextRecord).count() == 1


def test_completed_execution_is_not_automatically_healthy():
    common = {
        "execution_id": "execution-1",
        "patrol_batch_id": "batch-1",
        "device_id": "device-1",
        "check_item_version_id": "check-v1",
        "execution_status": "completed",
        "assessment_mode": "health",
        "comparison_status": "first_observation",
        "observation_count": 1,
        "observed_entity_count": 1,
        "evaluated_entity_count": 1,
        "evidence_qualification_id": "qualification-1",
        "coverage_decision_id": "coverage-1",
        "source_identity_status": "confirmed",
        "output_quality_status": "complete",
        "freshness_status": "fresh",
        "empty_result_classification": "non_empty",
        "coverage_status": "complete",
        "coverage_basis": "fixed",
    }
    unable = CheckAssessmentService.assess(
        CheckAssessmentInput(**common, rule_assets_ready=False)
    )
    assert unable.assessment_status == "unable_to_determine"
    assert "current_state_rules_not_ready" in unable.blocking_reason_codes

    risky = CheckAssessmentService.assess(
        CheckAssessmentInput(
            **common,
            rule_assets_ready=True,
            current_finding_ids=("finding-1",),
        )
    )
    assert risky.assessment_status == "at_risk"

    healthy = CheckAssessmentService.assess(
        CheckAssessmentInput(**common, rule_assets_ready=True)
    )
    assert healthy.assessment_status == "healthy"
    assert healthy.completeness_status == "complete"


def test_evidence_qualification_fails_closed_for_conflict_truncation_and_stale():
    assessed_at = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
    decision = EvidenceQualificationService.qualify(
        EvidenceQualificationInput(
            execution_id="execution-quality-1",
            patrol_batch_id="batch-quality-1",
            device_id="inventory-device-1",
            check_item_version_id="check-v1",
            implementation_version_id="implementation-v1",
            runtime_bundle_version_id="bundle-v1",
            runtime_bundle_digest="a" * 64,
            snapshot_ids=("snapshot-quality-1",),
            source_device_ids=("external-device",),
            inventory_device_ids=("another-inventory-device",),
            output_statuses=("truncated",),
            observed_ats=(assessed_at - timedelta(days=2),),
            observation_count=4,
            assessment_mode="health",
            policy_snapshot={
                "freshness_policy": {
                    "max_observation_age_seconds": 3600,
                    "max_collection_span_seconds": 300,
                },
                "empty_result_policy": {"classification": "invalid"},
            },
            qualified_at=assessed_at,
        )
    )
    assert decision.source_identity_status == "conflict"
    assert decision.output_quality_status == "truncated"
    assert decision.freshness_status == "stale"
    assert {
        "source_identity_conflict",
        "output_quality_truncated",
        "observation_stale",
    } <= set(decision.reason_codes)


def test_coverage_decision_uses_auditable_entity_denominator():
    expected = tuple(f"GigabitEthernet1/0/{index}" for index in range(1, 49))
    observed = expected[:40]
    decision = CoverageDecisionService.decide(
        CoverageDecisionInput(
            execution_id="execution-coverage-1",
            patrol_batch_id="batch-coverage-1",
            device_id="device-coverage-1",
            check_item_version_id="check-interface-v1",
            observed_entity_keys=observed,
            evaluated_entity_count=40,
            expected_entity_keys=expected,
            policy_snapshot={"model": "inventory_entity_set"},
            denominator_ref={
                "basis": "inventory_entity_set",
                "inventory_snapshot_digest": "b" * 64,
                "entity_type": "interface",
            },
        )
    )
    assert decision.coverage_status == "partial"
    assert decision.coverage_basis == "inventory_entity_set"
    assert decision.values["expected_entity_count"] == 48
    assert decision.values["observed_entity_count"] == 40
    assert len(decision.values["missing_entity_sample_json"]) == 8

    unbounded = CoverageDecisionService.decide(
        CoverageDecisionInput(
            execution_id="execution-coverage-2",
            patrol_batch_id="batch-coverage-1",
            device_id="device-coverage-1",
            check_item_version_id="check-interface-v1",
            observed_entity_keys=observed,
            evaluated_entity_count=40,
            policy_snapshot={"model": "non_exhaustive"},
        )
    )
    assert unbounded.coverage_status == "unbounded"

    expanded = CoverageDecisionService.decide(
        CoverageDecisionInput(
            execution_id="execution-coverage-3",
            patrol_batch_id="batch-coverage-1",
            device_id="device-coverage-1",
            check_item_version_id="check-interface-v1",
            observed_entity_keys=(*expected, "GigabitEthernet1/0/49"),
            evaluated_entity_count=49,
            expected_entity_keys=expected,
            policy_snapshot={"model": "derived_entity_set"},
            denominator_ref={
                "basis": "derived_entity_set",
                "source": "previous_qualified_observation",
                "evidence_qualification_digest": "c" * 64,
            },
        )
    )
    assert expanded.coverage_status == "complete"
    assert expanded.values["unexpected_entity_sample_json"] == [
        "GigabitEthernet1/0/49"
    ]

    unaudited = CoverageDecisionService.decide(
        CoverageDecisionInput(
            execution_id="execution-coverage-4",
            patrol_batch_id="batch-coverage-1",
            device_id="device-coverage-1",
            check_item_version_id="check-interface-v1",
            observed_entity_keys=expected,
            evaluated_entity_count=48,
            expected_entity_keys=expected,
            policy_snapshot={"model": "derived_entity_set"},
        )
    )
    assert unaudited.coverage_status == "unknown"
    assert "coverage_denominator_reference_missing" in (
        unaudited.reason_codes
    )


def test_evidence_coverage_does_not_require_health_rule_evaluation():
    decision = CoverageDecisionService.decide(
        CoverageDecisionInput(
            execution_id="execution-evidence-coverage",
            patrol_batch_id="batch-evidence-coverage",
            device_id="device-evidence-coverage",
            check_item_version_id="check-version-evidence",
            observed_entity_keys=(("device", "__singleton__"),),
            evaluated_entity_count=0,
            policy_snapshot={
                "model": "fixed",
                "expected_count": 1,
                "require_rule_evaluation": False,
            },
        )
    )

    assert decision.coverage_status == "complete"
    assert "entity_evaluation_incomplete" not in decision.reason_codes


def test_health_assessment_supports_policy_approved_not_applicable():
    decision = CheckAssessmentService.assess(
        CheckAssessmentInput(
            patrol_batch_id="batch-not-applicable",
            device_id="device-not-applicable",
            check_item_version_id="optical-v2",
            execution_id="execution-not-applicable",
            execution_status="completed",
            assessment_mode="health",
            comparison_status="first_observation",
            observation_count=0,
            observed_entity_count=0,
            evaluated_entity_count=0,
            rule_assets_ready=False,
            evidence_qualification_id="qualification-na",
            coverage_decision_id="coverage-na",
            source_identity_status="confirmed",
            output_quality_status="unsupported",
            freshness_status="fresh",
            empty_result_classification="not_applicable",
            coverage_status="empty",
            coverage_basis="derived_entity_set",
        )
    )

    assert decision.assessment_status == "not_applicable"
    assert decision.completeness_status == "complete"
    assert decision.values["risk_verification_status"] == "not_applicable"


def test_runtime_bundle_activation_is_atomic_and_detects_drift(tmp_path):
    (
        session_factory,
        _scope,
        _catalog_repository,
        check_item,
        implementation,
        service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)

    assert bundle["version_status"] == "active"
    assert {
        item["asset_type"] for item in bundle["assets"]
    } >= {
        "parser_template",
        "observation_contract",
        "change_policy",
        "current_state_rule",
        "event_rule",
        "identity_policy",
    }
    assert service.get_active(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )["version_id"] == bundle["version_id"]

    with _session_scope(session_factory) as db:
        db.get(EventRuleVersionRecord, "rule-cpu-high-v1").digest = "e" * 64
    with pytest.raises(RuntimeBundleValidationError, match="digest changed"):
        service.get_active(
            check_item_version_id=check_item.version_id,
            implementation_version_id=implementation.version_id,
        )

    with _session_scope(session_factory) as db:
        db.get(EventRuleVersionRecord, "rule-cpu-high-v1").digest = "d" * 64


def test_catalog_health_counts_only_valid_active_runtime_bundles(tmp_path):
    (
        session_factory,
        _scope,
        _catalog_repository,
        check_item,
        implementation,
        service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)

    health = service.catalog_health(page_size=100)

    assert health["summary"]["ready_count"] == 1
    assert health["summary"]["implementation_pair_count"] > 1
    assert health["summary"]["missing_count"] == (
        health["summary"]["implementation_pair_count"] - 1
    )
    ready = service.catalog_health(status="ready")
    assert ready["total"] == 1
    assert ready["items"][0]["check_item_id"] == check_item.id
    assert ready["items"][0]["implementation_id"] == implementation.id
    assert ready["items"][0]["bundle_version_id"] == bundle["version_id"]

    with _session_scope(session_factory) as db:
        db.get(ParserTemplate, "parser-cpu-v1").sha256 = "e" * 64

    drifted = service.catalog_health(status="invalid")
    assert drifted["summary"]["ready_count"] == 0
    assert drifted["summary"]["invalid_count"] == 1
    assert drifted["total"] == 1
    assert drifted["items"][0]["status"] == "invalid"
    assert any(
        "digest changed" in reason
        for reason in drifted["items"][0]["blocking_reasons"]
    )


def test_runtime_bundle_preview_is_read_only_and_assembly_is_idempotent(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")
    with _session_scope(session_factory) as db:
        db.query(CollectionImplementationParserBindingRecord).delete()
        assert db.query(CheckItemRuntimeBundleVersionRecord).count() == 0
        assert db.query(RuntimeBundleAssetRecord).count() == 0

    service = CheckItemRuntimeBundleService(scope)
    preview = service.preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )

    assert preview["operationally_ready"] is True
    assert preview["blockers"] == []
    assert preview["fallback_resilience"] == "reduced"
    assert preview["selected"]["parser_template_ids"] == ["parser-cpu-v1"]
    assert preview["selected"]["event_rule_bindings"] == {
        "rule-cpu-high-v1": ["device.cpu.utilization"]
    }
    with _session_scope(session_factory) as db:
        assert db.query(CheckItemRuntimeBundleVersionRecord).count() == 0
        assert db.query(RuntimeBundleAssetRecord).count() == 0

    draft = service.assemble(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        preview_digest=preview["preview_digest"],
        actor="engineer",
    )
    repeated = service.assemble(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        preview_digest=preview["preview_digest"],
        actor="engineer",
    )
    assert draft["version_id"] == repeated["version_id"]
    assert draft["version_status"] == "draft"
    assert draft["compatibility"]["fallback_resilience"] == "reduced"

    active = service.activate(
        draft["version_id"],
        actor="approver",
        reason="validated canonical CPU runtime path",
    )
    assert active["version_status"] == "active"
    with _session_scope(session_factory) as db:
        actions = {
            row.action
            for row in db.query(AuditLogRecord)
            .filter(
                AuditLogRecord.resource_type
                == "check_item_runtime_bundle_version"
            )
            .all()
        }
    assert actions == {
        "runtime_bundle_draft_created",
        "runtime_bundle_activated",
    }


def test_runtime_bundle_assembly_rejects_stale_preview(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")
    service = CheckItemRuntimeBundleService(scope)
    preview = service.preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )
    with _session_scope(session_factory) as db:
        parser = db.get(ParserTemplate, "parser-cpu-v1")
        validation = dict(parser.validation_json or {})
        compatibility = dict(validation["compatibility"])
        compatibility["structure_signatures"] = ["cpu-usage-v2"]
        validation["compatibility"] = compatibility
        parser.validation_json = validation

    with pytest.raises(RuntimeBundleValidationError, match="stale"):
        service.assemble(
            check_item_version_id=check_item.version_id,
            implementation_version_id=implementation.version_id,
            preview_digest=preview["preview_digest"],
            actor="engineer",
        )


def test_runtime_bundle_rejects_wide_implementation_with_model_parser(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")
    with _session_scope(session_factory) as db:
        parser = db.get(ParserTemplate, "parser-cpu-v1")
        parser.model = "S5130S-54S-HI-G"
        validation = dict(parser.validation_json or {})
        compatibility = dict(validation["compatibility"])
        compatibility.update(
            {
                "scope": "model",
                "model": "S5130S-54S-HI-G",
            }
        )
        validation["compatibility"] = compatibility
        parser.validation_json = validation

    preview = CheckItemRuntimeBundleService(scope).preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )
    blocker_codes = {item["code"] for item in preview["blockers"]}
    assert preview["operationally_ready"] is False
    assert "implementation_scope_broader_than_parser" in blocker_codes


def test_runtime_bundle_requires_source_to_canonical_entity_mapping(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")
    with _session_scope(session_factory) as db:
        contract = db.get(ObservationContractRecord, "contract-cpu-v1")
        contract.entity_type = "cpu_record"
        contract.primary_keys_json = ["slot", "cpu"]
        contract.required_fields_json = ["slot", "cpu", "cpu_usage"]
        contract.canonical_entity_type = "cpu_scope"
        contract.entity_identity_fields_json = ["scope", "scope_id"]
        contract.entity_dimensions_json = {}

    blocked = CheckItemRuntimeBundleService(scope).preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )
    assert blocked["operationally_ready"] is False
    assert "canonical_identity_mapping_missing" in {
        item["code"] for item in blocked["blockers"]
    }

    with _session_scope(session_factory) as db:
        contract = db.get(ObservationContractRecord, "contract-cpu-v1")
        contract.entity_dimensions_json = {
            "scope": {"literal": "processor"},
            "scope_id": {
                "op": "concat",
                "args": [
                    {"field": "slot"},
                    {"literal": "/"},
                    {"field": "cpu"},
                ],
            },
        }

    ready = CheckItemRuntimeBundleService(scope).preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )
    assert ready["operationally_ready"] is True


def test_runtime_bundle_rejects_rule_without_explicit_observation_type(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")
    with _session_scope(session_factory) as db:
        rule = db.get(EventRuleVersionRecord, "rule-cpu-high-v1")
        condition = dict(rule.condition_json or {})
        condition.pop("observation_types")
        rule.condition_json = condition

    preview = CheckItemRuntimeBundleService(scope).preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        event_rule_version_ids=["rule-cpu-high-v1"],
    )
    blocker_codes = {item["code"] for item in preview["blockers"]}
    assert "event_rule_observation_types_missing" in blocker_codes
    assert "current_state_event_rule_missing" in blocker_codes


def test_runtime_bundle_rejects_health_assets_without_current_state_rule(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    with _session_scope(session_factory) as db:
        db.get(EventRuleVersionRecord, "rule-cpu-high-v1").evaluation_scope = "change"

    with pytest.raises(
        RuntimeBundleValidationError,
        match="缺少可用于当前状态判断的 Active EventRule",
    ):
        CheckItemRuntimeBundleService(scope).create_version(
            check_item_version_id=check_item.version_id,
            implementation_version_id=implementation.version_id,
            parser_template_ids=["parser-cpu-v1"],
            observation_contract_ids=["contract-cpu-v1"],
            change_policy_version_ids=["policy-cpu-v1"],
            event_rule_bindings={
                "rule-cpu-high-v1": "device.cpu.utilization",
            },
            actor="test",
        )


def test_nonstructured_runtime_bundle_freezes_comparison_profile(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    catalog = catalog_repository.load_active()
    check_item = catalog.check_item("configuration.running")
    implementation = next(
        item
        for item in catalog.implementations
        if item.id == "h3c-running-configuration"
    )
    assert check_item is not None
    profiles = load_text_comparison_profiles()
    TextComparisonProfileRepository(scope).bootstrap(profiles)
    comparison_profile = next(
        item
        for item in profiles
        if item.id == "configuration.ordered_lines"
    )
    service = CheckItemRuntimeBundleService(scope)
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")

    preview = service.preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )
    assert preview["blockers"] == []
    assert (
        preview["selected"]["comparison_profile_version_id"]
        == comparison_profile.version_id
    )

    bundle = service.create_version(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        parser_template_ids=[],
        observation_contract_ids=[],
        change_policy_version_ids=[],
        event_rule_bindings={},
        actor="test",
    )
    bundle = service.activate(
        bundle["version_id"],
        actor="test",
        reason="unit test activation",
    )
    assert bundle["comparison_profile_version_id"] == comparison_profile.version_id
    assert [item["asset_type"] for item in bundle["assets"]] == [
        "comparison_profile",
        "identity_policy",
    ]
    assert bundle["parser_bundle"] == {}

    with _session_scope(session_factory) as db:
        db.get(
            TextComparisonProfileVersionRecord,
            comparison_profile.version_id,
        ).digest = "0" * 64
    with pytest.raises(RuntimeBundleValidationError, match="digest changed"):
        service.get_version(bundle["version_id"])


def test_first_patrol_can_open_risk_without_a_change(tmp_path):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        _service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    observed_at = datetime(2026, 7, 28, 10, tzinfo=timezone.utc)
    with _session_scope(session_factory) as db:
        db.add(
            CheckExecutionRecord(
                id="execution-cpu-1",
                idempotency_key="batch-1|device-1|system.cpu_usage",
                patrol_batch_id="batch-1",
                device_id="device-1",
                check_item_version_id=check_item.version_id,
                implementation_version_id=implementation.version_id,
                collection_action_ids_json=["action-1"],
                runtime_bundle_id=bundle["version_id"],
                runtime_bundle_digest=bundle["digest"],
                execution_status="completed",
                reason_codes_json=[],
                observation_count=1,
                observed_entity_count=1,
                target_manifest_digest="f" * 64,
                asset_manifest_digest=bundle["digest"],
                evidence_refs_json=["snapshot-1"],
                completed_at=observed_at,
                created_at=observed_at,
                updated_at=observed_at,
            )
        )

    result = CurrentStateEvaluationService.evaluate(
        CurrentStateEvaluationInput(
            patrol_batch_id="batch-1",
            check_execution_id="execution-cpu-1",
            device_id="device-1",
            check_item_id="system.cpu_usage",
            check_item_version_id=check_item.version_id,
            implementation_version_id=implementation.version_id,
            runtime_bundle_version_id=bundle["version_id"],
            runtime_bundle_digest=bundle["digest"],
            observations=(
                {
                    "id": "observation-cpu-1",
                    "device_id": "device-1",
                    "run_id": "run-1",
                    "entity_type": "cpu_scope",
                    "entity_key": '["device","0"]',
                    "field": "cpu_usage",
                    "observation_type": "device.cpu.utilization",
                    "value": 92,
                    "unit": "percent",
                    "dimensions": {"window": "instant"},
                    "command": "display cpu-usage",
                    "snapshot_id": "snapshot-1",
                    "observed_at": observed_at,
                    "parser_template_id": "parser-cpu-v1",
                    "observation_contract_id": "contract-cpu-v1",
                },
            ),
            rules=(
                {
                    "rule_id": "rule-cpu-high",
                    "version_id": "rule-cpu-high-v1",
                    "version": 1,
                    "digest": "d" * 64,
                    "event_type": "CpuUtilizationHigh",
                    "severity": "major",
                    "event_class": "fault",
                    "evaluation_scope": "both",
                    "condition": {
                        "kind": "threshold",
                        "entity_types": ["cpu_scope"],
                        "fields": ["cpu_usage"],
                        "gte": 80,
                    },
                },
            ),
            runtime_assets=tuple(bundle["assets"]),
            assessed_at=observed_at,
        )
    )
    assert result.coverage["finding_count"] == 1
    assert result.coverage["evaluated_entity_count"] == 1
    assert result.finding_values[0]["actual_value_number"] == 92.0

    saved = CurrentStateFindingRepository(scope).save(
        result,
        check_execution_id="execution-cpu-1",
    )
    assert len(saved) == 1
    risk_service = RiskCaseService(scope)
    risks = risk_service.observe_findings(
        check_item_id="system.cpu_usage",
        finding_ids=[saved[0]["id"]],
    )
    assert len(risks) == 1
    assert risks[0]["status"] == "open"

    still_open = risk_service.record_unable_to_confirm(
        risks[0]["id"],
        patrol_batch_id="batch-2",
        observed_at=observed_at + timedelta(days=1),
        reason="collection_failed",
        evidence_refs=["execution-cpu-2"],
    )
    assert still_open["status"] == "open"
    assert still_open["verification_status"] == "not_reverified"
    assert still_open["last_seen_at"].replace(tzinfo=timezone.utc) == (
        observed_at
    )
    with pytest.raises(ValueError, match="successful current-state"):
        risk_service.resolve_with_evidence(
            risks[0]["id"],
            successful_current_evaluation=False,
            observed_at=observed_at + timedelta(days=2),
            patrol_batch_id="batch-3",
            evidence_refs=["observation-cpu-recovered"],
            recovery_rule_version_id="rule-cpu-recovery-v1",
        )

    resolved = risk_service.resolve_with_evidence(
        risks[0]["id"],
        successful_current_evaluation=True,
        observed_at=observed_at + timedelta(days=2),
        patrol_batch_id="batch-3",
        evidence_refs=["observation-cpu-recovered"],
        recovery_rule_version_id="rule-cpu-recovery-v1",
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolution_evidence_refs_json"] == [
        "observation-cpu-recovered"
    ]

    assessment = CheckAssessmentService.assess(
        CheckAssessmentInput(
            execution_id="execution-cpu-1",
            patrol_batch_id="batch-1",
            device_id="device-1",
            check_item_version_id=check_item.version_id,
            execution_status="completed",
            assessment_mode="health",
            comparison_status="first_observation",
            observation_count=1,
            observed_entity_count=1,
            evaluated_entity_count=1,
            current_finding_ids=(saved[0]["id"],),
            unresolved_risk_case_ids=(risks[0]["id"],),
            rule_assets_ready=True,
            evidence_qualification_id="qualification-cpu-1",
            coverage_decision_id="coverage-cpu-1",
            source_identity_status="confirmed",
            output_quality_status="complete",
            freshness_status="fresh",
            empty_result_classification="non_empty",
            coverage_status="complete",
            coverage_basis="fixed",
            evidence_refs=("snapshot-1",),
            assessment_rule_bundle_digest=bundle["digest"],
        )
    )
    assert assessment.assessment_status == "at_risk"

    with _session_scope(session_factory) as db:
        assert (
            db.get(CheckExecutionRecord, "execution-cpu-1").current_finding_count
            == 1
        )
        occurrences = (
            db.query(RiskOccurrenceRecord)
            .order_by(RiskOccurrenceRecord.observed_at)
            .all()
        )
        assert [row.occurrence_kind for row in occurrences] == [
            "opened",
            "unable_to_confirm",
            "resolved",
        ]


def test_risk_workbench_query_and_remediation_lifecycle(tmp_path):
    session_factory, scope, _catalog_repository = _runtime(tmp_path)
    now = datetime(2026, 7, 28, 10, tzinfo=timezone.utc)
    risk_id = "risk-workbench-1"
    with _session_scope(session_factory) as db:
        db.add(
            NetworkDevice(
                id="device-risk-1",
                device_name="XA-Core-SW1",
                management_ip="192.0.2.80",
                vendor="H3C",
                model="S5130S",
                status="active",
            )
        )
        db.add(
            RiskCaseRecord(
                id=risk_id,
                correlation_key="risk-workbench-correlation-1",
                check_item_id="system.cpu_usage",
                device_id="device-risk-1",
                entity_type="cpu_scope",
                entity_key='["device","0"]',
                correlation_dimensions_json={"window": "5m"},
                risk_type="CpuUtilizationHigh",
                severity="major",
                status="open",
                first_seen_at=now - timedelta(days=3),
                last_seen_at=now,
                source_finding_ids_json=[],
                source_event_ids_json=[],
                remediation_status="not_started",
                created_at=now - timedelta(days=3),
                updated_at=now,
            )
        )

    service = RiskCaseService(scope)
    page = service.list_cases(
        page=1,
        page_size=10,
        search="192.0.2.80",
    )
    assert page["total"] == 1
    assert page["items"][0]["device_name"] == "XA-Core-SW1"
    assert "CPU" in page["items"][0]["check_item_name"]
    assert page["items"][0]["age_days"] >= 3
    assert page["summary"]["active"] == 1

    acknowledged = service.acknowledge(
        risk_id,
        actor="engineer",
        reason="已确认，安排窗口处理",
        observed_at=now,
        idempotency_key="ack-risk-workbench-1",
    )
    assert acknowledged["status"] == "acknowledged"
    repeated_acknowledgement = service.acknowledge(
        risk_id,
        actor="engineer",
        reason="重复请求不应新增生命周期记录",
        observed_at=now + timedelta(minutes=1),
        idempotency_key="ack-risk-workbench-1",
    )
    assert repeated_acknowledgement["status"] == "acknowledged"

    remediation = service.create_remediation(
        risk_id,
        action_text="核查高 CPU 进程并在维护窗口优化。",
        owner="network-team",
        created_by="engineer",
        due_at=now + timedelta(days=2),
        idempotency_key="remediation-risk-workbench-1",
    )
    repeated_remediation = service.create_remediation(
        risk_id,
        action_text="该文本不会覆盖第一次幂等创建。",
        owner="other-team",
        created_by="engineer",
        idempotency_key="remediation-risk-workbench-1",
    )
    assert repeated_remediation["id"] == remediation["id"]
    assert repeated_remediation["owner"] == "network-team"

    started = service.update_remediation(
        risk_id,
        remediation["id"],
        status="in_progress",
        actor="network-team",
        reason="维护窗口已经开始",
        observed_at=now + timedelta(hours=1),
        idempotency_key="start-remediation-risk-workbench-1",
    )
    assert started["status"] == "in_progress"
    with pytest.raises(ValueError, match="requires evidence"):
        service.update_remediation(
            risk_id,
            remediation["id"],
            status="completed",
            actor="network-team",
            reason="处理完成但没有证据",
        )
    completed = service.update_remediation(
        risk_id,
        remediation["id"],
        status="completed",
        actor="network-team",
        reason="进程优化完成，附变更单和复测输出",
        evidence_refs=["ticket:CHG-20260728-1", "snapshot:cpu-after-change"],
        observed_at=now + timedelta(hours=2),
        idempotency_key="complete-remediation-risk-workbench-1",
    )
    assert completed["status"] == "completed"

    detail = service.get_case(risk_id)
    assert detail["status"] == "recovering"
    assert detail["remediation_status"] == "completed"
    assert detail["remediations"][0]["evidence_refs_json"] == [
        "snapshot:cpu-after-change",
        "ticket:CHG-20260728-1",
    ]
    assert [item["occurrence_kind"] for item in detail["occurrences"]] == [
        "recovering",
        "acknowledged",
    ]


def test_execution_service_finalizes_current_state_without_fake_change(tmp_path):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        _bundle_service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    observed_at = datetime(2026, 7, 28, 10, tzinfo=timezone.utc)
    context = ComparisonContextService().select_online(
        device_id="device-exec-1",
        check_item_version_id=check_item.version_id,
        current_observed_at=observed_at,
        candidates=[],
        max_baseline_age_seconds=86400,
        canonical_schema_version=(
            f"{bundle['canonical_catalog_version']}:"
            f"{bundle['canonical_catalog_digest']}"
        ),
        entity_identity_policy_version=(
            bundle["entity_identity_policy_version_id"]
        ),
    )
    ComparisonContextRepository(scope).save(context)
    with _session_scope(session_factory) as db:
        db.add(
            CheckExecutionRecord(
                id="execution-service-cpu",
                idempotency_key="execution-service-cpu",
                patrol_batch_id="batch-service-cpu",
                device_id="device-exec-1",
                check_item_version_id=check_item.version_id,
                implementation_version_id=implementation.version_id,
                collection_action_ids_json=["action-cpu"],
                runtime_bundle_id=bundle["bundle_id"],
                runtime_bundle_version_id=bundle["version_id"],
                runtime_bundle_digest=bundle["digest"],
                comparison_context_id=context.id,
                execution_status="processing",
                reason_codes_json=[],
                target_manifest_digest="1" * 64,
                asset_manifest_digest=bundle["digest"],
                evidence_refs_json=["snapshot-cpu"],
                created_at=observed_at,
                updated_at=observed_at,
            )
        )

    service = CheckItemExecutionService(scope)
    result = service.finalize_structured(
        StructuredExecutionResultInput(
            execution_id="execution-service-cpu",
            observations=(
                {
                    "id": "observation-service-cpu",
                    "device_id": "device-exec-1",
                    "run_id": "run-service-cpu",
                    "entity_type": "cpu_scope",
                    "entity_key": '["device","0"]',
                    "field": "cpu_usage",
                    "observation_type": "device.cpu.utilization",
                    "value": 95,
                    "unit": "percent",
                    "dimensions": {"window": "instant"},
                    "command": "display cpu-usage",
                    "snapshot_id": "snapshot-cpu",
                    "observed_at": observed_at,
                    "parser_template_id": "parser-cpu-v1",
                    "observation_contract_id": "contract-cpu-v1",
                },
            ),
            snapshot_facts=(
                {
                    "id": "snapshot-cpu",
                    "device_id": "device-exec-1",
                    "inventory_device_id": "device-exec-1",
                    "output_status": "data",
                    "observed_at": observed_at,
                },
            ),
            evidence_refs=("snapshot-cpu",),
            assessed_at=observed_at,
        )
    )
    assert result["assessment"]["assessment_status"] == "at_risk"
    assert result["assessment"]["comparison_status"] == "first_observation"
    assert len(result["findings"]) == 1
    assert len(result["risks"]) == 1
    assert result["changes"] == []
    assert result["events"] == []

    repeated = service.finalize_structured(
        StructuredExecutionResultInput(
            execution_id="execution-service-cpu",
            observations=(),
            assessed_at=observed_at,
        )
    )
    assert repeated["assessment"]["id"] == result["assessment"]["id"]
    with _session_scope(session_factory) as db:
        execution = db.get(
            CheckExecutionRecord,
            "execution-service-cpu",
        )
        assert execution.change_count == 0
        assert execution.current_finding_count == 1
        assert db.query(CurrentStateFindingRecord).count() == 1
        assert db.query(RiskCaseRecord).count() == 1
        assert db.query(CheckAssessmentRecord).count() == 1


def test_structured_zero_records_forms_terminal_unknown_assessment(tmp_path):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        _bundle_service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    observed_at = datetime(2026, 7, 28, 10, 45, tzinfo=timezone.utc)
    context = ComparisonContextService().select_online(
        device_id="device-empty-1",
        check_item_version_id=check_item.version_id,
        current_observed_at=observed_at,
        candidates=[],
        max_baseline_age_seconds=86400,
        canonical_schema_version=(
            f"{bundle['canonical_catalog_version']}:"
            f"{bundle['canonical_catalog_digest']}"
        ),
        entity_identity_policy_version=(
            bundle["entity_identity_policy_version_id"]
        ),
    )
    ComparisonContextRepository(scope).save(context)
    with _session_scope(session_factory) as db:
        db.add(
            CheckExecutionRecord(
                id="execution-empty-cpu",
                idempotency_key="execution-empty-cpu",
                patrol_batch_id="batch-empty-cpu",
                device_id="device-empty-1",
                check_item_version_id=check_item.version_id,
                implementation_version_id=implementation.version_id,
                collection_action_ids_json=["action-empty-cpu"],
                runtime_bundle_id=bundle["bundle_id"],
                runtime_bundle_version_id=bundle["version_id"],
                runtime_bundle_digest=bundle["digest"],
                comparison_context_id=context.id,
                execution_status="processing",
                reason_codes_json=[],
                target_manifest_digest="2" * 64,
                asset_manifest_digest=bundle["digest"],
                evidence_refs_json=[],
                created_at=observed_at,
                updated_at=observed_at,
            )
        )

    result = CheckItemExecutionService(scope).finalize_structured(
        StructuredExecutionResultInput(
            execution_id="execution-empty-cpu",
            observations=(),
            snapshot_facts=(
                {
                    "id": "snapshot-empty-cpu",
                    "device_id": "device-empty-1",
                    "inventory_device_id": "device-empty-1",
                    "output_status": "valid_empty",
                    "observed_at": observed_at,
                },
            ),
            assessed_at=observed_at,
        )
    )
    assert result["execution"]["execution_status"] == "completed"
    assert result["assessment"]["assessment_status"] == "unable_to_determine"
    assert "output_quality_empty" in result["assessment"][
        "blocking_reason_codes_json"
    ]
    assert "no_observations" in result["assessment"][
        "blocking_reason_codes_json"
    ]
    assert result["findings"] == []
    with _session_scope(session_factory) as db:
        assert db.query(EvidenceQualificationRecord).count() == 1
        assert db.query(CoverageDecisionRecord).count() == 1
        assert db.query(CheckAssessmentRecord).count() == 1


def test_run_finalizer_uses_only_snapshot_selected_for_check_item(tmp_path):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        _bundle_service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    observed_at = datetime(2026, 7, 28, 10, 30, tzinfo=timezone.utc)
    context = ComparisonContextService().select_online(
        device_id="device-finalizer-1",
        check_item_version_id=check_item.version_id,
        current_observed_at=observed_at,
        candidates=[],
        max_baseline_age_seconds=86400,
        canonical_schema_version=(
            f"{bundle['canonical_catalog_version']}:"
            f"{bundle['canonical_catalog_digest']}"
        ),
        entity_identity_policy_version=(
            bundle["entity_identity_policy_version_id"]
        ),
    )
    ComparisonContextRepository(scope).save(context)
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                CommandSnapshot(
                    id="snapshot-finalizer-cpu",
                    run_id="run-finalizer-cpu",
                    device_id="device-finalizer-1",
                    execution_item_id="cpu",
                    collection_action_id="action-finalizer-cpu",
                    implementation_version_id=implementation.version_id,
                    check_item_ids_json=["system.cpu_usage"],
                    device_name="H3C-Finalizer",
                    management_ip="192.0.2.31",
                    vendor="H3C",
                    model="S5130",
                    command="display cpu-usage",
                    canonical_command="display cpu-usage",
                    observed_at=observed_at,
                    structured_json=[{"cpu_usage": "35"}],
                    raw_text="CPU usage: 35%",
                    raw_text_hash="8" * 64,
                    parse_status="parsed",
                    output_status="success",
                    parser_template_id="parser-cpu-v1",
                    parser_version="1",
                    contract_version=1,
                    metadata_json={
                        "check_item_bindings": [
                            {
                                "check_item_version_id": (
                                    check_item.version_id
                                )
                            }
                        ],
                        "selected_check_item_bindings": [
                            {
                                "check_item_version_id": (
                                    check_item.version_id
                                )
                            }
                        ],
                    },
                ),
                StateObservationRecord(
                    id="observation-finalizer-cpu",
                    idempotency_key="observation-finalizer-cpu",
                    run_id="run-finalizer-cpu",
                    snapshot_id="snapshot-finalizer-cpu",
                    mode="production",
                    source="cli",
                    source_type="cli",
                    device_id="device-finalizer-1",
                    vendor="H3C",
                    model="S5130",
                    canonical_command="display cpu-usage",
                    execution_item_id="cpu",
                    entity_type="cpu_scope",
                    entity_key='["device","0"]',
                    field_name="cpu_usage",
                    observation_type="device.cpu.utilization",
                    semantic_type="gauge",
                    unit="percent",
                    dimensions_json={"window": "instant"},
                    value_json=35,
                    value_number=35,
                    value_type="number",
                    observation_kind="state",
                    observed_at=observed_at,
                    fact_order_key="",
                    parser_template_id="parser-cpu-v1",
                    parser_version="1",
                    contract_version=1,
                    confidence=1.0,
                ),
                CheckExecutionRecord(
                    id="execution-finalizer-cpu",
                    idempotency_key="execution-finalizer-cpu",
                    patrol_batch_id="batch-finalizer-cpu",
                    device_id="device-finalizer-1",
                    check_item_version_id=check_item.version_id,
                    implementation_version_id=implementation.version_id,
                    collection_action_ids_json=["action-finalizer-cpu"],
                    runtime_bundle_id=bundle["bundle_id"],
                    runtime_bundle_version_id=bundle["version_id"],
                    runtime_bundle_digest=bundle["digest"],
                    comparison_context_id=context.id,
                    execution_status="processing",
                    reason_codes_json=[],
                    target_manifest_digest="9" * 64,
                    asset_manifest_digest=bundle["digest"],
                    evidence_refs_json=[],
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
            ]
        )

    result = CheckItemRunFinalizer(scope).finalize(
        patrol_batch_id="batch-finalizer-cpu",
        run_id="run-finalizer-cpu",
        device_id="device-finalizer-1",
    )

    assert result["success"] is True
    assert result["failure_count"] == 0
    with _session_scope(session_factory) as db:
        execution = db.get(
            CheckExecutionRecord,
            "execution-finalizer-cpu",
        )
        assessment = db.get(
            CheckAssessmentRecord,
            execution.assessment_id,
        )
        assert execution.execution_status == "completed"
        assert execution.observation_count == 1
        assert assessment.assessment_status == "healthy"
        assert "snapshot-finalizer-cpu" in execution.evidence_refs_json


def test_run_finalizer_does_not_claim_snapshot_selected_for_another_check(
    tmp_path,
):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        _bundle_service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    observed_at = datetime(2026, 7, 28, 11, tzinfo=timezone.utc)
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                CommandSnapshot(
                    id="snapshot-wrong-binding",
                    run_id="run-wrong-binding",
                    device_id="device-wrong-binding",
                    execution_item_id="shared-command",
                    collection_action_id="action-shared",
                    implementation_version_id=implementation.version_id,
                    check_item_ids_json=["system.cpu_usage"],
                    device_name="H3C-Wrong-Binding",
                    vendor="H3C",
                    model="S5130",
                    command="display cpu-usage",
                    canonical_command="display cpu-usage",
                    observed_at=observed_at,
                    structured_json=[{"cpu_usage": "35"}],
                    raw_text="CPU usage: 35%",
                    raw_text_hash="a" * 64,
                    parse_status="parsed",
                    output_status="success",
                    metadata_json={
                        "check_item_bindings": [
                            {
                                "check_item_version_id": (
                                    check_item.version_id
                                )
                            }
                        ],
                        "selected_check_item_bindings": [
                            {
                                "check_item_version_id": (
                                    "check-item:another:v1"
                                )
                            }
                        ],
                    },
                ),
                CheckExecutionRecord(
                    id="execution-wrong-binding",
                    idempotency_key="execution-wrong-binding",
                    patrol_batch_id="batch-wrong-binding",
                    device_id="device-wrong-binding",
                    check_item_version_id=check_item.version_id,
                    implementation_version_id=implementation.version_id,
                    collection_action_ids_json=["action-shared"],
                    runtime_bundle_id=bundle["bundle_id"],
                    runtime_bundle_version_id=bundle["version_id"],
                    runtime_bundle_digest=bundle["digest"],
                    execution_status="processing",
                    reason_codes_json=[],
                    target_manifest_digest="b" * 64,
                    asset_manifest_digest=bundle["digest"],
                    evidence_refs_json=[],
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
            ]
        )

    result = CheckItemRunFinalizer(scope).finalize(
        patrol_batch_id="batch-wrong-binding",
        run_id="run-wrong-binding",
        device_id="device-wrong-binding",
    )

    assert result["success"] is False
    assert result["failure_count"] == 1
    with _session_scope(session_factory) as db:
        execution = db.get(
            CheckExecutionRecord,
            "execution-wrong-binding",
        )
        assessment = db.get(
            CheckAssessmentRecord,
            execution.assessment_id,
        )
        assert execution.execution_status == "parse_failed"
        assert assessment.assessment_status == "unable_to_determine"
        assert "parse_or_observation_missing" in (
            assessment.blocking_reason_codes_json
        )


def test_run_finalizer_fail_device_preserves_actual_failure_stage(tmp_path):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        _bundle_service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    observed_at = datetime(2026, 7, 28, 11, 30, tzinfo=timezone.utc)
    with _session_scope(session_factory) as db:
        db.add(
            CheckExecutionRecord(
                id="execution-assessment-failed",
                idempotency_key="execution-assessment-failed",
                patrol_batch_id="batch-assessment-failed",
                device_id="device-assessment-failed",
                check_item_version_id=check_item.version_id,
                implementation_version_id=implementation.version_id,
                collection_action_ids_json=["action-cpu"],
                runtime_bundle_id=bundle["bundle_id"],
                runtime_bundle_version_id=bundle["version_id"],
                runtime_bundle_digest=bundle["digest"],
                execution_status="processing",
                reason_codes_json=[],
                target_manifest_digest="c" * 64,
                asset_manifest_digest=bundle["digest"],
                evidence_refs_json=[],
                created_at=observed_at,
                updated_at=observed_at,
            )
        )

    result = CheckItemRunFinalizer(scope).fail_device(
        patrol_batch_id="batch-assessment-failed",
        device_id="device-assessment-failed",
        reason_code="workflow_failed",
        status="assessment_failed",
        evidence_refs=("workflow-run-1",),
    )

    assert result["failed_execution_count"] == 1
    with _session_scope(session_factory) as db:
        execution = db.get(
            CheckExecutionRecord,
            "execution-assessment-failed",
        )
        assessment = db.get(
            CheckAssessmentRecord,
            execution.assessment_id,
        )
        assert execution.execution_status == "assessment_failed"
        assert assessment.assessment_status == "unable_to_determine"
        assert "execution_assessment_failed" in (
            assessment.blocking_reason_codes_json
        )


def test_execution_service_text_diff_uses_frozen_redacted_profile(tmp_path):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        profile,
        bundle,
    ) = _create_configuration_bundle(tmp_path)
    previous_at = datetime(2026, 7, 27, 10, tzinfo=timezone.utc)
    current_at = datetime(2026, 7, 28, 10, tzinfo=timezone.utc)
    context = ComparisonContextService().select_explicit(
        device_id="device-config-1",
        check_item_version_id=check_item.version_id,
        current_observed_at=current_at,
        baseline=BaselineCandidate(
            run_id="run-config-old",
            observed_at=previous_at,
            snapshot_ids=("snapshot-config-old",),
            canonical_schema_version=(
                f"{bundle['canonical_catalog_version']}:"
                f"{bundle['canonical_catalog_digest']}"
            ),
            entity_identity_policy_version=(
                bundle["entity_identity_policy_version_id"]
            ),
        ),
        canonical_schema_version=(
            f"{bundle['canonical_catalog_version']}:"
            f"{bundle['canonical_catalog_digest']}"
        ),
        entity_identity_policy_version=(
            bundle["entity_identity_policy_version_id"]
        ),
    )
    ComparisonContextRepository(scope).save(context)
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                CommandSnapshot(
                    id="snapshot-config-old",
                    run_id="run-config-old",
                    device_id="device-config-1",
                    execution_item_id="show-running",
                    collection_action_id="action-config-old",
                    implementation_version_id=implementation.version_id,
                    check_item_ids_json=["configuration.running"],
                    device_name="H3C-1",
                    management_ip="192.0.2.11",
                    vendor="H3C",
                    model="S5130",
                    command="display current-configuration",
                    canonical_command="display current-configuration",
                    observed_at=previous_at,
                    structured_json=None,
                    raw_text=(
                        "username ops secret old-secret\n"
                        "interface GigabitEthernet1/0/1\n"
                    ),
                    raw_text_hash="2" * 64,
                    parse_status="raw_hashed",
                    output_status="success",
                ),
                CommandSnapshot(
                    id="snapshot-config-current",
                    run_id="run-config-current",
                    device_id="device-config-1",
                    execution_item_id="show-running",
                    collection_action_id="action-config-current",
                    implementation_version_id=implementation.version_id,
                    check_item_ids_json=["configuration.running"],
                    device_name="H3C-1",
                    management_ip="192.0.2.11",
                    vendor="H3C",
                    model="S5130",
                    command="display current-configuration",
                    canonical_command="display current-configuration",
                    observed_at=current_at,
                    structured_json=None,
                    raw_text=(
                        "username ops secret new-secret\n"
                        "interface GigabitEthernet1/0/2\n"
                    ),
                    raw_text_hash="3" * 64,
                    parse_status="raw_hashed",
                    output_status="success",
                ),
                CheckExecutionRecord(
                    id="execution-service-config",
                    idempotency_key="execution-service-config",
                    patrol_batch_id="batch-service-config",
                    device_id="device-config-1",
                    check_item_version_id=check_item.version_id,
                    implementation_version_id=implementation.version_id,
                    collection_action_ids_json=["action-config-current"],
                    runtime_bundle_id=bundle["bundle_id"],
                    runtime_bundle_version_id=bundle["version_id"],
                    runtime_bundle_digest=bundle["digest"],
                    comparison_context_id=context.id,
                    execution_status="processing",
                    reason_codes_json=[],
                    target_manifest_digest="4" * 64,
                    asset_manifest_digest=bundle["digest"],
                    evidence_refs_json=[],
                    created_at=current_at,
                    updated_at=current_at,
                ),
            ]
        )

    result = CheckItemExecutionService(scope).finalize_text(
        TextExecutionResultInput(
            execution_id="execution-service-config",
            current_snapshot_id="snapshot-config-current",
            previous_snapshot_id="snapshot-config-old",
            assessed_at=current_at,
        )
    )
    assert result["assessment"]["assessment_status"] == "informational"
    assert result["assessment"]["comparison_status"] == "comparable"
    assert result["comparison"]["changed"] is True
    assert result["comparison"]["profile_version_id"] == profile.version_id
    assert len(result["changes"]) == 1
    preview = json.dumps(
        result["changes"][0]["dimensions_json"],
        ensure_ascii=False,
    )
    assert "old-secret" not in preview
    assert "new-secret" not in preview
    assert "GigabitEthernet1/0/2" in preview


def test_effective_plan_freezes_every_device_check_and_asset_gap(tmp_path):
    (
        session_factory,
        scope,
        catalog_repository,
        check_item,
        _implementation,
        _bundle_service,
        _bundle,
    ) = _create_cpu_bundle(tmp_path)
    now = datetime(2026, 7, 28, 10, tzinfo=timezone.utc)
    with _session_scope(session_factory) as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id="batch-plan-1",
                workflow_name="patrol-change-event",
                launch_mode="workflow",
                patrol_mode="production",
                target_type="devices",
                target_label="H3C test device",
                selector_json={"device_ids": ["device-1"]},
                target_count=1,
                launched_count=0,
                skipped_count=0,
                save_baseline=False,
                dispatch_status="pending",
                report_status="not_requested",
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )
    devices = (
        {
            "device_id": "device-1",
            "device_name": "H3C-1",
            "management_ip": "192.0.2.10",
            "vendor": "H3C",
            "family": "Comware7",
            "model": "S5130",
        },
    )
    target = build_target_manifest(
        devices=devices,
        selector={"device_ids": ["device-1"]},
    )
    inventory = build_inventory_snapshot(devices)
    request = EffectivePlanRequest(
        patrol_batch_id="batch-plan-1",
        devices=devices,
        check_item_ids=("system.cpu_usage", "system.memory_usage"),
        target_manifest_digest=target["digest"],
        inventory_snapshot_digest=inventory["digest"],
        current_observed_at=now,
        actor="test",
    )
    service = CheckItemEffectivePlanService(
        scope,
        catalog=catalog_repository.load_active(),
    )
    plan = service.freeze(request)

    assert len(plan.decisions) == 2
    cpu = next(
        item
        for item in plan.decisions
        if item["check_item_id"] == "system.cpu_usage"
    )
    memory = next(
        item
        for item in plan.decisions
        if item["check_item_id"] == "system.memory_usage"
    )
    assert cpu["status"] == "resolved"
    assert cpu["runtime_bundle_version_id"]
    assert memory["status"] == "blocked_asset_missing"
    assert memory["reason_codes"] == ["active_runtime_bundle_missing"]
    assert len(plan.collection_actions) == 1
    assert plan.collection_actions[0]["canonical_command"] == "display cpu"
    assert plan.collection_actions[0]["command"] == "display cpu-usage"
    assert cpu["unavailable_commands"] == [
        {
            "command": "display cpu",
            "canonical_command": "display cpu",
            "fallback_rank": 1,
            "reason_code": "asset_unavailable_at_plan_time",
        }
    ]
    assert len(plan.executions) == 2
    assert len(plan.terminal_assessments) == 1
    assert plan.terminal_assessments[0]["assessment_status"] == (
        "unable_to_determine"
    )
    assert len(plan.asset_gaps) == 1
    resolved_context = next(
        item
        for item in plan.comparison_contexts
        if item.check_item_version_id == check_item.version_id
    )
    assert resolved_context.canonical_schema_version == (
        f"{_bundle['canonical_catalog_version']}:"
        f"{_bundle['canonical_catalog_digest']}"
    )
    assert len(resolved_context.canonical_schema_version) > 64
    expected_identity_versions = sorted(
        {
            item["entity_identity_policy_version_id"]
            for item in plan.decisions
        }
    )
    assert plan.manifest["entity_identity_policy_versions"] == (
        expected_identity_versions
    )

    same = service.freeze(request)
    assert same.manifest_digest == plan.manifest_digest
    with _session_scope(session_factory) as db:
        assert db.query(CheckExecutionRecord).filter(
            CheckExecutionRecord.patrol_batch_id == "batch-plan-1"
        ).count() == 2
        assert db.query(CheckItemRuntimeManifestRecord).count() == 1
        assert db.query(AssetGapRecord).count() == 1
        runtime_manifest = db.query(
            CheckItemRuntimeManifestRecord
        ).one()
        assert sorted(
            runtime_manifest.entity_identity_policy_versions_json
        ) == expected_identity_versions
        assert runtime_manifest.manifest_json[
            "entity_identity_policy_versions"
        ] == expected_identity_versions
        batch = db.get(PatrolBatchRecord, "batch-plan-1")
        assert batch.runtime_manifest_digest == plan.manifest_digest
        assert batch.target_manifest_digest == target["digest"]

    changed = replace(request, target_manifest_digest="0" * 64)
    with pytest.raises(ValueError, match="target or inventory differs"):
        service.freeze(changed)
    unknown = replace(
        request,
        check_item_ids=("system.cpu_usage", "unknown.check"),
    )
    with pytest.raises(ValueError, match="unknown active check items"):
        service.freeze(unknown)

    with _session_scope(session_factory) as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id="batch-plan-2",
                workflow_name="patrol-change-event",
                launch_mode="workflow",
                patrol_mode="production",
                target_type="devices",
                target_label="H3C repeated gap",
                selector_json={"device_ids": ["device-1"]},
                target_count=1,
                launched_count=0,
                skipped_count=0,
                save_baseline=False,
                dispatch_status="pending",
                report_status="not_requested",
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )
    repeated_request = replace(
        request,
        patrol_batch_id="batch-plan-2",
        check_item_ids=("system.memory_usage",),
    )
    repeated = service.freeze(repeated_request)
    assert len(repeated.asset_gaps) == 1
    assert repeated.asset_gaps[0]["affected_execution_count"] == 1

    original_reloaded = service.load_frozen("batch-plan-1")
    assert original_reloaded is not None
    assert len(original_reloaded.asset_gaps) == 1
    assert original_reloaded.asset_gaps[0]["id"] == repeated.asset_gaps[0]["id"]
    with _session_scope(session_factory) as db:
        gap = db.get(AssetGapRecord, repeated.asset_gaps[0]["id"])
        assert gap.occurrence_count == 2
        assert gap.metadata_json["latest_patrol_batch_id"] == "batch-plan-2"


def test_profile_selection_is_version_safe(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    now = datetime.now(timezone.utc)
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                CommandProfileRecord(
                    id="profile-1",
                    name="golden-patrol",
                    status="draft",
                    current_version=0,
                    created_at=now,
                    updated_at=now,
                ),
                CommandProfileVersionRecord(
                    id="profile-1-v1",
                    profile_id="profile-1",
                    version=1,
                    status="draft",
                    items_json=[],
                    created_by="test",
                    created_at=now,
                ),
            ]
        )
    repository = PatrolProfileCheckItemRepository(scope)
    rows = repository.set_draft_selection(
        "profile-1-v1",
        ["system.cpu_usage", "interface.oper_status"],
        actor="test",
    )
    assert len(rows) == 2
    assert repository.active_check_item_ids("profile-1-v1") == (
        "interface.oper_status",
        "system.cpu_usage",
    )
    with _session_scope(session_factory) as db:
        db.get(CommandProfileVersionRecord, "profile-1-v1").status = "active"
    with pytest.raises(ValueError, match="immutable"):
        repository.set_draft_selection(
            "profile-1-v1",
            ["system.cpu_usage"],
            actor="test",
        )


def test_text_comparison_is_versioned_and_redacts_secrets(tmp_path):
    session_factory, scope, _catalog_repository = _runtime(tmp_path)
    profiles = load_text_comparison_profiles()
    repository = TextComparisonProfileRepository(scope)
    assert repository.bootstrap(profiles) == {
        "created_roots": 3,
        "created_versions": 3,
    }
    assert repository.bootstrap(profiles) == {
        "created_roots": 0,
        "created_versions": 0,
    }
    profile = next(
        item for item in profiles if item.id == "configuration.ordered_lines"
    )
    result = compare_text(
        profile=profile,
        previous_text=(
            "!Last configuration change 2026-07-27\n"
            "username ops secret old-secret\n"
            "interface GigabitEthernet1/0/1\n"
        ),
        current_text=(
            "!Last configuration change 2026-07-28\n"
            "username ops secret new-secret\n"
            "interface GigabitEthernet1/0/2\n"
        ),
    )
    assert result.changed is True
    assert result.added_lines == ("interface GigabitEthernet1/0/2",)
    assert result.removed_lines == ("interface GigabitEthernet1/0/1",)
    assert "new-secret" not in "\n".join(result.redacted_preview)
    with _session_scope(session_factory) as db:
        assert db.query(TextComparisonProfileVersionRecord).count() == 3


def test_asset_gap_is_idempotent_work_queue_not_generation(tmp_path):
    session_factory, scope, _catalog_repository = _runtime(tmp_path)
    repository = AssetGapRepository(scope)
    first = repository.record(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-item:system.cpu_usage:v1",
        implementation_version_id="implementation:h3c-cpu-usage:v1",
        gap_type="output_signature_drift",
        reason_code="unknown_output_signature",
        canonical_command="display cpu-usage",
        output_signature="f" * 64,
        sanitized_sample_refs=["sanitized://sample-1"],
    )
    second = repository.record(
        check_item_id="system.cpu_usage",
        check_item_version_id="check-item:system.cpu_usage:v1",
        implementation_version_id="implementation:h3c-cpu-usage:v1",
        gap_type="output_signature_drift",
        reason_code="unknown_output_signature",
        canonical_command="display cpu-usage",
        output_signature="f" * 64,
        sanitized_sample_refs=["sanitized://sample-2"],
    )
    assert second["id"] == first["id"]
    assert second["occurrence_count"] == 2
    assert second["sanitized_sample_refs_json"] == [
        "sanitized://sample-1",
        "sanitized://sample-2",
    ]
    assert repository.list_page(status="open")["total"] == 1


def test_report_manifest_is_final_only_after_execution_assessment_reconciliation(
    tmp_path,
):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        _bundle_service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
    context = ComparisonContextService().select_online(
        device_id="device-report-1",
        check_item_version_id=check_item.version_id,
        current_observed_at=now,
        candidates=[],
        max_baseline_age_seconds=86400,
        canonical_schema_version="canonical-v1",
        entity_identity_policy_version="identity-v1",
    )
    ComparisonContextRepository(scope).save(context)
    target_digest = "5" * 64
    inventory_digest = "7" * 64
    runtime_bundle_refs = [
        {
            "bundle_version_id": bundle["version_id"],
            "digest": bundle["digest"],
        }
    ]
    runtime_document, runtime_digest = _runtime_manifest_fixture(
        patrol_batch_id="batch-report-1",
        target_digest=target_digest,
        inventory_digest=inventory_digest,
        check_item_versions=[check_item.version_id],
        collection_actions=[{"id": "action-report-1"}],
        runtime_bundle_refs=runtime_bundle_refs,
        comparison_contexts=[{"id": context.id}],
        identity_versions=[
            bundle["entity_identity_policy_version_id"]
        ],
    )
    qualification = EvidenceQualificationService.qualify(
        EvidenceQualificationInput(
            execution_id="execution-report-1",
            patrol_batch_id="batch-report-1",
            device_id="device-report-1",
            check_item_version_id=check_item.version_id,
            implementation_version_id=implementation.version_id,
            runtime_bundle_version_id=bundle["version_id"],
            runtime_bundle_digest=bundle["digest"],
            snapshot_ids=("snapshot-report-1",),
            source_device_ids=("device-report-1",),
            inventory_device_ids=("device-report-1",),
            output_statuses=("data",),
            observed_ats=(now,),
            observation_count=1,
            assessment_mode="health",
            policy_snapshot=dict(
                bundle["compatibility"]["evidence_policy"]
            ),
            evidence_refs=("snapshot-report-1",),
            qualified_at=now,
        )
    )
    coverage_decision = CoverageDecisionService.decide(
        CoverageDecisionInput(
            execution_id="execution-report-1",
            patrol_batch_id="batch-report-1",
            device_id="device-report-1",
            check_item_version_id=check_item.version_id,
            observed_entity_keys=(("cpu_scope", '["device","0"]'),),
            evaluated_entity_count=1,
            policy_snapshot=dict(
                bundle["compatibility"]["evidence_policy"][
                    "coverage_policy"
                ]
            ),
            evidence_refs=(qualification.id,),
            decided_at=now,
        )
    )
    assessment = CheckAssessmentService.assess(
        CheckAssessmentInput(
            execution_id="execution-report-1",
            patrol_batch_id="batch-report-1",
            device_id="device-report-1",
            check_item_version_id=check_item.version_id,
            execution_status="completed",
            assessment_mode="health",
            comparison_status="first_observation",
            observation_count=1,
            observed_entity_count=1,
            evaluated_entity_count=1,
            rule_assets_ready=True,
            evidence_qualification_id=qualification.id,
            coverage_decision_id=coverage_decision.id,
            source_identity_status=(
                qualification.source_identity_status
            ),
            output_quality_status=(
                qualification.output_quality_status
            ),
            freshness_status=qualification.freshness_status,
            empty_result_classification=(
                qualification.empty_result_classification
            ),
            coverage_status=coverage_decision.coverage_status,
            coverage_basis=coverage_decision.coverage_basis,
            evidence_refs=("snapshot-report-1",),
            assessment_rule_bundle_digest=bundle["digest"],
            comparison_context_digest=context.digest,
            assessed_at=now,
        )
    )
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                PatrolBatchRecord(
                    patrol_batch_id="batch-report-1",
                    workflow_name="patrol-change-event",
                    launch_mode="direct",
                    patrol_mode="production",
                    target_type="devices",
                    target_label="report test",
                    selector_json={"device_ids": ["device-report-1"]},
                    target_count=1,
                    launched_count=1,
                    skipped_count=0,
                    target_manifest_digest=target_digest,
                    inventory_snapshot_digest=inventory_digest,
                    runtime_manifest_id="runtime-report-1",
                    runtime_manifest_digest=runtime_digest,
                    save_baseline=False,
                    dispatch_status="completed",
                    report_status="not_requested",
                    archived=False,
                    created_at=now,
                    updated_at=now,
                ),
                PatrolBatchDeviceRecord(
                    id="target-report-1",
                    patrol_batch_id="batch-report-1",
                    device_id="device-report-1",
                    device_name="H3C-Report",
                    management_ip="192.0.2.12",
                    selection_status="launched",
                    preflight_status="ready",
                    celery_task_id="task-report-1",
                    dispatch_status="dispatched",
                    dispatch_attempts=1,
                    dispatched_at=now,
                    created_at=now,
                    updated_at=now,
                ),
                CheckItemRuntimeManifestRecord(
                    id="runtime-report-1",
                    patrol_batch_id="batch-report-1",
                    schema_version=2,
                    target_manifest_digest=target_digest,
                    inventory_snapshot_digest=inventory_digest,
                    check_item_versions_json=[check_item.version_id],
                    collection_actions_json=["action-report-1"],
                    runtime_bundle_refs_json=runtime_bundle_refs,
                    comparison_context_ids_json=[context.id],
                    entity_identity_policy_versions_json=[
                        bundle["entity_identity_policy_version_id"]
                    ],
                    assessment_schema_version="check-assessment/v1",
                    report_schema_version="patrol-report/v2",
                    manifest_json=runtime_document,
                    digest=runtime_digest,
                    status="frozen",
                    created_by="test",
                    created_at=now,
                ),
                CommandSnapshot(
                    id="snapshot-report-1",
                    run_id="run-report-1",
                    device_id="device-report-1",
                    execution_item_id="cpu",
                    collection_action_id="action-report-1",
                    implementation_version_id=implementation.version_id,
                    check_item_ids_json=["system.cpu_usage"],
                    device_name="H3C-Report",
                    management_ip="192.0.2.12",
                    vendor="H3C",
                    model="S5130",
                    command="display cpu-usage",
                    canonical_command="display cpu-usage",
                    observed_at=now,
                    structured_json=[{"cpu_usage": "35"}],
                    raw_text="CPU usage: 35%",
                    raw_text_hash="6" * 64,
                    parse_status="parsed",
                    output_status="success",
                    parser_template_id="parser-cpu-v1",
                    parser_version="1",
                    contract_version=1,
                ),
                StateObservationRecord(
                    id="observation-report-1",
                    idempotency_key="observation-report-1",
                    run_id="run-report-1",
                    snapshot_id="snapshot-report-1",
                    mode="production",
                    source="cli",
                    source_type="cli",
                    device_id="device-report-1",
                    vendor="H3C",
                    model="S5130",
                    canonical_command="display cpu-usage",
                    execution_item_id="cpu",
                    entity_type="cpu_scope",
                    entity_key='["device","0"]',
                    field_name="cpu_usage",
                    observation_type="device.cpu.utilization",
                    semantic_type="gauge",
                    unit="percent",
                    dimensions_json={"window": "instant"},
                    value_json=35,
                    value_number=35,
                    value_type="number",
                    observation_kind="state",
                    observed_at=now,
                    fact_order_key="",
                    parser_template_id="parser-cpu-v1",
                    parser_version="1",
                    contract_version=1,
                    confidence=1.0,
                ),
                RiskCaseRecord(
                    id="risk-from-other-batch",
                    correlation_key="other-batch-risk",
                    check_item_id="system.cpu_usage",
                    device_id="device-report-1",
                    entity_type="cpu_scope",
                    entity_key='["device","9"]',
                    risk_type="CpuUtilizationHigh",
                    severity="major",
                    status="open",
                    first_seen_at=now - timedelta(days=1),
                    last_seen_at=now - timedelta(days=1),
                    source_finding_ids_json=[],
                    source_event_ids_json=[],
                    remediation_status="not_started",
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
                CheckExecutionRecord(
                    id="execution-report-1",
                    idempotency_key="execution-report-1",
                    patrol_batch_id="batch-report-1",
                    device_id="device-report-1",
                    check_item_version_id=check_item.version_id,
                    implementation_version_id=implementation.version_id,
                    collection_action_ids_json=["action-report-1"],
                    runtime_bundle_id=bundle["bundle_id"],
                    runtime_bundle_version_id=bundle["version_id"],
                    runtime_bundle_digest=bundle["digest"],
                    comparison_context_id=context.id,
                    execution_status="completed",
                    reason_codes_json=[],
                    observation_count=1,
                    observed_entity_count=1,
                    change_count=0,
                    event_count=0,
                    current_finding_count=0,
                    assessment_id=assessment.id,
                    evidence_qualification_id=qualification.id,
                    coverage_decision_id=coverage_decision.id,
                    source_identity_status="confirmed",
                    output_quality_status="complete",
                    freshness_status="fresh",
                    empty_result_classification="non_empty",
                    coverage_status="complete",
                    coverage_basis="fixed",
                    risk_verification_status="no_current_risk",
                    expected_entity_count=1,
                    observation_min_at=now,
                    observation_max_at=now,
                    target_manifest_digest=target_digest,
                    asset_manifest_digest=bundle["digest"],
                    evidence_refs_json=["snapshot-report-1"],
                    completed_at=now,
                    created_at=now,
                    updated_at=now,
                ),
                EvidenceQualificationRecord(**qualification.values),
                CoverageDecisionRecord(**coverage_decision.values),
                CheckAssessmentRecord(**assessment.values),
            ]
        )

    service = ReportManifestService(scope)
    manifest = service.build(
        "batch-report-1",
        generated_by="test",
    )
    assert manifest["status"] == "final"
    assert manifest["source_facts_json"]["readiness"][
        "ready_for_final"
    ] is True
    facts = manifest["source_facts_json"]
    assert facts["check_items"][check_item.version_id][
        "check_item_id"
    ] == "system.cpu_usage"
    assert facts["observations"][0]["execution_ids"] == [
        "execution-report-1"
    ]
    assert facts["observations"][0]["value_number"] == 35
    assert facts["evidence_index"][0]["snapshot_id"] == (
        "snapshot-report-1"
    )
    assert "raw_text" not in facts["evidence_index"][0]
    assert facts["risks"] == []
    assert service.build(
        "batch-report-1",
        generated_by="test",
    )["id"] == manifest["id"]
    batch_results = CheckItemBatchResultService(scope)
    live_summary = batch_results.summary("batch-report-1")
    assert live_summary["conclusion"]["code"] == "healthy"
    assert live_summary["conclusion"]["formal"] is True
    result_page = batch_results.list_results(
        "batch-report-1",
        limit=25,
        offset=0,
        search="192.0.2.12",
        assessment_status="healthy",
    )
    assert result_page["total"] == 1
    assert result_page["items"][0]["check_item_id"] == (
        "system.cpu_usage"
    )
    assert result_page["items"][0]["assessment_status"] == "healthy"
    assert result_page["items"][0]["source_identity_status"] == (
        "confirmed"
    )
    assert result_page["items"][0]["output_quality_status"] == "complete"
    assert result_page["items"][0]["freshness_status"] == "fresh"
    assert result_page["items"][0]["coverage_status"] == "complete"
    assert result_page["items"][0]["coverage_basis"] == "fixed"
    assert result_page["items"][0]["expected_entity_count"] == 1
    assert batch_results.list_results(
        "batch-report-1",
        output_quality_status="truncated",
    )["total"] == 0
    assert batch_results.list_results(
        "batch-report-1",
        source_identity_status="confirmed",
        freshness_status="fresh",
        coverage_status="complete",
    )["total"] == 1
    attached = service.attach_artifacts(
        manifest["id"],
        object_keys={
            "markdown": "reports/batch-report-1/report.md",
            "excel": "reports/batch-report-1/appendix.xlsx",
        },
        sha256_by_artifact={
            "markdown": "8" * 64,
            "excel": "9" * 64,
        },
    )
    assert attached["artifact_sha256_json"]["excel"] == "9" * 64
    release_service = ReportReleaseService(scope)
    with pytest.raises(ValueError, match="disclosure confirmations"):
        release_service.release(
            manifest["id"],
            signed_by="reviewer",
            signoff_comment="reviewed",
            disclosure_confirmations=["facts_reviewed"],
            verified_artifact_sha256=attached[
                "artifact_sha256_json"
            ],
        )
    with pytest.raises(ValueError, match="do not match"):
        release_service.release(
            manifest["id"],
            signed_by="reviewer",
            signoff_comment="reviewed",
            disclosure_confirmations=[
                "facts_reviewed",
                "unknown_scope_disclosed",
                "artifact_digests_verified",
            ],
            verified_artifact_sha256={
                "markdown": "7" * 64,
                "excel": "9" * 64,
            },
        )
    release = release_service.release(
        manifest["id"],
        signed_by="reviewer",
        signoff_comment="facts and disclosure boundaries reviewed",
        disclosure_confirmations=[
            "facts_reviewed",
            "unknown_scope_disclosed",
            "artifact_digests_verified",
        ],
        verified_artifact_sha256=attached["artifact_sha256_json"],
        signed_at=now + timedelta(minutes=5),
    )
    assert release["status"] == "released"
    assert release["source_facts_digest"] == manifest["source_digest"]
    assert release_service.release(
        manifest["id"],
        signed_by="reviewer",
        signoff_comment="facts and disclosure boundaries reviewed",
        disclosure_confirmations=[
            "facts_reviewed",
            "unknown_scope_disclosed",
            "artifact_digests_verified",
        ],
        verified_artifact_sha256=attached["artifact_sha256_json"],
    )["id"] == release["id"]
    with pytest.raises(ValueError, match="immutable"):
        service.attach_artifacts(
            manifest["id"],
            object_keys={
                "markdown": "reports/batch-report-1/report-v2.md",
                "excel": "reports/batch-report-1/appendix.xlsx",
            },
            sha256_by_artifact={
                "markdown": "a" * 64,
                "excel": "9" * 64,
            },
        )
    with _session_scope(session_factory) as db:
        execution = db.get(
            CheckExecutionRecord,
            "execution-report-1",
        )
        execution.reason_codes_json = ["late_fact_revision"]
    revised = service.build(
        "batch-report-1",
        generated_by="test",
    )
    assert revised["id"] != manifest["id"]
    assert revised["version"] == 2
    assert revised["status"] == "final"
    previous_cutoff = manifest["report_cutoff_at"]
    revised_cutoff = revised["report_cutoff_at"]
    if previous_cutoff.tzinfo is None:
        previous_cutoff = previous_cutoff.replace(tzinfo=timezone.utc)
    if revised_cutoff.tzinfo is None:
        revised_cutoff = revised_cutoff.replace(tzinfo=timezone.utc)
    assert revised_cutoff > previous_cutoff
    assert revised["source_facts_json"]["executions"][0][
        "reason_codes_json"
    ] == ["late_fact_revision"]
    assert revised["artifact_object_keys_json"] == {}
    assert release_service.get_for_manifest(revised["id"]) is None
    assert release_service.get_for_manifest(manifest["id"])["id"] == (
        release["id"]
    )
    assert service.latest_for_batch("batch-report-1")["id"] == (
        revised["id"]
    )
    with _session_scope(session_factory) as db:
        assert db.get(
            ReportManifestRecord,
            manifest["id"],
        ).status == "superseded"
    assert service.build(
        "batch-report-1",
        generated_by="test",
    )["id"] == revised["id"]


def test_mixed_device_outcomes_and_direct_workflow_terminal_semantics(
    tmp_path,
):
    (
        session_factory,
        scope,
        catalog_repository,
        check_item,
        implementation,
        _bundle_service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    now = datetime(2026, 7, 28, 12, 30, tzinfo=timezone.utc)
    batch_id = "batch-mixed-terminal"
    devices = (
        {
            "device_id": "device-mixed-healthy",
            "device_name": "H3C-Healthy",
            "management_ip": "192.0.2.21",
            "vendor": "H3C",
            "family": "Comware7",
            "model": "S5130",
        },
        {
            "device_id": "device-mixed-failed",
            "device_name": "H3C-Unreachable",
            "management_ip": "192.0.2.22",
            "vendor": "H3C",
            "family": "Comware7",
            "model": "S5130",
        },
    )
    target = build_target_manifest(
        devices=devices,
        selector={"device_ids": [item["device_id"] for item in devices]},
    )
    inventory = build_inventory_snapshot(devices)
    with _session_scope(session_factory) as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id=batch_id,
                workflow_name="patrol-change-event",
                launch_mode="direct",
                patrol_mode="production",
                target_type="devices",
                target_label="mixed terminal outcomes",
                selector_json={
                    "device_ids": [item["device_id"] for item in devices]
                },
                target_count=2,
                launched_count=2,
                skipped_count=0,
                target_manifest_digest=target["digest"],
                inventory_snapshot_digest=inventory["digest"],
                dispatch_status="completed",
                report_status="not_requested",
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )
    plan = CheckItemEffectivePlanService(
        scope,
        catalog=catalog_repository.load_active(),
    ).freeze(
        EffectivePlanRequest(
            patrol_batch_id=batch_id,
            devices=devices,
            check_item_ids=("system.cpu_usage",),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=now,
            actor="test",
        )
    )
    assert len(plan.executions) == 2
    assert len(plan.collection_actions) == 2
    action_by_device = {
        str(item["device_id"]): item
        for item in plan.collection_actions
    }
    execution_by_device = {
        str(item["device_id"]): item
        for item in plan.executions
    }
    healthy_action = action_by_device["device-mixed-healthy"]
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                PatrolBatchDeviceRecord(
                    id="target-mixed-healthy",
                    patrol_batch_id=batch_id,
                    device_id="device-mixed-healthy",
                    device_name="H3C-Healthy",
                    management_ip="192.0.2.21",
                    selection_status="launched",
                    preflight_status="ready",
                    dispatch_status="dispatched",
                    dispatch_attempts=1,
                    dispatched_at=now,
                    created_at=now,
                    updated_at=now,
                ),
                PatrolBatchDeviceRecord(
                    id="target-mixed-failed",
                    patrol_batch_id=batch_id,
                    device_id="device-mixed-failed",
                    device_name="H3C-Unreachable",
                    management_ip="192.0.2.22",
                    selection_status="launched",
                    preflight_status="ready",
                    failure_code="login_failed",
                    error_message="authentication failed",
                    dispatch_status="failed",
                    dispatch_attempts=1,
                    dispatched_at=now,
                    created_at=now,
                    updated_at=now,
                ),
                CommandSnapshot(
                    id="snapshot-mixed-healthy",
                    run_id="run-mixed-healthy",
                    device_id="device-mixed-healthy",
                    execution_item_id=str(healthy_action["id"]),
                    collection_action_id=str(healthy_action["id"]),
                    implementation_version_id=implementation.version_id,
                    check_item_ids_json=["system.cpu_usage"],
                    device_name="H3C-Healthy",
                    management_ip="192.0.2.21",
                    vendor="H3C",
                    model="S5130",
                    command="display cpu-usage",
                    canonical_command="display cpu",
                    observed_at=now,
                    structured_json=[{"cpu_usage": "35"}],
                    raw_text="CPU usage: 35%",
                    raw_text_hash="d" * 64,
                    parse_status="parsed",
                    output_status="success",
                    parser_template_id="parser-cpu-v1",
                    parser_version="1",
                    contract_version=1,
                ),
                StateObservationRecord(
                    id="observation-mixed-healthy",
                    idempotency_key="observation-mixed-healthy",
                    run_id="run-mixed-healthy",
                    snapshot_id="snapshot-mixed-healthy",
                    mode="production",
                    source="cli",
                    source_type="cli",
                    device_id="device-mixed-healthy",
                    vendor="H3C",
                    model="S5130",
                    canonical_command="display cpu",
                    execution_item_id=str(healthy_action["id"]),
                    entity_type="cpu_scope",
                    entity_key='["device","0"]',
                    field_name="cpu_usage",
                    observation_type="device.cpu.utilization",
                    semantic_type="gauge",
                    unit="percent",
                    dimensions_json={"window": "instant"},
                    value_json=35,
                    value_number=35,
                    value_type="number",
                    observation_kind="state",
                    observed_at=now,
                    fact_order_key="",
                    parser_template_id="parser-cpu-v1",
                    parser_version="1",
                    contract_version=1,
                    confidence=1.0,
                ),
            ]
        )
    healthy = CheckItemExecutionService(scope).finalize_structured(
        StructuredExecutionResultInput(
            execution_id=str(
                execution_by_device["device-mixed-healthy"]["id"]
            ),
            observations=(
                {
                    "id": "observation-mixed-healthy",
                    "device_id": "device-mixed-healthy",
                    "run_id": "run-mixed-healthy",
                    "snapshot_id": "snapshot-mixed-healthy",
                    "entity_type": "cpu_scope",
                    "entity_key": '["device","0"]',
                    "field": "cpu_usage",
                    "observation_type": "device.cpu.utilization",
                    "value": 35,
                    "unit": "percent",
                    "dimensions": {"window": "instant"},
                    "command": "display cpu",
                    "observed_at": now,
                    "parser_template_id": "parser-cpu-v1",
                    "observation_contract_id": "contract-cpu-v1",
                },
            ),
            snapshot_facts=(
                {
                    "id": "snapshot-mixed-healthy",
                    "device_id": "device-mixed-healthy",
                    "inventory_device_id": "device-mixed-healthy",
                    "output_status": "data",
                    "observed_at": now,
                },
            ),
            evidence_refs=("snapshot-mixed-healthy",),
            assessed_at=now,
        )
    )
    assert healthy["assessment"]["assessment_status"] == "healthy"
    failed = CheckItemRunFinalizer(scope).fail_device(
        patrol_batch_id=batch_id,
        device_id="device-mixed-failed",
        reason_code="login_failed",
        status="collection_failed",
    )
    assert failed["failed_execution_count"] == 1

    service = ReportManifestService(scope)
    direct = service.build(batch_id, generated_by="test")
    assert direct["status"] == "final"
    direct_report = build_check_item_report_view(direct)
    assert direct_report["conclusion"]["code"] != "healthy"
    assessments = {
        item["device_id"]: item["assessment_status"]
        for item in direct["source_facts_json"]["assessments"]
    }
    assert assessments == {
        "device-mixed-failed": "unable_to_determine",
        "device-mixed-healthy": "healthy",
    }

    with _session_scope(session_factory) as db:
        batch = db.get(PatrolBatchRecord, batch_id)
        batch.launch_mode = "workflow"
        targets = {
            item.device_id: item
            for item in db.query(PatrolBatchDeviceRecord)
            .filter(PatrolBatchDeviceRecord.patrol_batch_id == batch_id)
            .all()
        }
        targets["device-mixed-healthy"].workflow_run_id = (
            "workflow-mixed-healthy"
        )
        targets["device-mixed-failed"].workflow_run_id = (
            "workflow-mixed-failed"
        )
        db.add_all(
            [
                WorkflowRun(
                    id="workflow-mixed-healthy",
                    template_name="patrol-change-event",
                    source="device-management",
                    status="completed",
                    context={"patrol_batch_id": batch_id},
                    current_step_index=1,
                    created_at=now,
                    updated_at=now,
                    completed_at=now,
                ),
                WorkflowRun(
                    id="workflow-mixed-failed",
                    template_name="patrol-change-event",
                    source="device-management",
                    status="failed",
                    context={"patrol_batch_id": batch_id},
                    current_step_index=1,
                    error_message="authentication failed",
                    created_at=now,
                    updated_at=now,
                    completed_at=now,
                ),
            ]
        )
    workflow_terminal = service.build(batch_id, generated_by="test")
    assert workflow_terminal["status"] == "final"
    assert build_check_item_report_view(workflow_terminal)[
        "conclusion"
    ]["code"] == direct_report["conclusion"]["code"]

    with _session_scope(session_factory) as db:
        run = db.get(WorkflowRun, "workflow-mixed-failed")
        run.status = "running"
        run.completed_at = None
        run.updated_at = now + timedelta(minutes=1)
    workflow_active = service.build(batch_id, generated_by="test")
    assert workflow_active["status"] == "partial"
    assert "workflow_runs_not_terminal" in workflow_active[
        "source_facts_json"
    ]["readiness"]["reason_codes"]

    with _session_scope(session_factory) as db:
        run = db.get(WorkflowRun, "workflow-mixed-failed")
        run.status = "failed"
        run.completed_at = now + timedelta(minutes=2)
        run.updated_at = run.completed_at
    workflow_reconciled = service.build(batch_id, generated_by="test")
    assert workflow_reconciled["status"] == "final"
    assert build_check_item_report_view(workflow_reconciled)[
        "conclusion"
    ]["code"] == direct_report["conclusion"]["code"]


def test_report_manifest_stays_partial_when_assessment_is_missing(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item = catalog_repository.load_active().check_item("system.cpu_usage")
    assert check_item is not None
    now = datetime(2026, 7, 28, 13, tzinfo=timezone.utc)
    runtime_document, runtime_digest = _runtime_manifest_fixture(
        patrol_batch_id="batch-report-partial",
        target_digest="a" * 64,
        inventory_digest="b" * 64,
        check_item_versions=[check_item.version_id],
        collection_actions=[],
        runtime_bundle_refs=[],
        comparison_contexts=[],
        identity_versions=[],
    )
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                PatrolBatchRecord(
                    patrol_batch_id="batch-report-partial",
                    workflow_name="patrol-change-event",
                    launch_mode="direct",
                    patrol_mode="production",
                    target_type="devices",
                    target_label="partial report test",
                    selector_json={"device_ids": ["device-report-2"]},
                    target_count=1,
                    launched_count=1,
                    skipped_count=0,
                    target_manifest_digest="a" * 64,
                    inventory_snapshot_digest="b" * 64,
                    runtime_manifest_id="runtime-report-partial",
                    runtime_manifest_digest=runtime_digest,
                    dispatch_status="completed",
                    report_status="not_requested",
                    archived=False,
                    created_at=now,
                    updated_at=now,
                ),
                PatrolBatchDeviceRecord(
                    id="target-report-2",
                    patrol_batch_id="batch-report-partial",
                    device_id="device-report-2",
                    device_name="H3C-Partial",
                    selection_status="launched",
                    preflight_status="ready",
                    celery_task_id="task-report-2",
                    dispatch_status="dispatched",
                    dispatch_attempts=1,
                    created_at=now,
                    updated_at=now,
                ),
                CheckItemRuntimeManifestRecord(
                    id="runtime-report-partial",
                    patrol_batch_id="batch-report-partial",
                    schema_version=2,
                    target_manifest_digest="a" * 64,
                    inventory_snapshot_digest="b" * 64,
                    check_item_versions_json=[check_item.version_id],
                    collection_actions_json=[],
                    runtime_bundle_refs_json=[],
                    comparison_context_ids_json=[],
                    entity_identity_policy_versions_json=[],
                    assessment_schema_version="check-assessment/v1",
                    report_schema_version="patrol-report/v2",
                    manifest_json=runtime_document,
                    digest=runtime_digest,
                    status="frozen",
                    created_by="test",
                    created_at=now,
                ),
                CheckExecutionRecord(
                    id="execution-report-partial",
                    idempotency_key="execution-report-partial",
                    patrol_batch_id="batch-report-partial",
                    device_id="device-report-2",
                    check_item_version_id=check_item.version_id,
                    collection_action_ids_json=[],
                    execution_status="processing",
                    reason_codes_json=[],
                    target_manifest_digest="a" * 64,
                    evidence_refs_json=[],
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )

    manifest = ReportManifestService(scope).build(
        "batch-report-partial",
        generated_by="test",
    )
    assert manifest["status"] == "partial"
    reasons = manifest["source_facts_json"]["readiness"]["reason_codes"]
    assert "check_executions_not_terminal" in reasons
    assert "check_assessments_missing" in reasons
    live_summary = CheckItemBatchResultService(scope).summary(
        "batch-report-partial"
    )
    assert live_summary["conclusion"]["code"] == "in_progress"
    assert live_summary["conclusion"]["formal"] is False
    assert "未发现异常" not in live_summary["conclusion"]["text"]
    pending_page = CheckItemBatchResultService(scope).list_results(
        "batch-report-partial",
        assessment_status="pending",
    )
    assert pending_page["total"] == 1
