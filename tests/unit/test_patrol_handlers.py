from pathlib import Path
from types import SimpleNamespace

from src.core.patrol.handlers import (
    BuildEventsHandler,
    DetectChangesHandler,
    DispatchPatrolBatchHandler,
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

    def get_plan(self, plan_id):
        if plan_id != "plan-1":
            return None
        plan = self.created[0][0] if self.created else {"commands": ["show version"]}
        return {"execution_plan_id": plan_id, "plan_json": plan}


class _Batches:
    def __init__(self):
        self.created = []
        self.enqueued_errors = []
        self.claims = []
        self.dispatched = []
        self.failed = []
        self.final_status = "completed"

    def create_pending_batch(self, **kwargs):
        self.created.append(kwargs)

    def note_dispatch_enqueue_error(self, patrol_batch_id, error):
        self.enqueued_errors.append((patrol_batch_id, error))

    def claim_dispatch_targets(self, patrol_batch_id, **_kwargs):
        if not self.claims:
            return []
        return self.claims.pop(0)

    def mark_target_dispatched(self, target_id, **kwargs):
        self.dispatched.append((target_id, kwargs))
        return True

    def mark_target_dispatch_failed(self, target_id, **kwargs):
        self.failed.append((target_id, kwargs))
        return True

    def finalize_dispatch(self, _patrol_batch_id, **_kwargs):
        return self.final_status


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


def test_start_patrol_handler_persists_batch_before_enqueuing_dispatch():
    plans = _Plans()
    batches = _Batches()
    enqueued = []
    handler = StartPatrolHandler(
        plan_repository=plans,
        batch_repository=batches,
        batch_dispatcher=lambda batch_id: enqueued.append(batch_id) or "dispatch-1",
        batch_id_factory=lambda: "batch-1",
    )

    result = handler.handle(_command())

    assert result["dispatch_status"] == "queued"
    assert result["queued_count"] == 1
    assert result["launched_count"] == 0
    assert batches.created[0]["planned_rows"][0]["execution_plan_id"] == "plan-1"
    assert enqueued == ["batch-1"]


def test_start_patrol_handler_freezes_check_item_plan_before_dispatch():
    plans = _Plans()
    batches = _Batches()
    enqueued = []
    effective_calls = []
    preview = SimpleNamespace(
        manifest_id="runtime-manifest-1",
        manifest_digest="a" * 64,
        collection_actions=(
            {
                "id": "action-1",
                "device_id": "device-1",
                "collector": "cli",
                "command": "display cpu-usage",
                "fallback_rank": 0,
                "bindings": [
                    {
                        "check_item_version_id": "check-cpu-v1",
                        "implementation_version_id": "impl-cpu-v1",
                        "runtime_bundle_version_id": "bundle-cpu-v1",
                        "fallback_rank": 0,
                    }
                ],
            },
        ),
        executions=(
            {
                "id": "execution-1",
                "device_id": "device-1",
                "execution_status": "planned",
            },
        ),
    )

    class EffectivePlans:
        def preview(self, request):
            effective_calls.append(("preview", request))
            return preview

        def freeze(self, request):
            effective_calls.append(("freeze", request))
            return preview

    handler = StartPatrolHandler(
        plan_repository=plans,
        batch_repository=batches,
        effective_plan_service=EffectivePlans(),
        batch_dispatcher=lambda batch_id: enqueued.append(batch_id) or "dispatch-1",
        batch_id_factory=lambda: "batch-check-items",
    )
    command = _command()
    command = StartPatrolCommand(
        **{
            **command.__dict__,
            "target_devices": [
                {
                    "device_id": "device-1",
                    "device_name": "core-1",
                    "management_ip": "192.0.2.1",
                    "vendor": "H3C",
                    "family": "Comware7",
                    "model": "S5130",
                }
            ],
            "check_item_ids_by_device": {
                "device-1": ["system.cpu_usage"]
            },
            "profile_version_ids_by_device": {
                "device-1": "profile-v1"
            },
        }
    )

    result = handler.handle(command)

    assert [item[0] for item in effective_calls] == [
        "preview",
        "freeze",
    ]
    persisted_plan = plans.created[0][0]
    assert persisted_plan["runtime_manifest_id"] == "runtime-manifest-1"
    assert persisted_plan["collection_items"][0][
        "collection_item_id"
    ] == "action-1"
    assert batches.created[0]["planned_rows"][0][
        "execution_plan_id"
    ] == "plan-1"
    assert enqueued == ["batch-check-items"]
    assert result["check_item_mode"] is True


def test_dispatch_patrol_batch_starts_idempotent_workflow_and_marks_target():
    plans = _Plans()
    batches = _Batches()
    batches.claims = [[{
        "target_id": "target-1",
        "claim_token": "claim-1",
        "patrol_batch_id": "batch-1",
        "device_id": "device-1",
        "device_name": "core-1",
        "execution_plan_id": "plan-1",
        "workflow_name": "patrol-change-event",
        "launch_mode": "workflow",
        "mode": "production",
        "save_baseline": False,
        "ticket_id": None,
        "target_snapshot": {},
    }]]
    contexts = []
    handler = DispatchPatrolBatchHandler(
        plan_repository=plans,
        batch_repository=batches,
        workflow_starter=lambda name, context, **kwargs: contexts.append(
            (name, context, kwargs)
        ) or kwargs["run_id"],
    )

    result = handler.handle("batch-1")

    assert result["dispatched_count"] == 1
    assert contexts[0][1]["execution_plan_id"] == "plan-1"
    assert contexts[0][1]["update_current_state"] is True
    assert contexts[0][2]["run_id"]
    assert batches.dispatched[0][1]["workflow_run_id"] == contexts[0][2]["run_id"]


def test_dispatch_patrol_batch_marks_plan_and_target_failed_when_launch_fails():
    plans = _Plans()
    batches = _Batches()
    batches.claims = [[{
        "target_id": "target-1",
        "claim_token": "claim-1",
        "patrol_batch_id": "batch-1",
        "device_id": "device-1",
        "device_name": "core-1",
        "execution_plan_id": "plan-1",
        "workflow_name": "patrol-change-event",
        "launch_mode": "workflow",
        "mode": "production",
        "save_baseline": False,
        "ticket_id": None,
        "target_snapshot": {},
    }]]

    def fail(*_args, **_kwargs):
        raise ValueError("workflow missing")

    handler = DispatchPatrolBatchHandler(
        plan_repository=plans,
        batch_repository=batches,
        workflow_starter=fail,
    )

    result = handler.handle("batch-1")

    assert result["failed_count"] == 1
    assert plans.failed == [
        (
            "plan-1",
            {"failure_code": "workflow_launch_failed", "error": "workflow missing"},
        )
    ]
    assert batches.failed[0][0] == "target-1"


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
