from __future__ import annotations

import pytest

from src.core.check_items.canonical_rule_context import (
    build_canonical_rule_context,
    generate_canonical_rule_candidates,
)
from src.core.check_items.catalog import load_catalog
from src.core.patrol.event_rule_compiler import compile_event_rule_intents


def _catalog_item(check_item_id: str):
    return next(
        item for item in load_catalog().check_items if item.id == check_item_id
    )


def test_rule_context_is_derived_only_from_check_item_canonical_semantics():
    item = _catalog_item("interface.oper_status")

    first = build_canonical_rule_context(
        check_item_id=item.id,
        check_item_version_id=item.version_id,
        result_mode=item.result_mode,
        assessment_mode=item.assessment_mode,
        canonical_contract=item.canonical_contract,
    )
    second = build_canonical_rule_context(
        check_item_id=item.id,
        check_item_version_id=item.version_id,
        result_mode=item.result_mode,
        assessment_mode=item.assessment_mode,
        canonical_contract=item.canonical_contract,
    )

    assert first["digest"] == second["digest"]
    assert first["rule_asset_requirements"]["requires_observation_contract"] is False
    assert "interface.oper_status" in first["rule_asset_requirements"][
        "change_policy_observation_types"
    ]
    semantics = first["contract"]["state_semantics"]["oper_status"]
    assert semantics["state_groups"]["healthy"] == ["up"]
    assert "down" in semantics["state_groups"]["unhealthy"]
    serialized = repr(first).casefold()
    assert "parser_template_id" not in serialized
    assert "dataset_id" not in serialized
    assert "raw_cli" not in serialized


def test_rule_context_rejects_collection_evidence_in_contract():
    item = _catalog_item("interface.oper_status")
    contract = {**item.canonical_contract, "samples": [{"oper_status": "up"}]}

    with pytest.raises(ValueError, match="cannot contain collection evidence"):
        build_canonical_rule_context(
            check_item_id=item.id,
            check_item_version_id=item.version_id,
            result_mode=item.result_mode,
            assessment_mode=item.assessment_mode,
            canonical_contract=contract,
        )


def test_current_state_rule_uses_canonical_state_groups_without_samples():
    item = _catalog_item("interface.oper_status")
    context = build_canonical_rule_context(
        check_item_id=item.id,
        check_item_version_id=item.version_id,
        result_mode=item.result_mode,
        assessment_mode=item.assessment_mode,
        canonical_contract=item.canonical_contract,
    )

    result = compile_event_rule_intents(
        candidate_rules=[],
        contract=context["contract"],
        change_policy={
            "observation_type": "interface.oper_status",
            "semantic_type": "state",
            "compare_mode": "state_transition",
        },
        rule_asset_requirements={
            "requires_current_state_rule": True,
            "requires_event_rule": False,
            "current_state_observation_types": ["interface.oper_status"],
        },
    )

    assert result["success"] is True
    assert result["compiled"] is True
    assert result["rules"][0]["condition"]["kind"] == "entity_status_down"
    assert result["rules"][0]["condition"]["values"] == [
        "down",
        "error_disabled",
        "unavailable",
    ]


def test_generates_change_and_event_candidates_without_collection_inputs():
    item = _catalog_item("interface.oper_status")
    context = build_canonical_rule_context(
        check_item_id=item.id,
        check_item_version_id=item.version_id,
        result_mode=item.result_mode,
        assessment_mode=item.assessment_mode,
        canonical_contract=item.canonical_contract,
    )

    result = generate_canonical_rule_candidates(context)

    assert result["success"] is True
    assert result["input_summary"] == {
        "source": "canonical_semantics_only",
        "sample_count": 0,
        "parser_template_count": 0,
    }
    assert result["change_policies"][0]["source"] == (
        "canonical_check_item_semantics"
    )
    assert {item["event_type"] for item in result["event_rules"]} == {
        "InterfaceDown",
        "InterfaceUp",
    }


def test_health_target_without_state_groups_or_thresholds_is_blocked():
    item = _catalog_item("environment.temperature")
    context = build_canonical_rule_context(
        check_item_id=item.id,
        check_item_version_id=item.version_id,
        result_mode=item.result_mode,
        assessment_mode=item.assessment_mode,
        canonical_contract=item.canonical_contract,
    )

    result = generate_canonical_rule_candidates(context)

    assert result["success"] is False
    assert result["blockers"] == [{
        "code": "canonical_rule_semantics_missing",
        "observation_type": "environment.temperature.current",
        "message": (
            "标准语义 B 声明需要健康判断，但尚未提供可执行的"
            "状态分组或阈值约束。"
        ),
    }]


@pytest.mark.parametrize(
    ("check_item_id", "observation_type", "warning_field", "alarm_field"),
    [
        ("system.cpu_usage", "device.cpu.utilization", "cpu_high_warning_percent", "cpu_high_alarm_percent"),
        ("system.memory_usage", "device.memory.utilization", "memory_high_warning_percent", "memory_high_alarm_percent"),
    ],
)
def test_cpu_and_memory_candidates_use_dynamic_thresholds_only(
    check_item_id: str,
    observation_type: str,
    warning_field: str,
    alarm_field: str,
):
    item = _catalog_item(check_item_id)
    context = build_canonical_rule_context(
        check_item_id=item.id,
        check_item_version_id=item.version_id,
        result_mode=item.result_mode,
        assessment_mode=item.assessment_mode,
        canonical_contract=item.canonical_contract,
    )

    result = generate_canonical_rule_candidates(context)
    policy = next(
        row for row in result["change_policies"]
        if row["observation_type"] == observation_type
    )

    assert result["success"] is True
    assert policy["thresholds"] == {}
    assert policy["allow_static_threshold_fallback"] is False
    assert policy["threshold_references"] == {
        "high": {"field_name": warning_field},
        "critical_high": {"field_name": alarm_field},
    }


def test_optical_candidate_declares_linear_baseline_and_module_identity():
    item = _catalog_item("interface.optical_power")
    context = build_canonical_rule_context(
        check_item_id=item.id,
        check_item_version_id=item.version_id,
        result_mode=item.result_mode,
        assessment_mode=item.assessment_mode,
        canonical_contract=item.canonical_contract,
    )

    result = generate_canonical_rule_candidates(context)
    rx = next(
        row for row in result["change_policies"]
        if row["observation_type"] == "interface.optical.rx_power"
    )

    assert rx["baseline_comparison"] == {
        "kind": "linear_power_loss_ratio",
        "max_loss_ratio": 0.1,
        "identity_fields": ["part_number", "serial_number"],
    }
    baseline_rule = next(
        row for row in result["event_rules"]
        if row["event_type"] == "OpticalRxPowerDegraded"
    )
    assert baseline_rule["condition"]["kind"] == "linear_power_baseline_loss"
