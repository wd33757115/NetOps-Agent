"""PostgreSQL-backed device backup and patrol tools."""

from __future__ import annotations

import asyncio
import copy
import re
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from src.core.collection import CollectionPolicyResolver, ExecutionPlan
from src.core.device_ops.manager import DeviceInventoryManager
from src.core.ingestion.adapters.cli import (
    CLICommandCollector,
    load_command_collection_manifest,
    test_cli_connectivity,
)
from src.apps.rule_governance.runtime import DeviceIdentity, DeviceIdentityResolver
from src.core.parser.runtime import ParserRuntime
from src.core.patrol.concurrency import (
    DistributedPatrolSemaphore,
    HierarchicalConcurrencyLease,
    HierarchicalPatrolLeaseManager,
    PatrolConcurrencyLeaseLostError,
    PatrolConcurrencyUnavailableError,
)
from src.core.patrol.modes import PRODUCTION_MODE, normalize_patrol_mode


class ActionType(str, Enum):
    BACKUP = "backup"
    PATROL = "patrol"
    LIST_DEVICES = "list_devices"
    LIST_GROUPS = "list_groups"
    UNKNOWN = "unknown"


class DeviceFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str | None = None
    device_name: str | None = None
    ip: str | None = None
    group: str | None = None
    vendor: str | None = None
    model: str | None = None
    family: str | None = None
    site: str | None = None
    role: str | None = None
    status: str | None = None

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str | None) -> str | None:
        if value and not re.match(r"^[\d.]+$", value):
            raise ValueError("invalid IP address")
        return value


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    action: ActionType
    total_devices: int = 0
    success_devices: int = 0
    failed_devices: int = 0
    message: str = ""
    details: list[dict[str, Any]] | None = None
    output_files: list[str] | None = None
    execution_time: float = 0.0
    errors: list[str] | None = None


DBManager = DeviceInventoryManager


def test_device_connectivity(
    device: dict[str, Any], *, actor_id: str = "system", task_id: str | None = None
) -> dict[str, Any]:
    """Open and close a real device session using the same path as patrol."""
    return test_cli_connectivity(
        device, actor_id=actor_id, source_app="device-management", task_id=task_id
    )


def _record_connection_health(
    db_manager: DeviceInventoryManager,
    device: dict[str, Any],
    *,
    status: str,
    failure_code: str | None = None,
    error: str | None = None,
    source: str = "collector",
) -> None:
    repository = getattr(db_manager, "repository", None)
    if repository is None or not device.get("device_id"):
        return
    try:
        repository.record_connection_health(
            str(device["device_id"]),
            status=status,
            failure_code=failure_code,
            error=error,
            source=source,
        )
    except Exception:
        pass


def _validate_plan_for_device(
    *,
    device: dict[str, Any],
    plan: ExecutionPlan,
    action: str,
    mode: str,
) -> None:
    if plan.action != action:
        raise ValueError(f"execution plan action mismatch: {plan.action} != {action}")
    if normalize_patrol_mode(plan.mode) != mode:
        raise ValueError(f"execution plan mode mismatch: {plan.mode} != {mode}")
    device_id = str(device.get("device_id") or "")
    if plan.device_id and plan.device_id != device_id:
        raise ValueError(f"execution plan device mismatch: {plan.device_id} != {device_id}")
    if not plan.commands:
        raise ValueError("execution plan has no commands")


class ConfigBackupTool:
    def __init__(
        self,
        db_manager: DeviceInventoryManager,
        *,
        cli_collector: CLICommandCollector | None = None,
        actor_id: str = "system",
        task_id: str | None = None,
    ):
        self.db_manager = db_manager
        self.policy_resolver = CollectionPolicyResolver()
        self.cli_collector = cli_collector or CLICommandCollector(
            actor_id=actor_id, source_app="device-backup", task_id=task_id
        )

    async def backup_device(
        self,
        device: dict[str, Any],
        execution_plan: ExecutionPlan | None = None,
    ) -> dict[str, Any]:
        try:
            plan = execution_plan or self.policy_resolver.build_plan(
                device=device, action="backup", mode=PRODUCTION_MODE
            )
            _validate_plan_for_device(
                device=device, plan=plan, action="backup", mode=PRODUCTION_MODE
            )
            output_file = await asyncio.to_thread(
                self.cli_collector.collect_command_outputs,
                device=device,
                execution_plan=plan,
            )
            manifest = load_command_collection_manifest(output_file)
            command_results = list(manifest.get("commands") or [])
            collection_status = str(manifest.get("status") or "unknown")
            if collection_status == "unknown" and not command_results:
                collection_status = "complete"
            _record_connection_health(
                self.db_manager,
                device,
                status="connected",
                source="device-backup",
            )
            return {
                "device_name": device.get("device_name"),
                "ip": device.get("ip"),
                "vendor": device.get("vendor"),
                "model": device.get("model"),
                "success": collection_status == "complete",
                "output_file": str(output_file),
                "commands_count": len(plan.commands),
                "execution_plan": plan.to_metadata(),
            }
        except Exception as exc:
            _record_connection_health(
                self.db_manager,
                device,
                status="failed",
                failure_code="connection_failed",
                error=str(exc),
                source="device-backup",
            )
            return {
                "device_name": device.get("device_name"),
                "ip": device.get("ip"),
                "success": False,
                "error": str(exc),
            }

    async def backup_by_filter(self, filter_params: DeviceFilter) -> TaskResult:
        started_at = datetime.now()
        devices = self.db_manager.get_devices_by_filter(filter_params)
        if not devices:
            return TaskResult(
                success=False,
                action=ActionType.BACKUP,
                message="No devices matched the requested filter.",
            )
        results = await asyncio.gather(*(self.backup_device(device) for device in devices))
        return _task_result(ActionType.BACKUP, devices, results, started_at)

    async def backup_by_plan(self, execution_plan: ExecutionPlan) -> TaskResult:
        started_at = datetime.now()
        device = self.db_manager.get_device_by_ip_or_name(execution_plan.device_id)
        if device is None:
            matches = self.db_manager.get_devices_by_filter(
                DeviceFilter(device_id=execution_plan.device_id)
            )
            device = matches[0] if matches else None
        if device is None:
            return TaskResult(
                success=False,
                action=ActionType.BACKUP,
                message=f"Execution plan device not found: {execution_plan.device_id}",
            )
        result = await self.backup_device(device, execution_plan=execution_plan)
        return _task_result(ActionType.BACKUP, [device], [result], started_at)


class PatrolTool:
    def __init__(
        self,
        db_manager: DeviceInventoryManager,
        *,
        cli_collector: CLICommandCollector | None = None,
        result_callback: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        actor_id: str = "system",
        task_id: str | None = None,
    ):
        self.db_manager = db_manager
        self.policy_resolver = CollectionPolicyResolver()
        self.cli_collector = cli_collector or CLICommandCollector(
            actor_id=actor_id, source_app="inspection", task_id=task_id
        )
        self.result_callback = result_callback
        from src.common.config import get_settings

        self.max_concurrency = max(1, int(get_settings().PATROL_MAX_CONCURRENCY))
        settings = get_settings()
        self.distributed_semaphore = DistributedPatrolSemaphore(
            limit=self.max_concurrency,
            lease_seconds=settings.PATROL_CONCURRENCY_LEASE_SECONDS,
            wait_seconds=settings.PATROL_CONCURRENCY_WAIT_SECONDS,
            enabled=settings.PATROL_DISTRIBUTED_CONCURRENCY_ENABLED,
            fail_closed=settings.PATROL_CONCURRENCY_FAIL_CLOSED,
        )
        self.hierarchical_lease_manager = HierarchicalPatrolLeaseManager(
            global_semaphore=self.distributed_semaphore,
            site_limit=settings.PATROL_SITE_MAX_CONCURRENCY,
            lease_seconds=settings.PATROL_CONCURRENCY_LEASE_SECONDS,
            wait_seconds=settings.PATROL_CONCURRENCY_WAIT_SECONDS,
            enabled=settings.PATROL_DISTRIBUTED_CONCURRENCY_ENABLED,
            fail_closed=settings.PATROL_CONCURRENCY_FAIL_CLOSED,
        )

    @staticmethod
    def _site_scope(device: dict[str, Any]) -> str:
        attributes = dict(device.get("attributes") or {})
        return str(
            device.get("site")
            or attributes.get("site")
            or device.get("group")
            or attributes.get("group")
            or "__unassigned__"
        )

    def _assert_concurrency_lease(self, lease: Any) -> None:
        if isinstance(lease, HierarchicalConcurrencyLease):
            self.hierarchical_lease_manager.assert_lease(lease)
            return
        self.distributed_semaphore.assert_lease(lease)

    @staticmethod
    def _concurrency_failure_code(exc: Exception) -> str:
        if isinstance(exc, PatrolConcurrencyLeaseLostError):
            return "concurrency_lease_lost"
        if isinstance(exc, PatrolConcurrencyUnavailableError):
            return "concurrency_control_unavailable"
        if isinstance(exc, TimeoutError):
            return "concurrency_lease_timeout"
        return "device_task_failed"

    @staticmethod
    def _decorate_concurrency_result(
        result: dict[str, Any],
        lease: HierarchicalConcurrencyLease,
    ) -> None:
        result["concurrency_mode"] = lease.effective_mode
        result["device_fencing_token"] = lease.fencing_token

    @staticmethod
    def _production_coverage(device: dict[str, Any], commands: list[str]) -> list[dict[str, Any]]:
        vendor = str(device.get("vendor") or "").strip()
        model = str(device.get("model") or "").strip()
        resolver = DeviceIdentityResolver()
        identity = DeviceIdentity(
            vendor=vendor or None,
            model=model or None,
            family=resolver.family_for(vendor, model) if vendor and model else None,
            confidence=1.0 if vendor and model else 0.0,
            evidence_text="PostgreSQL device inventory",
            source="inventory",
        )
        runtime = ParserRuntime()
        return [runtime.check_coverage(identity=identity, command=command) for command in commands]

    async def patrol_device(
        self,
        device: dict[str, Any],
        save_baseline: bool = False,
        mode: str = PRODUCTION_MODE,
        execution_plan: ExecutionPlan | None = None,
        concurrency_lease: Any | None = None,
    ) -> dict[str, Any]:
        try:
            mode = normalize_patrol_mode(mode)
            plan = execution_plan or self.policy_resolver.build_plan(
                device=device, action="patrol", mode=mode
            )
            _validate_plan_for_device(device=device, plan=plan, action="patrol", mode=mode)
            coverage_preflight: list[dict[str, Any]] = []
            coverage_decision: dict[str, Any] | None = None
            check_item_plan = any(
                item.check_item_bindings for item in plan.commands
            )
            if mode == PRODUCTION_MODE and check_item_plan:
                # The Effective Plan already resolved and froze an approved
                # RuntimeBundle. Re-running the legacy global Parser lookup
                # here can disagree with that immutable decision after cache
                # refreshes or asset rollout and must not override it.
                coverage_decision = {
                    "decision": "allow",
                    "failure_code": None,
                    "authority": "frozen_runtime_bundle",
                    "violations": [],
                }
            elif mode == PRODUCTION_MODE:
                from src.core.patrol.coverage_policy import decide_parser_coverage

                coverage_decision = decide_parser_coverage(
                    coverage=self._production_coverage(device, plan.command_texts()),
                    mode=mode,
                    execution_plan=plan,
                )
                coverage_preflight = list(coverage_decision.get("violations") or [])
                if coverage_decision["decision"] == "block":
                    return {
                        "inventory_device_id": device.get("device_id"),
                        "device_name": device.get("device_name"),
                        "ip": device.get("ip"),
                        "vendor": device.get("vendor"),
                        "model": device.get("model"),
                        "success": False,
                        "error": "production patrol blocked by parser coverage policy",
                        "failure_code": coverage_decision["failure_code"],
                        "coverage_preflight": coverage_preflight,
                        "coverage_decision": coverage_decision,
                    }
                if coverage_decision["decision"] == "skip":
                    return {
                        "inventory_device_id": device.get("device_id"),
                        "device_name": device.get("device_name"),
                        "ip": device.get("ip"),
                        "vendor": device.get("vendor"),
                        "model": device.get("model"),
                        "success": False,
                        "error": "production patrol skipped by parser coverage policy",
                        "failure_code": coverage_decision["failure_code"],
                        "skipped": True,
                        "coverage_preflight": coverage_preflight,
                        "coverage_decision": coverage_decision,
                    }
            collector = copy.copy(self.cli_collector)
            collection_device = dict(device)
            if concurrency_lease is not None:
                collector.continuation_guard = lambda: (
                    self._assert_concurrency_lease(concurrency_lease)
                )
                if isinstance(concurrency_lease, HierarchicalConcurrencyLease):
                    collection_device["concurrency_mode"] = (
                        concurrency_lease.effective_mode
                    )
                    collection_device["device_fencing_token"] = (
                        concurrency_lease.fencing_token
                    )
            output_file = await asyncio.to_thread(
                collector.collect_command_outputs,
                device=collection_device,
                execution_plan=plan,
            )
            if concurrency_lease is not None:
                self._assert_concurrency_lease(concurrency_lease)
            manifest = load_command_collection_manifest(output_file)
            command_results = list(manifest.get("commands") or [])
            collection_status = str(manifest.get("status") or "unknown")
            if collection_status == "unknown" and not command_results:
                collection_status = "complete"
            completed_commands = sum(item.get("status") == "complete" for item in command_results)
            _record_connection_health(
                self.db_manager,
                device,
                status="connected",
                source="device-patrol",
            )
            return {
                "inventory_device_id": device.get("device_id"),
                "device_name": device.get("device_name"),
                "ip": device.get("ip"),
                "vendor": device.get("vendor"),
                "model": device.get("model"),
                "success": collection_status == "complete",
                "output_file": str(output_file),
                "commands_count": len(plan.commands),
                "execution_plan": plan.to_metadata(),
                "baseline_saved": save_baseline,
                "collection_status": collection_status,
                "completed_commands": completed_commands,
                "command_collection_results": command_results,
                "coverage_preflight": coverage_preflight,
                "coverage_decision": coverage_decision,
            }
        except Exception as exc:
            failure_code = self._concurrency_failure_code(exc)
            _record_connection_health(
                self.db_manager,
                device,
                status="failed",
                failure_code=(
                    failure_code
                    if failure_code != "device_task_failed"
                    else "connection_failed"
                ),
                error=str(exc),
                source="device-patrol",
            )
            return {
                "inventory_device_id": device.get("device_id"),
                "device_name": device.get("device_name"),
                "ip": device.get("ip"),
                "success": False,
                "error": str(exc),
                "failure_code": (
                    failure_code
                    if failure_code != "device_task_failed"
                    else "connection_failed"
                ),
                "collection_status": "failed",
                "execution_plan": (plan.to_metadata() if "plan" in locals() else None),
            }

    async def patrol_by_filter(
        self,
        filter_params: DeviceFilter,
        save_baseline: bool = False,
        mode: str = PRODUCTION_MODE,
    ) -> TaskResult:
        started_at = datetime.now()
        devices = self.db_manager.get_devices_by_filter(filter_params)
        if not devices:
            return TaskResult(
                success=False,
                action=ActionType.PATROL,
                message="No devices matched the requested filter.",
            )
        queue: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue()
        for index, device in enumerate(devices):
            queue.put_nowait((index, device))
        indexed_results: list[dict[str, Any] | None] = [None] * len(devices)

        async def worker() -> None:
            while True:
                try:
                    index, device = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    callback_invoked = False
                    try:
                        lease = await asyncio.to_thread(
                            self.hierarchical_lease_manager.acquire,
                            device_id=str(
                                device.get("device_id")
                                or device.get("ip")
                                or device.get("management_ip")
                                or device.get("device_name")
                                or "unknown"
                            ),
                            site=self._site_scope(device),
                        )
                        with self.hierarchical_lease_manager.maintain(lease):
                            result = await self.patrol_device(
                                device,
                                save_baseline,
                                mode=mode,
                                concurrency_lease=lease,
                            )
                            self._decorate_concurrency_result(result, lease)
                            self.hierarchical_lease_manager.assert_lease(lease)
                            await self._notify_result(result)
                            callback_invoked = True
                            self.hierarchical_lease_manager.assert_lease(lease)
                    except Exception as exc:
                        failure_code = self._concurrency_failure_code(exc)
                        result = {
                            "inventory_device_id": device.get("device_id"),
                            "device_name": device.get("device_name"),
                            "ip": device.get("ip") or device.get("management_ip"),
                            "success": False,
                            "failure_code": failure_code,
                            "error": str(exc),
                            "collection_status": "failed",
                        }
                    if (
                        not callback_invoked
                        and result.get("failure_code") != "concurrency_lease_lost"
                    ):
                        await self._notify_result(result)
                    indexed_results[index] = result
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker()) for _ in range(min(self.max_concurrency, len(devices)))
        ]
        await asyncio.gather(*workers)
        results = [result for result in indexed_results if result is not None]
        return _task_result(ActionType.PATROL, devices, results, started_at)

    async def patrol_by_plan(
        self,
        execution_plan: ExecutionPlan,
        save_baseline: bool = False,
    ) -> TaskResult:
        started_at = datetime.now()
        device = self.db_manager.get_device_by_ip_or_name(execution_plan.device_id)
        if device is None:
            matches = self.db_manager.get_devices_by_filter(
                DeviceFilter(device_id=execution_plan.device_id)
            )
            device = matches[0] if matches else None
        if device is None:
            return TaskResult(
                success=False,
                action=ActionType.PATROL,
                message=f"Execution plan device not found: {execution_plan.device_id}",
            )
        callback_invoked = False
        try:
            lease = await asyncio.to_thread(
                self.hierarchical_lease_manager.acquire,
                device_id=str(
                    device.get("device_id")
                    or device.get("ip")
                    or device.get("management_ip")
                    or device.get("device_name")
                    or "unknown"
                ),
                site=self._site_scope(device),
            )
            with self.hierarchical_lease_manager.maintain(lease):
                result = await self.patrol_device(
                    device,
                    save_baseline,
                    mode=execution_plan.mode,
                    execution_plan=execution_plan,
                    concurrency_lease=lease,
                )
                self._decorate_concurrency_result(result, lease)
                self.hierarchical_lease_manager.assert_lease(lease)
                await self._notify_result(result)
                callback_invoked = True
                self.hierarchical_lease_manager.assert_lease(lease)
        except Exception as exc:
            failure_code = self._concurrency_failure_code(exc)
            result = {
                "inventory_device_id": device.get("device_id"),
                "device_name": device.get("device_name"),
                "ip": device.get("ip") or device.get("management_ip"),
                "success": False,
                "failure_code": failure_code,
                "error": str(exc),
                "collection_status": "failed",
                "execution_plan": execution_plan.to_metadata(),
            }
        if not callback_invoked and result.get("failure_code") != "concurrency_lease_lost":
            await self._notify_result(result)
        return _task_result(ActionType.PATROL, [device], [result], started_at)

    async def _notify_result(self, result: dict[str, Any]) -> None:
        if self.result_callback is None:
            return
        try:
            persisted = await asyncio.to_thread(self.result_callback, result)
            if persisted is not None:
                result["snapshot_persistence"] = persisted
        except Exception as exc:
            error = f"device result persistence failed: {exc}"
            result["success"] = False
            result["failure_code"] = "snapshot_persistence_failed"
            result["persistence_error"] = error
            result["snapshot_persistence"] = {
                "snapshot_devices": 0,
                "snapshot_commands": 0,
                "snapshot_errors": [error],
                "snapshot_health": "failed",
                "collection_status": "failed",
                "coverage_violations": [],
                "parse_violations": [],
                "production_parser_ready": False,
            }


def _task_result(
    action: ActionType,
    devices: list[dict[str, Any]],
    results: list[dict[str, Any]],
    started_at: datetime,
) -> TaskResult:
    success_count = sum(bool(item.get("success")) for item in results)
    return TaskResult(
        success=success_count == len(devices),
        action=action,
        total_devices=len(devices),
        success_devices=success_count,
        failed_devices=len(devices) - success_count,
        message=f"{action.value} completed: {success_count}/{len(devices)}",
        details=results,
        output_files=[str(item["output_file"]) for item in results if item.get("output_file")],
        execution_time=(datetime.now() - started_at).total_seconds(),
        errors=[str(item["error"]) for item in results if item.get("error")],
    )
