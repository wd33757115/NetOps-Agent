"""Audit or hard-delete duplicate TextFSM generation tasks.

Run with workers stopped. The command never merges samples or generation
attempts from deleted jobs into the retained asset task.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.parser.repository import ParserJobRepository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit or purge duplicate Parser template generation tasks."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Hard-delete duplicate history. The default is a read-only preview.",
    )
    parser.add_argument(
        "--expect-job-count",
        type=int,
        help="Refuse --execute unless the preflight task count matches this value.",
    )
    parser.add_argument(
        "--expect-duplicate-groups",
        type=int,
        help="Refuse --execute unless the preflight duplicate-group count matches this value.",
    )
    args = parser.parse_args()
    repository = ParserJobRepository()
    preflight = repository.purge_duplicate_asset_jobs(dry_run=True)
    mismatches: list[str] = []
    if (
        args.expect_job_count is not None
        and preflight["job_count_before"] != args.expect_job_count
    ):
        mismatches.append(
            "job count mismatch: "
            f"expected {args.expect_job_count}, got {preflight['job_count_before']}"
        )
    if (
        args.expect_duplicate_groups is not None
        and preflight["duplicate_group_count"] != args.expect_duplicate_groups
    ):
        mismatches.append(
            "duplicate group count mismatch: "
            f"expected {args.expect_duplicate_groups}, "
            f"got {preflight['duplicate_group_count']}"
        )
    if args.execute and mismatches:
        result = {**preflight, "executed": False, "errors": mismatches}
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 2
    result = (
        repository.purge_duplicate_asset_jobs(dry_run=False)
        if args.execute
        else preflight
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
