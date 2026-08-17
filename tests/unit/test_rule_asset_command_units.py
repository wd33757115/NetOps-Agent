from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from src.core.check_items.catalog import CheckItemCatalogRepository
from src.core.parser.policy import ParserPolicy
from src.core.rule_control.command_asset_units import (
    RuleAssetCommandUnitService,
    _backfill_url,
    _bundle_url,
    _catalog_projection_version,
    _child_stage_status,
    _generation_url,
    _latest_bundle_by_context,
    _latest_context_by_seed_for_source,
    _parser_url,
)
from src.infrastructure.db.models import (
    CommandSnapshot,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    ParserJobSample,
    ParserTemplate,
    ParserTemplateJob,
    PatrolDataset,
    PatrolRun,
    RuleAssetCommandUnitCheckItemRecord,
    RuleAssetCommandUnitRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
    SnapshotParseResult,
)
from src.infrastructure.db.postgres import get_db_session


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_catalog_projection_version_changes_for_any_active_implementation_revision():
    repository = CheckItemCatalogRepository()
    repository.bootstrap(actor="pytest-projection-fingerprint")
    catalog = repository.load_active()
    implementation = catalog.implementations[0]
    revised = replace(
        implementation,
        version=implementation.version + 1,
        version_id=f"{implementation.version_id}-revision",
        digest="f" * 64,
    )
    revised_catalog = replace(
        catalog,
        implementations=tuple(
            revised if item.version_id == implementation.version_id else item
            for item in catalog.implementations
        ),
    )

    assert revised_catalog.catalog_version == catalog.catalog_version
    assert _catalog_projection_version(revised_catalog) != _catalog_projection_version(
        catalog
    )


def test_command_unit_contexts_never_cross_offline_source_boundaries() -> None:
    contexts = [
        RuleGenerationContextRecord(
            id="context-other-new",
            job_id="seed-1",
            offline_job_id="offline-other",
            dataset_ids_json=["other"],
            context_json={},
            context_digest="digest-other-new",
            privacy_report_json={},
        ),
        RuleGenerationContextRecord(
            id="context-current-new",
            job_id="seed-1",
            offline_job_id="offline-current",
            dataset_ids_json=["current"],
            context_json={},
            context_digest="digest-current-new",
            privacy_report_json={},
        ),
        RuleGenerationContextRecord(
            id="context-current-old",
            job_id="seed-1",
            offline_job_id="offline-current",
            dataset_ids_json=["current"],
            context_json={},
            context_digest="digest-current-old",
            privacy_report_json={},
        ),
        RuleGenerationContextRecord(
            id="context-legacy-unscoped",
            job_id="seed-2",
            offline_job_id=None,
            dataset_ids_json=[],
            context_json={},
            context_digest="digest-legacy-unscoped",
            privacy_report_json={},
        ),
    ]

    selected = _latest_context_by_seed_for_source(
        contexts,
        "offline-current",
    )

    assert set(selected) == {"seed-1"}
    assert selected["seed-1"].id == "context-current-new"


def test_command_asset_actions_use_product_workspaces() -> None:
    parser_query = _query(
        _parser_url(
            "offline-1",
            "display memory",
            dataset_ids=["dataset-q2", "dataset-q3"],
        )
    )
    assert parser_query == {
        "workspace": ["production"],
        "subtab": ["asset-production-units"],
        "action": ["parser"],
        "source_job_id": ["offline-1"],
        "offline_job_id": ["offline-1"],
        "command": ["display memory"],
        "dataset_ids": ["dataset-q2,dataset-q3"],
    }

    generation_query = _query(
        _generation_url(
            "offline-1",
            "display power",
            dataset_ids=["dataset-q2", "dataset-q3"],
            seed_job_id="seed-1",
        )
    )
    assert generation_query["workspace"] == ["production"]
    assert generation_query["subtab"] == ["asset-production-units"]
    assert generation_query["action"] == ["rule-generation"]
    assert generation_query["rule_seed_job_id"] == ["seed-1"]
    assert generation_query["dataset_ids"] == ["dataset-q2,dataset-q3"]

    backfill_query = _query(
        _backfill_url(
            "offline-1",
            "display power",
            template_id="template-1",
            dataset_ids=["dataset-q3"],
        )
    )
    assert backfill_query["workspace"] == ["runtime"]
    assert backfill_query["subtab"] == ["coverage-backfill"]
    assert backfill_query["action"] == ["backfill"]

    bundle_query = _query(_bundle_url("offline-1", "bundle-1"))
    assert bundle_query["workspace"] == ["production"]
    assert bundle_query["subtab"] == ["asset-production-units"]
    assert bundle_query["action"] == ["release"]
    assert bundle_query["bundle_id"] == ["bundle-1"]


def test_check_item_projection_ignores_assets_not_required_by_profile() -> None:
    child = {
        "governance_status": "failed",
        "governance_bucket": "exception",
        "component_statuses": {
            "parser": "published",
            "contract": "published",
            "policy": "failed",
            "event": "blocked",
        },
    }
    plan = {
        "check_item": {
            "id": "device.version",
            "version_id": "check-version-v2",
        },
        "implementation": {
            "version_id": "h3c-device-version-v1",
        },
        "asset_requirement_profile": {
            "requirements": {
                "identity_policy": {"required": True},
                "parser_template": {"required": True},
                "observation_contract": {"required": True},
                "current_state_rule": {"required": False},
                "change_policy": {"required": False},
                "event_rule": {"required": False},
                "comparison_profile": {"required": False},
            },
        },
        "selected": {
            "entity_identity_policy_version_id": "identity-v1",
            "parser_template_ids": ["parser-v1"],
            "observation_contract_ids": ["contract-v1"],
            "change_policy_version_ids": [],
            "event_rule_version_ids": [],
        },
        "compatibility": {},
        "candidates": {},
        "operationally_ready": True,
        "active_bundle": None,
        "blockers": [],
        "warnings": [],
        "preview_digest": "preview-1",
    }

    projected = RuleAssetCommandUnitService._project_check_item_implementation(
        child,
        plan,
    )

    assert projected["component_statuses"] == {
        "identity": "published",
        "parser": "published",
        "contract": "published",
        "current_state": "not_applicable",
        "policy": "not_applicable",
        "event": "not_applicable",
        "comparison": "not_applicable",
        "runtime_bundle": "ready_to_generate",
    }
    assert projected["source_governance_status"] == "failed"
    assert projected["governance_status"] == "ready_to_generate"
    assert projected["governance_bucket"] == "action_required"
    assert projected["next_action"]["code"] == "assemble_runtime_bundle"
    target_query = _query(projected["next_action"]["target_url"])
    assert target_query["check_item_version_id"] == ["check-version-v2"]
    assert target_query["implementation_version_id"] == [
        "h3c-device-version-v1"
    ]


def test_check_item_projection_separates_current_state_and_change_event_rules() -> None:
    child = {
        "governance_status": "published",
        "governance_bucket": "published",
        "component_statuses": {
            "parser": "published",
            "contract": "published",
            "policy": "published",
            "event": "published",
        },
    }
    plan = {
        "check_item": {
            "id": "system.cpu_usage",
            "version_id": "check-cpu-v5",
        },
        "implementation": {
            "version_id": "h3c-cpu-v1",
        },
        "asset_requirement_profile": {
            "requirements": {
                "identity_policy": {"required": True},
                "parser_template": {"required": True},
                "observation_contract": {"required": True},
                "current_state_rule": {
                    "required": True,
                    "required_observation_types": [
                        "device.cpu.utilization"
                    ],
                },
                "change_policy": {
                    "required": True,
                    "required_observation_types": [
                        "device.cpu.utilization"
                    ],
                },
                "event_rule": {"required": True},
                "comparison_profile": {"required": False},
            },
        },
        "selected": {
            "entity_identity_policy_version_id": "identity-v1",
            "parser_template_ids": ["parser-v1"],
            "observation_contract_ids": ["contract-v1"],
            "change_policy_version_ids": ["policy-v1"],
            "event_rule_version_ids": ["event-change-v1"],
        },
        "compatibility": {
            "current_state_rule_types": [],
            "change_policy_types": ["device.cpu.utilization"],
        },
        "candidates": {
            "event_rules": [{"version_id": "event-change-v1"}],
        },
        "operationally_ready": False,
        "active_bundle": None,
        "blockers": [
            {
                "code": "current_state_event_rule_missing",
                "message": "缺少当前状态规则。",
            }
        ],
        "warnings": [],
    }

    projected = RuleAssetCommandUnitService._project_check_item_implementation(
        child,
        plan,
    )

    assert projected["component_statuses"]["current_state"] == "blocked"
    assert projected["component_statuses"]["policy"] == "published"
    assert projected["component_statuses"]["event"] == "published"
    assert projected["component_statuses"]["runtime_bundle"] == "blocked"
    assert projected["governance_status"] == "blocked"
    assert projected["governance_bucket"] == "action_required"
    assert projected["next_action"]["code"] == "inspect_runtime_bundle"


def test_check_item_projection_keeps_upstream_parser_action_before_runtime_bundle() -> None:
    child = {
        "governance_status": "ready_to_generate",
        "governance_bucket": "action_required",
        "current_stage": "parser_generation",
        "current_stage_label": "Parser 生成",
        "next_action": {
            "code": "open_parser_job",
            "label": "生成 Parser",
            "target_url": "/skills/parser?action=parser&command=show+interface+status",
        },
        "component_statuses": {
            "parser": "ready_to_generate",
            "contract": "not_started",
        },
    }
    plan = {
        "check_item": {
            "id": "interface.oper_status",
            "version_id": "check-interface-v1",
        },
        "implementation": {
            "version_id": "cisco-interface-status-v1",
        },
        "asset_requirement_profile": {
            "requirements": {
                "identity_policy": {"required": True},
                "parser_template": {"required": True},
                "observation_contract": {"required": True},
                "current_state_rule": {"required": False},
                "change_policy": {"required": False},
                "event_rule": {"required": False},
                "comparison_profile": {"required": False},
            },
        },
        "selected": {"entity_identity_policy_version_id": "identity-v1"},
        "compatibility": {},
        "candidates": {},
        "operationally_ready": False,
        "active_bundle": None,
        "blockers": [
            {
                "code": "parser_template_missing",
                "message": "missing Parser template",
            }
        ],
        "warnings": [],
    }

    projected = RuleAssetCommandUnitService._project_check_item_implementation(
        child,
        plan,
    )

    assert projected["next_action"] == child["next_action"]
    assert projected["current_stage"] == "parser_generation"
    assert projected["current_stage_label"] == "Parser 生成"


def test_catalog_binding_overrides_legacy_ignored_mode_for_non_empty_output() -> None:
    unit = {
        "handling": "not_applicable",
        "asset_mode": "ignored",
        "has_non_empty_output": True,
    }
    catalog = SimpleNamespace(
        check_items=[
            SimpleNamespace(id="network.arp_table", result_mode="structured")
        ]
    )

    RuleAssetCommandUnitService._apply_catalog_asset_mode(
        unit,
        [{"check_item_id": "network.arp_table"}],
        catalog=catalog,
    )

    assert unit["handling"] == "structured"
    assert unit["asset_mode"] == "full_chain"


def test_catalog_binding_keeps_confirmed_empty_output_not_applicable() -> None:
    unit = {
        "handling": "not_applicable",
        "asset_mode": "ignored",
        "has_non_empty_output": False,
    }
    catalog = SimpleNamespace(
        check_items=[
            SimpleNamespace(id="redundancy.mlag", result_mode="structured")
        ]
    )

    RuleAssetCommandUnitService._apply_catalog_asset_mode(
        unit,
        [{"check_item_id": "redundancy.mlag"}],
        catalog=catalog,
    )

    assert unit["handling"] == "not_applicable"
    assert unit["asset_mode"] == "ignored"


def test_mapped_confirmed_empty_output_does_not_require_runtime_assets() -> None:
    child = {
        "handling": "not_applicable",
        "asset_mode": "ignored",
        "governance_status": "not_applicable",
        "governance_bucket": "not_applicable",
        "component_statuses": {},
    }
    plan = {
        "asset_requirement_profile": {"requirements": {}},
        "blockers": [{"code": "parser_template_missing"}],
        "preview_digest": "preview-empty",
    }

    projected = RuleAssetCommandUnitService._project_check_item_implementation(
        child,
        plan,
    )

    assert projected["governance_status"] == "not_applicable"
    assert projected["governance_bucket"] == "not_applicable"
    assert projected["blocker_count"] == 0
    assert projected["next_action"] is None
    assert set(projected["component_statuses"].values()) == {"not_applicable"}


def test_unclassified_ignored_commands_do_not_create_false_exceptions() -> None:
    child = {
        "id": "ignored-command",
        "governance_status": "not_applicable",
        "governance_bucket": "not_applicable",
        "current_stage": "release",
        "current_stage_label": "not applicable",
        "progress_percent": 100,
        "blocker_count": 0,
        "sample_count": 2,
        "device_count": 1,
        "output_signature_count": 1,
        "canonical_command": "show clock",
        "vendor": "Cisco",
        "profile": "N9K-C9364C",
        "component_statuses": {
            component: "not_applicable"
            for component in (
                "identity",
                "parser",
                "contract",
                "current_state",
                "policy",
                "event",
                "comparison",
                "runtime_bundle",
            )
        },
        "next_action": None,
    }

    result = RuleAssetCommandUnitService._rollup_check_item_group(
        "__unclassified__",
        {"check_item_id": None, "implementations": [child]},
    )

    assert result["governance_status"] == "not_applicable"
    assert result["governance_bucket"] == "not_applicable"
    assert result["blocker_count"] == 0
    assert result["next_action"] is None


def _snapshot(
    *,
    suffix: str,
    run_id: str,
    dataset_id: str,
    ordinal: int,
    vendor: str,
    model: str,
    command: str,
    device_id: str,
    device_name: str | None = None,
    management_ip: str | None = None,
    parse_status: str = "template_missing",
    output_status: str = "content",
) -> CommandSnapshot:
    raw_text = f"{device_id}\n{command}\noutput-{ordinal}"
    return CommandSnapshot(
        id=f"snapshot-command-unit-{suffix}-{ordinal}",
        run_id=run_id,
        dataset_id=dataset_id,
        device_id=device_id,
        execution_item_id=f"execution-{suffix}-{ordinal}",
        device_name=device_name or device_id,
        management_ip=management_ip,
        vendor=vendor,
        model=model,
        command=command,
        canonical_command=command,
        observed_at=datetime.now(timezone.utc),
        structured_json=None,
        raw_text=raw_text,
        raw_text_hash=hashlib.sha256(raw_text.encode()).hexdigest(),
        parse_status=parse_status,
        output_status=output_status,
    )


def test_command_units_deduplicate_evidence_and_keep_asset_boundaries():
    suffix = uuid.uuid4().hex[:10]
    job_id = f"offline-command-unit-{suffix}"
    dataset_id = f"dataset-command-unit-{suffix}"
    run_id = f"run-command-unit-{suffix}"
    with get_db_session() as db:
        db.add(
            PatrolDataset(
                id=dataset_id,
                name="Command unit test",
                mode="parser_validation",
                source="imported_file",
            )
        )
        db.flush()
        db.add(
            PatrolRun(
                id=run_id,
                mode="parser_validation",
                source="imported_file",
                dataset_id=dataset_id,
                status="completed",
                logical_run_id=run_id,
                device_count=3,
                command_count=4,
            )
        )
        db.flush()
        db.add(
            OfflinePatrolJobRecord(
                id=job_id,
                idempotency_key=f"idempotency-command-unit-{suffix}",
                purpose="rule_asset_source",
                job_type="parser_validation",
                name="Command unit test",
                owner_id="pytest",
                status="completed",
                progress_json={},
            )
        )
        db.add(
            OfflinePatrolPeriodRecord(
                id=f"period-command-unit-{suffix}",
                job_id=job_id,
                role="sample",
                label="Sample",
                observed_at=datetime.now(timezone.utc),
                dataset_id=dataset_id,
                patrol_run_id=run_id,
                status="completed",
            )
        )
        db.flush()
        db.add_all(
            [
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=1,
                    vendor="H3C",
                    model="S6520",
                    command="display interface brief",
                    device_id="SW1",
                    management_ip="10.0.0.1",
                ),
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=2,
                    vendor="H3C",
                    model="S6520",
                    command="display interface brief",
                    device_id="SW2",
                    management_ip="10.0.0.2",
                ),
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=3,
                    vendor="Huawei",
                    model="CE6865",
                    command="display interface brief",
                    device_id="SW3",
                ),
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=4,
                    vendor="H3C",
                    model="S6520",
                    command="show running-config",
                    device_id="SW1",
                ),
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=5,
                    vendor="H3C",
                    model="S6520",
                    command="display interface brief",
                    device_id="sample-period-current-SW1",
                    device_name="SW1",
                    management_ip="10.0.0.1",
                ),
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=6,
                    vendor="H3C",
                    model="UnsupportedModel",
                    command="display m-lag system",
                    device_id="unsupported-device",
                    parse_status="not_applicable",
                    output_status="unsupported_command",
                ),
            ]
        )

    service = RuleAssetCommandUnitService()
    try:
        first = service.reconcile(job_id)
        second = service.reconcile(job_id)
        assert first["unit_count"] == 4
        assert second["unit_count"] == 4

        interface_page = service.list_units(
            job_id,
            search="interface brief",
            page=1,
            page_size=1,
        )
        assert interface_page["total"] == 2
        assert interface_page["page_count"] == 2

        all_units = service.list_units(job_id, page=1, page_size=10)
        assert all_units["total"] == 4
        h3c_interface = next(
            item
            for item in all_units["items"]
            if item["vendor"] == "H3C"
            and item["canonical_command"] == "display interface brief"
        )
        assert h3c_interface["sample_count"] == 3
        assert h3c_interface["device_count"] == 2
        assert h3c_interface["asset_mode"] == "full_chain"
        assert h3c_interface["current_stage"] == "parser_generation"

        raw_hash = next(
            item
            for item in all_units["items"]
            if item["canonical_command"] == "show running-config"
        )
        assert raw_hash["asset_mode"] == "raw_hash"
        assert raw_hash["status"] == "completed"
        assert raw_hash["stage_statuses"]["event_rule"] == "skipped"

        unsupported = next(
            item
            for item in all_units["items"]
            if item["canonical_command"] == "display m-lag system"
        )
        assert unsupported["profile"] == "UnsupportedModel"
        assert unsupported["handling"] == "not_applicable"
        assert unsupported["asset_mode"] == "ignored"
        assert unsupported["status"] == "completed"

        detail = service.get_unit(job_id, h3c_interface["id"])
        assert len(detail["stages"]) == 10
        assert len(detail["evidence"]) == 3
        assert all("raw_text" not in item for item in detail["evidence"])
        visible_actions = [
            stage["next_action"]
            for stage in detail["stages"]
            if stage.get("next_action")
        ]
        assert visible_actions == [detail["next_action"]]
        assert detail["next_action"]["code"] == "open_parser_job"
        assert f"source_job_id={job_id}" in detail["next_action"]["target_url"]
        paged_detail = service.get_unit(
            job_id,
            h3c_interface["id"],
            evidence_page=2,
            evidence_page_size=2,
        )
        assert len(paged_detail["evidence"]) == 1
        assert paged_detail["evidence_pagination"] == {
            "page": 2,
            "page_size": 2,
            "total": 3,
            "page_count": 2,
        }

        with get_db_session() as db:
            assert (
                db.query(RuleAssetCommandUnitRecord)
                .filter(RuleAssetCommandUnitRecord.offline_job_id == job_id)
                .count()
                == 4
            )
    finally:
        with get_db_session() as db:
            db.query(RuleAssetCommandUnitRecord).filter(
                RuleAssetCommandUnitRecord.offline_job_id == job_id
            ).delete(synchronize_session=False)
            db.query(CommandSnapshot).filter(CommandSnapshot.run_id == run_id).delete(
                synchronize_session=False
            )
            db.query(OfflinePatrolPeriodRecord).filter(
                OfflinePatrolPeriodRecord.job_id == job_id
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolJobRecord).filter(OfflinePatrolJobRecord.id == job_id).delete(
                synchronize_session=False
            )
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(synchronize_session=False)
            db.query(PatrolDataset).filter(PatrolDataset.id == dataset_id).delete(
                synchronize_session=False
            )


def test_parser_job_resolution_never_reuses_an_unmatched_model_profile():
    candidate = SimpleNamespace(model="S5130S", profile="S5130S")

    resolved = RuleAssetCommandUnitService._resolve_parser_job(
        {
            "vendor": "H3C",
            "model": "MSR5620",
            "command": "display interface brief",
        },
        {("h3c", "display interface brief"): [candidate]},
    )

    assert resolved is None


def test_collecting_parser_job_is_actionable_not_reported_as_running():
    service = RuleAssetCommandUnitService()
    stages = service._build_stages(
        offline_job_id="offline-parser-config",
        dataset_ids=["dataset-parser-config"],
        unit={
            "command": "show interface status",
            "vendor": "Cisco",
            "profile": "N9K-C93180YC-FX",
            "models": {"N9K-C93180YC-FX"},
            "device_count": 1,
            "sample_count": 1,
            "parse_counts": {"template_missing": 1},
            "structured_count": 0,
            "asset_mode": "full_chain",
        },
        parser_job=SimpleNamespace(
            id="parser-job-config",
            status="collecting",
            template_attempt_count=0,
            sample_count=1,
            last_error=None,
            failure_code="template_missing",
        ),
        template=None,
        active_contract=None,
        seed=None,
        context=None,
        bundle=None,
        children=[],
        output_signature_count=1,
    )

    parser_stage = next(item for item in stages if item["key"] == "parser_generation")
    assert parser_stage["status"] == "ready"
    assert parser_stage["next_action"]["code"] == "open_parser_job"
    assert "source_job_id=offline-parser-config" in parser_stage["next_action"]["target_url"]
    assert "offline_job_id=offline-parser-config" in parser_stage["next_action"]["target_url"]
    assert parser_stage["next_action"]["label"] == "配置并生成 Parser"


def test_rejected_bundle_blocks_child_assets_and_surfaces_recovery_action():
    children = [
        SimpleNamespace(
            asset_type="CHANGE_POLICY",
            status="pending_approval",
            validation_report_json={"success": True},
        )
    ]

    status, matching = _child_stage_status(
        children,
        "CHANGE_POLICY",
        bundle_status="rejected",
    )
    assert status == "blocked"
    assert matching == children

    stages = [
        {"key": "change_policy", "status": "needs_review", "next_action": None},
        {
            "key": "validation_review",
            "status": "blocked",
            "next_action": {"code": "review_bundle"},
        },
        {"key": "release", "status": "blocked", "next_action": None},
    ]
    current, overall, _progress, next_action = RuleAssetCommandUnitService._rollup(stages)

    assert current == "validation_review"
    assert overall == "blocked"
    assert next_action == {"code": "review_bundle"}


def test_latest_bundle_selection_uses_immutable_creation_order():
    context_id = "context-revision-selection"
    parent = SimpleNamespace(
        id="bundle-parent",
        created_at=datetime(2026, 7, 24, 1, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 24, 3, 0, tzinfo=timezone.utc),
        payload_json={"context_id": context_id},
    )
    replacement = SimpleNamespace(
        id="bundle-replacement",
        created_at=datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
        payload_json={
            "context_id": context_id,
            "engineering_revision": {"parent_bundle_id": parent.id},
        },
    )

    selected = _latest_bundle_by_context([parent, replacement])

    assert selected[context_id] is replacement


def test_command_unit_projection_uses_latest_successful_reparse_as_effective_state():
    suffix = uuid.uuid4().hex[:10]
    job_id = f"offline-reparse-unit-{suffix}"
    dataset_id = f"dataset-reparse-unit-{suffix}"
    run_id = f"run-reparse-unit-{suffix}"
    snapshot_id = f"snapshot-command-unit-{suffix}-1"
    template_id = f"template-reparse-unit-{suffix}"
    command = "display interface brief"
    model = f"LAB-{suffix}"
    with get_db_session() as db:
        db.add(
            PatrolDataset(
                id=dataset_id,
                name="Reparse command unit test",
                mode="parser_validation",
                source="imported_file",
            )
        )
        db.flush()
        db.add(
            PatrolRun(
                id=run_id,
                mode="parser_validation",
                source="imported_file",
                dataset_id=dataset_id,
                status="completed",
                logical_run_id=run_id,
                device_count=1,
                command_count=1,
            )
        )
        db.add(
            OfflinePatrolJobRecord(
                id=job_id,
                idempotency_key=f"idempotency-reparse-unit-{suffix}",
                purpose="rule_asset_source",
                job_type="parser_validation",
                name="Reparse command unit test",
                owner_id="pytest",
                status="completed",
                progress_json={},
            )
        )
        db.add(
            OfflinePatrolPeriodRecord(
                id=f"period-reparse-unit-{suffix}",
                job_id=job_id,
                role="sample",
                label="Sample",
                observed_at=datetime.now(timezone.utc),
                dataset_id=dataset_id,
                patrol_run_id=run_id,
                status="completed",
            )
        )
        db.add(
            ParserTemplate(
                id=template_id,
                vendor="H3C",
                model=model,
                family="Comware7",
                profile=model,
                canonical_command=command,
                category="interface_l3",
                version="v1",
                asset_path=f"pytest/{template_id}.textfsm",
                sha256=hashlib.sha256(template_id.encode()).hexdigest(),
                status="active",
                validation_json={
                    "entity_type": "interface_l3",
                    "primary_keys": ["interface"],
                    "regression_validation": {
                        "passed": True,
                        "sample_count": 1,
                        "passed_samples": 1,
                    },
                },
            )
        )
        db.flush()
        db.add(
            _snapshot(
                suffix=suffix,
                run_id=run_id,
                dataset_id=dataset_id,
                ordinal=1,
                vendor="H3C",
                model=model,
                command=command,
                device_id="SW1",
            )
        )
        db.flush()
        db.add(
            SnapshotParseResult(
                id=f"parse-result-reparse-unit-{suffix}",
                snapshot_id=snapshot_id,
                template_id=template_id,
                template_version="v1",
                parse_status="success",
                structured_json=[{"interface": "GE1/0/1", "status": "UP"}],
                parsed_at=datetime.now(timezone.utc),
            )
        )

    service = RuleAssetCommandUnitService()
    try:
        service.reconcile(job_id)
        unit = service.list_units(job_id, page=1, page_size=10)["items"][0]
        assert unit["parse_counts"] == {"success": 1}
        assert unit["output_signature_count"] == 1
        assert unit["stage_statuses"]["structured_quality"] == "passed"
        assert unit["current_stage"] == "observation_contract"
        assert unit["rule_seed_job_id"]
        assert unit["next_action"]["code"] == "generate_rule_asset_bundle"
        assert unit["next_action"]["label"] == "生成完整规则资产包"

        service.reconcile(job_id)
        with get_db_session() as db:
            seeds = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.job_type == "OBSERVATION_CONTRACT",
                    RuleGenerationJobRecord.source_ref == f"parser_template:{template_id}",
                )
                .all()
            )
            assert len(seeds) == 1

        detail = service.get_unit(job_id, unit["id"])
        assert len(detail["evidence"]) == 1
        evidence = detail["evidence"][0]
        assert evidence["parse_status"] == "success"
        assert evidence["original_parse_status"] == "template_missing"
        assert evidence["parse_source"] == "reparse"
        assert evidence["parser_template_id"] == template_id
        assert evidence["structured_record_count"] == 1
        contract_stage = next(
            item for item in detail["stages"] if item["key"] == "observation_contract"
        )
        assert contract_stage["status"] == "ready"
        assert f"rule_seed_job_id={unit['rule_seed_job_id']}" in (
            contract_stage["next_action"]["target_url"]
        )
        assert "workspace=production" in contract_stage["next_action"]["target_url"]
        assert "action=rule-generation" in contract_stage["next_action"]["target_url"]

        with get_db_session() as db:
            stale = db.get(RuleAssetCommandUnitRecord, unit["id"])
            stale.projection_version = 2
            stale.next_action_json = {
                "code": "generate_policy",
                "label": "生成并修订 Policy",
                "target_url": "/stale",
            }
        refreshed = service.list_units(job_id, page=1, page_size=10)["items"][0]
        expected_projection_version = _catalog_projection_version(
            CheckItemCatalogRepository().load_active()
        )
        assert refreshed["projection_version"] == expected_projection_version
        assert refreshed["next_action"]["code"] == "generate_rule_asset_bundle"
        assert refreshed["next_action"]["label"] == "生成完整规则资产包"
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.source_ref == f"parser_template:{template_id}"
            ).delete(synchronize_session=False)
            db.query(RuleAssetCommandUnitRecord).filter_by(offline_job_id=job_id).delete()
            db.query(SnapshotParseResult).filter_by(snapshot_id=snapshot_id).delete()
            db.query(CommandSnapshot).filter_by(id=snapshot_id).delete()
            db.query(OfflinePatrolPeriodRecord).filter_by(job_id=job_id).delete()
            db.query(OfflinePatrolJobRecord).filter_by(id=job_id).delete()
            db.query(PatrolRun).filter_by(id=run_id).delete()
            db.query(PatrolDataset).filter_by(id=dataset_id).delete()
            db.query(ParserTemplate).filter_by(id=template_id).delete()


def test_parser_job_state_changes_refresh_linked_offline_command_units():
    suffix = uuid.uuid4().hex[:10]
    offline_job_id = f"offline-parser-refresh-{suffix}"
    parser_job_id = f"parser-refresh-{suffix}"
    dataset_id = f"dataset-parser-refresh-{suffix}"
    run_id = f"run-parser-refresh-{suffix}"
    snapshot_id = f"snapshot-command-unit-parser-refresh-{suffix}-1"
    command = "display interface brief"
    with get_db_session() as db:
        db.add(
            PatrolDataset(
                id=dataset_id,
                name="Parser projection refresh test",
                mode="parser_validation",
                source="imported_file",
            )
        )
        db.add(
            PatrolRun(
                id=run_id,
                mode="parser_validation",
                source="imported_file",
                dataset_id=dataset_id,
                status="completed",
                logical_run_id=run_id,
                device_count=1,
                command_count=1,
            )
        )
        db.add(
            OfflinePatrolJobRecord(
                id=offline_job_id,
                idempotency_key=f"idempotency-parser-refresh-{suffix}",
                purpose="rule_asset_source",
                job_type="parser_validation",
                name="Parser projection refresh test",
                owner_id="pytest",
                status="completed",
                progress_json={},
            )
        )
        db.add(
            OfflinePatrolPeriodRecord(
                id=f"period-parser-refresh-{suffix}",
                job_id=offline_job_id,
                role="sample",
                label="Sample",
                observed_at=datetime.now(timezone.utc),
                dataset_id=dataset_id,
                patrol_run_id=run_id,
                status="completed",
            )
        )
        db.flush()
        db.add(
            _snapshot(
                suffix=f"parser-refresh-{suffix}",
                run_id=run_id,
                dataset_id=dataset_id,
                ordinal=1,
                vendor="H3C",
                model="S6520",
                command=command,
                device_id="SW1",
            )
        )

    service = RuleAssetCommandUnitService()
    try:
        service.reconcile(offline_job_id)
        initial = service.list_units(offline_job_id, page=1, page_size=10)["items"][0]
        assert initial["parser_job_id"]
        parser_job_id = initial["parser_job_id"]

        with get_db_session() as db:
            parser_job = db.get(ParserTemplateJob, parser_job_id)
            assert parser_job is not None
            parser_job.status = "failed"
            parser_job.parser_spec_status = "confirmed"
            parser_job.failure_code = "generation_validation_failed"
            parser_job.last_error = "candidate did not pass validation"

        failed_refresh = service.reconcile_for_parser_job(parser_job_id)
        assert failed_refresh["offline_job_ids"] == [offline_job_id]
        failed = service.list_units(offline_job_id, page=1, page_size=10)["items"][0]
        assert failed["parser_job_id"] == parser_job_id
        assert failed["stage_statuses"]["parser_generation"] == "failed"

        with get_db_session() as db:
            parser_job = db.get(ParserTemplateJob, parser_job_id)
            parser_job.status = "succeeded"
            parser_job.failure_code = None
            parser_job.last_error = None
            parser_job.updated_at = datetime.now(timezone.utc)

        succeeded_refresh = service.reconcile_for_parser_job(parser_job_id)
        assert succeeded_refresh["reconciled_count"] == 1
        succeeded = service.list_units(
            offline_job_id,
            page=1,
            page_size=10,
        )["items"][0]
        assert succeeded["stage_statuses"]["parser_generation"] == "needs_review"
        assert succeeded["stage_statuses"]["parser_validation"] == "needs_review"
        assert succeeded["status"] == "needs_review"
    finally:
        with get_db_session() as db:
            db.query(RuleAssetCommandUnitRecord).filter_by(
                offline_job_id=offline_job_id
            ).delete()
            db.query(ParserJobSample).filter_by(job_id=parser_job_id).delete()
            db.query(ParserTemplateJob).filter_by(id=parser_job_id).delete()
            db.query(CommandSnapshot).filter_by(id=snapshot_id).delete()
            db.query(OfflinePatrolPeriodRecord).filter_by(
                job_id=offline_job_id
            ).delete()
            db.query(OfflinePatrolJobRecord).filter_by(id=offline_job_id).delete()
            db.query(PatrolRun).filter_by(id=run_id).delete()
            db.query(PatrolDataset).filter_by(id=dataset_id).delete()


class _PolicyRepository:
    @staticmethod
    def list_command_aliases():
        return []


def test_parser_policy_preserves_ios_env_all_and_expands_known_env_aliases():
    policy = ParserPolicy(repository=_PolicyRepository())

    assert policy.normalize_command("show env all") == "show env all"
    assert policy.normalize_command("show environment all") == "show env all"
    assert policy.normalize_command("show env fan") == "show environment fan"
    assert policy.normalize_command("show env power") == "show environment power"
    assert policy.normalize_command("show env temperature") == (
        "show environment temperature"
    )


def test_parser_policy_exposes_all_governed_semantic_asset_modes(tmp_path):
    policy_path = tmp_path / "command_policy.yaml"
    policy_path.write_text(
        """
commands:
  show inventory: {enabled: true, asset_mode: observation_only}
  show version: {enabled: true, asset_mode: change_only}
  show interface status: {enabled: true, asset_mode: full_chain}
  show running-config: {enabled: false, handling: raw_hash_diff, asset_mode: raw_hash}
  show clock: {enabled: false, handling: raw_only, asset_mode: ignored}
""".strip(),
        encoding="utf-8",
    )
    policy = ParserPolicy(policy_path=policy_path, repository=_PolicyRepository())

    def asset_mode(command: str) -> str:
        return policy.decide(
            vendor="Cisco",
            model="C9300",
            family=None,
            command=command,
        ).asset_mode

    assert asset_mode("show inventory") == "observation_only"
    assert asset_mode("show version") == "change_only"
    assert asset_mode("show interface status") == "full_chain"
    assert asset_mode("show running-config") == "raw_hash"
    assert asset_mode("show clock") == "ignored"


def test_parser_policy_rejects_asset_modes_that_conflict_with_handling(tmp_path):
    policy_path = tmp_path / "invalid-command-policy.yaml"
    policy_path.write_text(
        "commands:\n  show clock: {enabled: false, handling: raw_only, asset_mode: full_chain}\n",
        encoding="utf-8",
    )
    policy = ParserPolicy(policy_path=policy_path, repository=_PolicyRepository())

    with pytest.raises(ValueError, match="requires structured handling"):
        policy.decide(vendor="Cisco", model="C9300", family=None, command="show clock")


@pytest.mark.parametrize(
    ("asset_mode", "policy_status", "event_status"),
    [
        ("observation_only", "skipped", "skipped"),
        ("change_only", "not_started", "skipped"),
        ("full_chain", "not_started", "not_started"),
    ],
)
def test_command_unit_stages_respect_semantic_asset_mode(
    asset_mode: str,
    policy_status: str,
    event_status: str,
):
    unit = {
        "vendor": "Cisco",
        "profile": "C9300",
        "models": {"C9300"},
        "command": "show inventory",
        "asset_mode": asset_mode,
        "sample_count": 2,
        "device_count": 1,
        "structured_count": 2,
        "parse_counts": {"parsed": 2},
    }

    stages = RuleAssetCommandUnitService()._build_stages(
        offline_job_id="offline-stage-mode-test",
        unit=unit,
        parser_job=None,
        template=None,
        active_contract=None,
        seed=None,
        context=None,
        bundle=None,
        children=[],
        output_signature_count=1,
    )
    by_key = {stage["key"]: stage["status"] for stage in stages}

    assert by_key["change_policy"] == policy_status
    assert by_key["event_rule"] == event_status


def test_command_unit_uses_bundle_requirements_instead_of_legacy_full_chain_mode():
    requirements = {
        "schema_version": "rule-asset-requirements/v1",
        "requires_observation_contract": True,
        "requires_change_policy": False,
        "requires_current_state_rule": False,
        "requires_event_rule": False,
        "requires_event_rule_assets": False,
        "requires_historical_replay": False,
    }
    unit = {
        "vendor": "Cisco",
        "profile": "WS-C2960XR-24TS-I",
        "models": {"WS-C2960XR-24TS-I"},
        "command": "show ip interface brief",
        "asset_mode": "full_chain",
        "sample_count": 2,
        "device_count": 1,
        "structured_count": 118,
        "parse_counts": {"parsed": 2},
    }
    bundle = SimpleNamespace(
        id="bundle-contract-only",
        status="active",
        payload_json={"asset_requirements": requirements},
        validation_report_json={
            "success": True,
            "bundle_gate": {
                "success": True,
                "asset_requirements": requirements,
            },
            "context_gate": {"success": True},
            "historical_replay": None,
        },
    )

    stages = RuleAssetCommandUnitService()._build_stages(
        offline_job_id="offline-contract-only",
        unit=unit,
        parser_job=None,
        template=SimpleNamespace(id="template-contract-only", status="active"),
        active_contract=SimpleNamespace(id="contract-active"),
        seed=None,
        context=None,
        bundle=bundle,
        children=[
            SimpleNamespace(
                id="contract-child",
                asset_type="OBSERVATION_CONTRACT",
                status="active",
                validation_report_json={"success": True},
            )
        ],
        output_signature_count=1,
    )
    by_key = {stage["key"]: stage["status"] for stage in stages}
    current, overall, progress, next_action = RuleAssetCommandUnitService._rollup(stages)

    assert unit["asset_mode"] == "observation_only"
    assert by_key["change_policy"] == "skipped"
    assert by_key["event_rule"] == "skipped"
    assert by_key["validation_review"] == "passed"
    assert by_key["release"] == "passed"
    assert (current, overall, progress, next_action) == ("release", "completed", 100, None)


def test_product_command_asset_list_deduplicates_latest_units_and_enforces_owner_scope():
    suffix = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc)
    owner_id = f"user:owner-{suffix}"
    other_owner_id = f"user:other-{suffix}"
    job_ids = [
        f"offline-product-old-{suffix}",
        f"offline-product-new-{suffix}",
        f"offline-product-other-{suffix}",
    ]
    unit_ids = [
        f"command-product-old-{suffix}",
        f"command-product-new-{suffix}",
        f"command-product-other-{suffix}",
    ]
    shared_asset_key = f"asset-shared-{suffix}"

    def command_unit(
        *,
        unit_id: str,
        job_id: str,
        asset_key: str,
        command: str,
        status: str,
        current_stage: str,
        updated_at: datetime,
    ) -> RuleAssetCommandUnitRecord:
        return RuleAssetCommandUnitRecord(
            id=unit_id,
            offline_job_id=job_id,
            asset_key=asset_key,
            vendor="H3C",
            model="S5130S",
            models_json=["S5130S"],
            profile="S5130S",
            canonical_command=command,
            handling="structured",
            asset_mode="full_chain",
            status=status,
            current_stage=current_stage,
            progress_percent=20,
            sample_count=1,
            device_count=1,
            output_signature_count=1,
            blocker_count=0,
            parse_counts_json={"parsed": 1},
            stage_states_json=[
                {"key": "parser_generation", "status": "passed"},
                {"key": "observation_contract", "status": "ready"},
                {"key": "change_policy", "status": "not_started"},
                {"key": "event_rule", "status": "not_started"},
            ],
            next_action_json={"code": "generate", "label": "生成规则", "target_url": "/next"},
            projection_version=3,
            created_at=updated_at,
            updated_at=updated_at,
        )

    with get_db_session() as db:
        db.add_all(
            [
                OfflinePatrolJobRecord(
                    id=job_ids[0],
                    idempotency_key=f"idempotency-{job_ids[0]}",
                    purpose="rule_asset_source",
                    job_type="parser_validation",
                    name=f"Older source {suffix}",
                    owner_id=owner_id,
                    status="completed",
                    progress_json={},
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                ),
                OfflinePatrolJobRecord(
                    id=job_ids[1],
                    idempotency_key=f"idempotency-{job_ids[1]}",
                    purpose="rule_asset_source",
                    job_type="parser_validation",
                    name=f"Latest source {suffix}",
                    owner_id=owner_id,
                    status="completed",
                    progress_json={},
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
                OfflinePatrolJobRecord(
                    id=job_ids[2],
                    idempotency_key=f"idempotency-{job_ids[2]}",
                    purpose="rule_asset_source",
                    job_type="parser_validation",
                    name=f"Other owner source {suffix}",
                    owner_id=other_owner_id,
                    status="completed",
                    progress_json={},
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                command_unit(
                    unit_id=unit_ids[0],
                    job_id=job_ids[0],
                    asset_key=shared_asset_key,
                    command="display memory",
                    status="blocked",
                    current_stage="parser_generation",
                    updated_at=now - timedelta(days=2),
                ),
                command_unit(
                    unit_id=unit_ids[1],
                    job_id=job_ids[1],
                    asset_key=shared_asset_key,
                    command="display memory",
                    status="needs_review",
                    current_stage="parser_validation",
                    updated_at=now - timedelta(days=1),
                ),
                command_unit(
                    unit_id=unit_ids[2],
                    job_id=job_ids[2],
                    asset_key=f"asset-other-{suffix}",
                    command="display interface brief",
                    status="completed",
                    current_stage="release",
                    updated_at=now,
                ),
            ]
        )

    service = RuleAssetCommandUnitService()
    try:
        owner_page = service.list_production_units(
            owner_id=owner_id,
            page=1,
            page_size=10,
        )
        assert owner_page["total"] == 1
        assert owner_page["items"][0]["id"] == unit_ids[1]
        assert owner_page["items"][0]["governance_status"] == "engineering_review"
        assert owner_page["items"][0]["governance_bucket"] == "awaiting_approval"
        assert owner_page["items"][0]["component_statuses"]["parser"] == "published"
        assert owner_page["summary"]["awaiting_approval"] == 1

        filtered = service.list_production_units(
            owner_id=owner_id,
            bucket="awaiting_approval",
            page=1,
            page_size=10,
        )
        assert filtered["total"] == 1
        model_filtered = service.list_production_units(
            owner_id=owner_id,
            model="S5130S",
            page=1,
            page_size=10,
        )
        assert model_filtered["total"] == 1
        model_miss = service.list_production_units(
            owner_id=owner_id,
            model="S5130S-NOT-PRESENT",
            page=1,
            page_size=10,
        )
        assert model_miss["total"] == 0

        admin_page = service.list_production_units(
            owner_id=owner_id,
            is_admin=True,
            page=1,
            page_size=10,
            search=suffix,
        )
        assert admin_page["total"] == 2
        assert any(
            item["governance_status"] == "published"
            for item in admin_page["items"]
        )
    finally:
        with get_db_session() as db:
            db.query(RuleAssetCommandUnitRecord).filter(
                RuleAssetCommandUnitRecord.id.in_(unit_ids)
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.id.in_(job_ids)
            ).delete(synchronize_session=False)


def test_check_item_workbench_groups_catalog_implementations_and_blocks_unclassified():
    suffix = uuid.uuid4().hex[:10]
    owner_id = f"user:check-item-owner-{suffix}"
    job_id = f"offline-check-item-production-{suffix}"
    memory_unit_id = f"command-memory-{suffix}"
    unknown_unit_id = f"command-unclassified-{suffix}"
    ignored_unit_id = f"command-ignored-{suffix}"
    now = datetime.now(timezone.utc)
    catalog_repository = CheckItemCatalogRepository()
    catalog_repository.bootstrap(actor="pytest-check-item-workbench")
    catalog = catalog_repository.load_active()
    projection_version = _catalog_projection_version(catalog)
    memory_item = catalog.check_item("system.memory_usage")
    memory_implementation = next(
        item for item in catalog.implementations if item.id == "h3c-memory-usage"
    )
    assert memory_item is not None

    def unit(
        *,
        unit_id: str,
        asset_key: str,
        command: str,
        asset_mode: str = "full_chain",
    ) -> RuleAssetCommandUnitRecord:
        return RuleAssetCommandUnitRecord(
            id=unit_id,
            offline_job_id=job_id,
            asset_key=asset_key,
            vendor="H3C",
            model="S5130S",
            models_json=["S5130S"],
            profile="S5130S",
            canonical_command=command,
            handling="structured",
            asset_mode=asset_mode,
            status="completed",
            current_stage="release",
            progress_percent=100,
            sample_count=2,
            device_count=2,
            output_signature_count=1,
            blocker_count=0,
            parse_counts_json={"parsed": 2},
            stage_states_json=[
                {"key": "parser_generation", "status": "passed"},
                {"key": "observation_contract", "status": "passed"},
                {"key": "change_policy", "status": "passed"},
                {"key": "event_rule", "status": "passed"},
                {"key": "release", "status": "passed"},
            ],
            next_action_json=None,
            projection_version=projection_version,
            created_at=now,
            updated_at=now,
        )

    with get_db_session() as db:
        db.add(
            OfflinePatrolJobRecord(
                id=job_id,
                idempotency_key=f"idempotency-{job_id}",
                purpose="rule_asset_source",
                job_type="parser_validation",
                name=f"Q3 检查项样本 {suffix}",
                owner_id=owner_id,
                status="completed",
                progress_json={},
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add_all(
            [
                unit(
                    unit_id=memory_unit_id,
                    asset_key=f"asset-memory-{suffix}",
                    command="display memory",
                ),
                unit(
                    unit_id=unknown_unit_id,
                    asset_key=f"asset-unknown-{suffix}",
                    command="display vendor-special-state",
                ),
                unit(
                    unit_id=ignored_unit_id,
                    asset_key=f"asset-ignored-{suffix}",
                    command="screen-length disable",
                    asset_mode="ignored",
                ),
            ]
        )
        db.flush()
        db.add(
            RuleAssetCommandUnitCheckItemRecord(
                id=f"unit-check-link-{suffix}",
                command_unit_id=memory_unit_id,
                check_item_id=memory_item.id,
                check_item_version_id=memory_item.version_id,
                implementation_id=memory_implementation.id,
                implementation_version_id=memory_implementation.version_id,
                mapping_source="catalog_resolver",
                created_at=now,
            )
        )

    service = RuleAssetCommandUnitService(
        catalog_repository=catalog_repository,
    )
    try:
        page = service.list_check_item_production_units(
            owner_id=owner_id,
            page=1,
            page_size=10,
        )
        assert page["total"] == 3
        memory_group = next(
            item
            for item in page["items"]
            if item["check_item_id"] == "system.memory_usage"
        )
        assert memory_group["implementation_count"] == 1
        assert memory_group["name"] == "Memory utilization"
        assert memory_group["display_name"] == "内存使用率"
        assert memory_group["display_description"] == (
            "评估设备或槽位的内存容量与使用率。"
        )
        assert memory_group["command_count"] == 1
        assert memory_group["implementations"][0]["canonical_command"] == (
            "display memory"
        )
        assert memory_group["implementations"][0]["mapping_status"] == (
            "catalog_resolved"
        )
        assert memory_group["implementations"][0]["source_job"]["name"].startswith(
            "Q3 检查项样本"
        )

        unclassified = next(
            item for item in page["items"] if item["check_item_id"] is None
        )
        assert unclassified["name"] == "待归类检查项"
        assert unclassified["governance_status"] == "blocked"
        assert unclassified["governance_bucket"] == "exception"
        assert unclassified["current_stage"] == "identity_grouping"
        assert unclassified["next_action"]["code"] == "classify_check_item"
        assert page["summary"]["unclassified"] == 1

        ignored = next(
            item
            for item in page["items"]
            if item["category"] == "not_applicable"
        )
        assert ignored["display_name"] == "无需规则命令"
        assert ignored["governance_status"] == "not_applicable"
        assert ignored["governance_bucket"] == "not_applicable"
        assert ignored["classification_required"] is False
        assert ignored["next_action"] is None
        assert ignored["implementations"][0]["mapping_status"] == "not_required"
    finally:
        with get_db_session() as db:
            db.query(RuleAssetCommandUnitCheckItemRecord).filter(
                RuleAssetCommandUnitCheckItemRecord.command_unit_id.in_(
                    [memory_unit_id, unknown_unit_id, ignored_unit_id]
                )
            ).delete(synchronize_session=False)
            db.query(RuleAssetCommandUnitRecord).filter(
                RuleAssetCommandUnitRecord.id.in_(
                    [memory_unit_id, unknown_unit_id, ignored_unit_id]
                )
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.id == job_id
            ).delete(synchronize_session=False)
