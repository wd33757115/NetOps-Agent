#!/usr/bin/env python3
"""Migrate legacy NetOps SQLite business data into PostgreSQL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.db.migrations import require_database_schema_current  # noqa: E402
from src.infrastructure.db.postgres import require_postgres_connection  # noqa: E402
from src.infrastructure.db.sqlite_migration import (  # noqa: E402
    migrate_device_inventory,
    migrate_patrol_store,
)

SHARED_DATABASES = PROJECT_ROOT / "src" / "skills" / "shared" / "databases"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--devices",
        default=str(SHARED_DATABASES / "devices.db"),
    )
    parser.add_argument(
        "--production",
        default=str(SHARED_DATABASES / "patrol.db"),
    )
    parser.add_argument(
        "--validation",
        default=str(SHARED_DATABASES / "parser-validation.db"),
    )
    args = parser.parse_args()

    require_postgres_connection()
    require_database_schema_current()
    results = {
        "devices": migrate_device_inventory(args.devices, dry_run=args.dry_run),
        "production": migrate_patrol_store(
            args.production,
            mode="production",
            dry_run=args.dry_run,
        ),
        "parser_validation": migrate_patrol_store(
            args.validation,
            mode="parser_validation",
            dry_run=args.dry_run,
        ),
    }
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
