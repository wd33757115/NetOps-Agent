#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[7]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.change_policy import (  # noqa: E402
    compose_dimension_policies,
    infer_change_policy,
    numeric_stats,
    policy_from_dict,
    validate_change_policy,
)
from src.apps.rule_governance.backend.rule_control.generation_agent import (  # noqa: E402
    DEFAULT_RULE_GENERATION_PROVIDERS,
    RuleGenerationAgent,
)


def _historical_values(params: dict[str, Any]) -> list[Any]:
    values = params.get("historical_values") or params.get("values") or []
    if not isinstance(values, list):
        raise ValueError("historical_values must be a list")
    return values


def _target_historical_values(
    params: dict[str, Any],
    field_name: str,
    *,
    field_ref: str | None = None,
) -> list[Any]:
    by_field = params.get("historical_values_by_field")
    lookup = str(field_ref or field_name)
    if isinstance(by_field, dict) and isinstance(by_field.get(lookup), list):
        return list(by_field[lookup])
    records = params.get("records") or params.get("structured_records") or []
    if isinstance(records, list):
        values = [
            record.get(field_name)
            for record in records
            if isinstance(record, dict) and record.get(field_name) not in (None, "")
        ]
        if values:
            return values
    return _historical_values(params)


def _merge_generated_loops(results: list[dict[str, Any]]) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for result in results:
        loop = result.get("agent_loop")
        if not isinstance(loop, dict):
            continue
        for attempt in loop.get("attempts") or []:
            if isinstance(attempt, dict):
                attempts.append({**attempt, "attempt": len(attempts) + 1})
    return {
        "ready_for_approval": all(result.get("success") is True for result in results),
        "next_action": "submit_for_approval"
        if all(result.get("success") is True for result in results)
        else "revise_change_policy",
        "attempt_count": len(attempts),
        "attempts": attempts,
    }


def _compile_reference_policy_candidate(
    candidate: dict[str, Any],
    approved_references: dict[str, Any],
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not approved_references:
        return dict(candidate)
    output = dict(candidate)
    runtime_references: dict[str, dict[str, Any]] = {}
    allowed_modes = {
        str(item)
        for item in dict(constraints or {}).get("allowed_compare_modes") or []
    }
    allowed_roles = {"low", "critical_low", "high", "critical_high"}
    if allowed_modes == {"threshold_low_and_deadband"}:
        allowed_roles = {"low", "critical_low"}
    for role, raw in approved_references.items():
        if not isinstance(raw, dict):
            continue
        if str(role) not in allowed_roles:
            continue
        source_field_name = str(raw.get("field_name") or "").strip()
        runtime_field_name = str(
            raw.get("context_key") or source_field_name
        ).strip()
        if not runtime_field_name:
            continue
        runtime_references[str(role)] = {
            "field_name": runtime_field_name,
            "level": str(raw.get("level") or "reference"),
            "source": str(raw.get("source") or "structured_reference"),
        }
        if source_field_name and source_field_name != runtime_field_name:
            runtime_references[str(role)]["source_field_name"] = source_field_name
    output["threshold_references"] = runtime_references
    # Dataset medians describe the samples; they are not safe runtime defaults for
    # a different optic. A manually-authored policy can still provide static
    # thresholds through the retained builtin/validation interface.
    output["thresholds"] = {}
    output["allow_static_threshold_fallback"] = False
    has_low = bool({"low", "critical_low"}.intersection(runtime_references))
    has_high = bool({"high", "critical_high"}.intersection(runtime_references))
    if has_low and not has_high:
        output["compare_mode"] = "threshold_low_and_deadband"
    elif has_high:
        output["compare_mode"] = "threshold_and_deadband"
    output["recovery_threshold"] = None
    output["minimum_duration"] = None
    output["source"] = "structured_threshold_reference"
    output["confidence"] = max(float(output.get("confidence") or 0), 0.95)
    output["reason"] = (
        "Structured per-entity threshold fields from the approved "
        "CheckItem / CommandAdapter V2 are authoritative. Dataset medians are profiling "
        "evidence only and are never used as runtime threshold fallbacks; "
        f"deadband={output.get('deadband')!r}."
    )
    return output


def _compile_approved_static_policy_constraints(
    candidate: dict[str, Any],
    constraints: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bind generated candidates to explicit CheckItem policy decisions.

    Historical statistics remain useful evidence, but they must not override
    thresholds that the CheckItem catalog has already approved. Manual
    ``validate`` mode intentionally does not call this compiler so engineers can
    still see and correct a mismatching proposal instead of having it rewritten.
    """

    approved = dict(constraints or {})
    required_thresholds = approved.get("required_thresholds")
    required_recovery = approved.get("required_recovery_threshold")
    required_deadband = approved.get("required_deadband")
    if (
        not isinstance(required_thresholds, dict)
        and required_recovery is None
        and required_deadband is None
    ):
        return dict(candidate)

    output = dict(candidate)
    if isinstance(required_thresholds, dict):
        output["thresholds"] = {
            str(key): value for key, value in required_thresholds.items()
        }
    if required_recovery is not None:
        output["recovery_threshold"] = required_recovery
    if required_deadband is not None:
        output["deadband"] = required_deadband

    allowed_modes = sorted(
        {
            str(item)
            for item in approved.get("allowed_compare_modes") or []
            if str(item)
        }
    )
    if len(allowed_modes) == 1:
        output["compare_mode"] = allowed_modes[0]

    allowed_durations = list(approved.get("allowed_minimum_durations") or [])
    if (
        "allowed_minimum_durations" in approved
        and output.get("minimum_duration") not in allowed_durations
    ):
        output["minimum_duration"] = (
            None
            if None in allowed_durations
            else sorted(allowed_durations, key=lambda item: str(item))[0]
            if allowed_durations
            else None
        )

    output["source"] = "approved_check_item_policy"
    output["confidence"] = max(float(output.get("confidence") or 0), 0.99)
    output["reason"] = (
        "Threshold, recovery and deadband values are compiled from the approved "
        "CheckItem policy constraints; historical statistics are retained as "
        "validation evidence only."
    )
    return output


def _compile_directional_threshold_keys(candidate: dict[str, Any]) -> dict[str, Any]:
    """Normalize one model-proposed threshold into the runtime role namespace."""

    output = dict(candidate)
    thresholds = dict(output.get("thresholds") or {})
    if not thresholds:
        return output
    compare_mode = str(output.get("compare_mode") or "").strip()
    expected_roles = (
        {"low", "critical_low"}
        if compare_mode == "threshold_low_and_deadband"
        else {"high", "critical_high"}
        if compare_mode == "threshold_and_deadband"
        else set()
    )
    if not expected_roles or expected_roles.intersection(thresholds):
        return output
    numeric_thresholds = [
        value
        for value in thresholds.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if len(numeric_thresholds) != 1:
        return output
    canonical_role = "low" if "low" in expected_roles else "high"
    output["thresholds"] = {canonical_role: numeric_thresholds[0]}
    return output


def _validate_policy_constraints(policy: Any, constraints: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed_modes = {str(item) for item in constraints.get("allowed_compare_modes") or []}
    if allowed_modes and policy.compare_mode not in allowed_modes:
        errors.append(
            "compare_mode must be one of the policy constraints: "
            + ", ".join(sorted(allowed_modes))
        )
    required_keys = {str(item) for item in constraints.get("required_threshold_keys") or []}
    missing_keys = sorted(required_keys - set(policy.thresholds))
    if missing_keys:
        errors.append("required threshold keys are missing: " + ", ".join(missing_keys))
    required_thresholds = constraints.get("required_thresholds")
    if isinstance(required_thresholds, dict):
        expected_keys = {str(key) for key in required_thresholds}
        actual_keys = {str(key) for key in policy.thresholds}
        for key, expected in required_thresholds.items():
            actual = policy.thresholds.get(str(key))
            if actual is None or not math.isclose(
                float(actual),
                float(expected),
                rel_tol=0,
                abs_tol=1e-9,
            ):
                errors.append(
                    f"threshold {key} must equal the approved value {expected}"
                )
        if constraints.get("allow_additional_thresholds") is not True:
            unexpected = sorted(actual_keys - expected_keys)
            if unexpected:
                errors.append(
                    "unapproved threshold keys are present: " + ", ".join(unexpected)
                )
    references = dict(policy.threshold_references or {})
    if constraints.get("require_structured_threshold_references") is True:
        if not references or policy.source != "structured_threshold_reference":
            errors.append(
                "policy must use approved structured threshold references"
            )
    required_reference_any = {
        str(item)
        for item in constraints.get("required_threshold_reference_any") or []
    }
    if required_reference_any and not required_reference_any.intersection(references):
        errors.append(
            "policy requires at least one threshold reference role: "
            + ", ".join(sorted(required_reference_any))
        )
    bounds = constraints.get("threshold_bounds") or {}
    if isinstance(bounds, dict):
        for key, raw_bound in bounds.items():
            if key not in policy.thresholds or not isinstance(raw_bound, dict):
                continue
            value = float(policy.thresholds[key])
            if raw_bound.get("min") is not None and value < float(raw_bound["min"]):
                errors.append(f"threshold {key} is below the allowed minimum")
            if raw_bound.get("max") is not None and value > float(raw_bound["max"]):
                errors.append(f"threshold {key} exceeds the allowed maximum")
    direction = str(constraints.get("recovery_direction") or "").strip()
    recovery = policy.recovery_threshold
    dynamic_recovery = bool(references) and policy.deadband is not None
    if direction and recovery is None and not dynamic_recovery:
        errors.append("recovery_threshold is required by policy constraints")
    reference_key = str(constraints.get("recovery_reference_threshold") or "").strip()
    reference = policy.thresholds.get(reference_key) if reference_key else None
    if recovery is not None and reference is not None:
        if direction == "above" and float(recovery) <= float(reference):
            errors.append("recovery_threshold must be above the referenced low threshold")
        if direction == "below" and float(recovery) >= float(reference):
            errors.append("recovery_threshold must be below the referenced high threshold")
        hysteresis = abs(float(recovery) - float(reference))
        if constraints.get("minimum_hysteresis") is not None and hysteresis < float(
            constraints["minimum_hysteresis"]
        ):
            errors.append("recovery hysteresis is below the required minimum")
    elif dynamic_recovery and constraints.get("minimum_hysteresis") is not None:
        if abs(float(policy.deadband or 0)) < float(
            constraints["minimum_hysteresis"]
        ):
            errors.append("recovery hysteresis is below the required minimum")
    recovery_bounds = constraints.get("recovery_bounds") or {}
    if recovery is not None and isinstance(recovery_bounds, dict):
        if recovery_bounds.get("min") is not None and float(recovery) < float(
            recovery_bounds["min"]
        ):
            errors.append("recovery_threshold is below the allowed minimum")
        if recovery_bounds.get("max") is not None and float(recovery) > float(
            recovery_bounds["max"]
        ):
            errors.append("recovery_threshold exceeds the allowed maximum")
    if constraints.get("required_recovery_threshold") is not None:
        expected_recovery = float(constraints["required_recovery_threshold"])
        if recovery is None or not math.isclose(
            float(recovery),
            expected_recovery,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            errors.append(
                "recovery_threshold must equal the approved value "
                f"{constraints['required_recovery_threshold']}"
            )
    deadband_bounds = constraints.get("deadband_bounds") or {}
    if policy.deadband is not None and isinstance(deadband_bounds, dict):
        if deadband_bounds.get("min") is not None and float(policy.deadband) < float(
            deadband_bounds["min"]
        ):
            errors.append("deadband is below the allowed minimum")
        if deadband_bounds.get("max") is not None and float(policy.deadband) > float(
            deadband_bounds["max"]
        ):
            errors.append("deadband exceeds the allowed maximum")
    if constraints.get("required_deadband") is not None:
        expected_deadband = float(constraints["required_deadband"])
        if policy.deadband is None or not math.isclose(
            float(policy.deadband),
            expected_deadband,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            errors.append(
                f"deadband must equal the approved value {constraints['required_deadband']}"
            )
    if "allowed_minimum_durations" in constraints:
        allowed_durations = set(constraints.get("allowed_minimum_durations") or [])
        if policy.minimum_duration not in allowed_durations:
            errors.append(
                "minimum_duration is not supported; allowed values are: "
                + ", ".join(repr(item) for item in sorted(allowed_durations, key=str))
            )
    if constraints.get("evidence_only_reason"):
        speculative = re.findall(
            r"(?i)\b(?:likely|maybe|probably|possibly|may\s+represent|might)\b|"
            r"可能|疑似|推测|大概|或许",
            str(policy.reason or ""),
        )
        if speculative:
            errors.append(
                "reason contains unsupported causal speculation: "
                + ", ".join(sorted(set(speculative)))
                + "; describe only field semantics, constraints, and measured statistics"
            )
    return errors


def _agent_loop(
    *,
    mode: str,
    result: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    policy = result.get("policy") if isinstance(result.get("policy"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    stats = (
        result.get("historical_stats")
        if isinstance(result.get("historical_stats"), dict)
        else {}
    )
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
    policy_targets = params.get("policy_targets")
    if isinstance(policy_targets, list) and policy_targets:
        results: list[dict[str, Any]] = []
        for index, target in enumerate(policy_targets):
            if not isinstance(target, dict):
                raise ValueError(f"policy_targets[{index}] must be an object")
            field_name = str(target.get("field_name") or "").strip()
            observation_type = str(target.get("observation_type") or "").strip()
            field_semantics = dict(target.get("field_semantics") or {})
            record_set_name = str(target.get("record_set_name") or "").strip()
            source_targets = [
                dict(item)
                for item in target.get("source_targets") or []
                if isinstance(item, dict)
            ]
            if not field_name or not observation_type:
                raise ValueError(
                    f"policy_targets[{index}] requires field_name and observation_type"
                )
            if source_targets:
                target_records = [
                    {field_name: record.get(source_field)}
                    for source_target in source_targets
                    for source_record_set in [
                        str(source_target.get("record_set_name") or "")
                    ]
                    for source_field in [
                        str(source_target.get("field_name") or "")
                    ]
                    for record in params.get("records") or []
                    if isinstance(record, dict)
                    and source_field
                    and source_field in record
                    and (
                        not source_record_set
                        or str(record.get("__record_set__") or "")
                        == source_record_set
                    )
                ]
            else:
                target_records = [
                    {
                        str(key): value
                        for key, value in record.items()
                        if str(key) != "__record_set__"
                    }
                    for record in params.get("records") or []
                    if isinstance(record, dict)
                    and (
                        not record_set_name
                        or str(record.get("__record_set__") or "")
                        == record_set_name
                    )
                ]
            child_params = {
                **params,
                "policy_targets": None,
                "field_name": field_name,
                "observation_type": observation_type,
                "field_semantics": field_semantics,
                "records": target_records,
                "historical_values": _target_historical_values(
                    params,
                    field_name,
                    field_ref=str(target.get("field_ref") or "") or None,
                ),
                "threshold_references": target.get("threshold_references") or {},
                "dimension_selector": target.get("dimension_selector") or {},
                "policy_constraints": target.get("policy_constraints")
                or field_semantics.get("policy_constraints")
                or (params.get("policy_constraints_by_field") or {}).get(field_name)
                or params.get("policy_constraints"),
            }
            results.append(run_change_policy_generation(child_params))
        target_policies = [
            dict(result["policy"])
            for result in results
            if isinstance(result.get("policy"), dict)
        ]
        policies = compose_dimension_policies(target_policies)
        return {
            "success": all(result.get("success") is True for result in results),
            "mode": "policy_target_fanout",
            "policy_count": len(policies),
            "target_policy_count": len(target_policies),
            "policy": policies[0] if len(policies) == 1 else None,
            "policies": policies,
            "target_policies": target_policies,
            "results": results,
            "agent_loop": _merge_generated_loops(results),
            "generation_provider": str(
                params.get("generation_provider") or "builtin"
            ).casefold(),
        }
    mode = str(params.get("mode") or "infer_and_validate").strip().lower()
    values = _historical_values(params)
    proposal_payload = params.get("proposal") or params.get("change_policy")
    dimension_selector = dict(params.get("dimension_selector") or {})
    generation_provider = str(params.get("generation_provider") or "").strip().casefold()
    if generation_provider == "llm" and mode != "validate":
        observation_type = str(
            params.get("observation_type") or params.get("field_name") or ""
        ).strip()
        field_semantics = dict(params.get("field_semantics") or {})
        expected_semantic_type = str(field_semantics.get("semantic_type") or "").strip()
        stats = numeric_stats(values)
        policy_constraints = dict(params.get("policy_constraints") or {})
        threshold_references = dict(params.get("threshold_references") or {})

        def validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
            compiled_candidate = _compile_reference_policy_candidate(
                candidate,
                threshold_references,
                policy_constraints,
            )
            compiled_candidate = _compile_approved_static_policy_constraints(
                compiled_candidate,
                policy_constraints,
            )
            compiled_candidate = _compile_directional_threshold_keys(
                compiled_candidate
            )
            compiled_candidate["dimension_selector"] = dimension_selector
            policy = policy_from_dict(compiled_candidate)
            report = dict(validate_change_policy(policy))
            errors = [str(item) for item in report.get("errors") or []]
            if policy.observation_type != observation_type:
                errors.append("observation_type must match the selected target")
            if expected_semantic_type and policy.semantic_type != expected_semantic_type:
                errors.append("semantic_type must match the approved field semantics")
            errors.extend(_validate_policy_constraints(policy, policy_constraints))
            report["success"] = not errors
            report["errors"] = errors
            report["policy"] = policy.to_dict()
            return report

        agent = RuleGenerationAgent(
            DEFAULT_RULE_GENERATION_PROVIDERS.create(generation_provider),
            max_attempts=int(params.get("max_agent_attempts") or 3),
        )
        generated = agent.run(
            stage="change_policy",
            context={
                "observation_type": observation_type,
                "field_name": params.get("field_name"),
                "field_semantics": field_semantics,
                "historical_stats": stats,
                "historical_values": values[:200],
                "device_role": params.get("device_role"),
                "policy_intent": params.get("policy_intent"),
                "policy_constraints": policy_constraints,
                "threshold_references": threshold_references,
                "proposal_seed": proposal_payload if isinstance(proposal_payload, dict) else None,
            },
            validator=validate_candidate,
        )
        validation = dict(generated.get("validation") or {})
        return {
            "success": generated.get("success") is True,
            "mode": "llm_agent_loop",
            "policy": validation.pop("policy", None) or generated.get("candidate"),
            "validation": validation,
            "historical_stats": stats,
            "agent_loop": generated.get("agent_loop"),
            "generation_provider": generation_provider,
        }
    if mode == "infer":
        inferred = infer_change_policy(
            observation_type=str(params.get("observation_type") or params.get("field_name") or ""),
            field_name=params.get("field_name"),
            field_semantics=dict(params.get("field_semantics") or {}),
            historical_values=values,
            device_role=params.get("device_role"),
            policy_intent=params.get("policy_intent"),
            dimension_selector=dimension_selector,
        )
        policy = policy_from_dict(
            _compile_approved_static_policy_constraints(
                _compile_reference_policy_candidate(
                    inferred.to_dict(),
                    dict(params.get("threshold_references") or {}),
                    dict(params.get("policy_constraints") or {}),
                ),
                dict(params.get("policy_constraints") or {}),
            )
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
        policy = policy_from_dict(
            {**proposal_payload, "dimension_selector": dimension_selector}
        )
        validation = dict(validate_change_policy(policy))
        constraint_errors = _validate_policy_constraints(
            policy, dict(params.get("policy_constraints") or {})
        )
        validation["errors"] = [
            *[str(item) for item in validation.get("errors") or []],
            *constraint_errors,
        ]
        validation["success"] = not validation["errors"]
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
        candidate = (
            dict(proposal_payload)
            if isinstance(proposal_payload, dict)
            else infer_change_policy(
                observation_type=str(
                    params.get("observation_type") or params.get("field_name") or ""
                ),
                field_name=params.get("field_name"),
                field_semantics=dict(params.get("field_semantics") or {}),
                historical_values=values,
                device_role=params.get("device_role"),
                policy_intent=params.get("policy_intent"),
                dimension_selector=dimension_selector,
            ).to_dict()
        )
        candidate["dimension_selector"] = dimension_selector
        policy = policy_from_dict(
            _compile_approved_static_policy_constraints(
                _compile_reference_policy_candidate(
                    candidate,
                    dict(params.get("threshold_references") or {}),
                    dict(params.get("policy_constraints") or {}),
                ),
                dict(params.get("policy_constraints") or {}),
            )
        )
        validation = dict(validate_change_policy(policy))
        constraint_errors = _validate_policy_constraints(
            policy, dict(params.get("policy_constraints") or {})
        )
        validation["errors"] = [
            *[str(item) for item in validation.get("errors") or []],
            *constraint_errors,
        ]
        validation["success"] = not validation["errors"]
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
