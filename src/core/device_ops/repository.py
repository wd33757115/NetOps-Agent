"""PostgreSQL network-device inventory repository."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.infrastructure.db.models import (
    NetworkDevice,
    NetworkDeviceGroup,
    NetworkDeviceGroupMember,
)
from src.infrastructure.db.postgres import get_db_session

DEVICE_NAMESPACE = uuid.UUID("6173c313-e87d-4af6-93cb-3145fc5c0f07")
GROUP_NAMESPACE = uuid.UUID("ba08bf74-9dd0-43da-b00c-06dd419948e7")


def stable_device_id(device_name: str, management_ip: str) -> str:
    return str(
        uuid.uuid5(
            DEVICE_NAMESPACE,
            f"{device_name.strip().lower()}|{management_ip.strip().lower()}",
        )
    )


def stable_group_id(name: str) -> str:
    return str(uuid.uuid5(GROUP_NAMESPACE, name.strip().lower()))


class DeviceRepository:
    def upsert_device(
        self,
        *,
        device_name: str,
        management_ip: str,
        username: str | None = None,
        credential_ref: str | None = None,
        vendor: str | None = None,
        model: str | None = None,
        family: str | None = None,
        output_path: str | None = None,
        command_profile: str | None = None,
        legacy_device_id: int | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        device_id = stable_device_id(device_name, management_ip)
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            row = db.get(NetworkDevice, device_id)
            if row is None:
                row = NetworkDevice(
                    id=device_id,
                    device_name=device_name,
                    management_ip=management_ip,
                    created_at=now,
                )
                db.add(row)
            row.legacy_device_id = legacy_device_id
            row.username = username
            row.credential_ref = credential_ref
            row.vendor = vendor
            row.model = model
            row.family = family
            row.output_path = output_path
            row.command_profile = command_profile
            row.attributes_json = attributes or {}
            row.status = "active"
            row.updated_at = now
        return device_id

    def ensure_group(self, name: str, description: str | None = None) -> str:
        group_id = stable_group_id(name)
        with get_db_session() as db:
            row = db.get(NetworkDeviceGroup, group_id)
            if row is None:
                row = NetworkDeviceGroup(id=group_id, name=name, description=description)
                db.add(row)
            else:
                row.name = name
                row.description = description or row.description
                row.updated_at = datetime.now(timezone.utc)
        return group_id

    def add_device_to_group(self, device_id: str, group_id: str) -> bool:
        with get_db_session() as db:
            row = db.get(
                NetworkDeviceGroupMember,
                {"group_id": group_id, "device_id": device_id},
            )
            if row is not None:
                return False
            db.add(NetworkDeviceGroupMember(group_id=group_id, device_id=device_id))
            return True

    def list_groups(self) -> list[str]:
        with get_db_session() as db:
            return [
                row.name
                for row in db.query(NetworkDeviceGroup)
                .order_by(NetworkDeviceGroup.name)
                .all()
            ]

    def get_devices_by_filter(
        self,
        *,
        group: str | None = None,
        device_name: str | None = None,
        ip: str | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(NetworkDevice)
            if group:
                query = (
                    query.join(
                        NetworkDeviceGroupMember,
                        NetworkDeviceGroupMember.device_id == NetworkDevice.id,
                    )
                    .join(
                        NetworkDeviceGroup,
                        NetworkDeviceGroup.id == NetworkDeviceGroupMember.group_id,
                    )
                    .filter(NetworkDeviceGroup.name == group)
                )
            if device_name:
                query = query.filter(NetworkDevice.device_name.ilike(f"%{device_name}%"))
            if ip:
                query = query.filter(NetworkDevice.management_ip == ip)
            if model:
                query = query.filter(NetworkDevice.model.ilike(f"%{model}%"))
            return [self._legacy_shape(row) for row in query.all()]

    def get_device_by_ip_or_name(self, identifier: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = (
                db.query(NetworkDevice)
                .filter(
                    (NetworkDevice.management_ip == identifier)
                    | (NetworkDevice.device_name == identifier)
                    | NetworkDevice.device_name.ilike(f"%{identifier}%")
                )
                .first()
            )
            return self._legacy_shape(row) if row else None

    @staticmethod
    def _legacy_shape(row: NetworkDevice) -> dict[str, Any]:
        """Keep collector call sites stable while inventory storage moves to PostgreSQL."""
        return {
            "device_id": row.id,
            "device_name": row.device_name,
            "ip": row.management_ip,
            "username": row.username,
            "password": None,
            "credential_ref": row.credential_ref,
            "vendor": row.vendor,
            "model": row.model,
            "family": row.family,
            "out_path": row.output_path,
            "command_file": row.command_profile,
            "status": row.status,
            "attributes": row.attributes_json or {},
        }
