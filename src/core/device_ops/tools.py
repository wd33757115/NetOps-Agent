"""PostgreSQL-backed device backup and patrol tools."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from src.core.device_ops.manager import DeviceInventoryManager
from src.core.parser.identity import DeviceIdentity, DeviceIdentityResolver
from src.core.parser.runtime import ParserRuntime
from src.core.patrol.modes import PRODUCTION_MODE, normalize_patrol_mode

BEIJING_TZ = timezone(timedelta(hours=8))
OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "skills" / "shared" / "outputs"


class ActionType(str, Enum):
    BACKUP = "backup"
    PATROL = "patrol"
    LIST_DEVICES = "list_devices"
    LIST_GROUPS = "list_groups"
    UNKNOWN = "unknown"


class DeviceFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_name: str | None = None
    ip: str | None = None
    group: str | None = None
    model: str | None = None

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
    attributes = dict(device.get("attributes") or {})
    explicit = str(attributes.get("netmiko_device_type") or "").strip()
    if explicit:
        return explicit
    vendor = str(device.get("vendor") or "").lower()
    if vendor == "cisco":
        return "cisco_ios"
    if vendor in {"h3c", "hp"}:
        return "hp_comware"
    if vendor == "huawei":
        return "huawei"
    return "generic"


def _commands(device: dict[str, Any], action: str) -> list[str]:
    attributes = dict(device.get("attributes") or {})
    configured = attributes.get(f"{action}_commands")
    if isinstance(configured, list) and configured:
        return [str(item) for item in configured]
    vendor = str(device.get("vendor") or "").lower()
    if action == "backup":
        return (
            ["show version", "show running-config"]
            if vendor == "cisco"
            else ["display version", "display current-configuration"]
        )
    if vendor == "cisco":
        return [
            "show version",
            "show inventory",
            "show interface status",
            "show processes cpu",
            "show processes memory",
        ]
    return [
        "display version",
        "display device manuinfo",
        "display interface brief",
        "display cpu-usage",
        "display memory",
    ]


def _connect(device: dict[str, Any]):
    from netmiko import ConnectHandler

    password = device.get("password")
    if not password:
        raise ValueError(
            "device credential is unavailable; configure credential_ref as env:VARIABLE"
        )
    return ConnectHandler(
        device_type=_device_type(device),
        host=device.get("ip"),
        username=device.get("username") or "admin",
        password=password,
        read_timeout_override=60,
    )


def _write_command_report(
    *,
    device: dict[str, Any],
    action: str,
    commands: list[str],
) -> Path:
    device_name = str(device.get("device_name") or "unknown")
    timestamp = datetime.now(BEIJING_TZ).strftime("%Y%m%d%H%M%S")
    output_dir = OUTPUT_ROOT / action / timestamp / device_name
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{device_name}_{device.get('ip')}_{timestamp}.txt"

    connection = _connect(device)
    sections: list[str] = []
    try:
        for command in commands:
            output = connection.send_command(command, read_timeout=60)
            sections.append(
                f"Command: {command}\nOutput:\n{output}\n{'-' * 50}"
            )
    finally:
        connection.disconnect()
    output_file.write_text("\n".join(sections), encoding="utf-8")
    return output_file


class ConfigBackupTool:
    def __init__(self, db_manager: DeviceInventoryManager):
        self.db_manager = db_manager

    async def backup_device(self, device: dict[str, Any]) -> dict[str, Any]:
        try:
            commands = _commands(device, "backup")
            output_file = await asyncio.to_thread(
                _write_command_report,
                device=device,
                action="backup",
                commands=commands,
            )
            return {
                "device_name": device.get("device_name"),
                "ip": device.get("ip"),
                "vendor": device.get("vendor"),
                "model": device.get("model"),
                "success": True,
                "output_file": str(output_file),
                "commands_count": len(commands),
            }
        except Exception as exc:
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


class PatrolTool:
    def __init__(self, db_manager: DeviceInventoryManager):
        self.db_manager = db_manager

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
    ) -> dict[str, Any]:
        try:
            mode = normalize_patrol_mode(mode)
            commands = _commands(device, "patrol")
            if mode == PRODUCTION_MODE:
                blocking = [
                    item
                    for item in self._production_coverage(device, commands)
                    if item["policy_status"] == "eligible"
                    and item["coverage_status"] != "ready"
                ]
                if blocking:
                    return {
                        "device_name": device.get("device_name"),
                        "ip": device.get("ip"),
                        "vendor": device.get("vendor"),
                        "model": device.get("model"),
                        "success": False,
                        "error": "production patrol blocked by parser coverage preflight",
                        "coverage_violations": blocking,
                    }
            output_file = await asyncio.to_thread(
                _write_command_report,
                device=device,
                action="patrol",
                commands=commands,
            )
            return {
                "device_name": device.get("device_name"),
                "ip": device.get("ip"),
                "vendor": device.get("vendor"),
                "model": device.get("model"),
                "success": True,
                "output_file": str(output_file),
                "commands_count": len(commands),
                "baseline_saved": save_baseline,
            }
        except Exception as exc:
            return {
                "device_name": device.get("device_name"),
                "ip": device.get("ip"),
                "success": False,
                "error": str(exc),
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
        results = await asyncio.gather(
            *(
                self.patrol_device(device, save_baseline, mode=mode)
                for device in devices
            )
        )
        return _task_result(ActionType.PATROL, devices, results, started_at)


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
