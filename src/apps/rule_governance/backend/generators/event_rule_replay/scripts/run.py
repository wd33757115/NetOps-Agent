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
from src.core.patrol.rule_replay import replay_event_rule_definitions  # noqa: E402


def _definition_from_candidate(candidate: dict[str, Any], index: int) -> EventRuleDefinition:
    condition = candidate.get("condition")
    if not isinstance(condition, dict):
        raise ValueError(f"candidate_rules[{index}].condition must be an object")
    return EventRuleDefinition(
        rule_id=str(candidate.get("rule_id") or candidate.get("name") or f"candidate-rule-{index}"),
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


def _candidate_definitions(params: dict[str, Any]) -> list[EventRuleDefinition]:
    if "candidate_rules" in params:
        candidates = params["candidate_rules"]
    elif "rules" in params:
        candidates = params["rules"]
    else:
        raise ValueError("candidate_rules is required")
    if not isinstance(candidates, list):
        raise ValueError("candidate_rules must be a list")
    definitions: list[EventRuleDefinition] = []
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate_rules[{index}] must be an object")
        definitions.append(_definition_from_candidate(candidate, index))
    return definitions


def run_replay(params: dict[str, Any]) -> dict[str, Any]:
    run_ids = params.get("run_ids")
    if run_ids is not None and not isinstance(run_ids, list):
        raise ValueError("run_ids must be an array when provided")
    return replay_event_rule_definitions(
        _candidate_definitions(params),
        limit=int(params.get("limit") or 1000),
        include_active_collisions=params.get("include_active_collisions") is not False,
        run_ids=run_ids,
        candidate_contract=(
            dict(params["candidate_contract"])
            if isinstance(params.get("candidate_contract"), dict)
            else None
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay candidate event rules")
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    result = run_replay(params)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
