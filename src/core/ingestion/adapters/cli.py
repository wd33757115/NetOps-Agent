"""CLI ingestion adapter.

This module owns transport-level CLI collection. Device Management and
device-patrol can decide *what* to collect, but the connection and raw output
capture path lives here so future collectors can share a consistent boundary.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from src.core.collection import ExecutionPlan
from src.core.credentials import CredentialResolver
from src.core.ingestion.adapter import IngestionAdapter
from src.core.ingestion.domain import IngestionResult

BEIJING_TZ = timezone(timedelta(hours=8))
OUTPUT_ROOT = Path(__file__).resolve().parents[3] / "skills" / "shared" / "outputs"


@dataclass(frozen=True, slots=True)
class CLICollectionPlan:
    device: dict[str, Any]
    execution_plan: ExecutionPlan


def _safe_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def device_type_for(device: dict[str, Any]) -> str:
    attributes = dict(device.get("attributes") or {})
    explicit = str(
        attributes.get("netmiko_device_type")
        or attributes.get("device_type")
        or ""
    ).strip()
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


def device_with_plan_context(
    device: dict[str, Any],
    execution_plan: ExecutionPlan,
) -> dict[str, Any]:
    """Merge execution-plan runtime context without mutating inventory data."""
    attributes = dict(device.get("attributes") or {})
    collector_options = dict(attributes.get("collector_options") or {})
    action_options = attributes.get(f"{execution_plan.action}_collector_options")
    if isinstance(action_options, dict):
        collector_options.update(action_options)
    collector_options.update(dict(execution_plan.collector_options or {}))
    attributes.update(collector_options)
    return {
        **device,
        "credential_ref": execution_plan.credential_ref or device.get("credential_ref"),
        "attributes": attributes,
    }


def connect_cli_device(
    device: dict[str, Any],
    *,
    credential_resolver: CredentialResolver | None = None,
):
    from netmiko import ConnectHandler

    resolver = credential_resolver or CredentialResolver()
    password = device.get("password") or resolver.resolve_password(
        device.get("credential_ref")
    )
    if not password:
        raise ValueError(
            "device credential is unavailable; configure credential_ref as env:VARIABLE"
        )
    attributes = dict(device.get("attributes") or {})
    host = device.get("ip") or device.get("management_ip")
    if not host:
        raise ValueError("device management IP is required")
    return ConnectHandler(
        device_type=device_type_for(device),
        host=host,
        username=device.get("username") or "admin",
        password=password,
        port=_safe_int(attributes.get("port"), 22),
        conn_timeout=_safe_float(attributes.get("conn_timeout"), 10.0),
        auth_timeout=_safe_float(attributes.get("auth_timeout"), 10.0),
        banner_timeout=_safe_float(attributes.get("banner_timeout"), 15.0),
        global_delay_factor=_safe_float(attributes.get("global_delay_factor"), 1.0),
        read_timeout_override=_safe_int(
            attributes.get("read_timeout_override") or attributes.get("read_timeout"),
            60,
        ),
    )


def connect_cli_device_with_retries(
    device: dict[str, Any],
    *,
    connector: Callable[[dict[str, Any]], Any] = connect_cli_device,
) -> Any:
    """Connect to a CLI device with a small retry window for virtual-lab SSH flaps."""
    attributes = dict(device.get("attributes") or {})
    attempts = max(1, _safe_int(attributes.get("connection_attempts"), 3))
    delay = max(0.0, _safe_float(attributes.get("connection_retry_delay"), 1.0))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return connector(device)
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def test_cli_connectivity(
    device: dict[str, Any],
    *,
    connector: Callable[[dict[str, Any]], Any] = connect_cli_device,
) -> dict[str, Any]:
    """Open and close a real device session using the production CLI path."""
    started = time.perf_counter()
    connection = None
    try:
        connection = connect_cli_device_with_retries(device, connector=connector)
        prompt = ""
        find_prompt = getattr(connection, "find_prompt", None)
        if callable(find_prompt):
            prompt = str(find_prompt() or "")
        return {
            "success": True,
            "failure_code": None,
            "device_name": device.get("device_name"),
            "ip": device.get("ip") or device.get("management_ip"),
            "device_type": device_type_for(device),
            "username": device.get("username") or "admin",
            "credential_ref": device.get("credential_ref"),
            "prompt": prompt,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        return {
            "success": False,
            "failure_code": "connection_failed",
            "device_name": device.get("device_name"),
            "ip": device.get("ip") or device.get("management_ip"),
            "device_type": device_type_for(device),
            "username": device.get("username") or "admin",
            "credential_ref": device.get("credential_ref"),
            "error": str(exc),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:
                pass


class CLICommandCollector(IngestionAdapter):
    """Collect raw CLI command output into a command-report file."""

    source_type = "cli"

    def __init__(
        self,
        *,
        output_root: Path = OUTPUT_ROOT,
        connector: Callable[[dict[str, Any]], Any] = connect_cli_device,
    ) -> None:
        self.output_root = output_root
        self.connector = connector

    def collect(self, plan: CLICollectionPlan) -> IngestionResult:
        effective_device = device_with_plan_context(plan.device, plan.execution_plan)
        output_file = self.collect_command_outputs(
            device=effective_device,
            execution_plan=plan.execution_plan,
        )
        return IngestionResult(
            source_type=self.source_type,
            evidence_type="cli_command_output",
            output_file=output_file,
            command_count=len(plan.execution_plan.commands),
            metadata={"action": plan.execution_plan.action},
        )

    def collect_command_outputs(
        self,
        *,
        device: dict[str, Any],
        execution_plan: ExecutionPlan,
    ) -> Path:
        device_name = str(device.get("device_name") or "unknown")
        timestamp = datetime.now(BEIJING_TZ).strftime("%Y%m%d%H%M%S")
        output_dir = self.output_root / execution_plan.action / timestamp / device_name
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{device_name}_{device.get('ip')}_{timestamp}.txt"

        effective_device = device_with_plan_context(device, execution_plan)
        read_timeout = _safe_int(
            dict(effective_device.get("attributes") or {}).get("read_timeout"),
            60,
        )
        connection = connect_cli_device_with_retries(
            effective_device,
            connector=self.connector,
        )
        sections: list[str] = []
        try:
            for command in execution_plan.command_texts():
                output = connection.send_command(command, read_timeout=read_timeout)
                sections.append(
                    f"Command: {command}\nOutput:\n{output}\n{'-' * 50}"
                )
        finally:
            connection.disconnect()
        output_file.write_text("\n".join(sections), encoding="utf-8")
        return output_file
