"""Collector-facing PostgreSQL device inventory adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.platform.inventory.repository import DeviceRepository


class DeviceInventoryManager:
    """Keep legacy collector shapes while using PostgreSQL as authority."""

    def __init__(self, db_file: Path | None = None):
        self.legacy_db_file = db_file
        self.repository = DeviceRepository()

    def get_devices_by_filter(self, filter_params: Any) -> list[dict[str, Any]]:
        return self.repository.get_devices_by_filter(
            device_id=getattr(filter_params, "device_id", None),
            group=getattr(filter_params, "group", None),
            device_name=getattr(filter_params, "device_name", None),
            ip=getattr(filter_params, "ip", None),
            vendor=getattr(filter_params, "vendor", None),
            model=getattr(filter_params, "model", None),
            family=getattr(filter_params, "family", None),
            site=getattr(filter_params, "site", None),
            role=getattr(filter_params, "role", None),
            status=getattr(filter_params, "status", None),
        )

    def list_groups(self) -> list[str]:
        return self.repository.list_groups()

    def get_device_by_ip_or_name(self, identifier: str) -> dict[str, Any] | None:
        return self.repository.get_device_by_ip_or_name(identifier)
