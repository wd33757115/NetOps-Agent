from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.patrol import batch_manifest
from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
from src.infrastructure.db.models import (
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
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
    assert batches[0]["target_count"] == 2000
    assert batches[0]["completed_count"] == 1500
    assert batches[0]["running_count"] == 499
    assert detached_runs is not None and len(detached_runs) == 1999
    assert detached_runs[0].context["patrol_batch_id"] == "batch-2000"


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
