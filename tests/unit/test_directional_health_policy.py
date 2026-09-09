import json
import math
from copy import deepcopy

import pytest

from src.core.check_items.numeric_health import evaluate_numeric_health, validate_health_policy
from src.core.workflows.patrol_batch_service import evaluate_current_state

OPTICAL = {
    "baseline_mode": "trend_only",
    "device_thresholds": {"high": ["high"], "low": ["low"]},
    "trend": {
        "kind": "linear_power_drop",
        "threshold_ratio": 0.1,
        "identity_fields": ["part_number", "serial_number"],
    },
}
LOAD = {
    "baseline_mode": "trend_only",
    "window": "5m",
    "device_thresholds": {"high": ["high"]},
    "trend": {"kind": "utilization_increase", "threshold_ratio": 0.1, "min_increase_points": 10},
}


def sample(value=-2, baseline=-39, policy=None):
    policy = policy or OPTICAL
    optical = policy == OPTICAL
    ctx = {
        "high": 0 if optical else 80,
        "low": -20,
        "part_number": "optic",
        "serial_number": "serial",
    }
    return {
        "id": "current",
        "device_id": "sw",
        "snapshot_id": "snapshot",
        "entity_key": '["GE1/0/1","1"]',
        "entity_type": "interface" if optical else "cpu_scope",
        "command": "display transceiver diagnosis interface" if optical else "display cpu-usage",
        "field": "rx_power_dbm" if optical else "cpu_usage_percent",
        "observation_type": "interface.optical.rx_power" if optical else "device.cpu.utilization",
        "unit": "dBm" if optical else "percent",
        "dimensions": {} if optical else {"window": "5m"},
        "value": value,
        "context": {
            **ctx,
            "baseline_value": baseline,
            "baseline_observation_id": "prior",
            "baseline_unit": "dBm" if optical else "percent",
            "baseline_context": dict(ctx),
        },
        "metadata": {
            "check_item_id": "optic" if optical else "cpu",
            "check_item_version_id": "v2",
            "semantics": {"health_policy": deepcopy(policy)},
        },
    }


@pytest.mark.parametrize(
    "baseline,value,health,trend",
    [
        (-39, -2, "healthy", "recovered"),
        (-39, -39, "abnormal", "persistent_abnormal"),
        (-39, -21, "abnormal", "persistent_abnormal"),
        (-39, 1, "abnormal", "persistent_abnormal"),
        (-10, -21, "abnormal", "new_abnormal"),
        (-10, 1, "abnormal", "new_abnormal"),
        (-10, -11, "healthy", "notice"),
        (-10, -9, "healthy", "power_increased"),
        (-10, -10, "healthy", "stable"),
        (-10, -10.1, "healthy", "within_tolerance"),
        (-10, 0, "healthy", "power_increased"),
    ],
)
def test_optical_health_is_separate_from_directional_trend(baseline, value, health, trend):
    result = evaluate_numeric_health(sample(value, baseline), OPTICAL)
    assert result["status"] == health
    assert result["trend"]["status"] == trend
    assert "baseline_relative_deviation_exceeded" not in result["violations"]


def test_linear_power_loss_boundary_and_exact_ratio():
    r = evaluate_numeric_health(sample(-11, -10), OPTICAL)
    assert r["trend"]["ratio"] == pytest.approx(1 - 10 ** (-0.1))
    boundary = -10 + 10 * math.log10(0.9)
    assert (
        evaluate_numeric_health(sample(boundary, -10), OPTICAL)["trend"]["status"]
        == "within_tolerance"
    )
    extreme = evaluate_numeric_health(sample(1e300, -1e300), OPTICAL)
    json.dumps(extreme, allow_nan=False)
    assert extreme["status"] == "abnormal"


@pytest.mark.parametrize(
    "baseline,value,health,trend",
    [
        (90, 20, "healthy", "recovered"),
        (70, 20, "healthy", "load_decreased"),
        (20, 23, "healthy", "within_tolerance"),
        (20, 30, "healthy", "notice"),
        (70, 80, "healthy", "notice"),
        (70, 81, "abnormal", "new_abnormal"),
        (0, 0, "healthy", "stable"),
        (0, 20, "healthy", "unknown"),
    ],
)
def test_load_increase_requires_points_and_ratio(baseline, value, health, trend):
    result = evaluate_numeric_health(sample(value, baseline, LOAD), LOAD)
    assert result["status"] == health
    assert result["trend"]["status"] == trend


@pytest.mark.parametrize(
    "change", ["missing_baseline", "prior_limits", "unit", "replaced_module", "no_identity"]
)
def test_incomparable_baseline_does_not_block_current_health_or_claim_recovery(change):
    obs = sample()
    if change == "missing_baseline":
        obs["context"].pop("baseline_observation_id")
    elif change == "prior_limits":
        obs["context"]["baseline_context"].pop("low")
    elif change == "unit":
        obs["context"]["baseline_unit"] = "mW"
    elif change == "replaced_module":
        obs["context"]["serial_number"] = "new-module"
    else:
        obs["context"].pop("serial_number")
    result = evaluate_numeric_health(obs, OPTICAL)
    assert result["status"] == "healthy"
    assert result["trend"]["status"] == "unknown"
    assert result["trend"]["missing"]
    full = evaluate_current_state([obs], [], rule_source="test")
    assert full["finding_count"] == 0
    assert full["partial_threshold_observation_count"] == 0


def test_missing_window_and_current_limits_stay_unknown():
    obs = sample(20, 20, LOAD)
    obs["dimensions"]["window"] = "instant"
    assert evaluate_numeric_health(obs, LOAD)["status"] == "unknown"
    obs = sample()
    obs["context"].pop("low")
    assert evaluate_numeric_health(obs, OPTICAL)["status"] == "unknown"


def test_baseline_uses_its_own_device_limits():
    obs = sample(-2, -10)
    obs["context"]["baseline_context"]["low"] = -9
    assert evaluate_numeric_health(obs, OPTICAL)["trend"]["status"] == "recovered"


def test_trend_notice_is_auditable_but_not_a_current_fault():
    result = evaluate_current_state([sample(-11, -10)], [], rule_source="test")
    assert result["finding_count"] == 0
    assert result["evaluated_observation_count"] == 1
    basis = result["assessment_basis"]["entries"][0]
    assert basis["health_assessment"]["trend"]["status"] == "notice"
    assert basis["baseline_binding"]["baseline_observation_id"] == "prior"


def test_invalid_trend_policy_rejected():
    for value in (None, -1, 0, float("inf")):
        policy = deepcopy(OPTICAL)
        policy["trend"]["threshold_ratio"] = value
        assert validate_health_policy(policy)


def test_report_keeps_trend_notices_out_of_risks_and_exports_explanation():
    from src.core.patrol.leadership_report import build_leadership_report
    from src.core.patrol.report_view import project_report

    assessed = evaluate_current_state([sample(-11, -10)], [], rule_source="test")
    summary = {
        "current_state_rule_coverage": assessed,
        "current_findings": [],
        "events": [],
        "changes": [],
        "runs": [],
    }
    report = build_leadership_report("job", summary)
    assert report["risk_summary"]["total"] == 0
    assert report["numeric_trends"]["notice_count"] == 1
    view = project_report(report)
    assert "20.57%" in view["scalars"]["changes.summary"]
    assert "不计入当前故障数" in view["scalars"]["changes.summary"]


def test_interface_comparison_discloses_missing_identity_but_blocks_known_replacement():
    policy = deepcopy(OPTICAL)
    policy["trend"]["require_identity"] = False
    obs = sample()
    obs["context"].pop("serial_number")
    health = evaluate_numeric_health(obs, policy)
    assert health["trend"]["status"] == "recovered"
    assert health["trend"]["unverified_identity_fields"] == ["serial_number"]
    obs["context"]["serial_number"] = "replacement"
    health = evaluate_numeric_health(obs, policy)
    assert health["trend"]["status"] == "unknown"
    assert "baseline_module_identity_changed" in health["trend"]["missing"]
