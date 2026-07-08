#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.rule_control.submission import RuleAssetSubmissionService  # noqa: E402


def run_submit(params: dict[str, Any]) -> dict[str, Any]:
    job_id = str(params.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    actor = params.get("actor") or params.get("created_by") or "rule-asset-submitter"
    return RuleAssetSubmissionService().submit_candidate(job_id, actor=str(actor))


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit generated rule asset candidate")
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    result = run_submit(params)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
