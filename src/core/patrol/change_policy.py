"""Deterministic ChangePolicy proposal and validation helpers."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from statistics import mean, pstdev
from typing import Any


@dataclass(frozen=True)
class ChangePolicyProposal:
    observation_type: str
    semantic_type: str
    compare_mode: str
    ignore_fields: tuple[str, ...] = ()
    deadband: float | None = None
    thresholds: dict[str, float] | None = None
    threshold_references: dict[str, dict[str, Any]] | None = None
    baseline_comparison: dict[str, Any] | None = None
    allow_static_threshold_fallback: bool = False
    recovery_threshold: float | None = None
    minimum_duration: str | None = None
    source: str = "deterministic_policy_generator"
    confidence: float = 0.85
    reason: str = ""
    dimension_selector: dict[str, Any] | None = None
    dimension_policies: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%"))
        except ValueError:
            return None
    return None


def normalize_dimension_selector(value: Any) -> dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ValueError("dimension_selector must be an object")
    selector: dict[str, Any] = {}
    for raw_name, raw_value in sorted(value.items()):
        name = str(raw_name).strip()
        if not name:
            raise ValueError("dimension_selector keys cannot be empty")
        if isinstance(raw_value, (dict, list, tuple, set)):
            raise ValueError(
                f"dimension_selector.{name} must be a scalar value"
            )
        selector[name] = raw_value
    return selector


def dimension_selector_matches(
    selector: Any,
    dimensions: Any,
) -> bool:
    expected = normalize_dimension_selector(selector)
    actual = dict(dimensions or {}) if isinstance(dimensions or {}, dict) else {}
    return all(actual.get(name) == value for name, value in expected.items())


def compose_dimension_policies(
    policies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compile target policies into one runtime asset per observation type."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw_policy in policies:
        policy = dict(raw_policy)
        observation_type = str(policy.get("observation_type") or "").strip()
        if not observation_type:
            raise ValueError("policy observation_type is required")
        policy["dimension_selector"] = normalize_dimension_selector(
            policy.get("dimension_selector")
        )
        grouped.setdefault(observation_type, []).append(policy)

    compiled: list[dict[str, Any]] = []
    for observation_type, candidates in grouped.items():
        if len(candidates) == 1:
            compiled.append(candidates[0])
            continue
        selectors = [
            json.dumps(
                candidate.get("dimension_selector") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for candidate in candidates
        ]
        if len(set(selectors)) != len(selectors):
            raise ValueError(
                f"duplicate dimension selectors for {observation_type}"
            )
        semantic_types = {
            str(candidate.get("semantic_type") or "")
            for candidate in candidates
        }
        if len(semantic_types) != 1:
            raise ValueError(
                f"dimension policies for {observation_type} must share semantic_type"
            )
        branches = []
        for candidate in sorted(
            candidates,
            key=lambda item: json.dumps(
                item.get("dimension_selector") or {},
                sort_keys=True,
                separators=(",", ":"),
            ),
        ):
            branch_policy = {
                key: value
                for key, value in candidate.items()
                if key not in {"dimension_selector", "dimension_policies"}
            }
            branches.append(
                {
                    "selector": dict(candidate.get("dimension_selector") or {}),
                    "policy": branch_policy,
                }
            )
        compiled.append(
            {
                "observation_type": observation_type,
                "semantic_type": next(iter(semantic_types)),
                "compare_mode": "dimension_dispatch",
                "dimension_selector": {},
                "dimension_policies": branches,
                "source": "platform_dimension_dispatch",
                "confidence": min(
                    float(candidate.get("confidence") or 0.0)
                    for candidate in candidates
                ),
                "reason": (
                    f"platform compiled {len(branches)} dimension-specific "
                    f"policies for {observation_type}"
                ),
            }
        )
    return compiled


def _select_dimension_policy(
    policy: ChangePolicyProposal,
    dimensions: dict[str, Any] | None,
) -> ChangePolicyProposal | None:
    if policy.compare_mode != "dimension_dispatch":
        return (
            policy
            if dimension_selector_matches(policy.dimension_selector, dimensions)
            else None
        )
    matches: list[tuple[int, ChangePolicyProposal]] = []
    for branch in policy.dimension_policies:
        if not isinstance(branch, dict):
            continue
        selector = normalize_dimension_selector(branch.get("selector"))
        child = branch.get("policy")
        if not isinstance(child, dict):
            continue
        if dimension_selector_matches(selector, dimensions):
            matches.append(
                (
                    len(selector),
                    policy_from_dict(
                        {**child, "dimension_selector": selector}
                    ),
                )
            )
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None
    return matches[0][1]


def numeric_stats(values: list[Any]) -> dict[str, Any]:
    numbers = sorted(value for value in (_number(item) for item in values) if value is not None)
    if not numbers:
        return {
            "count": len(values),
            "numeric_count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "stddev": None,
            "p95": None,
            "p99": None,
        }
    return {
        "count": len(values),
        "numeric_count": len(numbers),
        "min": numbers[0],
        "max": numbers[-1],
        "mean": mean(numbers),
        "stddev": pstdev(numbers) if len(numbers) > 1 else 0.0,
        "p95": percentile(numbers, 95),
        "p99": percentile(numbers, 99),
    }


def percentile(numbers: list[float], percentile_value: int) -> float:
    if not numbers:
        raise ValueError("numbers cannot be empty")
    if len(numbers) == 1:
        return numbers[0]
    rank = (len(numbers) - 1) * (percentile_value / 100)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return numbers[int(rank)]
    return numbers[lower] + (numbers[upper] - numbers[lower]) * (rank - lower)


def infer_change_policy(
    *,
    observation_type: str,
    field_name: str | None = None,
    field_semantics: dict[str, Any] | None = None,
    historical_values: list[Any] | None = None,
    device_role: str | None = None,
    policy_intent: str | None = None,
    dimension_selector: dict[str, Any] | None = None,
) -> ChangePolicyProposal:
    semantics = dict(field_semantics or {})
    semantic_type = str(semantics.get("semantic_type") or "text")
    unit = str(semantics.get("unit") or "")
    field = str(field_name or observation_type or "")
    stats = numeric_stats(historical_values or [])
    reason_parts = [
        f"semantic_type={semantic_type}",
        f"field={field}",
    ]
    if device_role:
        reason_parts.append(f"device_role={device_role}")
    if policy_intent:
        reason_parts.append(f"intent={policy_intent}")

    if semantic_type == "state":
        return ChangePolicyProposal(
            observation_type=observation_type,
            semantic_type=semantic_type,
            compare_mode="state_transition",
            reason="; ".join(reason_parts),
            dimension_selector=normalize_dimension_selector(dimension_selector),
        )
    if semantic_type == "gauge":
        thresholds: dict[str, float] = {}
        threshold_references = {
            str(role): dict(reference)
            for role, reference in dict(
                semantics.get("threshold_references") or {}
            ).items()
            if isinstance(reference, dict)
            and str(reference.get("field_name") or "").strip()
        }
        recovery_threshold = None
        deadband = 0.0
        lowered = " ".join([field, observation_type, policy_intent or ""]).lower()
        if unit.lower() == "dbm" or any(
            token in lowered for token in ("optical", "rx_power", "tx_power", "dbm")
        ):
            deadband = 2.0
            low_roles = {"low", "critical_low"}.intersection(
                threshold_references
            )
            high_roles = {"high", "critical_high"}.intersection(
                threshold_references
            )
            if low_roles and not high_roles:
                compare_mode = "threshold_low_and_deadband"
            elif threshold_references:
                compare_mode = "threshold_and_deadband"
            else:
                compare_mode = "numeric_delta"
                reason_parts.append("threshold_status=threshold_unknown")
        elif unit == "percent" or any(token in lowered for token in ("cpu", "memory", "usage")):
            deadband = 5.0
            compare_mode = "threshold_and_deadband"
            if not threshold_references:
                p99 = stats.get("p99")
                high = max(90.0, float(p99 or 0))
                high = min(100.0, round(high, 2))
                recovery_threshold = max(0.0, round(high - 5.0, 2))
                thresholds["high"] = high
        else:
            compare_mode = "numeric_delta"
        return ChangePolicyProposal(
            observation_type=observation_type,
            semantic_type=semantic_type,
            compare_mode=compare_mode,
            deadband=deadband,
            thresholds=thresholds,
            threshold_references=threshold_references,
            recovery_threshold=recovery_threshold,
            minimum_duration="0s",
            source=(
                "structured_threshold_reference"
                if threshold_references
                else "deterministic_policy_generator"
            ),
            confidence=0.95 if threshold_references else 0.85,
            reason="; ".join(
                [
                    *reason_parts,
                    f"p95={stats.get('p95')}",
                    f"p99={stats.get('p99')}",
                ]
            ),
            dimension_selector=normalize_dimension_selector(dimension_selector),
        )
    if semantic_type == "counter":
        return ChangePolicyProposal(
            observation_type=observation_type,
            semantic_type=semantic_type,
            compare_mode="counter_delta",
            reason="; ".join(reason_parts),
            dimension_selector=normalize_dimension_selector(dimension_selector),
        )
    if semantic_type == "raw_hash":
        return ChangePolicyProposal(
            observation_type=observation_type,
            semantic_type=semantic_type,
            compare_mode="hash_diff",
            reason="; ".join(reason_parts),
            dimension_selector=normalize_dimension_selector(dimension_selector),
        )
    if semantic_type == "enum":
        return ChangePolicyProposal(
            observation_type=observation_type,
            semantic_type=semantic_type,
            compare_mode="enum_transition",
            reason="; ".join(reason_parts),
            dimension_selector=normalize_dimension_selector(dimension_selector),
        )
    return ChangePolicyProposal(
        observation_type=observation_type,
        semantic_type=semantic_type,
        compare_mode="exact",
        reason="; ".join(reason_parts),
        dimension_selector=normalize_dimension_selector(dimension_selector),
    )


def policy_from_dict(payload: dict[str, Any]) -> ChangePolicyProposal:
    return ChangePolicyProposal(
        observation_type=str(payload.get("observation_type") or payload.get("field") or ""),
        semantic_type=str(payload.get("semantic_type") or "text"),
        compare_mode=str(payload.get("compare_mode") or "exact"),
        ignore_fields=tuple(str(item) for item in payload.get("ignore_fields") or ()),
        deadband=(
            float(payload["deadband"])
            if payload.get("deadband") is not None
            else None
        ),
        thresholds={
            str(key): float(value)
            for key, value in dict(payload.get("thresholds") or {}).items()
        },
        threshold_references={
            str(key): dict(value)
            for key, value in dict(payload.get("threshold_references") or {}).items()
            if isinstance(value, dict)
        },
        baseline_comparison=(
            dict(payload["baseline_comparison"])
            if isinstance(payload.get("baseline_comparison"), dict)
            else None
        ),
        allow_static_threshold_fallback=bool(
            payload.get("allow_static_threshold_fallback", False)
        ),
        recovery_threshold=(
            float(payload["recovery_threshold"])
            if payload.get("recovery_threshold") is not None
            else None
        ),
        minimum_duration=payload.get("minimum_duration"),
        source=str(payload.get("source") or "provided"),
        confidence=float(payload.get("confidence") or 0.8),
        reason=str(payload.get("reason") or ""),
        dimension_selector=normalize_dimension_selector(
            payload.get("dimension_selector")
        ),
        dimension_policies=tuple(
            dict(item)
            for item in payload.get("dimension_policies") or ()
            if isinstance(item, dict)
        ),
    )


def validate_change_policy(policy: ChangePolicyProposal) -> dict[str, Any]:
    errors: list[str] = []
    if not policy.observation_type:
        errors.append("observation_type is required")
    allowed_modes = {
        "dimension_dispatch",
        "state_transition",
        "threshold_and_deadband",
        "threshold_low_and_deadband",
        "numeric_delta",
        "counter_delta",
        "hash_diff",
        "enum_transition",
        "exact",
    }
    if policy.compare_mode not in allowed_modes:
        errors.append(f"unsupported compare_mode: {policy.compare_mode}")
    compatible_modes = {
        "boolean": {"exact", "state_transition", "enum_transition"},
        "counter": {"counter_delta"},
        "enum": {"enum_transition", "state_transition", "exact"},
        "gauge": {
            "numeric_delta",
            "threshold_and_deadband",
            "threshold_low_and_deadband",
        },
        "percentage": {
            "numeric_delta",
            "threshold_and_deadband",
            "threshold_low_and_deadband",
        },
        "raw_hash": {"hash_diff"},
        "state": {"state_transition", "enum_transition", "exact"},
        "text": {"exact", "hash_diff"},
    }
    semantic_type = str(policy.semantic_type or "").strip().casefold()
    if (
        semantic_type in compatible_modes
        and policy.compare_mode not in compatible_modes[semantic_type]
        and policy.compare_mode != "dimension_dispatch"
    ):
        errors.append(
            f"compare_mode {policy.compare_mode} is incompatible with "
            f"semantic_type {semantic_type}"
        )
    if policy.deadband is not None and float(policy.deadband) < 0:
        errors.append("deadband must be greater than or equal to zero")
    if policy.minimum_duration not in {None, "", "0s"}:
        errors.append(
            "minimum_duration is not executable; only null or 0s is supported"
        )
    try:
        normalize_dimension_selector(policy.dimension_selector)
    except ValueError as exc:
        errors.append(str(exc))
    if policy.compare_mode == "dimension_dispatch":
        if not policy.dimension_policies:
            errors.append("dimension_dispatch requires dimension_policies")
        selectors: set[str] = set()
        for index, branch in enumerate(policy.dimension_policies):
            selector = branch.get("selector")
            child = branch.get("policy")
            try:
                normalized_selector = normalize_dimension_selector(selector)
            except ValueError as exc:
                errors.append(f"dimension_policies[{index}]: {exc}")
                continue
            selector_key = json.dumps(
                normalized_selector,
                sort_keys=True,
                separators=(",", ":"),
            )
            if selector_key in selectors:
                errors.append(
                    f"dimension_policies[{index}] duplicates a dimension selector"
                )
            selectors.add(selector_key)
            if not isinstance(child, dict):
                errors.append(
                    f"dimension_policies[{index}].policy must be an object"
                )
                continue
            child_policy = policy_from_dict(child)
            if child_policy.observation_type != policy.observation_type:
                errors.append(
                    f"dimension_policies[{index}] observation_type must match parent"
                )
            for child_error in validate_change_policy(child_policy)["errors"]:
                errors.append(
                    f"dimension_policies[{index}]: {child_error}"
                )
        return {
            "success": not errors,
            "errors": errors,
            "policy": policy.to_dict(),
        }
    if policy.semantic_type in {"state", "enum", "text"} and policy.thresholds:
        errors.append("non-numeric semantic types must not define thresholds")
    if policy.semantic_type == "counter" and policy.compare_mode != "counter_delta":
        errors.append("counter semantic type must use counter_delta")
    if policy.compare_mode == "threshold_and_deadband":
        high = (policy.thresholds or {}).get("high")
        high_reference = (policy.threshold_references or {}).get("high")
        critical_high = (policy.thresholds or {}).get("critical_high")
        critical_high_reference = (policy.threshold_references or {}).get("critical_high")
        if (
            high is None
            and not high_reference
            and critical_high is None
            and not critical_high_reference
        ):
            errors.append(
                "threshold_and_deadband requires a high or critical_high threshold"
            )
        if policy.recovery_threshold is not None and high is not None:
            if policy.recovery_threshold >= high:
                errors.append("recovery_threshold must be lower than thresholds.high")
    if policy.compare_mode == "threshold_low_and_deadband":
        low = (policy.thresholds or {}).get("low")
        low_reference = (policy.threshold_references or {}).get("low")
        critical_low = (policy.thresholds or {}).get("critical_low")
        critical_low_reference = (policy.threshold_references or {}).get("critical_low")
        if (
            low is None
            and not low_reference
            and critical_low is None
            and not critical_low_reference
        ):
            errors.append(
                "threshold_low_and_deadband requires a low or critical_low threshold"
            )
        if policy.recovery_threshold is not None and low is not None:
            if policy.recovery_threshold <= low:
                errors.append("recovery_threshold must be higher than thresholds.low")
    for role, reference in (policy.threshold_references or {}).items():
        if role not in {"low", "high", "critical_low", "critical_high"}:
            errors.append(f"unsupported threshold reference role: {role}")
            continue
        if not isinstance(reference, dict) or not str(
            reference.get("field_name") or ""
        ).strip():
            errors.append(f"threshold_references.{role}.field_name is required")
    if policy.baseline_comparison:
        baseline = dict(policy.baseline_comparison)
        if baseline.get("kind") != "linear_power_loss_ratio":
            errors.append("unsupported baseline_comparison.kind")
        ratio = _number(baseline.get("max_loss_ratio"))
        if ratio is None or not 0 < ratio < 1:
            errors.append("baseline_comparison.max_loss_ratio must be between 0 and 1")
        identity_fields = baseline.get("identity_fields") or []
        if not isinstance(identity_fields, list) or not all(
            str(item).strip() for item in identity_fields
        ):
            errors.append("baseline_comparison.identity_fields is required")
    return {
        "success": not errors,
        "errors": errors,
        "policy": policy.to_dict(),
    }


def compare_with_policy(
    policy: ChangePolicyProposal,
    *,
    old: Any,
    new: Any,
    field: str | None = None,
    context: dict[str, Any] | None = None,
    dimensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_policy = _select_dimension_policy(policy, dimensions)
    if selected_policy is None:
        return {
            "changed": False,
            "reason": "dimension_policy_not_matched",
        }
    if selected_policy is not policy:
        result = compare_with_policy(
            selected_policy,
            old=old,
            new=new,
            field=field,
            context=context,
            dimensions=dimensions,
        )
        result["dimension_policy_matched"] = True
        result["dimension_selector"] = selected_policy.dimension_selector or {}
        result["selected_compare_mode"] = selected_policy.compare_mode
        return result
    policy = selected_policy
    if field and field in policy.ignore_fields:
        return {"changed": False, "reason": "ignored_field"}
    if policy.compare_mode in {"state_transition", "enum_transition", "exact", "hash_diff"}:
        return {
            "changed": str(old).casefold() != str(new).casefold()
            if policy.compare_mode == "state_transition"
            else old != new,
            "reason": policy.compare_mode,
        }
    if policy.compare_mode == "counter_delta":
        old_num = _number(old)
        new_num = _number(new)
        return {
            "changed": old_num is not None and new_num is not None and new_num != old_num,
            "delta": (new_num - old_num) if old_num is not None and new_num is not None else None,
            "reason": "counter_delta",
        }
    if policy.compare_mode in {
        "numeric_delta",
        "threshold_and_deadband",
        "threshold_low_and_deadband",
    }:
        old_num = _number(old)
        new_num = _number(new)
        if old_num is None or new_num is None:
            return {"changed": old != new, "reason": "non_numeric_fallback"}
        deadband = float(policy.deadband or 0)
        threshold_sources: dict[str, str] = {}

        def threshold(role: str) -> float | None:
            reference = dict((policy.threshold_references or {}).get(role) or {})
            reference_field = str(reference.get("field_name") or "")
            referenced_value = _number(dict(context or {}).get(reference_field))
            if referenced_value is not None:
                threshold_sources[role] = f"context:{reference_field}"
                return referenced_value
            if reference and not policy.allow_static_threshold_fallback:
                return None
            static_value = _number((policy.thresholds or {}).get(role))
            if static_value is not None:
                threshold_sources[role] = (
                    "policy_fallback" if reference else "policy_static"
                )
            return static_value

        high_thresholds = {
            role: value
            for role in ("high", "critical_high")
            if (value := threshold(role)) is not None
        }
        low_thresholds = {
            role: value
            for role in ("low", "critical_low")
            if (value := threshold(role)) is not None
        }
        crossed_high_roles = [
            role
            for role, value in high_thresholds.items()
            if old_num < value <= new_num
        ]
        crossed_low_roles = [
            role
            for role, value in low_thresholds.items()
            if old_num > value >= new_num
        ]
        recovered_high_roles = [
            role
            for role, value in high_thresholds.items()
            if old_num >= value and new_num <= value - deadband
        ]
        recovered_low_roles = []
        for role, value in low_thresholds.items():
            recovery_boundary = (
                float(policy.recovery_threshold)
                if role == "low" and policy.recovery_threshold is not None
                else value + deadband
            )
            if old_num <= value and new_num >= recovery_boundary:
                recovered_low_roles.append(role)
        configured_reference_fields = {
            role: str(reference.get("field_name") or "")
            for role, reference in (policy.threshold_references or {}).items()
            if isinstance(reference, dict)
            and str(reference.get("field_name") or "").strip()
        }
        missing_threshold_references = sorted(
            field_name
            for role, field_name in configured_reference_fields.items()
            if role not in threshold_sources
        )
        threshold_status = (
            "threshold_unknown"
            if configured_reference_fields
            and not high_thresholds
            and not low_thresholds
            else "resolved"
            if high_thresholds or low_thresholds
            else "not_configured"
        )
        return {
            "changed": bool(
                crossed_high_roles
                or crossed_low_roles
                or recovered_high_roles
                or recovered_low_roles
            )
            or abs(new_num - old_num) > deadband,
            "delta": new_num - old_num,
            "crossed_high": bool(crossed_high_roles),
            "crossed_low": bool(crossed_low_roles),
            "recovered_high": bool(recovered_high_roles),
            "recovered_low": bool(recovered_low_roles),
            "crossed_high_roles": crossed_high_roles,
            "crossed_low_roles": crossed_low_roles,
            "recovered_high_roles": recovered_high_roles,
            "recovered_low_roles": recovered_low_roles,
            "threshold_sources": threshold_sources,
            "missing_threshold_references": missing_threshold_references,
            "threshold_status": threshold_status,
            "reason": (
                "threshold_unknown"
                if threshold_status == "threshold_unknown"
                else policy.compare_mode
            ),
        }
    return {"changed": old != new, "reason": "fallback"}
