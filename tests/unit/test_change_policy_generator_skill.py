from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.core.patrol.change_policy import (
    compare_with_policy,
    infer_change_policy,
    validate_change_policy,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPT = (
    PROJECT_ROOT / "src" / "skills" / "change-policy-generator" / "scripts" / "run.py"
)


def _run_skill(tmp_path: Path, params: dict) -> dict:
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
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


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
