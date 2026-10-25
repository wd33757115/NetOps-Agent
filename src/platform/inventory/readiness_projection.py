"""Persistence for the rebuildable device readiness read model."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func

from src.infrastructure.db.models import (
    ChangePolicyVersionRecord,
    CollectionProfileBindingRecord,
    CommandProfileVersionRecord,
    DeviceReadinessProjection,
    EventRuleVersionRecord,
    ParserTemplate,
)
from src.infrastructure.db.postgres import get_db_session


class DeviceReadinessProjectionRepository:
    def list_for_devices(
        self,
        device_ids: list[str],
        *,
        mode: str,
    ) -> dict[str, dict[str, Any]]:
        if not device_ids:
            return {}
        with get_db_session() as db:
            rows = (
                db.query(DeviceReadinessProjection)
                .filter(
                    DeviceReadinessProjection.device_id.in_(device_ids),
                    DeviceReadinessProjection.mode == mode,
                )
                .all()
            )
            return {
                row.device_id: {
                    **dict(row.readiness_json or {}),
                    "projection_computed_at": row.computed_at.isoformat(),
                }
                for row in rows
            }

    def upsert(
        self,
        *,
        device_id: str,
        mode: str,
        source_digest: str,
        readiness: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        payload = {**readiness, "source_digest": source_digest}
        with get_db_session() as db:
            row = db.get(DeviceReadinessProjection, (device_id, mode))
            if row is None:
                row = DeviceReadinessProjection(device_id=device_id, mode=mode)
                db.add(row)
            row.status = str(readiness.get("status") or "unknown")
            row.source_digest = source_digest
            row.readiness_json = payload
            row.computed_at = now
            row.updated_at = now

    def asset_revision(self) -> str:
        models = (
            ParserTemplate,
            ChangePolicyVersionRecord,
            EventRuleVersionRecord,
            CommandProfileVersionRecord,
            CollectionProfileBindingRecord,
        )
        values: list[str] = []
        with get_db_session() as db:
            for model in models:
                timestamp_column = getattr(model, "updated_at", None)
                if timestamp_column is None:
                    timestamp_column = getattr(model, "created_at")
                value = db.query(func.max(timestamp_column)).scalar()
                values.append(value.isoformat() if value else "-")
        return "|".join(values)


def readiness_source_digest(
    device: dict[str, Any],
    *,
    mode: str,
    asset_revision: str,
) -> str:
    payload = {
        "device_id": device.get("device_id") or device.get("id"),
        "mode": mode,
        "vendor": device.get("vendor"),
        "model": device.get("model"),
        "family": device.get("family"),
        "status": device.get("status"),
        "credential_ref": device.get("credential_ref"),
        "command_profile": device.get("command_profile") or device.get("command_file"),
        "attributes": device.get("attributes") or device.get("attributes_json") or {},
        "asset_revision": asset_revision,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
