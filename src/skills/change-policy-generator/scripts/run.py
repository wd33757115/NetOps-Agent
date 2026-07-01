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

from src.core.patrol.change_policy import (  # noqa: E402
    infer_change_policy,
    numeric_stats,
    policy_from_dict,
    validate_change_policy,
)


def _historical_values(params: dict[str, Any]) -> list[Any]:
    values = params.get("historical_values") or params.get("values") or []
    if not isinstance(values, list):
        raise ValueError("historical_values must be a list")
    return values


def run_change_policy_generation(params: dict[str, Any]) -> dict[str, Any]:
    mode = str(params.get("mode") or "infer_and_validate").strip().lower()
    values = _historical_values(params)
    proposal_payload = params.get("proposal") or params.get("change_policy")
    if mode == "infer":
        policy = infer_change_policy(
            observation_type=str(params.get("observation_type") or params.get("field_name") or ""),
            field_name=params.get("field_name"),
            field_semantics=dict(params.get("field_semantics") or {}),
            historical_values=values,
            device_role=params.get("device_role"),
            policy_intent=params.get("policy_intent"),
        )
        return {
            "success": True,
            "mode": mode,
            "policy": policy.to_dict(),
            "historical_stats": numeric_stats(values),
        }
    if mode == "validate":
        if not isinstance(proposal_payload, dict):
            raise ValueError("proposal is required in validate mode")
        policy = policy_from_dict(proposal_payload)
        validation = validate_change_policy(policy)
        return {
            "success": validation["success"],
            "mode": mode,
            "policy": policy.to_dict(),
            "validation": validation,
            "historical_stats": numeric_stats(values),
        }
    if mode == "infer_and_validate":
        policy = (
            policy_from_dict(proposal_payload)
            if isinstance(proposal_payload, dict)
            else infer_change_policy(
                observation_type=str(params.get("observation_type") or params.get("field_name") or ""),
                field_name=params.get("field_name"),
                field_semantics=dict(params.get("field_semantics") or {}),
                historical_values=values,
                device_role=params.get("device_role"),
                policy_intent=params.get("policy_intent"),
            )
        )
        validation = validate_change_policy(policy)
        return {
            "success": validation["success"],
            "mode": mode,
            "policy": policy.to_dict(),
            "validation": validation,
            "historical_stats": numeric_stats(values),
        }
    raise ValueError("mode must be infer, validate, or infer_and_validate")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and validate ChangePolicy")
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    result = run_change_policy_generation(params)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
