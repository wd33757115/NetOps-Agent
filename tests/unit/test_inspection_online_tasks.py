import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.apps.inspection.backend import online_execution as ledger
from src.apps.inspection.backend import online_tasks as tasks
from src.apps.inspection.backend.online_reads import all_targets_terminal
from src.core.patrol import batch_manifest
from src.infrastructure.db.models import (
    CollectionExecutionPlanRecord as Plan,
)
from src.infrastructure.db.models import (
    PatrolBatchDeviceRecord as Target,
)
from src.infrastructure.db.models import (
    PatrolBatchRecord as Batch,
)
from src.infrastructure.db.models import (
    PatrolRun,
    WorkflowRun,
)

TASK_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "patrol-task|batch|device"))
RUN_ID = f"patrol-{TASK_ID}"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from src.apps.inspection.backend import access
    monkeypatch.setattr(access, "execution_lock", lambda **kwargs: nullcontext())
    monkeypatch.setattr(access, "require_running", lambda: None)
    monkeypatch.setattr(access, "authorize", lambda actor: None)
    monkeypatch.setattr(tasks, "app_execution_lock", lambda: nullcontext())
    monkeypatch.setattr(
        tasks, "authorize_batch", lambda batch_id: SimpleNamespace(user_id="online-user"), raising=False
    )
    monkeypatch.setattr(tasks, "authorize", lambda actor: None, raising=False)
    engine = create_engine(f"sqlite:///{tmp_path / 'online.db'}")
    for model in (Plan, Batch, Target, PatrolRun, WorkflowRun):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def scope():
        with factory.begin() as db:
            yield db

    for module in (ledger, tasks, batch_manifest):
        monkeypatch.setattr(module, "get_db_session", scope)
    now = datetime.now(timezone.utc)
    with scope() as db:
        db.add(
            Batch(
                patrol_batch_id="batch",
                workflow_name="patrol-change-event",
                launch_mode="direct",
                patrol_mode="production",
                target_type="devices",
                target_label="测试设备",
                selector_json={},
                target_count=1,
                launched_count=1,
                runtime_manifest_id="manifest",
                runtime_manifest_digest="digest",
                dispatch_status="completed",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            Plan(
                id="plan",
                device_id="device",
                action="patrol",
                mode="production",
                profile_name="profile",
                policy_source="test",
                plan_json={
                    "device_id": "device",
                    "runtime_manifest_id": "manifest",
                    "runtime_manifest_digest": "digest",
                },
                plan_sha256="plan-digest",
                patrol_batch_id="batch",
                status="launched",
            )
        )
        db.add(
            Target(
                id="target",
                patrol_batch_id="batch",
                device_id="device",
                selection_status="launched",
                preflight_status="ready",
                dispatch_status="dispatched",
                execution_plan_id="plan",
                celery_task_id=TASK_ID,
                created_at=now,
                updated_at=now,
            )
        )
    calls = []

    def collect(self, command):
        calls.append(("collection", command))
        assert command.defer_completion
        assert command.actor_id == "online-user"
        assert command.source_app == "inspection"
        with scope() as db:
            run = db.get(PatrolRun, RUN_ID)
            assert run.owner_token == command.owner_token
            assert ledger.execution_state(run)["status"] == "running"
            assert not (run.metadata_json or {}).get("celery_task_completed")
            run.status = run.collection_status = "completed"
            run.completed_at = datetime.now(timezone.utc)
        return {
            "success": True,
            "snapshot_run_id": RUN_ID,
            "runtime_asset_manifest": {"manifest_kind": "check_item_runtime", "digest": "frozen"},
        }

    def step(phase):
        def call(self, params):
            calls.append((phase, params))
            assert (
                batch_manifest.PatrolBatchManifestRepository().request_terminal_report("batch")
                is False
            )
            with scope() as db:
                targets = db.query(Target).all()
                assert not all_targets_terminal(db, targets)
                assert ledger.execution_state(db.get(PatrolRun, RUN_ID))["status"] == "running"
            return {"success": True, "check_item_assessment": {"success": True}}

        return call

    monkeypatch.setattr(tasks.ExecutePatrolHandler, "handle", collect)
    monkeypatch.setattr(tasks.NormalizePatrolHandler, "handle", step("normalization"))
    monkeypatch.setattr(tasks.DetectChangesHandler, "handle", step("changes"))
    monkeypatch.setattr(tasks.BuildEventsHandler, "handle", step("events"))
    settle, report = Mock(), Mock(return_value=True)
    monkeypatch.setattr(tasks.CheckItemRunFinalizer, "fail_device", settle)
    monkeypatch.setattr(tasks, "request_terminal_report", report)
    yield scope, calls, settle, report
    engine.dispose()


def deliver(task_id=TASK_ID):
    tasks.execute_online.push_request(id=task_id, hostname="test-worker")
    try:
        return tasks.execute_online.run("batch", "plan")
    finally:
        tasks.execute_online.pop_request()


def test_online_flow_completes_analysis_before_completion_and_report(runtime):
    scope, calls, settle, report = runtime
    assert deliver()["success"]
    assert [name for name, _ in calls] == ["collection", "normalization", "changes", "events"]
    assert calls[1][1]["update_current_state"] is True
    assert calls[2][1]["runtime_asset_manifest"] == calls[3][1]["runtime_asset_manifest"]
    assert calls[3][1]["patrol_batch_id"] == "batch"
    assert calls[3][1]["publish"] is False
    with scope() as db:
        run = db.get(PatrolRun, RUN_ID)
        assert ledger.execution_state(run)["status"] == "completed"
        assert len(ledger.execution_state(run)["steps"]) == 4
        assert run.metadata_json["celery_task_completed"]
        assert all_targets_terminal(db, db.query(Target).all())
        assert db.query(WorkflowRun).count() == 0
    settle.assert_not_called()
    report.assert_called_once_with("batch", run_id=RUN_ID)
    assert deliver()["ignored"]
    assert len(calls) == 4


@pytest.mark.parametrize(
    "phase,handler,status",
    [
        ("normalization", "NormalizePatrolHandler", "normalization_failed"),
        ("changes", "DetectChangesHandler", "comparison_failed"),
        ("events", "BuildEventsHandler", "assessment_failed"),
    ],
)
def test_stage_failure_is_a_terminal_failure_not_a_healthy_result(
    runtime, monkeypatch, phase, handler, status
):
    scope, calls, settle, report = runtime
    monkeypatch.setattr(
        getattr(tasks, handler), "handle", lambda *a: {"success": False, "error": "failed rule"}
    )
    assert deliver()["status"] == "failed"
    settle.assert_called_once()
    assert settle.call_args.kwargs["status"] == status
    with scope() as db:
        state = ledger.execution_state(db.get(PatrolRun, RUN_ID))
        assert state["phase"] == phase
        assert "failed rule" in state["error"]
        assert state["steps"]["collection"]["snapshot_run_id"] == RUN_ID
    report.assert_called_once()
    if phase == "normalization":
        assert [c[0] for c in calls] == ["collection"]


def test_nested_check_item_assessment_failure_is_not_reported_as_success(runtime, monkeypatch):
    _, _, settle, _ = runtime
    monkeypatch.setattr(
        tasks.BuildEventsHandler,
        "handle",
        lambda *a: {
            "success": True,
            "check_item_assessment": {"success": False, "failures": ["missing evidence"]},
        },
    )
    assert deliver()["status"] == "failed"
    assert settle.call_args.kwargs["status"] == "assessment_failed"


def test_cancel_during_collection_stops_analysis_and_settles_cancelled(runtime, monkeypatch):
    scope, calls, settle, _ = runtime
    original = tasks.ExecutePatrolHandler.handle

    def collect_then_cancel(self, command):
        result = original(self, command)
        batch_manifest.PatrolBatchManifestRepository().cancel_dispatch(
            "batch", actor="owner", reason="stop"
        )
        with scope() as db:
            assert not all_targets_terminal(db, db.query(Target).all())
        return result

    monkeypatch.setattr(tasks.ExecutePatrolHandler, "handle", collect_then_cancel)
    assert deliver()["status"] == "cancelled"
    assert [c[0] for c in calls] == ["collection"]
    assert settle.call_args.kwargs["status"] == "cancelled"
    with scope() as db:
        assert all_targets_terminal(db, db.query(Target).all())
        assert ledger.execution_state(db.get(PatrolRun, RUN_ID))["status"] == "cancelled"


def test_queued_cancellation_and_wrong_task_identity_never_collect(runtime):
    scope, calls, _, _ = runtime
    with pytest.raises(ValueError, match="不匹配"):
        deliver("forged-task")
    with scope() as db:
        db.get(Target, "target").dispatch_status = "cancelled"
    assert deliver()["ignored"]
    assert not calls


def test_stale_owner_cannot_finish_new_execution_state(runtime):
    scope, _, _, _ = runtime
    repository = ledger.OnlinePatrolExecution()
    execution = repository.claim("batch", "plan", TASK_ID)
    with scope() as db:
        db.get(PatrolRun, RUN_ID).owner_token = "replacement"
    with pytest.raises(ledger.OnlinePatrolFencedError):
        repository.checkpoint(execution, "events", {})
    assert repository.finish(execution, results={}) is False
    with scope() as db:
        assert ledger.execution_state(db.get(PatrolRun, RUN_ID))["status"] == "running"


def test_running_redelivery_is_not_a_second_ssh_collection(runtime):
    _, calls, _, _ = runtime
    ledger.OnlinePatrolExecution().claim("batch", "plan", TASK_ID)
    assert deliver()["ignored"]
    assert not calls


def test_worker_that_lost_ownership_cannot_fail_new_check_item_results(runtime, monkeypatch):
    scope, _, settle, report = runtime

    def steal_and_fail(self, command):
        with scope() as db:
            db.get(PatrolRun, RUN_ID).owner_token = "new-owner"
        raise ValueError("old worker lost ownership")

    monkeypatch.setattr(tasks.ExecutePatrolHandler, "handle", steal_and_fail)
    assert deliver()["reason"] == "ownership_lost"
    settle.assert_not_called()
    report.assert_not_called()


def test_recovery_preserves_evidence_and_fails_interrupted_attempt_without_recollection(runtime):
    scope, calls, settle, report = runtime
    repository = ledger.OnlinePatrolExecution()
    execution = repository.claim("batch", "plan", TASK_ID)
    repository.checkpoint(execution, "changes", {"collection": {"snapshot_run_id": RUN_ID}})
    with scope() as db:
        db.get(PatrolRun, RUN_ID).updated_at = datetime.now(timezone.utc) - timedelta(minutes=66)
    assert tasks.recover_online.run()["interrupted"] == [RUN_ID]
    assert not calls
    assert settle.call_args.kwargs["status"] == "comparison_failed"
    with scope() as db:
        state = ledger.execution_state(db.get(PatrolRun, RUN_ID))
        assert state["status"] == "failed"
        assert state["steps"]["collection"]["snapshot_run_id"] == RUN_ID
    report.assert_called_once()
    assert deliver()["ignored"]


def test_batch_reads_and_cancellation_use_direct_execution_facts(runtime):
    scope, _, _, _ = runtime
    repository = batch_manifest.PatrolBatchManifestRepository()
    ledger.OnlinePatrolExecution().claim("batch", "plan", TASK_ID)
    active = repository.list_batches_page(status="active")
    assert active["total"] == 1 and active["items"][0]["running_count"] == 1
    assert repository.list_batches_page(status="completed")["total"] == 0
    execution = {"run_id": RUN_ID, "patrol_batch_id": "batch", "execution_plan_id": "plan"}
    with scope() as db:
        execution["owner_token"] = db.get(PatrolRun, RUN_ID).owner_token
    ledger.OnlinePatrolExecution().finish(execution, results={})
    completed = repository.list_batches_page(status="completed")
    assert completed["total"] == 1 and completed["items"][0]["completed_count"] == 1
    device = repository.list_devices("batch")["items"][0]
    assert device["execution_status"] == "completed" and device["celery_task_id"] == TASK_ID
    assert device["workflow_run_id"] is None
    assert repository.cancel_dispatch("batch", actor="owner", reason="stop")["cancellable"] is False


def test_direct_production_dispatches_only_frozen_ids_and_shadow_keeps_collection_task(monkeypatch):
    from src.core.celery_tasks.tasks import execute_device_patrol_task
    from src.core.patrol.handlers import DispatchPatrolBatchHandler

    online, capture = Mock(return_value=Mock(id="task")), Mock(return_value=Mock(id="task"))
    monkeypatch.setattr(tasks.execute_online, "apply_async", online)
    monkeypatch.setattr(execute_device_patrol_task, "apply_async", capture)
    for mode in ("production", "parser_validation"):
        DispatchPatrolBatchHandler._launch_task(
            task_id="task",
            mode=mode,
            execution_plan={"untrusted": True},
            execution_plan_id="plan",
            patrol_batch_id="batch",
        )
    assert online.call_args.kwargs["kwargs"] == {
        "execution_plan_id": "plan",
        "patrol_batch_id": "batch",
    }
    assert capture.call_args.kwargs["kwargs"]["mode"] == "parser_validation"


def test_actual_postgres_execution_lock_prevents_duplicate_worker():
    from concurrent.futures import ThreadPoolExecutor

    with ledger.get_db_session() as db:
        if db.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL advisory locks require an isolated PostgreSQL fixture")

    task_id = f"test-online-{uuid.uuid4()}"

    def attempt():
        with ledger.execution_lock(task_id):
            return True

    with ThreadPoolExecutor(max_workers=1) as pool:
        with ledger.execution_lock(task_id):
            with pytest.raises(ledger.OnlinePatrolBusyError):
                pool.submit(attempt).result(timeout=10)
        assert pool.submit(attempt).result(timeout=10)


def test_recovery_redelivers_unclaimed_target_with_original_task_id(runtime, monkeypatch):
    scope, calls, _, _ = runtime
    with scope() as db:
        db.get(Target, "target").updated_at = datetime.now(timezone.utc) - timedelta(minutes=3)
    dispatch = Mock()
    monkeypatch.setattr(tasks.execute_online, "apply_async", dispatch)
    assert tasks.recover_online.run()["queued"] == 1
    assert not calls
    dispatch.assert_called_once_with(
        kwargs={"patrol_batch_id": "batch", "execution_plan_id": "plan"},
        task_id=TASK_ID,
        queue="netops.patrol",
    )
    dispatch.side_effect = RuntimeError("broker unavailable")
    result = tasks.recover_online.run()
    assert result["queued"] == 0
    assert result["dispatch_errors"][0]["task_id"] == TASK_ID
    with scope() as db:
        assert db.query(PatrolRun).count() == 0
        assert db.get(Target, "target").dispatch_status == "dispatched"


def test_invalid_frozen_plan_is_visible_and_not_requeued_forever(runtime):
    scope, calls, settle, report = runtime
    with scope() as db:
        plan = db.get(Plan, "plan")
        plan.plan_json = {**plan.plan_json, "runtime_manifest_digest": "tampered"}
    assert deliver()["status"] == "failed"
    assert not calls
    settle.assert_called_once()
    report.assert_called_once()
    with scope() as db:
        target = db.get(Target, "target")
        assert target.dispatch_status == "failed"
        assert target.failure_code == "invalid_frozen_plan"
    failures = batch_manifest.PatrolBatchManifestRepository().list_batches_page(status="failed")
    assert failures["total"] == 1 and failures["items"][0]["failed_count"] == 1
    assert deliver()["ignored"]


def test_collection_repository_preserves_fixed_execution_reservation(runtime, monkeypatch):
    from src.core.patrol import repository as collection
    from src.infrastructure.db.models import CommandSnapshot, PatrolCollectionResult

    scope, _, _, _ = runtime
    execution = ledger.OnlinePatrolExecution().claim("batch", "plan", TASK_ID, "worker")
    monkeypatch.setattr(collection, "get_db_session", scope)
    with scope() as db:
        CommandSnapshot.__table__.create(db.get_bind())
        PatrolCollectionResult.__table__.create(db.get_bind())
    repository = collection.PatrolRunRepositoryBackend()
    repository.create_run(
        run_id=RUN_ID,
        logical_run_id=RUN_ID,
        source="device-patrol",
        mode="production",
        owner_token=execution["owner_token"],
        worker_id="worker",
        metadata={"runtime_asset_manifest": {"manifest_kind": "check_item_runtime"}},
    )
    repository.finish_run(
        RUN_ID, owner_token=execution["owner_token"], collection_status="completed"
    )
    with scope() as db:
        run = db.get(PatrolRun, RUN_ID)
        assert run.collection_status == "completed"
        assert ledger.execution_state(run)["status"] == "running"
        assert not run.metadata_json.get("celery_task_completed")
        assert not all_targets_terminal(db, db.query(Target).all())



def test_revoked_app_grant_prevents_collection_and_records_failure(runtime, monkeypatch):
    from fastapi import HTTPException
    scope, calls, settle, report = runtime
    def denied(batch_id):
        raise HTTPException(403, "应用权限已撤销")
    monkeypatch.setattr(tasks, "authorize_batch", denied)
    result = deliver()
    assert not result["success"]
    assert calls == []
    with scope() as db:
        assert ledger.execution_state(db.get(PatrolRun, RUN_ID))["status"] == "failed"
    assert settle.call_count == 1


def test_attested_replay_uses_reserved_run_without_live_collection(runtime, monkeypatch):
    from src.core.check_items.current_authority_replay import AttestedOfflineCurrentReplayService
    scope, calls, _, _ = runtime
    with scope() as db:
        db.get(Batch, "batch").workflow_name = "patrol-attested-offline-current"
    def replay(self, **kwargs):
        assert kwargs["snapshot_run_id"] == RUN_ID
        assert kwargs["execution_plan_id"] == "plan"
        with scope() as db:
            assert db.get(PatrolRun, RUN_ID).owner_token == kwargs["owner_token"]
        return {"success": True, "snapshot_run_id": RUN_ID, "runtime_asset_manifest": {"manifest_kind": "check_item_runtime"}}
    monkeypatch.setattr(AttestedOfflineCurrentReplayService, "execute", replay)
    assert deliver()["success"]
    assert [name for name, _ in calls] == ["normalization", "changes", "events"]
    with scope() as db:
        assert db.query(WorkflowRun).count() == 0
