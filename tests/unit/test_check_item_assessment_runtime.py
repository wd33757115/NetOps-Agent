from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.core.check_items import (
    AssessmentPolicyAssetService,
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
    ProductionBaselineCandidateRepository,
    ReportManifestService,
    ReportReleaseService,
    RiskCaseService,
    RuntimeBundleValidationError,
    StructuredExecutionResultInput,
    TextComparisonProfileRepository,
    TextExecutionResultInput,
    build_comparison_fact_contract,
    build_inventory_snapshot,
    build_target_manifest,
    compare_text,
    load_catalog,
    load_text_comparison_profiles,
)
from src.core.check_items.assessment_policy import (
    assessment_policy_source_digest,
    build_assessment_basis,
    build_target_assessment_policy_snapshot,
)
from src.core.check_items.assessment_policy_migration import (
    AssessmentPolicyMigrationService,
)
from src.core.check_items.report_content_review import (
    REQUIRED_CONTENT_CONFIRMATIONS,
    ReportContentReviewService,
)
from src.core.check_items.report_contract import (
    build_report_authority_context,
)
from src.core.check_items.reporting import build_check_item_report_view
from src.infrastructure.db.models import (
    AssessmentPolicyBindingRecord,
    AssessmentPolicyCutoverRecord,
    AssessmentPolicyDecisionRecord,
    AssessmentPolicyProfileRecord,
    AssessmentPolicyVersionRecord,
    AssetGapRecord,
    AuditLogRecord,
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
    EventRuleRecord,
    EventRuleVersionRecord,
    EvidenceQualificationRecord,
    NetworkChangeRecord,
    NetworkDevice,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    ParserTemplate,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolCollectionResult,
    PatrolIntendedTargetMatrixRecord,
    PatrolProfileCheckItemRecord,
    PatrolScopeContractRecord,
    PatrolScopeContractVersionRecord,
    PatrolScopeDecisionRecord,
    ReportClaimRecord,
    ReportContentReviewItemRecord,
    ReportContentReviewRecord,
    ReportManifestRecord,
    ReportReleaseRecord,
    RiskCaseRecord,
    RiskEpisodeAssociationRecord,
    RiskOccurrenceRecord,
    RiskRemediationRecord,
    RuntimeBundleAssetRecord,
    StateObservationRecord,
    TextComparisonProfileRecord,
    TextComparisonProfileVersionRecord,
    WorkflowRun,
)


def _stable_digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


class _MemoryObjectStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)

    def download_file(self, object_name: str) -> bytes | None:
        return self.objects.get(object_name)

    def sha256_object(self, object_name: str) -> dict[str, int | str] | None:
        content = self.objects.get(object_name)
        if content is None:
            return None
        return {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }


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
            AssessmentPolicyProfileRecord.__table__,
            AssessmentPolicyVersionRecord.__table__,
            AssessmentPolicyDecisionRecord.__table__,
            AssessmentPolicyBindingRecord.__table__,
            AssessmentPolicyCutoverRecord.__table__,
            CollectionImplementationRecord.__table__,
            CollectionImplementationVersionRecord.__table__,
            CheckItemImplementationLinkRecord.__table__,
            AuditLogRecord.__table__,
            ParserTemplate.__table__,
            CollectionImplementationParserBindingRecord.__table__,
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
            PatrolCollectionResult.__table__,
            StateObservationRecord.__table__,
            NetworkChangeRecord.__table__,
            NetworkEventRecord.__table__,
            NetworkEventOccurrenceRecord.__table__,
            PatrolBatchRecord.__table__,
            PatrolScopeContractRecord.__table__,
            PatrolScopeContractVersionRecord.__table__,
            PatrolIntendedTargetMatrixRecord.__table__,
            PatrolScopeDecisionRecord.__table__,
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
            RiskEpisodeAssociationRecord.__table__,
            RiskOccurrenceRecord.__table__,
            RiskRemediationRecord.__table__,
            ReportManifestRecord.__table__,
            ReportClaimRecord.__table__,
            ReportContentReviewRecord.__table__,
            ReportContentReviewItemRecord.__table__,
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
                        "Value scope (\\S+)\n"
                        "Value scope_id (\\S+)\n"
                        "Value cpu_usage (\\d+)\n\n"
                        "Start\n"
                        " ^CPU usage ${scope} ${scope_id}: "
                        "${cpu_usage}% -> Record\n"
                    ),
                    sha256="a" * 64,
                    status="active",
                    validation_json={
                        "passed": True,
                        "unique_sample_count": 3,
                        "sample_manifest_sha256": "f" * 64,
                        "parser_spec": {
                            "entity": "cpu_scope",
                            "primary_keys": ["scope", "scope_id"],
                            "fields": {
                                "scope": {"required": True, "type": "text"},
                                "scope_id": {"required": True, "type": "text"},
                                "cpu_usage": {
                                    "required": True,
                                    "type": "number",
                                },
                            },
                            "allow_empty": False,
                            "empty_patterns": [],
                        },
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
                    severity="warning",
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
                EventRuleRecord(
                    id="rule-cpu-critical",
                    name="system.cpu_usage.critical",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
                EventRuleVersionRecord(
                    id="rule-cpu-critical-v1",
                    rule_id="rule-cpu-critical",
                    version=1,
                    event_type="CpuUtilizationCritical",
                    severity="critical",
                    condition_json={
                        "kind": "threshold",
                        "threshold_role": "critical_high",
                        "observation_types": ["device.cpu.utilization"],
                        "entity_types": ["cpu_scope"],
                        "fields": ["cpu_usage"],
                        "gte": 90,
                    },
                    priority=110,
                    exclusive=True,
                    event_class="fault",
                    evaluation_scope="both",
                    recovery_json={},
                    correlation_key_json=[
                        "device_id", "entity_type", "entity_key"
                    ],
                    status="active",
                    validation_report_json={"passed": True},
                    digest="f" * 64,
                    created_by="test",
                    created_at=now,
                    activated_at=now,
                ),
                EventRuleRecord(
                    id="rule-cpu-recovered",
                    name="system.cpu_usage.recovered",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
                EventRuleVersionRecord(
                    id="rule-cpu-recovered-v1",
                    rule_id="rule-cpu-recovered",
                    version=1,
                    event_type="CpuUtilizationRecovered",
                    severity="info",
                    condition_json={
                        "kind": "threshold",
                        "observation_types": [
                            "device.cpu.utilization"
                        ],
                        "entity_types": ["cpu_scope"],
                        "fields": ["cpu_usage"],
                        "lte": 75,
                    },
                    priority=90,
                    exclusive=True,
                    event_class="recovery",
                    evaluation_scope="change",
                    recovery_json={
                        "resolves": [
                            "CpuUtilizationHigh",
                            "CpuUtilizationCritical",
                        ]
                    },
                    correlation_key_json=[
                        "device_id",
                        "entity_type",
                        "entity_key",
                    ],
                    status="active",
                    validation_report_json={"passed": True},
                    digest="e" * 64,
                    created_by="test",
                    created_at=now,
                    activated_at=now,
                ),
            ]
        )
    return check_item, implementation


def _create_cpu_bundle(tmp_path, *, direct_projection: bool = True):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    if direct_projection:
        with _session_scope(session_factory) as db:
            implementation_version = db.get(
                CollectionImplementationVersionRecord,
                implementation.version_id,
            )
            assert implementation_version is not None
            implementation_version.adapter_spec_json = {
                "schema_version": "command-adapter/v2",
                "parser": {"template_id": "parser-cpu-v1"},
                "outputs": [
                    {
                        "check_item_id": "system.cpu_usage",
                        "check_item_version_id": check_item.version_id,
                        "source_record_set": "default",
                        "projection": {
                            "fields": {
                                "scope": {"source": "scope"},
                                "scope_id": {"source": "scope_id"},
                                "cpu_usage_percent": {
                                    "source": "cpu_usage",
                                    "dimensions": {"window": "instant"},
                                },
                            }
                        },
                    }
                ],
            }
            implementation_version.digest = _stable_digest(
                {
                    "previous": implementation_version.digest,
                    "adapter_spec": implementation_version.adapter_spec_json,
                }
            )
    service = CheckItemRuntimeBundleService(scope)
    bundle = service.create_version(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        parser_template_ids=["parser-cpu-v1"],
        change_policy_version_ids=["policy-cpu-v1"],
        event_rule_bindings={
            "rule-cpu-high-v1": "device.cpu.utilization",
            "rule-cpu-critical-v1": "device.cpu.utilization",
            "rule-cpu-recovered-v1": "device.cpu.utilization",
        },
        actor="test",
    )
    bundle = service.activate(
        bundle["version_id"],
        actor="bundle-approver",
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


def test_v2_runtime_bundle_freezes_projection_without_observation_contract(tmp_path):
    (
        _session_factory,
        _scope,
        _catalog_repository,
        _check_item,
        _implementation,
        _service,
        bundle,
    ) = _create_cpu_bundle(tmp_path, direct_projection=True)

    asset_types = {item["asset_type"] for item in bundle["assets"]}
    assert "command_adapter_projection" in asset_types
    assert "observation_contract" not in asset_types
    assert bundle["asset_requirement_profile"]["requirements"][
        "command_adapter_projection"
    ]["required"] is True
    assert bundle["asset_requirement_profile"]["requirements"][
        "observation_contract"
    ]["required"] is False
    assert bundle["compatibility"]["command_adapter_projection"][
        "check_item_version_id"
    ]


def _target_policy_snapshot(check_item, device_id: str) -> dict:
    source = {
        "policy_id": f"{device_id}-cpu-thresholds",
        "version": "1",
        "status": "approved",
        "approved_by": "test",
        "approved_at": "2026-01-01T00:00:00+00:00",
        "check_item_id": check_item.id,
        "observation_type": "device.cpu.utilization",
        "scope": {"device_ids": [device_id]},
        "thresholds": {
            "high": {"value": 80, "unit": "percent", "hysteresis": 5},
            "critical_high": {"value": 90, "unit": "percent", "hysteresis": 5},
        },
        "evidence_refs": [f"profile:{device_id}:cpu"],
    }
    source["digest"] = assessment_policy_source_digest(source)
    return build_target_assessment_policy_snapshot(
        check_item_id=check_item.id,
        check_item_version_id=check_item.version_id,
        canonical_contract=check_item.canonical_contract,
        device={
            "id": device_id,
            "attributes": {"assessment_policy_bindings": [source]},
        },
    )


def _execution_policy_fields(check_item, device_id: str) -> dict:
    snapshot = _target_policy_snapshot(check_item, device_id)
    return {
        "assessment_policy_snapshot_json": snapshot,
        "assessment_policy_snapshot_digest": snapshot["digest"],
    }


def _resolved_assessment_basis(
    *,
    check_item,
    device_id: str,
    runtime_bundle_version_id: str,
    runtime_bundle_digest: str,
    observation_id: str,
) -> dict:
    snapshot = _target_policy_snapshot(check_item, device_id)
    entry = {
        "schema_version": "check-assessment-basis/v1",
        "observation_id": observation_id,
        "observation_type": "device.cpu.utilization",
        "entity_identity": {
            "device_id": device_id,
            "entity_type": "cpu_scope",
            "entity_key": '["device","0"]',
            "dimensions": {},
        },
        "rule_binding": {"rule_version_id": "rule-cpu-high-v1"},
        "runtime_bundle_version_id": runtime_bundle_version_id,
        "runtime_bundle_digest": runtime_bundle_digest,
        "policy_snapshot_digest": snapshot["digest"],
        "threshold_binding": {
            "status": "resolved",
            "source_kind": "catalog_default",
            "value": 80.0,
            "unit": "percent",
        },
        "expected_state_binding": {"status": "not_required"},
        "policy_resolution": {"status": "resolved"},
        "evidence_refs": [observation_id, runtime_bundle_version_id],
    }
    entry["digest"] = _stable_digest(entry)
    return build_assessment_basis(
        entries=(entry,),
        policy_snapshot_digest=snapshot["digest"],
        runtime_bundle_version_id=runtime_bundle_version_id,
        runtime_bundle_digest=runtime_bundle_digest,
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
        change_policy_version_ids=[],
        event_rule_bindings={},
        comparison_profile_version_id=profile.version_id,
        actor="test",
    )
    bundle = service.activate(
        bundle["version_id"],
        actor="bundle-approver",
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


def _complete_cpu_production_fact(
    *,
    session_factory,
    scope,
    catalog_repository,
    check_item,
    implementation,
    bundle,
    patrol_batch_id: str,
    device_id: str,
    observed_at: datetime,
    snapshot_id: str,
    run_id: str,
):
    devices = (
        {
            "device_id": device_id,
            "device_name": "H3C baseline device",
            "management_ip": "192.0.2.40",
            "vendor": "H3C",
            "family": "Comware7",
            "model": "S5130",
        },
    )
    target = build_target_manifest(
        devices=devices,
        selector={"device_ids": [device_id]},
    )
    inventory = build_inventory_snapshot(devices)
    with _session_scope(session_factory) as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id=patrol_batch_id,
                workflow_name="patrol-change-event",
                launch_mode="direct",
                patrol_mode="production",
                report_purpose="production_current",
                target_type="devices",
                target_label="baseline selection test",
                selector_json={"device_ids": [device_id]},
                target_count=1,
                launched_count=1,
                skipped_count=0,
                target_manifest_digest=target["digest"],
                inventory_snapshot_digest=inventory["digest"],
                dispatch_status="completed",
                report_status="not_requested",
                archived=False,
                created_at=observed_at,
                updated_at=observed_at,
            )
        )
    plan = CheckItemEffectivePlanService(
        scope,
        catalog=catalog_repository.load_active(),
    ).freeze(
        EffectivePlanRequest(
            patrol_batch_id=patrol_batch_id,
            devices=devices,
            check_item_ids=("system.cpu_usage",),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=observed_at,
            actor="baseline-test",
            report_purpose="production_current",
        )
    )
    action = plan.collection_actions[0]
    execution = plan.executions[0]
    with _session_scope(session_factory) as db:
        db.add(
            CommandSnapshot(
                id=snapshot_id,
                run_id=run_id,
                device_id=device_id,
                execution_item_id=str(action["id"]),
                collection_action_id=str(action["id"]),
                implementation_version_id=implementation.version_id,
                check_item_ids_json=["system.cpu_usage"],
                device_name="H3C baseline device",
                management_ip="192.0.2.40",
                vendor="H3C",
                model="S5130",
                command=str(action["command"]),
                canonical_command=str(action["canonical_command"]),
                parser_template_id="parser-cpu-v1",
                parser_version="1",
                contract_version=1,
                observed_at=observed_at,
                structured_json=[{"cpu_usage": 35}],
                raw_text="CPU usage: 35%",
                raw_text_hash=hashlib.sha256(
                    b"CPU usage: 35%"
                ).hexdigest(),
                parse_status="parsed",
                output_status="success",
            )
        )
        db.add(
            StateObservationRecord(
                id=f"observation-{patrol_batch_id}",
                idempotency_key=hashlib.sha256(
                    f"observation-{patrol_batch_id}".encode()
                ).hexdigest(),
                run_id=run_id,
                snapshot_id=snapshot_id,
                mode="production",
                source="cli",
                source_type="cli",
                device_id=device_id,
                vendor="H3C",
                model="S5130",
                canonical_command=str(action["canonical_command"]),
                execution_item_id=str(action["id"]),
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
            )
        )
    finalized = CheckItemExecutionService(scope).finalize_structured(
        StructuredExecutionResultInput(
            execution_id=str(execution["id"]),
            observations=(
                {
                    "id": f"observation-{patrol_batch_id}",
                    "device_id": device_id,
                    "run_id": run_id,
                    "entity_type": "cpu_scope",
                    "entity_key": '["device","0"]',
                    "field": "cpu_usage",
                    "observation_type": "device.cpu.utilization",
                    "value": 35,
                    "unit": "percent",
                    "dimensions": {"window": "instant"},
                    "context": {
                        "cpu_high_warning_percent": 80,
                        "cpu_high_alarm_percent": 90,
                        "threshold_reference_sources": {
                            "high": {"field_name": "cpu_high_warning_percent"},
                            "critical_high": {"field_name": "cpu_high_alarm_percent"},
                        },
                    },
                    "command": str(action["canonical_command"]),
                    "snapshot_id": snapshot_id,
                    "observed_at": observed_at,
                    "parser_template_id": "parser-cpu-v1",
                    "observation_contract_id": "contract-cpu-v1",
                },
            ),
            snapshot_facts=(
                {
                    "id": snapshot_id,
                    "device_id": device_id,
                    "inventory_device_id": device_id,
                    "output_status": "data",
                    "observed_at": observed_at,
                },
            ),
            evidence_refs=(snapshot_id,),
            assessed_at=observed_at,
        )
    )
    assert finalized["assessment"]["assessment_status"] == "healthy"
    assert finalized["assessment"]["completeness_status"] == "complete"
    return devices, target, inventory, plan, finalized


def test_production_plan_automatically_selects_last_qualified_fact(tmp_path):
    (
        session_factory,
        scope,
        catalog_repository,
        check_item,
        implementation,
        _bundle_service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    previous_at = datetime(2026, 7, 27, 10, tzinfo=timezone.utc)
    devices, target, inventory, _prior_plan, _finalized = (
        _complete_cpu_production_fact(
            session_factory=session_factory,
            scope=scope,
            catalog_repository=catalog_repository,
            check_item=check_item,
            implementation=implementation,
            bundle=bundle,
            patrol_batch_id="batch-baseline-prior",
            device_id="device-baseline-1",
            observed_at=previous_at,
            snapshot_id="snapshot-baseline-prior",
            run_id="run-baseline-prior",
        )
    )
    service = CheckItemEffectivePlanService(
        scope,
        catalog=catalog_repository.load_active(),
    )
    current = service.preview(
        EffectivePlanRequest(
            patrol_batch_id="batch-baseline-current",
            devices=devices,
            check_item_ids=("system.cpu_usage",),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=previous_at + timedelta(days=1),
            actor="baseline-test",
            report_purpose="production_current",
        )
    )
    context = current.comparison_contexts[0]
    assert context.comparison_status == "comparable", context.metadata
    assert context.baseline_run_id == "run-baseline-prior"
    assert context.baseline_snapshot_ids == ("snapshot-baseline-prior",)
    assert context.selection_reason == "latest_compatible_success"
    selected = context.metadata["selected_baseline"]
    assert selected["patrol_batch_id"] == "batch-baseline-prior"
    assert selected["runtime_bundle_digest"] == bundle["digest"]
    assert selected["rejection_codes"] == []
    assert context.metadata["current_fact_contract"]["digest"] == (
        selected["fact_contract"]["digest"]
    )

    with _session_scope(session_factory) as db:
        batch = db.get(PatrolBatchRecord, "batch-baseline-prior")
        batch.report_purpose = "shadow_validation"
    wrong_purpose = service.preview(
        EffectivePlanRequest(
            patrol_batch_id="batch-baseline-after-shadow",
            devices=devices,
            check_item_ids=("system.cpu_usage",),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=previous_at + timedelta(days=2),
            actor="baseline-test",
            report_purpose="production_current",
        )
    ).comparison_contexts[0]
    assert wrong_purpose.comparison_status == "baseline_missing"
    assert wrong_purpose.metadata["candidate_rejection_counts"][
        "acquisition_purpose_ineligible"
    ] == 1

    with _session_scope(session_factory) as db:
        batch = db.get(PatrolBatchRecord, "batch-baseline-prior")
        batch.report_purpose = "production_current"
        observation = db.query(StateObservationRecord).one()
        observation.observation_type = "unexpected.observation"
    wrong_observation = service.preview(
        EffectivePlanRequest(
            patrol_batch_id="batch-baseline-after-observation-drift",
            devices=devices,
            check_item_ids=("system.cpu_usage",),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=previous_at + timedelta(days=2),
            actor="baseline-test",
            report_purpose="production_current",
        )
    ).comparison_contexts[0]
    assert wrong_observation.comparison_status == "baseline_missing"
    assert wrong_observation.metadata["candidate_rejection_counts"][
        "state_observation_contract_mismatch"
    ] == 1

    with _session_scope(session_factory) as db:
        observation = db.query(StateObservationRecord).one()
        observation.observation_type = "device.cpu.utilization"
        manifest = db.query(CheckItemRuntimeManifestRecord).one()
        original_manifest = dict(manifest.manifest_json)
        manifest.manifest_json = {
            **original_manifest,
            "patrol_batch_id": "tampered-batch",
        }
    wrong_manifest = service.preview(
        EffectivePlanRequest(
            patrol_batch_id="batch-baseline-after-manifest-drift",
            devices=devices,
            check_item_ids=("system.cpu_usage",),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=previous_at + timedelta(days=2),
            actor="baseline-test",
            report_purpose="production_current",
        )
    ).comparison_contexts[0]
    assert wrong_manifest.comparison_status == "baseline_missing"
    assert wrong_manifest.metadata["candidate_rejection_counts"][
        "runtime_manifest_integrity_failed"
    ] == 1

    with _session_scope(session_factory) as db:
        manifest = db.query(CheckItemRuntimeManifestRecord).one()
        manifest.manifest_json = original_manifest
        qualification = db.query(EvidenceQualificationRecord).one()
        qualification.digest = "0" * 64
    rejected = service.preview(
        EffectivePlanRequest(
            patrol_batch_id="batch-baseline-after-tamper",
            devices=devices,
            check_item_ids=("system.cpu_usage",),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=previous_at + timedelta(days=2),
            actor="baseline-test",
            report_purpose="production_current",
        )
    ).comparison_contexts[0]
    assert rejected.comparison_status == "baseline_missing"
    assert rejected.selection_reason == "prior_facts_failed_qualification"
    assert rejected.metadata["candidate_rejection_counts"][
        "evidence_qualification_integrity_failed"
    ] == 1


def test_comparison_rejects_equally_recent_compatible_baselines():
    current = datetime(2026, 7, 28, 10, tzinfo=timezone.utc)
    candidates = [
        BaselineCandidate(
            run_id=run_id,
            observed_at=current - timedelta(hours=1),
            snapshot_ids=(f"snapshot-{run_id}",),
            canonical_schema_version="canonical-v1",
            entity_identity_policy_version="identity-v1",
            metadata={"execution_id": f"execution-{run_id}"},
        )
        for run_id in ("run-a", "run-b")
    ]
    decision = ComparisonContextService().select_online(
        device_id="device-ambiguous",
        check_item_version_id="check-v1",
        current_observed_at=current,
        candidates=candidates,
        max_baseline_age_seconds=86400,
        canonical_schema_version="canonical-v1",
        entity_identity_policy_version="identity-v1",
    )
    assert decision.comparison_status == "baseline_missing"
    assert decision.selection_reason == (
        "multiple_equally_recent_compatible_baselines"
    )
    assert len(decision.metadata["ambiguous_candidates"]) == 2


def test_comparison_requires_the_same_canonical_fact_contract():
    current = datetime(2026, 7, 28, 10, tzinfo=timezone.utc)
    common = {
        "check_item_version_id": "check-v1",
        "result_mode": "structured",
        "assessment_mode": "health",
        "implementation_version_id": "implementation-v1",
        "canonical_catalog_version": 3,
        "canonical_catalog_digest": "c" * 64,
        "entity_identity_policy_version_id": "identity-v1",
        "dependency_digests": {
            "identity_policy": "i" * 64,
            "observation_contracts": {"contract-v1": "o" * 64},
        },
        "comparison_profile_version_id": None,
    }
    baseline_contract = build_comparison_fact_contract(
        check_item_digest="a" * 64,
        **common,
    )
    current_contract = build_comparison_fact_contract(
        check_item_digest="b" * 64,
        **common,
    )
    decision = ComparisonContextService().select_online(
        device_id="device-contract",
        check_item_version_id="check-v1",
        current_observed_at=current,
        candidates=(
            BaselineCandidate(
                run_id="run-contract-old",
                observed_at=current - timedelta(hours=1),
                canonical_schema_version="canonical-v1",
                entity_identity_policy_version="identity-v1",
                metadata={"fact_contract": baseline_contract},
            ),
        ),
        max_baseline_age_seconds=86400,
        canonical_schema_version="canonical-v1",
        entity_identity_policy_version="identity-v1",
        current_fact_contract=current_contract,
    )
    assert decision.comparison_status == "schema_incompatible"
    assert decision.selection_reason == "no_compatible_fact_contract"


def test_baseline_candidate_lookup_is_bulk_for_large_target_sets(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item = catalog_repository.load_active().check_item("system.cpu_usage")
    assert check_item is not None
    engine = session_factory.kw["bind"]
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(str(statement))

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        result = ProductionBaselineCandidateRepository(scope).load_many(
            targets=(
                (f"device-{index}", check_item.version_id)
                for index in range(200)
            ),
            current_observed_at=datetime(
                2026, 7, 28, 10, tzinfo=timezone.utc
            ),
            current_patrol_batch_id="batch-current",
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    assert result == {}
    selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(selects) == 1


def test_terminal_device_failures_settle_two_thousand_executions_with_fixed_selects(
    tmp_path,
):
    session_factory, scope, _catalog_repository = _runtime(tmp_path)
    with _session_scope(session_factory) as db:
        check_item_version_ids = [
            str(row[0])
            for row in db.query(CheckItemVersionRecord.id)
            .filter(CheckItemVersionRecord.status == "active")
            .order_by(CheckItemVersionRecord.id)
            .limit(2)
            .all()
        ]
    assert len(check_item_version_ids) == 2

    patrol_batch_id = "batch-terminal-failure-scale"
    now = datetime.now(timezone.utc)
    devices = [f"device-{index:04d}" for index in range(1000)]
    with _session_scope(session_factory) as db:
        db.bulk_save_objects(
            [
                CheckExecutionRecord(
                    id=f"execution-{device_id}-{check_index}",
                    idempotency_key=f"execution-{device_id}-{check_index}",
                    patrol_batch_id=patrol_batch_id,
                    device_id=device_id,
                    check_item_version_id=check_item_version_id,
                    collection_action_ids_json=[],
                    execution_status="planned",
                    reason_codes_json=[],
                    target_manifest_digest="a" * 64,
                    evidence_refs_json=[],
                    created_at=now,
                    updated_at=now,
                )
                for device_id in devices
                for check_index, check_item_version_id in enumerate(
                    check_item_version_ids
                )
            ]
        )

    engine = session_factory.kw["bind"]
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(str(statement))

    failures = [
        {
            "device_id": device_id,
            "status": "cancelled",
            "reason_code": "patrol_batch_cancelled",
            "evidence_refs": (),
        }
        for device_id in devices
    ]
    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        result = CheckItemRunFinalizer(session_scope=scope).fail_devices(
            patrol_batch_id=patrol_batch_id,
            failures=failures,
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(selects) <= 3
    assert result["failed_execution_count"] == 2000
    with _session_scope(session_factory) as db:
        assert (
            db.query(CheckAssessmentRecord)
            .filter(CheckAssessmentRecord.patrol_batch_id == patrol_batch_id)
            .count()
            == 2000
        )
        assert (
            db.query(CheckAssessmentRecord)
            .filter(
                CheckAssessmentRecord.patrol_batch_id == patrol_batch_id,
                CheckAssessmentRecord.assessment_status
                == "unable_to_determine",
            )
            .count()
            == 2000
        )

    repeated = CheckItemRunFinalizer(session_scope=scope).fail_devices(
        patrol_batch_id=patrol_batch_id,
        failures=failures,
    )
    assert repeated["failed_execution_count"] == 0
    with _session_scope(session_factory) as db:
        assert (
            db.query(CheckAssessmentRecord)
            .filter(CheckAssessmentRecord.patrol_batch_id == patrol_batch_id)
            .count()
            == 2000
        )


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


def test_comparison_and_evidence_digests_use_timezone_invariant_utc_values():
    utc_at = datetime(2026, 7, 28, 4, tzinfo=timezone.utc)
    china_at = utc_at.astimezone(timezone(timedelta(hours=8)))

    utc_context = ComparisonContextService().select_online(
        device_id="timezone-device",
        check_item_version_id="timezone-check-v1",
        current_observed_at=utc_at,
        candidates=(),
        max_baseline_age_seconds=3600,
        canonical_schema_version="canonical-v1",
        entity_identity_policy_version="identity-v1",
    )
    china_context = ComparisonContextService().select_online(
        device_id="timezone-device",
        check_item_version_id="timezone-check-v1",
        current_observed_at=china_at,
        candidates=(),
        max_baseline_age_seconds=3600,
        canonical_schema_version="canonical-v1",
        entity_identity_policy_version="identity-v1",
    )
    assert china_context.digest == utc_context.digest
    assert china_context.current_observed_at == utc_at

    common = {
        "execution_id": "timezone-execution",
        "patrol_batch_id": "timezone-batch",
        "device_id": "timezone-device",
        "check_item_version_id": "timezone-check-v1",
        "implementation_version_id": "timezone-implementation-v1",
        "runtime_bundle_version_id": "timezone-bundle-v1",
        "runtime_bundle_digest": "a" * 64,
        "snapshot_ids": ("timezone-snapshot",),
        "source_device_ids": ("timezone-device",),
        "inventory_device_ids": ("timezone-device",),
        "output_statuses": ("complete",),
        "policy_snapshot": {},
    }
    utc_evidence = EvidenceQualificationService.qualify(
        EvidenceQualificationInput(
            **common,
            observed_ats=(utc_at,),
            qualified_at=utc_at,
        )
    )
    china_evidence = EvidenceQualificationService.qualify(
        EvidenceQualificationInput(
            **common,
            observed_ats=(china_at,),
            qualified_at=china_at,
        )
    )
    assert china_evidence.digest == utc_evidence.digest
    assert china_evidence.values["qualified_at"] == utc_at


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


def test_coverage_decision_records_parser_failure_as_not_evaluable():
    decision = CoverageDecisionService.decide(
        CoverageDecisionInput(
            execution_id="execution-parser-failed",
            patrol_batch_id="batch-parser-failed",
            device_id="device-parser-failed",
            check_item_version_id="check-interface-v1",
            observed_entity_keys=(),
            evaluated_entity_count=0,
            policy_snapshot={
                "model": "inventory_entity_set",
            },
            denominator_ref={
                "basis": "inventory_entity_set",
                "inventory_snapshot_digest": "e" * 64,
            },
            evaluable=False,
            evaluation_failure_reason_codes=(
                "parse_failed",
                "template_missing",
            ),
        )
    )

    assert decision.coverage_status == "not_evaluable"
    assert decision.coverage_basis == "inventory_entity_set"
    assert {
        "coverage_not_evaluable",
        "parse_failed",
        "template_missing",
    } <= set(decision.reason_codes)


def test_health_assessment_does_not_hide_unsupported_command_as_not_applicable():
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

    assert decision.assessment_status == "unable_to_determine"
    assert decision.completeness_status == "insufficient"
    assert "output_quality_unsupported" in decision.blocking_reason_codes


def test_evidence_qualification_requires_frozen_applicability_empty_evidence():
    assessed_at = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
    pattern = r"(?im)^\s*no\s+vpc\s+configured\s*$"
    common = {
        "execution_id": "execution-empty-policy",
        "patrol_batch_id": "batch-empty-policy",
        "device_id": "device-empty-policy",
        "check_item_version_id": "check-v1",
        "implementation_version_id": "implementation-v1",
        "runtime_bundle_version_id": "bundle-v1",
        "runtime_bundle_digest": "a" * 64,
        "snapshot_ids": ("snapshot-empty-policy",),
        "source_device_ids": ("device-empty-policy",),
        "inventory_device_ids": ("device-empty-policy",),
        "output_statuses": ("valid_empty",),
        "observed_ats": (assessed_at,),
        "qualified_at": assessed_at,
    }
    policy = {
        "freshness_policy": {
            "max_observation_age_seconds": 3600,
            "max_collection_span_seconds": 300,
        },
        "empty_result_policy": {"classification": "not_applicable"},
        "applicability_decision": {
            "status": "applicable",
            "selected_policy": {
                "policy_version_id": "applicability-v1",
                "empty_result_classification": "expected_empty",
                "legal_empty_patterns": [pattern],
            },
        },
    }

    accepted = EvidenceQualificationService.qualify(
        EvidenceQualificationInput(
            **common,
            output_reasons=(pattern,),
            policy_snapshot=policy,
        )
    )
    assert accepted.empty_result_classification == "expected_empty"
    assert "governed_legal_empty_evidence_matched" in accepted.reason_codes

    rejected = EvidenceQualificationService.qualify(
        EvidenceQualificationInput(
            **{**common, "execution_id": "execution-empty-policy-rejected"},
            output_reasons=(r"(?im)^\s*no\s+entries\s*$",),
            policy_snapshot=policy,
        )
    )
    assert rejected.empty_result_classification == "invalid"
    assert (
        "empty_output_not_authorized_by_frozen_applicability"
        in rejected.reason_codes
    )


def test_evidence_qualification_never_turns_unsupported_command_into_not_applicable():
    assessed_at = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
    decision = EvidenceQualificationService.qualify(
        EvidenceQualificationInput(
            execution_id="execution-unsupported-policy",
            patrol_batch_id="batch-unsupported-policy",
            device_id="device-unsupported-policy",
            check_item_version_id="check-v1",
            implementation_version_id="implementation-v1",
            runtime_bundle_version_id="bundle-v1",
            runtime_bundle_digest="a" * 64,
            snapshot_ids=("snapshot-unsupported-policy",),
            source_device_ids=("device-unsupported-policy",),
            inventory_device_ids=("device-unsupported-policy",),
            output_statuses=("unsupported_command",),
            observed_ats=(assessed_at,),
            output_reasons=(r"%\s*invalid input",),
            policy_snapshot={
                "freshness_policy": {
                    "max_observation_age_seconds": 3600,
                    "max_collection_span_seconds": 300,
                },
                "empty_result_policy": {"classification": "not_applicable"},
                "applicability_decision": {
                    "status": "not_applicable",
                    "selected_policy": {
                        "policy_version_id": "applicability-v1",
                        "empty_result_classification": "not_applicable",
                        "legal_empty_patterns": [r"%\s*invalid input"],
                    },
                },
            },
            qualified_at=assessed_at,
        )
    )
    assert decision.empty_result_classification == "unsupported_unapproved"
    assert (
        "unsupported_command_cannot_be_not_applicable" in decision.reason_codes
    )


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
    assert preview["fallback_resilience"] == "not_applicable"
    assert preview["selected"]["parser_template_ids"] == ["parser-cpu-v1"]
    assert preview["selected"]["event_rule_bindings"] == {
        "rule-cpu-critical-v1": ["device.cpu.utilization"],
        "rule-cpu-high-v1": ["device.cpu.utilization"],
        "rule-cpu-recovered-v1": ["device.cpu.utilization"],
    }
    assert preview["asset_requirement_profile"][
        "required_asset_types"
    ] == [
        "parser_template",
        "observation_contract",
        "current_state_rule",
        "change_policy",
        "event_rule",
        "identity_policy",
    ]
    assert preview["asset_requirement_profile"]["requirements"][
        "event_rule"
    ]["required_event_types_by_observation"] == {
        "device.cpu.utilization": [
            "CpuUtilizationCritical",
            "CpuUtilizationHigh",
            "CpuUtilizationRecovered",
        ]
    }
    assert preview["asset_requirement_profile"]["requirements"][
        "event_rule"
    ]["required_event_severities"] == {
        "CpuUtilizationCritical": "critical",
        "CpuUtilizationHigh": "warning",
        "CpuUtilizationRecovered": "info",
    }
    assert preview["asset_requirement_profile"]["requirements"][
        "event_rule"
    ]["required_recovery_pairs_by_observation"] == {
        "device.cpu.utilization": [
            {
                "fault": "CpuUtilizationCritical",
                "recovery": "CpuUtilizationRecovered",
            },
            {
                "fault": "CpuUtilizationHigh",
                "recovery": "CpuUtilizationRecovered",
            }
        ]
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
    assert draft["compatibility"]["fallback_resilience"] == "not_applicable"

    with pytest.raises(
        RuntimeBundleValidationError,
        match="approver different from the version creator",
    ):
        service.activate(
            draft["version_id"],
            actor="api:engineer",
            reason="creator cannot self-approve",
        )
    active = service.activate(
        draft["version_id"],
        actor="api:engineer",
        reason="",
        allow_same_actor=True,
    )
    assert active["version_status"] == "active"
    with _session_scope(session_factory) as db:
        audit_rows = (
            db.query(AuditLogRecord)
            .filter(
                AuditLogRecord.resource_type
                == "check_item_runtime_bundle_version"
            )
            .all()
        )
        actions = {row.action for row in audit_rows}
        activation_audit = next(
            row for row in audit_rows if row.action == "runtime_bundle_activated"
        )
        assert activation_audit.detail["separation_of_duties_override"] is True
        assert activation_audit.detail["reason"] == ""
    assert actions == {
        "runtime_bundle_draft_created",
        "runtime_bundle_activated",
    }


@pytest.mark.parametrize(
    ("rule_version_id", "attribute", "value", "expected_code"),
    [
        (
            "rule-cpu-high-v1",
            "event_type",
            "CpuUtilizationUnexpected",
            "event_rule_type_outside_asset_profile",
        ),
        (
            "rule-cpu-high-v1",
            "severity",
            "major",
            "event_rule_severity_mismatch",
        ),
        (
            "rule-cpu-recovered-v1",
            "event_class",
            "fault",
            "event_rule_class_mismatch",
        ),
        (
            "rule-cpu-recovered-v1",
            "recovery_json",
            {"resolves": ["UnrelatedFault"]},
            "event_rule_recovery_pair_mismatch",
        ),
    ],
)
def test_runtime_bundle_rejects_event_rules_outside_catalog_contract(
    tmp_path,
    rule_version_id,
    attribute,
    value,
    expected_code,
):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")
    with _session_scope(session_factory) as db:
        db.query(CollectionImplementationParserBindingRecord).delete()
        version = db.get(EventRuleVersionRecord, rule_version_id)
        setattr(version, attribute, value)

    preview = CheckItemRuntimeBundleService(scope).preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )

    assert preview["operationally_ready"] is False
    candidate = next(
        item
        for item in preview["candidates"]["event_rules"]
        if item["event_rule_version_id"] == rule_version_id
    )
    assert expected_code in {
        item["code"] for item in candidate["blockers"]
    }


def test_runtime_bundle_does_not_block_on_missing_optional_observation(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")
    with _session_scope(session_factory) as db:
        version = db.get(CheckItemVersionRecord, check_item.version_id)
        canonical = dict(version.canonical_contract_json or {})
        canonical["observations"] = [
            *list(canonical.get("observations") or []),
            {
                "observation_type": "interface.admin_status",
                "semantic_type": "state",
                "required": False,
            },
        ]
        version.canonical_contract_json = canonical

    preview = CheckItemRuntimeBundleService(scope).preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )

    assert preview["operationally_ready"] is True
    assert "interface.admin_status" in preview["compatibility"][
        "expected_observation_types"
    ]
    assert "interface.admin_status" not in preview["compatibility"][
        "required_observation_types"
    ]
    assert "interface.admin_status" not in preview["compatibility"][
        "produced_observation_types"
    ]
    created = CheckItemRuntimeBundleService(scope).create_version(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        parser_template_ids=["parser-cpu-v1"],
        change_policy_version_ids=["policy-cpu-v1"],
        event_rule_bindings={
            "rule-cpu-high-v1": "device.cpu.utilization",
            "rule-cpu-critical-v1": "device.cpu.utilization",
            "rule-cpu-recovered-v1": "device.cpu.utilization",
        },
        actor="test",
    )
    assert created["version_status"] == "draft"


def test_runtime_bundle_rejects_parser_spec_and_textfsm_field_drift(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")
    with _session_scope(session_factory) as db:
        parser = db.get(ParserTemplate, "parser-cpu-v1")
        validation = dict(parser.validation_json or {})
        parser_spec = dict(validation.get("parser_spec") or {})
        parser_spec["fields"] = {
            **dict(parser_spec.get("fields") or {}),
            "phantom_metric": {"required": False, "type": "number"},
        }
        validation["parser_spec"] = parser_spec
        parser.validation_json = validation

    preview = CheckItemRuntimeBundleService(scope).preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )

    assert preview["operationally_ready"] is False
    assert "parser_field_contract_drift" in {
        item["code"] for item in preview["blockers"]
    }
    candidate = next(
        item
        for item in preview["candidates"]["parsers"]
        if item["parser_template_id"] == "parser-cpu-v1"
    )
    assert candidate["field_contract"]["status"] == "schema_drift"
    assert candidate["field_contract"]["differences"][0][
        "missing_in_template"
    ] == ["phantom_metric"]
    blocker = next(
        item
        for item in preview["blockers"]
        if item["code"] == "parser_field_contract_drift"
    )
    assert blocker["remediation_action"]["code"] == "repair_parser_asset"
    assert blocker["remediation_action"]["asset_type"] == "parser_template"




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
    assert all(item.get("remediation") for item in preview["blockers"])
    parser_blocker = next(
        item
        for item in preview["blockers"]
        if item["code"] == "implementation_scope_broader_than_parser"
    )
    assert parser_blocker["remediation_action"]["code"] == "repair_parser_asset"
    assert parser_blocker["remediation_action"]["asset_type"] == "parser_template"
    assert "workspace=production" in parser_blocker["remediation_action"]["target_url"]
    assert "command=system.cpu_usage" in parser_blocker["remediation_action"]["target_url"]


def test_runtime_bundle_accepts_exact_model_pattern_and_plural_vendor_selector(tmp_path):
    session_factory, scope, catalog_repository = _runtime(tmp_path)
    check_item, implementation = _seed_cpu_assets(
        session_factory,
        catalog_repository,
    )
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")
    with _session_scope(session_factory) as db:
        implementation_row = db.get(
            CollectionImplementationVersionRecord,
            implementation.version_id,
        )
        implementation_row.match_json = {
            "vendors": ["H3C"],
            "families": ["Comware7"],
            # Catalog-managed exact model selectors are emitted with escaped
            # punctuation. They still describe one model, not a broad regex.
            "model_patterns": [r"^S5130S\-54S\-HI\-G$"],
        }
        implementation_row.template_scope = "model"
        parser = db.get(ParserTemplate, "parser-cpu-v1")
        parser.model = "S5130S-54S-HI-G"
        validation = dict(parser.validation_json or {})
        compatibility = dict(validation["compatibility"])
        compatibility.update(
            {
                "scope": "model",
                "model": "S5130S-54S-HI-G",
                "family": "Comware7",
            }
        )
        validation["compatibility"] = compatibility
        parser.validation_json = validation

    preview = CheckItemRuntimeBundleService(scope).preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )

    blocker_codes = {item["code"] for item in preview["blockers"]}
    assert "implementation_vendor_missing" not in blocker_codes
    assert "parser_vendor_mismatch" not in blocker_codes
    assert "implementation_scope_broader_than_parser" not in blocker_codes




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
    comparison_profile = next(
        item
        for item in profiles
        if item.id == "configuration.ordered_lines"
    )
    service = CheckItemRuntimeBundleService(scope)
    EntityIdentityPolicyRepository(scope).bootstrap(actor="test")

    missing_preview = service.preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )
    missing_profile = next(
        item
        for item in missing_preview["blockers"]
        if item["code"] == "comparison_profile_missing"
    )
    assert missing_profile["remediation_action"]["code"] == "import_rule_samples"
    assert (
        "subtab=source-jobs"
        in missing_profile["remediation_action"]["target_url"]
    )
    assert (
        "check_item_id=configuration.running"
        in missing_profile["remediation_action"]["target_url"]
    )

    TextComparisonProfileRepository(scope).bootstrap(profiles)
    preview = service.preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
    )
    assert preview["blockers"] == []
    assert (
        preview["selected"]["comparison_profile_version_id"]
        == comparison_profile.version_id
    )
    assert preview["command_routes"][0]["status"] == "executable"
    assert preview["command_routes"][0]["execution_mode"] == (
        "normalized_diff"
    )
    assert preview["asset_requirement_profile"][
        "required_asset_types"
    ] == ["comparison_profile", "identity_policy"]
    assert preview["asset_requirement_profile"]["not_required"][
        "change_policy"
    ] == "text_comparison_profile_owns_change_policy"

    bundle = service.create_version(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        parser_template_ids=[],
        change_policy_version_ids=[],
        event_rule_bindings={},
        actor="test",
    )
    bundle = service.activate(
        bundle["version_id"],
        actor="bundle-approver",
        reason="unit test activation",
    )
    assert bundle["comparison_profile_version_id"] == comparison_profile.version_id
    assert [item["asset_type"] for item in bundle["assets"]] == [
        "comparison_profile",
        "identity_policy",
    ]
    assert bundle["parser_bundle"] == {}
    assert bundle["asset_requirement_profile_digest"] == (
        preview["asset_requirement_profile_digest"]
    )

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


def test_recovery_confirmation_revalidates_frozen_runtime_evidence(tmp_path):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        _service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    assessed_at = datetime(2026, 7, 30, 10, tzinfo=timezone.utc)
    risk_id = "risk-cpu-recovery-confirmation"
    execution_id = "execution-cpu-recovery"
    assessment_id = "assessment-cpu-recovery"
    qualification_id = "qualification-cpu-recovery"
    coverage_id = "coverage-cpu-recovery"
    change_id = "change-cpu-recovery"
    event_id = "event-cpu-high"
    occurrence_id = "event-occurrence-cpu-recovery"
    with _session_scope(session_factory) as db:
        db.add(
            NetworkDevice(
                id="device-recovery-1",
                device_name="XA-Core-Recovery-1",
                management_ip="192.0.2.90",
                vendor="H3C",
                model="S5130S",
                status="active",
            )
        )
        db.add(
            CheckExecutionRecord(
                id=execution_id,
                idempotency_key="batch-recovery|device-recovery-1|cpu",
                patrol_batch_id="batch-recovery",
                device_id="device-recovery-1",
                check_item_version_id=check_item.version_id,
                implementation_version_id=implementation.version_id,
                collection_action_ids_json=["action-recovery"],
                runtime_bundle_id=bundle["bundle_id"],
                runtime_bundle_version_id=bundle["version_id"],
                runtime_bundle_digest=bundle["digest"],
                execution_status="completed",
                reason_codes_json=[],
                observation_count=1,
                observed_entity_count=1,
                change_count=1,
                event_count=1,
                current_finding_count=0,
                assessment_id=assessment_id,
                evidence_qualification_id=qualification_id,
                coverage_decision_id=coverage_id,
                source_identity_status="confirmed",
                output_quality_status="complete",
                freshness_status="fresh",
                empty_result_classification="non_empty",
                coverage_status="complete",
                coverage_basis="fixed",
                risk_verification_status="recovery_pending",
                expected_entity_count=1,
                target_manifest_digest="1" * 64,
                asset_manifest_digest=bundle["digest"],
                evidence_refs_json=["snapshot:cpu-recovery"],
                completed_at=assessed_at,
                created_at=assessed_at,
                updated_at=assessed_at,
            )
        )
        db.add(
            EvidenceQualificationRecord(
                id=qualification_id,
                digest="2" * 64,
                check_execution_id=execution_id,
                patrol_batch_id="batch-recovery",
                device_id="device-recovery-1",
                check_item_version_id=check_item.version_id,
                implementation_version_id=implementation.version_id,
                runtime_bundle_version_id=bundle["version_id"],
                runtime_bundle_digest=bundle["digest"],
                source_identity_status="confirmed",
                output_quality_status="complete",
                freshness_status="fresh",
                empty_result_classification="non_empty",
                snapshot_ids_json=["snapshot:cpu-recovery"],
                qualification_reason_codes_json=[],
                policy_snapshot_json={"schema_version": 1},
                policy_digest="3" * 64,
                evidence_refs_json=["snapshot:cpu-recovery"],
                qualified_at=assessed_at,
                created_at=assessed_at,
            )
        )
        db.add(
            CoverageDecisionRecord(
                id=coverage_id,
                digest="4" * 64,
                check_execution_id=execution_id,
                patrol_batch_id="batch-recovery",
                device_id="device-recovery-1",
                check_item_version_id=check_item.version_id,
                coverage_status="partial",
                coverage_basis="fixed",
                expected_entity_count=1,
                observed_entity_count=1,
                evaluated_entity_count=1,
                expected_entity_set_digest="5" * 64,
                observed_entity_set_digest="5" * 64,
                denominator_ref_json={"basis": "fixed"},
                missing_entity_sample_json=[],
                unexpected_entity_sample_json=[],
                reason_codes_json=[],
                policy_snapshot_json={"model": "fixed"},
                policy_digest="6" * 64,
                evidence_refs_json=["snapshot:cpu-recovery"],
                decided_at=assessed_at,
                created_at=assessed_at,
            )
        )
        db.add(
            CheckAssessmentRecord(
                id=assessment_id,
                idempotency_key="assessment-cpu-recovery-key",
                patrol_batch_id="batch-recovery",
                device_id="device-recovery-1",
                check_item_version_id=check_item.version_id,
                execution_id=execution_id,
                assessment_status="healthy",
                completeness_status="complete",
                comparison_status="comparable",
                expected_entity_count=1,
                observed_entity_count=1,
                evaluated_entity_count=1,
                threshold_unknown_count=0,
                current_finding_ids_json=[],
                risk_case_ids_json=[risk_id],
                evidence_qualification_id=qualification_id,
                coverage_decision_id=coverage_id,
                source_identity_status="confirmed",
                output_quality_status="complete",
                freshness_status="fresh",
                empty_result_classification="non_empty",
                coverage_status="complete",
                coverage_basis="fixed",
                risk_verification_status="recovery_pending",
                blocking_reason_codes_json=[],
                assessment_rule_bundle_digest=bundle["digest"],
                evidence_digest="7" * 64,
                assessed_at=assessed_at,
                created_at=assessed_at,
            )
        )
        db.add(
            NetworkChangeRecord(
                id=change_id,
                idempotency_key="change-cpu-recovery-key",
                run_id="run-recovery",
                check_item_id="system.cpu_usage",
                check_item_version_id=check_item.version_id,
                check_execution_id=execution_id,
                device_id="device-recovery-1",
                canonical_command="display cpu-usage",
                entity_type="cpu_scope",
                entity_key='["device","0"]',
                dimensions_json={"window": "instant"},
                field="cpu_usage",
                observation_type="device.cpu.utilization",
                old_value_json=92,
                new_value_json=60,
                change_type="modified",
                source="patrol",
                event_status="event_created",
                observed_at=assessed_at,
                detected_at=assessed_at,
                processed_at=assessed_at,
            )
        )
        db.add(
            NetworkEventRecord(
                id=event_id,
                idempotency_key="event-cpu-high-key",
                check_item_id="system.cpu_usage",
                check_item_version_id=check_item.version_id,
                check_execution_id="execution-cpu-fault",
                event_type="CpuUtilizationHigh",
                severity="warning",
                device_id="device-recovery-1",
                entity_type="cpu_scope",
                entity_key='["device","0"]',
                correlation_key="cpu|device-recovery-1|0",
                correlation_dimensions_json={"window": "instant"},
                source_change_id="change-cpu-fault",
                run_id="run-fault",
                latest_change_id=change_id,
                last_seen_run_id="run-recovery",
                occurred_at=assessed_at - timedelta(days=1),
                first_seen_at=assessed_at - timedelta(days=1),
                last_seen_at=assessed_at,
                last_seen_order_key="recovery",
                occurrence_count=2,
                status="resolved",
                payload_json={"resolved_at": assessed_at.isoformat()},
                created_at=assessed_at - timedelta(days=1),
            )
        )
        db.add(
            NetworkEventOccurrenceRecord(
                id=occurrence_id,
                idempotency_key="event-occurrence-cpu-recovery-key",
                event_id=event_id,
                run_id="run-recovery",
                source_change_id=change_id,
                observed_at=assessed_at,
                severity="info",
                occurrence_kind="recovery",
                payload_json={
                    "event_type": "CpuUtilizationRecovered",
                    "event_class": "recovery",
                    "device_id": "device-recovery-1",
                    "entity_type": "cpu_scope",
                    "entity_key": '["device","0"]',
                    "projection_applied": True,
                    "rule": {
                        "rule_id": "system.cpu_usage.recovered",
                        "version_id": "rule-cpu-recovered-v1",
                        "version": 1,
                        "event_class": "recovery",
                        "recovery": {
                            "resolves": ["CpuUtilizationHigh"]
                        },
                    },
                },
                created_at=assessed_at,
            )
        )
        db.add(
            RiskCaseRecord(
                id=risk_id,
                correlation_key="risk-cpu-recovery-confirmation-key",
                check_item_id="system.cpu_usage",
                device_id="device-recovery-1",
                entity_type="cpu_scope",
                entity_key='["device","0"]',
                correlation_dimensions_json={"window": "instant"},
                risk_type="CpuUtilizationHigh",
                severity="warning",
                status="recovering",
                first_seen_at=assessed_at - timedelta(days=1),
                last_seen_at=assessed_at - timedelta(days=1),
                last_successful_evaluation_at=assessed_at,
                verification_status="recovery_pending",
                last_verified_at=assessed_at,
                last_verification_execution_id=execution_id,
                source_finding_ids_json=["finding-cpu-fault"],
                source_event_ids_json=[event_id],
                remediation_status="completed",
                created_at=assessed_at - timedelta(days=1),
                updated_at=assessed_at,
            )
        )

    service = RiskCaseService(scope)
    blocked_detail = service.get_case(risk_id)
    assert blocked_detail["recovery_confirmation"]["state"] == "blocked"
    assert "coverage_not_complete" in blocked_detail[
        "recovery_confirmation"
    ]["blocker_codes"]
    blocked_candidate = blocked_detail["recovery_candidates"][0]
    with pytest.raises(ValueError, match="coverage_not_complete"):
        service.confirm_recovery(
            risk_id,
            recovery_candidate_id=blocked_candidate["id"],
            actor="engineer",
            reason="确认恢复",
            idempotency_key="confirm-recovery-0001",
        )

    with _session_scope(session_factory) as db:
        db.get(CoverageDecisionRecord, coverage_id).coverage_status = "complete"

    detail = service.get_case(risk_id)
    assert detail["recovery_confirmation"]["state"] == "ready"
    candidate = next(
        item
        for item in detail["recovery_candidates"]
        if item["kind"] == "recovery_event" and item["eligible"]
    )
    result = service.confirm_recovery(
        risk_id,
        recovery_candidate_id=candidate["id"],
        actor="engineer",
        reason="已复核当前值、覆盖范围与恢复规则，确认技术恢复",
        idempotency_key="confirm-recovery-0002",
    )
    assert result["risk_case"]["status"] == "resolved"
    assert result["risk_case"]["verification_status"] == "resolved"
    assert result["confirmation"]["event_occurrence_id"] == occurrence_id
    assert "event_rule:rule-cpu-recovered-v1" in result[
        "risk_case"
    ]["resolution_evidence_refs_json"]

    replay = service.confirm_recovery(
        risk_id,
        recovery_candidate_id=candidate["id"],
        actor="engineer",
        reason="幂等重试",
        idempotency_key="confirm-recovery-0002",
    )
    assert replay["risk_case"]["status"] == "resolved"
    assert replay["confirmation"]["idempotent_replay"] is True
    with _session_scope(session_factory) as db:
        resolved_occurrences = (
            db.query(RiskOccurrenceRecord)
            .filter(
                RiskOccurrenceRecord.risk_case_id == risk_id,
                RiskOccurrenceRecord.occurrence_kind == "resolved",
            )
            .all()
        )
        assert len(resolved_occurrences) == 1
        assert resolved_occurrences[0].actor == "engineer"


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

    service.resolve_with_evidence(
        risk_id,
        successful_current_evaluation=True,
        observed_at=now + timedelta(hours=3),
        patrol_batch_id="batch-risk-recovery",
        evidence_refs=["assessment:healthy", "coverage:complete"],
        recovery_rule_version_id="rule-cpu-recovery-v1",
        actor="recovery-reviewer",
        reason="技术复检已通过",
    )
    with pytest.raises(ValueError, match="cannot accept their own"):
        service.accept_remediation(
            risk_id,
            remediation["id"],
            actor="network-team",
            reason="自验收不得通过",
            evidence_refs=["acceptance:self-review"],
            idempotency_key="accept-remediation-self-review",
        )
    accepted = service.accept_remediation(
        risk_id,
        remediation["id"],
        actor="independent-reviewer",
        reason="已核对变更单和复测结果",
        evidence_refs=["acceptance:review-1"],
        idempotency_key="accept-remediation-review-1",
    )
    assert accepted["status"] == "accepted"
    assert accepted["completed_by"] == "network-team"
    assert accepted["accepted_by"] == "independent-reviewer"

    ready = service.get_case(risk_id)
    assert ready["status"] == "resolved"
    assert ready["remediation_status"] == "accepted"
    assert ready["closure"]["state"] == "ready_to_close"
    assert ready["closure"]["allowed_dispositions"] == [
        "remediation_accepted"
    ]
    with pytest.raises(ValueError, match="no-remediation closure is invalid"):
        service.close_case(
            risk_id,
            actor="risk-manager",
            reason="错误地尝试绕过整改验收",
            disposition="no_remediation_required",
            evidence_refs=["closure:invalid"],
            idempotency_key="close-risk-invalid-1",
        )
    closed = service.close_case(
        risk_id,
        actor="risk-manager",
        reason="技术恢复和整改验收均已完成",
        disposition="remediation_accepted",
        evidence_refs=["acceptance:review-1", "closure:approval-1"],
        idempotency_key="close-risk-approved-1",
    )
    assert closed["closed_by"] == "risk-manager"
    assert closed["closure_disposition"] == "remediation_accepted"
    replay = service.close_case(
        risk_id,
        actor="risk-manager",
        reason="幂等重放",
        disposition="remediation_accepted",
        evidence_refs=["closure:approval-1"],
        idempotency_key="close-risk-approved-1",
    )
    assert replay["closed_at"] == closed["closed_at"]
    final_detail = service.get_case(risk_id)
    assert final_detail["closure"]["state"] == "closed"
    assert final_detail["closure"]["closed_by"] == "risk-manager"
    assert final_detail["remediations"][0]["status"] == "accepted"
    assert final_detail["occurrences"][0]["occurrence_kind"] == "closed"
    first_episode_id = final_detail["episode_id"]
    assert final_detail["occurrences"][0]["risk_episode_id"] == first_episode_id

    with _session_scope(session_factory) as db:
        reopened = db.get(RiskCaseRecord, risk_id)
        reopened.episode_id = "risk-workbench-episode-2"
        reopened.status = "open"
        reopened.verification_status = "confirmed_current"
        reopened.resolved_at = None
        reopened.closed_at = None
        reopened.closed_by = None
        reopened.closure_reason = None
        reopened.closure_disposition = None
        reopened.closure_evidence_refs_json = None
        reopened.remediation_status = "not_started"
        reopened.owner = None
        reopened.due_at = None

    second_remediation = service.create_remediation(
        risk_id,
        action_text="复发后再次执行整改。",
        owner="network-team",
        created_by="engineer",
        idempotency_key="remediation-risk-workbench-1",
    )
    assert second_remediation["id"] != remediation["id"]
    assert second_remediation["risk_episode_id"] == "risk-workbench-episode-2"
    service.update_remediation(
        risk_id,
        second_remediation["id"],
        status="in_progress",
        actor="network-team",
        reason="第二轮整改开始",
        idempotency_key="start-remediation-risk-workbench-1",
    )
    service.update_remediation(
        risk_id,
        second_remediation["id"],
        status="completed",
        actor="network-team",
        reason="第二轮整改完成",
        evidence_refs=["snapshot:episode-2-after-change"],
        idempotency_key="complete-remediation-risk-workbench-1",
    )
    service.resolve_with_evidence(
        risk_id,
        successful_current_evaluation=True,
        observed_at=now + timedelta(hours=4),
        patrol_batch_id="batch-risk-recovery-episode-2",
        evidence_refs=["assessment:episode-2-healthy"],
        recovery_rule_version_id="rule-cpu-recovery-v1",
        actor="recovery-reviewer",
        reason="第二轮技术复检通过",
    )
    service.accept_remediation(
        risk_id,
        second_remediation["id"],
        actor="independent-reviewer",
        reason="第二轮整改验收通过",
        evidence_refs=["acceptance:episode-2"],
        idempotency_key="accept-remediation-review-1",
    )
    second_closed = service.close_case(
        risk_id,
        actor="risk-manager",
        reason="第二轮技术恢复和整改验收均已完成",
        disposition="remediation_accepted",
        evidence_refs=["closure:episode-2"],
        idempotency_key="close-risk-approved-1",
    )
    assert second_closed["closed_at"] != closed["closed_at"]
    with _session_scope(session_factory) as db:
        closed_occurrences = (
            db.query(RiskOccurrenceRecord)
            .filter(
                RiskOccurrenceRecord.risk_case_id == risk_id,
                RiskOccurrenceRecord.occurrence_kind == "closed",
            )
            .order_by(RiskOccurrenceRecord.observed_at)
            .all()
        )
        assert [item.risk_episode_id for item in closed_occurrences] == [
            first_episode_id,
            "risk-workbench-episode-2",
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
    assessment_policy_snapshot = _target_policy_snapshot(
        check_item,
        "device-exec-1",
    )
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
                assessment_policy_snapshot_json=(
                    assessment_policy_snapshot
                ),
                assessment_policy_snapshot_digest=(
                    assessment_policy_snapshot["digest"]
                ),
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
                **_execution_policy_fields(check_item, "device-empty-1"),
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
    # This case verifies snapshot binding, not stale-evidence handling.
    observed_at = datetime.now(timezone.utc)
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
                NetworkChangeRecord(
                    id="change-finalizer-cpu-deleted",
                    idempotency_key="change-finalizer-cpu-deleted",
                    run_id="run-finalizer-cpu",
                    check_item_id="system.cpu_usage",
                    check_item_version_id=check_item.version_id,
                    check_execution_id="execution-finalizer-cpu",
                    device_id="device-finalizer-1",
                    canonical_command="display cpu-usage",
                    entity_type="cpu_scope",
                    entity_key='["device","slot-removed"]',
                    dimensions_json={"window": "instant"},
                    field="cpu_usage",
                    observation_type="device.cpu.utilization",
                    old_value_json=40,
                    new_value_json=None,
                    change_type="deleted",
                    previous_snapshot_id="snapshot-finalizer-cpu-baseline",
                    current_snapshot_id=None,
                    previous_observation_id=(
                        "observation-finalizer-cpu-baseline"
                    ),
                    current_observation_id=None,
                    source="state_observation",
                    analysis_revision="original",
                    rule_bundle_digest=bundle["digest"],
                    event_status="no_event",
                    observed_at=observed_at,
                    detected_at=observed_at,
                    processed_at=observed_at,
                    confidence=1.0,
                    created_at=observed_at,
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
                    **_execution_policy_fields(
                        check_item,
                        "device-finalizer-1",
                    ),
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
        assert execution.change_count == 1
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
                    **_execution_policy_fields(
                        check_item,
                        "device-wrong-binding",
                    ),
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


def test_run_finalizer_classifies_command_timeout_before_parser_failure(
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
    observed_at = datetime.now(timezone.utc)
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                PatrolCollectionResult(
                    id="collection-timeout",
                    run_id="run-command-timeout",
                    inventory_device_id="device-command-timeout",
                    device_id="device-command-timeout",
                    execution_item_id="cpu-timeout",
                    collection_action_id="action-command-timeout",
                    canonical_command="display cpu-usage",
                    status="timeout",
                    execution_status="timeout",
                    parse_status="not_attempted",
                    output_status="not_classified",
                    fact_status="blocked_by_collection",
                    normalization_status="blocked",
                    failure_code="timeout",
                    observed_at=observed_at,
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
                CheckExecutionRecord(
                    id="execution-command-timeout",
                    idempotency_key="execution-command-timeout",
                    patrol_batch_id="batch-command-timeout",
                    device_id="device-command-timeout",
                    check_item_version_id=check_item.version_id,
                    implementation_version_id=implementation.version_id,
                    collection_action_ids_json=["action-command-timeout"],
                    runtime_bundle_id=bundle["bundle_id"],
                    runtime_bundle_version_id=bundle["version_id"],
                    runtime_bundle_digest=bundle["digest"],
                    **_execution_policy_fields(
                        check_item,
                        "device-command-timeout",
                    ),
                    execution_status="processing",
                    reason_codes_json=[],
                    target_manifest_digest="1" * 64,
                    asset_manifest_digest=bundle["digest"],
                    evidence_refs_json=[],
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
            ]
        )

    result = CheckItemRunFinalizer(scope).finalize(
        patrol_batch_id="batch-command-timeout",
        run_id="run-command-timeout",
        device_id="device-command-timeout",
    )

    assert result["success"] is False
    assert result["failures"][0]["reason_code"] == "command_timeout"
    with _session_scope(session_factory) as db:
        execution = db.get(
            CheckExecutionRecord,
            "execution-command-timeout",
        )
        assessment = db.get(
            CheckAssessmentRecord,
            execution.assessment_id,
        )
        assert execution.execution_status == "collection_failed"
        assert assessment.assessment_status == "unable_to_determine"
        assert "command_timeout" in assessment.blocking_reason_codes_json
        assert "parse_or_observation_missing" not in (
            assessment.blocking_reason_codes_json
        )
        assert "collection-timeout" in execution.evidence_refs_json


def test_run_finalizer_detects_snapshot_persistence_failure(tmp_path):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        _bundle_service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    observed_at = datetime.now(timezone.utc)
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                PatrolCollectionResult(
                    id="collection-without-snapshot",
                    run_id="run-without-snapshot",
                    inventory_device_id="device-without-snapshot",
                    device_id="device-without-snapshot",
                    execution_item_id="cpu-without-snapshot",
                    collection_action_id="action-without-snapshot",
                    canonical_command="display cpu-usage",
                    status="complete",
                    execution_status="complete",
                    parse_status="not_attempted",
                    output_status="success",
                    fact_status="blocked_by_parser",
                    normalization_status="blocked",
                    observed_at=observed_at,
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
                CheckExecutionRecord(
                    id="execution-without-snapshot",
                    idempotency_key="execution-without-snapshot",
                    patrol_batch_id="batch-without-snapshot",
                    device_id="device-without-snapshot",
                    check_item_version_id=check_item.version_id,
                    implementation_version_id=implementation.version_id,
                    collection_action_ids_json=["action-without-snapshot"],
                    runtime_bundle_id=bundle["bundle_id"],
                    runtime_bundle_version_id=bundle["version_id"],
                    runtime_bundle_digest=bundle["digest"],
                    **_execution_policy_fields(
                        check_item,
                        "device-without-snapshot",
                    ),
                    execution_status="processing",
                    reason_codes_json=[],
                    target_manifest_digest="2" * 64,
                    asset_manifest_digest=bundle["digest"],
                    evidence_refs_json=[],
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
            ]
        )

    result = CheckItemRunFinalizer(scope).finalize(
        patrol_batch_id="batch-without-snapshot",
        run_id="run-without-snapshot",
        device_id="device-without-snapshot",
    )

    assert result["success"] is False
    assert result["failures"][0]["reason_code"] == (
        "snapshot_persistence_failed"
    )
    with _session_scope(session_factory) as db:
        execution = db.get(
            CheckExecutionRecord,
            "execution-without-snapshot",
        )
        assessment = db.get(
            CheckAssessmentRecord,
            execution.assessment_id,
        )
        assert execution.execution_status == "collection_failed"
        assert "snapshot_persistence_failed" in (
            assessment.blocking_reason_codes_json
        )
        assert "collection-without-snapshot" in (
            execution.evidence_refs_json
        )


def test_run_finalizer_preserves_qualification_when_all_fallback_parsers_fail(
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
    observed_at = datetime.now(timezone.utc)
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                CommandSnapshot(
                    id="snapshot-parser-failed",
                    run_id="run-parser-failed",
                    device_id="device-parser-failed",
                    inventory_device_id="device-parser-failed",
                    execution_item_id="cpu",
                    collection_action_id="action-parser-failed",
                    implementation_version_id=implementation.version_id,
                    check_item_ids_json=["system.cpu_usage"],
                    device_name="H3C-Parser-Failed",
                    vendor="H3C",
                    model="S5130",
                    command="display cpu-usage",
                    canonical_command="display cpu-usage",
                    observed_at=observed_at,
                    structured_json=[],
                    raw_text="CPU usage: invalid",
                    raw_text_hash="f" * 64,
                    parse_status="parse_failed",
                    output_status="success",
                    metadata_json={
                        "check_item_bindings": [
                            {
                                "check_item_version_id": (
                                    check_item.version_id
                                ),
                                "fallback_rank": 1,
                            }
                        ],
                        "selected_check_item_bindings": [],
                    },
                ),
                PatrolCollectionResult(
                    id="collection-parser-failed",
                    run_id="run-parser-failed",
                    inventory_device_id="device-parser-failed",
                    device_id="device-parser-failed",
                    execution_item_id="cpu",
                    collection_action_id="action-parser-failed",
                    canonical_command="display cpu-usage",
                    status="complete",
                    execution_status="complete",
                    parse_status="parse_failed",
                    output_status="success",
                    fact_status="blocked_by_parser",
                    normalization_status="blocked",
                    observed_at=observed_at,
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
                PatrolCollectionResult(
                    id="collection-parser-primary-timeout",
                    run_id="run-parser-failed",
                    inventory_device_id="device-parser-failed",
                    device_id="device-parser-failed",
                    execution_item_id="cpu-primary",
                    collection_action_id="action-parser-primary-timeout",
                    canonical_command="display cpu-usage summary",
                    status="timeout",
                    execution_status="timeout",
                    parse_status="not_attempted",
                    output_status="not_classified",
                    fact_status="blocked_by_collection",
                    normalization_status="blocked",
                    failure_code="timeout",
                    observed_at=observed_at,
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
                CheckExecutionRecord(
                    id="execution-parser-failed",
                    idempotency_key="execution-parser-failed",
                    patrol_batch_id="batch-parser-failed",
                    device_id="device-parser-failed",
                    check_item_version_id=check_item.version_id,
                    implementation_version_id=implementation.version_id,
                    collection_action_ids_json=[
                        "action-parser-primary-timeout",
                        "action-parser-failed",
                    ],
                    runtime_bundle_id=bundle["bundle_id"],
                    runtime_bundle_version_id=bundle["version_id"],
                    runtime_bundle_digest=bundle["digest"],
                    **_execution_policy_fields(
                        check_item,
                        "device-parser-failed",
                    ),
                    execution_status="processing",
                    reason_codes_json=[],
                    target_manifest_digest="d" * 64,
                    asset_manifest_digest=bundle["digest"],
                    evidence_refs_json=[],
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
            ]
        )

    result = CheckItemRunFinalizer(scope).finalize(
        patrol_batch_id="batch-parser-failed",
        run_id="run-parser-failed",
        device_id="device-parser-failed",
    )

    assert result["success"] is False
    assert result["failure_count"] == 1
    with _session_scope(session_factory) as db:
        execution = db.get(
            CheckExecutionRecord,
            "execution-parser-failed",
        )
        qualification = db.get(
            EvidenceQualificationRecord,
            execution.evidence_qualification_id,
        )
        coverage = db.get(
            CoverageDecisionRecord,
            execution.coverage_decision_id,
        )
        assessment = db.get(
            CheckAssessmentRecord,
            execution.assessment_id,
        )
        assert qualification is not None
        assert qualification.source_identity_status == "confirmed"
        assert qualification.output_quality_status == "complete"
        assert coverage is not None
        assert coverage.coverage_status == "not_evaluable"
        assert "parse_failed" in coverage.reason_codes_json
        assert assessment.evidence_qualification_id == qualification.id
        assert assessment.coverage_decision_id == coverage.id
        assert assessment.coverage_status == "not_evaluable"
        assert assessment.assessment_status == "unable_to_determine"
        assert "command_timeout" not in (
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
                **_execution_policy_fields(
                    check_item,
                    "device-assessment-failed",
                ),
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


def test_run_finalizer_bulk_device_failures_keep_coverage_stage(tmp_path):
    (
        session_factory,
        scope,
        _catalog_repository,
        check_item,
        implementation,
        _bundle_service,
        bundle,
    ) = _create_cpu_bundle(tmp_path)
    observed_at = datetime.now(timezone.utc)
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                CommandSnapshot(
                    id="snapshot-normalizer-stage",
                    run_id="run-normalizer-stage",
                    inventory_device_id="device-normalizer-stage",
                    device_id="device-normalizer-stage",
                    execution_item_id="normalizer-stage",
                    collection_action_id="action-normalizer-stage",
                    implementation_version_id=implementation.version_id,
                    check_item_ids_json=["system.cpu_usage"],
                    device_name="H3C-Normalizer-Stage",
                    vendor="H3C",
                    model="S5130",
                    command="display cpu-usage",
                    canonical_command="display cpu-usage",
                    observed_at=observed_at,
                    structured_json=[{"cpu_usage": "35"}],
                    raw_text="CPU usage: 35%",
                    raw_text_hash="4" * 64,
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
                                    check_item.version_id
                                )
                            }
                        ],
                    },
                ),
                CheckExecutionRecord(
                    id="execution-normalizer-stage",
                    idempotency_key="execution-normalizer-stage",
                    patrol_batch_id="batch-mixed-failure-stage",
                    device_id="device-normalizer-stage",
                    check_item_version_id=check_item.version_id,
                    implementation_version_id=implementation.version_id,
                    collection_action_ids_json=["action-normalizer-stage"],
                    runtime_bundle_id=bundle["bundle_id"],
                    runtime_bundle_version_id=bundle["version_id"],
                    runtime_bundle_digest=bundle["digest"],
                    **_execution_policy_fields(
                        check_item,
                        "device-normalizer-stage",
                    ),
                    execution_status="processing",
                    reason_codes_json=[],
                    target_manifest_digest="3" * 64,
                    asset_manifest_digest=bundle["digest"],
                    evidence_refs_json=[],
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
                CheckExecutionRecord(
                    id="execution-collection-stage",
                    idempotency_key="execution-collection-stage",
                    patrol_batch_id="batch-mixed-failure-stage",
                    device_id="device-collection-stage",
                    check_item_version_id=check_item.version_id,
                    implementation_version_id=implementation.version_id,
                    collection_action_ids_json=["action-collection-stage"],
                    runtime_bundle_id=bundle["bundle_id"],
                    runtime_bundle_version_id=bundle["version_id"],
                    runtime_bundle_digest=bundle["digest"],
                    **_execution_policy_fields(
                        check_item,
                        "device-collection-stage",
                    ),
                    execution_status="processing",
                    reason_codes_json=[],
                    target_manifest_digest="3" * 64,
                    asset_manifest_digest=bundle["digest"],
                    evidence_refs_json=[],
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
            ]
        )

    result = CheckItemRunFinalizer(scope).fail_devices(
        patrol_batch_id="batch-mixed-failure-stage",
        failures=[
            {
                "device_id": "device-normalizer-stage",
                "status": "normalization_failed",
                "reason_code": "worker_timeout",
                "evidence_refs": ("workflow-normalizer-stage",),
                "run_id": "run-normalizer-stage",
            },
            {
                "device_id": "device-collection-stage",
                "status": "collection_failed",
                "reason_code": "command_timeout",
                "evidence_refs": ("workflow-collection-stage",),
            },
        ],
    )

    assert result["failed_execution_count"] == 2
    with _session_scope(session_factory) as db:
        normalization_execution = db.get(
            CheckExecutionRecord,
            "execution-normalizer-stage",
        )
        normalization_coverage = db.get(
            CoverageDecisionRecord,
            normalization_execution.coverage_decision_id,
        )
        collection_execution = db.get(
            CheckExecutionRecord,
            "execution-collection-stage",
        )
        assert normalization_execution.execution_status == (
            "normalization_failed"
        )
        assert normalization_coverage.coverage_status == "not_evaluable"
        assert "normalization_failed" in (
            normalization_coverage.reason_codes_json
        )
        assert collection_execution.execution_status == "collection_failed"
        assert collection_execution.coverage_decision_id is None


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
                    inventory_device_id="device-config-1",
                    device_id="H3C-1",
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
                    **_execution_policy_fields(check_item, "device-config-1"),
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
    assert result["changes"][0]["device_id"] == "device-config-1"
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
    # CommandAdapter V2 owns one CLI command per implementation; it may emit
    # multiple CheckItems but cannot use a second command as a fallback.
    assert cpu["unavailable_commands"] == []
    assert cpu["fallback_resilience"] == "not_applicable"
    assert cpu["declared_command_count"] == 1
    assert cpu["executable_command_count"] == 1
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
    assert plan.manifest["report_cutoff_policy"] == {
        "business_time_source": "batch_terminal_server_time",
        "ingestion_boundary_source": "report_requested_at",
        "clock": "server_utc",
        "late_fact_strategy": "new_report_manifest_revision",
    }

    same = service.freeze(request)
    assert same.manifest_digest == plan.manifest_digest
    with _session_scope(session_factory) as db:
        assert db.query(CheckExecutionRecord).filter(
            CheckExecutionRecord.patrol_batch_id == "batch-plan-1"
        ).count() == 2
        assert db.query(CheckItemRuntimeManifestRecord).count() == 1
        assert db.query(PatrolScopeContractRecord).count() == 1
        assert db.query(PatrolScopeContractVersionRecord).count() == 1
        assert db.query(PatrolIntendedTargetMatrixRecord).count() == 1
        assert db.query(PatrolScopeDecisionRecord).count() == 2
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
        assert batch.scope_contract_version_id == plan.manifest[
            "scope_contract_version_id"
        ]
        assert batch.scope_contract_digest == plan.manifest[
            "scope_contract_digest"
        ]
        assert batch.intended_target_matrix_digest == plan.manifest[
            "intended_target_matrix_digest"
        ]
        assert batch.scope_decision_digest == plan.manifest[
            "scope_decision_digest"
        ]

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


def test_effective_plan_freezes_governed_assessment_policy_binding(tmp_path):
    (
        session_factory,
        scope,
        catalog_repository,
        check_item,
        _implementation,
        _bundle_service,
        _bundle,
    ) = _create_cpu_bundle(tmp_path)
    policy_service = AssessmentPolicyAssetService(scope)
    observed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                NetworkDevice(
                    id="device-policy-plan",
                    device_name="H3C-Policy",
                    management_ip="192.0.2.80",
                    vendor="H3C",
                    model="S5130",
                    site="xian",
                    role="core",
                    status="active",
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
                PatrolBatchRecord(
                    patrol_batch_id="batch-policy-plan",
                    workflow_name="patrol-change-event",
                    launch_mode="workflow",
                    patrol_mode="production",
                    target_type="devices",
                    target_label="governed policy target",
                    selector_json={"device_ids": ["device-policy-plan"]},
                    target_count=1,
                    launched_count=0,
                    skipped_count=0,
                    save_baseline=False,
                    dispatch_status="pending",
                    report_status="not_requested",
                    archived=False,
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
            ]
        )

    draft = policy_service.create_version(
        check_item_id=check_item.id,
        check_item_version_id=check_item.version_id,
        observation_type="device.cpu.utilization",
        policy_role="high",
        name="核心设备 CPU 高水位",
        policy={
            "value": 72,
            "unit": "percent",
            "operator": "gte",
            "hysteresis": 5,
        },
        evidence_refs=["customer-policy:cpu-core-v1"],
        provenance={"source": "customer_policy"},
        created_by="policy-author",
        idempotency_key="plan-policy-create-001",
    )
    policy_service.submit(
        draft["id"],
        actor="policy-author",
        reason="提交网络专家复核",
        idempotency_key="plan-policy-submit-001",
    )
    policy_service.semantic_review(
        draft["id"],
        decision="approved",
        actor="network-sme",
        reason="阈值符合客户核心设备运维标准",
        idempotency_key="plan-policy-semantic-001",
    )
    approved = policy_service.publication_review(
        draft["id"],
        decision="approved",
        actor="policy-publisher",
        reason="证据与职责分离完整，批准发布",
        idempotency_key="plan-policy-publish-001",
    )
    binding = policy_service.bind(
        approved["id"],
        binding_type="inventory_profile",
        scope={"device_ids": ["device-policy-plan"]},
        actor="policy-publisher",
        reason="绑定指定核心交换机",
        idempotency_key="plan-policy-binding-001",
    )

    devices = (
        {
            "device_id": "device-policy-plan",
            "device_name": "H3C-Policy",
            "management_ip": "192.0.2.80",
            "vendor": "H3C",
            "family": "Comware7",
            "model": "S5130",
            "site": "xian",
            "role": "core",
        },
    )
    target = build_target_manifest(
        devices=devices,
        selector={"device_ids": ["device-policy-plan"]},
    )
    inventory = build_inventory_snapshot(devices)
    plan = CheckItemEffectivePlanService(
        scope,
        catalog=catalog_repository.load_active(),
    ).freeze(
        EffectivePlanRequest(
            patrol_batch_id="batch-policy-plan",
            devices=devices,
            check_item_ids=(check_item.id,),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=observed_at,
            actor="test",
        )
    )

    decision = plan.decisions[0]
    snapshot = decision["assessment_policy_snapshot"]
    source = snapshot["sources"][0]
    assert source["source_kind"] == "inventory_profile"
    assert source["policy_version_id"] == approved["id"]
    assert source["policy_version_digest"] == approved["digest"]
    assert source["binding_id"] == binding["id"]
    assert source["binding_digest"] == binding["binding_digest"]
    assert source["approval_digest"]
    assert source["thresholds"]["high"]["value"] == 72.0
    assert decision["assessment_policy_snapshot_digest"] == snapshot["digest"]

    with _session_scope(session_factory) as db:
        execution = db.query(CheckExecutionRecord).filter(
            CheckExecutionRecord.patrol_batch_id == "batch-policy-plan"
        ).one()
        assert execution.assessment_policy_snapshot_digest == snapshot["digest"]
        assert execution.assessment_policy_snapshot_json == snapshot


def test_effective_plan_stops_collection_when_applicability_is_unknown(tmp_path):
    (
        _session_factory,
        scope,
        catalog_repository,
        check_item,
        _implementation,
        _bundle_service,
        _bundle,
    ) = _create_cpu_bundle(tmp_path)
    catalog = catalog_repository.load_active()
    governed_check_item = replace(
        check_item,
        applicability={"capability": "cpu.telemetry"},
    )
    governed_catalog = replace(
        catalog,
        check_items=tuple(
            governed_check_item if item.id == check_item.id else item
            for item in catalog.check_items
        ),
    )
    devices = (
        {
            "device_id": "device-without-capability-inventory",
            "device_name": "Unknown-Capability",
            "management_ip": "192.0.2.81",
            "vendor": "H3C",
            "family": "Comware7",
            "model": "S5130",
        },
    )
    target = build_target_manifest(
        devices=devices,
        selector={"device_ids": ["device-without-capability-inventory"]},
    )
    inventory = build_inventory_snapshot(devices)

    plan = CheckItemEffectivePlanService(
        scope,
        catalog=governed_catalog,
    ).preview(
        EffectivePlanRequest(
            patrol_batch_id="batch-applicability-unknown",
            devices=devices,
            check_item_ids=(check_item.id,),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=datetime.now(timezone.utc),
            actor="test",
        )
    )

    decision = plan.decisions[0]
    assert decision["status"] == "unsupported"
    assert decision["applicability"] == "unknown"
    assert decision["reason_codes"] == ["applicability_capability_unknown"]
    assert decision["applicability_decision"]["status"] == "unknown"
    assert plan.collection_actions == ()
    assert plan.executions[0]["execution_status"] == "unsupported"
    assert plan.terminal_assessments[0]["assessment_status"] == (
        "unable_to_determine"
    )
    assert plan.terminal_assessments[0]["assessment_basis_json"][
        "applicability_decision"
    ]["status"] == "unknown"


def test_effective_plan_cutover_disables_legacy_policy_sources_and_fails_closed(
    tmp_path,
    monkeypatch,
):
    (
        _session_factory,
        scope,
        catalog_repository,
        check_item,
        _implementation,
        _bundle_service,
        _bundle,
    ) = _create_cpu_bundle(tmp_path)
    device_id = "device-governed-cutover"
    monkeypatch.setattr(
        AssessmentPolicyMigrationService,
        "effective_cutover_modes",
        lambda self, targets, session=None: {
            (check_item.version_id, device_id): {
                "id": "cutover-1",
                "resolver_mode": "governed_only",
                "evidence_digest": "e" * 64,
                "binding_ids_json": ["binding-applicability"],
                "shadow_result_ids_json": ["shadow-1"],
                "created_by": "asset-publisher",
                "reason": "matched shadow evidence",
                "created_at": "2026-08-02T10:00:00+00:00",
            }
        },
    )
    devices = (
        {
            "device_id": device_id,
            "device_name": "Governed-Cutover",
            "management_ip": "192.0.2.82",
            "vendor": "H3C",
            "family": "Comware7",
            "model": "S5130",
            "attributes": {
                "assessment_policy_bindings": [
                    {
                        "policy_id": "legacy-cpu",
                        "version": "1",
                        "check_item_id": check_item.id,
                        "observation_type": "device.cpu.utilization",
                        "policy_role": "high",
                        "status": "approved",
                    }
                ]
            },
        },
    )
    target = build_target_manifest(
        devices=devices,
        selector={"device_ids": [device_id]},
    )
    inventory = build_inventory_snapshot(devices)

    plan = CheckItemEffectivePlanService(
        scope,
        catalog=catalog_repository.load_active(),
    ).preview(
        EffectivePlanRequest(
            patrol_batch_id="batch-governed-cutover",
            devices=devices,
            check_item_ids=(check_item.id,),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=datetime.now(timezone.utc),
            actor="test",
        )
    )

    decision = plan.decisions[0]
    snapshot = decision["assessment_policy_snapshot"]
    assert snapshot["resolver_mode"] == "governed_only"
    assert snapshot["legacy_sources_allowed"] is False
    assert snapshot["sources"] == []
    assert snapshot["resolver_transition"]["id"] == "cutover-1"
    assert decision["status"] == "unsupported"
    assert decision["applicability"] == "unknown"
    assert decision["reason_codes"] == ["assessment_applicability_policy_missing"]
    assert plan.collection_actions == ()
    assert plan.terminal_assessments[0]["assessment_status"] == (
        "unable_to_determine"
    )


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
        "created_roots": 4,
        "created_versions": 4,
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
        assert db.query(TextComparisonProfileVersionRecord).count() == 4


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
    assessment_policy_snapshot = _target_policy_snapshot(
        check_item,
        "device-report-1",
    )
    assessment_basis = _resolved_assessment_basis(
        check_item=check_item,
        device_id="device-report-1",
        runtime_bundle_version_id=bundle["version_id"],
        runtime_bundle_digest=bundle["digest"],
        observation_id="observation-report-1",
    )
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
    scope_contract = {
        "scope_contract_id": "scope-contract-report-1",
        "claim_boundary": "selected_devices",
    }
    intended_target_pairs = [
        {
            "device_id": "device-report-1",
            "check_item_version_id": check_item.version_id,
        }
    ]
    scope_decisions = [
        {
            **intended_target_pairs[0],
            "decision": "included",
            "reason_codes": ["selected_target"],
        }
    ]
    runtime_document.update(
        {
            "scope_contract": scope_contract,
            "scope_contract_digest": _stable_digest(scope_contract),
            "intended_target_pairs": intended_target_pairs,
            "intended_target_matrix_digest": _stable_digest(
                intended_target_pairs
            ),
            "scope_decisions": scope_decisions,
            "scope_decision_digest": _stable_digest(scope_decisions),
            "unmatched_device_ids": [],
        }
    )
    runtime_document.pop("digest", None)
    runtime_digest = _stable_digest(runtime_document)
    runtime_document["digest"] = runtime_digest
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
            runtime_bundle_version_id=bundle["version_id"],
            assessment_policy_snapshot_digest=(
                assessment_policy_snapshot["digest"]
            ),
            assessment_basis=assessment_basis,
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
                    assessment_policy_snapshot_json=(
                        assessment_policy_snapshot
                    ),
                    assessment_policy_snapshot_digest=(
                        assessment_policy_snapshot["digest"]
                    ),
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
    assert manifest["schema_version"] == (
        "patrol-report-manifest/v5"
    )
    assert manifest["report_purpose"] == "production_current"
    assert len(manifest["raw_evidence_set_digest"]) == 64
    assert manifest["source_facts_json"]["readiness"][
        "ready_for_final"
    ] is True
    facts = manifest["source_facts_json"]
    assert facts["schema_version"] == "patrol-report-source/v5"
    assert facts["report_purpose"] == "production_current"
    assert facts["raw_evidence_set_digest"] == (
        manifest["raw_evidence_set_digest"]
    )
    assert facts["fact_ingestion_high_watermark"]
    assert facts["late_fact_count"] == 0
    assert len(facts["claim_ledger"]["claims"]) == manifest[
        "claim_count"
    ]
    assert facts["claim_ledger_digest"] == manifest[
        "claim_ledger_digest"
    ]
    projection_snapshot = facts["report_projection_snapshot"]
    assert facts["report_projection_digest"] == (
        projection_snapshot["digest"]
    )
    assert projection_snapshot["bindings"][
        check_item.version_id
    ]["profile_ref"] == "cpu@1"
    with _session_scope(session_factory) as db:
        assert (
            db.query(ReportClaimRecord)
            .filter(
                ReportClaimRecord.report_manifest_id
                == manifest["id"]
            )
            .count()
            == manifest["claim_count"]
        )
    assert manifest["fact_ingestion_high_watermark"]
    assert facts["check_items"][check_item.version_id][
        "check_item_id"
    ] == "system.cpu_usage"
    assert facts["observations"][0]["execution_ids"] == [
        "execution-report-1"
    ]
    assert facts["observations"][0]["value_number"] == 35
    assert facts["executions"][0][
        "assessment_policy_snapshot_digest"
    ] == assessment_policy_snapshot["digest"]
    assert facts["assessments"][0][
        "assessment_basis_digest"
    ] == assessment_basis["digest"]
    assert facts["assessments"][0]["assessment_basis_json"][
        "summary"
    ]["source_counts"] == {"catalog_default": 1}
    assessment_claim = next(
        item
        for item in facts["claim_ledger"]["claims"]
        if item["claim_code"] == f"assessment.{assessment.id}"
    )
    assert f"AssessmentBasis:{assessment_basis['digest']}" in (
        assessment_claim["fact_refs"]
    )
    assert facts["evidence_index"][0]["snapshot_id"] == (
        "snapshot-report-1"
    )
    assert "raw_text" not in facts["evidence_index"][0]
    assert [item["id"] for item in facts["risks"]] == [
        "risk-from-other-batch"
    ]
    permission = facts["conclusion_permission"]
    assert permission["terminal_truth_reconciled"] is True
    assert permission["current_conclusion_ready"] is True
    assert (
        permission["evaluated_scope_no_abnormality_allowed"]
        is False
    )
    assert permission["overall_healthy_allowed"] is False
    assert (
        "unresolved_in_scope_risk_case"
        in permission["blocker_reason_codes"]
    )
    report_view = build_check_item_report_view(manifest)
    assert report_view["matrix"][0]["assessment_basis_digest"] == (
        assessment_basis["digest"]
    )
    assert report_view["matrix"][0][
        "assessment_basis_source_counts"
    ] == {"catalog_default": 1}
    assert report_view["report_projection_digest"] == (
        facts["report_projection_digest"]
    )
    assert report_view["conclusion"]["code"] == "unresolved_risk"
    assert "未关闭风险" in report_view["conclusion"]["text"]
    assert "未发现异常" not in report_view["conclusion"]["text"]
    assert service.build(
        "batch-report-1",
        generated_by="test",
    )["id"] == manifest["id"]
    batch_results = CheckItemBatchResultService(scope)
    live_summary = batch_results.summary("batch-report-1")
    assert live_summary["conclusion"]["code"] == (
        "unresolved_risk"
    )
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
    result_detail = batch_results.get_result_detail(
        "batch-report-1",
        "execution-report-1",
    )
    assert result_detail["schema_version"] == "patrol-check-result-detail/v1"
    assert result_detail["assessment_policy_snapshot"]["digest"] == (
        assessment_policy_snapshot["digest"]
    )
    assert result_detail["assessment_basis"]["digest"] == (
        assessment_basis["digest"]
    )
    assert result_detail["integrity"] == {
        "policy_snapshot_present": True,
        "policy_snapshot_valid": True,
        "assessment_basis_present": True,
        "assessment_basis_valid": True,
        "policy_snapshot_digest_matches": True,
        "assessment_basis_digest_matches": True,
    }
    assert result_detail["assessment_basis_entries"] == {
        "total": 1,
        "limit": 50,
        "offset": 0,
        "has_more": False,
    }
    assert "observation-report-1" in result_detail["evidence_refs"]
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
    artifact_object_keys = {
        "markdown": "reports/batch-report-1/report.md",
        "docx": "reports/batch-report-1/report.docx",
        "excel": "reports/batch-report-1/appendix.xlsx",
        "json": "reports/batch-report-1/report.json",
        "evidence": "reports/batch-report-1/evidence.zip",
    }
    artifact_objects = {
        object_key: f"assessment-runtime-{name}".encode()
        for name, object_key in artifact_object_keys.items()
    }
    artifact_digests = {
        name: hashlib.sha256(artifact_objects[object_key]).hexdigest()
        for name, object_key in artifact_object_keys.items()
    }
    object_storage = _MemoryObjectStorage(artifact_objects)
    attached = service.attach_artifacts(
        manifest["id"],
        object_keys=artifact_object_keys,
        sha256_by_artifact=artifact_digests,
    )
    assert attached["artifact_sha256_json"]["excel"] == artifact_digests[
        "excel"
    ]
    release_service = ReportReleaseService(
        scope,
        object_storage=object_storage,
    )
    with _session_scope(session_factory) as db:
        row = db.get(ReportManifestRecord, manifest["id"])
        original_facts = json.loads(
            json.dumps(row.source_facts_json)
        )
        original_source_digest = str(row.source_digest)
        tampered_facts = json.loads(json.dumps(original_facts))
        original_context = dict(
            original_facts["report_authority_context"]
        )
        tampered_facts["report_authority_context"] = (
            build_report_authority_context(
                report_purpose="production_current",
                patrol_mode=original_context.get("patrol_mode"),
                launch_mode=original_context.get("launch_mode"),
                workflow_name=original_context.get("workflow_name"),
                target_type="offline_import",
                readiness_ready=bool(
                    original_context.get("facts_ready")
                ),
                target_manifest_digest=original_context.get(
                    "target_manifest_digest"
                ),
                inventory_snapshot_digest=original_context.get(
                    "inventory_snapshot_digest"
                ),
                runtime_manifest_digest=original_context.get(
                    "runtime_manifest_digest"
                ),
                observation_min_at=original_context.get(
                    "observation_min_at"
                ),
                observation_max_at=original_context.get(
                    "observation_max_at"
                ),
            )
        )
        row.source_facts_json = tampered_facts
        row.source_digest = _stable_digest(tampered_facts)
    with pytest.raises(
        ValueError,
        match="report purpose is inconsistent with source evidence",
    ):
        release_service.release(
            manifest["id"],
            signed_by="reviewer",
            signoff_comment="tampered authority must be rejected",
            disclosure_confirmations=[
                "facts_reviewed",
                "unknown_scope_disclosed",
                "artifact_digests_verified",
            ],
            verified_artifact_sha256=attached[
                "artifact_sha256_json"
            ],
        )
    with _session_scope(session_factory) as db:
        row = db.get(ReportManifestRecord, manifest["id"])
        row.source_facts_json = original_facts
        row.source_digest = original_source_digest
    with _session_scope(session_factory) as db:
        row = db.get(ReportManifestRecord, manifest["id"])
        stale_permission_facts = json.loads(
            json.dumps(original_facts)
        )
        stale_permission_facts["assessments"][0][
            "assessment_status"
        ] = "unable_to_determine"
        stale_permission_facts["assessments"][0][
            "blocking_reason_codes_json"
        ] = ["parser_missing"]
        row.source_facts_json = stale_permission_facts
        row.source_digest = _stable_digest(
            stale_permission_facts
        )
    with pytest.raises(
        ValueError,
        match="conclusion permission does not match",
    ):
        release_service.release(
            manifest["id"],
            signed_by="reviewer",
            signoff_comment=(
                "stale conclusion permission must be rejected"
            ),
            disclosure_confirmations=[
                "facts_reviewed",
                "unknown_scope_disclosed",
                "artifact_digests_verified",
            ],
            verified_artifact_sha256=attached[
                "artifact_sha256_json"
            ],
        )
    with _session_scope(session_factory) as db:
        row = db.get(ReportManifestRecord, manifest["id"])
        row.source_facts_json = original_facts
        row.source_digest = original_source_digest
    with pytest.raises(
        ValueError,
        match="signer different from the manifest generator",
    ):
        release_service.release(
            manifest["id"],
            signed_by="api:test",
            signoff_comment="self sign-off must be rejected",
            disclosure_confirmations=[
                "facts_reviewed",
                "unknown_scope_disclosed",
                "artifact_digests_verified",
            ],
            verified_artifact_sha256=attached[
                "artifact_sha256_json"
            ],
        )
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
                **attached["artifact_sha256_json"],
                "markdown": "7" * 64,
            },
        )
    content_review = ReportContentReviewService(
        scope,
        object_storage=object_storage,
    )
    review = content_review.start(
        manifest["id"],
        created_by="content-reviewer",
    )
    pending_items = content_review.list_items(
        manifest["id"],
        page=1,
        page_size=200,
        review_status="pending",
    )
    assert pending_items["total"] == review["required_item_count"]
    decided = content_review.decide_items(
        manifest["id"],
        review_item_ids=[
            item["review_item_id"] for item in pending_items["items"]
        ],
        decision="accepted",
        reviewed_by="content-reviewer",
        review_comment="reviewed all required and sampled report facts",
    )
    assert decided["can_accept"] is True
    accepted_content = content_review.accept(
        manifest["id"],
        accepted_by="content-reviewer",
        acceptance_comment=(
            "accepted report content, evidence traces and action wording"
        ),
        confirmations=REQUIRED_CONTENT_CONFIRMATIONS,
        verified_artifact_sha256=attached["artifact_sha256_json"],
        accepted_at=now + timedelta(minutes=4),
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
    assert release["claim_ledger_digest"] == manifest[
        "claim_ledger_digest"
    ]
    assert release["content_review_id"] == accepted_content["id"]
    assert release["content_acceptance_digest"] == accepted_content[
        "acceptance_digest"
    ]
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
    with _session_scope(session_factory) as db:
        row = db.get(ReportManifestRecord, manifest["id"])
        original_facts = json.loads(
            json.dumps(row.source_facts_json)
        )
        tampered_facts = json.loads(json.dumps(original_facts))
        tampered_facts["late_fact_count"] = 999
        row.source_facts_json = tampered_facts
    with pytest.raises(
        ValueError,
        match="source facts do not match the immutable digest",
    ):
        release_service.release(
            manifest["id"],
            signed_by="reviewer",
            signoff_comment="idempotent retry must still verify facts",
            disclosure_confirmations=[
                "facts_reviewed",
                "unknown_scope_disclosed",
                "artifact_digests_verified",
            ],
            verified_artifact_sha256=attached[
                "artifact_sha256_json"
            ],
        )
    with _session_scope(session_factory) as db:
        row = db.get(ReportManifestRecord, manifest["id"])
        row.source_facts_json = original_facts
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
    previous_high_watermark = manifest[
        "fact_ingestion_high_watermark"
    ]
    revised_high_watermark = revised[
        "fact_ingestion_high_watermark"
    ]
    if previous_high_watermark.tzinfo is None:
        previous_high_watermark = previous_high_watermark.replace(
            tzinfo=timezone.utc
        )
    if revised_high_watermark.tzinfo is None:
        revised_high_watermark = revised_high_watermark.replace(
            tzinfo=timezone.utc
        )
    assert revised_high_watermark > previous_high_watermark
    assert revised["source_facts_json"]["late_fact_count"] >= 1
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


def test_mixed_device_outcomes_and_report_terminal_semantics(
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
                    "context": {
                        "cpu_high_warning_percent": 80,
                        "cpu_high_alarm_percent": 90,
                        "threshold_reference_sources": {
                            "high": {"field_name": "cpu_high_warning_percent"},
                            "critical_high": {"field_name": "cpu_high_alarm_percent"},
                        },
                    },
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
