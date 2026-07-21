from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.workflows import repository as workflow_repository
from src.infrastructure.db.models import (
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    WorkflowRun,
    WorkflowStepRecord,
)


def _session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PatrolBatchRecord.__table__.create(engine)
    WorkflowRun.__table__.create(engine)
    WorkflowStepRecord.__table__.create(engine)
    PatrolBatchDeviceRecord.__table__.create(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(workflow_repository, "get_db_session", session_scope)
    return session_scope


def _add_patrol_workflow(
    session_scope,
    *,
    run_id: str,
    status: str,
    updated_at: datetime,
) -> None:
    now = datetime.now(timezone.utc)
    batch_id = f"batch-{run_id}"
    with session_scope() as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id=batch_id,
                workflow_name="patrol-change-event",
                launch_mode="workflow",
                patrol_mode="production",
                target_type="selected",
                target_label="one device",
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
                id=run_id,
                template_name="patrol-change-event",
                source="device-management",
                status=status,
                context={"patrol_batch_id": batch_id, "device_id": "device-1"},
                created_at=updated_at,
                updated_at=updated_at,
            )
        )
        db.add(
            WorkflowStepRecord(
                id=f"step-{run_id}",
                run_id=run_id,
                step_index=0,
                step_name="collect",
                skill_name="device-patrol",
                status=status if status in {"queued", "running"} else "pending",
                created_at=updated_at,
            )
        )
        db.add(
            PatrolBatchDeviceRecord(
                id=f"target-{run_id}",
                patrol_batch_id=batch_id,
                device_id="device-1",
                selection_status="launched",
                preflight_status="ready",
                dispatch_status="dispatched",
                workflow_run_id=run_id,
                created_at=now,
                updated_at=now,
            )
        )


def test_reconciles_stale_running_patrol_workflow_with_failure_evidence(monkeypatch):
    session_scope = _session_factory(monkeypatch)
    _add_patrol_workflow(
        session_scope,
        run_id="run-stale",
        status="running",
        updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    result = workflow_repository.reconcile_stale_patrol_workflow_runs(
        queued_stale_seconds=14_400,
        running_stale_seconds=900,
    )

    assert result == {
        "reconciled_count": 1,
        "run_ids": ["run-stale"],
        "failure_counts": {"workflow_heartbeat_expired": 1},
    }
    with session_scope() as db:
        run = db.get(WorkflowRun, "run-stale")
        target = db.get(PatrolBatchDeviceRecord, "target-run-stale")
        step = db.get(WorkflowStepRecord, "step-run-stale")
        assert run.status == "failed"
        assert run.completed_at is not None
        assert run.context["failure_code"] == "workflow_heartbeat_expired"
        assert run.context["retryable"] is True
        assert target.failure_code == "workflow_heartbeat_expired"
        assert target.error_message == run.error_message
        assert step.status == "failed"
        assert step.completed_at is not None

    second = workflow_repository.reconcile_stale_patrol_workflow_runs(
        queued_stale_seconds=14_400,
        running_stale_seconds=900,
    )
    assert second["reconciled_count"] == 0


def test_reconciler_uses_longer_queue_timeout_and_ignores_non_patrol_runs(monkeypatch):
    session_scope = _session_factory(monkeypatch)
    now = datetime.now(timezone.utc)
    _add_patrol_workflow(
        session_scope,
        run_id="run-queued-recently",
        status="queued",
        updated_at=now - timedelta(hours=1),
    )
    with session_scope() as db:
        db.add(
            WorkflowRun(
                id="unrelated-run",
                template_name="other-workflow",
                source="chat",
                status="running",
                context={},
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
        )

    result = workflow_repository.reconcile_stale_patrol_workflow_runs(
        queued_stale_seconds=14_400,
        running_stale_seconds=900,
    )

    assert result["reconciled_count"] == 0
    with session_scope() as db:
        assert db.get(WorkflowRun, "run-queued-recently").status == "queued"
        assert db.get(WorkflowRun, "unrelated-run").status == "running"


def test_reconciles_patrol_workflow_that_exceeds_queue_slo(monkeypatch):
    session_scope = _session_factory(monkeypatch)
    _add_patrol_workflow(
        session_scope,
        run_id="run-queued-stale",
        status="queued",
        updated_at=datetime.now(timezone.utc) - timedelta(hours=5),
    )

    result = workflow_repository.reconcile_stale_patrol_workflow_runs(
        queued_stale_seconds=14_400,
        running_stale_seconds=900,
    )

    assert result["failure_counts"] == {"workflow_queue_timeout": 1}
    with session_scope() as db:
        run = db.get(WorkflowRun, "run-queued-stale")
        target = db.get(PatrolBatchDeviceRecord, "target-run-queued-stale")
        assert run.status == "failed"
        assert run.context["failure_code"] == "workflow_queue_timeout"
        assert target.failure_code == "workflow_queue_timeout"
