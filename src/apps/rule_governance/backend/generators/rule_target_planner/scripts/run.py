#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[7]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.apps.rule_governance.backend.rule_control.planning import (
    compile_rule_targets,  # noqa: E402
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile rule generation targets")
    parser.add_argument("--params", required=True)
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as stream:
        params = json.load(stream)
    result = compile_rule_targets(
        contract=dict(params.get("contract") or {}),
        records=list(params.get("records") or []),
        record_groups=list(params.get("record_groups") or []) or None,
        rule_asset_requirements=dict(params.get("rule_asset_requirements") or {}),
    )
    if not result["success"]:
        result["error"] = "; ".join(
            str(item.get("message") or item.get("code")) for item in result.get("blockers") or []
        )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
