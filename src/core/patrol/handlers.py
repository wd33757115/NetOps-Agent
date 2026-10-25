# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Application handlers for patrol use cases shared by transport adapters."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from src.core.check_items.report_contract import (
    PARSER_VALIDATION,
    SHADOW_VALIDATION,
    normalize_report_purpose,
    report_purpose_for_patrol_mode,
)
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
    def mark_plan_freeze_failed(
        self, patrol_batch_id: str, error: str
    ) -> None: ...
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
    target_devices: list[dict[str, Any]] | None = None
    check_item_ids_by_device: dict[str, list[str]] | None = None
    profile_version_ids_by_device: dict[str, str | None] | None = None
    actor: str = "device-management"
    report_purpose: str | None = None
    scope_contract: dict[str, Any] | None = None
    scope_exclusions: dict[str, dict[str, Any]] | None = None
    scope_not_applicable: dict[str, dict[str, Any]] | None = None


class StartPatrolHandler:
    """Persist the complete batch intent, then enqueue durable dispatch."""

    def __init__(
        self,
        *,
        plan_repository: PlanRepository | None = None,
        batch_repository: BatchManifestRepository | None = None,
        batch_dispatcher: Callable[[str], str] | None = None,
        batch_id_factory: Callable[[], str] | None = None,
        effective_plan_service: Any | None = None,
        check_execution_service: Any | None = None,
    ) -> None:
        self._plans = plan_repository or ExecutionPlanRepository()
        if batch_repository is None:
            from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

            batch_repository = PatrolBatchManifestRepository()
        self._batches = batch_repository
        self._batch_dispatcher = batch_dispatcher or self._enqueue_batch_dispatch
        self._batch_id_factory = batch_id_factory or (lambda: f"patrol-batch-{uuid.uuid4()}")
        self._effective_plan_service = effective_plan_service
        self._check_execution_service = check_execution_service

    def handle(self, command: StartPatrolCommand) -> dict[str, Any]:
        if command.launch_mode not in {"workflow", "direct"}:
            raise ValueError("launch_mode must be workflow or direct")
        command = replace(command, launch_mode="direct")
        patrol_mode = normalize_patrol_mode(command.mode)
        report_purpose = normalize_report_purpose(
            command.report_purpose,
            default=report_purpose_for_patrol_mode(patrol_mode),
        )
        if (
            patrol_mode == PARSER_VALIDATION
            and report_purpose
            not in {PARSER_VALIDATION, SHADOW_VALIDATION}
        ):
            raise ValueError(
                "non-production parser patrols must use "
                "report_purpose=parser_validation or shadow_validation"
            )
        patrol_batch_id = self._batch_id_factory()
        check_item_ids_by_device = {
            str(device_id): tuple(
                sorted(
                    {
                        str(item)
                        for item in values
                        if str(item)
                    }
                )
            )
            for device_id, values in (
                command.check_item_ids_by_device or {}
            ).items()
        }
        check_item_mode = bool(check_item_ids_by_device)
        skipped_errors = [dict(item) for item in command.skipped_errors]
        effective_request = None
        effective_preview = None
        rows_to_plan = list(command.rows)
        if check_item_mode:
            (
                effective_request,
                effective_preview,
                rows_to_plan,
                additional_skips,
            ) = self._prepare_check_item_launch(
                patrol_batch_id=patrol_batch_id,
                command=command,
                check_item_ids_by_device=check_item_ids_by_device,
            )
            skipped_errors.extend(additional_skips)
        planned_rows: list[dict[str, Any]] = []
        try:
            for row in rows_to_plan:
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
                patrol_mode=patrol_mode,
                report_purpose=report_purpose,
                save_baseline=command.save_baseline,
                ticket_id=command.ticket_id,
                target_count=command.target_count,
                planned_rows=planned_rows,
                skipped_errors=skipped_errors,
                target_snapshot=dict(command.target_snapshot or {}),
            )
            if check_item_mode:
                frozen = self._effective_plan().freeze(effective_request)
                if frozen.manifest_digest != effective_preview.manifest_digest:
                    raise ValueError(
                        "effective plan changed between preview and freeze"
                    )
                self._finalize_preflight_failures(
                    frozen=frozen,
                    skipped_errors=skipped_errors,
                )
        except Exception as exc:
            for row in planned_rows:
                self._plans.mark_failed(
                    str(row["execution_plan_id"]),
                    failure_code="batch_manifest_persist_failed",
                    error=str(exc),
                )
            if check_item_mode:
                try:
                    self._batches.mark_plan_freeze_failed(
                        patrol_batch_id,
                        str(exc),
                    )
                except Exception:
                    pass
            raise

        dispatch_task_id = None
        if planned_rows:
            try:
                dispatch_task_id = self._batch_dispatcher(patrol_batch_id)
            except Exception as exc:
                self._batches.note_dispatch_enqueue_error(patrol_batch_id, str(exc))
        else:
            self._batches.finalize_dispatch(patrol_batch_id)
        return {
            "success": True,
            "target_count": command.target_count,
            "queued_count": len(planned_rows),
            "launched_count": 0,
            "mode": patrol_mode,
            "report_purpose": report_purpose,
            "save_baseline": command.save_baseline,
            "ticket_id": command.ticket_id,
            "patrol_batch_id": patrol_batch_id,
            "check_item_mode": check_item_mode,
            "runtime_manifest_id": (
                effective_preview.manifest_id
                if effective_preview is not None
                else None
            ),
            "runtime_manifest_digest": (
                effective_preview.manifest_digest
                if effective_preview is not None
                else None
            ),
            "launch_mode": command.launch_mode,
            "workflow_name": command.workflow_name,
            "dispatch_status": "queued" if dispatch_task_id else "pending",
            "dispatch_task_id": dispatch_task_id,
            "runs": [],
            "tasks": [],
            "skipped_count": len(skipped_errors),
            "skipped_errors": skipped_errors,
        }

    def _effective_plan(self) -> Any:
        if self._effective_plan_service is None:
            from src.core.check_items import CheckItemEffectivePlanService

            self._effective_plan_service = CheckItemEffectivePlanService()
        return self._effective_plan_service

    def _execution_service(self) -> Any:
        if self._check_execution_service is None:
            from src.core.check_items import CheckItemExecutionService

            self._check_execution_service = CheckItemExecutionService()
        return self._check_execution_service

    def _prepare_check_item_launch(
        self,
        *,
        patrol_batch_id: str,
        command: StartPatrolCommand,
        check_item_ids_by_device: dict[str, tuple[str, ...]],
    ) -> tuple[Any, Any, list[dict[str, Any]], list[dict[str, Any]]]:
        from src.core.check_items import (
            EffectivePlanRequest,
            build_inventory_snapshot,
            build_target_manifest,
        )

        devices = tuple(
            dict(item) for item in (command.target_devices or [])
        )
        if not devices:
            raise ValueError(
                "check-item patrol requires the complete target inventory snapshot"
            )
        device_ids = {
            str(item.get("device_id") or item.get("id") or "")
            for item in devices
        }
        if device_ids != set(check_item_ids_by_device):
            raise ValueError(
                "every target device must resolve exactly one check-item profile"
            )
        target = build_target_manifest(
            devices=devices,
            selector=dict(
                (command.target_snapshot or {}).get("selector") or {}
            ),
        )
        inventory = build_inventory_snapshot(devices)
        profile_versions = dict(
            command.profile_version_ids_by_device or {}
        )
        unique_profiles = {
            str(value)
            for value in profile_versions.values()
            if value
        }
        request = EffectivePlanRequest(
            patrol_batch_id=patrol_batch_id,
            devices=devices,
            check_item_ids=(),
            check_item_ids_by_device=check_item_ids_by_device,
            profile_version_ids_by_device=profile_versions,
            profile_version_id=(
                next(iter(unique_profiles))
                if len(unique_profiles) == 1
                else None
            ),
            target_manifest_digest=str(target["digest"]),
            inventory_snapshot_digest=str(inventory["digest"]),
            current_observed_at=datetime.now(timezone.utc),
            actor=command.actor,
            report_purpose=command.report_purpose,
            scope_contract=(
                dict(command.scope_contract)
                if command.scope_contract is not None
                else dict(
                    (command.target_snapshot or {}).get(
                        "scope_contract"
                    )
                    or {}
                )
                or None
            ),
            scope_exclusions=dict(command.scope_exclusions or {}),
            scope_not_applicable=dict(
                command.scope_not_applicable or {}
            ),
        )
        preview = self._effective_plan().preview(request)
        actions_by_device: dict[str, list[dict[str, Any]]] = {}
        for action in preview.collection_actions:
            actions_by_device.setdefault(
                str(action["device_id"]),
                [],
            ).append(dict(action))
        rows: list[dict[str, Any]] = []
        additional_skips: list[dict[str, Any]] = []
        for row in command.rows:
            device_id = str(row.get("device_id") or "")
            actions = actions_by_device.get(device_id, [])
            if not actions:
                additional_skips.append(
                    {
                        "device_id": device_id,
                        "device_name": row.get("device_name"),
                        "ip": row.get("ip"),
                        "failure_code": "no_collection_action_required",
                        "error": (
                            "all selected check items are terminal before "
                            "collection or have no executable action"
                        ),
                        "preflight_status": "resolved_without_collection",
                    }
                )
                continue
            unsupported_collectors = sorted(
                {
                    str(action.get("collector") or "")
                    for action in actions
                    if str(action.get("collector") or "") != "cli"
                }
            )
            if unsupported_collectors:
                raise ValueError(
                    "unsupported patrol collectors: "
                    + ", ".join(unsupported_collectors)
                )
            base_plan = dict(row.get("plan") or {})
            base_plan.update(
                {
                    "device_id": device_id,
                    "action": "patrol",
                    "mode": command.mode,
                    "check_item_ids": list(
                        check_item_ids_by_device[device_id]
                    ),
                    "runtime_manifest_id": preview.manifest_id,
                    "runtime_manifest_digest": preview.manifest_digest,
                    "collection_items": [
                        {
                            "command": action["command"],
                            "action": "patrol",
                            "collection_item_id": action["id"],
                            "fallback_rank": action["fallback_rank"],
                            "check_item_bindings": list(
                                action.get("bindings") or []
                            ),
                        }
                        for action in actions
                    ],
                }
            )
            rows.append(
                {
                    **row,
                    "plan": base_plan,
                    "commands": [
                        action["command"] for action in actions
                    ],
                    "command_count": len(actions),
                }
            )
        return request, preview, rows, additional_skips

    def _finalize_preflight_failures(
        self,
        *,
        frozen: Any,
        skipped_errors: list[dict[str, Any]],
    ) -> None:
        failure_by_device = {
            str(item.get("device_id") or ""): item
            for item in skipped_errors
            if item.get("device_id")
        }
        for execution in frozen.executions:
            device_id = str(execution["device_id"])
            failure = failure_by_device.get(device_id)
            if (
                failure is None
                or execution["execution_status"] != "planned"
            ):
                continue
            self._execution_service().fail_execution(
                str(execution["id"]),
                status="collection_failed",
                reason_codes=(
                    str(
                        failure.get("failure_code")
                        or "preflight_failed"
                    ),
                ),
                evidence_refs=(str(execution["id"]),),
            )

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
        from src.infrastructure.db.models import PatrolBatchRecord
        from src.infrastructure.db.postgres import get_db_session
        with get_db_session() as db:
            batch = db.get(PatrolBatchRecord, patrol_batch_id)
            rule_capture = bool(batch and (batch.selector_json or {}).get("rollout_stage_execution_id"))
        if rule_capture:
            from src.apps.rule_governance.backend.rollout_tasks import dispatch_capture
            task = dispatch_capture.apply_async(args=[patrol_batch_id], queue="app.rule-governance")
        else:
            from src.apps.inspection.backend.tasks import dispatch_patrol_batch_task
            task = dispatch_patrol_batch_task.apply_async(args=[patrol_batch_id], queue="netops.patrol")
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
        check_item_finalizer: Any | None = None,
    ) -> None:
        self._plans = plan_repository or ExecutionPlanRepository()
        if batch_repository is None:
            from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

            batch_repository = PatrolBatchManifestRepository()
        self._batches = batch_repository
        self._workflow_starter = workflow_starter or self._start_workflow
        self._task_launcher = task_launcher or self._launch_task
        self._check_item_finalizer = check_item_finalizer

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
                plan_json: dict[str, Any] = {}
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
                    committed = self._batches.mark_target_dispatched(
                        str(target["target_id"]),
                        claim_token=str(target["claim_token"]),
                        **launch_fields,
                    )
                    if not committed:
                        self._cancel_uncommitted_launch(
                            launch_mode=str(target["launch_mode"]),
                            dispatch_id=dispatch_id,
                            patrol_batch_id=patrol_batch_id,
                            device_id=str(target["device_id"]),
                            runtime_manifest_id=plan_json.get("runtime_manifest_id"),
                        )
                        continue
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
                    failure_recorded = self._batches.mark_target_dispatch_failed(
                        str(target["target_id"]),
                        claim_token=str(target["claim_token"]),
                        failure_code=failure_code,
                        error=str(exc),
                    )
                    if not failure_recorded:
                        continue
                    if plan_json.get("runtime_manifest_id"):
                        try:
                            if self._check_item_finalizer is None:
                                from src.core.check_items import (
                                    CheckItemRunFinalizer,
                                )

                                self._check_item_finalizer = (
                                    CheckItemRunFinalizer()
                                )
                            self._check_item_finalizer.fail_device(
                                patrol_batch_id=patrol_batch_id,
                                device_id=str(target["device_id"]),
                                reason_code=failure_code,
                                evidence_refs=(plan_id,),
                            )
                        except Exception:
                            pass
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
        raise ValueError("巡检 Workflow 已退役，请通过巡检应用重新提交固定任务")

    def _cancel_uncommitted_launch(
        self,
        *,
        launch_mode: str,
        dispatch_id: str,
        patrol_batch_id: str,
        device_id: str,
        runtime_manifest_id: str | None,
    ) -> None:
        reason = "batch cancellation won the dispatch commit race"
        if launch_mode == "workflow":
            from src.core.workflows.engine import WorkflowEngine

            WorkflowEngine.cancel_run(
                dispatch_id,
                actor="batch-cancellation-fence",
                reason=reason,
            )
        else:
            from src.core.celery_tasks.celery_app import celery

            celery.control.revoke(dispatch_id, terminate=False)
        if runtime_manifest_id:
            if self._check_item_finalizer is None:
                from src.core.check_items import CheckItemRunFinalizer

                self._check_item_finalizer = CheckItemRunFinalizer()
            self._check_item_finalizer.fail_device(
                patrol_batch_id=patrol_batch_id,
                device_id=device_id,
                reason_code="patrol_batch_cancelled",
                status="cancelled",
                evidence_refs=(dispatch_id,),
            )

    @staticmethod
    def _launch_task(**kwargs: Any) -> str:
        if kwargs.get("mode") == PRODUCTION_MODE:
            from src.apps.inspection.backend.online_tasks import execute_online

            task = execute_online.apply_async(
                kwargs={"patrol_batch_id": kwargs["patrol_batch_id"],
                        "execution_plan_id": kwargs["execution_plan_id"]},
                queue="netops.patrol", task_id=str(kwargs["task_id"]),
            )
            return str(task.id)
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
    defer_completion: bool = False
    actor_id: str = "system"
    source_app: str = "inspection"


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
                actor_id=command.actor_id,
                source_app=command.source_app,
            )
        )
        snapshot_run_id = result.get("snapshot_run_id")
        if snapshot_run_id and not command.defer_completion:
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
    def __init__(
        self,
        executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        *,
        check_item_finalizer: Any | None = None,
    ):
        if executor is None:
            from src.core.patrol.event_builder import build_events_from_params

            executor = build_events_from_params
        self._executor = executor
        self._check_item_finalizer = check_item_finalizer

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        copied = dict(params)
        result = self._executor(copied)
        runtime_manifest = dict(
            copied.get("runtime_asset_manifest") or {}
        )
        if (
            result.get("success")
            and runtime_manifest.get("manifest_kind")
            == "check_item_runtime"
            and copied.get("patrol_batch_id")
            and copied.get("device_id")
            and copied.get("run_id")
        ):
            if self._check_item_finalizer is None:
                from src.core.check_items import CheckItemRunFinalizer

                self._check_item_finalizer = CheckItemRunFinalizer()
            result = {
                **result,
                "check_item_assessment": (
                    self._check_item_finalizer.finalize(
                        patrol_batch_id=str(
                            copied["patrol_batch_id"]
                        ),
                        run_id=str(copied["run_id"]),
                        device_id=str(copied["device_id"]),
                    )
                ),
            }
        return result


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
