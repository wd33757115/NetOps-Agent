from pathlib import Path
from types import SimpleNamespace

from src.core.skills import celery_routing
from src.core.workflows.engine import WorkflowEngine


def test_builtin_patrol_and_ai_skills_have_immutable_role_queues(monkeypatch):
    monkeypatch.setattr(
        celery_routing,
        "get_catalog_entry",
        lambda _name: {"celery_queue": "netops.default", "domain": "general"},
    )

    assert celery_routing.resolve_skill_celery_queue("device-patrol") == "netops.patrol"
    assert celery_routing.resolve_skill_celery_queue("state-normalizer") == "netops.patrol"
    assert celery_routing.resolve_skill_celery_queue("change-detector") == "netops.patrol"
    assert celery_routing.resolve_skill_celery_queue("event-builder") == "netops.patrol"
    assert celery_routing.resolve_skill_celery_queue("textfsm-generator") == "netops.ai"
    assert celery_routing.resolve_skill_celery_queue("event-rule-generator") == "netops.ai"


def test_custom_skill_still_uses_catalog_queue(monkeypatch):
    monkeypatch.setattr(
        celery_routing,
        "get_catalog_entry",
        lambda _name: {"celery_queue": "tenant.custom", "domain": "general"},
    )

    assert celery_routing.resolve_skill_celery_queue("tenant-skill") == "tenant.custom"


def test_workflow_dispatch_routes_patrol_step_to_patrol_worker(monkeypatch):
    from src.core.workflows import engine, tasks

    run = SimpleNamespace(id="run-1", status="pending")
    step = SimpleNamespace(
        id="step-1",
        step_name="patrol",
        skill_name="device-patrol",
    )
    dispatched = []
    run_updates = []
    queued_steps = []
    events = []

    monkeypatch.setattr(engine, "get_workflow_run", lambda _run_id: run)
    monkeypatch.setattr(engine, "list_workflow_steps", lambda _run_id: [step])
    monkeypatch.setattr(
        engine,
        "update_run_step_index",
        lambda *args, **kwargs: run_updates.append((args, kwargs)),
    )
    monkeypatch.setattr(
        engine,
        "publish_workflow_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )
    monkeypatch.setattr(
        engine,
        "mark_step_queued",
        lambda *args: queued_steps.append(args),
    )
    monkeypatch.setattr(
        tasks.dispatch_workflow_step_task,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs) or SimpleNamespace(id="task-1"),
    )

    WorkflowEngine.dispatch_step("run-1", 0)

    assert dispatched[0]["args"] == ["run-1", 0]
    assert dispatched[0]["queue"] == "netops.patrol"
    assert dispatched[0]["task_id"]
    assert run_updates == [(('run-1', 0), {"status": "queued"})]
    assert queued_steps == [("step-1", "task-1")]
    assert events[0][1]["status"] == "queued"


def test_local_worker_scripts_consume_role_queues():
    root = Path(__file__).resolve().parents[2]
    required = {"netops.patrol", "netops.ai", "netops.health"}

    for relative_path in ["scripts/test/start.ps1", "scripts/test/celery_worker.ps1"]:
        content = (root / relative_path).read_text(encoding="utf-8")
        assert all(queue in content for queue in required), relative_path


def test_production_start_enables_core_profile_and_verifies_beat():
    root = Path(__file__).resolve().parents[2]
    powershell_start = (root / "scripts/prod/start.ps1").read_text(encoding="utf-8")
    shell_start = (root / "scripts/prod/start.sh").read_text(encoding="utf-8")
    powershell_smoke = (root / "scripts/prod/smoke_test.ps1").read_text(encoding="utf-8")
    shell_smoke = (root / "scripts/prod/smoke_test.sh").read_text(encoding="utf-8")

    assert powershell_start.isascii()
    assert powershell_smoke.isascii()
    assert '"--profile", "core", "up", "-d"' in powershell_start
    assert "--profile core up -d" in shell_start
    assert "-SkipDockerCheck" not in powershell_start
    assert "SKIP_DOCKER_CHECK=1" not in shell_start
    assert "netops-celery-beat" in powershell_smoke
    assert "netops-celery-beat" in shell_smoke
    assert 'id -eq "celery_beat"' in powershell_smoke
    assert "c.get('id') == 'celery_beat'" in shell_smoke


def test_beat_schedule_publishes_expiring_heartbeat_to_health_queue():
    from src.core.celery_tasks.celery_app import celery

    entry = celery.conf.beat_schedule["celery-beat-heartbeat"]

    assert entry["task"] == "record_celery_beat_heartbeat"
    assert entry["options"]["queue"] == "netops.health"
    assert entry["options"]["expires"] >= entry["schedule"]


def test_terminal_workflow_ignores_late_celery_delivery(monkeypatch):
    from src.core.workflows import tasks

    monkeypatch.setattr(
        tasks,
        "get_workflow_run",
        lambda _run_id: SimpleNamespace(status="failed"),
    )
    monkeypatch.setattr(
        tasks,
        "list_workflow_steps",
        lambda _run_id: (_ for _ in ()).throw(AssertionError("steps must not be loaded")),
    )

    result = tasks.dispatch_workflow_step_task.run("run-terminal", 0)

    assert result["ignored"] is True
    assert result["error"] == "workflow already failed"


def test_report_reconciler_restores_retryable_state_when_enqueue_fails(monkeypatch):
    from src.core.celery_tasks import tasks
    from src.core.patrol import batch_manifest

    status_updates = []

    class FakeRepository:
        def terminal_report_candidate_ids(self, *, limit):
            assert limit == 100
            return ["batch-report"]

        def request_terminal_report(self, patrol_batch_id):
            assert patrol_batch_id == "batch-report"
            return True

        def mark_report_status(self, patrol_batch_id, status, *, error=None):
            status_updates.append((patrol_batch_id, status, error))

    monkeypatch.setattr(batch_manifest, "PatrolBatchManifestRepository", FakeRepository)
    monkeypatch.setattr(
        tasks.materialize_patrol_batch_report_task,
        "apply_async",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )

    result = tasks.reconcile_patrol_batch_reports_task.run()

    assert result == {
        "success": False,
        "candidate_count": 1,
        "requested_count": 0,
        "failed_count": 1,
    }
    assert status_updates == [
        ("batch-report", "not_requested", "broker unavailable"),
    ]
