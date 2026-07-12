"""Measure the warm device-readiness list query budget against PostgreSQL."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.core.device_ops.readiness_projection import (  # noqa: E402
    DeviceReadinessProjectionRepository,
    readiness_source_digest,
)
from src.gateway import devices_api  # noqa: E402
from src.infrastructure.db.models import (  # noqa: E402
    DeviceReadinessProjection,
    NetworkDevice,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402
from src.observability.read_metrics import latest_read_observation  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--max-queries", type=int, default=8)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if args.count < 1 or args.count > 500:
        raise ValueError("count must be between 1 and 500")
    prefix = f"query-budget-{uuid.uuid4().hex[:12]}-"
    revision = DeviceReadinessProjectionRepository().asset_revision()
    devices: list[dict] = []
    projections: list[DeviceReadinessProjection] = []
    now = datetime.now(timezone.utc)
    with get_db_session() as db:
        for index in range(args.count):
            device_id = f"{prefix}{index:04d}"
            ip = f"198.18.{index // 250}.{index % 250 + 1}"
            device = {
                "device_id": device_id,
                "device_name": f"QB-{prefix[-13:-1]}-{index:04d}",
                "ip": ip,
                "management_ip": ip,
                "vendor": "Cisco",
                "model": "C2960",
                "family": "IOS-C2960",
                "status": "active",
                "credential_ref": "env:QUERY_BUDGET",
                "attributes": {},
            }
            digest = readiness_source_digest(
                device,
                mode="production",
                asset_revision=revision,
            )
            db.add(
                NetworkDevice(
                    id=device_id,
                    device_name=device["device_name"],
                    management_ip=ip,
                    vendor="Cisco",
                    model="C2960",
                    family="IOS-C2960",
                    status="active",
                    credential_ref="env:QUERY_BUDGET",
                    attributes_json={},
                )
            )
            projections.append(
                DeviceReadinessProjection(
                    device_id=device_id,
                    mode="production",
                    status="ready",
                    source_digest=digest,
                    readiness_json={"status": "ready", "source_digest": digest},
                    computed_at=now,
                    updated_at=now,
                )
            )
            devices.append(device)
        db.flush()
        db.add_all(projections)

    class BudgetDeviceRepository:
        def get_devices_by_filter(self, **_kwargs):
            return devices

    original_repository = devices_api.DeviceRepository
    try:
        devices_api.DeviceRepository = BudgetDeviceRepository
        result = devices_api.list_devices(
            include_patrol_readiness=True,
            limit=args.count,
            offset=0,
        )
        observation = latest_read_observation("device_list", "with_readiness") or {}
        report = {"rows": len(result["items"]), **observation}
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if int(observation.get("query_count") or 0) <= args.max_queries else 1
    finally:
        devices_api.DeviceRepository = original_repository
        with get_db_session() as db:
            db.query(NetworkDevice).filter(NetworkDevice.id.like(f"{prefix}%")).delete(
                synchronize_session=False
            )


if __name__ == "__main__":
    raise SystemExit(main())
