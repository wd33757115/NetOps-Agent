import asyncio
import csv
import io
import json
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.apps.inspection.backend import online_reads
from src.core.patrol import batch_manifest
from src.core.workflows import patrol_batch_service as summaries
from src.gateway import devices_api
from src.apps.inspection.backend import batch_api as workflow_api
from src.infrastructure.db import models as m


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'reads.db'}")
    for model in (
        m.PatrolBatchRecord,
        m.PatrolBatchDeviceRecord,
        m.PatrolRun,
        m.CollectionExecutionPlanRecord,
        m.WorkflowRun,
        m.WorkflowStepRecord,
        m.CommandSnapshot,
        m.PatrolCollectionResult,
        m.StateObservationRecord,
        m.PatrolAnalysisAttemptRecord,
        m.NetworkChangeRecord,
        m.NetworkEventRecord,
        m.NetworkEventOccurrenceRecord,
        m.RawEvidenceRecord,
    ):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def scope():
        with factory.begin() as db:
            yield db

    for module in (online_reads, summaries, devices_api, batch_manifest):
        monkeypatch.setattr(module, "get_db_session", scope)
    monkeypatch.setattr(
        summaries,
        "_runtime_event_rule_bundle",
        lambda _db: {
            "rules": [],
            "source": "test",
            "digest": None,
        },
    )
    yield scope
    engine.dispose()


def seed(scope, batch="batch", device="device", status="running", phase="normalization"):
    now = datetime.now(timezone.utc)
    task_id = f"task-{batch}-{device}"
    run_id = f"patrol-{task_id}"
    with scope() as db:
        db.add(
            m.PatrolBatchRecord(
                patrol_batch_id=batch,
                workflow_name="patrol-change-event",
                launch_mode="direct",
                patrol_mode="production",
                runtime_manifest_id="manifest",
                runtime_manifest_digest="digest",
                target_type="devices",
                target_label="设备",
                selector_json={},
                target_count=1,
                launched_count=1,
                dispatch_status="completed",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            m.PatrolBatchDeviceRecord(
                id=f"target-{batch}-{device}",
                patrol_batch_id=batch,
                device_id=device,
                device_name=f"SW-{device}",
                selection_status="launched",
                preflight_status="ready",
                dispatch_status="dispatched",
                celery_task_id=task_id,
                created_at=now,
                updated_at=now,
            )
        )
        if status == "queued":
            return run_id
        steps = {"collection": {"success": True, "snapshot_run_id": run_id}}
        if status == "completed" or phase == "events":
            steps.update(
                {name: {"success": True} for name in ("normalization", "changes", "events")}
            )
        db.add(
            m.PatrolRun(
                id=run_id,
                logical_run_id=run_id,
                source="device-patrol",
                mode="production",
                status="completed",
                collection_status="completed",
                analysis_status="pending",
                started_at=now,
                completed_at=now,
                metadata_json={
                    "patrol_batch_id": batch,
                    "raw_outputs_archive": {
                        "download_url": "/api/v1/files/download?key=archive.zip"
                    },
                    "inspection_execution": {
                        "task_id": task_id,
                        "status": status,
                        "phase": phase,
                        "steps": steps,
                        "started_at": now.isoformat(),
                        "completed_at": now.isoformat()
                        if status in {"completed", "failed", "cancelled"}
                        else None,
                        "error": "检查项结论失败" if status == "failed" else None,
                    },
                },
            )
        )
        db.flush()
        db.add(
            m.CommandSnapshot(
                id=f"snapshot-{batch}-{device}",
                run_id=run_id,
                execution_item_id=f"item-{batch}-{device}",
                device_id=device,
                device_name=f"SW-{device}",
                command="show version",
                canonical_command="show version",
                observed_at=now,
                raw_text=f"version {batch}",
                raw_text_hash="a" * 64,
                parse_status="parsed",
                output_status="ok",
                snapshot_kind="full",
            )
        )
    return run_id


def test_summary_reads_fixed_run_without_workflow_steps(database):
    run_id = seed(database)
    seed(database, batch="other")
    result = summaries.build_patrol_batch_summary("batch", [])
    assert result["run_count"] == result["device_count"] == 1
    assert result["status_counts"] == {"running": 1}
    assert result["quality"]["command_count"] == result["quality"]["snapshot_count"] == 1
    assert result["runs"][0]["snapshot_run_id"] == run_id
    assert result["runs"][0]["execution_kind"] == "inspection"
    assert result["scope"]["active_count"] == 1
    assert result["scope"]["completed_count"] == 0
    assert [step["status"] for step in result["pipeline"]["stages"]] == [
        "completed",
        "running",
        "pending",
        "pending",
    ]
    assert all(step["duration"]["sample_count"] == 0 for step in result["pipeline"]["stages"])
    with database() as db:
        assert db.query(m.WorkflowRun).count() == db.query(m.WorkflowStepRecord).count() == 0


def test_queued_batch_has_task_identity_without_invented_run(database):
    seed(database, status="queued")
    result = summaries.build_patrol_batch_summary("batch", [])
    assert result["status_counts"] == {"queued": 1}
    assert result["quality"]["status"] == "not_started"
    assert result["runs"][0]["run_id"] is None
    assert result["runs"][0]["task_id"] == "task-batch-device"
    assert workflow_api._snapshot_run_ids_for_runs([], "batch") == []


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_gate_failure_overrides_returned_stage_result(database, status):
    seed(database, status=status, phase="events")
    result = summaries.build_patrol_batch_summary("batch", [])
    stage = result["pipeline"]["stages"][-1]
    assert stage["status"] == status
    assert stage["completed_count"] == 0
    assert result["status_counts"] == {status: 1}


def test_timeline_filters_by_batch_and_device_and_uses_full_execution_status(database):
    run_id = seed(database)
    seed(database, batch="other")
    seed(database, batch="foreign", device="other-device")
    result = devices_api._device_evidence_timeline({"device_id": "device"}, patrol_batch_id="batch")
    assert len(result["items"]) == 1
    row = result["items"][0]
    assert row["snapshot_run_id"] == run_id
    assert row["patrol_batch_id"] == "batch"
    assert row["workflow_run_id"] is row["workflow_status"] is None
    assert row["execution_status"] == "running"
    assert row["execution_completed_at"] is None
    assert row["collection_status"] == "completed"
    assert row["execution_steps"][1]["status"] == "running"
    assert (
        devices_api._device_evidence_timeline(
            {"device_id": "other-device"},
            patrol_batch_id="batch",
        )["items"]
        == []
    )
    assert (
        devices_api._device_evidence_timeline(
            {"device_id": "device"},
            workflow_run_id="missing",
            patrol_batch_id="batch",
        )["items"]
        == []
    )


def test_timeline_includes_claimed_run_before_any_snapshot(database):
    run_id = seed(database, phase="collection")
    with database() as db:
        db.query(m.CommandSnapshot).delete()
    rows = devices_api._device_evidence_timeline({"device_id": "device"})["items"]
    assert len(rows) == 1
    assert rows[0]["snapshot_run_id"] == run_id
    assert rows[0]["snapshot_count"] == 0
    assert rows[0]["execution_status"] == "running"


async def body(response):
    return "".join(
        [
            value.decode() if isinstance(value, bytes) else value
            async for value in response.body_iterator
        ]
    )


def test_ledger_and_raw_data_exports_resolve_actual_batch_runs(database):
    run_id = seed(database)
    seed(database, batch="other")
    response = asyncio.run(workflow_api.export_patrol_batch_csv("batch"))
    rows = list(csv.DictReader(io.StringIO(response.body.decode())))
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["task_id"] == "task-batch-device"
    assert rows[0]["execution_kind"] == "inspection"
    assert rows[0]["status"] == "running"
    response = asyncio.run(workflow_api.export_patrol_batch_raw_data("batch"))
    raw = asyncio.run(body(response))
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(rows) == 1
    assert rows[0]["snapshot_run_id"] == run_id
    assert "version other" not in raw


def test_raw_cli_export_uses_collector_archive_without_workflow(database, monkeypatch):
    from src.infrastructure.storage import minio_client

    seed(database)
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("version.txt", "version batch")
    storage = Mock()
    storage.is_ready.return_value = True
    storage.download_file.return_value = source.getvalue()
    monkeypatch.setattr(minio_client, "get_minio_storage", lambda: storage)
    monkeypatch.setattr(workflow_api, "_resolve_raw_cli_manifest", lambda *_: (None, []))
    response = asyncio.run(
        workflow_api.export_patrol_batch_raw_cli("batch", report_manifest_id=None)
    )

    async def zip_body():
        return b"".join([chunk async for chunk in response.body_iterator])

    with zipfile.ZipFile(io.BytesIO(asyncio.run(zip_body()))) as archive:
        assert any(archive.read(name) == b"version batch" for name in archive.namelist())
    storage.download_file.assert_called_once_with("archive.zip")


def test_event_export_and_summary_are_scoped_to_fixed_batch(database):
    now = datetime.now(timezone.utc)
    for batch in ("batch", "other"):
        run_id = seed(database, batch=batch, status="completed", phase="completed")
        with database() as db:
            db.add(
                m.NetworkEventRecord(
                    id=f"event-{batch}",
                    idempotency_key=f"event-{batch}",
                    event_type="DeviceFault",
                    severity="major",
                    device_id="device",
                    entity_type="device",
                    entity_key="device",
                    correlation_key=batch,
                    run_id=run_id,
                    occurred_at=now,
                    first_seen_at=now,
                    last_seen_at=now,
                    status="open",
                    payload_json={},
                )
            )
    result = summaries.build_patrol_batch_summary("batch", [])
    assert result["event_count"] == 1
    assert result["events"][0]["id"] == "event-batch"
    assert result["status_counts"] == {"completed": 1}
    response = asyncio.run(workflow_api.export_patrol_batch_events("batch"))
    rows = list(csv.DictReader(io.StringIO(response.body.decode())))
    assert len(rows) == 1
    assert rows[0]["event_id"] == "event-batch"
    assert rows[0]["device_name"] == "SW-device"


def test_fixed_reader_does_not_claim_legacy_collector_runs(database):
    run_id = seed(database)
    with database() as db:
        db.get(m.PatrolRun, run_id).metadata_json = {}
    assert online_reads.read_batch_execution_rows("batch") == []
    assert online_reads.read_batch_execution_rows("unknown") is None


def test_preflight_only_batch_keeps_empty_execution_diagnostic(database):
    seed(database, status="queued")
    with database() as db:
        batch = db.get(m.PatrolBatchRecord, "batch")
        batch.launched_count, batch.skipped_count = 0, 1
        target = db.query(m.PatrolBatchDeviceRecord).one()
        target.celery_task_id = None
        target.preflight_status = "failed"
        target.selection_status = "skipped"
    result = summaries.build_patrol_batch_summary("batch", [])
    assert result["run_count"] == 0
    assert result["quality"]["status"] == "not_started"
    assert result["pipeline"]["stages"] == []
    assert result["scope"]["preflight_failed_count"] == 1


def test_admission_failure_is_visible_without_a_patrol_run(database):
    seed(database, status="queued")
    with database() as db:
        target = db.query(m.PatrolBatchDeviceRecord).one()
        target.dispatch_status = "failed"
        target.error_message = "冻结运行包不匹配"
    result = summaries.build_patrol_batch_summary("batch", [])
    assert result["status_counts"] == {"failed": 1}
    assert result["runs"][0]["run_id"] is None
    assert result["runs"][0]["error_message"] == "冻结运行包不匹配"
    response = asyncio.run(workflow_api.export_patrol_batch_csv("batch"))
    row = next(csv.DictReader(io.StringIO(response.body.decode())))
    assert row["current_step_index"] == ""
    assert row["error_message"] == "冻结运行包不匹配"
