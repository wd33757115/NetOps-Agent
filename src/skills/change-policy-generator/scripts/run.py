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


def _agent_loop(
    *,
    mode: str,
    result: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    policy = result.get("policy") if isinstance(result.get("policy"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    stats = result.get("historical_stats") if isinstance(result.get("historical_stats"), dict) else {}
    errors = [str(item) for item in validation.get("errors") or []]
    success = result.get("success") is True
    revision_request = None
    if not success:
        revision_request = {
            "target_generator": "change-policy-generator",
            "reason": errors or ["change policy validation failed"],
            "required_fixes": [
                "keep compare_mode compatible with semantic_type",
                "avoid threshold policies for counter fields",
                "set recovery_threshold below high threshold for gauges",
                "derive thresholds from historical_stats instead of inventing values",
            ],
            "next_params_patch": {
                "mode": "infer_and_validate",
                "observation_type": params.get("observation_type") or params.get("field_name"),
                "field_name": params.get("field_name"),
                "field_semantics": params.get("field_semantics") or {},
                "agent_loop": True,
            },
        }
    return {
        "ready_for_approval": success,
        "next_action": "submit_for_approval" if success else "revise_change_policy",
        "revision_request": revision_request,
        "attempts": [
            {
                "attempt": 1,
                "stage": "change_policy_generation",
                "generator": "change-policy-generator",
                "mode": mode,
                "success": success,
                "input_summary": {
                    "observation_type": params.get("observation_type") or params.get("field_name"),
                    "field_name": params.get("field_name"),
                    "semantic_type": dict(params.get("field_semantics") or {}).get("semantic_type"),
                    "historical_count": stats.get("count", 0),
                    "numeric_count": stats.get("numeric_count", 0),
                },
                "output_summary": {
                    "observation_type": policy.get("observation_type"),
                    "semantic_type": policy.get("semantic_type"),
                    "compare_mode": policy.get("compare_mode"),
                    "thresholds": policy.get("thresholds") or {},
                    "recovery_threshold": policy.get("recovery_threshold"),
                    "deadband": policy.get("deadband"),
                },
                "validator_feedback": {
                    "errors": errors,
                    "stats": {
                        "min": stats.get("min"),
                        "max": stats.get("max"),
                        "p95": stats.get("p95"),
                        "p99": stats.get("p99"),
                    },
                },
                "revision_request": revision_request,
            }
        ],
    }


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
        result = {
            "success": True,
            "mode": mode,
            "policy": policy.to_dict(),
            "historical_stats": numeric_stats(values),
        }
        if params.get("agent_loop"):
            result["agent_loop"] = _agent_loop(mode=mode, result=result, params=params)
        return result
    if mode == "validate":
        if not isinstance(proposal_payload, dict):
            raise ValueError("proposal is required in validate mode")
        policy = policy_from_dict(proposal_payload)
        validation = validate_change_policy(policy)
        result = {
            "success": validation["success"],
            "mode": mode,
            "policy": policy.to_dict(),
            "validation": validation,
            "historical_stats": numeric_stats(values),
        }
        if params.get("agent_loop"):
            result["agent_loop"] = _agent_loop(mode=mode, result=result, params=params)
        return result
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
        result = {
            "success": validation["success"],
            "mode": mode,
            "policy": policy.to_dict(),
            "validation": validation,
            "historical_stats": numeric_stats(values),
        }
        if params.get("agent_loop"):
            result["agent_loop"] = _agent_loop(mode=mode, result=result, params=params)
        return result
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
