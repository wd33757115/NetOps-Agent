"""PostgreSQL network-device inventory repository."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_

from src.infrastructure.db.models import (
    NetworkDevice,
    NetworkDeviceConnectionHealth,
    NetworkDeviceDynamicGroup,
    NetworkDeviceGroup,
    NetworkDeviceGroupMember,
    NetworkDeviceTombstone,
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


def normalize_device_status(status: str | None, *, default: str = "active") -> str:
    value = (status or default).strip().lower()
    if value == "inactive":
        return "disabled"
    if value not in {"active", "disabled", "deleted"}:
        raise ValueError("device status must be active, disabled or deleted")
    return value


def sanitize_device_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    next_attributes = dict(attributes or {})
    next_attributes.pop("patrol_commands", None)
    return next_attributes


def _embedded_assessment_policy_digests(
    attributes: dict[str, Any] | None,
) -> set[str]:
    payload = dict(attributes or {})
    sources = list(
        payload.get("assessment_policy_bindings") or payload.get("assessment_policies") or []
    )
    return {
        json.dumps(
            source,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        for source in sources
    }


def reject_new_embedded_assessment_policies(
    attributes: dict[str, Any] | None,
    *,
    existing_attributes: dict[str, Any] | None,
) -> None:
    """Keep legacy values readable while preventing new mutable policy writes."""

    incoming = _embedded_assessment_policy_digests(attributes)
    existing = _embedded_assessment_policy_digests(existing_attributes)
    if not incoming.issubset(existing):
        raise ValueError(
            "embedded assessment policy writes are disabled; create a governed "
            "AssessmentPolicy draft and binding in the rule asset control plane"
        )


def _selector_matches(device: dict[str, Any], selector: dict[str, Any]) -> bool:
    if not selector:
        return True
    for key, expected in selector.items():
        if expected is None or expected == "":
            continue
        actual = device.get(key)
        if actual is None and key in {"group", "groups"}:
            actual = device.get("groups")
        if isinstance(expected, list):
            expected_values = {str(item).lower() for item in expected if item is not None}
            if isinstance(actual, list):
                actual_values = {str(item).lower() for item in actual}
                if not actual_values.intersection(expected_values):
                    return False
            elif str(actual or "").lower() not in expected_values:
                return False
        elif isinstance(actual, list):
            if str(expected).lower() not in {str(item).lower() for item in actual}:
                return False
        elif str(actual or "").lower() != str(expected).lower():
            return False
    return True


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
        platform: str | None = None,
        device_type: str | None = None,
        os_version: str | None = None,
        serial_number: str | None = None,
        site: str | None = None,
        role: str | None = None,
        status: str = "active",
        output_path: str | None = None,
        command_profile: str | None = None,
        legacy_device_id: int | None = None,
        attributes: dict[str, Any] | None = None,
        groups: list[str] | None = None,
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
            row.platform = platform
            row.device_type = device_type
            row.os_version = os_version
            row.serial_number = serial_number
            row.site = site
            row.role = role
            row.output_path = output_path
            row.command_profile = command_profile
            row.attributes_json = sanitize_device_attributes(attributes)
            row.status = normalize_device_status(status)
            row.updated_at = now
            if groups is not None:
                self._replace_device_groups(db, device_id, groups)
        return device_id

    def update_device(
        self,
        device_id: str,
        *,
        device_name: str | None = None,
        management_ip: str | None = None,
        username: str | None = None,
        credential_ref: str | None = None,
        vendor: str | None = None,
        model: str | None = None,
        family: str | None = None,
        platform: str | None = None,
        device_type: str | None = None,
        os_version: str | None = None,
        serial_number: str | None = None,
        site: str | None = None,
        role: str | None = None,
        status: str | None = None,
        output_path: str | None = None,
        command_profile: str | None = None,
        attributes: dict[str, Any] | None = None,
        groups: list[str] | None = None,
    ) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(NetworkDevice, device_id)
            reject_new_embedded_assessment_policies(
                attributes,
                existing_attributes=(dict(row.attributes_json or {}) if row is not None else None),
            )
            if row is None:
                return None
            for field, value in {
                "device_name": device_name,
                "management_ip": management_ip,
                "username": username,
                "credential_ref": credential_ref,
                "vendor": vendor,
                "model": model,
                "family": family,
                "platform": platform,
                "device_type": device_type,
                "os_version": os_version,
                "serial_number": serial_number,
                "site": site,
                "role": role,
                "status": normalize_device_status(status) if status is not None else None,
                "output_path": output_path,
                "command_profile": command_profile,
                "attributes_json": sanitize_device_attributes(attributes)
                if attributes is not None
                else None,
            }.items():
                if value is not None:
                    setattr(row, field, value)
            row.updated_at = datetime.now(timezone.utc)
            if groups is not None:
                self._replace_device_groups(db, device_id, groups)
            group_names = self._groups_by_device(db, [device_id]).get(device_id, [])
            return self._legacy_shape(row, groups=group_names)

    def set_device_status(self, device_id: str, status: str) -> dict[str, Any] | None:
        normalized = normalize_device_status(status)
        if normalized == "deleted":
            raise ValueError("use delete_device to delete a device")
        return self.update_device(device_id, status=normalized)

    def delete_device(self, device_id: str) -> dict[str, Any] | None:
        """Delete the inventory asset while preserving denormalized historical evidence."""
        with get_db_session() as db:
            row = db.get(NetworkDevice, device_id)
            if row is None:
                return None
            groups = self._groups_by_device(db, [device_id]).get(device_id, [])
            snapshot = self._legacy_shape(row, groups=groups)
            db.add(
                NetworkDeviceTombstone(
                    id=str(uuid.uuid4()),
                    device_id=device_id,
                    device_name=row.device_name,
                    management_ip=row.management_ip,
                    vendor=row.vendor,
                    model=row.model,
                    family=row.family,
                    snapshot_json=snapshot,
                    deleted_at=datetime.now(timezone.utc),
                )
            )
            db.delete(row)
            return snapshot

    def record_connection_health(
        self,
        device_id: str,
        *,
        status: str,
        failure_code: str | None = None,
        error: str | None = None,
        latency_ms: int | None = None,
        source: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(NetworkDevice, device_id)
            if row is None:
                return None
            checked_at = datetime.now(timezone.utc)
            row.last_connection_status = status
            row.last_connection_checked_at = checked_at
            row.last_connection_failure_code = failure_code
            row.last_connection_error = error
            row.last_connection_latency_ms = latency_ms
            row.updated_at = checked_at
            db.add(
                NetworkDeviceConnectionHealth(
                    id=str(uuid.uuid4()),
                    device_id=device_id,
                    status=status,
                    failure_code=failure_code,
                    error=error,
                    latency_ms=latency_ms,
                    source=source,
                    metadata_json=metadata or {},
                    checked_at=checked_at,
                )
            )
            groups = self._groups_by_device(db, [device_id]).get(device_id, [])
            return self._legacy_shape(row, groups=groups)

    def list_connection_health(
        self,
        device_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(NetworkDeviceConnectionHealth)
                .filter(NetworkDeviceConnectionHealth.device_id == device_id)
                .order_by(NetworkDeviceConnectionHealth.checked_at.desc())
                .limit(limit)
                .all()
            )
            return [self._health_shape(row) for row in rows]

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

    def set_device_groups(self, device_id: str, group_names: list[str]) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(NetworkDevice, device_id)
            if row is None:
                return None
            self._replace_device_groups(db, device_id, group_names)
            groups = self._groups_by_device(db, [device_id]).get(device_id, [])
            return self._legacy_shape(row, groups=groups)

    def list_groups(self) -> list[str]:
        with get_db_session() as db:
            return [
                row.name
                for row in db.query(NetworkDeviceGroup).order_by(NetworkDeviceGroup.name).all()
            ]

    def upsert_dynamic_group(
        self,
        *,
        name: str,
        selector: dict[str, Any],
        description: str | None = None,
        status: str = "active",
        group_id: str | None = None,
    ) -> dict[str, Any]:
        if status == "inactive":
            status = "disabled"
        if status not in {"active", "disabled"}:
            raise ValueError("dynamic group status must be active or disabled")
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("dynamic group name is required")
        if not isinstance(selector, dict) or not selector:
            raise ValueError("dynamic group selector is required")
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            row = db.get(NetworkDeviceDynamicGroup, group_id) if group_id else None
            if row is None:
                row = (
                    db.query(NetworkDeviceDynamicGroup)
                    .filter(NetworkDeviceDynamicGroup.name == normalized_name)
                    .first()
                )
            if row is None:
                row = NetworkDeviceDynamicGroup(
                    id=str(uuid.uuid4()),
                    name=normalized_name,
                    created_at=now,
                )
                db.add(row)
            row.name = normalized_name
            row.description = description
            row.selector_json = selector
            row.status = status
            row.updated_at = now
            db.flush()
            return self._dynamic_group_shape(row)

    def list_dynamic_groups(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(NetworkDeviceDynamicGroup)
            if status:
                query = query.filter(NetworkDeviceDynamicGroup.status == status)
            rows = query.order_by(NetworkDeviceDynamicGroup.name).all()
            return [self._dynamic_group_shape(row) for row in rows]

    def get_dynamic_group(self, group_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(NetworkDeviceDynamicGroup, group_id)
            return self._dynamic_group_shape(row) if row else None

    def set_dynamic_group_status(self, group_id: str, status: str) -> dict[str, Any] | None:
        if status == "inactive":
            status = "disabled"
        if status not in {"active", "disabled"}:
            raise ValueError("dynamic group status must be active or disabled")
        with get_db_session() as db:
            row = db.get(NetworkDeviceDynamicGroup, group_id)
            if row is None:
                return None
            row.status = status
            row.updated_at = datetime.now(timezone.utc)
            return self._dynamic_group_shape(row)

    def resolve_dynamic_group_devices(
        self, group_id: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        group = self.get_dynamic_group(group_id)
        if group is None or group.get("status") != "active":
            return group, []
        devices = self.get_devices_by_filter(status="active")
        selector = dict(group.get("selector") or {})
        return group, [device for device in devices if _selector_matches(device, selector)]

    def get_devices_by_filter(
        self,
        *,
        search: str | None = None,
        device_id: str | None = None,
        group: str | None = None,
        device_name: str | None = None,
        ip: str | None = None,
        vendor: str | None = None,
        model: str | None = None,
        family: str | None = None,
        platform: str | None = None,
        device_type: str | None = None,
        site: str | None = None,
        role: str | None = None,
        status: str | None = None,
        connection_status: str | None = None,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(NetworkDevice)
            if search:
                pattern = f"%{str(search).strip()}%"
                query = query.filter(
                    or_(
                        NetworkDevice.id.ilike(pattern),
                        NetworkDevice.device_name.ilike(pattern),
                        NetworkDevice.management_ip.ilike(pattern),
                        NetworkDevice.vendor.ilike(pattern),
                        NetworkDevice.model.ilike(pattern),
                    )
                )
            if device_id:
                query = query.filter(NetworkDevice.id == device_id)
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
            if vendor:
                query = query.filter(NetworkDevice.vendor.ilike(vendor))
            if model:
                query = query.filter(NetworkDevice.model.ilike(f"%{model}%"))
            if family:
                query = query.filter(NetworkDevice.family.ilike(f"%{family}%"))
            if platform:
                query = query.filter(NetworkDevice.platform.ilike(f"%{platform}%"))
            if device_type:
                query = query.filter(NetworkDevice.device_type.ilike(f"%{device_type}%"))
            if site:
                query = query.filter(NetworkDevice.site.ilike(f"%{site}%"))
            if role:
                query = query.filter(NetworkDevice.role.ilike(f"%{role}%"))
            if status == "all":
                status = None
            if status == "inactive":
                status = "disabled"
            if status:
                query = query.filter(NetworkDevice.status == status)
            if connection_status:
                query = query.filter(NetworkDevice.last_connection_status == connection_status)
            rows = query.order_by(NetworkDevice.device_name, NetworkDevice.management_ip).all()
            groups_by_device = self._groups_by_device(db, [row.id for row in rows])
            return [
                self._legacy_shape(row, groups=groups_by_device.get(row.id, [])) for row in rows
            ]

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
            if not row:
                return None
            groups_by_device = self._groups_by_device(db, [row.id])
            return self._legacy_shape(row, groups=groups_by_device.get(row.id, []))

    @staticmethod
    def _groups_by_device(db: Any, device_ids: list[str]) -> dict[str, list[str]]:
        if not device_ids:
            return {}
        rows = (
            db.query(NetworkDeviceGroupMember.device_id, NetworkDeviceGroup.name)
            .join(
                NetworkDeviceGroup,
                NetworkDeviceGroup.id == NetworkDeviceGroupMember.group_id,
            )
            .filter(NetworkDeviceGroupMember.device_id.in_(device_ids))
            .order_by(NetworkDeviceGroup.name)
            .all()
        )
        result: dict[str, list[str]] = {device_id: [] for device_id in device_ids}
        for device_id, group_name in rows:
            result.setdefault(str(device_id), []).append(str(group_name))
        return result

    def _replace_device_groups(self, db: Any, device_id: str, group_names: list[str]) -> None:
        normalized = [name.strip() for name in group_names if name and name.strip()]
        unique_names = list(dict.fromkeys(normalized))
        (
            db.query(NetworkDeviceGroupMember)
            .filter(NetworkDeviceGroupMember.device_id == device_id)
            .delete(synchronize_session=False)
        )
        for name in unique_names:
            group_id = stable_group_id(name)
            group = db.get(NetworkDeviceGroup, group_id)
            if group is None:
                group = NetworkDeviceGroup(id=group_id, name=name)
                db.add(group)
            db.add(NetworkDeviceGroupMember(group_id=group_id, device_id=device_id))

    @staticmethod
    def _legacy_shape(row: NetworkDevice, *, groups: list[str] | None = None) -> dict[str, Any]:
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
            "platform": row.platform,
            "device_type": row.device_type,
            "os_version": row.os_version,
            "serial_number": row.serial_number,
            "site": row.site,
            "role": row.role,
            "out_path": row.output_path,
            "command_file": row.command_profile,
            "status": row.status,
            "last_connection_status": row.last_connection_status,
            "last_connection_checked_at": (
                row.last_connection_checked_at.isoformat()
                if row.last_connection_checked_at
                else None
            ),
            "last_connection_failure_code": row.last_connection_failure_code,
            "last_connection_error": row.last_connection_error,
            "last_connection_latency_ms": row.last_connection_latency_ms,
            "groups": groups or [],
            "attributes": row.attributes_json or {},
        }

    @staticmethod
    def _dynamic_group_shape(row: NetworkDeviceDynamicGroup) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "selector": row.selector_json or {},
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def _health_shape(row: NetworkDeviceConnectionHealth) -> dict[str, Any]:
        return {
            "id": row.id,
            "device_id": row.device_id,
            "status": row.status,
            "failure_code": row.failure_code,
            "error": row.error,
            "latency_ms": row.latency_ms,
            "source": row.source,
            "metadata": row.metadata_json or {},
            "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        }
