from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.core.patrol.event_rule_generation import generate_and_validate_event_rules


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPT = (
    PROJECT_ROOT / "src" / "skills" / "event-rule-generator" / "scripts" / "run.py"
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


def test_event_rule_generator_builds_interface_rules():
    result = generate_and_validate_event_rules(
        contract={
            "entity_type": "interface_l3",
            "required_fields": ["interface", "status", "protocol"],
            "primary_keys": ["interface"],
            "state_semantics": {
                "interface": {"semantic_type": "identity"},
                "protocol": {"semantic_type": "state"},
            },
        },
        change_policy={
            "observation_type": "interface.oper_status",
            "semantic_type": "state",
            "compare_mode": "state_transition",
        },
    )

    assert result["success"] is True
    assert {rule["event_type"] for rule in result["rules"]} == {
        "InterfaceDown",
        "InterfaceUp",
    }


def test_event_rule_generator_builds_cpu_threshold_rules():
    result = generate_and_validate_event_rules(
        contract={
            "entity_type": "cpu",
            "required_fields": ["slot", "cpu_5s"],
            "primary_keys": ["slot"],
            "state_semantics": {
                "slot": {"semantic_type": "identity"},
                "cpu_5s": {"semantic_type": "gauge", "unit": "percent"},
            },
        },
        change_policy={
            "observation_type": "device.cpu.utilization",
            "semantic_type": "gauge",
            "compare_mode": "threshold_and_deadband",
            "thresholds": {"high": 90},
            "recovery_threshold": 85,
        },
    )

    assert result["success"] is True
    assert {rule["event_type"] for rule in result["rules"]} == {
        "CPUHigh",
        "CPURecovered",
    }


def test_event_rule_generator_builds_optical_low_threshold_candidates():
    contract = {
        "entity_type": "interface_optical",
        "required_fields": ["interface"],
        "optional_fields": ["rx_power_dbm", "tx_power_dbm"],
        "primary_keys": ["interface"],
        "state_semantics": {
            "interface": {"semantic_type": "identity"},
            "rx_power_dbm": {
                "semantic_type": "gauge",
                "observation_type": "interface.optical.rx_power",
                "unit": "dbm",
            },
            "tx_power_dbm": {
                "semantic_type": "gauge",
                "observation_type": "interface.optical.tx_power",
                "unit": "dbm",
            },
        },
    }
    rx_result = generate_and_validate_event_rules(
        contract={
            **contract,
        },
        change_policy={
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "compare_mode": "threshold_low_and_deadband",
            "thresholds": {"low": -17, "critical_low": -20},
            "recovery_threshold": -15,
            "deadband": 2,
        },
    )
    tx_result = generate_and_validate_event_rules(
        contract=contract,
        change_policy={
            "observation_type": "interface.optical.tx_power",
            "semantic_type": "gauge",
            "compare_mode": "threshold_low_and_deadband",
            "thresholds": {"low": -17, "critical_low": -20},
            "recovery_threshold": -15,
            "deadband": 2,
        },
    )

    assert rx_result["success"] is True
    assert tx_result["success"] is True
    assert {rule["event_type"] for rule in rx_result["rules"]} == {
        "OpticalRxPowerLow",
        "OpticalRecovered",
    }
    assert {rule["event_type"] for rule in tx_result["rules"]} == {
        "OpticalTxPowerLow",
        "OpticalRecovered",
    }
    assert {rule["condition"]["kind"] for rule in [*rx_result["rules"], *tx_result["rules"]]} == {
        "threshold_lte",
        "threshold_low_recovery",
    }


def test_event_rule_generator_does_not_classify_unknown_gauge_as_cpu():
    result = generate_and_validate_event_rules(
        contract={
            "entity_type": "temperature_sensor",
            "required_fields": ["sensor", "temperature"],
            "primary_keys": ["sensor"],
            "state_semantics": {
                "sensor": {"semantic_type": "identity"},
                "temperature": {"semantic_type": "gauge", "unit": "celsius"},
            },
        },
        change_policy={
            "observation_type": "environment.temperature",
            "semantic_type": "gauge",
            "compare_mode": "threshold_and_deadband",
            "thresholds": {"high": 80},
            "recovery_threshold": 70,
        },
    )

    assert result["success"] is False
    assert result["generated_rule_count"] == 0
    assert result["rules"] == []


def test_event_rule_generator_skill(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "contract": {
                "entity_type": "fan",
                "required_fields": ["fan", "state"],
                "primary_keys": ["fan"],
                "state_semantics": {
                    "fan": {"semantic_type": "identity"},
                    "state": {"semantic_type": "state"},
                },
            },
            "change_policy": {
                "observation_type": "fan.state",
                "semantic_type": "state",
                "compare_mode": "state_transition",
            },
            "agent_loop": True,
        },
    )

    assert result["success"] is True
    assert result["rules"][0]["event_type"] == "FanFailed"
    assert result["reports"][0]["validation"]["success"] is True
    assert result["agent_loop"]["ready_for_approval"] is True
    attempt = result["agent_loop"]["attempts"][0]
    assert attempt["stage"] == "event_rule_generation"
    assert attempt["output_summary"]["generated_rule_count"] >= 1
