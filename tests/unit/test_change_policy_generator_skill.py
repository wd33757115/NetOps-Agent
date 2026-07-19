from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from src.core.patrol.change_policy import (
    compare_with_policy,
    infer_change_policy,
    policy_from_dict,
    validate_change_policy,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPT = (
    PROJECT_ROOT / "src" / "skills" / "change-policy-generator" / "scripts" / "run.py"
)


def _run_skill(tmp_path: Path, params: dict, *, expect_success: bool = True) -> dict:
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SKILL_SCRIPT), "--params", str(params_path)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if expect_success:
        assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


def _load_skill_module():
    spec = importlib.util.spec_from_file_location("change_policy_skill", SKILL_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_change_policy_infers_state_transition():
    policy = infer_change_policy(
        observation_type="interface.oper_status",
        field_name="status",
        field_semantics={"semantic_type": "state"},
    )

    assert policy.compare_mode == "state_transition"
    assert validate_change_policy(policy)["success"] is True
    assert compare_with_policy(policy, old="up", new="down")["changed"] is True
    assert compare_with_policy(policy, old="UP", new="up")["changed"] is False


def test_change_policy_infers_percent_threshold_and_deadband():
    policy = infer_change_policy(
        observation_type="device.cpu.utilization",
        field_name="cpu_5s",
        field_semantics={"semantic_type": "gauge", "unit": "percent"},
        historical_values=[20, 30, 50, 88],
    )

    assert policy.compare_mode == "threshold_and_deadband"
    assert policy.thresholds["high"] >= 90
    assert policy.recovery_threshold < policy.thresholds["high"]
    assert compare_with_policy(policy, old=20, new=23)["changed"] is False
    assert compare_with_policy(policy, old=20, new=95)["crossed_high"] is True


def test_change_policy_uses_per_observation_threshold_before_dataset_fallback():
    policy = policy_from_dict(
        {
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "compare_mode": "threshold_low_and_deadband",
            "thresholds": {"low": -17.0},
            "threshold_references": {
                "low": {"field_name": "rx_low_warning_dbm"}
            },
            "deadband": 2.0,
        }
    )

    result = compare_with_policy(
        policy,
        old=-11.0,
        new=-12.5,
        field="rx_power_dbm",
        context={"rx_low_warning_dbm": -12.0},
    )

    assert result["changed"] is True
    assert result["crossed_low_roles"] == ["low"]
    assert result["threshold_sources"] == {
        "low": "context:rx_low_warning_dbm"
    }


def test_optical_policy_without_structured_reference_does_not_invent_thresholds():
    policy = infer_change_policy(
        observation_type="interface.optical.rx_power",
        field_name="rx_power_dbm",
        field_semantics={"semantic_type": "gauge", "unit": "dbm"},
    )

    assert policy.compare_mode == "numeric_delta"
    assert policy.thresholds == {}
    assert policy.threshold_references == {}
    assert "threshold_status=threshold_unknown" in policy.reason


def test_missing_dynamic_threshold_does_not_use_profile_median_as_fallback():
    policy = policy_from_dict(
        {
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "compare_mode": "threshold_low_and_deadband",
            "thresholds": {"low": -17.0},
            "threshold_references": {
                "low": {"field_name": "rx_low_warning_dbm"}
            },
            "deadband": 2.0,
        }
    )

    result = compare_with_policy(
        policy,
        old=-11.0,
        new=-18.0,
        field="rx_power_dbm",
        context={},
    )

    assert result["threshold_status"] == "threshold_unknown"
    assert result["threshold_sources"] == {}
    assert result["missing_threshold_references"] == ["rx_low_warning_dbm"]
    assert result["crossed_low"] is False


def test_change_policy_rejects_counter_as_threshold_policy():
    policy = infer_change_policy(
        observation_type="interface.packet_count",
        field_name="packets_total",
        field_semantics={"semantic_type": "counter"},
    )

    assert policy.compare_mode == "counter_delta"
    assert validate_change_policy(policy)["success"] is True


def test_change_policy_generator_skill(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "mode": "infer_and_validate",
            "observation_type": "device.cpu.utilization",
            "field_name": "cpu_5s",
            "field_semantics": {"semantic_type": "gauge", "unit": "percent"},
            "historical_values": [10, 20, 30, 88],
            "agent_loop": True,
        },
    )

    assert result["success"] is True
    assert result["policy"]["compare_mode"] == "threshold_and_deadband"
    assert result["historical_stats"]["numeric_count"] == 4
    assert result["agent_loop"]["ready_for_approval"] is True
    attempt = result["agent_loop"]["attempts"][0]
    assert attempt["stage"] == "change_policy_generation"
    assert attempt["input_summary"]["historical_count"] == 4


def test_change_policy_generator_fans_out_all_contract_targets(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "mode": "infer_and_validate",
            "records": [
                {"interface": "GE1/0/1", "rx_power_dbm": -2.1, "tx_power_dbm": -1.8},
                {"interface": "GE1/0/2", "rx_power_dbm": -18.0, "tx_power_dbm": -2.0},
            ],
            "policy_targets": [
                {
                    "field_name": "rx_power_dbm",
                    "observation_type": "interface.optical.rx_power",
                    "field_semantics": {"semantic_type": "gauge", "unit": "dbm"},
                },
                {
                    "field_name": "tx_power_dbm",
                    "observation_type": "interface.optical.tx_power",
                    "field_semantics": {"semantic_type": "gauge", "unit": "dbm"},
                },
            ],
            "agent_loop": True,
        },
    )

    assert result["success"] is True
    assert result["policy_count"] == 2
    assert {item["observation_type"] for item in result["policies"]} == {
        "interface.optical.rx_power",
        "interface.optical.tx_power",
    }
    assert result["agent_loop"]["ready_for_approval"] is True


def test_change_policy_compiler_preserves_structured_threshold_references():
    module = _load_skill_module()
    compiled = module._compile_reference_policy_candidate(
        {
            "observation_type": "generic.measurement",
            "semantic_type": "gauge",
            "compare_mode": "numeric_delta",
            "thresholds": {},
            "deadband": 2,
            "reason": "historical distribution",
        },
        {
            "low": {
                "field_name": "low_reference",
                "context_key": "threshold.low",
                "level": "alarm",
                "source": "structured_reference",
                "field_profile": {"median": -12.0},
            },
            "high": {
                "field_name": "high_reference",
                "level": "alarm",
                "source": "structured_reference",
                "field_profile": {"median": 2.0},
            },
        },
    )

    assert compiled["compare_mode"] == "threshold_and_deadband"
    assert compiled["thresholds"] == {}
    assert compiled["allow_static_threshold_fallback"] is False
    assert compiled["recovery_threshold"] is None
    assert compiled["minimum_duration"] is None
    assert compiled["threshold_references"] == {
        "low": {
            "field_name": "threshold.low",
            "level": "alarm",
            "source": "structured_reference",
            "source_field_name": "low_reference",
        },
        "high": {
            "field_name": "high_reference",
            "level": "alarm",
            "source": "structured_reference",
        },
    }


def test_change_policy_compiler_supports_alarm_only_dynamic_reference():
    module = _load_skill_module()
    compiled = module._compile_reference_policy_candidate(
        {
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "compare_mode": "numeric_delta",
            "thresholds": {},
            "deadband": 2,
        },
        {
            "critical_low": {
                "field_name": "rx_low_alarm_dbm",
                "level": "alarm",
                "source": "structured_reference",
                "field_profile": {"median": -18.01},
            }
        },
    )

    assert compiled["compare_mode"] == "threshold_low_and_deadband"
    assert compiled["thresholds"] == {}
    assert compiled["source"] == "structured_threshold_reference"
    assert validate_change_policy(
        module.policy_from_dict(compiled)
    )["success"] is True
    assert module._validate_policy_constraints(
        module.policy_from_dict(compiled),
        {
            "require_structured_threshold_references": True,
            "required_threshold_reference_any": ["low", "critical_low"],
            "recovery_direction": "above",
            "minimum_hysteresis": 2,
        },
    ) == []


def test_change_policy_constraints_bound_recovery_threshold(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "mode": "validate",
            "proposal": {
                "observation_type": "interface.optical.rx_power",
                "semantic_type": "gauge",
                "compare_mode": "threshold_low_and_deadband",
                "thresholds": {"low": -17},
                "recovery_threshold": -14,
                "deadband": 1,
                "minimum_duration": "5m",
                "reason": "values likely represent an unplugged module",
            },
            "policy_constraints": {
                "allowed_compare_modes": ["threshold_low_and_deadband"],
                "recovery_bounds": {"min": -15, "max": -15},
                "deadband_bounds": {"min": 2, "max": 2},
                "allowed_minimum_durations": [None, "0s"],
                "evidence_only_reason": True,
            },
        },
        expect_success=False,
    )

    assert result["success"] is False
    assert "recovery_threshold exceeds the allowed maximum" in result["validation"][
        "errors"
    ]
    assert "deadband is below the allowed minimum" in result["validation"]["errors"]
    assert any(
        error.startswith("minimum_duration is not supported")
        for error in result["validation"]["errors"]
    )
    assert any(
        error.startswith("reason contains unsupported causal speculation")
        for error in result["validation"]["errors"]
    )
