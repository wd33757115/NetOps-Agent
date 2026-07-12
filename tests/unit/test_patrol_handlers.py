from pathlib import Path

from src.core.patrol.handlers import (
    BuildEventsHandler,
    DetectChangesHandler,
    ExecutePatrolCommand,
    ExecutePatrolHandler,
    GenerateReportHandler,
    NormalizePatrolHandler,
    StartPatrolCommand,
    StartPatrolHandler,
)


class _Plans:
    def __init__(self):
        self.created = []
        self.launched = []
        self.failed = []

    def create_plan(self, plan, **kwargs):
        self.created.append((plan, kwargs))
        return {"execution_plan_id": f"plan-{len(self.created)}"}

    def mark_launched(self, execution_plan_id, **kwargs):
        self.launched.append((execution_plan_id, kwargs))

    def mark_failed(self, execution_plan_id, **kwargs):
        self.failed.append((execution_plan_id, kwargs))


def _command(launch_mode="workflow"):
    return StartPatrolCommand(
        rows=[{
            "device_id": "device-1",
            "device_name": "core-1",
            "ip": "192.0.2.1",
            "command_count": 2,
            "plan": {"commands": ["display interface brief"]},
        }],
        target_count=1,
        skipped_errors=[],
        launch_mode=launch_mode,
        workflow_name="patrol-change-event",
    )


def test_start_patrol_handler_dispatches_workflow_with_persisted_plan():
    plans = _Plans()
    contexts = []
    handler = StartPatrolHandler(
        plan_repository=plans,
        workflow_starter=lambda name, context, **kwargs: contexts.append(
            (name, context, kwargs)
        ) or "run-1",
        batch_id_factory=lambda: "batch-1",
    )

    result = handler.handle(_command())

    assert result["runs"][0]["workflow_run_id"] == "run-1"
    assert contexts[0][1]["execution_plan_id"] == "plan-1"
    assert contexts[0][1]["update_current_state"] is True
    assert plans.launched[0][1]["workflow_run_id"] == "run-1"


def test_start_patrol_handler_dispatches_direct_task_through_same_plan_lifecycle():
    plans = _Plans()
    task_payloads = []
    handler = StartPatrolHandler(
        plan_repository=plans,
        task_launcher=lambda **kwargs: task_payloads.append(kwargs) or "task-1",
        batch_id_factory=lambda: "batch-1",
    )

    result = handler.handle(_command("direct"))

    assert result["tasks"][0]["celery_task_id"] == "task-1"
    assert task_payloads[0]["execution_plan_id"] == "plan-1"
    assert plans.launched[0][1]["celery_task_id"] == "task-1"


def test_start_patrol_handler_marks_plan_failed_when_dispatch_fails():
    plans = _Plans()

    def fail(*_args, **_kwargs):
        raise ValueError("workflow missing")

    handler = StartPatrolHandler(
        plan_repository=plans,
        workflow_starter=fail,
        batch_id_factory=lambda: "batch-1",
    )

    try:
        handler.handle(_command())
    except ValueError:
        pass
    else:
        raise AssertionError("expected dispatch failure")

    assert plans.failed == [
        (
            "plan-1",
            {"failure_code": "workflow_launch_failed", "error": "workflow missing"},
        )
    ]


def test_deterministic_patrol_handlers_copy_adapter_input_and_delegate_once():
    original = {"run_id": "run-1"}
    calls = []

    def execute(params):
        calls.append(params)
        params["mutated"] = True
        return {"success": True}

    for handler in (
        NormalizePatrolHandler(execute),
        DetectChangesHandler(execute),
        BuildEventsHandler(execute),
    ):
        assert handler.handle(original) == {"success": True}

    assert len(calls) == 3
    assert original == {"run_id": "run-1"}


def test_execute_patrol_handler_builds_runner_request_and_marks_completion():
    requests = []
    completions = []

    class Runner:
        def run(self, request):
            requests.append(request)
            return {"success": True, "snapshot_run_id": "patrol-task-1"}

    class Coordinator:
        def complete(self, run_id, **kwargs):
            completions.append((run_id, kwargs))

    result = ExecutePatrolHandler(
        runner=Runner(), coordinator=Coordinator()
    ).handle(
        ExecutePatrolCommand(
            task_id="task-1",
            execution_plan_id="plan-1",
            owner_token="owner-1",
        )
    )

    assert result["snapshot_run_id"] == "patrol-task-1"
    assert requests[0].execution_plan_id == "plan-1"
    assert completions == [("patrol-task-1", {"owner_token": "owner-1"})]


def test_generate_report_handler_materializes_summary_once_before_rendering():
    calls = []

    def summarize(batch_id, runs):
        calls.append(("summary", batch_id, runs))
        return {"run_count": len(runs)}

    def render(batch_id, summary, **kwargs):
        calls.append(("render", batch_id, summary, kwargs))
        return "report"

    result = GenerateReportHandler(
        summary_builder=summarize,
        renderer=render,
    ).handle("batch-1", ["run-1"], generated_at="now")

    assert result == "report"
    assert calls == [
        ("summary", "batch-1", ["run-1"]),
        ("render", "batch-1", {"run_count": 1}, {"generated_at": "now"}),
    ]


def test_patrol_adapters_do_not_bypass_application_handlers():
    adapters = [
        Path("src/gateway/devices_api.py"),
        Path("src/core/celery_tasks/tasks.py"),
        Path("src/skills/state-normalizer/scripts/run.py"),
        Path("src/skills/change-detector/scripts/run.py"),
        Path("src/skills/event-builder/scripts/run.py"),
        Path("src/core/parser/backfill.py"),
        Path("src/core/patrol/reporting.py"),
    ]
    forbidden = (
        "DevicePatrolRunner().run(",
        "normalize_state_from_params(params)",
        "detect_changes_from_params(",
        "build_events_from_params(",
    )

    violations = []
    for path in adapters:
        source = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            if pattern in source:
                violations.append(f"{path}: {pattern}")

    celery_source = Path("src/core/celery_tasks/tasks.py").read_text(encoding="utf-8")
    if "from src.core.patrol.repository import PatrolRepository" in celery_source:
        violations.append("Celery patrol adapter imports PatrolRepository")

    assert violations == []
