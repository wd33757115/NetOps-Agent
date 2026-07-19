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


def test_event_rule_compiler_keeps_alarm_only_reference_critical():
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
            "threshold_references": {
                "critical_low": {"field_name": "rx_low_alarm_dbm"}
            },
            "deadband": 2,
        },
    )

    assert compiled["success"] is True
    assert [rule["trigger_role"] for rule in compiled["rules"]] == [
        "critical_low",
        "low_recovery",
    ]
    fault, recovery = compiled["rules"]
    assert fault["severity"] == "critical"
    assert fault["condition"]["reference_field"] == "rx_low_alarm_dbm"
    assert recovery["condition"] == {
        "kind": "threshold_field_low_recovery",
        "entity_types": ["interface_optical"],
        "fields": ["rx_power_dbm"],
        "reference_field": "rx_low_alarm_dbm",
        "hysteresis": 2.0,
    }
    assert recovery["recovery"]["resolves"] == ["OpticalRxPowerAlarm"]


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

    recovery = next(
        rule for rule in compiled["rules"] if rule["trigger_role"] == "low_recovery"
    )
    assert recovery["condition"]["reference_field"] == "rx_low_warning_dbm"
    assert recovery["condition"]["fallback_reference_fields"] == [
        "rx_low_alarm_dbm"
    ]


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
    recovery = next(
        rule for rule in result["rules"] if rule["event_class"] == "recovery"
    )
    assert fault["condition"] == {
        "kind": "threshold_field_lte",
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


def test_event_rule_validator_requires_explicit_critical_authorization():
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

    rejected = module._validate_llm_rules(
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

    assert rejected["success"] is False
    assert any(
        "without explicit event_constraints authorization" in error for error in rejected["errors"]
    )
    assert authorized["success"] is True


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
