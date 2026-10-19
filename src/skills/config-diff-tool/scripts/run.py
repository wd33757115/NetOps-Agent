#!/usr/bin/env python3
"""config-diff-tool entry script for Skill executor / Celery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from diff_config import diff_config  # noqa: E402


def run_config_diff(payload: dict) -> dict:
    config1 = str(payload.get("config1") or payload.get("before") or "")
    config2 = str(payload.get("config2") or payload.get("after") or "")
    if not config1 or not config2:
        return {
            "success": False,
            "error": "config1 and config2 are required",
            "message": "缺少对比配置内容",
        }
    result = diff_config(config1, config2)
    return {
        "success": True,
        "message": "配置对比完成",
        "data": result,
        "handling": "raw_hash_diff",
        "device_type": payload.get("device_type"),
    }


def main() -> int:
    # The platform calls entry scripts with --params <file>; stdin stays for running it by hand.
    parser = argparse.ArgumentParser()
    parser.add_argument("--params")
    args, _ = parser.parse_known_args()
    text = Path(args.params).read_text(encoding="utf-8") if args.params else sys.stdin.read()
    payload = json.loads(text or "{}")
    result = run_config_diff(payload)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
