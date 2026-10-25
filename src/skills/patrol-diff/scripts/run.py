#!/usr/bin/env python
"""Skill entrypoint for patrol-diff."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.reporting import build_human_diff  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a human-facing patrol diff")
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    if params.get("current_run_id_only") and not params.get("current_run_id"):
        params["current_run_id"] = params["current_run_id_only"]
    result = build_human_diff(params)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
