"""Bootstrap device identity discovery without requiring an active command profile."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from src.apps.rule_governance.runtime import DeviceIdentity, DeviceIdentityResolver
from src.core.ingestion.adapters.cli import connect_cli_device, device_type_for
from src.core.patrol.command_splitter import CommandBlock, canonicalize_command

BOOTSTRAP_COMMANDS_BY_VENDOR: dict[str, list[str]] = {
    "cisco": ["terminal length 0", "show version", "show inventory"],
    "h3c": ["screen-length disable", "display version", "display device manuinfo"],
    "hp": ["screen-length disable", "display version", "display device manuinfo"],
    "huawei": ["screen-length 0 temporary", "display version", "display device manuinfo"],
}


def bootstrap_identity_commands(device: dict[str, Any]) -> list[str]:
    vendor = str(device.get("vendor") or "").strip().lower()
    platform = str(device.get("platform") or "").strip().lower()
    if vendor in BOOTSTRAP_COMMANDS_BY_VENDOR:
        return BOOTSTRAP_COMMANDS_BY_VENDOR[vendor]
    if platform in {"ios", "nxos", "cisco_ios", "cisco_nxos"}:
        return BOOTSTRAP_COMMANDS_BY_VENDOR["cisco"]
    if platform in {"comware", "hp_comware"}:
        return BOOTSTRAP_COMMANDS_BY_VENDOR["h3c"]
    if platform == "huawei":
        return BOOTSTRAP_COMMANDS_BY_VENDOR["huawei"]
    return ["show version", "show inventory"]


def identity_evidence(identity: DeviceIdentity) -> dict[str, Any]:
    return {
        "vendor": identity.vendor,
        "model": identity.model,
        "family": identity.family,
        "confidence": identity.confidence,
        "source": identity.source,
        "evidence_command": identity.evidence_command,
        "evidence_text": identity.evidence_text,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


def platform_for_identity(identity: DeviceIdentity, current: str | None) -> str | None:
    if current:
        return current
    vendor = (identity.vendor or "").lower()
    family = (identity.family or "").lower()
    if vendor == "h3c" or "comware" in family:
        return "comware"
    if vendor == "huawei":
        return "huawei"
    if vendor == "cisco":
        if "nxos" in family:
            return "nxos"
        return "ios"
    return current


@dataclass(frozen=True)
class IdentityDiscoveryResult:
    success: bool
    identity_status: str
    identity: DeviceIdentity
    commands: list[str]
    command_outputs: list[dict[str, Any]]
    elapsed_seconds: float
    failure_code: str | None = None
    error: str | None = None
    device_type: str | None = None
    prompt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "identity_status": self.identity_status,
            "identity": identity_evidence(self.identity),
            "commands": self.commands,
            "command_outputs": self.command_outputs,
            "elapsed_seconds": self.elapsed_seconds,
            "failure_code": self.failure_code,
            "error": self.error,
            "device_type": self.device_type,
            "prompt": self.prompt,
        }


def discover_device_identity(
    device: dict[str, Any],
    *,
    connector: Callable[[dict[str, Any]], Any] = connect_cli_device,
    identity_resolver: DeviceIdentityResolver | None = None,
    actor_id: str = "system",
    task_id: str | None = None,
) -> IdentityDiscoveryResult:
    """Collect minimal CLI evidence and resolve vendor/model/family.

    This is a bootstrap path for Device Management. It deliberately does not use
    CollectionPolicyResolver or command-profile activation state.
    """

    started = time.perf_counter()
    commands = bootstrap_identity_commands(device)
    connection = None
    prompt = None
    outputs: list[dict[str, Any]] = []
    try:
        connection = (
            connector(device, actor_id=actor_id, source_app="device-management", task_id=task_id)
            if connector is connect_cli_device else connector(device)
        )
        find_prompt = getattr(connection, "find_prompt", None)
        if callable(find_prompt):
            prompt = str(find_prompt() or "")
        blocks: list[CommandBlock] = []
        for command in commands:
            send_command = getattr(connection, "send_command")
            output = str(send_command(command, read_timeout=30) or "")
            outputs.append(
                {
                    "command": command,
                    "canonical_command": canonicalize_command(command),
                    "output_preview": output[:1000],
                    "output_length": len(output),
                }
            )
            blocks.append(
                CommandBlock(
                    device_name=str(device.get("device_name") or "unknown"),
                    command=command,
                    command_canonical=canonicalize_command(command),
                    raw_output=output,
                    start_line=0,
                    end_line=0,
                    prompt_style="identity_discovery",
                )
            )
        resolver = identity_resolver or DeviceIdentityResolver()
        identity = resolver.resolve(
            blocks=blocks,
            vendor=device.get("vendor"),
            model=device.get("model"),
        )
        success = identity.status == "known"
        return IdentityDiscoveryResult(
            success=success,
            identity_status=identity.status,
            identity=identity,
            commands=commands,
            command_outputs=outputs,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            failure_code=None if success else "identity_unknown",
            error=None if success else "未能从最小身份命令中可靠识别设备型号",
            device_type=device_type_for(device),
            prompt=prompt,
        )
    except Exception as exc:
        return IdentityDiscoveryResult(
            success=False,
            identity_status="unknown",
            identity=DeviceIdentity(
                vendor=device.get("vendor"),
                model=device.get("model"),
                family=device.get("family"),
                confidence=0.0,
                source="connection_failed",
            ),
            commands=commands,
            command_outputs=outputs,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            failure_code=getattr(exc, "failure_code", "connection_failed"),
            error=str(exc),
            device_type=device_type_for(device),
            prompt=prompt,
        )
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:
                pass
