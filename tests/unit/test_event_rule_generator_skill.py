from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from src.core.patrol.event_rule_compiler import compile_event_rule_intents
from src.core.patrol.event_rule_generation import generate_and_validate_event_rules

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPT = PROJECT_ROOT / "src" / "skills" / "event-rule-generator" / "scripts" / "run.py"


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
    spec = importlib.util.spec_from_file_location("event_rule_skill", SKILL_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fanout_scopes_current_state_only_requirement_to_one_observation():
    module = _load_skill_module()
    scoped = module._scoped_rule_asset_requirements(
        {
            "schema_version": "rule-asset-requirements/v1",
            "requires_observation_contract": True,
            "requires_change_policy": True,
            "requires_current_state_rule": True,
            "requires_event_rule": False,
            "requires_event_rule_assets": True,
            "requires_historical_replay": False,
            "current_state_observation_types": [
                "environment.power.state",
                "environment.fan.state",
            ],
            "change_policy_observation_types": [
                "environment.power.state",
                "environment.fan.state",
            ],
            "required_event_types_by_observation": {
                "environment.power.state": [
                    "PowerFault",
                    "PowerRecovered",
                ]
            },
        },
        target={"required_rule_roles": ["current_state"]},
        observation_type="environment.power.state",
    )

    assert scoped["requires_current_state_rule"] is True
    assert scoped["requires_event_rule"] is False
    assert scoped["requires_event_rule_assets"] is True
    assert scoped["requires_historical_replay"] is False
    assert scoped["current_state_observation_types"] == [
        "environment.power.state"
    ]
    assert scoped["change_policy_observation_types"] == [
        "environment.power.state"
    ]
    assert scoped["required_event_types_by_observation"] == {}


def test_fanout_scopes_recovery_constraints_out_of_current_state_target():
    module = _load_skill_module()
    constraints = {
        "role_event_types": {
            "high": "CpuUtilizationHigh",
            "high_recovery": "CpuUtilizationRecovered",
        },
        "required_event_types": [
            "CpuUtilizationHigh",
            "CpuUtilizationRecovered",
        ],
        "required_event_classes": {
            "CpuUtilizationHigh": "fault",
            "CpuUtilizationRecovered": "recovery",
        },
        "required_severities": {
            "CpuUtilizationHigh": "warning",
            "CpuUtilizationRecovered": "info",
        },
        "required_recovery_pairs": [
            {
                "fault": "CpuUtilizationHigh",
                "recovery": "CpuUtilizationRecovered",
            }
        ],
        "max_rule_count": 2,
    }

    scoped = module._scoped_event_constraints(
        constraints,
        target={"required_rule_roles": ["current_state"]},
    )

    assert scoped["role_event_types"] == {
        "high": "CpuUtilizationHigh"
    }
    assert scoped["required_event_types"] == ["CpuUtilizationHigh"]
    assert scoped["required_event_classes"] == {
        "CpuUtilizationHigh": "fault"
    }
    assert scoped["required_severities"] == {
        "CpuUtilizationHigh": "warning"
    }
    assert scoped["required_recovery_pairs"] == []
    assert scoped["max_rule_count"] == 1
    assert module._scoped_event_constraints(
        constraints,
        target={"required_rule_roles": ["current_state", "change_event"]},
    ) == constraints


def test_current_state_numeric_rule_does_not_require_recovery():
    module = _load_skill_module()
    result = module._validate_llm_rules(
        {
            "rules": [
                {
                    "name": "memory-high",
                    "event_type": "MemoryHigh",
                    "trigger_role": "high",
                    "severity": "major",
                }
            ]
        },
        contract={
            "entity_type": "memory_scope",
            "canonical_entity_type": "memory_scope",
            "primary_keys": [],
            "required_fields": ["memory_usage_percent"],
            "optional_fields": [],
            "state_semantics": {
                "memory_usage_percent": {
                    "semantic_type": "gauge",
                    "observation_type": "device.memory.utilization",
                }
            },
        },
        change_policy={
            "observation_type": "device.memory.utilization",
            "semantic_type": "gauge",
            "compare_mode": "threshold_and_deadband",
            "thresholds": {"high": 90},
            "recovery_threshold": 85,
            "deadband": 5,
        },
        rule_asset_requirements={
            "schema_version": "rule-asset-requirements/v1",
            "requires_observation_contract": True,
            "requires_change_policy": True,
            "requires_current_state_rule": True,
            "requires_event_rule": False,
            "requires_event_rule_assets": True,
            "requires_historical_replay": False,
            "current_state_observation_types": [
                "device.memory.utilization"
            ],
            "change_policy_observation_types": [
                "device.memory.utilization"
            ],
        },
    )

    assert result["success"] is True
    assert result["generated_rule_count"] == 1
    assert result["rules"][0]["trigger_role"] == "high"
    assert result["rules"][0]["evaluation_scope"] == "current_state"


def test_fanout_fails_closed_when_required_current_state_target_is_missing():
    module = _load_skill_module()
    result = module.run_event_rule_generation(
        {
            "contract": {
                "entity_type": "fan",
                "primary_keys": ["fan"],
                "required_fields": ["fan", "fan_status"],
                "optional_fields": [],
                "state_semantics": {
                    "fan": {"semantic_type": "identity"},
                    "fan_status": {
                        "semantic_type": "state",
                        "observation_type": "environment.fan.state",
                    },
                },
            },
            "change_policies": [
                {
                    "observation_type": "environment.fan.state",
                    "semantic_type": "state",
                    "compare_mode": "enum_transition",
                }
            ],
            "event_policy_targets": [],
            "event_observation_types": [],
            "rule_asset_requirements": {
                "schema_version": "rule-asset-requirements/v1",
                "requires_observation_contract": True,
                "requires_change_policy": True,
                "requires_current_state_rule": True,
                "requires_event_rule": False,
                "requires_event_rule_assets": True,
                "requires_historical_replay": False,
                "current_state_observation_types": ["environment.fan.state"],
                "change_policy_observation_types": ["environment.fan.state"],
            },
        }
    )

    assert result["success"] is False
    assert result["generated_rule_count"] == 0
    assert result["agent_loop"]["ready_for_approval"] is False
    assert "bundle requires a current-state EventRule role" in result["validation"]["errors"]
    assert (
        "bundle requires current-state rule coverage for environment.fan.state"
        in result["validation"]["errors"]
    )


def test_llm_transition_intent_compiles_to_current_state_only_fault_rule():
    module = _load_skill_module()
    result = module._validate_llm_rules(
        {
            "rules": [
                {
                    "name": "environment-fan-status-fault",
                    "event_type": "environment_fan_status_abnormal",
                    "trigger_role": "down",
                    "severity": "major",
                    "event_class": "fault",
                    "evaluation_scope": "both",
                    "condition": {
                        "kind": "transition",
                        "entity_types": ["environment_fan"],
                        "fields": ["fan_status"],
                        "from_group": "up",
                        "to_group": "down",
                        "allow_initial_target": False,
                    },
                }
            ]
        },
        contract={
            "entity_type": "fan",
            "canonical_entity_type": "environment_fan",
            "primary_keys": ["fan"],
            "required_fields": ["fan", "fan_status"],
            "optional_fields": [],
            "state_semantics": {
                "fan": {"semantic_type": "identity"},
                "fan_status": {
                    "semantic_type": "state",
                    "observation_type": "environment.fan.state",
                },
            },
        },
        change_policy={
            "observation_type": "environment.fan.state",
            "semantic_type": "state",
            "compare_mode": "enum_transition",
        },
        rule_asset_requirements={
            "schema_version": "rule-asset-requirements/v1",
            "requires_observation_contract": True,
            "requires_change_policy": True,
            "requires_current_state_rule": True,
            "requires_event_rule": False,
            "requires_event_rule_assets": True,
            "requires_historical_replay": False,
            "current_state_observation_types": ["environment.fan.state"],
            "change_policy_observation_types": ["environment.fan.state"],
        },
    )

    assert result["success"] is True
    assert result["generated_rule_count"] == 1
    assert result["rules"][0]["event_class"] == "fault"
    assert result["rules"][0]["evaluation_scope"] == "current_state"
    assert result["rules"][0]["condition"]["kind"] == "entity_status_down"
    assert result["rules"][0]["condition"]["observation_types"] == [
        "environment.fan.state"
    ]
    assert any(
        item.get("kind") == "current_state_profile_compiled"
        for item in result["normalizations"]
    )


def test_event_rule_compiler_does_not_elevate_alarm_role_severity():
    compiled = compile_event_rule_intents(
        candidate_rules=[
            {
                "event_type": "OpticalRxPowerAlarm",
                "trigger_role": "critical_low",
                "severity": "warning",
            }
        ],
        contract={
            "entity_type": "interface_optical",
            "primary_keys": ["interface"],
            "required_fields": ["interface"],
            "optional_fields": ["rx_power_dbm", "rx_low_alarm_dbm"],
            "state_semantics": {
                "rx_power_dbm": {
                    "semantic_type": "gauge",
                    "observation_type": "interface.optical.rx_power",
                }
            },
        },
        change_policy={
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "compare_mode": "threshold_low_and_deadband",
            "thresholds": {"critical_low": -18.01},
            "threshold_references": {"critical_low": {"field_name": "rx_low_alarm_dbm"}},
            "deadband": 2,
        },
    )

    assert compiled["success"] is True
    assert [rule["trigger_role"] for rule in compiled["rules"]] == [
        "critical_low",
        "low_recovery",
    ]
    fault, recovery = compiled["rules"]
    assert fault["severity"] == "warning"
    assert fault["condition"]["reference_field"] == "rx_low_alarm_dbm"
    assert recovery["condition"] == {
        "kind": "threshold_field_low_recovery",
        "threshold_role": "critical_low",
        "observation_types": ["interface.optical.rx_power"],
        "entity_types": ["interface_optical"],
        "fields": ["rx_power_dbm"],
        "reference_field": "rx_low_alarm_dbm",
        "hysteresis": 2.0,
    }
    assert recovery["recovery"]["resolves"] == ["OpticalRxPowerAlarm"]


def test_event_rule_compiler_governs_synthesized_critical_role():
    compiled = compile_event_rule_intents(
        candidate_rules=[],
        contract={
            "entity_type": "interface_optical",
            "primary_keys": ["interface"],
            "required_fields": ["interface"],
            "optional_fields": ["rx_power_dbm", "rx_low_alarm_dbm"],
            "state_semantics": {
                "rx_power_dbm": {
                    "semantic_type": "gauge",
                    "observation_type": "interface.optical.rx_power",
                }
            },
        },
        change_policy={
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "compare_mode": "threshold_low_and_deadband",
            "threshold_references": {"critical_low": {"field_name": "rx_low_alarm_dbm"}},
            "deadband": 2,
        },
    )

    fault = next(rule for rule in compiled["rules"] if rule["event_class"] == "fault")
    assert fault["severity"] == "major"
    repair = next(
        item for item in compiled["normalizations"] if item["kind"] == "severity_governance"
    )
    assert repair["trigger_role"] == "critical_low"
    assert repair["proposed"] == "critical"
    assert repair["effective"] == "major"


def test_event_rule_compiler_adds_alarm_fallback_to_warning_recovery():
    compiled = compile_event_rule_intents(
        candidate_rules=[
            {
                "event_type": "OpticalRxPowerLow",
                "trigger_role": "low",
                "severity": "warning",
            },
            {
                "event_type": "OpticalRxPowerCritical",
                "trigger_role": "critical_low",
                "severity": "critical",
            },
        ],
        contract={
            "entity_type": "interface_optical",
            "primary_keys": ["interface"],
            "required_fields": ["interface"],
            "optional_fields": [
                "rx_power_dbm",
                "rx_low_warning_dbm",
                "rx_low_alarm_dbm",
            ],
            "state_semantics": {
                "rx_power_dbm": {
                    "semantic_type": "gauge",
                    "observation_type": "interface.optical.rx_power",
                }
            },
        },
        change_policy={
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "compare_mode": "threshold_low_and_deadband",
            "thresholds": {"low": -15.0, "critical_low": -18.01},
            "threshold_references": {
                "low": {"field_name": "rx_low_warning_dbm"},
                "critical_low": {"field_name": "rx_low_alarm_dbm"},
            },
            "deadband": 2,
        },
    )

    recovery = next(rule for rule in compiled["rules"] if rule["trigger_role"] == "low_recovery")
    assert recovery["condition"]["reference_field"] == "rx_low_warning_dbm"
    assert recovery["condition"]["fallback_reference_fields"] == ["rx_low_alarm_dbm"]


def test_event_rule_compiler_repairs_directionally_wrong_event_type():
    compiled = compile_event_rule_intents(
        candidate_rules=[
            {
                "name": "measurement-critical-high",
                "event_type": "measurement_critical_high",
                "trigger_role": "critical_high",
            },
            {
                "name": "measurement-critical-low",
                "event_type": "measurement_critical_high",
                "trigger_role": "critical_low",
            },
        ],
        contract={
            "entity_type": "measurement_entity",
            "primary_keys": ["sensor"],
            "required_fields": ["sensor"],
            "optional_fields": ["measurement", "low_alarm", "high_alarm"],
            "state_semantics": {
                "measurement": {
                    "semantic_type": "gauge",
                    "observation_type": "generic.measurement",
                }
            },
        },
        change_policy={
            "observation_type": "generic.measurement",
            "semantic_type": "gauge",
            "compare_mode": "threshold_and_deadband",
            "threshold_references": {
                "critical_low": {"field_name": "low_alarm"},
                "critical_high": {"field_name": "high_alarm"},
            },
            "deadband": 1,
        },
    )

    by_role = {rule["trigger_role"]: rule for rule in compiled["rules"]}
    assert by_role["critical_high"]["event_type"] == "measurement_critical_high"
    assert by_role["critical_low"]["event_type"] == ("generic_measurement_critical_low")
    assert by_role["low_recovery"]["recovery"]["resolves"] == ["generic_measurement_critical_low"]
    repair = next(
        item for item in compiled["normalizations"] if item["kind"] == "event_type_governance"
    )
    assert repair["trigger_role"] == "critical_low"
    assert repair["inferred_role"] == "critical_high"
    assert repair["reason"] == "event_type_direction_conflicts_with_trigger_role"


def test_event_rule_compiler_repairs_neutral_event_type_reused_across_roles():
    compiled = compile_event_rule_intents(
        candidate_rules=[
            {
                "event_type": "MeasurementFault",
                "trigger_role": "high",
            },
            {
                "event_type": "MeasurementFault",
                "trigger_role": "low",
            },
        ],
        contract={
            "entity_type": "measurement_entity",
            "primary_keys": ["sensor"],
            "required_fields": ["sensor"],
            "optional_fields": ["measurement"],
            "state_semantics": {
                "measurement": {
                    "semantic_type": "gauge",
                    "observation_type": "generic.measurement",
                }
            },
        },
        change_policy={
            "observation_type": "generic.measurement",
            "semantic_type": "gauge",
            "compare_mode": "threshold_and_deadband",
            "thresholds": {"low": 10, "high": 90},
            "deadband": 2,
        },
    )

    by_role = {rule["trigger_role"]: rule for rule in compiled["rules"]}
    assert by_role["high"]["event_type"] == "MeasurementFault"
    assert by_role["low"]["event_type"] == "generic_measurement_low"
    assert any(
        item.get("reason") == "event_type_reused_by_another_trigger_role"
        for item in compiled["normalizations"]
    )


def test_event_rule_validator_exposes_event_type_governance_to_agent_loop():
    module = _load_skill_module()
    result = module._validate_llm_rules(
        {
            "rules": [
                {
                    "event_type": "measurement_critical_high",
                    "trigger_role": "critical_low",
                }
            ]
        },
        contract={
            "entity_type": "measurement_entity",
            "primary_keys": ["sensor"],
            "required_fields": ["sensor"],
            "optional_fields": ["measurement", "low_alarm"],
            "state_semantics": {
                "measurement": {
                    "semantic_type": "gauge",
                    "observation_type": "generic.measurement",
                }
            },
        },
        change_policy={
            "observation_type": "generic.measurement",
            "semantic_type": "gauge",
            "compare_mode": "threshold_low_and_deadband",
            "threshold_references": {
                "critical_low": {"field_name": "low_alarm"},
            },
            "deadband": 1,
        },
    )

    assert result["success"] is True
    assert any(
        repair.get("kind") == "event_type_governance"
        and repair.get("trigger_role") == "critical_low"
        for repair in result["governance_repairs"]
    )


def test_generic_generator_does_not_recreate_retired_interface_rules():
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

    assert result["success"] is False
    assert result["rules"] == []


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
    applicability = {
        "version": 1,
        "on_missing_context": "reject",
        "exclude_when": [
            {
                "code": "transceiver_absent",
                "when": {
                    "field": "transceiver_present",
                    "op": "in_ci",
                    "values": ["absent"],
                },
            }
        ],
    }
    contract = {
        "entity_type": "interface_optical",
        "required_fields": ["interface"],
        "optional_fields": [
            "transceiver_present",
            "rx_power_dbm",
            "tx_power_dbm",
        ],
        "primary_keys": ["interface"],
        "state_semantics": {
            "interface": {"semantic_type": "identity"},
            "rx_power_dbm": {
                "semantic_type": "gauge",
                "observation_type": "interface.optical.rx_power",
                "unit": "dbm",
                "applicability": applicability,
            },
            "tx_power_dbm": {
                "semantic_type": "gauge",
                "observation_type": "interface.optical.tx_power",
                "unit": "dbm",
                "applicability": applicability,
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
        "OpticalRxPowerCritical",
        "OpticalRxPowerRecovered",
    }
    assert {rule["event_type"] for rule in tx_result["rules"]} == {
        "OpticalTxPowerLow",
        "OpticalTxPowerCritical",
        "OpticalTxPowerRecovered",
    }
    assert {rule["condition"]["kind"] for rule in [*rx_result["rules"], *tx_result["rules"]]} == {
        "threshold_lte",
        "threshold_low_recovery",
    }
    assert all(
        rule["condition"]["applicability"] == applicability
        for rule in [*rx_result["rules"], *tx_result["rules"]]
    )


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


def test_builtin_optical_rules_bind_to_per_observation_alarm_threshold():
    result = generate_and_validate_event_rules(
        contract={
            "entity_type": "interface_optical",
            "required_fields": ["interface"],
            "optional_fields": ["rx_power_dbm", "rx_low_alarm_dbm"],
            "primary_keys": ["interface"],
            "state_semantics": {
                "interface": {"semantic_type": "identity"},
                "rx_power_dbm": {
                    "semantic_type": "gauge",
                    "observation_type": "interface.optical.rx_power",
                    "unit": "dbm",
                },
            },
        },
        change_policy={
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "compare_mode": "threshold_low_and_deadband",
            "thresholds": {},
            "threshold_references": {
                "critical_low": {
                    "field_name": "rx_low_alarm_dbm",
                    "level": "alarm",
                }
            },
            "deadband": 2,
        },
    )

    assert result["success"] is True
    assert {rule["event_type"] for rule in result["rules"]} == {
        "OpticalRxPowerCritical",
        "OpticalRxPowerRecovered",
    }
    fault = next(rule for rule in result["rules"] if rule["event_class"] == "fault")
    recovery = next(rule for rule in result["rules"] if rule["event_class"] == "recovery")
    assert fault["condition"] == {
        "kind": "threshold_field_lte",
        "threshold_role": "critical_low",
        "observation_types": ["interface.optical.rx_power"],
        "entity_types": ["interface_optical"],
        "fields": ["rx_power_dbm"],
        "reference_field": "rx_low_alarm_dbm",
    }
    assert recovery["condition"]["kind"] == "threshold_field_low_recovery"
    assert recovery["condition"]["reference_field"] == "rx_low_alarm_dbm"
    assert recovery["recovery"]["resolves"] == ["OpticalRxPowerCritical"]


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


def test_event_rule_generator_fans_out_multiple_change_policies(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "contract": {
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
            },
            "change_policies": [
                {
                    "observation_type": "interface.optical.rx_power",
                    "semantic_type": "gauge",
                    "compare_mode": "threshold_low_and_deadband",
                    "thresholds": {"low": -17, "critical_low": -20},
                    "recovery_threshold": -15,
                    "deadband": 2,
                },
                {
                    "observation_type": "interface.optical.tx_power",
                    "semantic_type": "gauge",
                    "compare_mode": "threshold_low_and_deadband",
                    "thresholds": {"low": -17, "critical_low": -20},
                    "recovery_threshold": -15,
                    "deadband": 2,
                },
            ],
            "agent_loop": True,
        },
    )

    assert result["success"] is True
    assert result["generated_rule_count"] == 6
    assert {item["event_type"] for item in result["rules"]} == {
        "OpticalRxPowerLow",
        "OpticalRxPowerCritical",
        "OpticalRxPowerRecovered",
        "OpticalTxPowerLow",
        "OpticalTxPowerCritical",
        "OpticalTxPowerRecovered",
    }


def test_event_rule_fanout_uses_target_contract_for_multi_record_parent(
    tmp_path: Path,
):
    target_contract = {
        "entity_type": "single_channel_diagnostic",
        "canonical_entity_type": "interface_optical",
        "required_fields": ["interface_name", "rx_power"],
        "optional_fields": [],
        "primary_keys": ["interface_name"],
        "state_semantics": {
            "interface_name": {"semantic_type": "identity"},
            "rx_power": {
                "semantic_type": "gauge",
                "observation_type": "interface.optical.rx_power",
                "unit": "dbm",
            },
        },
    }
    result = _run_skill(
        tmp_path,
        {
            "contract": {
                "entity_type": "multi_record",
                "required_fields": [],
                "optional_fields": [],
                "primary_keys": [],
                "state_semantics": {},
                "record_sets": [target_contract],
            },
            "change_policies": [
                {
                    "observation_type": "interface.optical.rx_power",
                    "semantic_type": "gauge",
                    "compare_mode": "threshold_low_and_deadband",
                    "threshold_references": {
                        "critical_low": {
                            "field_name": "threshold.critical_low"
                        }
                    },
                    "deadband": 2,
                }
            ],
            "event_policy_targets": [
                {
                    "observation_type": "interface.optical.rx_power",
                    "dimension_selector": {},
                    "event_contract": target_contract,
                }
            ],
        },
    )

    assert result["success"] is True
    assert {item["event_type"] for item in result["rules"]} == {
        "OpticalRxPowerCritical",
        "OpticalRxPowerRecovered",
    }
    assert all(
        item["condition"]["entity_types"] == ["interface_optical"]
        for item in result["rules"]
    )


def test_event_rule_generator_scopes_same_observation_type_by_dimensions(
    tmp_path: Path,
):
    result = _run_skill(
        tmp_path,
        {
            "contract": {
                "entity_type": "device",
                "required_fields": ["device_name"],
                "optional_fields": ["cpu_usage"],
                "primary_keys": [],
                "state_semantics": {
                    "device_name": {"semantic_type": "identity"},
                    "cpu_usage": {
                        "semantic_type": "gauge",
                        "observation_type": "device.cpu.utilization",
                        "unit": "percent",
                    },
                },
            },
            "change_policies": [
                {
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "compare_mode": "threshold_and_deadband",
                    "thresholds": {"high": 90},
                    "recovery_threshold": 85,
                    "deadband": 5,
                    "dimension_selector": {"scope": "system", "window": "5s"},
                },
                {
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "compare_mode": "threshold_and_deadband",
                    "thresholds": {"high": 95},
                    "recovery_threshold": 90,
                    "deadband": 5,
                    "dimension_selector": {"scope": "system", "window": "5m"},
                },
            ],
        },
    )

    assert result["success"] is True
    assert result["generated_rule_count"] == 4
    assert len({rule["name"] for rule in result["rules"]}) == 4
    assert {
        tuple(sorted(rule["condition"]["dimensions"].items()))
        for rule in result["rules"]
    } == {
        (("scope", "system"), ("window", "5s")),
        (("scope", "system"), ("window", "5m")),
    }
    assert all(
        {"scope", "window"}.issubset(rule["correlation_keys"])
        for rule in result["rules"]
    )


def test_event_rule_generator_filters_same_observation_by_dimension_target(
    tmp_path: Path,
):
    result = _run_skill(
        tmp_path,
        {
            "contract": {
                "entity_type": "cpu_scope",
                "required_fields": ["slot", "cpu", "usage_5s", "usage_5m"],
                "optional_fields": [],
                "primary_keys": ["slot", "cpu"],
                "state_semantics": {
                    "slot": {"semantic_type": "identity"},
                    "cpu": {"semantic_type": "identity"},
                    "usage_5s": {
                        "semantic_type": "gauge",
                        "observation_type": "device.cpu.utilization",
                        "dimensions": {"window": {"literal": "5s"}},
                    },
                    "usage_5m": {
                        "semantic_type": "gauge",
                        "observation_type": "device.cpu.utilization",
                        "dimensions": {"window": {"literal": "5m"}},
                    },
                },
            },
            "change_policies": [
                {
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "compare_mode": "dimension_dispatch",
                    "dimension_policies": [
                        {
                            "selector": {"window": "5s"},
                            "policy": {
                                "observation_type": "device.cpu.utilization",
                                "semantic_type": "gauge",
                                "compare_mode": "threshold_and_deadband",
                                "thresholds": {"high": 80},
                                "recovery_threshold": 75,
                                "deadband": 5,
                            },
                        },
                        {
                            "selector": {"window": "5m"},
                            "policy": {
                                "observation_type": "device.cpu.utilization",
                                "semantic_type": "gauge",
                                "compare_mode": "threshold_and_deadband",
                                "thresholds": {"high": 80},
                                "recovery_threshold": 75,
                                "deadband": 5,
                            },
                        },
                    ],
                }
            ],
            "event_policy_targets": [
                {
                    "observation_type": "device.cpu.utilization",
                    "dimension_selector": {"window": "5m"},
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["generated_rule_count"] == 2
    assert {
        tuple(sorted(rule["condition"]["dimensions"].items()))
        for rule in result["rules"]
    } == {(("window", "5m"),)}


def test_event_rule_constraints_require_complete_fault_recovery_pair(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "contract": {
                "entity_type": "interface_l3",
                "required_fields": ["interface", "status"],
                "optional_fields": [],
                "primary_keys": ["interface"],
                "state_semantics": {
                    "interface": {"semantic_type": "identity"},
                    "status": {
                        "semantic_type": "state",
                        "observation_type": "interface.oper_status",
                    },
                },
            },
            "change_policy": {
                "observation_type": "interface.oper_status",
                "semantic_type": "state",
                "compare_mode": "state_transition",
            },
            "event_constraints": {
                "required_event_types": ["InterfaceDown", "MissingRecovery"],
                "required_recovery_pairs": [
                    {"fault": "InterfaceDown", "recovery": "MissingRecovery"}
                ],
                "allow_initial_transition_fault": False,
            },
        },
        expect_success=False,
    )

    assert result["success"] is False
    assert "required event type is missing: MissingRecovery" in result["validation"]["errors"]


def test_builtin_generator_compiles_approved_event_identity(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "contract": {
                "entity_type": "cpu_scope",
                "canonical_entity_type": "cpu_scope",
                "required_fields": ["slot", "cpu_5m"],
                "optional_fields": [],
                "primary_keys": ["slot"],
                "state_semantics": {
                    "slot": {"semantic_type": "identity"},
                    "cpu_5m": {
                        "semantic_type": "gauge",
                        "observation_type": "device.cpu.utilization",
                    },
                },
            },
            "change_policies": [
                {
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "compare_mode": "threshold_and_deadband",
                    "thresholds": {"high": 80},
                    "recovery_threshold": 75,
                    "deadband": 5,
                    "dimension_selector": {"scope": "slot", "window": "5m"},
                }
            ],
            "event_constraints_by_observation_type": {
                "device.cpu.utilization": {
                    "role_event_types": {
                        "high": "CpuUtilizationHigh",
                        "high_recovery": "CpuUtilizationRecovered",
                    },
                    "required_event_types": [
                        "CpuUtilizationHigh",
                        "CpuUtilizationRecovered",
                    ],
                    "required_event_classes": {
                        "CpuUtilizationHigh": "fault",
                        "CpuUtilizationRecovered": "recovery",
                    },
                    "required_severities": {
                        "CpuUtilizationHigh": "warning",
                        "CpuUtilizationRecovered": "info",
                    },
                    "required_recovery_pairs": [
                        {
                            "fault": "CpuUtilizationHigh",
                            "recovery": "CpuUtilizationRecovered",
                        }
                    ],
                    "max_rule_count": 2,
                }
            },
        },
    )

    assert result["success"] is True
    assert {rule["event_type"] for rule in result["rules"]} == {
        "CpuUtilizationHigh",
        "CpuUtilizationRecovered",
    }
    recovered = next(
        rule
        for rule in result["rules"]
        if rule["event_type"] == "CpuUtilizationRecovered"
    )
    assert recovered["recovery"]["resolves"] == ["CpuUtilizationHigh"]
    assert result["governance_repairs"]


def test_event_rule_validator_normalizes_recovery_rule_name_reference():
    module = _load_skill_module()
    result = module._validate_llm_rules(
        {
            "rules": [
                {
                    "name": "interface_oper_status_down",
                    "event_type": "InterfaceDown",
                    "severity": "major",
                    "event_class": "fault",
                    "description": "接口中断",
                    "condition": {
                        "kind": "transition",
                        "entity_types": ["interface"],
                        "fields": ["status"],
                        "from_group": "up",
                        "to_group": "down",
                        "allow_initial_target": False,
                    },
                },
                {
                    "name": "interface_oper_status_up",
                    "event_type": "InterfaceUp",
                    "severity": "info",
                    "event_class": "recovery",
                    "description": "接口恢复",
                    "condition": {
                        "kind": "transition",
                        "entity_types": ["interface"],
                        "fields": ["status"],
                        "from_group": "down",
                        "to_group": "up",
                    },
                    "recovery": {"resolves": ["interface_oper_status_down"]},
                },
            ]
        },
        contract={
            "entity_type": "interface",
            "required_fields": ["interface", "status"],
            "optional_fields": [],
            "state_semantics": {
                "interface": {"semantic_type": "identity"},
                "status": {
                    "semantic_type": "state",
                    "observation_type": "interface.oper_status",
                },
            },
        },
        change_policy={
            "observation_type": "interface.oper_status",
            "semantic_type": "state",
            "compare_mode": "state_transition",
        },
        event_constraints={
            "required_event_types": ["InterfaceDown", "InterfaceUp"],
            "required_recovery_pairs": [{"fault": "InterfaceDown", "recovery": "InterfaceUp"}],
        },
    )

    assert result["success"] is True
    recovery = next(rule for rule in result["rules"] if rule["event_class"] == "recovery")
    assert recovery["recovery"]["resolves"] == ["InterfaceDown"]
    assert recovery["name"].startswith("interface-oper-status.")


def test_event_rule_validator_rejects_fault_type_reserved_by_another_target():
    module = _load_skill_module()
    result = module._validate_llm_rules(
        {
            "rules": [
                {
                    "name": "protocol-down",
                    "event_type": "InterfaceDown",
                    "severity": "major",
                    "description": "Protocol state changed to down",
                    "condition": {
                        "kind": "transition",
                        "entity_types": ["interface"],
                        "fields": ["protocol"],
                        "from_group": "up",
                        "to_group": "down",
                        "allow_initial_target": False,
                    },
                }
            ]
        },
        contract={
            "entity_type": "interface",
            "required_fields": ["interface", "protocol"],
            "optional_fields": [],
            "state_semantics": {
                "interface": {"semantic_type": "identity"},
                "protocol": {
                    "semantic_type": "state",
                    "observation_type": "interface.protocol_status",
                },
            },
        },
        change_policy={
            "observation_type": "interface.protocol_status",
            "semantic_type": "state",
            "compare_mode": "state_transition",
        },
        reserved_fault_event_types={
            "InterfaceDown": "interface.oper_status",
        },
    )

    assert result["success"] is False
    assert any(
        "already reserved by observation_type interface.oper_status" in error
        for error in result["errors"]
    )


def test_llm_fanout_namespaces_event_types_reused_across_observation_targets(monkeypatch):
    module = _load_skill_module()

    class WeakSharedEventTypeProvider:
        name = "fake-weak-shared-event-type-model"

        def propose(self, **_kwargs):
            from src.core.rule_control.generation_agent import ProviderResult

            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "rules": [
                        {
                            "name": "interface-down",
                            "event_type": "InterfaceDown",
                            "trigger_role": "down",
                            "severity": "major",
                            "event_class": "fault",
                        },
                        {
                            "name": "interface-up",
                            "event_type": "InterfaceUp",
                            "trigger_role": "up",
                            "severity": "info",
                            "event_class": "recovery",
                        },
                    ]
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        WeakSharedEventTypeProvider,
    )
    result = module.run_event_rule_generation(
        {
            "generation_provider": "llm",
            "contract": {
                "entity_type": "interface_brief",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "status"],
                "optional_fields": ["protocol"],
                "state_semantics": {
                    "interface": {"semantic_type": "identity"},
                    "status": {
                        "semantic_type": "state",
                        "observation_type": "interface.oper_status",
                    },
                    "protocol": {
                        "semantic_type": "state",
                        "observation_type": "interface.protocol_state",
                    },
                },
            },
            "change_policies": [
                {
                    "observation_type": "interface.protocol_state",
                    "semantic_type": "state",
                    "compare_mode": "state_transition",
                },
                {
                    "observation_type": "interface.oper_status",
                    "semantic_type": "state",
                    "compare_mode": "state_transition",
                },
            ],
        }
    )

    assert result["success"] is True
    assert result["generated_rule_count"] == 4
    assert {rule["event_type"] for rule in result["rules"]} == {
        "interface_oper_status_down",
        "interface_oper_status_up",
        "interface_protocol_state_down",
        "interface_protocol_state_up",
    }
    by_observation = {
        rule["condition"]["observation_types"][0]: rule
        for rule in result["rules"]
        if rule["event_class"] == "recovery"
    }
    assert by_observation["interface.oper_status"]["recovery"]["resolves"] == [
        "interface_oper_status_down"
    ]
    assert by_observation["interface.protocol_state"]["recovery"]["resolves"] == [
        "interface_protocol_state_down"
    ]
    assert len(result["governance_repairs"]) == 4
    assert {
        repair["kind"] for repair in result["governance_repairs"]
    } == {"cross_target_event_type_namespace"}


def test_event_rule_fanout_skips_change_only_observations(monkeypatch):
    module = _load_skill_module()

    class WeakProvider:
        name = "fake-weak-event-model"

        def propose(self, **_kwargs):
            from src.core.rule_control.generation_agent import ProviderResult

            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "rules": [
                        {
                            "name": "state-down",
                            "event_type": "StateDown",
                            "trigger_role": "down",
                            "severity": "major",
                            "event_class": "fault",
                        },
                        {
                            "name": "state-up",
                            "event_type": "StateUp",
                            "trigger_role": "up",
                            "severity": "info",
                            "event_class": "recovery",
                        },
                    ]
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        WeakProvider,
    )
    result = module.run_event_rule_generation(
        {
            "generation_provider": "llm",
            "contract": {
                "entity_type": "interface",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "status"],
                "optional_fields": ["duplex", "speed"],
                "state_semantics": {
                    "interface": {"semantic_type": "identity"},
                    "status": {
                        "semantic_type": "state",
                        "observation_type": "interface.oper_status",
                    },
                    "duplex": {
                        "semantic_type": "state",
                        "observation_type": "interface.duplex",
                    },
                    "speed": {
                        "semantic_type": "gauge",
                        "observation_type": "interface.speed",
                    },
                },
            },
            "change_policies": [
                {
                    "observation_type": "interface.oper_status",
                    "semantic_type": "state",
                    "compare_mode": "state_transition",
                },
                {
                    "observation_type": "interface.duplex",
                    "semantic_type": "state",
                    "compare_mode": "state_transition",
                },
                {
                    "observation_type": "interface.speed",
                    "semantic_type": "gauge",
                    "compare_mode": "value_change",
                },
            ],
            "event_observation_types": ["interface.oper_status"],
        }
    )

    assert result["success"] is True
    assert result["generated_rule_count"] == 2
    assert {
        rule["condition"]["observation_types"][0]
        for rule in result["rules"]
    } == {"interface.oper_status"}


def test_event_rule_validator_governs_unapproved_critical_and_preserves_gate():
    module = _load_skill_module()
    candidate = {
        "rules": [
            {
                "name": "interface-down",
                "event_type": "InterfaceDown",
                "severity": "critical",
                "condition": {
                    "kind": "transition",
                    "entity_types": ["interface"],
                    "fields": ["status"],
                    "from_group": "up",
                    "to_group": "down",
                    "allow_initial_target": False,
                },
            }
        ]
    }
    contract = {
        "entity_type": "interface",
        "required_fields": ["interface", "status"],
        "optional_fields": [],
        "state_semantics": {
            "interface": {"semantic_type": "identity"},
            "status": {
                "semantic_type": "state",
                "observation_type": "interface.oper_status",
            },
        },
    }
    policy = {
        "observation_type": "interface.oper_status",
        "semantic_type": "state",
        "compare_mode": "state_transition",
    }

    raw_gate_errors = module._critical_severity_errors(candidate["rules"], {})
    governed = module._validate_llm_rules(
        candidate,
        contract=contract,
        change_policy=policy,
    )
    authorized = module._validate_llm_rules(
        candidate,
        contract=contract,
        change_policy=policy,
        event_constraints={"allow_critical_event_types": ["InterfaceDown"]},
    )

    assert any(
        "without explicit event_constraints authorization" in error for error in raw_gate_errors
    )
    assert governed["success"] is True
    assert governed["rules"][0]["severity"] == "major"
    assert any(item["kind"] == "severity_governance" for item in governed["governance_repairs"])
    assert authorized["success"] is True
    assert authorized["rules"][0]["severity"] == "critical"


def test_llm_event_rule_generation_governs_unauthorized_critical_and_records_repair(
    monkeypatch,
):
    module = _load_skill_module()

    class WeakCriticalProvider:
        name = "fake-weak-model"

        def propose(self, **_kwargs):
            from src.core.rule_control.generation_agent import ProviderResult

            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "rules": [
                        {
                            "name": "interface-down",
                            "event_type": "InterfaceDown",
                            "severity": "critical",
                            "trigger_role": "down",
                            "condition": {
                                "kind": "transition",
                                "entity_types": ["interface"],
                                "fields": ["status"],
                                "from_group": "up",
                                "to_group": "down",
                                "allow_initial_target": False,
                            },
                            "description": "接口状态由正常变为异常",
                        },
                        {
                            "name": "interface-up",
                            "event_type": "InterfaceUp",
                            "severity": "warning",
                            "trigger_role": "up",
                            "condition": {
                                "kind": "transition",
                                "entity_types": ["interface"],
                                "fields": ["status"],
                                "from_group": "down",
                                "to_group": "up",
                                "allow_initial_target": False,
                            },
                            "description": "接口状态恢复正常",
                            "event_class": "recovery",
                            "recovery": {"resolves": ["InterfaceDown"]},
                        },
                    ]
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        WeakCriticalProvider,
    )
    result = module.run_event_rule_generation(
        {
            "generation_provider": "llm",
            "contract": {
                "entity_type": "interface",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "status"],
                "optional_fields": [],
                "state_semantics": {
                    "interface": {"semantic_type": "identity"},
                    "status": {
                        "semantic_type": "state",
                        "observation_type": "interface.oper_status",
                    },
                },
            },
            "change_policy": {
                "observation_type": "interface.oper_status",
                "semantic_type": "state",
                "compare_mode": "state_transition",
            },
        }
    )

    assert result["success"] is True
    fault = next(rule for rule in result["rules"] if rule["event_class"] == "fault")
    assert fault["severity"] == "major"
    attempt = result["agent_loop"]["attempts"][0]
    assert attempt["governance_repairs"] == [
        {
            "kind": "severity_governance",
            "path": "rules[0].severity",
            "event_type": "InterfaceDown",
            "proposed": "critical",
            "effective": "major",
            "reason": "critical_requires_explicit_authorization",
            "source": "deterministic_control_plane",
        }
    ]


def test_llm_event_rule_governor_preserves_explicitly_authorized_critical():
    module = _load_skill_module()
    candidate = {
        "rules": [
            {
                "name": "interface-down",
                "event_type": "InterfaceDown",
                "severity": "critical",
            }
        ]
    }

    governed, repairs = module._govern_llm_event_rule_candidate(
        candidate,
        {"event_constraints": {"allow_critical_event_types": ["InterfaceDown"]}},
    )

    assert governed == candidate
    assert repairs == []


def test_event_rule_ir_compiler_assigns_deterministic_precedence():
    module = _load_skill_module()
    rules = [
        {"name": "low", "event_type": "OpticalRxPowerLow", "priority": 200},
        {
            "name": "critical",
            "event_type": "OpticalRxPowerCritical",
            "priority": 100,
        },
    ]

    changes = module._normalize_rule_ir(
        rules,
        observation_type="interface.optical.rx_power",
        constraints={
            "priority_order": [{"higher": "OpticalRxPowerCritical", "lower": "OpticalRxPowerLow"}]
        },
    )

    priorities = {rule["event_type"]: rule["priority"] for rule in rules}
    assert priorities["OpticalRxPowerCritical"] > priorities["OpticalRxPowerLow"]
    assert all(rule["name"].startswith("interface-optical-rx-power.") for rule in rules)
    assert {item["kind"] for item in changes} == {
        "rule_name_namespace",
        "priority_precedence",
    }


def test_event_rule_ir_compiler_makes_superseding_critical_rule_exclusive():
    module = _load_skill_module()
    rules = [
        {
            "name": "low",
            "event_type": "OpticalRxPowerLow",
            "priority": 100,
            "exclusive": False,
        },
        {
            "name": "critical",
            "event_type": "OpticalRxPowerCritical",
            "priority": 100,
            "exclusive": False,
        },
    ]

    changes = module._normalize_rule_ir(
        rules,
        observation_type="interface.optical.rx_power",
        constraints={
            "required_exclusive_event_types": ["OpticalRxPowerCritical"],
            "priority_order": [
                {"higher": "OpticalRxPowerCritical", "lower": "OpticalRxPowerLow"}
            ],
        },
    )

    by_type = {rule["event_type"]: rule for rule in rules}
    assert by_type["OpticalRxPowerCritical"]["exclusive"] is True
    assert by_type["OpticalRxPowerCritical"]["priority"] > by_type[
        "OpticalRxPowerLow"
    ]["priority"]
    assert by_type["OpticalRxPowerLow"]["exclusive"] is False
    assert {item["kind"] for item in changes} == {
        "rule_name_namespace",
        "exclusive_precedence",
        "priority_precedence",
    }


def test_event_rule_ir_uses_stable_trigger_role_names_across_llm_wording():
    module = _load_skill_module()
    contract = {
        "entity_type": "interface",
        "primary_keys": ["interface"],
        "required_fields": ["interface", "status"],
        "optional_fields": [],
        "state_semantics": {
            "interface": {"semantic_type": "identity"},
            "status": {
                "semantic_type": "state",
                "observation_type": "interface.oper_status",
            },
        },
    }
    policy = {
        "observation_type": "interface.oper_status",
        "semantic_type": "state",
        "compare_mode": "state_transition",
    }

    first = module._validate_llm_rules(
        {
            "rules": [
                {
                    "name": "port-failed",
                    "event_type": "PortFailed",
                    "trigger_role": "down",
                },
                {
                    "name": "port-restored",
                    "event_type": "PortRestored",
                    "trigger_role": "up",
                    "event_class": "recovery",
                },
            ]
        },
        contract=contract,
        change_policy=policy,
    )
    second = module._validate_llm_rules(
        {
            "rules": [
                {
                    "name": "interface-unavailable-v2",
                    "event_type": "InterfaceUnavailable",
                    "trigger_role": "down",
                },
                {
                    "name": "service-normal-v2",
                    "event_type": "ServiceNormal",
                    "trigger_role": "up",
                    "event_class": "recovery",
                },
            ]
        },
        contract=contract,
        change_policy=policy,
    )

    assert first["success"] is True
    assert second["success"] is True
    assert [rule["name"] for rule in first["rules"]] == [
        "interface-oper-status.down",
        "interface-oper-status.up",
    ]
    assert [rule["name"] for rule in second["rules"]] == [
        "interface-oper-status.down",
        "interface-oper-status.up",
    ]


def test_event_rule_ir_compiler_repairs_weak_model_condition_syntax():
    module = _load_skill_module()
    result = module._validate_llm_rules(
        {
            "rules": [
                {
                    "name": "measurement-high",
                    "event_type": "measurement_high",
                    "severity": "warning",
                    "description": "测量值偏高",
                    "condition": {
                        "kind": "threshold",
                        "field": "measurement",
                        "operator": ">=",
                        "value": 80,
                        "minimum_duration": "5m",
                    },
                },
                {
                    "name": "measurement-high-recovered",
                    "event_type": "measurement_high_recovered",
                    "event_class": "recovery",
                    "description": "高值恢复",
                    "condition": {"kind": "threshold_recovery", "lte": 75},
                },
                {
                    "name": "measurement-low",
                    "event_type": "measurement_low",
                    "severity": "major",
                    "description": "测量值偏低",
                    "condition": {"kind": "threshold", "lt": 10},
                },
                {
                    "name": "measurement-low-recovered",
                    "event_type": "measurement_low_recovered",
                    "event_class": "recovery",
                    "description": "低值恢复",
                    "condition": {"kind": "threshold_recovery", "gte": 12},
                },
            ]
        },
        contract={
            "entity_type": "generic_sensor",
            "primary_keys": ["sensor_id"],
            "required_fields": ["sensor_id", "measurement"],
            "optional_fields": [],
            "state_semantics": {
                "sensor_id": {"semantic_type": "identity"},
                "measurement": {
                    "semantic_type": "gauge",
                    "observation_type": "generic.measurement",
                },
            },
        },
        change_policy={
            "observation_type": "generic.measurement",
            "semantic_type": "gauge",
            "compare_mode": "threshold_and_deadband",
            "thresholds": {"high": 80, "low": 10},
            "recovery_threshold": 75,
            "deadband": 2,
        },
    )

    assert result["success"] is True
    assert result["compiler"]["compiled"] is True
    conditions = {rule["event_type"]: rule["condition"] for rule in result["rules"]}
    assert conditions["measurement_high"] == {
        "kind": "threshold",
        "threshold_role": "high",
        "observation_types": ["generic.measurement"],
        "entity_types": ["generic_sensor"],
        "fields": ["measurement"],
        "gte": 80.0,
    }
    assert conditions["measurement_high_recovered"]["lt"] == 75.0
    assert conditions["measurement_low"]["kind"] == "threshold_lte"
    assert conditions["measurement_low"]["lte"] == 10.0
    assert conditions["measurement_low_recovered"]["kind"] == "threshold_low_recovery"
    assert conditions["measurement_low_recovered"]["gt"] == 12.0


def test_event_rule_ir_compiler_prefers_per_entity_reference_fields():
    module = _load_skill_module()
    candidate_rules = [
        {
            "name": role,
            "event_type": f"measurement_{role}",
            "trigger_role": role,
            "event_class": "recovery" if "recovery" in role else "fault",
            "description": role,
        }
        for role in ("high", "high_recovery", "low", "low_recovery")
    ]
    result = module._validate_llm_rules(
        {"rules": candidate_rules},
        contract={
            "entity_type": "generic_sensor",
            "primary_keys": ["sensor_id"],
            "required_fields": ["sensor_id", "measurement"],
            "optional_fields": ["low_reference", "high_reference"],
            "state_semantics": {
                "sensor_id": {"semantic_type": "identity"},
                "measurement": {
                    "semantic_type": "gauge",
                    "observation_type": "generic.measurement",
                },
                "low_reference": {"semantic_type": "gauge"},
                "high_reference": {"semantic_type": "gauge"},
            },
        },
        change_policy={
            "observation_type": "generic.measurement",
            "semantic_type": "gauge",
            "compare_mode": "threshold_and_deadband",
            "thresholds": {"low": 10, "high": 80},
            "threshold_references": {
                "low": {"field_name": "low_reference"},
                "high": {"field_name": "high_reference"},
            },
            "deadband": 2,
        },
    )

    assert result["success"] is True
    conditions = {rule["event_type"]: rule["condition"] for rule in result["rules"]}
    assert conditions["measurement_high"]["kind"] == "threshold_field_gte"
    assert conditions["measurement_high"]["reference_field"] == "high_reference"
    assert conditions["measurement_low"]["kind"] == "threshold_field_lte"
    assert conditions["measurement_low"]["reference_field"] == "low_reference"
    assert conditions["measurement_high_recovery"]["kind"] == ("threshold_field_high_recovery")
    assert conditions["measurement_low_recovery"]["kind"] == ("threshold_field_low_recovery")


def test_event_rule_compiler_targets_canonical_runtime_entity_type():
    compiled = compile_event_rule_intents(
        candidate_rules=[
            {
                "event_type": "CpuUtilizationHigh",
                "trigger_role": "high",
                "severity": "warning",
            },
            {
                "event_type": "CpuUtilizationRecovered",
                "trigger_role": "high_recovery",
                "event_class": "recovery",
                "severity": "info",
            },
        ],
        contract={
            "entity_type": "cpu_usage",
            "canonical_entity_type": "cpu_scope",
            "primary_keys": ["slot", "cpu"],
            "required_fields": ["slot", "cpu", "usage_5m"],
            "optional_fields": [],
            "state_semantics": {
                "usage_5m": {
                    "semantic_type": "gauge",
                    "observation_type": "device.cpu.utilization",
                }
            },
        },
        change_policy={
            "observation_type": "device.cpu.utilization",
            "semantic_type": "gauge",
            "compare_mode": "threshold_and_deadband",
            "thresholds": {"high": 80},
            "recovery_threshold": 75,
            "deadband": 5,
        },
    )

    assert compiled["success"] is True
    assert {
        tuple(rule["condition"]["entity_types"])
        for rule in compiled["rules"]
    } == {("cpu_scope",)}


def test_event_rule_compiler_builds_structured_lifecycle_change_rule():
    compiled = compile_event_rule_intents(
        candidate_rules=[
            {
                "event_type": "DeviceSoftwareVersionChanged",
                "trigger_role": "change",
                "severity": "warning",
            }
        ],
        contract={
            "entity_type": "device",
            "primary_keys": ["device_name"],
            "required_fields": ["device_name", "software_version"],
            "optional_fields": [],
            "state_semantics": {
                "software_version": {
                    "semantic_type": "text",
                    "observation_type": "device.software_version",
                }
            },
        },
        change_policy={
            "observation_type": "device.software_version",
            "semantic_type": "text",
            "compare_mode": "exact",
        },
    )

    assert compiled["success"] is True
    assert len(compiled["rules"]) == 1
    rule = compiled["rules"][0]
    assert rule["event_type"] == "DeviceSoftwareVersionChanged"
    assert rule["trigger_role"] == "change"
    assert rule["condition"] == {
        "kind": "field_change",
        "entity_types": ["device"],
        "fields": ["software_version"],
        "observation_types": ["device.software_version"],
        "allow_initial": False,
    }
