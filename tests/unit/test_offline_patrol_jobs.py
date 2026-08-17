from __future__ import annotations

import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.core.patrol.offline_jobs import (
    OfflinePatrolJobService,
    _normalized_preview,
    _offline_job_display_name,
    _preview_device_totals,
    _serialize_offline_change,
)
from src.core.patrol.unstructured_diff import summarize_unstructured_changes
from src.core.rule_control.command_asset_units import RuleAssetCommandUnitService
from src.gateway.offline_patrol_api import router as rule_asset_source_router
from src.infrastructure.db.models import (
    FileMetadata,
    NetworkDevice,
    OfflinePatrolDeviceBindingRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session


def test_rule_asset_source_and_offline_patrol_are_separate_first_class_apis():
    routes = {
        route.path: route
        for route in rule_asset_source_router.routes
        if hasattr(route, "path")
    }

    canonical = "/api/v1/parser/asset-source-jobs/{job_id}/asset-production-pipeline"
    patrol_asset_endpoint = (
        "/api/v1/patrol/offline-jobs/{job_id}/asset-production-pipeline"
    )
    patrol_command_assets_endpoint = (
        "/api/v1/patrol/offline-jobs/{job_id}/command-asset-units"
    )
    patrol_list = "/api/v1/patrol/offline-jobs"
    patrol_execute = "/api/v1/patrol/offline-jobs/{job_id}/execute"
    asset_output_confirm = (
        "/api/v1/parser/asset-source-jobs/{job_id}/output-dispositions/confirm"
    )
    patrol_output_confirm = (
        "/api/v1/patrol/offline-jobs/{job_id}/output-dispositions/confirm"
    )
    assert canonical in routes
    assert routes[canonical].deprecated is not True
    assert patrol_list in routes
    assert patrol_execute in routes
    assert asset_output_confirm in routes
    assert patrol_output_confirm in routes
    assert patrol_asset_endpoint not in routes
    assert patrol_command_assets_endpoint not in routes


def test_offline_report_preserves_unstructured_change_evidence():
    serialized = _serialize_offline_change(
        {
            "change_id": "change-1",
            "device_id": "device-1",
            "command": "show running-config",
            "entity_type": "raw_command_output",
            "entity_key": "__singleton__",
            "field": "raw_text_hash",
            "observation_type": "raw.command.hash",
            "old": "old-hash",
            "new": "new-hash",
            "comparison_type": "unstructured_ordered_lines",
            "dimensions": {
                "unstructured_diff": {
                    "changed": True,
                    "profile": "configuration",
                    "profile_label": "配置输出",
                    "added_line_count": 2,
                    "removed_line_count": 1,
                    "changed_line_count": 3,
                    "categories": [],
                    "preview": [],
                }
            },
        },
        binding_names={"device-1": "core-switch"},
    )

    summary = summarize_unstructured_changes(
        [serialized],
        collected_output_count=1,
    )

    assert serialized["observation_type"] == "raw.command.hash"
    assert serialized["comparison_type"] == "unstructured_ordered_lines"
    assert summary["changed_output_count"] == 1
    assert summary["added_line_count"] == 2
    assert summary["removed_line_count"] == 1


def test_empty_event_export_is_self_describing(monkeypatch):
    service = OfflinePatrolJobService()
    monkeypatch.setattr(
        service,
        "get_job",
        lambda _job_id: {
            "periods": [{"role": "current", "patrol_run_id": "run-empty-events"}]
        },
    )
    monkeypatch.setattr(
        "src.core.patrol.offline_jobs.ChangeEventRepository.list_run_event_facts",
        lambda _self, *, run_id: [],
    )

    filename, media_type, content = service.build_result_export(
        "offline-empty-events",
        "events",
    )

    header = content.decode("utf-8-sig").strip().split(",")
    assert filename == "offline-empty-events-events.csv"
    assert media_type == "text/csv; charset=utf-8"
    assert header[:3] == ["occurrence_id", "event_id", "run_id"]
    assert "event_type" in header
    assert "observed_at" in header


def test_offline_job_is_idempotent_paginated_and_owner_scoped():
    service = OfflinePatrolJobService()
    suffix = uuid.uuid4().hex
    owner_id = f"offline-test-owner-{suffix}"
    idempotency_key = f"offline-test-key-{suffix}"
    baseline = datetime.now(timezone.utc) - timedelta(days=90)
    current = datetime.now(timezone.utc)

    try:
        first = service.create_job(
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            job_type="history_compare",
            name="offline history test",
            periods=[
                {"role": "baseline", "label": "Q1", "observed_at": baseline},
                {"role": "current", "label": "Q2", "observed_at": current},
            ],
            folder_id=None,
        )
        second = service.create_job(
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            job_type="history_compare",
            name="this must not create a duplicate",
            periods=[
                {"role": "baseline", "label": "Q1", "observed_at": baseline},
                {"role": "current", "label": "Q2", "observed_at": current},
            ],
            folder_id=None,
        )

        assert first["id"] == second["id"]
        assert [item["role"] for item in first["periods"]] == ["baseline", "current"]
        listing = service.list_jobs(owner_id=owner_id, page=1, page_size=1)
        assert listing["total"] == 1
        assert listing["items"][0]["id"] == first["id"]
        with pytest.raises(PermissionError):
            service.get_job(first["id"], owner_id="another-operator")
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.owner_id == owner_id
            ).delete(synchronize_session=False)


def test_retry_restores_the_nearest_reachable_configuration_state():
    service = OfflinePatrolJobService()
    suffix = uuid.uuid4().hex
    owner_id = f"offline-retry-owner-{suffix}"
    file_id = f"offline-retry-file-{suffix}"

    try:
        job = service.create_job(
            owner_id=owner_id,
            idempotency_key=f"offline-retry-{suffix}",
            purpose="rule_asset_source",
            job_type="parser_validation",
            name="recoverable rule evidence",
            periods=[
                {
                    "role": "sample",
                    "label": "sample",
                    "observed_at": datetime.now(timezone.utc),
                }
            ],
            folder_id=None,
        )
        with get_db_session() as db:
            row = db.get(OfflinePatrolJobRecord, job["id"])
            row.status = "failed"
            row.error_message = "preview dispatch failed"
            row.progress_json = {"phase": "preview_dispatch_failed", "percent": 100}

        service.retry(job["id"])
        detail = service.get_job(job["id"], owner_id=owner_id)
        assert detail["status"] == "draft"
        assert detail["progress"] == {"phase": "draft", "percent": 0}

        with get_db_session() as db:
            db.add(
                FileMetadata(
                    id=file_id,
                    name="sample.txt",
                    object_key=f"tests/{file_id}.txt",
                    owner_id=owner_id,
                    visibility="private",
                    status="active",
                )
            )
            db.add(
                OfflinePatrolSourceFileRecord(
                    id=f"offline-source-{suffix}",
                    job_id=job["id"],
                    period_role="sample",
                    file_id=file_id,
                    relative_path="sample.txt",
                    size_bytes=16,
                    status="attached",
                )
            )
            row = db.get(OfflinePatrolJobRecord, job["id"])
            row.status = "failed"
            row.error_message = "preview worker failed"

        service.retry(job["id"])
        detail = service.get_job(job["id"], owner_id=owner_id)
        assert detail["status"] == "uploading"
        assert detail["progress"] == {"phase": "uploading", "percent": 0}

        with get_db_session() as db:
            row = db.get(OfflinePatrolJobRecord, job["id"])
            row.status = "failed"
            row.preview_json = {"periods": {"sample": {}}, "blockers": []}
            row.blockers_json = []

        service.retry(job["id"])
        detail = service.get_job(job["id"], owner_id=owner_id)
        assert detail["status"] == "ready"
        assert detail["progress"] == {"phase": "ready", "percent": 100}
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.owner_id == owner_id
            ).delete(synchronize_session=False)
            db.query(FileMetadata).filter(FileMetadata.id == file_id).delete(
                synchronize_session=False
            )


def test_offline_patrol_and_rule_asset_source_jobs_are_workspace_scoped():
    service = OfflinePatrolJobService()
    suffix = uuid.uuid4().hex
    owner_id = f"offline-purpose-owner-{suffix}"
    observed_at = datetime.now(timezone.utc)

    try:
        patrol = service.create_job(
            owner_id=owner_id,
            idempotency_key=f"patrol-{suffix}",
            purpose="offline_patrol",
            job_type="single_patrol",
            name="single offline patrol",
            periods=[{"role": "current", "label": "current", "observed_at": observed_at}],
            folder_id=None,
        )
        asset = service.create_job(
            owner_id=owner_id,
            idempotency_key=f"asset-{suffix}",
            purpose="rule_asset_source",
            job_type="parser_validation",
            name="parser evidence",
            periods=[{"role": "sample", "label": "sample", "observed_at": observed_at}],
            folder_id=None,
        )

        patrol_page = service.list_jobs(owner_id=owner_id, purpose="offline_patrol")
        asset_page = service.list_jobs(owner_id=owner_id, purpose="rule_asset_source")

        assert [item["id"] for item in patrol_page["items"]] == [patrol["id"]]
        assert [item["id"] for item in asset_page["items"]] == [asset["id"]]
        with pytest.raises(KeyError, match="workspace"):
            service.get_job(
                patrol["id"],
                owner_id=owner_id,
                purpose="rule_asset_source",
            )
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.owner_id == owner_id
            ).delete(synchronize_session=False)


def test_ambiguous_device_binding_exposes_ranked_inventory_candidates():
    service = OfflinePatrolJobService()
    suffix = uuid.uuid4().hex
    owner_id = f"offline-binding-owner-{suffix}"
    same_name_id = f"device-name-{suffix}"
    same_ip_id = f"device-ip-{suffix}"
    observed_name = f"SW-CONFLICT-{suffix}"
    observed_ip = "192.0.2.240"

    try:
        job = service.create_job(
            owner_id=owner_id,
            idempotency_key=f"offline-binding-{suffix}",
            purpose="offline_patrol",
            job_type="single_patrol",
            name="ambiguous identity evidence",
            periods=[
                {
                    "role": "current",
                    "label": "current",
                    "observed_at": datetime.now(timezone.utc),
                }
            ],
            folder_id=None,
        )
        with get_db_session() as db:
            db.add_all(
                [
                    NetworkDevice(
                        id=same_name_id,
                        device_name=observed_name,
                        management_ip="192.0.2.241",
                        vendor="H3C",
                        model="S5130",
                        status="active",
                    ),
                    NetworkDevice(
                        id=same_ip_id,
                        device_name=f"SW-IP-{suffix}",
                        management_ip=observed_ip,
                        vendor="Cisco",
                        model="N9K",
                        status="active",
                    ),
                    OfflinePatrolDeviceBindingRecord(
                        id=f"binding-{suffix}",
                        job_id=job["id"],
                        period_role="current",
                        external_device_id=f"external-{suffix}",
                        device_name=observed_name,
                        management_ip=observed_ip,
                        match_status="ambiguous",
                        confidence=0.95,
                        match_reason="multiple_inventory_candidates",
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    ),
                ]
            )

        detail = service.get_job(job["id"], owner_id=owner_id)
        binding = detail["device_bindings"][0]

        assert binding["match_status"] == "ambiguous"
        assert [
            item["inventory_device_id"] for item in binding["match_candidates"]
        ] == [same_ip_id, same_name_id]
        assert [
            item["match_reason"] for item in binding["match_candidates"]
        ] == ["management_ip", "device_name"]
        assert binding["match_candidates"][0]["confidence"] == 0.95
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.owner_id == owner_id
            ).delete(synchronize_session=False)
            db.query(NetworkDevice).filter(
                NetworkDevice.id.in_([same_name_id, same_ip_id])
            ).delete(synchronize_session=False)


def test_confirming_ambiguous_binding_immediately_recomputes_job_readiness():
    service = OfflinePatrolJobService()
    suffix = uuid.uuid4().hex
    owner_id = f"offline-binding-ready-owner-{suffix}"
    inventory_id = f"device-ready-{suffix}"
    binding_id = f"binding-ready-{suffix}"

    try:
        job = service.create_job(
            owner_id=owner_id,
            idempotency_key=f"offline-binding-ready-{suffix}",
            purpose="offline_patrol",
            job_type="single_patrol",
            name="identity confirmation readiness",
            periods=[
                {
                    "role": "current",
                    "label": "current",
                    "observed_at": datetime.now(timezone.utc),
                }
            ],
            folder_id=None,
        )
        with get_db_session() as db:
            db.add(
                NetworkDevice(
                    id=inventory_id,
                    device_name=f"SW-READY-{suffix}",
                    management_ip="192.0.2.242",
                    vendor="H3C",
                    model="S5130",
                    status="active",
                )
            )
            db.add(
                OfflinePatrolDeviceBindingRecord(
                    id=binding_id,
                    job_id=job["id"],
                    period_role="current",
                    external_device_id=f"external-ready-{suffix}",
                    device_name=f"SW-READY-{suffix}",
                    management_ip="192.0.2.242",
                    match_status="ambiguous",
                    confidence=0.95,
                    match_reason="multiple_inventory_candidates",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            row = db.get(OfflinePatrolJobRecord, job["id"])
            row.status = "blocked"
            row.preview_json = {
                "periods": {"current": {"files": []}},
                "blockers": [
                    {
                        "code": "ambiguous_device_binding",
                        "message": "1 台设备存在多个资产匹配候选",
                    }
                ],
                "warnings": [],
                "quality_issues": [],
            }
            row.blockers_json = list(row.preview_json["blockers"])
            row.progress_json = {"phase": "blocked", "percent": 100}

        service.confirm_bindings(
            job_id=job["id"],
            actor="engineer",
            bindings=[
                {
                    "binding_id": binding_id,
                    "inventory_device_id": inventory_id,
                }
            ],
        )
        detail = service.get_job(job["id"], owner_id=owner_id)

        assert detail["status"] == "ready"
        assert detail["progress"] == {"phase": "ready", "percent": 100}
        assert detail["blockers"] == []
        assert detail["preview"]["blockers"] == []
        assert detail["device_bindings"][0]["match_status"] == "confirmed"
        assert detail["device_bindings"][0]["inventory_device_id"] == inventory_id
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.owner_id == owner_id
            ).delete(synchronize_session=False)
            db.query(NetworkDevice).filter(
                NetworkDevice.id == inventory_id
            ).delete(synchronize_session=False)


def test_confirming_empty_output_is_job_local_auditable_and_recomputes_readiness():
    service = OfflinePatrolJobService()
    suffix = uuid.uuid4().hex
    owner_id = f"offline-empty-owner-{suffix}"
    review_item_id = f"output-review-{suffix}"

    try:
        job = service.create_job(
            owner_id=owner_id,
            idempotency_key=f"offline-empty-{suffix}",
            purpose="rule_asset_source",
            job_type="parser_validation",
            name="empty output governance",
            periods=[
                {
                    "role": "sample",
                    "label": "sample",
                    "observed_at": datetime.now(timezone.utc),
                }
            ],
            folder_id=None,
        )
        review_item = {
            "review_item_id": review_item_id,
            "period_role": "sample",
            "source_relative_path": "N9K-1.txt",
            "device_id": "N9K-1-192.0.2.10",
            "device_name": "N9K-1",
            "management_ip": "192.0.2.10",
            "canonical_command": "show vpc",
            "start_line": 20,
            "end_line": 21,
            "raw_text_hash": "0" * 64,
            "raw_text_length": 0,
            "status": "empty_unverified",
        }
        period_preview = {
            "file_count": 1,
            "device_count": 1,
            "command_count": 1,
            "ready": 0,
            "ignored": 0,
            "empty_unverified": 1,
            "files": [
                {
                    "relative_path": "N9K-1.txt",
                    "device_id": "N9K-1-192.0.2.10",
                    "device_name": "N9K-1",
                    "management_ip": "192.0.2.10",
                    "statuses": {"empty_unverified": 1},
                    "commands": [
                        {
                            "command": "show vpc",
                            "status": "empty_unverified",
                            "review_item_id": review_item_id,
                            "start_line": 20,
                            "end_line": 21,
                            "raw_text_hash": "0" * 64,
                            "raw_text_length": 0,
                        }
                    ],
                }
            ],
        }
        with get_db_session() as db:
            row = db.get(OfflinePatrolJobRecord, job["id"])
            row.status = "blocked"
            row.preview_json = {
                "periods": {"sample": period_preview},
                "totals": {"empty_unverified": 1},
                "blockers": [{"code": "empty_unverified", "count": 1}],
                "quality_issues": [{"code": "empty_unverified", "count": 1}],
                "warnings": [],
                "output_review_items": [review_item],
                "output_dispositions": [],
            }
            row.blockers_json = list(row.preview_json["quality_issues"])

        first = service.confirm_output_dispositions(
            job_id=job["id"],
            actor="engineer-1",
            review_item_ids=[review_item_id],
            reason="",
        )
        second = service.confirm_output_dispositions(
            job_id=job["id"],
            actor="engineer-1",
            review_item_ids=[review_item_id],
            reason="设备未配置 vPC",
        )
        detail = service.get_job(job["id"], owner_id=owner_id)

        assert first["status"] == "ready"
        assert second["status"] == "ready"
        assert detail["status"] == "ready"
        assert detail["output_review_items"] == []
        assert len(detail["output_dispositions"]) == 1
        assert detail["output_dispositions"][0]["decided_by"] == "engineer-1"
        assert detail["output_dispositions"][0]["reason"] == ""
        command = detail["preview"]["periods"]["sample"]["files"][0]["commands"][0]
        assert command["status"] == "ignored"
        assert command["original_status"] == "empty_unverified"
        assert detail["preview_totals"]["empty_unverified"] == 0
        assert detail["preview_totals"]["ignored"] == 1
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.owner_id == owner_id
            ).delete(synchronize_session=False)


def test_offline_patrol_cannot_create_parser_validation_asset_job():
    with pytest.raises(ValueError, match="not valid for offline_patrol"):
        OfflinePatrolJobService().create_job(
            owner_id="offline-boundary-test",
            idempotency_key=f"offline-boundary-{uuid.uuid4().hex}",
            purpose="offline_patrol",
            job_type="parser_validation",
            name="must stay in Parser control",
            periods=[
                {
                    "role": "sample",
                    "label": "sample",
                    "observed_at": datetime.now(timezone.utc),
                }
            ],
            folder_id=None,
        )


def test_offline_job_pagination_reaches_the_tail_of_ten_thousand_records():
    service = OfflinePatrolJobService()
    suffix = uuid.uuid4().hex
    owner_id = f"offline-scale-owner-{suffix}"
    created_at = datetime.now(timezone.utc) - timedelta(days=1)
    record_count = 10_005

    try:
        with get_db_session() as db:
            db.bulk_insert_mappings(
                OfflinePatrolJobRecord,
                [
                    {
                        "id": f"offline-scale-{suffix}-{index:05d}",
                        "idempotency_key": f"offline-scale-key-{suffix}-{index:05d}",
                        "job_type": "parser_validation",
                        "name": f"Scale patrol {index:05d}",
                        "owner_id": owner_id,
                        "status": "completed",
                        "publish_events": False,
                        "attempt_number": 1,
                        "progress_json": {"phase": "completed", "percent": 100},
                        "archived": False,
                        "created_at": created_at + timedelta(microseconds=index),
                        "updated_at": created_at + timedelta(microseconds=index),
                    }
                    for index in range(record_count)
                ],
            )

        first_page = service.list_jobs(owner_id=owner_id, page=1, page_size=25)
        last_page = service.list_jobs(owner_id=owner_id, page=401, page_size=25)

        assert first_page["total"] == record_count
        assert first_page["page_size"] == 25
        assert len(first_page["items"]) == 25
        assert first_page["items"][0]["id"].endswith("10004")
        assert len(last_page["items"]) == 5
        assert last_page["items"][-1]["id"].endswith("00000")
        assert {item["id"] for item in first_page["items"]}.isdisjoint(
            item["id"] for item in last_page["items"]
        )
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.owner_id == owner_id
            ).delete(synchronize_session=False)


def test_history_compare_requires_strict_period_order():
    observed_at = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="baseline observed_at"):
        OfflinePatrolJobService().create_job(
            owner_id="period-order-test",
            idempotency_key=uuid.uuid4().hex,
            job_type="history_compare",
            name="invalid periods",
            periods=[
                {"role": "baseline", "observed_at": observed_at},
                {"role": "current", "observed_at": observed_at},
            ],
            folder_id=None,
        )


def test_history_preview_counts_physical_devices_once_across_periods():
    previews = {
        "baseline": {
            "device_count": 2,
            "files": [
                {"device_id": "SW1-baseline", "management_ip": "10.0.0.1"},
                {"device_id": "SW2-baseline", "management_ip": "10.0.0.2"},
            ],
        },
        "current": {
            "device_count": 2,
            "files": [
                {"device_id": "SW1-current", "management_ip": "10.0.0.1"},
                {"device_id": "SW2-current", "management_ip": "10.0.0.2"},
            ],
        },
    }

    unique_devices, device_periods = _preview_device_totals(previews)

    assert unique_devices == 2
    assert device_periods == 4
    normalized = _normalized_preview({"periods": previews, "totals": {"device_count": 4}})
    assert normalized["totals"]["device_count"] == 2
    assert normalized["totals"]["device_period_count"] == 4


def test_corrupted_historical_name_gets_a_non_destructive_display_fallback():
    assert (
        _offline_job_display_name("?????? Q2?Q3?????????", "history_compare")
        == "Q2 → Q3 规则回放样本"
    )
    assert _offline_job_display_name("季度巡检", "history_compare") == "季度巡检"
    assert _offline_job_display_name("?????", "parser_validation") == "规则资产样本导入"


def test_execution_quality_gate_keeps_asset_gaps_as_non_blocking_warnings(monkeypatch):
    service = OfflinePatrolJobService()
    monkeypatch.setattr(
        service,
        "get_job",
        lambda _job_id: {
            "purpose": "rule_asset_source",
            "job_type": "history_compare",
            "source_files": [{"id": "source-1"}],
            "preview": {"periods": {"baseline": {}, "current": {}}},
            "blockers": [],
        },
    )
    monkeypatch.setattr(
        service,
        "_build_quality_blockers",
        lambda _job_id, _previews: [
            {"code": "change_policy_missing", "message": "policy was deprecated"}
        ],
    )

    result = service.execution_quality_gate("offline-current-assets")

    assert result["success"] is True
    assert result["blockers"] == []
    assert result["warnings"][0]["code"] == "change_policy_missing"
    assert result["warnings"][0]["severity"] == "warning"


def test_rule_asset_source_can_ingest_before_any_common_parser_is_ready(monkeypatch):
    service = OfflinePatrolJobService()
    monkeypatch.setattr(
        service,
        "get_job",
        lambda _job_id: {
            "purpose": "rule_asset_source",
            "job_type": "history_compare",
            "source_files": [{"id": "baseline"}, {"id": "current"}],
            "preview": {"periods": {"baseline": {}, "current": {}}},
            "blockers": [],
        },
    )
    monkeypatch.setattr(
        service,
        "_build_quality_blockers",
        lambda _job_id, _previews: [
            {
                "code": "no_common_ready_parser",
                "message": "no common command has a ready Parser yet",
            },
            {
                "code": "parser_template_inactive",
                "message": "a previously selected template is inactive",
            },
            {"code": "template_missing", "message": "Parser generation is required"},
            {"code": "parse_failed", "message": "Parser repair is required"},
        ],
    )

    result = service.execution_quality_gate("offline-rule-assets-with-parser-gaps")

    assert result["success"] is True
    assert result["blockers"] == []
    assert {item["code"] for item in result["warnings"]} == {
        "no_common_ready_parser",
        "parser_template_inactive",
        "template_missing",
        "parse_failed",
    }
    assert all(item["severity"] == "warning" for item in result["warnings"])


def test_execution_quality_gate_still_blocks_ingestion_integrity_failures(monkeypatch):
    service = OfflinePatrolJobService()
    monkeypatch.setattr(
        service,
        "get_job",
        lambda _job_id: {
            "job_type": "history_compare",
            "source_files": [{"id": "source-1"}],
            "preview": {"periods": {"baseline": {}, "current": {}}},
            "blockers": [],
        },
    )
    monkeypatch.setattr(
        service,
        "_build_quality_blockers",
        lambda _job_id, _previews: [
            {"code": "identity_unknown", "message": "device identity is unknown"}
        ],
    )

    result = service.execution_quality_gate("offline-invalid-identity")

    assert result["success"] is False
    assert result["blockers"][0]["code"] == "identity_unknown"
    assert result["blockers"][0]["severity"] == "blocking"
    assert result["warnings"] == []


def test_history_compare_allows_partial_report_when_parser_assets_are_missing(monkeypatch):
    service = OfflinePatrolJobService()
    monkeypatch.setattr(
        service,
        "get_job",
        lambda _job_id: {
            "purpose": "offline_patrol",
            "job_type": "history_compare",
            "source_files": [{"id": "baseline"}, {"id": "current"}],
            "preview": {"periods": {"baseline": {}, "current": {}}},
        },
    )
    monkeypatch.setattr(
        service,
        "_build_quality_blockers",
        lambda _job_id, _previews: [
            {"code": "template_missing", "message": "Parser is missing"},
            {"code": "event_rule_missing", "message": "Event rule is missing"},
        ],
    )

    result = service.execution_quality_gate("offline-partial-history")

    assert result["success"] is True
    assert result["blockers"] == []
    assert {item["code"] for item in result["warnings"]} == {
        "template_missing",
        "event_rule_missing",
    }

    summary = service._serialize_job_summary(
        SimpleNamespace(
            id="offline-partial-history",
            purpose="offline_patrol",
            job_type="history_compare",
            report_intent="historical",
            name="partial history",
            owner_id="operator",
            status="ready",
            vendor=None,
            model=None,
            publish_events=False,
            folder_id=None,
            workflow_run_id=None,
            attempt_number=0,
            progress_json={"phase": "ready", "percent": 100},
            preview_json={},
            blockers_json=result["quality_issues"],
            archived=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            completed_at=None,
        )
    )
    assert summary["blocker_count"] == 0
    assert summary["warning_count"] == 2


def test_single_patrol_keeps_parser_asset_gaps_blocking(monkeypatch):
    service = OfflinePatrolJobService()
    monkeypatch.setattr(
        service,
        "get_job",
        lambda _job_id: {
            "purpose": "offline_patrol",
            "job_type": "single_patrol",
            "source_files": [{"id": "current"}],
            "preview": {"periods": {"current": {}}},
        },
    )
    monkeypatch.setattr(
        service,
        "_build_quality_blockers",
        lambda _job_id, _previews: [
            {"code": "template_missing", "message": "Parser is missing"},
        ],
    )

    result = service.execution_quality_gate("offline-current-attestation")

    assert result["success"] is False
    assert result["blockers"][0]["code"] == "template_missing"


def test_history_compare_accepts_common_raw_comparison_without_parser():
    service = OfflinePatrolJobService()
    previews = {
        role: {
            "files": [
                {
                    "device_id": "edge-1",
                    "commands": [
                        {"command": "show running-config", "status": "raw_compare"},
                    ],
                }
            ]
        }
        for role in ("baseline", "current")
    }

    issues = service._build_quality_blockers("missing-job", previews)

    assert "no_common_ready_parser" not in {item["code"] for item in issues}


def test_terminal_workflow_is_projected_with_report_artifact(monkeypatch):
    service = OfflinePatrolJobService()
    projected_command_assets: list[str] = []
    monkeypatch.setattr(
        RuleAssetCommandUnitService,
        "reconcile",
        lambda _self, job_id: projected_command_assets.append(job_id) or {"unit_count": 0},
    )
    suffix = uuid.uuid4().hex
    owner_id = f"offline-projection-owner-{suffix}"
    workflow_run_id = f"offline-projection-workflow-{suffix}"
    baseline = datetime.now(timezone.utc) - timedelta(days=90)
    current = datetime.now(timezone.utc)
    job = service.create_job(
        owner_id=owner_id,
        idempotency_key=f"offline-projection-key-{suffix}",
        job_type="history_compare",
        name="offline terminal projection",
        periods=[
            {"role": "baseline", "observed_at": baseline},
            {"role": "current", "observed_at": current},
        ],
        folder_id=None,
    )
    report_artifact = {
        "file_key": f"patrol_reports/{workflow_run_id}/report.md",
        "download_url": "/api/artifacts/download/?key=report.md",
        "filename": "report.md",
        "content_type": "text/markdown; charset=utf-8",
    }

    try:
        with get_db_session() as db:
            row = db.get(OfflinePatrolJobRecord, job["id"])
            row.status = "running"
            row.workflow_run_id = workflow_run_id
            period = (
                db.query(OfflinePatrolPeriodRecord)
                .filter(
                    OfflinePatrolPeriodRecord.job_id == job["id"],
                    OfflinePatrolPeriodRecord.role == "current",
                )
                .one()
            )
            period.patrol_run_id = f"patrol-run-{suffix}"
            db.add(
                WorkflowRun(
                    id=workflow_run_id,
                    template_name="patrol-history-change-event",
                    source="offline-patrol",
                    user_id=owner_id,
                    status="completed",
                    context={"offline_job_id": job["id"]},
                    completed_at=current,
                    created_at=baseline,
                    updated_at=current,
                )
            )
            db.add(
                WorkflowStepRecord(
                    id=f"offline-projection-step-{suffix}",
                    run_id=workflow_run_id,
                    step_index=7,
                    step_name="report_generation",
                    skill_name="report-generator",
                    status="completed",
                    output_artifacts={"report": report_artifact},
                    result={"success": True},
                    created_at=current,
                    completed_at=current,
                )
            )

        assert service.sync_workflow_terminal_state(workflow_run_id) == 1
        projected = service.get_job(job["id"], owner_id=owner_id)
        assert projected["status"] == "completed"
        assert projected["progress"] == {"phase": "completed", "percent": 100}
        assert projected["result"]["run_id"] == f"patrol-run-{suffix}"
        assert projected["artifacts"]["report"] == report_artifact
        assert projected_command_assets == [job["id"]]
    finally:
        with get_db_session() as db:
            db.query(WorkflowStepRecord).filter(
                WorkflowStepRecord.run_id == workflow_run_id
            ).delete(synchronize_session=False)
            db.query(WorkflowRun).filter(WorkflowRun.id == workflow_run_id).delete(
                synchronize_session=False
            )
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.owner_id == owner_id
            ).delete(synchronize_session=False)


def test_extract_zip_accepts_capture_files_and_ignores_other_assets(tmp_path):
    archive_path = tmp_path / "captures.zip"
    target = tmp_path / "output"
    target.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Q1/SW1.txt", "<SW1>display interface brief\n")
        archive.writestr("Q1/SW2.log", "<SW2>display interface brief\n")
        archive.writestr("Q1/readme.md", "not a CLI capture")

    count, expanded_bytes = OfflinePatrolJobService._extract_zip(archive_path, target)

    assert count == 2
    assert expanded_bytes > 0
    assert (target / "Q1" / "SW1.txt").is_file()
    assert (target / "Q1" / "SW2.log").is_file()
    assert not (target / "Q1" / "readme.md").exists()


@pytest.mark.parametrize("member", ["../outside.txt", "C:/outside.txt"])
def test_extract_zip_rejects_unsafe_paths(tmp_path, member):
    archive_path = tmp_path / "unsafe.zip"
    target = tmp_path / "output"
    target.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, "untrusted")

    with pytest.raises(ValueError, match="unsafe offline patrol relative path"):
        OfflinePatrolJobService._extract_zip(archive_path, target)
