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
    def get_plan(self, plan_id: str) -> dict[str, Any] | None: ...


class BatchManifestRepository(Protocol):
    def create_pending_batch(self, **kwargs: Any) -> None: ...
    def note_dispatch_enqueue_error(self, patrol_batch_id: str, error: str) -> None: ...
    def claim_dispatch_targets(
        self, patrol_batch_id: str, **kwargs: Any
    ) -> list[dict[str, Any]]: ...
    def mark_target_dispatched(self, target_id: str, **kwargs: Any) -> bool: ...
    def mark_target_dispatch_failed(self, target_id: str, **kwargs: Any) -> bool: ...
    def finalize_dispatch(self, patrol_batch_id: str, **kwargs: Any) -> str: ...


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
    """Persist the complete batch intent, then enqueue durable dispatch."""

    def __init__(
        self,
        *,
        plan_repository: PlanRepository | None = None,
        batch_repository: BatchManifestRepository | None = None,
        batch_dispatcher: Callable[[str], str] | None = None,
        batch_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._plans = plan_repository or ExecutionPlanRepository()
        if batch_repository is None:
            from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

            batch_repository = PatrolBatchManifestRepository()
        self._batches = batch_repository
        self._batch_dispatcher = batch_dispatcher or self._enqueue_batch_dispatch
        self._batch_id_factory = batch_id_factory or (lambda: f"patrol-batch-{uuid.uuid4()}")

    def handle(self, command: StartPatrolCommand) -> dict[str, Any]:
        if command.launch_mode not in {"workflow", "direct"}:
            raise ValueError("launch_mode must be workflow or direct")
        patrol_batch_id = self._batch_id_factory()
        planned_rows: list[dict[str, Any]] = []
        try:
            for row in command.rows:
                plan_record = self._plans.create_plan(
                    row["plan"],
                    launch_source=command.launch_mode,
                    patrol_batch_id=patrol_batch_id,
                )
                planned_rows.append(
                    {**row, "execution_plan_id": str(plan_record["execution_plan_id"])}
                )
            self._batches.create_pending_batch(
                patrol_batch_id=patrol_batch_id,
                workflow_name=command.workflow_name,
                launch_mode=command.launch_mode,
                patrol_mode=command.mode,
                save_baseline=command.save_baseline,
                ticket_id=command.ticket_id,
                target_count=command.target_count,
                planned_rows=planned_rows,
                skipped_errors=command.skipped_errors,
                target_snapshot=dict(command.target_snapshot or {}),
            )
        except Exception as exc:
            for row in planned_rows:
                self._plans.mark_failed(
                    str(row["execution_plan_id"]),
                    failure_code="batch_manifest_persist_failed",
                    error=str(exc),
                )
            raise

        dispatch_task_id = None
        try:
            dispatch_task_id = self._batch_dispatcher(patrol_batch_id)
        except Exception as exc:
            self._batches.note_dispatch_enqueue_error(patrol_batch_id, str(exc))
        return {
            "success": True,
            "target_count": command.target_count,
            "queued_count": len(planned_rows),
            "launched_count": 0,
            "mode": command.mode,
            "save_baseline": command.save_baseline,
            "ticket_id": command.ticket_id,
            "patrol_batch_id": patrol_batch_id,
            "launch_mode": command.launch_mode,
            "workflow_name": command.workflow_name,
            "dispatch_status": "queued" if dispatch_task_id else "pending",
            "dispatch_task_id": dispatch_task_id,
            "runs": [],
            "tasks": [],
            "skipped_count": len(command.skipped_errors),
            "skipped_errors": command.skipped_errors,
        }

    @staticmethod
    def _workflow_context(
        *,
        execution_plan: dict[str, Any],
        execution_plan_id: str,
        patrol_batch_id: str,
        ticket_id: str | None,
        save_baseline: bool,
        mode: str,
        target_snapshot: dict[str, Any],
        device_id: str,
        device_name: str | None,
    ) -> dict[str, Any]:
        return {
            "execution_plan": execution_plan,
            "execution_plan_id": execution_plan_id,
            "filter_params": {},
            "ticket_id": ticket_id,
            "save_baseline": save_baseline,
            "mode": mode,
            "update_current_state": normalize_patrol_mode(mode) == PRODUCTION_MODE,
            "publish_events": False,
            "patrol_batch_id": patrol_batch_id,
            "patrol_target": dict(target_snapshot or {}),
            "device_id": device_id,
            "device_name": device_name,
        }

    @staticmethod
    def _enqueue_batch_dispatch(patrol_batch_id: str) -> str:
        from src.core.celery_tasks.tasks import dispatch_patrol_batch_task

        task = dispatch_patrol_batch_task.apply_async(
            args=[patrol_batch_id],
            queue="netops.patrol",
        )
        return str(task.id)


class DispatchPatrolBatchHandler:
    """Idempotently dispatch the persisted targets of one patrol batch."""

    def __init__(
        self,
        *,
        plan_repository: PlanRepository | None = None,
        batch_repository: BatchManifestRepository | None = None,
        workflow_starter: Callable[..., str] | None = None,
        task_launcher: Callable[..., str] | None = None,
    ) -> None:
        self._plans = plan_repository or ExecutionPlanRepository()
        if batch_repository is None:
            from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

            batch_repository = PatrolBatchManifestRepository()
        self._batches = batch_repository
        self._workflow_starter = workflow_starter or self._start_workflow
        self._task_launcher = task_launcher or self._launch_task

    def handle(self, patrol_batch_id: str, *, chunk_size: int = 100) -> dict[str, Any]:
        dispatched = 0
        failed = 0
        while True:
            targets = self._batches.claim_dispatch_targets(
                patrol_batch_id,
                limit=chunk_size,
            )
            if not targets:
                break
            for target in targets:
                plan_id = str(target.get("execution_plan_id") or "")
                try:
                    plan = self._plans.get_plan(plan_id)
                    if plan is None:
                        raise ValueError(f"execution plan not found: {plan_id}")
                    plan_json = dict(plan.get("plan_json") or {})
                    if target["launch_mode"] == "workflow":
                        workflow_run_id = str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"patrol-workflow|{patrol_batch_id}|{target['device_id']}",
                            )
                        )
                        dispatch_id = self._workflow_starter(
                            str(target["workflow_name"]),
                            StartPatrolHandler._workflow_context(
                                execution_plan=plan_json,
                                execution_plan_id=plan_id,
                                patrol_batch_id=patrol_batch_id,
                                ticket_id=target.get("ticket_id"),
                                save_baseline=bool(target.get("save_baseline")),
                                mode=str(target.get("mode") or PRODUCTION_MODE),
                                target_snapshot=dict(target.get("target_snapshot") or {}),
                                device_id=str(target["device_id"]),
                                device_name=target.get("device_name"),
                            ),
                            source="device-management",
                            run_id=workflow_run_id,
                        )
                        launch_fields = {"workflow_run_id": dispatch_id}
                    else:
                        task_id = str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"patrol-task|{patrol_batch_id}|{target['device_id']}",
                            )
                        )
                        dispatch_id = self._task_launcher(
                            task_id=task_id,
                            execution_plan=plan_json,
                            execution_plan_id=plan_id,
                            ticket_id=target.get("ticket_id"),
                            save_baseline=bool(target.get("save_baseline")),
                            mode=str(target.get("mode") or PRODUCTION_MODE),
                            patrol_batch_id=patrol_batch_id,
                        )
                        launch_fields = {"celery_task_id": dispatch_id}
                    self._plans.mark_launched(
                        plan_id,
                        launch_source=str(target["launch_mode"]),
                        patrol_batch_id=patrol_batch_id,
                        **launch_fields,
                    )
                    self._batches.mark_target_dispatched(
                        str(target["target_id"]),
                        claim_token=str(target["claim_token"]),
                        **launch_fields,
                    )
                    dispatched += 1
                except Exception as exc:
                    failure_code = f"{target['launch_mode']}_launch_failed"
                    if plan_id:
                        try:
                            self._plans.mark_failed(
                                plan_id,
                                failure_code=failure_code,
                                error=str(exc),
                            )
                        except Exception:
                            pass
                    self._batches.mark_target_dispatch_failed(
                        str(target["target_id"]),
                        claim_token=str(target["claim_token"]),
                        failure_code=failure_code,
                        error=str(exc),
                    )
                    try:
                        from src.core.patrol.batch_events import publish_patrol_batch_event

                        publish_patrol_batch_event(
                            {
                                "type": "dispatch_failed",
                                "patrol_batch_id": patrol_batch_id,
                                "device_id": target.get("device_id"),
                                "failure_code": failure_code,
                                "error": str(exc),
                            }
                        )
                    except Exception:
                        pass
                    failed += 1
        status = self._batches.finalize_dispatch(patrol_batch_id)
        try:
            from src.core.patrol.batch_events import publish_patrol_batch_event

            publish_patrol_batch_event(
                {
                    "type": "dispatch_finalized",
                    "patrol_batch_id": patrol_batch_id,
                    "dispatch_status": status,
                    "dispatched_count": dispatched,
                    "failed_count": failed,
                }
            )
        except Exception:
            pass
        return {
            "success": status in {"completed", "pending"},
            "patrol_batch_id": patrol_batch_id,
            "dispatch_status": status,
            "dispatched_count": dispatched,
            "failed_count": failed,
        }

    @staticmethod
    def _start_workflow(template_name: str, context: dict[str, Any], **kwargs: Any) -> str:
        from src.core.workflows.engine import WorkflowEngine

        return WorkflowEngine.start(template_name, context, **kwargs)

    @staticmethod
    def _launch_task(**kwargs: Any) -> str:
        from src.core.celery_tasks.tasks import execute_device_patrol_task

        task_id = str(kwargs.pop("task_id"))
        task = execute_device_patrol_task.apply_async(
            kwargs=kwargs,
            queue="netops.patrol",
            task_id=task_id,
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
        try:
            from src.core.patrol.batch_events import publish_patrol_batch_event

            plan = dict(command.execution_plan or {})
            batch_id = (
                plan.get("patrol_batch_id")
                or (command.filter_params or {}).get("patrol_batch_id")
            )
            success = bool(result.get("success", True))
            collection_status = str(result.get("collection_status") or "")
            if batch_id and (not success or collection_status == "failed"):
                publish_patrol_batch_event(
                    {
                        "type": "device_patrol_failed",
                        "patrol_batch_id": str(batch_id),
                        "snapshot_run_id": snapshot_run_id,
                        "device_id": (command.filter_params or {}).get("device_id"),
                        "error": result.get("error") or result.get("message"),
                        "collection_status": collection_status or None,
                    }
                )
        except Exception:
            pass
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
