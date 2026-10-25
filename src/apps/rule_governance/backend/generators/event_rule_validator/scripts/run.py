#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[7]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.event_rules import EventRuleDefinition  # noqa: E402
from src.core.patrol.rule_validation import (  # noqa: E402
    EventRuleValidationCase,
    validate_event_rule_definition,
    validate_event_rule_definitions,
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
        priority=int(candidate.get("priority") or 100),
        exclusive=bool(candidate.get("exclusive", False)),
        event_class=str(candidate.get("event_class") or "fault"),
        recovery=dict(candidate.get("recovery") or {}),
        correlation_keys=tuple(
            candidate.get("correlation_keys")
            or candidate.get("correlation_key")
            or ("device_id", "entity_type", "entity_key", "field")
        ),
    )


def _definition_from_candidate(candidate: dict[str, Any], index: int = 1) -> EventRuleDefinition:
    condition = candidate.get("condition") or {}
    if not isinstance(condition, dict):
        raise ValueError(f"candidate_rules[{index}].condition must be an object")
    return EventRuleDefinition(
        rule_id=str(
            candidate.get("name")
            or candidate.get("rule_id")
            or f"candidate-rule-{index}"
        ),
        version=int(candidate.get("version") or 1),
        event_type=str(candidate["event_type"]),
        severity=str(candidate.get("severity") or "warning"),
        condition=dict(condition),
        description=candidate.get("description"),
        priority=int(candidate.get("priority") or 100),
        exclusive=bool(candidate.get("exclusive", False)),
        event_class=str(candidate.get("event_class") or "fault"),
        recovery=dict(candidate.get("recovery") or {}),
        correlation_keys=tuple(
            candidate.get("correlation_keys")
            or candidate.get("correlation_key")
            or ("device_id", "entity_type", "entity_key", "field")
        ),
    )


def _candidate_definitions(payload: dict[str, Any]) -> list[EventRuleDefinition]:
    if "candidate_rules" in payload:
        candidates = payload["candidate_rules"]
    elif "rules" in payload:
        candidates = payload["rules"]
    else:
        return [_candidate_definition(payload)]
    if not isinstance(candidates, list):
        raise ValueError("candidate_rules must be a list")
    definitions: list[EventRuleDefinition] = []
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate_rules[{index}] must be an object")
        definitions.append(_definition_from_candidate(candidate, index))
    return definitions


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
    generated = report.get("generated_result") or {}
    for row in generated.get("rows") or []:
        if not row.get("passed"):
            failed_rows.append({**row, "source": "generated"})
    invariant = report.get("invariant_result") or {}
    for row in invariant.get("rows") or []:
        if not row.get("passed"):
            failed_rows.append({**row, "source": "invariant"})

    attempt = {
        "attempt": 1,
        "stage": "event_rule_validation",
        "generator": "event-rule-validator",
        "mode": "deterministic_validation",
        "success": bool(report.get("success")),
        "input_summary": {
            "rule_count": report.get("rule_count", 1),
            "case_count": report.get("case_count", 0),
        },
        "output_summary": {
            "failed_case_count": len(failed_rows),
            "collision_count": report.get("collision_count", 0),
            "failed_count": report.get("failed_count", 0),
        },
        "validator_feedback": {
            "failed_cases": failed_rows[:10],
        },
    }

    if not failed_rows:
        return {
            "ready_for_approval": True,
            "next_action": "submit_for_approval",
            "revision_request": None,
            "attempts": [attempt],
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
                f"{row.get('name')}: expected {expected} but no rule matched; "
                "broaden fields, entity_types, or transition condition."
            )
        elif actual and not expected:
            hints.append(
                f"{row.get('name')}: unexpected {actual}; narrow entity_types, "
                "fields, or initial matching."
            )
        else:
            hints.append(
                f"{row.get('name')}: expected {expected}, got {actual}; "
                "check rule priority and condition semantics."
            )
    revision_request = {
        "target_generator": "event-rule-generator",
        "reason": hints or ["event rule validation failed"],
        "required_fixes": [
            "narrow or broaden entity_types according to failed cases",
            "adjust transition/threshold conditions to match expected events",
            "ensure initial observations only open explicit fault events",
            "keep recovery rules from becoming open active events",
        ],
        "failed_cases": failed_rows[:10],
        "next_params_patch": {
            "agent_loop": True,
        },
    }
    return {
        "ready_for_approval": False,
        "next_action": "revise_candidate_rule",
        "revision_request": revision_request,
        "attempts": [
            {
                **attempt,
                "validator_feedback": {
                    **attempt["validator_feedback"],
                    "hints": hints,
                },
                "revision_request": revision_request,
            }
        ],
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
    elif mode == "candidates":
        report = validate_event_rule_definitions(_candidate_definitions(params))
    elif mode == "cases":
        report = validate_event_rule_set(_case_payloads(params))
    else:
        raise ValueError("mode must be builtin, rule_pack, candidate, candidates, or cases")

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
