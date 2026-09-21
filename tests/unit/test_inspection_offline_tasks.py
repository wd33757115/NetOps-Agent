import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.apps.inspection.backend import offline_tasks as tasks
from src.core.patrol import offline_job_lock, offline_jobs
from src.infrastructure.db.models import OfflinePatrolJobRecord as Job
from src.infrastructure.db.models import OfflinePatrolPeriodRecord as Period

real_import_period = offline_jobs.OfflinePatrolJobService.import_period


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from src.apps.inspection.backend import access
    monkeypatch.setattr(access, "execution_lock", lambda **kwargs: nullcontext())
    monkeypatch.setattr(access, "require_running", lambda: None)
    monkeypatch.setattr(access, "authorize", lambda actor: None)
    monkeypatch.setattr(tasks, "app_execution_lock", lambda: nullcontext())
    monkeypatch.setattr(tasks, "authorize_batch", lambda batch_id: None, raising=False)
    monkeypatch.setattr(tasks, "authorize", lambda actor: None, raising=False)
    engine = create_engine(f"sqlite:///{tmp_path / 'offline.db'}")
    Job.__table__.create(engine)
    Period.__table__.create(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def scope():
        with factory.begin() as db:
            yield db

    for module in (tasks, offline_jobs, offline_job_lock):
        monkeypatch.setattr(module, "get_db_session", scope)
    with scope() as db:
        db.add(
            Job(
                id="job",
                idempotency_key="create",
                owner_id="owner",
                name="历史对比",
                purpose="offline_patrol",
                job_type="history_compare",
                status="ready",
                attempt_number=0,
            )
        )
        for role in ("baseline", "current"):
            db.add(
                Period(
                    id=role,
                    job_id="job",
                    role=role,
                    label=role,
                    dataset_id=role,
                    observed_at=datetime.now(timezone.utc),
                )
            )
    calls = []

    def step(name, **result):
        def run(*args, **kwargs):
            calls.append((name, args, kwargs))
            return {"success": True, **result}

        return run

    monkeypatch.setattr(
        offline_jobs.OfflinePatrolJobService, "execution_quality_gate", step("gate")
    )
    monkeypatch.setattr(
        offline_jobs.OfflinePatrolJobService,
        "import_period",
        lambda self, job, role, **kw: step("import:" + role, run_id=role)(**kw),
    )
    monkeypatch.setattr(tasks.NormalizePatrolHandler, "handle", step("normalize"))
    monkeypatch.setattr(tasks.DetectChangesHandler, "handle", step("changes"))
    monkeypatch.setattr(tasks.BuildEventsHandler, "handle", step("events"))
    monkeypatch.setattr(
        tasks,
        "render_run_report",
        step(
            "report",
            run_id="current",
            markdown="# Report",
            observation_count=3,
            change_count=2,
            event_count=1,
        ),
    )
    storage = Mock()
    storage.upload_file.return_value = True
    storage.get_presigned_url.return_value = "/download/report"
    monkeypatch.setattr(tasks, "get_minio_storage", lambda: storage)
    dispatch = Mock(return_value=True)
    monkeypatch.setattr(tasks, "dispatch", dispatch)
    yield scope, calls, storage, dispatch
    engine.dispose()


def start():
    return offline_jobs.OfflinePatrolJobService().start_execution(
        job_id="job",
        owner_id="owner",
        execution_idempotency_key="request",
    )


def deliver(task_id):
    tasks.execute_offline.push_request(id=task_id)
    try:
        return tasks.execute_offline.run("job", "request")
    finally:
        tasks.execute_offline.pop_request()


@pytest.mark.parametrize("job_type", ["history_compare", "single_patrol"])
def test_fixed_execution_runs_complete_business_flow_and_persists_report(runtime, job_type):
    scope, calls, storage, dispatch = runtime
    if job_type == "single_patrol":
        with scope() as db:
            db.get(Job, "job").job_type = job_type
            db.delete(db.get(Period, "baseline"))
    result = start()
    assert result["workflow_run_id"] is None
    assert result["execution"] == "offline_patrol"
    dispatch.assert_called_once_with("job", "request", result["task_id"])
    calls.clear()
    assert deliver(result["task_id"])["success"]
    expected = ["gate"]
    if job_type == "history_compare":
        expected += ["import:baseline", "normalize"]
    expected += ["import:current", "normalize", "changes", "events", "report"]
    assert [c[0] for c in calls] == expected
    normalized = [c[1][-1] for c in calls if c[0] == "normalize"]
    assert all(c["update_current_state"] is False for c in normalized)
    change = next(c[1][-1] for c in calls if c[0] == "changes")
    assert change.get("previous_run_id") == ("baseline" if job_type == "history_compare" else None)
    assert change.get("allow_partial_normalization", False) == (job_type == "history_compare")
    with scope() as db:
        job = db.get(Job, "job")
        assert job.status == "completed" and job.workflow_run_id is None
        assert job.result_json["observation_count"] == 3
        assert job.result_json["event_count"] == 1
        assert job.artifacts_json["report"]["download_url"] == "/api/apps/inspection/patrol/offline-jobs/job/artifacts/saved-report"
        assert job.celery_task_id == result["task_id"]
    storage.upload_file.assert_called_once()
    assert deliver(result["task_id"])["ignored"]
    assert start()["idempotent"]
    assert dispatch.call_count == 1


def test_broker_failure_leaves_durable_queued_job_for_recovery(runtime):
    scope, _, _, dispatch = runtime
    dispatch.return_value = False
    result = start()
    assert result["dispatch_status"] == "pending"
    with scope() as db:
        assert db.get(Job, "job").status == "queued"
    assert tasks.recover_offline.run()["queued"] == 1
    assert dispatch.call_args.args == ("job", "request", result["task_id"])


def test_cancel_during_step_stops_later_steps_and_preserves_cancelled_state(runtime, monkeypatch):
    scope, calls, storage, _ = runtime
    result = start()

    def cancel_on_normalization(self, params):
        offline_jobs.OfflinePatrolJobService().cancel("job")
        return {"success": True}

    monkeypatch.setattr(tasks.NormalizePatrolHandler, "handle", cancel_on_normalization)
    assert deliver(result["task_id"])["reason"] == "execution_stopped"
    assert not any(c[0] in {"changes", "events", "report", "import:current"} for c in calls)
    storage.upload_file.assert_not_called()
    with scope() as db:
        assert db.get(Job, "job").status == "cancelled"
        assert "baseline_ingest" in db.get(Job, "job").result_json["steps"]


def test_report_upload_failure_retains_analysis_and_failed_phase(runtime):
    scope, _, storage, _ = runtime
    storage.upload_file.return_value = False
    result = start()
    assert not deliver(result["task_id"])["success"]
    with scope() as db:
        job = db.get(Job, "job")
        assert job.status == "failed"
        assert job.progress_json["failed_phase"] == "report_storage"
        assert job.result_json["steps"]["event_building"]["success"]
        assert job.result_json["event_count"] == 1


def test_failed_phase_prevents_events_and_report(runtime, monkeypatch):
    scope, calls, storage, _ = runtime
    result = start()
    monkeypatch.setattr(
        tasks.DetectChangesHandler, "handle", lambda *a: {"success": False, "error": "bad contract"}
    )
    assert not deliver(result["task_id"])["success"]
    assert not any(c[0] in {"events", "report"} for c in calls)
    storage.upload_file.assert_not_called()
    with scope() as db:
        job = db.get(Job, "job")
        assert "bad contract" in job.error_message
        assert job.progress_json["failed_phase"] == "change_detection"


def test_stale_delivery_cannot_claim_new_attempt_or_rule_source(runtime):
    scope, calls, _, _ = runtime
    result = start()
    calls.clear()
    assert deliver("old-task")["ignored"]
    with scope() as db:
        db.get(Job, "job").purpose = "rule_asset_source"
    assert deliver(result["task_id"])["ignored"]
    assert not calls


def test_interruption_retains_evidence_and_never_replays_running_job(runtime):
    scope, _, _, dispatch = runtime
    result = start()
    with scope() as db:
        job = db.get(Job, "job")
        job.status = "running"
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=36)
        job.result_json = {"steps": {"baseline_ingest": {"success": True, "run_id": "evidence"}}}
    dispatch.reset_mock()
    assert tasks.recover_offline.run()["interrupted"] == ["job"]
    dispatch.assert_not_called()
    assert deliver(result["task_id"])["ignored"]
    with scope() as db:
        job = db.get(Job, "job")
        assert job.status == "failed"
        assert job.result_json["steps"]["baseline_ingest"]["run_id"] == "evidence"


def test_active_idempotent_submission_and_ownership(runtime, monkeypatch):
    scope, _, _, _ = runtime
    result = start()
    with scope() as db:
        db.get(Job, "job").status = "running"
    monkeypatch.setattr(
        offline_job_lock,
        "job_execution_lock",
        Mock(side_effect=AssertionError("should not acquire")),
    )
    assert start()["task_id"] == result["task_id"]
    with pytest.raises(PermissionError):
        offline_jobs.OfflinePatrolJobService().start_execution(
            job_id="job",
            owner_id="another",
            execution_idempotency_key="request",
        )


def test_postgres_lock_blocks_restart_until_worker_exits():
    # Actual independent PostgreSQL connections; no business rows are changed.
    from concurrent.futures import ThreadPoolExecutor

    job_id = f"test-offline-lock-{uuid.uuid4()}"

    def attempt():
        with offline_job_lock.job_execution_lock(job_id):
            return True

    with ThreadPoolExecutor(max_workers=1) as pool:
        with offline_job_lock.job_execution_lock(job_id):
            with pytest.raises(offline_job_lock.OfflineJobBusyError):
                pool.submit(attempt).result(timeout=10)
        assert pool.submit(attempt).result(timeout=10)


def test_raw_import_and_stale_preview_cannot_bypass_fixed_delivery(runtime):
    start()
    service = offline_jobs.OfflinePatrolJobService()
    with pytest.raises(ValueError, match="固定任务"):
        real_import_period(service, "job", "current")
    assert service.run_preview("job", patrol_task_id="old-preview")["ignored"]


def test_cancellation_during_real_import_cannot_overwrite_job_or_period(runtime, monkeypatch):
    scope, _, _, _ = runtime
    result = start()
    service = offline_jobs.OfflinePatrolJobService()
    with scope() as db:
        from src.infrastructure.db.models import OfflinePatrolDeviceBindingRecord

        OfflinePatrolDeviceBindingRecord.__table__.create(db.get_bind())
        db.get(Job, "job").status = "running"

    @contextmanager
    def materialize(*args):
        yield "unused-test-source"

    def import_and_cancel(**kwargs):
        service.cancel("job")
        return {"success": True, "run_id": kwargs["run_id"]}

    monkeypatch.setattr(service, "materialize_period", materialize)
    monkeypatch.setattr(offline_jobs, "import_raw_path", import_and_cancel)
    with pytest.raises(ValueError, match="取消"):
        real_import_period(
            service,
            "job",
            "current",
            patrol_execution_key="request",
            patrol_task_id=result["task_id"],
        )
    with scope() as db:
        assert db.get(Job, "job").status == "cancelled"
        assert db.get(Period, "current").result_json is None


@pytest.mark.parametrize("name", ["patrol-offline-current", "patrol-history-change-event", "patrol-change-event", "patrol-attested-offline-current"])
def test_retired_offline_workflow_cannot_start(name):
    from src.core.workflows.engine import WorkflowEngine

    with pytest.raises(ValueError, match="固定任务"):
        WorkflowEngine.start(name, {})
