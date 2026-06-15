#!/usr/bin/env python3
"""Upgrade or verify the authoritative PostgreSQL application schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.db.migrations import (  # noqa: E402
    adopt_legacy_schema,
    current_revision,
    head_revision,
    require_database_schema_current,
    upgrade_database,
)
from src.infrastructure.db.postgres import require_postgres_connection  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="stamp a pre-Alembic platform schema before applying newer revisions",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify that PostgreSQL is already at the migration head",
    )
    parser.add_argument("--revision", default="head")
    args = parser.parse_args()

    require_postgres_connection()
    if args.check:
        require_database_schema_current()
        print(f"[OK] PostgreSQL schema is current: {head_revision()}")
        return 0

    if args.adopt_existing and adopt_legacy_schema():
        print("[OK] Adopted existing platform schema at Alembic baseline")
    upgrade_database(args.revision)
    print(
        "[OK] PostgreSQL migration complete: "
        f"current={current_revision()}, head={head_revision()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
