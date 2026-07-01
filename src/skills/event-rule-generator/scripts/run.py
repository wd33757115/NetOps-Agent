#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.event_rule_generation import (  # noqa: E402
    generate_and_validate_event_rules,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and validate EventRule DSL")
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    contract = params.get("contract")
    change_policy = params.get("change_policy")
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    if not isinstance(change_policy, dict):
        raise ValueError("change_policy must be an object")
    result = generate_and_validate_event_rules(
        contract=contract,
        change_policy=change_policy,
        intent=params.get("intent"),
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
