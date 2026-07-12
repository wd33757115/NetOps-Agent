# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Application handlers for patrol use cases shared by transport adapters."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from src.core.collection import ExecutionPlanRepository
from src.core.patrol.modes import PRODUCTION_MODE, normalize_patrol_mode
from src.core.patrol.run_coordinator import PatrolRunCoordinator
from src.core.patrol.runner import DevicePatrolRunner, DevicePatrolRunRequest


class PlanRepository(Protocol):
    def create_plan(self, plan: dict[str, Any], **kwargs: Any) -> dict[str, Any]: ...
    def mark_failed(self, execution_plan_id: str, **kwargs: Any) -> Any: ...
    def mark_launched(self, execution_plan_id: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class StartPatrolCommand:
    rows: list[dict[str, Any]]
    target_count: int
    skipped_errors: list[dict[str, Any]]
    launch_mode: str
    workflow_name: str
    mode: str = PRODUCTION_MODE
    save_baseline: bool = False
    ticket_id: str | None = None
    target_snapshot: dict[str, Any] | None = None


class StartPatrolHandler:
    """Persist execution plans and dispatch patrol work through one use case."""

    def __init__(
        self,
        *,
        plan_repository: PlanRepository | None = None,
        workflow_starter: Callable[..., str] | None = None,
        task_launcher: Callable[..., str] | None = None,
        batch_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._plans = plan_repository or ExecutionPlanRepository()
        self._workflow_starter = workflow_starter or self._start_workflow
        self._task_launcher = task_launcher or self._launch_task
        self._batch_id_factory = batch_id_factory or (lambda: f"patrol-batch-{uuid.uuid4()}")

    def handle(self, command: StartPatrolCommand) -> dict[str, Any]:
        if command.launch_mode not in {"workflow", "direct"}:
            raise ValueError("launch_mode must be workflow or direct")
        patrol_batch_id = self._batch_id_factory()
        launched = [self._launch_row(command, row, patrol_batch_id) for row in command.rows]
        return {
            "success": True,
            "target_count": command.target_count,
            "launched_count": len(launched),
            "mode": command.mode,
            "save_baseline": command.save_baseline,
            "ticket_id": command.ticket_id,
            "patrol_batch_id": patrol_batch_id,
            "launch_mode": command.launch_mode,
            "workflow_name": command.workflow_name,
            "runs": launched if command.launch_mode == "workflow" else [],
            "tasks": launched if command.launch_mode == "direct" else [],
            "skipped_count": len(command.skipped_errors),
            "skipped_errors": command.skipped_errors,
        }

    def _launch_row(
        self,
        command: StartPatrolCommand,
        row: dict[str, Any],
        patrol_batch_id: str,
    ) -> dict[str, Any]:
        plan_record = self._plans.create_plan(
            row["plan"],
            launch_source=command.launch_mode,
            patrol_batch_id=patrol_batch_id,
        )
        plan_id = str(plan_record["execution_plan_id"])
        try:
            if command.launch_mode == "workflow":
                dispatch_id = self._workflow_starter(
                    command.workflow_name,
                    self._workflow_context(command, row, plan_id, patrol_batch_id),
                    source="device-management",
                )
                launch_fields = {"workflow_run_id": dispatch_id}
                result_field = "workflow_run_id"
            else:
                dispatch_id = self._task_launcher(
                    execution_plan=row["plan"],
                    execution_plan_id=plan_id,
                    ticket_id=command.ticket_id,
                    save_baseline=command.save_baseline,
                    mode=command.mode,
                    patrol_batch_id=patrol_batch_id,
                )
                launch_fields = {"celery_task_id": dispatch_id}
                result_field = "celery_task_id"
        except Exception as exc:
            self._plans.mark_failed(
                plan_id,
                failure_code=(
                    "workflow_launch_failed"
                    if command.launch_mode == "workflow"
                    else "celery_launch_failed"
                ),
                error=str(exc),
            )
            raise
        self._plans.mark_launched(
            plan_id,
            launch_source=command.launch_mode,
            patrol_batch_id=patrol_batch_id,
            **launch_fields,
        )
        result = {
            "device_id": row["device_id"],
            "device_name": row["device_name"],
            "ip": row["ip"],
            "execution_plan_id": plan_id,
            result_field: dispatch_id,
            "patrol_batch_id": patrol_batch_id,
            "command_count": row["command_count"],
        }
        if command.launch_mode == "workflow":
            result["workflow_name"] = command.workflow_name
        return result

    @staticmethod
    def _workflow_context(
        command: StartPatrolCommand,
        row: dict[str, Any],
        plan_id: str,
        patrol_batch_id: str,
    ) -> dict[str, Any]:
        return {
            "execution_plan": row["plan"],
            "execution_plan_id": plan_id,
            "filter_params": {},
            "ticket_id": command.ticket_id,
            "save_baseline": command.save_baseline,
            "mode": command.mode,
            "update_current_state": normalize_patrol_mode(command.mode) == PRODUCTION_MODE,
            "publish_events": False,
            "patrol_batch_id": patrol_batch_id,
            "patrol_target": dict(command.target_snapshot or {}),
            "device_id": row["device_id"],
            "device_name": row["device_name"],
        }

    @staticmethod
    def _start_workflow(template_name: str, context: dict[str, Any], **kwargs: Any) -> str:
        from src.core.workflows.engine import WorkflowEngine

        return WorkflowEngine.start(template_name, context, **kwargs)

    @staticmethod
    def _launch_task(**kwargs: Any) -> str:
        from src.core.celery_tasks.tasks import execute_device_patrol_task

        task = execute_device_patrol_task.apply_async(
            kwargs=kwargs,
            queue="netops.patrol",
        )
        return str(task.id)


@dataclass(frozen=True)
class ExecutePatrolCommand:
    filter_params: dict[str, Any] | None = None
    ticket_id: str | None = None
    save_baseline: bool = False
    mode: str = PRODUCTION_MODE
    execution_plan: dict[str, Any] | None = None
    execution_plan_id: str | None = None
    task_id: str | None = None
    attempt_number: int = 0
    worker_id: str | None = None
    owner_token: str | None = None


class ExecutePatrolHandler:
    def __init__(self, *, runner: Any | None = None, coordinator: Any | None = None):
        self._runner = runner or DevicePatrolRunner()
        self._coordinator = coordinator or PatrolRunCoordinator()

    def handle(self, command: ExecutePatrolCommand) -> dict[str, Any]:
        result = self._runner.run(
            DevicePatrolRunRequest(
                filter_params=command.filter_params,
                ticket_id=command.ticket_id,
                save_baseline=command.save_baseline,
                mode=command.mode,
                execution_plan=command.execution_plan,
                execution_plan_id=command.execution_plan_id,
                task_id=command.task_id,
                attempt_number=command.attempt_number,
                worker_id=command.worker_id,
                owner_token=command.owner_token,
            )
        )
        snapshot_run_id = result.get("snapshot_run_id")
        if snapshot_run_id:
            self._coordinator.complete(
                str(snapshot_run_id),
                owner_token=command.owner_token,
            )
        return result


class NormalizePatrolHandler:
    def __init__(self, executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        if executor is None:
            from src.core.state import normalize_state_from_params

            executor = normalize_state_from_params
        self._executor = executor

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._executor(dict(params))


class DetectChangesHandler:
    def __init__(self, executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        if executor is None:
            from src.core.patrol.change_detector import detect_changes_from_params

            executor = detect_changes_from_params
        self._executor = executor

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._executor(dict(params))


class BuildEventsHandler:
    def __init__(self, executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        if executor is None:
            from src.core.patrol.event_builder import build_events_from_params

            executor = build_events_from_params
        self._executor = executor

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._executor(dict(params))


class GenerateReportHandler:
    def __init__(
        self,
        *,
        summary_builder: Callable[[str, list[Any]], dict[str, Any]] | None = None,
        renderer: Callable[..., str] | None = None,
    ) -> None:
        if summary_builder is None:
            from src.core.workflows.patrol_batch_service import (
                build_patrol_batch_summary,
            )

            summary_builder = build_patrol_batch_summary
        if renderer is None:
            from src.core.patrol.patrol_report_service import PatrolReportService

            renderer = PatrolReportService.render_markdown
        self._summary_builder = summary_builder
        self._renderer = renderer

    def handle(
        self,
        patrol_batch_id: str,
        runs: list[Any],
        *,
        generated_at: Any | None = None,
    ) -> str:
        summary = self._summary_builder(patrol_batch_id, runs)
        return self._renderer(
            patrol_batch_id,
            summary,
            generated_at=generated_at,
        )
