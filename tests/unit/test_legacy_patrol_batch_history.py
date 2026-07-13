from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.patrol import legacy_batch_history
from src.core.patrol.legacy_batch_history import LegacyPatrolBatchHistoryRepository
from src.infrastructure.db.models import PatrolBatchRecord, WorkflowRun


def _session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PatrolBatchRecord.__table__.create(engine)
    WorkflowRun.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    @contextmanager
    def session_scope():
        session = session_factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr(legacy_batch_history, "get_db_session", session_scope)
    return session_scope


def _run(index, batch_id, status, created_at, *, device_name=None, management_ip=None):
    return WorkflowRun(
        id=f"run-{index}",
        template_name="patrol-change-event",
        source="device-management",
        status=status,
        context={
            "patrol_batch_id": batch_id,
            "device_id": f"device-{index}",
            "device_name": device_name or f"SW-{index}",
            "management_ip": management_ip,
        },
        created_at=created_at,
        completed_at=created_at if status in {"completed", "failed", "cancelled"} else None,
    )


def test_legacy_history_counts_all_runs_and_searches_every_device(monkeypatch):
    session_scope = _session_factory(monkeypatch)
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    runs = [
        _run(index, "large-batch", "completed", base, device_name=f"SW-{index:04d}")
        for index in range(1001)
    ]
    runs.append(
        _run(
            2000,
            "older-batch",
            "completed",
            base - timedelta(days=1),
            device_name="deep-target",
            management_ip="10.9.8.7",
        )
    )
    with session_scope() as db:
        db.bulk_save_objects(runs)

    repository = LegacyPatrolBatchHistoryRepository()
    page = repository.list_batches_window(limit=25)
    by_name = repository.list_batches_window(limit=25, search="deep-target")
    by_ip = repository.list_batches_window(limit=25, search="10.9.8.7")

    assert page["total"] == 2
    assert page["items"][0]["total"] == 1001
    assert by_name["total"] == 1
    assert by_name["items"][0]["patrol_batch_id"] == "older-batch"
    assert by_ip["items"][0]["total"] == 1


def test_legacy_history_uses_one_aggregate_status_per_batch(monkeypatch):
    session_scope = _session_factory(monkeypatch)
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with session_scope() as db:
        db.add_all(
            [
                _run(1, "active-mixed", "completed", base),
                _run(2, "active-mixed", "running", base),
                _run(3, "failed-mixed", "completed", base - timedelta(minutes=1)),
                _run(4, "failed-mixed", "failed", base - timedelta(minutes=1)),
                _run(5, "completed", "completed", base - timedelta(minutes=2)),
            ]
        )

    repository = LegacyPatrolBatchHistoryRepository()
    active = repository.list_batches_window(limit=25, status="active")
    failed = repository.list_batches_window(limit=25, status="failed")
    completed = repository.list_batches_window(limit=25, status="completed")

    assert [item["patrol_batch_id"] for item in active["items"]] == ["active-mixed"]
    assert [item["patrol_batch_id"] for item in failed["items"]] == ["failed-mixed"]
    assert [item["patrol_batch_id"] for item in completed["items"]] == ["completed"]
