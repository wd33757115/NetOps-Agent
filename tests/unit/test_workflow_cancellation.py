from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core import check_items
from src.core.patrol import batch_manifest
from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
from src.core.workflows import engine as workflow_engine
from src.core.workflows import repository
from src.core.workflows import tasks as workflow_tasks
from src.infrastructure.db.models import (
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    WorkflowRun,
    WorkflowStepRecord,
)


def _session_scope(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PatrolBatchRecord.__table__.create(engine)
    WorkflowRun.__table__.create(engine)
    PatrolBatchDeviceRecord.__table__.create(engine)
    WorkflowStepRecord.__table__.create(engine)
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

    monkeypatch.setattr(repository, "get_db_session", session_scope)
    monkeypatch.setattr(batch_manifest, "get_db_session", session_scope)
    return session_scope


def _add_batch(
    session_scope,
    *,
    batch_id: str,
    run_id: str | None,
    run_status: str = "running",
) -> str:
    now = datetime.now(timezone.utc)
    target_id = f"{batch_id}-target"
    with session_scope() as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id=batch_id,
                workflow_name="patrol-change-event",
                launch_mode="workflow",
                patrol_mode="production",
                target_type="device",
                target_label="SW-1",
                selector_json={},
                target_count=1,
                launched_count=1 if run_id else 0,
                skipped_count=0,
                dispatch_status="completed" if run_id else "pending",
                report_status="not_requested",
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )
        if run_id:
            db.add(
                WorkflowRun(
                    id=run_id,
                    template_name="patrol-change-event",
                    source="device-management",
                    status=run_status,
                    context={"patrol_batch_id": batch_id, "device_id": "device-1"},
                    created_at=now,
                    updated_at=now,
                )
            )
        db.add(
            PatrolBatchDeviceRecord(
                id=target_id,
                patrol_batch_id=batch_id,
                device_id="device-1",
                selection_status="launched" if run_id else "selected",
                preflight_status="ready",
                workflow_run_id=run_id,
                dispatch_status="dispatched" if run_id else "pending",
                dispatch_attempts=1 if run_id else 0,
                created_at=now,
                updated_at=now,
            )
        )
    return target_id


@pytest.mark.parametrize(
    ("failure_code", "skill_name", "expected"),
    [
        ("command_timeout", "event-builder", "collection_failed"),
        ("worker_timeout", "device-patrol", "collection_failed"),
        ("parser_runtime_failed", "device-patrol", "parse_failed"),
        ("worker_timeout", "state-normalizer", "normalization_failed"),
        ("worker_timeout", "change-detector", "comparison_failed"),
        ("worker_timeout", "event-builder", "assessment_failed"),
        ("workflow_queue_timeout", None, "assessment_failed"),
    ],
)
def test_workflow_failure_status_uses_reason_and_failed_skill(
    failure_code,
    skill_name,
    expected,
):
    assert batch_manifest._check_execution_failure_status(
        failure_code=failure_code,
        skill_name=skill_name,
    ) == expected


def test_cancelled_workflow_is_idempotent_and_fences_late_success(monkeypatch):
    session_scope = _session_scope(monkeypatch)
    _add_batch(session_scope, batch_id="batch-cancel", run_id="run-cancel")
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        db.add_all(
            [
                WorkflowStepRecord(
                    id="step-running",
                    run_id="run-cancel",
                    step_index=0,
                    step_name="collect",
                    skill_name="device-patrol",
                    celery_task_id="task-running",
                    status="running",
                    started_at=now,
                    created_at=now,
                ),
                WorkflowStepRecord(
                    id="step-pending",
                    run_id="run-cancel",
                    step_index=1,
                    step_name="normalize",
                    skill_name="state-normalizer",
                    status="pending",
                    created_at=now,
                ),
            ]
        )

    first = repository.cancel_workflow_run(
        "run-cancel",
        actor="operator-1",
        reason="maintenance window closed",
    )
    second = repository.cancel_workflow_run(
        "run-cancel",
        actor="operator-1",
        reason="maintenance window closed",
    )

    assert first["changed"] is True
    assert first["task_ids"] == ["task-running"]
    assert second["changed"] is False
    assert second["idempotent"] is True
    assert repository.mark_step_completed(
        "step-running",
        {"success": True, "message": "late success"},
    ) is False
    assert repository.mark_step_failed(
        "step-running",
        "late worker failure",
    ) is False
    assert repository.update_run_status("run-cancel", "completed") is False
    assert repository.update_run_status(
        "run-cancel",
        "failed",
        error="late worker failure",
    ) is False

    with session_scope() as db:
        run = db.get(WorkflowRun, "run-cancel")
        steps = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.run_id == "run-cancel")
            .order_by(WorkflowStepRecord.step_index)
            .all()
        )
        target = db.get(PatrolBatchDeviceRecord, "batch-cancel-target")
        assert run.status == "cancelled"
        assert run.context["failure_code"] == "workflow_cancelled"
        assert [step.status for step in steps] == ["cancelled", "cancelled"]
        assert target.dispatch_status == "cancelled"


def test_failed_workflow_preserves_stable_reason_on_run_and_device(monkeypatch):
    session_scope = _session_scope(monkeypatch)
    target_id = _add_batch(
        session_scope,
        batch_id="batch-command-timeout",
        run_id="run-command-timeout",
    )

    assert repository.update_run_status(
        "run-command-timeout",
        "failed",
        error="show interface status exceeded 30 seconds",
        failure_code="command_timeout",
    ) is True

    with session_scope() as db:
        run = db.get(WorkflowRun, "run-command-timeout")
        target = db.get(PatrolBatchDeviceRecord, target_id)
        assert run.status == "failed"
        assert run.context["failure_code"] == "command_timeout"
        assert target.failure_code == "command_timeout"
        assert target.error_message == (
            "show interface status exceeded 30 seconds"
        )


def test_workflow_engine_propagates_skill_failure_code(monkeypatch):
    run = SimpleNamespace(
        id="run-command-timeout",
        status="running",
        template_name="patrol-change-event",
        context={"patrol_batch_id": "batch-command-timeout"},
    )
    step = SimpleNamespace(
        id="step-command-timeout",
        step_name="collect",
        skill_name="device-patrol",
    )
    updates: list[dict] = []

    monkeypatch.setattr(workflow_engine, "get_workflow_run", lambda _id: run)
    monkeypatch.setattr(
        workflow_engine,
        "list_workflow_steps",
        lambda _id: [step],
    )
    monkeypatch.setattr(
        workflow_engine,
        "mark_step_completed",
        lambda _id, _result: True,
    )

    def update_status(run_id, status, **kwargs):
        updates.append({"run_id": run_id, "status": status, **kwargs})
        return True

    monkeypatch.setattr(workflow_engine, "update_run_status", update_status)
    monkeypatch.setattr(
        workflow_engine,
        "record_workflow_step",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        workflow_engine,
        "publish_workflow_event",
        lambda *_args, **_kwargs: None,
    )
    for name in (
        "_project_source_terminal_state",
        "_finish_trace",
        "_notify_failure",
        "_request_patrol_batch_report_if_terminal",
    ):
        monkeypatch.setattr(
            workflow_engine.WorkflowEngine,
            name,
            lambda *_args, **_kwargs: None,
        )

    workflow_engine.WorkflowEngine.handle_step_complete(
        "run-command-timeout",
        0,
        {
            "success": False,
            "error": "command exceeded deadline",
            "failure_code": "command_timeout",
        },
    )

    assert updates == [
        {
            "run_id": "run-command-timeout",
            "status": "failed",
            "error": "command exceeded deadline",
            "failure_code": "command_timeout",
        }
    ]


def test_cancelled_unlaunched_target_can_settle_terminal_report(monkeypatch):
    session_scope = _session_scope(monkeypatch)
    target_id = _add_batch(
        session_scope,
        batch_id="batch-pending-cancel",
        run_id=None,
    )
    repository_under_test = PatrolBatchManifestRepository()

    result = repository_under_test.cancel_dispatch(
        "batch-pending-cancel",
        actor="operator-1",
        reason="scope is no longer valid",
    )

    assert result is not None
    assert result["changed"] is True
    assert result["device_ids"] == ["device-1"]
    assert repository_under_test.finalize_dispatch("batch-pending-cancel") == "cancelled"
    assert repository_under_test.request_terminal_report("batch-pending-cancel") is True

    with session_scope() as db:
        batch = db.get(PatrolBatchRecord, "batch-pending-cancel")
        target = db.get(PatrolBatchDeviceRecord, target_id)
        assert batch.dispatch_status == "cancelled"
        assert batch.report_status == "queued"
        assert target.dispatch_status == "cancelled"
        assert target.failure_code == "patrol_batch_cancelled"


def test_cancelled_run_never_enters_skill_execution(monkeypatch):
    monkeypatch.setattr(
        workflow_tasks,
        "get_workflow_run",
        lambda _run_id: SimpleNamespace(status="cancelled"),
    )
    monkeypatch.setattr(
        workflow_tasks,
        "list_workflow_steps",
        lambda _run_id: (_ for _ in ()).throw(
            AssertionError("cancelled workflow must not load or execute a step")
        ),
    )

    result = workflow_tasks._run_workflow_step("run-cancelled", 0)

    assert result == {
        "success": False,
        "ignored": True,
        "error": "workflow already cancelled",
        "step_index": 0,
    }


def test_runtime_manifest_reconciliation_settles_unlaunched_terminal_targets(
    monkeypatch,
):
    session_scope = _session_scope(monkeypatch)
    _add_batch(
        session_scope,
        batch_id="batch-terminal-targets",
        run_id=None,
    )
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        batch = db.get(PatrolBatchRecord, "batch-terminal-targets")
        batch.runtime_manifest_id = "runtime-manifest-1"
        cancelled = db.get(
            PatrolBatchDeviceRecord,
            "batch-terminal-targets-target",
        )
        cancelled.dispatch_status = "cancelled"
        cancelled.failure_code = "patrol_batch_cancelled"
        db.add_all(
            [
                PatrolBatchDeviceRecord(
                    id="target-dispatch-failed",
                    patrol_batch_id="batch-terminal-targets",
                    device_id="device-2",
                    selection_status="selected",
                    preflight_status="failed",
                    dispatch_status="failed",
                    dispatch_attempts=3,
                    failure_code="credential_missing",
                    created_at=now,
                    updated_at=now,
                ),
                PatrolBatchDeviceRecord(
                    id="target-skipped",
                    patrol_batch_id="batch-terminal-targets",
                    device_id="device-3",
                    selection_status="skipped",
                    preflight_status="skipped",
                    dispatch_status="failed",
                    dispatch_attempts=0,
                    failure_code="not_selected",
                    created_at=now,
                    updated_at=now,
                ),
                WorkflowRun(
                    id="run-command-timeout",
                    template_name="patrol-change-event",
                    source="device-management",
                    status="failed",
                    context={
                        "patrol_batch_id": "batch-terminal-targets",
                        "device_id": "device-4",
                        "failure_code": "command_timeout",
                    },
                    error_message="command exceeded deadline",
                    created_at=now,
                    updated_at=now,
                    completed_at=now,
                ),
                WorkflowStepRecord(
                    id="step-command-timeout-reconcile",
                    run_id="run-command-timeout",
                    step_index=0,
                    step_name="patrol",
                    skill_name="device-patrol",
                    status="failed",
                    error_message="command exceeded deadline",
                    created_at=now,
                    completed_at=now,
                ),
                PatrolBatchDeviceRecord(
                    id="target-workflow-failed",
                    patrol_batch_id="batch-terminal-targets",
                    device_id="device-4",
                    selection_status="launched",
                    preflight_status="ready",
                    workflow_run_id="run-command-timeout",
                    dispatch_status="dispatched",
                    dispatch_attempts=1,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )

    calls: list[dict] = []

    class _Finalizer:
        def fail_devices(self, **kwargs):
            calls.append(kwargs)
            return {"failed_execution_count": len(kwargs["failures"])}

    monkeypatch.setattr(check_items, "CheckItemRunFinalizer", _Finalizer)

    PatrolBatchManifestRepository._reconcile_failed_check_item_workflows(
        "batch-terminal-targets"
    )

    assert calls == [
        {
            "patrol_batch_id": "batch-terminal-targets",
            "failures": [
                {
                    "device_id": "device-1",
                    "reason_code": "patrol_batch_cancelled",
                    "status": "cancelled",
                    "evidence_refs": (),
                },
                {
                    "device_id": "device-2",
                    "reason_code": "credential_missing",
                    "status": "collection_failed",
                    "evidence_refs": (),
                },
                {
                    "device_id": "device-4",
                    "reason_code": "command_timeout",
                    "status": "collection_failed",
                    "evidence_refs": ("run-command-timeout",),
                },
            ],
        },
    ]


def test_runtime_manifest_reconciliation_uses_snapshot_run_and_failed_stage(
    monkeypatch,
):
    session_scope = _session_scope(monkeypatch)
    _add_batch(
        session_scope,
        batch_id="batch-normalizer-timeout",
        run_id="workflow-normalizer-timeout",
        run_status="failed",
    )
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        batch = db.get(PatrolBatchRecord, "batch-normalizer-timeout")
        batch.runtime_manifest_id = "runtime-manifest-normalizer-timeout"
        run = db.get(WorkflowRun, "workflow-normalizer-timeout")
        run.current_step_index = 1
        run.context = {
            **dict(run.context or {}),
            "failure_code": "worker_timeout",
        }
        db.add_all(
            [
                WorkflowStepRecord(
                    id="step-normalizer-patrol",
                    run_id=run.id,
                    step_index=0,
                    step_name="patrol",
                    skill_name="device-patrol",
                    status="completed",
                    result={
                        "success": True,
                        "snapshot_run_id": "patrol-run-normalizer-timeout",
                    },
                    created_at=now,
                    completed_at=now,
                ),
                WorkflowStepRecord(
                    id="step-normalizer-failed",
                    run_id=run.id,
                    step_index=1,
                    step_name="state_normalization",
                    skill_name="state-normalizer",
                    status="failed",
                    result={
                        "success": False,
                        "failure_code": "worker_timeout",
                    },
                    created_at=now,
                    completed_at=now,
                ),
            ]
        )

    calls: list[dict] = []

    class _Finalizer:
        def fail_devices(self, **kwargs):
            calls.append(kwargs)
            return {"failed_execution_count": len(kwargs["failures"])}

    monkeypatch.setattr(check_items, "CheckItemRunFinalizer", _Finalizer)

    PatrolBatchManifestRepository._reconcile_failed_check_item_workflows(
        "batch-normalizer-timeout"
    )

    assert calls == [
        {
            "patrol_batch_id": "batch-normalizer-timeout",
            "failures": [
                {
                    "device_id": "device-1",
                    "status": "normalization_failed",
                    "reason_code": "worker_timeout",
                    "evidence_refs": ("workflow-normalizer-timeout",),
                    "run_id": "patrol-run-normalizer-timeout",
                }
            ],
        }
    ]
