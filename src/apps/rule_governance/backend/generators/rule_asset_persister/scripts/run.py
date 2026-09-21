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

from src.core.observation.repository import ObservationContractRepository  # noqa: E402
from src.core.patrol.change_policy_repository import ChangePolicyRepository  # noqa: E402
from src.core.patrol.event_rules import EventRuleRepository  # noqa: E402
from src.apps.rule_governance.backend.rule_control.asset_requirements import (  # noqa: E402
    evaluate_requirement_replay_gate,
    normalize_rule_asset_requirements,
    rule_asset_composition_errors,
)
from src.apps.rule_governance.backend.rule_control.repository import RuleGenerationJobRepository  # noqa: E402
from src.apps.rule_governance.backend.rule_control.source_scope import (  # noqa: E402
    require_current_rule_generation_context,
)
from src.infrastructure.db.models import (  # noqa: E402
    EventRuleVersionRecord,
    RuleGenerationContextRecord,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402


def _success(payload: dict[str, Any] | None) -> bool:
    return bool(payload and payload.get("success") is True)


def _merge_agent_loops(*loops: Any) -> dict[str, Any] | None:
    attempts: list[dict[str, Any]] = []
    target_attempt_counts: list[int] = []
    seen_loops: set[str] = set()
    ready = True
    next_actions: list[str] = []
    revision_requests: list[dict[str, Any]] = []
    for loop in loops:
        if not isinstance(loop, dict):
            continue
        signature = json.dumps(loop, ensure_ascii=False, sort_keys=True, default=str)
        if signature in seen_loops:
            continue
        seen_loops.add(signature)
        loop_attempts = [item for item in (loop.get("attempts") or []) if isinstance(item, dict)]
        target_attempt_counts.append(len(loop_attempts))
        ready = ready and bool(loop.get("ready_for_approval"))
        if loop.get("next_action"):
            next_actions.append(str(loop["next_action"]))
        if isinstance(loop.get("revision_request"), dict):
            revision_requests.append(loop["revision_request"])
        for attempt in loop_attempts:
            if isinstance(attempt.get("revision_request"), dict):
                revision_requests.append(attempt["revision_request"])
            attempts.append({**attempt, "attempt": len(attempts) + 1})
    if not attempts:
        return None
    merged = {
        "ready_for_approval": ready,
        "next_action": "submit_for_approval"
        if ready
        else (next_actions[-1] if next_actions else "revise_candidate"),
        "attempt_count": len(attempts),
        "total_attempt_count": len(attempts),
        "target_count": len(target_attempt_counts),
        "max_attempts_per_target": max(target_attempt_counts, default=0),
        "attempts": attempts,
    }
    if revision_requests:
        merged["revision_request"] = revision_requests[-1]
        merged["revision_requests"] = revision_requests
    return merged


def _bundle_observation_types(params: dict[str, Any]) -> list[str]:
    result = params.get("change_policy_result") or params.get("change_policy") or {}
    if not isinstance(result, dict):
        return []
    policies: list[dict[str, Any]] = []
    for item in result.get("results") or []:
        if isinstance(item, dict) and isinstance(item.get("policy"), dict):
            policies.append(dict(item["policy"]))
    if isinstance(result.get("policy"), dict):
        policies.append(dict(result["policy"]))
    policies.extend(
        dict(item) for item in (result.get("policies") or []) if isinstance(item, dict)
    )
    return sorted(
        {
            str(item.get("observation_type") or "").strip()
            for item in policies
            if str(item.get("observation_type") or "").strip()
        }
    )


def _source_template_id(params: dict[str, Any]) -> str | None:
    explicit = str(params.get("source_template_id") or "").strip()
    if explicit:
        return explicit
    source_ref = str(params.get("source_ref") or "").strip()
    prefix = "parser_template:"
    if source_ref.startswith(prefix):
        template_id = source_ref[len(prefix) :].strip()
        if template_id:
            return template_id
    return None


def _schema_id(params: dict[str, Any], proposal: dict[str, Any]) -> str:
    explicit = params.get("schema_id")
    if explicit:
        return str(explicit)
    template_id = _source_template_id(params)
    if template_id:
        return template_id
    proposed = str(proposal.get("schema_id") or "").strip()
    if proposed:
        return proposed
    raise ValueError(
        "stable schema_id is required; parser-generated assets must use "
        "source_ref=parser_template:<template_id>"
    )


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
    requirements = normalize_rule_asset_requirements(
        params.get("rule_asset_requirements")
    )
    if "require_event_rule_replay" in params:
        replay_required = params.get("require_event_rule_replay") is not False
    elif params.get("rule_asset_requirements") is not None:
        replay_required = requirements["requires_historical_replay"] is True
    else:
        replay_required = True
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
                    replay_errors.append("historical replay estimated event rate exceeds limit")

    synthetic_success = synthetic.get("success") is True
    agent_loop = _merge_agent_loops(
        result.get("agent_loop"),
        synthetic.get("agent_loop") if isinstance(synthetic, dict) else None,
    )
    report = {
        "success": synthetic_success and replay_success,
        "generator": "event-rule-generator",
        "generation_provider": result.get("generation_provider") or "builtin",
        "agent_loop": agent_loop,
        "synthetic_validation": synthetic,
        "historical_replay": replay if isinstance(replay, dict) else None,
        "replay_required": replay_required,
        "asset_requirements": requirements,
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
        "generation_provider": result.get("generation_provider") or "builtin",
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
    source_template_id = _source_template_id(params)
    schema_ref = params.get("schema_ref")
    if not schema_ref and source_template_id:
        schema_ref = f"parser_template:{source_template_id}"
    contract = ObservationContractRepository().create_contract_version(
        source_type="cli",
        schema_id=_schema_id(params, proposal),
        schema_ref=schema_ref,
        category=proposal.get("category"),
        validation=proposal,
        source_template_id=source_template_id,
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
    if not isinstance(result, dict):
        return []
    child_results = [item for item in (result.get("results") or []) if isinstance(item, dict)]
    compiled_policies = [
        item for item in (result.get("policies") or []) if isinstance(item, dict)
    ]
    if compiled_policies:
        policy_results = [
            {
                "success": result.get("success"),
                "policy": item,
                "generation_provider": result.get("generation_provider"),
                "agent_loop": result.get("agent_loop"),
            }
            for item in compiled_policies
        ]
    elif child_results:
        policy_results = child_results
    elif result.get("policy"):
        policy_results = [result]
    else:
        policy_results = [
            {
                "success": result.get("success"),
                "policy": item,
                "generation_provider": result.get("generation_provider"),
                "agent_loop": result.get("agent_loop"),
            }
            for item in (result.get("policies") or [])
            if isinstance(item, dict)
        ]
    if not policy_results:
        return []
    job_repo = RuleGenerationJobRepository()
    assets: list[dict[str, Any]] = []
    for index, child_result in enumerate(policy_results):
        policy_payload = child_result.get("policy")
        if not isinstance(policy_payload, dict):
            raise ValueError(f"change policy result {index} has no policy object")
        policy = dict(policy_payload)
        validation_report = {
            "success": _success(child_result),
            "generator": "change-policy-generator",
            "generation_provider": child_result.get("generation_provider")
            or result.get("generation_provider")
            or "builtin",
            "validation": child_result.get("validation"),
            "historical_stats": child_result.get("historical_stats"),
            "agent_loop": _merge_agent_loops(child_result.get("agent_loop")),
        }
        job = job_repo.create_job(
            job_type="CHANGE_POLICY",
            source_ref=source_ref,
            payload={"policy": policy},
            validation_report=validation_report,
            created_by=created_by,
        )
        explicit_name = params.get("change_policy_name") if len(policy_results) == 1 else None
        observation_type = (
            policy.get("observation_type")
            or params.get("observation_type")
            or "unknown"
        )
        name = str(
            explicit_name
            or f"policy:{observation_type}"
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
        assets.append(
            {
                "asset_type": "CHANGE_POLICY",
                "asset_id": persisted["policy_id"],
                "asset_version_id": persisted["version_id"],
                "asset_digest": persisted["digest"],
                "observation_type": str(observation_type),
                "job": job,
            }
        )
    return assets


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
            priority=int(rule.get("priority") or 100),
            exclusive=bool(rule.get("exclusive", False)),
            event_class=str(rule.get("event_class") or "fault"),
            evaluation_scope=rule.get("evaluation_scope"),
            recovery=dict(rule.get("recovery") or {}),
            correlation_keys=(rule.get("correlation_key") or rule.get("correlation_keys")),
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
                "evaluation_scope": str(
                    rule.get("evaluation_scope") or "both"
                ),
                "event_class": str(rule.get("event_class") or "fault"),
                "event_type": str(rule.get("event_type") or ""),
                "observation_types": [
                    str(item)
                    for item in dict(rule.get("condition") or {}).get(
                        "observation_types"
                    )
                    or []
                    if str(item).strip()
                ],
                "job": job,
            }
        )
    return assets


def _context_gate(params: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any]:
    context_id = str(params.get("rule_generation_context_id") or "").strip()
    context_digest = str(params.get("rule_generation_context_digest") or "").strip()
    providers = {
        str(
            ((asset.get("job") or {}).get("validation_report_json") or {}).get(
                "generation_provider"
            )
            or ""
        ).casefold()
        for asset in assets
    }
    llm_generated = "llm" in providers
    if not context_id:
        return {
            "success": not llm_generated,
            "mode": "legacy_manual" if not llm_generated else "missing_immutable_context",
            "context_id": None,
            "context_digest": None,
            "privacy": {"success": not llm_generated, "raw_cli_included": False},
        }
    if not context_digest:
        return {
            "success": False,
            "mode": "immutable_dataset_context",
            "context_id": context_id,
            "context_digest": None,
            "errors": ["immutable context digest is required"],
        }
    with get_db_session() as db:
        row = db.get(RuleGenerationContextRecord, context_id)
        if row is None:
            return {
                "success": False,
                "mode": "immutable_dataset_context",
                "context_id": context_id,
                "errors": ["rule generation context not found"],
            }
        privacy = dict(row.privacy_report_json or {})
        try:
            integrity = require_current_rule_generation_context(
                db,
                row,
                expected_context_digest=context_digest,
            )
        except (KeyError, ValueError) as exc:
            return {
                "success": False,
                "mode": "immutable_dataset_context",
                "context_id": context_id,
                "context_digest": row.context_digest,
                "offline_job_id": row.offline_job_id,
                "dataset_ids": list(row.dataset_ids_json or []),
                "privacy": privacy,
                "errors": [str(exc)],
            }
        return {
            "success": True,
            "mode": "immutable_dataset_context",
            "context_id": row.id,
            "context_digest": row.context_digest,
            "offline_job_id": row.offline_job_id,
            "dataset_ids": list(row.dataset_ids_json or []),
            "replay_run_ids": list(
                dict(row.context_json or {}).get("replay_run_ids") or []
            ),
            "privacy": privacy,
            "source_integrity": integrity,
            "errors": [],
        }


def _bundle_validation_report(
    params: dict[str, Any],
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    requirements = normalize_rule_asset_requirements(
        params.get("rule_asset_requirements")
    )
    counts: dict[str, int] = {}
    child_validation_success = True
    child_jobs: list[dict[str, Any]] = []
    for asset in assets:
        asset_type = str(asset.get("asset_type") or "")
        counts[asset_type] = counts.get(asset_type, 0) + 1
        job = dict(asset.get("job") or {})
        report = dict(job.get("validation_report_json") or {})
        child_validation_success = child_validation_success and report.get("success") is True
        child_jobs.append(
            {
                "job_id": job.get("id"),
                "asset_type": asset_type,
                "asset_id": asset.get("asset_id"),
                "asset_version_id": asset.get("asset_version_id"),
                "asset_digest": asset.get("asset_digest"),
                "validation_success": report.get("success") is True,
            }
        )
    completeness_errors = rule_asset_composition_errors(
        counts=counts,
        requirements=requirements,
        change_policy_roles=[
            item
            for item in assets
            if str(item.get("asset_type") or "") == "CHANGE_POLICY"
        ],
        event_rule_roles=[
            item
            for item in assets
            if str(item.get("asset_type") or "") == "EVENT_RULE"
        ],
    )
    completeness = not completeness_errors
    replay = params.get("event_rule_replay") or params.get("historical_replay")
    context_gate = _context_gate(params, assets)
    replay_gate = evaluate_requirement_replay_gate(
        requirements=requirements,
        replay=replay if isinstance(replay, dict) else None,
        context_gate=context_gate,
    )
    replay_success = replay_gate["success"] is True
    success = (
        completeness and child_validation_success and replay_success and context_gate["success"]
    )
    blockers: list[str] = []
    if not completeness:
        blockers.extend(completeness_errors)
    if not child_validation_success:
        blockers.append("one or more child assets failed deterministic validation")
    if not replay_success:
        blockers.extend(replay_gate["errors"])
    if not context_gate["success"]:
        blockers.append("immutable sanitized context gate failed")
    return {
        "success": success,
        "generator": "rule-asset-persister",
        "bundle_gate": {
            "success": success,
            "asset_counts": counts,
            "child_validation_success": child_validation_success,
            "completeness": completeness,
            "asset_requirements": requirements,
            "replay_gate": replay_gate,
            "blockers": blockers,
        },
        "context_gate": context_gate,
        "historical_replay": replay if isinstance(replay, dict) else None,
        "asset_requirements": requirements,
        "children": child_jobs,
        "agent_loop": _merge_agent_loops(
            *[
                ((asset.get("job") or {}).get("validation_report_json") or {}).get("agent_loop")
                for asset in assets
            ]
        ),
    }


def _link_seed_to_bundle(
    repo: RuleGenerationJobRepository,
    params: dict[str, Any],
    bundle: dict[str, Any],
) -> None:
    """Keep the reusable seed linked to its latest concrete generation result."""

    seed_job_id = str(params.get("rule_generation_seed_job_id") or "").strip()
    if not seed_job_id:
        return
    seed = repo.get_job(seed_job_id)
    if seed is None:
        return
    source_ref = str(params.get("source_ref") or "")
    if source_ref and str(seed.get("source_ref") or "") != source_ref:
        raise ValueError("rule generation seed source_ref does not match persisted bundle")
    payload = dict(seed.get("payload_json") or {})
    payload["latest_bundle_id"] = str(bundle["id"])
    payload["latest_bundle_status"] = str(bundle.get("status") or "")
    payload["latest_bundle_context_id"] = params.get("rule_generation_context_id")
    repo.update_payload(seed_job_id, payload=payload)


def run_persist(params: dict[str, Any]) -> dict[str, Any]:
    generation = params.get("generation_execution")
    if generation:
        from src.apps.rule_governance.backend.rule_control.generation_jobs import GenerationJobs
        with GenerationJobs().write_guard(generation["run_id"], generation["token"]):
            return _run_persist(params)
    execution = params.get("revision_execution")
    if execution is not None:
        from src.apps.rule_governance.backend.rule_control.engineering_revision_jobs import EngineeringRevisionJobs

        with EngineeringRevisionJobs().write_guard(
            execution["task_id"], execution["lease_token"], source_ref=params.get("source_ref", ""),
        ):
            return _run_persist(params)
    return _run_persist(params)


def _run_persist(params: dict[str, Any]) -> dict[str, Any]:
    from src.apps.rule_governance.backend.identity import actor_user_id as normalize_actor_user_id

    identity = params.get("generation_identity")
    if identity is not None:
        if not isinstance(identity, dict):
            raise ValueError("generation identity must be an object")
        identity = {**identity, "actor_user_id": normalize_actor_user_id(identity.get("actor_user_id"))}
    source_ref = params.get("source_ref")
    created_by = params.get("created_by") or "rule-asset-persister"
    observation_types = _bundle_observation_types(params)
    primary_observation_type = observation_types[0] if len(observation_types) == 1 else None
    job_repo = RuleGenerationJobRepository()
    requirements = normalize_rule_asset_requirements(
        params.get("rule_asset_requirements")
    )
    bundle = job_repo.create_job(
        job_type="RULE_ASSET_BUNDLE",
        source_ref=source_ref,
        payload={
            "source_ref": source_ref,
            "generation_identity": identity,
            "generation_run_id": (params.get("generation_execution") or {}).get("run_id"),
            "observation_type": primary_observation_type,
            "observation_types": observation_types,
            "asset_scope": (
                "single_observation"
                if len(observation_types) == 1
                else "multi_observation"
            ),
            "command": params.get("command"),
            "vendor": params.get("vendor"),
            "model": params.get("model"),
            "context_id": params.get("rule_generation_context_id"),
            "context_digest": params.get("rule_generation_context_digest"),
            "asset_requirements": requirements,
            "child_job_ids": [],
        },
        validation_report={"success": False, "stage": "persisting_candidates"},
        created_by=created_by,
    )
    job_repo.transition_job(bundle["id"], status="validating")
    bundle_source_ref = f"rule_asset_bundle:{bundle['id']}"
    assets: list[dict[str, Any]] = []
    try:
        assets.extend(
            _persist_contract(
                params,
                source_ref=bundle_source_ref,
                created_by=created_by,
            )
        )
        assets.extend(
            _persist_change_policy(
                params,
                source_ref=bundle_source_ref,
                created_by=created_by,
            )
        )
        assets.extend(
            _persist_event_rules(
                params,
                source_ref=bundle_source_ref,
                created_by=created_by,
            )
        )
        validation_report = _bundle_validation_report(params, assets)
        child_job_ids = [str(asset["job"]["id"]) for asset in assets]
        payload = dict(bundle.get("payload_json") or {})
        payload.update(
            child_job_ids=child_job_ids,
            asset_counts=(validation_report.get("bundle_gate") or {}).get("asset_counts") or {},
            asset_requirements=requirements,
            offline_job_id=(validation_report.get("context_gate") or {}).get("offline_job_id"),
            dataset_ids=(validation_report.get("context_gate") or {}).get("dataset_ids") or [],
        )
        job_repo.update_payload(bundle["id"], payload=payload)
        job_repo.attach_asset(
            bundle["id"],
            asset_type="RULE_ASSET_BUNDLE",
            asset_id=bundle["id"],
            asset_version_id=str(
                params.get("rule_generation_context_digest")
                or params.get("rule_generation_context_id")
                or bundle["id"]
            ),
            asset_digest=params.get("rule_generation_context_digest"),
            asset_ref=bundle_source_ref,
        )
        job_repo.attach_validation_report(
            bundle["id"],
            validation_report=validation_report,
        )
        bundle = job_repo.transition_job(
            bundle["id"],
            status="pending_approval" if validation_report["success"] else "rejected",
        )
        _link_seed_to_bundle(job_repo, params, bundle)
    except Exception as exc:
        failure_report = {
            "success": False,
            "stage": "persisting_candidates",
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
        job_repo.attach_validation_report(bundle["id"], validation_report=failure_report)
        try:
            bundle = job_repo.transition_job(bundle["id"], status="rejected")
        except Exception:
            pass
        raise
    return {
        "success": True,
        "asset_count": len(assets),
        "assets": assets,
        "jobs": [asset["job"] for asset in assets],
        "bundle": bundle,
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
    from src.apps.rule_governance.backend.access import maintenance
    raise SystemExit(maintenance(main)())
