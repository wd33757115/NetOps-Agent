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

from src.core.patrol.event_rules import EventRuleDefinition  # noqa: E402
from src.core.patrol.rule_validation import (  # noqa: E402
    EventRuleValidationCase,
    validate_builtin_event_rules,
    validate_event_rule_definition,
    validate_event_rule_pack,
    validate_event_rule_set,
)


def _candidate_definition(payload: dict[str, Any]) -> EventRuleDefinition:
    candidate = payload.get("candidate_rule") or payload.get("rule") or {}
    if not isinstance(candidate, dict):
        raise ValueError("candidate_rule must be an object")
    condition = candidate.get("condition") or {}
    if not isinstance(condition, dict):
        raise ValueError("candidate_rule.condition must be an object")
    return EventRuleDefinition(
        rule_id=str(candidate.get("name") or candidate.get("rule_id") or "candidate-rule"),
        version=int(candidate.get("version") or 1),
        event_type=str(candidate["event_type"]),
        severity=str(candidate.get("severity") or "warning"),
        condition=dict(condition),
        description=candidate.get("description"),
    )


def _case_payloads(payload: dict[str, Any]) -> list[EventRuleValidationCase]:
    cases = payload.get("cases") or []
    if not isinstance(cases, list):
        raise ValueError("cases must be a list")
    result: list[EventRuleValidationCase] = []
    for index, item in enumerate(cases, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"case #{index} must be an object")
        change = item.get("change")
        if not isinstance(change, dict):
            raise ValueError(f"case #{index}.change must be an object")
        result.append(
            EventRuleValidationCase(
                name=str(item.get("name") or f"case-{index}"),
                change=change,
                expected_event_type=item.get("expected_event_type"),
                expected_rule_id=item.get("expected_rule_id"),
            )
        )
    return result


def _agent_loop_advice(report: dict[str, Any]) -> dict[str, Any]:
    failed_rows: list[dict[str, Any]] = []
    for row in report.get("rows") or []:
        if not row.get("passed"):
            failed_rows.append(row)
    for rule_result in report.get("rule_results") or []:
        for row in rule_result.get("rows") or []:
            if not row.get("passed"):
                failed_rows.append(
                    {
                        **row,
                        "rule_id": rule_result.get("rule_id"),
                        "event_type": rule_result.get("event_type"),
                    }
                )
    invariant = report.get("invariant_result") or {}
    for row in invariant.get("rows") or []:
        if not row.get("passed"):
            failed_rows.append({**row, "source": "invariant"})

    if not failed_rows:
        return {
            "ready_for_approval": True,
            "next_action": "submit_for_approval",
            "hints": [
                "All generated and invariant cases passed.",
                "Runtime execution can remain deterministic; do not call LLM in the event path.",
            ],
        }
    hints = []
    for row in failed_rows[:5]:
        expected = row.get("expected_event_type")
        actual = row.get("actual_event_type")
        if expected and not actual:
            hints.append(
                f"{row.get('name')}: expected {expected} but no rule matched; broaden fields, entity_types, or transition condition."
            )
        elif actual and not expected:
            hints.append(
                f"{row.get('name')}: unexpected {actual}; narrow entity_types, fields, or initial matching."
            )
        else:
            hints.append(
                f"{row.get('name')}: expected {expected}, got {actual}; check rule priority and condition semantics."
            )
    return {
        "ready_for_approval": False,
        "next_action": "revise_candidate_rule",
        "failed_case_count": len(failed_rows),
        "hints": hints,
    }


def run_validation(params: dict[str, Any]) -> dict[str, Any]:
    mode = str(params.get("mode") or "builtin").strip().lower()
    if mode == "builtin":
        report = validate_event_rule_pack()
    elif mode == "rule_pack":
        report = validate_event_rule_pack(params.get("rule_pack_path"))
    elif mode == "candidate":
        report = validate_event_rule_definition(_candidate_definition(params))
    elif mode == "cases":
        report = validate_event_rule_set(_case_payloads(params))
    else:
        raise ValueError("mode must be builtin, rule_pack, candidate, or cases")

    result = {
        "success": bool(report.get("success")),
        "mode": mode,
        "validation_report": report,
    }
    if params.get("agent_loop"):
        result["agent_loop"] = _agent_loop_advice(report)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate event rules deterministically")
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    result = run_validation(params)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
