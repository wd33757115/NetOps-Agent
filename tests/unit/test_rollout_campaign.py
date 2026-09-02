from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items.assessment_policy import (
    normalize_assessment_policy_contract,
)
from src.core.check_items.rollout import (
    COMPARISON_DOMAINS,
    ROLLOUT_STAGES,
    RolloutCampaignService,
    RolloutValidationError,
    _digest,
)
from src.core.check_items.rollout_execution import (
    RolloutOutboxRepository,
    RolloutProjectionEngine,
    RolloutStageExecutionError,
    RolloutStageExecutionService,
)
from src.core.check_items.rollout_legacy import (
    RolloutLegacyRouteService,
)
from src.infrastructure.db.models import (
    AssessmentPolicyBindingRecord,
    AssessmentPolicyDecisionRecord,
    AssessmentPolicyProfileRecord,
    AssessmentPolicyVersionRecord,
    AuditLogRecord,
    Base,
    CheckItemRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    CommandSnapshot,
    ComparisonContextRecord,
    EntityIdentityPolicyRecord,
    EntityIdentityPolicyVersionRecord,
    NetworkDevice,
    PatrolDataset,
    PatrolRun,
    ProductionFeedbackRecord,
    RolloutCampaignRecord,
    RolloutComparisonEvidenceRecord,
    RolloutLegacyRouteRecord,
    RolloutOutboxRecord,
    RolloutProjectionRecord,
    RolloutStageAttemptRecord,
    RolloutStageExecutionRecord,
)


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
    engine = create_engine(f"sqlite:///{tmp_path / 'rollout.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            CheckItemRecord.__table__,
            CheckItemVersionRecord.__table__,
            AssessmentPolicyProfileRecord.__table__,
            AssessmentPolicyVersionRecord.__table__,
            AssessmentPolicyDecisionRecord.__table__,
            AssessmentPolicyBindingRecord.__table__,
            CollectionImplementationRecord.__table__,
            CollectionImplementationVersionRecord.__table__,
            EntityIdentityPolicyRecord.__table__,
            EntityIdentityPolicyVersionRecord.__table__,
            NetworkDevice.__table__,
            CheckItemRuntimeBundleRecord.__table__,
            CheckItemRuntimeBundleVersionRecord.__table__,
            ComparisonContextRecord.__table__,
            PatrolDataset.__table__,
            PatrolRun.__table__,
            CommandSnapshot.__table__,
            AuditLogRecord.__table__,
            RolloutCampaignRecord.__table__,
            RolloutLegacyRouteRecord.__table__,
            RolloutStageAttemptRecord.__table__,
            RolloutStageExecutionRecord.__table__,
            RolloutProjectionRecord.__table__,
            RolloutComparisonEvidenceRecord.__table__,
            RolloutOutboxRecord.__table__,
            ProductionFeedbackRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)

    def scope():
        return _session_scope(session_factory)

    now = datetime.now(timezone.utc)
    with scope() as db:
        db.add_all(
            [
                NetworkDevice(
                    id="device-1",
                    device_name="core-1",
                    management_ip="192.0.2.1",
                    vendor="H3C",
                    model="S5130",
                    site="xian-core",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                CheckItemRecord(
                    id="system.cpu_usage",
                    name="CPU usage",
                    category="system",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
                CheckItemVersionRecord(
                    id="check-cpu-v1",
                    check_item_id="system.cpu_usage",
                    version=1,
                    result_mode="structured",
                    assessment_mode="health",
                    canonical_contract_json={"observations": []},
                    applicability_json={
                        "capability": "cpu_metrics",
                        "unknown_is": "unable_to_determine",
                    },
                    report_projection="cpu_usage",
                    status="active",
                    digest="a" * 64,
                    created_by="seed",
                    created_at=now,
                    activated_at=now,
                ),
                CollectionImplementationRecord(
                    id="h3c-cpu",
                    name="H3C CPU",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
                CollectionImplementationVersionRecord(
                    id="impl-cpu-v1",
                    implementation_id="h3c-cpu",
                    version=1,
                    collector="cli",
                    match_json={"vendor": "h3c"},
                    commands_json=[{"command": "display cpu-usage"}],
                    parser_requirement="structured",
                    template_scope="platform",
                    selection_priority=100,
                    status="active",
                    digest="b" * 64,
                    created_by="seed",
                    created_at=now,
                    activated_at=now,
                ),
                EntityIdentityPolicyRecord(
                    id="identity-default",
                    name="Default identity",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
                EntityIdentityPolicyVersionRecord(
                    id="identity-default-v1",
                    policy_id="identity-default",
                    version=1,
                    policy_json={},
                    digest="c" * 64,
                    status="active",
                    created_by="seed",
                    created_at=now,
                    activated_at=now,
                ),
                CheckItemRuntimeBundleRecord(
                    id="bundle-cpu",
                    check_item_id="system.cpu_usage",
                    implementation_id="h3c-cpu",
                    name="CPU runtime bundle",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
                CheckItemRuntimeBundleVersionRecord(
                    id="bundle-cpu-v1",
                    bundle_id="bundle-cpu",
                    version=1,
                    check_item_version_id="check-cpu-v1",
                    implementation_version_id="impl-cpu-v1",
                    parser_bundle_json={"template_ids": ["parser-cpu-v1"]},
                    canonical_catalog_version=1,
                    canonical_catalog_digest="d" * 64,
                    entity_identity_policy_version_id="identity-default-v1",
                    dependency_digests_json={},
                    compatibility_json={},
                    validation_report_json={"passed": True},
                    digest="e" * 64,
                    status="active",
                    created_by="seed",
                    created_at=now,
                    activated_at=now,
                ),
                CheckItemRuntimeBundleVersionRecord(
                    id="bundle-cpu-v2",
                    bundle_id="bundle-cpu",
                    version=2,
                    check_item_version_id="check-cpu-v1",
                    implementation_version_id="impl-cpu-v1",
                    parser_bundle_json={"template_ids": ["parser-cpu-v2"]},
                    canonical_catalog_version=1,
                    canonical_catalog_digest="d" * 64,
                    entity_identity_policy_version_id="identity-default-v1",
                    dependency_digests_json={},
                    compatibility_json={},
                    validation_report_json={"success": True},
                    digest="2" * 64,
                    status="draft",
                    created_by="asset-maker",
                    created_at=now,
                ),
                ComparisonContextRecord(
                    id="context-device-1-cpu",
                    digest="f" * 64,
                    mode="no_baseline",
                    device_id="device-1",
                    check_item_version_id="check-cpu-v1",
                    selection_reason="first qualified observation",
                    created_at=now,
                ),
                PatrolRun(
                    id="run-cpu-baseline",
                    mode="production",
                    source="unit_test",
                    status="completed",
                    collection_status="completed",
                    parser_readiness_status="ready",
                    fact_readiness_status="ready",
                    analysis_status="completed",
                    baseline_eligible=True,
                    run_purpose="scheduled_patrol",
                    logical_run_id="logical-run-cpu-baseline",
                    attempt_number=0,
                    owner_generation=1,
                    device_count=1,
                    command_count=1,
                    started_at=datetime(
                        2026,
                        7,
                        10,
                        tzinfo=timezone.utc,
                    ),
                    completed_at=datetime(
                        2026,
                        7,
                        10,
                        0,
                        1,
                        tzinfo=timezone.utc,
                    ),
                ),
                PatrolRun(
                    id="run-cpu-current",
                    mode="production",
                    source="unit_test",
                    status="completed",
                    collection_status="completed",
                    parser_readiness_status="ready",
                    fact_readiness_status="ready",
                    analysis_status="completed",
                    baseline_eligible=True,
                    run_purpose="scheduled_patrol",
                    logical_run_id="logical-run-cpu-current",
                    attempt_number=0,
                    owner_generation=1,
                    device_count=1,
                    command_count=1,
                    started_at=datetime(
                        2026,
                        7,
                        20,
                        tzinfo=timezone.utc,
                    ),
                    completed_at=datetime(
                        2026,
                        7,
                        20,
                        0,
                        1,
                        tzinfo=timezone.utc,
                    ),
                ),
                CommandSnapshot(
                    id="snapshot-cpu-baseline",
                    run_id="run-cpu-baseline",
                    inventory_device_id="device-1",
                    device_id="device-1",
                    execution_item_id="item-cpu-baseline",
                    implementation_version_id="impl-cpu-v1",
                    check_item_ids_json=["system.cpu_usage"],
                    device_name="core-1",
                    management_ip="192.0.2.1",
                    vendor="H3C",
                    model="S5130",
                    command="display cpu-usage",
                    canonical_command="display cpu-usage",
                    observed_at=datetime(
                        2026,
                        7,
                        10,
                        tzinfo=timezone.utc,
                    ),
                    raw_text="CPU usage: 10%",
                    raw_text_hash="1" * 64,
                    parse_status="parsed",
                    output_status="normal",
                    snapshot_kind="full",
                    created_at=datetime(
                        2026,
                        7,
                        10,
                        tzinfo=timezone.utc,
                    ),
                ),
                CommandSnapshot(
                    id="snapshot-cpu-current",
                    run_id="run-cpu-current",
                    inventory_device_id="device-1",
                    device_id="device-1",
                    execution_item_id="item-cpu-current",
                    implementation_version_id="impl-cpu-v1",
                    check_item_ids_json=["system.cpu_usage"],
                    device_name="core-1",
                    management_ip="192.0.2.1",
                    vendor="H3C",
                    model="S5130",
                    command="display cpu-usage",
                    canonical_command="display cpu-usage",
                    observed_at=datetime(
                        2026,
                        7,
                        20,
                        tzinfo=timezone.utc,
                    ),
                    raw_text="CPU usage: 12%",
                    raw_text_hash="2" * 64,
                    parse_status="parsed",
                    output_status="normal",
                    snapshot_kind="full",
                    created_at=datetime(
                        2026,
                        7,
                        20,
                        tzinfo=timezone.utc,
                    ),
                ),
            ]
        )
    return (
        session_factory,
        scope,
        RolloutCampaignService(
            scope,
            stage_execution_preparer=None,
            stage_result_verifier=None,
        ),
    )


def _freeze(service: RolloutCampaignService) -> dict:
    campaign = service.create(
        name="CPU guarded rollout",
        description="unit test",
        actor="maker",
        idempotency_key="create-cpu-rollout",
    )
    return service.freeze_target(
        campaign["id"],
        target_manifest={
            "selector": {"device_ids": ["device-1"]},
            "matrix": [
                {
                    "device_id": "device-1",
                    "check_item_version_id": "check-cpu-v1",
                    "implementation_version_id": "impl-cpu-v1",
                    "baseline_route": {
                        "mode": "runtime_bundle",
                        "runtime_bundle_version_id": "bundle-cpu-v1",
                    },
                    "candidate_runtime_bundle_version_id": "bundle-cpu-v2",
                }
            ],
        },
        runtime_bundle_refs=[
            {
                "role": "baseline",
                "version_id": "bundle-cpu-v1",
                "digest": "e" * 64,
            },
            {
                "role": "candidate",
                "version_id": "bundle-cpu-v2",
                "digest": "2" * 64,
            },
        ],
        comparison_context_ids=["context-device-1-cpu"],
        baseline_window={
            "start_at": "2026-07-01T00:00:00+00:00",
            "end_at": "2026-07-28T00:00:00+00:00",
        },
        thresholds={},
        rollback_plan={
            "mode": "legacy_route",
            "verified": True,
            "evidence_refs": [{"ref": "rollback-rehearsal-1"}],
        },
        actor="maker",
        reason="freeze reviewed target and runtime evidence",
        idempotency_key="freeze-cpu-rollout",
    )


def _observed_digests(campaign: dict) -> dict[str, str]:
    return {
        "target_manifest_digest": campaign["target_manifest_digest"],
        "runtime_bundle_digest": campaign["runtime_bundle_digest"],
        "comparison_context_digest": campaign["comparison_context_digest"],
    }


def _passing_metrics() -> dict:
    return {
        "runtime_bundle_coverage_rate": 1.0,
        "execution_success_rate": 1.0,
        "baseline_execution_success_rate": 1.0,
        "assessment_coverage_rate": 1.0,
        "baseline_assessment_coverage_rate": 1.0,
        "unevaluable_rate": 0.0,
        "baseline_unevaluable_rate": 0.0,
        "unexplained_p0_p1_count": 0,
        "historical_evidence_loss_count": 0,
        "unexplained_event_difference_count": 0,
        "event_collision_count": 0,
        "report_reconciled": True,
        "rollback_verified": True,
    }


def _passing_comparisons() -> dict[str, bool]:
    return {domain: True for domain in COMPARISON_DOMAINS}


class _FakeProjectionEngine:
    def project(self, *, track, target, snapshots, route, **_kwargs):
        observations = [
            {
                "device_id": target["device_id"],
                "entity_type": "device_cpu",
                "entity_key": "device",
                "field_name": "cpu_usage",
                "observation_type": "device.cpu.utilization",
                "semantic_type": "gauge",
                "unit": "percent",
                "dimensions": {},
                "value": 12.0,
            }
        ]
        return {
            "schema_version": "rollout-projection/v1",
            "track": track,
            "target_key": target["target_key"],
            "runtime_bundle_version_id": route[
                "runtime_bundle_version_id"
            ],
            "snapshot_ids": [item["id"] for item in snapshots],
            "parse_results": [
                {
                    "snapshot_id": item["id"],
                    "raw_text_hash": item["raw_text_hash"],
                    "canonical_command": item["canonical_command"],
                    "parser_status": "success",
                    "failure_code": None,
                    "output_status": "normal",
                    "record_count": 1,
                }
                for item in snapshots
            ],
            "observations": observations,
            "changes": [],
            "events": [],
            "current_events": [],
            "identity_bindings": [
                {
                    "device_id": target["device_id"],
                    "entity_type": "device_cpu",
                    "entity_key": "device",
                }
            ],
            "assessment": {
                "status": "completed",
                "parse_success": True,
                "observation_count": 1,
                "observed_entity_count": 1,
                "change_count": 0,
                "event_count": 0,
                "current_event_count": 0,
            },
            "report_projection": {
                "status": "completed",
                "observation_count": 1,
                "change_count": 0,
                "event_count": 0,
                "current_event_count": 0,
                "risk_count": 0,
            },
            "raw_cli_included": False,
        }


class _MemoryObjectStorage:
    def __init__(self):
        self.objects = {}

    def upload_file(self, object_key, content, *, content_type):
        self.objects[object_key] = {
            "content": bytes(content),
            "content_type": content_type,
        }
        return True


class _FakeCaptureAdapter:
    def __init__(self):
        self.started = 0
        self.polled = 0

    def start(self, *, execution_id, selection_manifest):
        self.started += 1
        assert selection_manifest["execution_mode"] == "live_capture"
        return {
            "status": "capturing",
            "capture_batch_id": f"capture-{execution_id}",
        }

    def poll(self, *, capture_batch_id, selection_manifest):
        self.polled += 1
        target = selection_manifest["targets"][0]
        assert capture_batch_id.startswith("capture-")
        return {
            "status": "completed",
            "capture_batch_id": capture_batch_id,
            "snapshot_bindings": {
                target["target_key"]: {
                    "snapshot_ids": ["snapshot-cpu-current"],
                    "periods": [
                        {
                            "period_key": "run-cpu-current",
                            "snapshot_ids": ["snapshot-cpu-current"],
                        }
                    ],
                }
            },
            "snapshot_ids": ["snapshot-cpu-current"],
            "snapshot_run_ids": ["run-cpu-current"],
            "workflow_run_ids": ["workflow-capture-1"],
        }


def _start_and_record(
    service: RolloutCampaignService,
    campaign: dict,
    *,
    suffix: str,
) -> dict:
    started = service.start_stage(
        campaign["id"],
        actor="operator",
        reason="start evidence collection",
        idempotency_key=f"start-{suffix}",
    )
    return service.record_stage_result(
        campaign["id"],
        metrics=_passing_metrics(),
        comparisons=_passing_comparisons(),
        evidence_refs=[{"ref": f"stage-evidence-{suffix}"}],
        observed_digests=_observed_digests(started),
        actor="system:rollout-evaluator",
        reason="all frozen stage criteria evaluated",
        idempotency_key=f"result-{suffix}",
        trusted=True,
    )


def test_shadow_routing_is_non_authoritative_and_scope_is_exact(tmp_path):
    _session_factory, _scope, service = _runtime(tmp_path)
    frozen = _freeze(service)
    assert frozen["status"] == "target_frozen"
    assert frozen["current_attempt"]["stage"] == "shadow"
    assert frozen["current_attempt"]["status"] == "pending"

    started = service.start_stage(
        frozen["id"],
        actor="operator",
        reason="collect shadow evidence",
        idempotency_key="start-shadow",
    )
    target = service.routing_decision(
        started["id"],
        device_id="device-1",
        check_item_version_id="check-cpu-v1",
        **_observed_digests(started),
    )
    assert target["candidate_enabled"] is True
    assert target["authoritative"] is False
    assert target["reason_code"] == "shadow_evidence_only"
    assert target["target_row"]["baseline_route"] == {
        "mode": "runtime_bundle",
        "runtime_bundle_version_id": "bundle-cpu-v1",
        "implementation_version_id": "impl-cpu-v1",
    }
    assert (
        target["target_row"]["candidate_runtime_bundle_version_id"]
        == "bundle-cpu-v2"
    )

    outside = service.routing_decision(
        started["id"],
        device_id="device-2",
        check_item_version_id="check-cpu-v1",
        **_observed_digests(started),
    )
    assert outside["candidate_enabled"] is False
    assert outside["authoritative"] is False
    assert outside["reason_code"] == "outside_frozen_target"


def test_rollout_freezes_distinct_implementation_commands_per_track(tmp_path):
    _session_factory, scope, _service = _runtime(tmp_path)
    now = datetime.now(timezone.utc)
    with scope() as db:
        db.add(
            CollectionImplementationVersionRecord(
                id="impl-cpu-v2",
                implementation_id="h3c-cpu",
                version=2,
                collector="cli",
                match_json={"vendor": "h3c", "family": "comware7"},
                commands_json=[{"command": "display cpu-usage summary"}],
                parser_requirement="structured",
                template_scope="platform",
                selection_priority=110,
                status="draft",
                digest="3" * 64,
                created_by="asset-maker",
                created_at=now,
            )
        )
        db.add(
            CheckItemRuntimeBundleVersionRecord(
                id="bundle-cpu-next-v1",
                bundle_id="bundle-cpu",
                version=3,
                check_item_version_id="check-cpu-v1",
                implementation_version_id="impl-cpu-v2",
                parser_bundle_json={"template_ids": ["parser-cpu-next-v1"]},
                canonical_catalog_version=1,
                canonical_catalog_digest="d" * 64,
                entity_identity_policy_version_id="identity-default-v1",
                dependency_digests_json={},
                compatibility_json={},
                validation_report_json={"success": True},
                digest="4" * 64,
                status="draft",
                created_by="asset-maker",
                created_at=now,
            )
        )
        for suffix, run_id, observed_at, raw_hash in (
            (
                "baseline",
                "run-cpu-baseline",
                datetime(2026, 7, 10, tzinfo=timezone.utc),
                "5" * 64,
            ),
            (
                "current",
                "run-cpu-current",
                datetime(2026, 7, 20, tzinfo=timezone.utc),
                "6" * 64,
            ),
        ):
            db.add(
                CommandSnapshot(
                    id=f"snapshot-cpu-next-{suffix}",
                    run_id=run_id,
                    inventory_device_id="device-1",
                    device_id="device-1",
                    execution_item_id=f"item-cpu-next-{suffix}",
                    implementation_version_id="impl-cpu-v2",
                    check_item_ids_json=["system.cpu_usage"],
                    device_name="core-1",
                    management_ip="192.0.2.1",
                    vendor="H3C",
                    model="S5130",
                    command="display cpu-usage summary",
                    canonical_command="display cpu-usage summary",
                    observed_at=observed_at,
                    raw_text="CPU average usage: 11%",
                    raw_text_hash=raw_hash,
                    parse_status="parsed",
                    output_status="normal",
                    snapshot_kind="full",
                    created_at=observed_at,
                )
            )

    controller = RolloutCampaignService(scope)
    campaign = controller.create(
        name="CPU implementation replacement",
        actor="maker",
        idempotency_key="create-cpu-implementation-rollout",
    )
    frozen = controller.freeze_target(
        campaign["id"],
        target_manifest={
            "matrix": [
                {
                    "device_id": "device-1",
                    "check_item_version_id": "check-cpu-v1",
                    "baseline_route": {
                        "mode": "runtime_bundle",
                        "runtime_bundle_version_id": "bundle-cpu-v1",
                    },
                    "candidate_runtime_bundle_version_id": (
                        "bundle-cpu-next-v1"
                    ),
                }
            ]
        },
        runtime_bundle_refs=[
            {
                "role": "baseline",
                "version_id": "bundle-cpu-v1",
                "digest": "e" * 64,
            },
            {
                "role": "candidate",
                "version_id": "bundle-cpu-next-v1",
                "digest": "4" * 64,
            },
        ],
        comparison_context_ids=["context-device-1-cpu"],
        baseline_window={
            "start_at": "2026-07-01T00:00:00+00:00",
            "end_at": "2026-07-28T00:00:00+00:00",
        },
        thresholds={},
        rollback_plan={
            "mode": "runtime_bundle",
            "verified": True,
            "evidence_refs": [{"ref": "rollback-rehearsal-next"}],
        },
        actor="maker",
        reason="compare the previous and next collection implementations",
        idempotency_key="freeze-cpu-implementation-rollout",
    )
    target = frozen["target_manifest_json"]["matrix"][0]
    assert frozen["target_manifest_json"]["schema_version"] == (
        "rollout-target/v3"
    )
    assert target["candidate_implementation_version_id"] == "impl-cpu-v2"
    assert target["baseline_route"]["implementation_version_id"] == (
        "impl-cpu-v1"
    )

    started = controller.start_stage(
        frozen["id"],
        actor="operator",
        reason="collect implementation replacement evidence",
        idempotency_key="start-cpu-implementation-shadow",
    )
    selection = started["stage_executions"][0]["selection_manifest_json"]
    selected_target = selection["targets"][0]
    expected_policy_contract = normalize_assessment_policy_contract(
        {"observations": []},
        {
            "capability": "cpu_metrics",
            "unknown_is": "unable_to_determine",
        },
    )
    assert selected_target["assessment_policy_snapshot"][
        "contract_digest"
    ] == expected_policy_contract["digest"]
    assert selected_target["commands_by_track"] == {
        "baseline": ["display cpu-usage"],
        "candidate": ["display cpu-usage summary"],
    }
    assert selected_target["snapshot_ids_by_track"] == {
        "baseline": ["snapshot-cpu-baseline", "snapshot-cpu-current"],
        "candidate": [
            "snapshot-cpu-next-baseline",
            "snapshot-cpu-next-current",
        ],
    }
    execution_id = started["stage_executions"][0]["id"]
    executor = RolloutStageExecutionService(
        scope,
        object_storage=_MemoryObjectStorage(),
        projection_engine=_FakeProjectionEngine(),
    )
    executor.execute(
        execution_id,
        worker_owner="implementation-rollout-worker",
    )
    detail = executor.get(execution_id)
    assert detail is not None
    projection_snapshots = {
        item["track"]: item["snapshot_ids_json"]
        for item in detail["projections"]
    }
    assert projection_snapshots == {
        "baseline": ["snapshot-cpu-baseline", "snapshot-cpu-current"],
        "candidate": [
            "snapshot-cpu-next-baseline",
            "snapshot-cpu-next-current",
        ],
    }


def test_existing_v2_target_manifest_remains_readable():
    row = {
        "target_key": _digest(
            {
                "device_id": "device-1",
                "check_item_version_id": "check-cpu-v1",
            }
        ),
        "device_id": "device-1",
        "check_item_version_id": "check-cpu-v1",
        "implementation_version_id": "impl-cpu-v1",
        "baseline_route": {
            "mode": "runtime_bundle",
            "runtime_bundle_version_id": "bundle-cpu-v1",
        },
        "candidate_runtime_bundle_version_id": "bundle-cpu-v2",
        "stratum": {
            "site": "xian-core",
            "vendor": "H3C",
            "model": "S5130",
            "check_item_version_id": "check-cpu-v1",
        },
    }
    campaign_id = "existing-v2-campaign"
    manifest = {
        "schema_version": "rollout-target/v2",
        "selector": {"device_ids": ["device-1"]},
        "selection_strategy": "frozen_proportional_stratified_prefix/v1",
        "matrix": [row],
        "stage_rosters": RolloutCampaignService._build_stage_rosters(
            campaign_id,
            [row],
        ),
    }
    assert RolloutCampaignService._validate_frozen_target_manifest(
        manifest,
        campaign_id=campaign_id,
    ) == manifest


def test_stage_approval_enforces_separation_of_duties(tmp_path):
    _session_factory, _scope, service = _runtime(tmp_path)
    awaiting = _start_and_record(
        service,
        _freeze(service),
        suffix="shadow",
    )
    assert awaiting["current_attempt"]["status"] == "awaiting_approval"

    for actor in ("maker", "api:operator"):
        with pytest.raises(
            RolloutValidationError,
            match="approver different",
        ):
            service.approve_stage(
                awaiting["id"],
                actor=actor,
                comment="self approval must fail",
                idempotency_key=f"approve-shadow-{actor}",
            )

    approved = service.approve_stage(
        awaiting["id"],
        actor="approver",
        comment="shadow evidence reconciled",
        idempotency_key="approve-shadow",
    )
    assert approved["status"] == "canary_5"
    assert approved["current_attempt"]["stage"] == "canary_5"
    assert approved["current_attempt"]["status"] == "pending"


def test_failed_stage_pauses_and_resume_creates_a_new_attempt(tmp_path):
    _session_factory, _scope, service = _runtime(tmp_path)
    frozen = _freeze(service)
    started = service.start_stage(
        frozen["id"],
        actor="operator",
        reason="collect shadow evidence",
        idempotency_key="start-shadow",
    )
    failed_metrics = _passing_metrics()
    failed_metrics["execution_success_rate"] = 0.5
    paused = service.record_stage_result(
        started["id"],
        metrics=failed_metrics,
        comparisons=_passing_comparisons(),
        evidence_refs=[{"ref": "failed-shadow-evidence"}],
        observed_digests=_observed_digests(started),
        actor="system:rollout-evaluator",
        reason="threshold evaluation",
        idempotency_key="failed-shadow",
        trusted=True,
    )
    assert paused["status"] == "paused"
    assert paused["current_attempt"]["status"] == "failed"
    assert "execution success rate is below" in paused["last_error"]

    resumed = service.resume(
        paused["id"],
        observed_digests=_observed_digests(paused),
        actor="release-manager",
        reason="root cause fixed; repeat the same stage",
        idempotency_key="resume-shadow",
    )
    assert resumed["status"] == "shadow"
    assert resumed["current_attempt"]["status"] == "pending"
    assert resumed["current_attempt"]["attempt_number"] == 2


def test_live_asset_drift_pauses_and_routes_to_previous_path(tmp_path):
    session_factory, scope, service = _runtime(tmp_path)
    frozen = _freeze(service)
    started = service.start_stage(
        frozen["id"],
        actor="operator",
        reason="collect shadow evidence",
        idempotency_key="start-shadow",
    )
    with scope() as db:
        db.get(
            CheckItemRuntimeBundleVersionRecord,
            "bundle-cpu-v1",
        ).status = "deprecated"

    decision = service.routing_decision(
        started["id"],
        device_id="device-1",
        check_item_version_id="check-cpu-v1",
        **_observed_digests(started),
    )
    assert decision["candidate_enabled"] is False
    assert decision["authoritative"] is False
    assert decision["reason_code"] == "campaign_digest_drifted"
    assert decision["new_campaign_required"] is True
    assert any(
        "runtime bundle" in blocker for blocker in decision["blockers"]
    )
    with _session_scope(session_factory) as db:
        campaign = db.get(RolloutCampaignRecord, started["id"])
        assert campaign.status == "paused"
        assert (
            db.query(AuditLogRecord)
            .filter(
                AuditLogRecord.resource_id == started["id"],
                AuditLogRecord.action
                == "rollout_routing_drift_detected",
            )
            .count()
            == 1
        )


def test_duplicate_comparison_context_for_one_target_is_rejected(tmp_path):
    _session_factory, scope, service = _runtime(tmp_path)
    now = datetime.now(timezone.utc)
    with scope() as db:
        db.add(
            ComparisonContextRecord(
                id="context-device-1-cpu-duplicate",
                digest="1" * 64,
                mode="no_baseline",
                device_id="device-1",
                check_item_version_id="check-cpu-v1",
                selection_reason="duplicate context",
                created_at=now,
            )
        )
    campaign = service.create(
        name="Invalid duplicate context rollout",
        actor="maker",
        idempotency_key="create-duplicate-context",
    )
    with pytest.raises(
        RolloutValidationError,
        match="exactly one context",
    ):
        service.freeze_target(
            campaign["id"],
            target_manifest={
                "matrix": [
                    {
                        "device_id": "device-1",
                        "check_item_version_id": "check-cpu-v1",
                        "implementation_version_id": "impl-cpu-v1",
                        "baseline_route": {
                            "mode": "runtime_bundle",
                            "runtime_bundle_version_id": "bundle-cpu-v1",
                        },
                        "candidate_runtime_bundle_version_id": (
                            "bundle-cpu-v2"
                        ),
                    }
                ]
            },
            runtime_bundle_refs=[
                {
                    "role": "baseline",
                    "version_id": "bundle-cpu-v1",
                    "digest": "e" * 64,
                },
                {
                    "role": "candidate",
                    "version_id": "bundle-cpu-v2",
                    "digest": "2" * 64,
                },
            ],
            comparison_context_ids=[
                "context-device-1-cpu",
                "context-device-1-cpu-duplicate",
            ],
            baseline_window={
                "start_at": "2026-07-01T00:00:00+00:00",
                "end_at": "2026-07-28T00:00:00+00:00",
            },
            thresholds={},
            rollback_plan={
                "mode": "legacy_route",
                "verified": True,
                "evidence_refs": [{"ref": "rollback-rehearsal-1"}],
            },
            actor="maker",
            reason="must reject ambiguous baseline authority",
            idempotency_key="freeze-duplicate-context",
        )


def test_all_stages_require_evidence_and_finish_only_after_observation(
    tmp_path,
):
    session_factory, _scope, service = _runtime(tmp_path)
    campaign = _freeze(service)
    for stage in ROLLOUT_STAGES:
        assert campaign["current_stage"] == stage
        awaiting = _start_and_record(
            service,
            campaign,
            suffix=stage,
        )
        assert awaiting["current_attempt"]["status"] == "awaiting_approval"
        campaign = service.approve_stage(
            campaign["id"],
            actor=f"approver-{stage}",
            comment=f"{stage} evidence accepted",
            idempotency_key=f"approve-{stage}",
        )

    assert campaign["status"] == "completed"
    assert campaign["completed_at"] is not None
    assert len(campaign["attempts"]) == len(ROLLOUT_STAGES)
    assert {item["status"] for item in campaign["attempts"]} == {"passed"}
    with _session_scope(session_factory) as db:
        actions = {
            row.action
            for row in db.query(AuditLogRecord)
            .filter(AuditLogRecord.resource_id == campaign["id"])
            .all()
        }
    assert {
        "rollout_campaign_created",
        "rollout_target_frozen",
        "rollout_stage_started",
        "rollout_stage_result_recorded",
        "rollout_stage_approved",
    } <= actions


def test_canary_selection_uses_the_frozen_stage_roster(tmp_path):
    _session_factory, _scope, service = _runtime(tmp_path)
    campaign = _start_and_record(
        service,
        _freeze(service),
        suffix="shadow",
    )
    campaign = service.approve_stage(
        campaign["id"],
        actor="approver",
        comment="advance to canary",
        idempotency_key="approve-shadow",
    )
    campaign = service.start_stage(
        campaign["id"],
        actor="operator",
        reason="start five percent canary",
        idempotency_key="start-canary-5",
    )
    decision = service.routing_decision(
        campaign["id"],
        device_id="device-1",
        check_item_version_id="check-cpu-v1",
        **_observed_digests(campaign),
    )
    assert decision["percentage"] == 5
    roster = campaign["target_manifest_json"]["stage_rosters"]["canary_5"]
    assert roster["target_count"] == 1
    assert decision["target_row"]["target_key"] in roster["target_keys"]
    assert decision["candidate_enabled"] is True
    assert decision["authoritative"] is True


def test_stage_rosters_are_nested_exact_and_stratified():
    matrix = []
    for index in range(40):
        vendor = "H3C" if index < 20 else "Cisco"
        matrix.append(
            {
                "target_key": f"target-{index:02d}",
                "stratum": {
                    "site": "xian-core",
                    "vendor": vendor,
                    "model": "model-a",
                    "check_item_version_id": "check-cpu-v1",
                },
            }
        )

    rosters = RolloutCampaignService._build_stage_rosters(
        "campaign-1",
        matrix,
    )

    assert rosters["shadow"]["target_count"] == 40
    assert rosters["canary_5"]["target_count"] == 2
    assert rosters["canary_20"]["target_count"] == 8
    assert rosters["canary_50"]["target_count"] == 20
    assert rosters["full_100"]["target_count"] == 40
    assert set(rosters["canary_5"]["target_keys"]) <= set(
        rosters["canary_20"]["target_keys"]
    )
    assert set(rosters["canary_20"]["target_keys"]) <= set(
        rosters["canary_50"]["target_keys"]
    )
    assert set(rosters["canary_50"]["target_keys"]) <= set(
        rosters["full_100"]["target_keys"]
    )
    vendor_by_target = {
        item["target_key"]: item["stratum"]["vendor"] for item in matrix
    }
    selected_vendors = {
        vendor_by_target[item]
        for item in rosters["canary_5"]["target_keys"]
    }
    assert selected_vendors == {"H3C", "Cisco"}


def test_idempotency_key_is_bound_to_the_request_digest(tmp_path):
    _session_factory, _scope, service = _runtime(tmp_path)
    created = service.create(
        name="CPU rollout",
        actor="maker",
        idempotency_key="same-create-key",
    )
    replayed = service.create(
        name="CPU rollout",
        actor="maker",
        idempotency_key="same-create-key",
    )
    assert replayed["id"] == created["id"]

    with pytest.raises(
        RolloutValidationError,
        match="different create request",
    ) as create_error:
        service.create(
            name="Different rollout",
            actor="maker",
            idempotency_key="same-create-key",
        )
    assert create_error.value.code == "idempotency_conflict"

    frozen = _freeze(service)
    started = service.start_stage(
        frozen["id"],
        actor="operator",
        reason="collect frozen evidence",
        idempotency_key="same-start-key",
    )
    replayed_start = service.start_stage(
        frozen["id"],
        actor="operator",
        reason="collect frozen evidence",
        idempotency_key="same-start-key",
    )
    assert replayed_start["lock_version"] == started["lock_version"]
    with pytest.raises(
        RolloutValidationError,
        match="different start_stage request",
    ) as start_error:
        service.start_stage(
            frozen["id"],
            actor="operator",
            reason="a different operation hidden behind the same key",
            idempotency_key="same-start-key",
        )
    assert start_error.value.code == "idempotency_conflict"


def test_manual_stage_result_is_rejected_even_with_valid_metrics(tmp_path):
    _session_factory, _scope, service = _runtime(tmp_path)
    started = service.start_stage(
        _freeze(service)["id"],
        actor="operator",
        reason="collect frozen evidence",
        idempotency_key="start-untrusted-result",
    )
    with pytest.raises(
        RolloutValidationError,
        match="trusted evaluator",
    ) as error:
        service.record_stage_result(
            started["id"],
            metrics=_passing_metrics(),
            comparisons=_passing_comparisons(),
            evidence_refs=[{"ref": "browser-supplied-evidence"}],
            observed_digests=_observed_digests(started),
            actor="operator",
            reason="browser claims that rollout passed",
            idempotency_key="untrusted-result",
        )
    assert error.value.code == "trusted_evaluator_required"


def test_stage_execution_uses_outbox_and_persists_isolated_evidence(tmp_path):
    _session_factory, scope, _controller = _runtime(tmp_path)
    controller = RolloutCampaignService(scope)
    started = controller.start_stage(
        _freeze(controller)["id"],
        actor="operator",
        reason="execute immutable shadow replay",
        idempotency_key="start-trusted-shadow",
    )
    assert started["next_action"] == "await_stage_execution"
    assert len(started["stage_executions"]) == 1
    execution_id = started["stage_executions"][0]["id"]

    outbox = RolloutOutboxRepository(scope)
    claimed = outbox.claim_pending(limit=10)
    assert len(claimed) == 1
    assert claimed[0]["aggregate_id"] == execution_id
    with pytest.raises(
        RolloutStageExecutionError,
        match="fenced",
    ):
        outbox.mark_published(
            claimed[0]["id"],
            claim_token="wrong-token",
        )
    outbox.mark_published(
        claimed[0]["id"],
        claim_token=claimed[0]["claim_token"],
    )

    storage = _MemoryObjectStorage()
    executor = RolloutStageExecutionService(
        scope,
        object_storage=storage,
        projection_engine=_FakeProjectionEngine(),
    )
    result = executor.execute(
        execution_id,
        worker_owner="unit-worker",
        worker_task_id="unit-task",
    )
    assert result["success"] is True
    assert result["campaign"]["current_attempt"]["status"] == (
        "awaiting_approval"
    )
    assert result["metrics"]["execution_success_rate"] == 1.0
    assert result["metrics"]["unexplained_p0_p1_count"] == 0
    assert len(storage.objects) == 1

    detail = executor.get(execution_id)
    assert detail is not None
    assert detail["status"] == "completed"
    assert len(detail["projections"]) == 2
    assert all(
        projection["facts_json"]["raw_cli_included"] is False
        for projection in detail["projections"]
    )
    evidence = detail["comparison_evidence"]
    assert evidence["manifest_json"]["raw_cli_included"] is False
    assert evidence["artifact_sha256"] in {
        item["sha256"]
        for item in result["campaign"]["current_attempt"][
            "evidence_refs_json"
        ]
    }

    replay = executor.execute(
        execution_id,
        worker_owner="unit-worker-retry",
        worker_task_id="unit-task-retry",
    )
    assert replay["campaign"]["current_attempt"]["status"] == (
        "awaiting_approval"
    )
    with scope() as db:
        assert db.query(RolloutProjectionRecord).count() == 2
        assert db.query(RolloutComparisonEvidenceRecord).count() == 1
        assert db.query(RolloutOutboxRecord).one().status == "published"


def test_live_capture_waits_for_one_raw_capture_then_runs_both_tracks(
    tmp_path,
):
    _session_factory, scope, _controller = _runtime(tmp_path)
    controller = RolloutCampaignService(scope)
    started = controller.start_stage(
        _freeze(controller)["id"],
        actor="operator",
        reason="collect fresh immutable raw evidence",
        idempotency_key="start-live-capture-shadow",
        execution_mode="live_capture",
    )
    execution = started["stage_executions"][0]
    assert execution["execution_mode"] == "live_capture"
    assert execution["source_snapshot_ids_json"] == []
    assert all(
        "commands" in target
        for target in execution["selection_manifest_json"]["targets"]
    )

    capture = _FakeCaptureAdapter()
    storage = _MemoryObjectStorage()
    executor = RolloutStageExecutionService(
        scope,
        object_storage=storage,
        projection_engine=_FakeProjectionEngine(),
        capture_adapter=capture,
    )
    waiting = executor.execute(
        execution["id"],
        worker_owner="capture-worker",
        worker_task_id="capture-task",
    )
    assert waiting["status"] == "capturing"
    assert waiting["awaiting_capture"] is True
    assert capture.started == 1
    assert executor.get(execution["id"])["projections"] == []

    reconciled = executor.reconcile_live_capture(execution["id"])
    assert reconciled["status"] == "queued"
    assert reconciled["projection_queued"] is True
    assert reconciled["source_snapshot_ids_json"] == [
        "snapshot-cpu-current"
    ]
    assert capture.polled == 1

    result = executor.execute(
        execution["id"],
        worker_owner="projection-worker",
        worker_task_id="projection-task",
    )
    assert result["success"] is True
    detail = executor.get(execution["id"])
    assert detail["status"] == "completed"
    assert len(detail["projections"]) == 2
    assert {
        tuple(item["snapshot_ids_json"])
        for item in detail["projections"]
    } == {("snapshot-cpu-current",)}
    manifest = detail["comparison_evidence"]["manifest_json"]
    assert manifest["execution_mode"] == "live_capture"
    assert manifest["capture_batch_id"] == (
        f"capture-{execution['id']}"
    )


def test_legacy_route_is_scope_bound_and_drift_pauses_stage(tmp_path):
    _session_factory, scope, _controller = _runtime(tmp_path)
    with scope() as db:
        db.add(
            RolloutLegacyRouteRecord(
                id="legacy-cpu-v1",
                route_name="CPU previous production path",
                check_item_version_id="check-cpu-v1",
                implementation_version_id="impl-cpu-v1",
                adapter_kind="runtime_bundle_compat",
                adapter_version="v1",
                config_json={
                    "runtime_bundle_version_id": "bundle-cpu-v1",
                    "runtime_bundle_digest": "e" * 64,
                },
                digest="9" * 64,
                status="active",
                idempotency_key="legacy-route-cpu-v1",
                created_by="baseline-owner",
                created_at=datetime.now(timezone.utc),
            )
        )
    controller = RolloutCampaignService(
        scope,
        stage_execution_preparer=None,
        stage_result_verifier=None,
    )
    campaign = controller.create(
        name="Legacy baseline rollout",
        actor="maker",
        idempotency_key="create-legacy-baseline-rollout",
    )
    frozen = controller.freeze_target(
        campaign["id"],
        target_manifest={
            "selector": {"device_ids": ["device-1"]},
            "matrix": [
                {
                    "device_id": "device-1",
                    "check_item_version_id": "check-cpu-v1",
                    "implementation_version_id": "impl-cpu-v1",
                    "baseline_route": {
                        "mode": "legacy_route",
                        "route_id": "legacy-cpu-v1",
                        "digest": "9" * 64,
                    },
                    "candidate_runtime_bundle_version_id": (
                        "bundle-cpu-v2"
                    ),
                }
            ],
        },
        runtime_bundle_refs=[
            {
                "role": "candidate",
                "version_id": "bundle-cpu-v2",
                "digest": "2" * 64,
            }
        ],
        comparison_context_ids=["context-device-1-cpu"],
        baseline_window={
            "start_at": "2026-07-01T00:00:00+00:00",
            "end_at": "2026-07-28T00:00:00+00:00",
        },
        thresholds={},
        rollback_plan={
            "mode": "legacy_route",
            "verified": True,
            "evidence_refs": [{"ref": "rollback-rehearsal-legacy"}],
        },
        actor="maker",
        reason="freeze exact legacy adapter and candidate",
        idempotency_key="freeze-legacy-baseline-rollout",
    )
    assert (
        frozen["target_manifest_json"]["matrix"][0][
            "baseline_route"
        ]["route_id"]
        == "legacy-cpu-v1"
    )

    with scope() as db:
        route = db.get(RolloutLegacyRouteRecord, "legacy-cpu-v1")
        route.status = "deprecated"
        route.deprecated_by = "baseline-owner"
        route.deprecated_at = datetime.now(timezone.utc)
        route.deprecation_reason = "simulated route drift"

    blocked = controller.start_stage(
        campaign["id"],
        actor="operator",
        reason="attempt after baseline drift",
        idempotency_key="start-legacy-after-drift",
    )
    assert blocked["status"] == "paused"
    assert "legacy baseline route is not active" in blocked["last_error"]
    assert blocked["stage_executions"] == []


def test_legacy_route_service_freezes_adapter_and_dependency_digest(
    tmp_path,
    monkeypatch,
):
    _session_factory, scope, _controller = _runtime(tmp_path)
    bundle = {
        "version_id": "bundle-cpu-v1",
        "check_item_version_id": "check-cpu-v1",
        "implementation_version_id": "impl-cpu-v1",
        "digest": "e" * 64,
        "status": "active",
    }

    class _BundleService:
        def get_version(self, version_id):
            return bundle if version_id == "bundle-cpu-v1" else None

    monkeypatch.setattr(
        "src.core.check_items.rollout_legacy."
        "CheckItemRuntimeBundleService",
        lambda _scope: _BundleService(),
    )
    service = RolloutLegacyRouteService(scope)
    created = service.create_runtime_bundle_route(
        route_name="CPU stable legacy path",
        runtime_bundle_version_id="bundle-cpu-v1",
        actor="baseline-owner",
        idempotency_key="create-legacy-service-route",
    )
    resolved = service.resolve(created["id"], created["digest"])
    assert resolved["adapter_kind"] == "runtime_bundle_compat"
    assert resolved["projection_route"] == {
        "mode": "runtime_bundle",
        "runtime_bundle_version_id": "bundle-cpu-v1",
        "implementation_version_id": "impl-cpu-v1",
    }

    bundle["digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest drifted"):
        service.resolve(created["id"], created["digest"])


def _evaluation_input(baseline_facts, candidate_facts):
    return {
        "campaign": {
            "id": "campaign-evaluation",
            "target_manifest_digest": "a" * 64,
            "runtime_bundle_digest": "b" * 64,
            "comparison_context_digest": "c" * 64,
            "thresholds_json": {},
            "rollback_plan_json": {"verified": True},
        },
        "attempt": {
            "id": "attempt-evaluation",
            "stage": "shadow",
        },
        "execution": {
            "id": "execution-evaluation",
            "selection_manifest_digest": "d" * 64,
            "snapshot_set_digest": "e" * 64,
            "fact_high_watermark": datetime.now(timezone.utc),
        },
        "projections": [
            {
                "id": "projection-baseline",
                "track": "baseline",
                "target_key": "target-1",
                "snapshot_set_digest": "f" * 64,
                "runtime_bundle_version_id": None,
                "facts_json": baseline_facts,
                "facts_digest": "1" * 64,
            },
            {
                "id": "projection-candidate",
                "track": "candidate",
                "target_key": "target-1",
                "snapshot_set_digest": "f" * 64,
                "runtime_bundle_version_id": "candidate-v1",
                "facts_json": candidate_facts,
                "facts_digest": "2" * 64,
            },
        ],
    }


def _completed_projection_facts():
    return {
        "snapshot_ids": ["snapshot-1"],
        "parse_results": [
            {
                "snapshot_id": "snapshot-1",
                "raw_text_hash": "a" * 64,
                "canonical_command": "display cpu-usage",
                "parser_status": "success",
                "failure_code": None,
                "output_status": "normal",
                "record_count": 1,
                "template_id": "ignored-by-quality-comparison",
                "template_version": 1,
            }
        ],
        "observations": [{"value": 12.0}],
        "findings": [],
        "assessment": {
            "formation_status": "completed",
            "parse_success": True,
            "assessment_status": "healthy",
            "observation_count": 1,
            "observed_entity_count": 1,
        },
        "changes": [],
        "events": [],
        "identity_bindings": [{"device_id": "device-1"}],
        "report_projection": {
            "status": "healthy",
            "risk_count": 0,
        },
    }


def test_first_runtime_bundle_uses_absolute_gates_without_fake_baseline():
    baseline_facts = {
        **RolloutProjectionEngine._no_prior_projection(
            execution_id="execution-evaluation",
            track="baseline",
            target={
                "target_key": "target-1",
                "device_id": "device-1",
                "check_item_version_id": "check-cpu-v1",
                "implementation_version_id": "impl-cpu-v1",
            },
            snapshots=[
                {
                    "id": "snapshot-1",
                    "raw_text_hash": "a" * 64,
                }
            ],
        )
    }
    candidate_facts = _completed_projection_facts()
    _manifest, metrics, comparisons = (
        RolloutStageExecutionService._evaluate(
            _evaluation_input(baseline_facts, candidate_facts)
        )
    )
    assert metrics["execution_success_rate"] == 1.0
    assert metrics["baseline_applicable_target_count"] == 0
    assert metrics["baseline_unevaluable_rate"] == 0.0
    assert all(
        item["status"] == "not_applicable"
        for item in comparisons.values()
    )


def test_confirmed_production_feedback_is_derived_into_rollout_evidence():
    baseline_facts = _completed_projection_facts()
    candidate_facts = {
        **_completed_projection_facts(),
        "events": [
            {"event_type": "InterfaceDown", "entity_key": f"GE1/0/{index}"}
            for index in range(1, 21)
        ],
    }
    detached = _evaluation_input(baseline_facts, candidate_facts)
    detached["feedback"] = [
        {
            "id": "feedback-1",
            "feedback_type": "false_positive",
            "triage_outcome": "confirmed",
            "execution_id": "execution-production-1",
            "assessment_id": "assessment-production-1",
            "runtime_bundle_version_id": "candidate-v1",
            "evidence_digest": "9" * 64,
            "source_context_digest": "8" * 64,
        }
    ]

    manifest, metrics, _comparisons = RolloutStageExecutionService._evaluate(detached)

    assert metrics["confirmed_false_positive_count"] == 1
    assert metrics["confirmed_false_positive_rate"] == 0.05
    assert metrics["feedback_rate_sample_sufficient"] is True
    assert manifest["production_feedback_refs"] == [
        {
            "id": "feedback-1",
            "feedback_type": "false_positive",
            "execution_id": "execution-production-1",
            "assessment_id": "assessment-production-1",
            "runtime_bundle_version_id": "candidate-v1",
            "evidence_digest": "9" * 64,
            "source_context_digest": "8" * 64,
        }
    ]
    blockers = RolloutCampaignService._metric_blockers(
        metrics,
        RolloutCampaignService._normalize_thresholds({}),
    )
    assert "confirmed false positives exceed the frozen threshold" in blockers


def test_evaluator_ignores_parser_identity_but_counts_severe_event_drift():
    baseline_facts = _completed_projection_facts()
    candidate_facts = _completed_projection_facts()
    candidate_facts["parse_results"][0]["template_id"] = "candidate-parser"
    candidate_facts["parse_results"][0]["template_version"] = 9
    candidate_facts["events"] = [
        {
            "event_type": "CpuCritical",
            "severity": "critical",
            "device_id": "device-1",
        },
        {
            "event_type": "CpuWarning",
            "severity": "warning",
            "device_id": "device-1",
        },
    ]
    _manifest, metrics, comparisons = (
        RolloutStageExecutionService._evaluate(
            _evaluation_input(baseline_facts, candidate_facts)
        )
    )
    assert comparisons["output_quality"]["status"] == "matched"
    assert comparisons["event"]["status"] == "unexplained"
    assert metrics["unexplained_event_difference_count"] == 1
    assert metrics["unexplained_p0_p1_count"] == 1


def test_snapshot_selection_uses_single_command_periods_after_adapter_cutover(tmp_path):
    _session_factory, scope, _controller = _runtime(tmp_path)
    with scope() as db:
        implementation = db.get(
            CollectionImplementationVersionRecord,
            "impl-cpu-v1",
        )
        implementation.commands_json = [
            {"command": "display cpu-usage"},
            {"command": "display memory"},
        ]
        for suffix, run_id, day, raw_hash in (
            ("baseline", "run-cpu-baseline", 10, "3" * 64),
            ("current", "run-cpu-current", 20, "4" * 64),
        ):
            db.add(
                CommandSnapshot(
                    id=f"snapshot-memory-{suffix}",
                    run_id=run_id,
                    inventory_device_id="device-1",
                    device_id="device-1",
                    execution_item_id=f"item-memory-{suffix}",
                    implementation_version_id="impl-cpu-v1",
                    check_item_ids_json=["system.cpu_usage"],
                    device_name="core-1",
                    management_ip="192.0.2.1",
                    vendor="H3C",
                    model="S5130",
                    command="display memory",
                    canonical_command="display memory",
                    observed_at=datetime(
                        2026,
                        7,
                        day,
                        0,
                        0,
                        30,
                        tzinfo=timezone.utc,
                    ),
                    raw_text="Memory usage: 20%",
                    raw_text_hash=raw_hash,
                    parse_status="parsed",
                    output_status="normal",
                    snapshot_kind="full",
                    created_at=datetime(
                        2026,
                        7,
                        day,
                        0,
                        0,
                        30,
                        tzinfo=timezone.utc,
                    ),
                )
            )

    controller = RolloutCampaignService(scope)
    started = controller.start_stage(
        _freeze(controller)["id"],
        actor="operator",
        reason="verify full command periods",
        idempotency_key="start-multi-command-periods",
    )
    target = started["stage_executions"][0][
        "selection_manifest_json"
    ]["targets"][0]
    assert len(target["periods"]) == 2
    # A V2 implementation owns exactly one CLI command.  It may project that
    # command to multiple CheckItems, but it never composes facts across
    # fallback commands.
    assert [len(item["snapshot_ids"]) for item in target["periods"]] == [1, 1]
    assert len(target["snapshot_ids"]) == 2
