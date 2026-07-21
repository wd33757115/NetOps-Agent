"""Collection source adapters (CLI first; other sources stubbed)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CollectionArtifact:
    source: str
    command_or_path: str
    raw_text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseCollectionAdapter(ABC):
    source: str = "unknown"

    @abstractmethod
    def collect(self, device: dict[str, Any], *, commands: list[str]) -> list[CollectionArtifact]:
        raise NotImplementedError


class CLICollectionAdapter(BaseCollectionAdapter):
    """Wraps existing PatrolTool / netmiko collection path."""

    source = "cli"

    def collect(self, device: dict[str, Any], *, commands: list[str]) -> list[CollectionArtifact]:
        from src.core.device_ops.tools import PatrolTool
        from src.core.device_ops.manager import DeviceInventoryManager
        import asyncio

        tool = PatrolTool(DeviceInventoryManager())
        # Reuse coverage-aware patrol_device for a single device; commands come from plan.
        result = asyncio.run(tool.patrol_device(device, mode="production"))
        output = str(result.get("output_file") or "")
        raw = ""
        if output:
            from pathlib import Path

            raw = Path(output).read_text(encoding="utf-8", errors="replace")
        return [
            CollectionArtifact(
                source=self.source,
                command_or_path=output or "inline",
                raw_text=raw,
                metadata={
                    "device_id": device.get("device_id"),
                    "commands": list(commands or []),
                    "success": bool(result.get("success")),
                },
            )
        ]


class GnmiCollectionAdapter(BaseCollectionAdapter):
    source = "gnmi"

    def collect(self, device: dict[str, Any], *, commands: list[str]) -> list[CollectionArtifact]:
        raise NotImplementedError("gNMI adapter is not implemented yet")


class SnmpCollectionAdapter(BaseCollectionAdapter):
    source = "snmp"

    def collect(self, device: dict[str, Any], *, commands: list[str]) -> list[CollectionArtifact]:
        raise NotImplementedError("SNMP adapter is not implemented yet")


class SyslogCollectionAdapter(BaseCollectionAdapter):
    source = "syslog"

    def collect(self, device: dict[str, Any], *, commands: list[str]) -> list[CollectionArtifact]:
        raise NotImplementedError("Syslog adapter is not implemented yet")


ADAPTER_REGISTRY: dict[str, type[BaseCollectionAdapter]] = {
    "cli": CLICollectionAdapter,
    "gnmi": GnmiCollectionAdapter,
    "snmp": SnmpCollectionAdapter,
    "syslog": SyslogCollectionAdapter,
}


def get_adapter(source: str = "cli") -> BaseCollectionAdapter:
    key = str(source or "cli").strip().lower()
    cls = ADAPTER_REGISTRY.get(key)
    if cls is None:
        raise KeyError(f"unknown collection adapter: {source}")
    return cls()
