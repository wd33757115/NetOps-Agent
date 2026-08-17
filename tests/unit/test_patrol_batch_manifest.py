from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.patrol import batch_manifest
from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
from src.infrastructure.db.models import (
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolReportMaterializationRequestRecord,
    WorkflowRun,
)


def _session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PatrolBatchRecord.__table__.create(engine)
    WorkflowRun.__table__.create(engine)
    PatrolBatchDeviceRecord.__table__.create(engine)
    PatrolReportMaterializationRequestRecord.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    @contextmanager
    def session_scope():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(batch_manifest, "get_db_session", session_scope)
    return session_scope


def test_manifest_supports_two_thousand_targets_with_server_pagination(monkeypatch):
    session_scope = _session_factory(monkeypatch)
    ready_rows = [
        {
            "device_id": f"device-{index:04d}",
            "device_name": f"SW-{index:04d}",
            "ip": f"10.0.{index // 255}.{index % 255}",
        }
        for index in range(1999)
    ]
    launched = [
        {
            "device_id": row["device_id"],
            "workflow_run_id": f"run-{index:04d}",
        }
        for index, row in enumerate(ready_rows)
    ]
    skipped = [
        {
            "device_id": "device-1999",
            "device_name": "SW-1999",
            "failure_code": "missing_credential",
            "error": "credential_ref is required",
        }
    ]
    repository = PatrolBatchManifestRepository()
    repository.record_launch(
        result={
            "patrol_batch_id": "batch-2000",
            "workflow_name": "patrol-change-event",
            "launch_mode": "workflow",
            "mode": "production",
            "target_count": 2000,
            "launched_count": 1999,
            "skipped_count": 1,
            "runs": launched,
        },
        ready_rows=ready_rows,
        skipped_errors=skipped,
        target_snapshot={
            "target_type": "group",
            "target_label": "设备分组 core",
            "selector": {"group": "core"},
        },
    )

    now = datetime.now(timezone.utc)
    with session_scope() as db:
        db.bulk_save_objects(
            [
                WorkflowRun(
                    id=item["workflow_run_id"],
                    template_name="patrol-change-event",
                    source="device-management",
                    status="completed" if index < 1500 else "running",
                    context={"device_id": item["device_id"], "patrol_batch_id": "batch-2000"},
                    created_at=now,
                    completed_at=now if index < 1500 else None,
                )
                for index, item in enumerate(launched)
            ]
        )

    first_page = repository.list_devices("batch-2000", limit=50, offset=0)
    last_page = repository.list_devices("batch-2000", limit=50, offset=1950)
    skipped_page = repository.list_devices(
        "batch-2000",
        limit=50,
        selection_status="skipped",
    )
    batches = repository.list_batches()
    detached_runs = repository.workflow_runs("batch-2000")

    assert first_page is not None and first_page["total"] == 2000
    assert len(first_page["items"]) == 50
    assert last_page is not None and len(last_page["items"]) == 50
    assert skipped_page is not None and skipped_page["total"] == 1
    assert skipped_page["items"][0]["connection_status"] == "not_attempted"
    assert batches[0]["target_label"] == "设备分组 core"
    assert batches[0]["report_purpose"] == "shadow_validation"
    assert batches[0]["target_count"] == 2000
    assert batches[0]["completed_count"] == 1500
    assert batches[0]["running_count"] == 499
    assert detached_runs is not None and len(detached_runs) == 1999
    assert detached_runs[0].context["patrol_batch_id"] == "batch-2000"


def test_batch_creation_rejects_current_purpose_for_offline_source(
    monkeypatch,
):
    session_scope = _session_factory(monkeypatch)
    repository = PatrolBatchManifestRepository()

    with pytest.raises(
        ValueError,
        match="report purpose is inconsistent with the patrol source",
    ):
        repository.create_pending_batch(
            patrol_batch_id="batch-invalid-current",
            workflow_name="patrol-offline-current",
            launch_mode="workflow",
            patrol_mode="production",
            report_purpose="production_current",
            save_baseline=False,
            ticket_id=None,
            target_count=1,
            planned_rows=[],
            skipped_errors=[],
            target_snapshot={
                "target_type": "offline_import",
                "target_label": "offline sample",
                "selector": {"job_id": "offline-1"},
            },
        )

    with session_scope() as db:
        assert db.get(
            PatrolBatchRecord,
            "batch-invalid-current",
        ) is None


def test_batch_history_uses_stable_server_pagination_and_total(monkeypatch):
    session_scope = _session_factory(monkeypatch)
    base_time = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with session_scope() as db:
        db.bulk_save_objects(
            [
                PatrolBatchRecord(
                    patrol_batch_id=f"batch-{index:04d}",
                    workflow_name="patrol-change-event",
                    launch_mode="workflow",
                    patrol_mode="production",
                    ticket_id=None,
                    target_type="group",
                    target_label=f"device group group-{index % 3}",
                    selector_json={"group": f"group-{index % 3}"},
                    target_count=1,
                    launched_count=1,
                    skipped_count=0,
                    archived=False,
                    created_at=base_time + timedelta(minutes=index),
                    updated_at=base_time + timedelta(minutes=index),
                )
                for index in range(60)
            ]
        )

    repository = PatrolBatchManifestRepository()
    first = repository.list_batches_page(limit=25, offset=0)
    third = repository.list_batches_page(limit=25, offset=50)
    searched = repository.list_batches_page(limit=25, search="batch-0007")

    assert first["total"] == 60
    assert len(first["items"]) == 25
    assert first["items"][0]["patrol_batch_id"] == "batch-0059"
    assert third["total"] == 60
    assert [item["patrol_batch_id"] for item in third["items"]] == [
        f"batch-{index:04d}" for index in range(9, -1, -1)
    ]
    assert searched["total"] == 1
    assert searched["items"][0]["patrol_batch_id"] == "batch-0007"


def test_batch_history_searches_all_devices_and_filters_aggregate_status(monkeypatch):
    session_scope = _session_factory(monkeypatch)
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        for batch_id, statuses in {
            "batch-active": ["completed", "running"],
            "batch-failed": ["completed", "failed"],
            "batch-completed": ["completed", "completed"],
        }.items():
            db.add(
                PatrolBatchRecord(
                    patrol_batch_id=batch_id,
                    workflow_name="patrol-change-event",
                    launch_mode="workflow",
                    patrol_mode="production",
                    target_type="group",
                    target_label=batch_id,
                    selector_json={},
                    target_count=2,
                    launched_count=2,
                    skipped_count=0,
                    archived=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            for index, status in enumerate(statuses):
                run_id = f"{batch_id}-run-{index}"
                db.add(
                    WorkflowRun(
                        id=run_id,
                        template_name="patrol-change-event",
                        source="device-management",
                        status=status,
                        context={"patrol_batch_id": batch_id},
                        created_at=now,
                    )
                )
                db.add(
                    PatrolBatchDeviceRecord(
                        id=f"{batch_id}-device-{index}",
                        patrol_batch_id=batch_id,
                        device_id=f"device-{batch_id}-{index}",
                        device_name=(
                            "hidden-target"
                            if batch_id == "batch-failed" and index == 1
                            else "other"
                        ),
                        management_ip=(
                            "10.8.7.6"
                            if batch_id == "batch-failed" and index == 1
                            else None
                        ),
                        selection_status="launched",
                        preflight_status="ready",
                        workflow_run_id=run_id,
                        created_at=now,
                        updated_at=now,
                    )
                )

    repository = PatrolBatchManifestRepository()

    active = repository.list_batches_page(status="active")["items"]
    failed = repository.list_batches_page(status="failed")["items"]
    completed = repository.list_batches_page(status="completed")["items"]
    by_name = repository.list_batches_page(search="hidden-target")["items"]
    by_ip = repository.list_batches_page(search="10.8.7.6")["items"]

    assert [item["patrol_batch_id"] for item in active] == ["batch-active"]
    assert [item["patrol_batch_id"] for item in failed] == ["batch-failed"]
    assert [item["patrol_batch_id"] for item in completed] == ["batch-completed"]
    assert by_name[0]["patrol_batch_id"] == "batch-failed"
    assert by_ip[0]["patrol_batch_id"] == "batch-failed"


def test_final_report_is_requested_once_only_after_all_runs_are_terminal(monkeypatch):
    session_scope = _session_factory(monkeypatch)
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id="batch-report",
                workflow_name="patrol-change-event",
                launch_mode="workflow",
                patrol_mode="production",
                target_type="group",
                target_label="core",
                selector_json={},
                target_count=2,
                launched_count=2,
                skipped_count=0,
                dispatch_status="completed",
                report_status="not_requested",
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )
        for index, status in enumerate(("completed", "running")):
            run_id = f"report-run-{index}"
            db.add(
                WorkflowRun(
                    id=run_id,
                    template_name="patrol-change-event",
                    source="device-management",
                    status=status,
                    context={"patrol_batch_id": "batch-report"},
                    created_at=now,
                )
            )
            db.add(
                PatrolBatchDeviceRecord(
                    id=f"report-target-{index}",
                    patrol_batch_id="batch-report",
                    device_id=f"device-{index}",
                    selection_status="launched",
                    preflight_status="ready",
                    dispatch_status="dispatched",
                    workflow_run_id=run_id,
                    created_at=now,
                    updated_at=now,
                )
            )

    repository = PatrolBatchManifestRepository()
    assert repository.request_terminal_report("batch-report") is False

    with session_scope() as db:
        db.get(WorkflowRun, "report-run-1").status = "failed"

    assert repository.request_terminal_report("batch-report") is True
    assert repository.request_terminal_report("batch-report") is False
    with session_scope() as db:
        batch = db.get(PatrolBatchRecord, "batch-report")
        batch.report_status = "completed"
        batch.report_completed_at = now

    assert repository.request_terminal_report("batch-report") is False
    assert repository.request_terminal_report(
        "batch-report",
        allow_regenerate=True,
    ) is True
    state = repository.report_generation_state("batch-report")
    assert state is not None
    assert state["status"] == "queued"
    assert state["completed_at"] is None


def test_report_request_ledger_replays_completed_key_and_fences_stale_worker(
    monkeypatch,
):
    session_scope = _session_factory(monkeypatch)
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id="batch-report-ledger",
                workflow_name="patrol-change-event",
                launch_mode="workflow",
                patrol_mode="production",
                target_type="device",
                target_label="SW-1",
                selector_json={},
                target_count=1,
                launched_count=1,
                skipped_count=0,
                dispatch_status="completed",
                report_status="not_requested",
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            WorkflowRun(
                id="report-ledger-run",
                template_name="patrol-change-event",
                source="device-management",
                status="completed",
                context={"patrol_batch_id": "batch-report-ledger"},
                created_at=now,
                completed_at=now,
            )
        )
        db.add(
            PatrolBatchDeviceRecord(
                id="report-ledger-target",
                patrol_batch_id="batch-report-ledger",
                device_id="device-1",
                selection_status="launched",
                preflight_status="ready",
                dispatch_status="dispatched",
                workflow_run_id="report-ledger-run",
                created_at=now,
                updated_at=now,
            )
        )

    repository = PatrolBatchManifestRepository()
    assert repository.request_terminal_report(
        "batch-report-ledger",
        allow_regenerate=True,
        idempotency_key="report-key-1",
        task_id="report-task-1",
        requested_by="api:engineer",
    ) is True
    first_state = repository.report_generation_state(
        "batch-report-ledger"
    )
    assert first_state is not None
    first_request_id = first_state["request_id"]
    assert first_state["task_id"] == "report-task-1"

    assert repository.mark_report_status(
        "batch-report-ledger",
        "generating",
        request_id=first_request_id,
    ) is True
    with session_scope() as db:
        batch = db.get(PatrolBatchRecord, "batch-report-ledger")
        batch.report_manifest_id = "report-1"
        batch.report_manifest_digest = "a" * 64
    assert repository.mark_report_status(
        "batch-report-ledger",
        "completed",
        request_id=first_request_id,
    ) is True

    assert repository.request_terminal_report(
        "batch-report-ledger",
        allow_regenerate=True,
        idempotency_key="report-key-1",
        task_id="report-task-1",
        requested_by="api:engineer",
    ) is False
    replay = repository.report_request_state(
        "batch-report-ledger",
        "report-key-1",
    )
    assert replay is not None
    assert replay["status"] == "completed"
    assert replay["report_manifest_id"] == "report-1"
    assert repository.mark_report_status(
        "batch-report-ledger",
        "generating",
        request_id=first_request_id,
    ) is False
    assert repository.report_generation_state(
        "batch-report-ledger"
    )["status"] == "completed"

    assert repository.request_terminal_report(
        "batch-report-ledger",
        allow_regenerate=True,
        idempotency_key="report-key-2",
        task_id="report-task-2",
        requested_by="api:engineer",
    ) is True
    second_state = repository.report_generation_state(
        "batch-report-ledger"
    )
    assert second_state is not None
    assert second_state["request_id"] != first_request_id
    assert repository.mark_report_status(
        "batch-report-ledger",
        "completed",
        request_id=first_request_id,
    ) is False
    assert repository.report_generation_state(
        "batch-report-ledger"
    )["status"] == "queued"
    assert repository.mark_report_status(
        "batch-report-ledger",
        "failed",
        request_id=second_state["request_id"],
        error="storage unavailable",
    ) is True
    failed_state = repository.report_generation_state(
        "batch-report-ledger"
    )
    assert failed_state["status"] == "failed"
    assert failed_state["completed_at"] is not None
    assert failed_state["error"] == "storage unavailable"


def test_terminal_report_candidates_only_include_dispatched_workflow_batches(monkeypatch):
    session_scope = _session_factory(monkeypatch)
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        for batch_id, launch_mode, dispatch_status, report_status in (
            ("eligible-complete", "workflow", "completed", "not_requested"),
            ("eligible-partial", "workflow", "partial", "not_requested"),
            ("still-dispatching", "workflow", "dispatching", "not_requested"),
            ("already-queued", "workflow", "completed", "queued"),
            ("direct-mode", "direct", "completed", "not_requested"),
        ):
            db.add(
                PatrolBatchRecord(
                    patrol_batch_id=batch_id,
                    workflow_name="patrol-change-event",
                    launch_mode=launch_mode,
                    patrol_mode="production",
                    target_type="group",
                    target_label="core",
                    selector_json={},
                    target_count=1,
                    launched_count=1,
                    skipped_count=0,
                    dispatch_status=dispatch_status,
                    report_status=report_status,
                    archived=False,
                    created_at=now,
                    updated_at=now,
                )
            )

    assert PatrolBatchManifestRepository().terminal_report_candidate_ids() == [
        "eligible-complete",
        "eligible-partial",
    ]
