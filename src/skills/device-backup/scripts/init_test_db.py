# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Seed PostgreSQL with non-production device inventory examples."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.device_ops.repository import DeviceRepository  # noqa: E402
from src.infrastructure.db.migrations import require_database_schema_current  # noqa: E402
from src.infrastructure.db.postgres import require_postgres_connection  # noqa: E402


def main() -> int:
    require_postgres_connection()
    require_database_schema_current()
    repository = DeviceRepository()

    groups = ["production", "validation", "dmz"]
    group_ids = {name: repository.ensure_group(name) for name in groups}
    devices = [
        ("core-sw-1", "192.0.2.11", "admin", "H3C", "S5500", "backup/core"),
        ("core-sw-2", "192.0.2.12", "admin", "H3C", "S5500", "backup/core"),
        ("access-sw-1", "192.0.2.21", "admin", "Huawei", "S5700", "backup/access"),
    ]
    device_ids = []
    for name, ip, username, vendor, model, output_path in devices:
        device_ids.append(
            repository.upsert_device(
                device_name=name,
                management_ip=ip,
                username=username,
                credential_ref=f"env:NETOPS_PASSWORD_{name.upper().replace('-', '_')}",
                vendor=vendor,
                model=model,
                output_path=output_path,
                attributes={"seed_data": True},
            )
        )
    for device_id in device_ids:
        repository.add_device_to_group(device_id, group_ids["validation"])

    print(
        f"PostgreSQL inventory seeded: {len(groups)} groups, "
        f"{len(devices)} devices"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
