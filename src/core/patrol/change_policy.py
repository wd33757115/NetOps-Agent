"""Deterministic ChangePolicy proposal and validation helpers."""

from __future__ import annotations

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
    recovery_threshold: float | None = None
    minimum_duration: str | None = None
    source: str = "deterministic_policy_generator"
    confidence: float = 0.85
    reason: str = ""

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
        )
    if semantic_type == "gauge":
        thresholds: dict[str, float] = {}
        recovery_threshold = None
        deadband = 0.0
        lowered = " ".join([field, observation_type, policy_intent or ""]).lower()
        if unit.lower() == "dbm" or any(
            token in lowered for token in ("optical", "rx_power", "tx_power", "dbm")
        ):
            thresholds["low"] = -17.0
            thresholds["critical_low"] = -20.0
            recovery_threshold = -15.0
            deadband = 2.0
        elif unit == "percent" or any(token in lowered for token in ("cpu", "memory", "usage")):
            p99 = stats.get("p99")
            high = max(90.0, float(p99 or 0))
            high = min(100.0, round(high, 2))
            recovery_threshold = max(0.0, round(high - 5.0, 2))
            thresholds["high"] = high
            deadband = 5.0
        return ChangePolicyProposal(
            observation_type=observation_type,
            semantic_type=semantic_type,
            compare_mode=(
                "threshold_low_and_deadband"
                if "low" in thresholds
                else "threshold_and_deadband"
                if thresholds
                else "numeric_delta"
            ),
            deadband=deadband,
            thresholds=thresholds,
            recovery_threshold=recovery_threshold,
            minimum_duration="0s",
            reason="; ".join(
                [
                    *reason_parts,
                    f"p95={stats.get('p95')}",
                    f"p99={stats.get('p99')}",
                ]
            ),
        )
    if semantic_type == "counter":
        return ChangePolicyProposal(
            observation_type=observation_type,
            semantic_type=semantic_type,
            compare_mode="counter_delta",
            reason="; ".join(reason_parts),
        )
    if semantic_type == "raw_hash":
        return ChangePolicyProposal(
            observation_type=observation_type,
            semantic_type=semantic_type,
            compare_mode="hash_diff",
            reason="; ".join(reason_parts),
        )
    if semantic_type == "enum":
        return ChangePolicyProposal(
            observation_type=observation_type,
            semantic_type=semantic_type,
            compare_mode="enum_transition",
            reason="; ".join(reason_parts),
        )
    return ChangePolicyProposal(
        observation_type=observation_type,
        semantic_type=semantic_type,
        compare_mode="exact",
        reason="; ".join(reason_parts),
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
        recovery_threshold=(
            float(payload["recovery_threshold"])
            if payload.get("recovery_threshold") is not None
            else None
        ),
        minimum_duration=payload.get("minimum_duration"),
        source=str(payload.get("source") or "provided"),
        confidence=float(payload.get("confidence") or 0.8),
        reason=str(payload.get("reason") or ""),
    )


def validate_change_policy(policy: ChangePolicyProposal) -> dict[str, Any]:
    errors: list[str] = []
    if not policy.observation_type:
        errors.append("observation_type is required")
    allowed_modes = {
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
    if policy.semantic_type in {"state", "enum", "text"} and policy.thresholds:
        errors.append("non-numeric semantic types must not define thresholds")
    if policy.semantic_type == "counter" and policy.compare_mode != "counter_delta":
        errors.append("counter semantic type must use counter_delta")
    if policy.compare_mode == "threshold_and_deadband":
        high = (policy.thresholds or {}).get("high")
        if high is None:
            errors.append("threshold_and_deadband requires thresholds.high")
        if policy.recovery_threshold is not None and high is not None:
            if policy.recovery_threshold >= high:
                errors.append("recovery_threshold must be lower than thresholds.high")
    if policy.compare_mode == "threshold_low_and_deadband":
        low = (policy.thresholds or {}).get("low")
        if low is None:
            errors.append("threshold_low_and_deadband requires thresholds.low")
        if policy.recovery_threshold is not None and low is not None:
            if policy.recovery_threshold <= low:
                errors.append("recovery_threshold must be higher than thresholds.low")
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
) -> dict[str, Any]:
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
        crossed_high = False
        crossed_low = False
        recovered_low = False
        high = (policy.thresholds or {}).get("high")
        if high is not None:
            crossed_high = old_num < high <= new_num
        low = (policy.thresholds or {}).get("low")
        recovery = policy.recovery_threshold
        if low is not None:
            crossed_low = old_num > low >= new_num
            if recovery is not None:
                recovered_low = old_num <= low and new_num >= float(recovery)
        return {
            "changed": crossed_high
            or crossed_low
            or recovered_low
            or abs(new_num - old_num) > deadband,
            "delta": new_num - old_num,
            "crossed_high": crossed_high,
            "crossed_low": crossed_low,
            "recovered_low": recovered_low,
            "reason": policy.compare_mode,
        }
    return {"changed": old != new, "reason": "fallback"}
