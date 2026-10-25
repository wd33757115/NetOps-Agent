"""Current health from an approved CheckItem policy, independent of change events."""

from __future__ import annotations

import math
from typing import Any

from src.core.check_items.assessment_policy import canonical_digest


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip().rstrip("%"))
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def validate_health_policy(policy: Any) -> list[str]:
    if not isinstance(policy, dict):
        return ["health_policy must be an object"]
    errors = []
    mode = policy.get("baseline_mode", "absolute_relative")
    if mode == "trend_only":
        trend = policy.get("trend") or {}
        if not isinstance(trend, dict):
            return ["health_policy.trend must be an object"]
        if trend.get("kind") not in {"linear_power_drop", "utilization_increase"}:
            errors.append("health_policy.trend.kind is invalid")
        ratio = number(trend.get("threshold_ratio"))
        if ratio is None or not 0 < ratio <= 1:
            errors.append("health_policy.trend.threshold_ratio must be > 0 and <= 1")
        if trend.get("kind") == "utilization_increase":
            points = number(trend.get("min_increase_points"))
            if points is None or not 0 < points <= 100:
                errors.append("health_policy.trend.min_increase_points must be > 0 and <= 100")
        fields = trend.get("identity_fields", [])
        if not isinstance(trend.get("require_identity", True), bool):
            errors.append("health_policy.trend.require_identity must be boolean")
        if not isinstance(fields, list) or any(not isinstance(f, str) or not f for f in fields):
            errors.append("health_policy.trend.identity_fields must contain field names")
    elif mode == "device_limits":
        # Device limits alone decide health; no baseline is consulted.
        if policy.get("trend"):
            errors.append("health_policy.trend requires baseline_mode trend_only")
    elif mode == "absolute_relative":
        ratio = number(policy.get("max_relative_deviation"))
        if ratio is None or not 0 <= ratio <= 1:
            errors.append("health_policy.max_relative_deviation must be between 0 and 1")
    else:
        errors.append("health_policy.baseline_mode is invalid")
    limits = policy.get("device_thresholds")
    if not isinstance(limits, dict) or not limits or set(limits) - {"high", "low"}:
        errors.append("health_policy.device_thresholds requires high and/or low field lists")
    elif any(
        not isinstance(fields, list)
        or not fields
        or any(not isinstance(field, str) or not field.strip() for field in fields)
        for fields in limits.values()
    ):
        errors.append("health_policy threshold fields must be non-empty names")
    if policy.get("window") not in (None, "5m"):
        errors.append("health_policy.window must be 5m when required")
    return errors


def evaluate_numeric_health(observation: dict, policy: dict) -> dict:
    """Evaluate the frozen policy version; never replace missing measurement facts."""
    if policy.get("baseline_mode") in {"trend_only", "device_limits"} and not validate_health_policy(
        policy
    ):
        return _evaluate_threshold_health(observation, policy)
    context = dict(observation.get("context") or {})
    value = number(observation.get("value"))
    baseline = number(context.get("baseline_value"))
    missing, violations = [], []
    policy_errors = validate_health_policy(policy)
    if policy_errors:
        # Validation also runs at publication; guard corrupted legacy payloads at runtime.
        policy = {"max_relative_deviation": 0, "device_thresholds": {}}
        missing.append("health_policy_invalid")
    if (
        str(context.get("baseline_unit") or observation.get("unit") or "").lower()
        != str(observation.get("unit") or "").lower()
    ):
        missing.append("baseline_unit_mismatch")
        baseline = None
    required_window = policy.get("window")
    if required_window and (observation.get("dimensions") or {}).get("window") != required_window:
        missing.append("measurement_window_missing")
    if value is None:
        missing.append("current_value_missing")
    limits = {}
    for side, fields in (policy.get("device_thresholds") or {}).items():
        values = [(field, number(context.get(field))) for field in fields]
        available = [(field, val) for field, val in values if val is not None]
        if not available:
            missing.append(f"device_{side}_threshold_missing")
            continue
        # All supplied bounds apply. The strictest bound wins; do not invent one.
        field, limit = (min if side == "high" else max)(available, key=lambda item: item[1])
        limits[side] = {"field": field, "value": limit, "source": "device_output"}
        if value is not None and (
            (side == "high" and value > limit) or (side == "low" and value < limit)
        ):
            violations.append(f"device_{side}_threshold_exceeded")
    if not policy.get("device_thresholds"):
        missing.append("device_threshold_definition_missing")
    if "high" in limits and "low" in limits and limits["low"]["value"] > limits["high"]["value"]:
        missing.append("device_threshold_conflict")
    deviation = None
    if baseline is None or not context.get("baseline_observation_id"):
        missing.append("baseline_reference_missing")
    elif value is not None:
        if baseline == 0:
            if value == 0:
                deviation = 0.0
            else:
                missing.append("zero_baseline_relative_deviation_undefined")
        else:
            deviation = abs(value - baseline) / abs(baseline)
            if deviation > float(policy["max_relative_deviation"]) and not math.isclose(
                deviation, float(policy["max_relative_deviation"]), rel_tol=1e-12, abs_tol=1e-12
            ):
                violations.append("baseline_relative_deviation_exceeded")
    if any(
        code in missing
        for code in (
            "measurement_window_missing",
            "device_threshold_conflict",
            "health_policy_invalid",
        )
    ):
        violations = []
    return {
        "status": "abnormal" if violations else "unknown" if missing else "healthy",
        "complete": not missing,
        "missing": missing,
        "violations": violations,
        "value": value,
        "baseline_value": baseline,
        "relative_deviation": deviation,
        "max_relative_deviation": policy.get("max_relative_deviation"),
        "thresholds": limits,
        "baseline_observation_id": context.get("baseline_observation_id"),
        "baseline_snapshot_id": context.get("baseline_snapshot_id"),
    }


def _device_limits(value: float | None, context: dict, policy: dict) -> tuple[dict, list, list]:
    limits, missing, violations = {}, [], []
    for side, fields in policy["device_thresholds"].items():
        available = [(field, number(context.get(field))) for field in fields]
        available = [(field, v) for field, v in available if v is not None]
        if not available:
            missing.append(f"device_{side}_threshold_missing")
            continue
        field, bound = (min if side == "high" else max)(available, key=lambda pair: pair[1])
        limits[side] = {"field": field, "value": bound, "source": "device_output"}
        if value is not None and (
            (side == "high" and value > bound) or (side == "low" and value < bound)
        ):
            violations.append(f"device_{side}_threshold_exceeded")
    if "low" in limits and "high" in limits and limits["low"]["value"] > limits["high"]["value"]:
        missing.append("device_threshold_conflict")
    return limits, missing, violations


def _evaluate_threshold_health(observation: dict, policy: dict) -> dict:
    """Device limits determine health. A valid healthy baseline only informs trends."""
    context = dict(observation.get("context") or {})
    value, baseline = number(observation.get("value")), number(context.get("baseline_value"))
    limits, missing, violations = _device_limits(value, context, policy)
    if policy["baseline_mode"] == "device_limits":
        if value is None:
            missing.append("current_value_missing")
        if (
            policy.get("window")
            and (observation.get("dimensions") or {}).get("window") != policy["window"]
        ):
            missing.append("measurement_window_missing")
        if {"measurement_window_missing", "device_threshold_conflict"} & set(missing):
            violations = []
        return {
            "status": "abnormal" if violations else "unknown" if missing else "healthy",
            "complete": not missing,
            "missing": missing,
            "violations": violations,
            "value": value,
            "baseline_value": baseline,
            "relative_deviation": None,
            "max_relative_deviation": None,
            "baseline_mode": "device_limits",
            "trend": None,
            "thresholds": limits,
            "baseline_observation_id": context.get("baseline_observation_id"),
            "baseline_snapshot_id": context.get("baseline_snapshot_id"),
        }
    trend_policy = {
        **policy["trend"],
        "threshold_ratio": number(policy["trend"]["threshold_ratio"]),
        "min_increase_points": number(policy["trend"].get("min_increase_points")),
    }
    optical = trend_policy["kind"] == "linear_power_drop"
    unit = str(observation.get("unit") or "").lower()
    if unit not in ({"dbm"} if optical else {"percent", "%"}):
        missing.append("measurement_unit_mismatch")
    if value is None:
        missing.append("current_value_missing")
    elif not optical and not 0 <= value <= 100:
        missing.append("current_value_invalid")
    if (
        policy.get("window")
        and (observation.get("dimensions") or {}).get("window") != policy["window"]
    ):
        missing.append("measurement_window_missing")
    if any(
        code in missing
        for code in (
            "measurement_window_missing",
            "measurement_unit_mismatch",
            "current_value_invalid",
            "device_threshold_conflict",
        )
    ):
        violations = []
    status = "abnormal" if violations else "unknown" if missing else "healthy"
    trend = {
        "kind": trend_policy["kind"],
        "status": "unknown",
        "missing": [],
        "threshold_ratio": trend_policy["threshold_ratio"],
        "ratio": None,
        "delta": None,
        "baseline_status": "unknown",
        "thresholds": {},
    }
    if status == "unknown" or missing:
        trend["missing"].append("current_health_incomplete")
    if (
        baseline is None
        or not context.get("baseline_observation_id")
        or context.get("baseline_status") == "missing"
    ):
        trend["missing"].append("baseline_reference_missing")
    if str(context.get("baseline_unit") or "").lower() != unit:
        trend["missing"].append("baseline_unit_mismatch")
    prior_context = dict(context.get("baseline_context") or {})
    # Use the prior device limits, never reclassify a past sample with today's limits.
    prior_limits, prior_missing, prior_violations = _device_limits(baseline, prior_context, policy)
    trend["thresholds"] = prior_limits
    if prior_missing:
        trend["missing"].append("baseline_thresholds_missing")
    if baseline is not None and not optical and not 0 <= baseline <= 100:
        trend["missing"].append("baseline_value_invalid")
    for field in trend_policy.get("identity_fields", []):
        now_id, prior_id = context.get(field), prior_context.get(field)
        if (
            now_id is None
            or prior_id is None
            or not str(now_id).strip()
            or not str(prior_id).strip()
        ):
            trend.setdefault("unverified_identity_fields", []).append(field)
            if trend_policy.get("require_identity", True):
                trend["missing"].append("baseline_module_identity_missing")
        elif str(now_id).strip() != str(prior_id).strip():
            trend["missing"].append("baseline_module_identity_changed")
    trend["missing"] = list(dict.fromkeys(trend["missing"]))
    if not trend["missing"]:
        trend["baseline_status"] = "abnormal" if prior_violations else "healthy"
        trend["delta"] = value - baseline
        if prior_violations:
            # An abnormal reference is evidence of a prior fault, not a healthy target.
            trend["status"] = "recovered" if status == "healthy" else "persistent_abnormal"
        elif status == "abnormal":
            trend["status"] = "new_abnormal"
        else:
            if optical:
                # Only compute downward loss: bounded [0, 1], no overflow on large recovery.
                ratio = (
                    -math.expm1((value - baseline) * math.log(10) / 10) if value < baseline else 0.0
                )
                trend["ratio"] = ratio
                sufficient_points = True
            else:
                points = max(0.0, value - baseline)
                trend["increase_points"] = points
                trend["min_increase_points"] = trend_policy["min_increase_points"]
                if baseline == 0 and points > 0:
                    trend["missing"].append("zero_baseline_relative_deviation_undefined")
                    ratio = None
                else:
                    ratio = points / baseline if baseline else 0.0
                trend["ratio"] = ratio
                sufficient_points = points >= trend_policy["min_increase_points"]
            threshold = trend_policy["threshold_ratio"]
            exceeded = (
                ratio is not None
                and ratio > threshold
                and not math.isclose(ratio, threshold, rel_tol=1e-12, abs_tol=1e-12)
            )
            trend["status"] = (
                "unknown"
                if trend["missing"]
                else "notice"
                if exceeded and sufficient_points
                else "stable"
                if value == baseline
                else "power_increased"
                if optical and value > baseline
                else "load_decreased"
                if not optical and value < baseline
                else "within_tolerance"
            )
    return {
        "status": status,
        "complete": not missing,
        "missing": missing,
        "violations": violations,
        "value": value,
        "baseline_value": baseline,
        "relative_deviation": None,
        "max_relative_deviation": None,
        "baseline_mode": "trend_only",
        "trend": trend,
        "thresholds": limits,
        "baseline_observation_id": context.get("baseline_observation_id"),
        "baseline_snapshot_id": context.get("baseline_snapshot_id"),
    }


def policy_rule(observation: dict) -> dict | None:
    metadata = dict(observation.get("metadata") or observation.get("metadata_json") or {})
    semantics = dict(metadata.get("semantics") or {})
    policy = semantics.get("health_policy")
    if not isinstance(policy, dict):
        return None
    version_id = metadata.get("check_item_version_id")
    if not version_id:
        return None
    observation_type = observation.get("observation_type")
    digest = canonical_digest(
        {
            "check_item_version_id": version_id,
            "observation_type": observation_type,
            "policy": policy,
        }
    )
    return {
        "rule_id": f"{metadata.get('check_item_id')}.{observation_type}.health",
        "version_id": version_id,
        "digest": digest,
        "event_type": "NumericHealthAbnormal",
        "severity": "warning",
        "evaluation_scope": "current_state",
        "event_class": "fault",
        "source": "check_item_health_policy",
        "condition": {
            "kind": "numeric_health",
            "observation_types": [observation_type],
            "fields": [observation.get("field")],
            "health_policy": policy,
        },
    }
