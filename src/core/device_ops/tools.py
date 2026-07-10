"""PostgreSQL-backed device backup and patrol tools."""

from __future__ import annotations

import asyncio
import json
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
    connect_cli_device,
    device_type_for,
    load_command_collection_manifest,
    test_cli_connectivity,
)
from src.core.parser.identity import DeviceIdentity, DeviceIdentityResolver
from src.core.parser.runtime import ParserRuntime
from src.core.patrol.concurrency import DistributedPatrolSemaphore
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


def _device_type(device: dict[str, Any]) -> str:
    return device_type_for(device)


def _connect(device: dict[str, Any]):
    return connect_cli_device(device)


def test_device_connectivity(device: dict[str, Any]) -> dict[str, Any]:
    """Open and close a real device session using the same path as patrol."""
    return test_cli_connectivity(device)


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


def _collect_command_outputs(
    *,
    device: dict[str, Any],
    plan: ExecutionPlan,
) -> Any:
    """Collect raw CLI command outputs for later snapshot parsing and reporting."""
    return CLICommandCollector().collect_command_outputs(
        device=device,
        execution_plan=plan,
    )


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
        raise ValueError(
            f"execution plan device mismatch: {plan.device_id} != {device_id}"
        )
    if not plan.commands:
        raise ValueError("execution plan has no commands")


class ConfigBackupTool:
    def __init__(
        self,
        db_manager: DeviceInventoryManager,
        *,
        cli_collector: CLICommandCollector | None = None,
    ):
        self.db_manager = db_manager
        self.policy_resolver = CollectionPolicyResolver()
        self.cli_collector = cli_collector or CLICommandCollector()

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
        results = await asyncio.gather(
            *(self.backup_device(device) for device in devices)
        )
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
    ):
        self.db_manager = db_manager
        self.policy_resolver = CollectionPolicyResolver()
        self.cli_collector = cli_collector or CLICommandCollector()
        self.result_callback = result_callback
        from src.common.config import get_settings

        self.max_concurrency = max(1, int(get_settings().PATROL_MAX_CONCURRENCY))
        settings = get_settings()
        self.distributed_semaphore = DistributedPatrolSemaphore(
            limit=self.max_concurrency,
            lease_seconds=settings.PATROL_CONCURRENCY_LEASE_SECONDS,
            wait_seconds=settings.PATROL_CONCURRENCY_WAIT_SECONDS,
            enabled=settings.PATROL_DISTRIBUTED_CONCURRENCY_ENABLED,
        )

    @staticmethod
    def _production_coverage(
        device: dict[str, Any], commands: list[str]
    ) -> list[dict[str, Any]]:
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
        return [
            runtime.check_coverage(identity=identity, command=command)
            for command in commands
        ]

    async def patrol_device(
        self,
        device: dict[str, Any],
        save_baseline: bool = False,
        mode: str = PRODUCTION_MODE,
        execution_plan: ExecutionPlan | None = None,
    ) -> dict[str, Any]:
        try:
            mode = normalize_patrol_mode(mode)
            plan = execution_plan or self.policy_resolver.build_plan(
                device=device, action="patrol", mode=mode
            )
            _validate_plan_for_device(device=device, plan=plan, action="patrol", mode=mode)
            if mode == PRODUCTION_MODE:
                blocking = [
                    item
                    for item in self._production_coverage(device, plan.command_texts())
                    if item["policy_status"] == "eligible"
                    and item["coverage_status"] != "ready"
                ]
                if blocking:
                    return {
                        "inventory_device_id": device.get("device_id"),
                        "device_name": device.get("device_name"),
                        "ip": device.get("ip"),
                        "vendor": device.get("vendor"),
                        "model": device.get("model"),
                        "success": False,
                        "error": "production patrol blocked by parser coverage preflight",
                        "coverage_violations": blocking,
                        "collection_status": "failed",
                        "execution_plan": plan.to_metadata(),
                    }
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
            completed_commands = sum(
                item.get("status") == "complete" for item in command_results
            )
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
            }
        except Exception as exc:
            _record_connection_health(
                self.db_manager,
                device,
                status="failed",
                failure_code="connection_failed",
                error=str(exc),
                source="device-patrol",
            )
            return {
                "inventory_device_id": device.get("device_id"),
                "device_name": device.get("device_name"),
                "ip": device.get("ip"),
                "success": False,
                "error": str(exc),
                "collection_status": "failed",
                "execution_plan": (
                    plan.to_metadata() if "plan" in locals() else None
                ),
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
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def bounded_patrol(device):
            async with semaphore:
                lease = await asyncio.to_thread(self.distributed_semaphore.acquire)
                with self.distributed_semaphore.maintain(lease):
                    result = await self.patrol_device(device, save_baseline, mode=mode)
                    result["concurrency_mode"] = lease.mode
                    return result

        tasks = [asyncio.create_task(bounded_patrol(device)) for device in devices]
        results: list[dict[str, Any]] = []
        for task in asyncio.as_completed(tasks):
            result = await task
            await self._notify_result(result)
            results.append(result)
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
        lease = await asyncio.to_thread(self.distributed_semaphore.acquire)
        with self.distributed_semaphore.maintain(lease):
            result = await self.patrol_device(
                device,
                save_baseline,
                mode=execution_plan.mode,
                execution_plan=execution_plan,
            )
            result["concurrency_mode"] = lease.mode
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
        output_files=[
            str(item["output_file"]) for item in results if item.get("output_file")
        ],
        execution_time=(datetime.now() - started_at).total_seconds(),
        errors=[str(item["error"]) for item in results if item.get("error")],
    )


class IntentParser:
    def parse(self, query: str) -> tuple[ActionType, DeviceFilter]:
        lowered = query.lower()
        if "巡检" in query or "patrol" in lowered:
            action = ActionType.PATROL
        elif "备份" in query or "backup" in lowered:
            action = ActionType.BACKUP
        elif "分组" in query or "group" in lowered:
            action = ActionType.LIST_GROUPS
        elif "设备" in query or "device" in lowered:
            action = ActionType.LIST_DEVICES
        else:
            action = ActionType.UNKNOWN
        ip_match = re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", query)
        return action, DeviceFilter(ip=ip_match.group(0) if ip_match else None)


class NetOpsToolsOrchestrator:
    def __init__(self):
        self.db_manager = DBManager()
        self.intent_parser = IntentParser()

    async def execute_async(self, query: str) -> dict[str, Any]:
        action, device_filter = self.intent_parser.parse(query)
        if action == ActionType.LIST_GROUPS:
            return {"success": True, "groups": self.db_manager.list_groups()}
        if action == ActionType.LIST_DEVICES:
            devices = self.db_manager.get_devices_by_filter(device_filter)
            return {
                "success": True,
                "devices": [
                    {key: value for key, value in item.items() if key != "password"}
                    for item in devices
                ],
            }
        if action in {ActionType.BACKUP, ActionType.PATROL}:
            from src.core.celery_tasks.tasks import (
                execute_config_backup_task,
                execute_device_patrol_task,
            )

            task = (
                execute_config_backup_task.delay(
                    filter_params=device_filter.model_dump(exclude_none=True)
                )
                if action == ActionType.BACKUP
                else execute_device_patrol_task.delay(
                    filter_params=device_filter.model_dump(exclude_none=True)
                )
            )
            return {
                "success": True,
                "action": action.value,
                "celery_task_id": task.id,
                "status": "pending",
            }
        return {"success": False, "message": "Unsupported request."}

    def execute_sync(self, query: str) -> dict[str, Any]:
        return asyncio.run(self.execute_async(query))

    execute = execute_async


def describe_inventory() -> str:
    manager = DBManager()
    return json.dumps(
        {
            "groups": manager.list_groups(),
            "devices": manager.get_devices_by_filter(DeviceFilter()),
        },
        ensure_ascii=False,
        default=str,
    )
