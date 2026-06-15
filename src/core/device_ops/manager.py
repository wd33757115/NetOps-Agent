"""Collector-facing PostgreSQL device inventory adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.core.device_ops.repository import DeviceRepository


class DeviceInventoryManager:
    """Keep legacy collector shapes while using PostgreSQL as authority."""

    def __init__(self, db_file: Path | None = None):
        self.legacy_db_file = db_file
        self.repository = DeviceRepository()

    @staticmethod
    def _with_credential(device: dict[str, Any]) -> dict[str, Any]:
        result = dict(device)
        credential_ref = str(result.get("credential_ref") or "").strip()
        result["password"] = (
            os.getenv(credential_ref[4:])
            if credential_ref.startswith("env:")
            else None
        )
        return result

    def get_devices_by_filter(self, filter_params: Any) -> list[dict[str, Any]]:
        devices = self.repository.get_devices_by_filter(
            group=getattr(filter_params, "group", None),
            device_name=getattr(filter_params, "device_name", None),
            ip=getattr(filter_params, "ip", None),
            model=getattr(filter_params, "model", None),
        )
        return [self._with_credential(device) for device in devices]

    def list_groups(self) -> list[str]:
        return self.repository.list_groups()

    def get_device_by_ip_or_name(self, identifier: str) -> dict[str, Any] | None:
        device = self.repository.get_device_by_ip_or_name(identifier)
        return self._with_credential(device) if device else None
