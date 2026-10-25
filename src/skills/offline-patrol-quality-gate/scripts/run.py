#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.offline_jobs import OfflinePatrolJobService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an offline patrol job")
    parser.add_argument("--params", required=True)
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as stream:
        params = json.load(stream)
    result = OfflinePatrolJobService().execution_quality_gate(str(params["offline_job_id"]))
    if not result.get("success"):
        result["error"] = "; ".join(
            str(item.get("message") or item.get("code")) for item in result.get("blockers") or []
        )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
