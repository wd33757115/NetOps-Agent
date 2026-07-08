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

from src.core.observation.repository import ObservationContractRepository  # noqa: E402
from src.core.patrol.change_policy_repository import ChangePolicyRepository  # noqa: E402
from src.core.patrol.event_rules import EventRuleRepository  # noqa: E402
from src.core.rule_control.repository import RuleGenerationJobRepository  # noqa: E402
from src.infrastructure.db.models import EventRuleVersionRecord  # noqa: E402
from src.infrastructure.db.postgres import get_db_session  # noqa: E402


def _success(payload: dict[str, Any] | None) -> bool:
    return bool(payload and payload.get("success") is True)


def _merge_agent_loops(*loops: Any) -> dict[str, Any] | None:
    attempts: list[dict[str, Any]] = []
    ready = True
    next_actions: list[str] = []
    revision_requests: list[dict[str, Any]] = []
    for loop in loops:
        if not isinstance(loop, dict):
            continue
        ready = ready and bool(loop.get("ready_for_approval"))
        if loop.get("next_action"):
            next_actions.append(str(loop["next_action"]))
        if isinstance(loop.get("revision_request"), dict):
            revision_requests.append(loop["revision_request"])
        for attempt in loop.get("attempts") or []:
            if isinstance(attempt, dict):
                if isinstance(attempt.get("revision_request"), dict):
                    revision_requests.append(attempt["revision_request"])
                attempts.append({**attempt, "attempt": len(attempts) + 1})
    if not attempts:
        return None
    merged = {
        "ready_for_approval": ready,
        "next_action": "submit_for_approval" if ready else (next_actions[-1] if next_actions else "revise_candidate"),
        "attempt_count": len(attempts),
        "attempts": attempts,
    }
    if revision_requests:
        merged["revision_request"] = revision_requests[-1]
        merged["revision_requests"] = revision_requests
    return merged


def _schema_id(params: dict[str, Any], proposal: dict[str, Any]) -> str:
    explicit = params.get("schema_id")
    if explicit:
        return str(explicit)
    vendor = str(params.get("vendor") or "Generic").strip() or "Generic"
    model = str(params.get("model") or "Generic").strip() or "Generic"
    command = str(params.get("command") or params.get("canonical_command") or "").strip()
    category = str(proposal.get("category") or proposal.get("entity_type") or "record")
    return ":".join(["cli", vendor, model, command or category])


def _transition_after_validation(
    repo: RuleGenerationJobRepository,
    job_id: str,
    validation_report: dict[str, Any],
) -> dict[str, Any]:
    repo.transition_job(job_id, status="validating")
    repo.attach_validation_report(job_id, validation_report=validation_report)
    if validation_report.get("success") is True:
        return repo.transition_job(job_id, status="pending_approval")
    return repo.transition_job(job_id, status="rejected")


def _event_rule_validation_report(
    params: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    synthetic = params.get("event_rule_validation") or result.get("validation") or {}
    if not isinstance(synthetic, dict):
        synthetic = {"success": False, "error": "event_rule_validation must be an object"}
    replay = params.get("event_rule_replay") or params.get("historical_replay")
    replay_required = params.get("require_event_rule_replay", True) is not False
    max_daily_events = params.get("max_estimated_daily_event_count")

    replay_errors: list[str] = []
    replay_success = True
    if replay_required:
        if not isinstance(replay, dict):
            replay_success = False
            replay_errors.append("historical replay result is required")
        else:
            replay_success = replay.get("success") is True
            try:
                collision_count = int(replay.get("collision_count") or 0)
            except (TypeError, ValueError):
                collision_count = 1
            if collision_count != 0:
                replay_success = False
                replay_errors.append("historical replay found rule collisions")
            if max_daily_events is not None:
                try:
                    estimated = float(replay.get("estimated_daily_event_count") or 0)
                    limit = float(max_daily_events)
                except (TypeError, ValueError):
                    estimated = 0.0
                    limit = -1.0
                if limit >= 0 and estimated > limit:
                    replay_success = False
                    replay_errors.append(
                        "historical replay estimated event rate exceeds limit"
                    )

    synthetic_success = synthetic.get("success") is True
    agent_loop = _merge_agent_loops(
        result.get("agent_loop"),
        synthetic.get("agent_loop") if isinstance(synthetic, dict) else None,
    )
    report = {
        "success": synthetic_success and replay_success,
        "generator": "event-rule-generator",
        "agent_loop": agent_loop,
        "synthetic_validation": synthetic,
        "historical_replay": replay if isinstance(replay, dict) else None,
        "replay_required": replay_required,
    }
    if replay_errors:
        report["errors"] = replay_errors
    return report


def _persist_contract(
    params: dict[str, Any],
    *,
    source_ref: str | None,
    created_by: str | None,
) -> list[dict[str, Any]]:
    result = params.get("contract_result") or params.get("observation_contract")
    if not isinstance(result, dict) or not result.get("proposal"):
        return []
    proposal = dict(result["proposal"])
    validation_report = {
        "success": _success(result),
        "generator": "observation-contract-generator",
        "validation": result.get("validation"),
        "agent_loop": _merge_agent_loops(result.get("agent_loop")),
    }
    job_repo = RuleGenerationJobRepository()
    job = job_repo.create_job(
        job_type="OBSERVATION_CONTRACT",
        source_ref=source_ref,
        payload={
            "command": params.get("command"),
            "vendor": params.get("vendor"),
            "model": params.get("model"),
            "proposal": proposal,
        },
        validation_report=validation_report,
        created_by=created_by,
    )
    contract = ObservationContractRepository().upsert_contract(
        source_type="cli",
        schema_id=_schema_id(params, proposal),
        schema_ref=params.get("schema_ref"),
        category=proposal.get("category"),
        validation=proposal,
        status="draft",
        source_job_id=job["id"],
        approval={"source": "rule-asset-persister"},
    )
    job = job_repo.attach_asset(
        job["id"],
        asset_type="OBSERVATION_CONTRACT",
        asset_id=contract.contract_id,
        asset_version_id=contract.contract_id,
        asset_digest=contract.contract_sha256,
        asset_ref=f"observation_contract:{contract.contract_id}",
    )
    job = _transition_after_validation(job_repo, job["id"], validation_report)
    return [
        {
            "asset_type": "OBSERVATION_CONTRACT",
            "asset_id": contract.contract_id,
            "asset_version_id": contract.contract_id,
            "asset_digest": contract.contract_sha256,
            "job": job,
        }
    ]


def _persist_change_policy(
    params: dict[str, Any],
    *,
    source_ref: str | None,
    created_by: str | None,
) -> list[dict[str, Any]]:
    result = params.get("change_policy_result") or params.get("change_policy")
    if not isinstance(result, dict) or not result.get("policy"):
        return []
    policy = dict(result["policy"])
    validation_report = {
        "success": _success(result),
        "generator": "change-policy-generator",
        "validation": result.get("validation"),
        "historical_stats": result.get("historical_stats"),
        "agent_loop": _merge_agent_loops(result.get("agent_loop")),
    }
    job_repo = RuleGenerationJobRepository()
    job = job_repo.create_job(
        job_type="CHANGE_POLICY",
        source_ref=source_ref,
        payload={"policy": policy},
        validation_report=validation_report,
        created_by=created_by,
    )
    name = str(
        params.get("change_policy_name")
        or f"policy:{policy.get('observation_type') or params.get('observation_type') or 'unknown'}"
    )
    persisted = ChangePolicyRepository().create_policy_version(
        name=name,
        policy=policy,
        status="draft",
        source_job_id=job["id"],
        validation_report=validation_report,
        created_by=created_by,
    )
    job = job_repo.attach_asset(
        job["id"],
        asset_type="CHANGE_POLICY",
        asset_id=persisted["policy_id"],
        asset_version_id=persisted["version_id"],
        asset_digest=persisted["digest"],
        asset_ref=f"change_policy_version:{persisted['version_id']}",
    )
    job = _transition_after_validation(job_repo, job["id"], validation_report)
    return [
        {
            "asset_type": "CHANGE_POLICY",
            "asset_id": persisted["policy_id"],
            "asset_version_id": persisted["version_id"],
            "asset_digest": persisted["digest"],
            "job": job,
        }
    ]


def _persist_event_rules(
    params: dict[str, Any],
    *,
    source_ref: str | None,
    created_by: str | None,
) -> list[dict[str, Any]]:
    result = params.get("event_rule_result") or params.get("event_rules")
    if not isinstance(result, dict):
        return []
    rules = result.get("rules") or []
    if not isinstance(rules, list):
        raise ValueError("event_rule_result.rules must be a list")
    validation_report = _event_rule_validation_report(params, result)

    assets: list[dict[str, Any]] = []
    job_repo = RuleGenerationJobRepository()
    event_repo = EventRuleRepository()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"event_rule_result.rules[{index}] must be an object")
        condition = rule.get("condition")
        if not isinstance(condition, dict):
            raise ValueError(f"event_rule_result.rules[{index}].condition must be an object")
        job = job_repo.create_job(
            job_type="EVENT_RULE",
            source_ref=source_ref,
            payload={"rule": rule},
            validation_report=validation_report,
            created_by=created_by,
        )
        version_id = event_repo.create_rule_version(
            name=str(rule.get("name") or rule.get("rule_id") or rule.get("event_type")),
            event_type=str(rule.get("event_type")),
            severity=str(rule.get("severity") or "warning"),
            condition=condition,
            description=rule.get("description"),
            created_by=created_by,
            source_job_id=job["id"],
            validation_report=validation_report,
            status="draft",
        )
        with get_db_session() as db:
            version = db.get(EventRuleVersionRecord, version_id)
            if version is None:
                raise ValueError("event rule version not found after creation")
            rule_id = version.rule_id
            digest = version.digest
        job = job_repo.attach_asset(
            job["id"],
            asset_type="EVENT_RULE",
            asset_id=rule_id,
            asset_version_id=version_id,
            asset_digest=digest,
            asset_ref=f"event_rule_version:{version_id}",
        )
        job = _transition_after_validation(job_repo, job["id"], validation_report)
        assets.append(
            {
                "asset_type": "EVENT_RULE",
                "asset_id": rule_id,
                "asset_version_id": version_id,
                "asset_digest": digest,
                "job": job,
            }
        )
    return assets


def run_persist(params: dict[str, Any]) -> dict[str, Any]:
    source_ref = params.get("source_ref")
    created_by = params.get("created_by") or "rule-asset-persister"
    assets: list[dict[str, Any]] = []
    assets.extend(_persist_contract(params, source_ref=source_ref, created_by=created_by))
    assets.extend(_persist_change_policy(params, source_ref=source_ref, created_by=created_by))
    assets.extend(_persist_event_rules(params, source_ref=source_ref, created_by=created_by))
    return {
        "success": True,
        "asset_count": len(assets),
        "assets": assets,
        "jobs": [asset["job"] for asset in assets],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist generated rule assets")
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    result = run_persist(params)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
