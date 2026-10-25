#!/usr/bin/env python
"""Skill entrypoint for governed attested-offline current replay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.check_items.current_authority_replay import (  # noqa: E402
    AttestedOfflineCurrentReplayService,
    AttestedOfflineReplayError,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay attested offline-current evidence"
    )
    parser.add_argument("--params", required=True)
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as stream:
        params = json.load(stream)
    try:
        result = AttestedOfflineCurrentReplayService().execute(
            execution_plan=dict(params.get("execution_plan") or {}),
            execution_plan_id=str(params.get("execution_plan_id") or ""),
            workflow_run_id=str(params.get("workflow_run_id") or ""),
        )
    except AttestedOfflineReplayError as exc:
        result = {
            "success": False,
            "failure_code": exc.code,
            "error": str(exc),
        }
    except Exception as exc:
        result = {
            "success": False,
            "failure_code": "attested_offline_replay_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
