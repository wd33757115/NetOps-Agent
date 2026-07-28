from __future__ import annotations

import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from src.core.patrol.offline_jobs import (
    OfflinePatrolJobService,
    _normalized_preview,
    _offline_job_display_name,
    _preview_device_totals,
)
from src.core.rule_control.command_asset_units import RuleAssetCommandUnitService
from src.gateway.offline_patrol_api import router as rule_asset_source_router
from src.infrastructure.db.models import (
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
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
    assert canonical in routes
    assert routes[canonical].deprecated is not True
    assert patrol_list in routes
    assert patrol_execute in routes
    assert patrol_asset_endpoint not in routes
    assert patrol_command_assets_endpoint not in routes


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
