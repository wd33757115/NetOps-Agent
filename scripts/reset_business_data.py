"""Reset NetOps business data while keeping PostgreSQL schema intact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.infrastructure.db.reset import RESET_CONFIRMATION, reset_business_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete NetOps business data from PostgreSQL.")
    parser.add_argument("--execute", action="store_true", help="perform deletion; omit for dry-run preview")
    parser.add_argument(
        "--include-configuration",
        action="store_true",
        help="also clear static/configuration tables normally preserved by business reset",
    )
    args = parser.parse_args()

    result = reset_business_data(
        dry_run=not args.execute,
        confirmation=RESET_CONFIRMATION if args.execute else None,
        include_configuration=args.include_configuration,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
