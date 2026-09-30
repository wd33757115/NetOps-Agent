"""CLI ingestion adapter.

This module owns transport-level CLI collection. Device Management and
device-patrol can decide *what* to collect, but the connection and raw output
capture path lives here so future collectors can share a consistent boundary.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.core.collection import ExecutionPlan
from src.platform.inventory.credentials import CredentialResolver
from src.core.ingestion.adapter import IngestionAdapter
from src.core.ingestion.domain import IngestionResult

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))
OUTPUT_ROOT = Path(__file__).resolve().parents[3] / "skills" / "shared" / "outputs"


class CollectionProgressFencedError(RuntimeError):
    """Raised when a stale collector is no longer allowed to persist progress."""


def command_collection_manifest_path(output_file: str | Path) -> Path:
    path = Path(output_file)
    return path.with_suffix(path.suffix + ".collection.json")


def load_command_collection_manifest(output_file: str | Path) -> dict[str, Any]:
    path = command_collection_manifest_path(output_file)
    if not path.is_file():
        return {"status": "unknown", "commands": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"status": "unknown", "commands": []}
    except (OSError, ValueError):
        return {"status": "unknown", "commands": []}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


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


_UNSUPPORTED_OUTPUT_MARKERS = (
    "% invalid input",
    "invalid command",
    "unknown command",
    "unrecognized command",
    "incomplete command",
    "ambiguous command",
    "command not found",
    "not supported",
    "unsupported command",
    "无效命令",
    "未知命令",
    "不支持该命令",
)


def _classify_cli_output(output: str) -> tuple[bool, str]:
    """Classify only transport usability; semantic validity is downstream."""

    normalized = str(output or "").strip()
    if not normalized:
        return False, "empty_output"
    folded = normalized.casefold()
    if any(marker in folded for marker in _UNSUPPORTED_OUTPUT_MARKERS):
        return False, "command_unsupported"
    return True, "complete"


def device_type_for(device: dict[str, Any]) -> str:
    attributes = dict(device.get("attributes") or {})
    explicit = str(
        attributes.get("netmiko_device_type") or attributes.get("device_type") or ""
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
    actor_id: str = "system",
    source_app: str = "legacy-cli",
    task_id: str | None = None,
):
    from src.platform.device_access.governance import SSHOperation

    from .guarded_cli import connect

    resolver = credential_resolver or CredentialResolver()
    password = device.get("password") or resolver.resolve_password(device.get("credential_ref"))
    if not password:
        raise ValueError(
            "device credential is unavailable; configure credential_ref as env:VARIABLE"
        )
    attributes = dict(device.get("attributes") or {})
    host = device.get("ip") or device.get("management_ip")
    if not host:
        raise ValueError("device management IP is required")
    port = _safe_int(attributes.get("port"), 22)
    operation = SSHOperation(
        actor_id=str(actor_id or "system"),
        source_app=source_app,
        target_ref=str(device.get("device_id") or host),
        host=str(host),
        port=port,
        task_id=task_id,
        secrets=(password,),
        device_name=device.get("device_name"),
    )
    return connect(
        operation,
        device_type=device_type_for(device),
        host=host,
        username=device.get("username") or "admin",
        password=password,
        port=port,
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
    actor_id: str = "system",
    source_app: str = "legacy-cli",
    task_id: str | None = None,
) -> Any:
    """Connect to a CLI device with a small retry window for virtual-lab SSH flaps."""
    attributes = dict(device.get("attributes") or {})
    attempts = max(1, _safe_int(attributes.get("connection_attempts"), 3))
    delay = max(0.0, _safe_float(attributes.get("connection_retry_delay"), 1.0))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            if connector is connect_cli_device:
                return connector(device, actor_id=actor_id, source_app=source_app, task_id=task_id)
            return connector(device)
        except Exception as exc:
            last_exc = exc
            if getattr(exc, "failure_code", None) in {
                "policy_denied", "audit_unavailable", "policy_unavailable", "invalid_request",
                "policy_match_failed", "invalid_command",
            }:
                raise
            if attempt >= attempts:
                break
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def test_cli_connectivity(
    device: dict[str, Any],
    *,
    connector: Callable[[dict[str, Any]], Any] = connect_cli_device,
    actor_id: str = "system",
    source_app: str = "device-management",
    task_id: str | None = None,
) -> dict[str, Any]:
    """Open and close a real device session using the production CLI path."""
    started = time.perf_counter()
    connection = None
    try:
        connection = connect_cli_device_with_retries(
            device, connector=connector, actor_id=actor_id, source_app=source_app, task_id=task_id
        )
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
            "failure_code": getattr(exc, "failure_code", "connection_failed"),
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
        progress_callback: Callable[..., Any] | None = None,
        continuation_guard: Callable[[], Any] | None = None,
        actor_id: str = "system",
        source_app: str = "legacy-cli",
        task_id: str | None = None,
    ) -> None:
        self.output_root = output_root
        self.connector = connector
        self.progress_callback = progress_callback
        self.continuation_guard = continuation_guard
        self.actor_id, self.source_app, self.task_id = actor_id, source_app, task_id

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
        timestamp = datetime.now(BEIJING_TZ).strftime("%Y%m%d%H%M%S_%f")
        capture_id = uuid.uuid4().hex
        output_dir = (
            self.output_root / execution_plan.action / timestamp / device_name / capture_id
        )
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
            actor_id=self.actor_id,
            source_app=self.source_app,
            task_id=self.task_id,
        )
        collection_items = list(execution_plan.commands)
        commands = execution_plan.command_texts()
        check_item_mode = any(
            item.check_item_bindings for item in collection_items
        )
        required_binding_keys = {
            (
                str(binding.get("check_item_version_id") or ""),
                str(binding.get("implementation_version_id") or ""),
            )
            for item in collection_items
            for binding in item.check_item_bindings
            if binding.get("check_item_version_id")
            and binding.get("implementation_version_id")
        }
        satisfied_binding_keys: set[tuple[str, str]] = set()
        primary_action_by_binding: dict[tuple[str, str], int] = {}
        for ordinal, candidate in enumerate(collection_items):
            for binding in candidate.check_item_bindings:
                key = (
                    str(binding.get("check_item_version_id") or ""),
                    str(binding.get("implementation_version_id") or ""),
                )
                if not all(key):
                    continue
                current = primary_action_by_binding.get(key)
                if current is None or (
                    int(candidate.fallback_rank), ordinal
                ) < (
                    int(collection_items[current].fallback_rank), current
                ):
                    primary_action_by_binding[key] = ordinal
        command_results: list[dict[str, Any]] = []
        output_file.write_text("", encoding="utf-8")
        started_at = datetime.now(timezone.utc).isoformat()

        def write_manifest(status: str, *, completed: bool = False) -> dict[str, Any]:
            payload = {
                "status": status,
                "device_id": execution_plan.device_id,
                "started_at": started_at,
                "completed_at": (datetime.now(timezone.utc).isoformat() if completed else None),
                "commands": list(command_results),
                "command_selection": {
                    "policy": "primary_only",
                    "unsupported_action": "log_only",
                },
                "check_item_fallback": {
                    "enabled": False,
                    "required_binding_count": len(required_binding_keys),
                    "satisfied_binding_count": len(
                        satisfied_binding_keys
                    ),
                    "satisfied_bindings": [
                        {
                            "check_item_version_id": check_item_version_id,
                            "implementation_version_id": implementation_version_id,
                        }
                        for (
                            check_item_version_id,
                            implementation_version_id,
                        ) in sorted(satisfied_binding_keys)
                    ],
                },
            }
            _atomic_write_json(command_collection_manifest_path(output_file), payload)
            return payload

        write_manifest("running")
        try:
            with output_file.open("a", encoding="utf-8", newline="\n") as report:
                for index, item in enumerate(collection_items):
                    if self.continuation_guard is not None:
                        self.continuation_guard()
                    command = item.command
                    execution_item_id = item.collection_item_id or hashlib.sha256(
                        (
                            f"collection-item-v1|{execution_plan.device_id}|"
                            f"{index}|{command}"
                        ).encode()
                    ).hexdigest()
                    observed_at = datetime.now(timezone.utc).isoformat()
                    binding_by_key = {
                        (
                            str(
                                binding.get("check_item_version_id")
                                or ""
                            ),
                            str(
                                binding.get(
                                    "implementation_version_id"
                                )
                                or ""
                            ),
                        ): dict(binding)
                        for binding in item.check_item_bindings
                        if binding.get("check_item_version_id")
                        and binding.get("implementation_version_id")
                    }
                    pending_binding_keys = set(binding_by_key) - (
                        satisfied_binding_keys
                    )
                    selected_binding_keys = {
                        key
                        for key in binding_by_key
                        if primary_action_by_binding.get(key) == index
                    }
                    ignored_binding_keys = set(binding_by_key) - selected_binding_keys
                    if binding_by_key and not selected_binding_keys:
                        logger.warning(
                            "legacy fallback command ignored; only the primary command is executed",
                            extra={
                                "event": "collection_legacy_fallback_ignored",
                                "device_id": execution_plan.device_id,
                                "command": command,
                                "fallback_rank": item.fallback_rank,
                                "binding_count": len(binding_by_key),
                            },
                        )
                        command_results.append(
                            {
                                "command": command,
                                "execution_item_id": execution_item_id,
                                "collection_item_id": item.collection_item_id,
                                "command_ordinal": index,
                                "status": "ignored_legacy_fallback",
                                "error": "fallback commands are disabled",
                                "observed_at": None,
                                "fallback_rank": item.fallback_rank,
                                "check_item_bindings": list(
                                    binding_by_key.values()
                                ),
                                "selected_for_bindings": [],
                            }
                        )
                        write_manifest("running")
                        continue
                    pending_binding_keys &= selected_binding_keys
                    try:
                        output = connection.send_command(command, read_timeout=read_timeout)
                    except Exception as exc:
                        failure = getattr(exc, "failure_code", None) or (
                            "timeout" if "timeout" in str(exc).lower() else "failed"
                        )
                        if binding_by_key:
                            logger.warning(
                                "primary collection command execution failed",
                                extra={
                                    "event": "collection_primary_command_unavailable",
                                    "device_id": execution_plan.device_id,
                                    "command": command,
                                    "reason": failure,
                                    "error": str(exc),
                                    "check_item_bindings": list(
                                        binding_by_key.values()
                                    ),
                                },
                            )
                        command_results.append(
                            {
                                "command": command,
                                "execution_item_id": execution_item_id,
                                "collection_item_id": item.collection_item_id,
                                "command_ordinal": index,
                                "status": failure,
                                "error": str(exc),
                                "observed_at": observed_at,
                                "fallback_rank": item.fallback_rank,
                                "check_item_bindings": list(
                                    binding_by_key.values()
                                ),
                                "selected_for_bindings": [],
                            }
                        )
                        if check_item_mode and failure not in {
                            "policy_denied", "audit_unavailable", "policy_unavailable",
                            "invalid_request", "output_limit", "policy_match_failed", "invalid_command",
                        }:
                            write_manifest("running")
                            continue
                        command_results.extend(
                            {
                                "command": pending_item.command,
                                "execution_item_id": pending_item.collection_item_id
                                or hashlib.sha256(
                                    (
                                        f"collection-item-v1|{execution_plan.device_id}|"
                                        f"{pending_index}|{pending_item.command}"
                                ).encode()
                                ).hexdigest(),
                                "collection_item_id": pending_item.collection_item_id,
                                "command_ordinal": pending_index,
                                "status": "not_executed",
                                "error": "previous command failed",
                                "observed_at": None,
                                "fallback_rank": pending_item.fallback_rank,
                                "check_item_bindings": [
                                    dict(binding)
                                    for binding in (
                                        pending_item.check_item_bindings
                                    )
                                ],
                                "selected_for_bindings": [],
                            }
                            for pending_index, pending_item in enumerate(
                                collection_items[index + 1 :],
                                start=index + 1,
                            )
                        )
                        break
                    output_text = str(output or "")
                    usable, unusable_reason = _classify_cli_output(
                        output_text
                    )
                    report.write(
                        f"Command: {command}\nOutput:\n"
                        f"{output_text}\n{'-' * 50}\n"
                    )
                    report.flush()
                    os.fsync(report.fileno())
                    selected_keys = (
                        pending_binding_keys if usable else set()
                    )
                    if not usable and binding_by_key:
                        logger.warning(
                            "primary collection command did not produce usable output",
                            extra={
                                "event": "collection_primary_command_unavailable",
                                "device_id": execution_plan.device_id,
                                "command": command,
                                "reason": unusable_reason,
                                "check_item_bindings": list(
                                    binding_by_key.values()
                                ),
                            },
                        )
                    satisfied_binding_keys.update(selected_keys)
                    command_results.append(
                        {
                            "command": command,
                            "execution_item_id": execution_item_id,
                            "collection_item_id": item.collection_item_id,
                            "command_ordinal": index,
                            "status": (
                                "complete"
                                if usable
                                else unusable_reason
                            ),
                            "error": (
                                None
                                if usable
                                else f"CLI output is {unusable_reason}"
                            ),
                            "observed_at": observed_at,
                            "fallback_rank": item.fallback_rank,
                            "check_item_bindings": list(
                                binding_by_key.values()
                            ),
                            "selected_for_bindings": [
                                binding_by_key[key]
                                for key in sorted(selected_keys)
                            ],
                            "ignored_fallback_bindings": [
                                binding_by_key[key]
                                for key in sorted(ignored_binding_keys)
                            ],
                        }
                    )
                    manifest = write_manifest("running")
                    if self.progress_callback is not None:
                        if self.continuation_guard is not None:
                            self.continuation_guard()
                        try:
                            self.progress_callback(
                                output_file=output_file,
                                manifest=manifest,
                                device=device,
                                execution_plan=execution_plan,
                            )
                        except CollectionProgressFencedError:
                            raise
                        except Exception as exc:
                            command_results[-1]["persistence_status"] = "failed"
                            command_results[-1]["persistence_error"] = str(exc)
                            logger.exception(
                                "command progress persistence failed; collection continues",
                                extra={
                                    "device_id": execution_plan.device_id,
                                    "command": command,
                                },
                            )
                        else:
                            command_results[-1]["persistence_status"] = "persisted"
                            command_results[-1]["persistence_error"] = None
                        write_manifest("running")
        finally:
            try:
                connection.disconnect()
            except Exception:
                pass
            completed = sum(
                item["status"] == "complete"
                for item in command_results
            )
            if check_item_mode:
                status = (
                    "complete"
                    if required_binding_keys <= satisfied_binding_keys
                    else "partial"
                    if satisfied_binding_keys
                    else "failed"
                )
            else:
                status = (
                    "complete"
                    if completed == len(commands)
                    else "partial"
                    if completed > 0
                    else "failed"
                )
            write_manifest(status, completed=True)
        return output_file
