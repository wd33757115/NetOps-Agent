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

from src.core.patrol.event_rule_generation import (  # noqa: E402
    generate_and_validate_event_rules,
)


def _agent_loop(result: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    failed = []
    for report in result.get("reports") or []:
        validation = report.get("validation") if isinstance(report, dict) else {}
        if isinstance(validation, dict) and validation.get("success") is not True:
            failed.append(
                {
                    "rule": (report.get("rule") or {}).get("name"),
                    "errors": validation.get("errors") or [],
                }
            )
    success = result.get("success") is True
    revision_request = None
    if not success:
        revision_request = {
            "target_generator": "event-rule-generator",
            "reason": [
                "event rule generation or deterministic validation failed",
            ],
            "required_fixes": [
                "match rules to the contract entity_type",
                "keep recovery rules from matching initial observations",
                "include entity_types so status/state fields are not misclassified",
                "keep dedupe and resolves semantics compatible with event lifecycle",
            ],
            "failed_rules": failed,
            "next_params_patch": {
                "agent_loop": True,
            },
        }
    return {
        "ready_for_approval": success,
        "next_action": "submit_for_validation" if success else "revise_event_rule_candidate",
        "revision_request": revision_request,
        "attempts": [
            {
                "attempt": 1,
                "stage": "event_rule_generation",
                "generator": "event-rule-generator",
                "mode": "generate_and_validate",
                "success": success,
                "input_summary": {
                    "entity_type": (params.get("contract") or {}).get("entity_type")
                    if isinstance(params.get("contract"), dict)
                    else None,
                    "observation_type": (params.get("change_policy") or {}).get("observation_type")
                    if isinstance(params.get("change_policy"), dict)
                    else None,
                    "compare_mode": (params.get("change_policy") or {}).get("compare_mode")
                    if isinstance(params.get("change_policy"), dict)
                    else None,
                    "intent_provided": bool(params.get("intent")),
                },
                "output_summary": {
                    "generated_rule_count": result.get("generated_rule_count", 0),
                    "event_types": [
                        str(rule.get("event_type"))
                        for rule in result.get("rules") or []
                        if isinstance(rule, dict)
                    ],
                },
                "validator_feedback": {
                    "failed_rule_count": len(failed),
                    "failed_rules": failed,
                },
                "revision_request": revision_request,
            }
        ],
    }


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
    if params.get("agent_loop"):
        result["agent_loop"] = _agent_loop(result, params)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
