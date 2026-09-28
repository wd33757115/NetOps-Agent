from __future__ import annotations

import pytest

from src.core.check_items.canonical_rule_context import (
    build_canonical_rule_context,
    generate_canonical_rule_candidates,
)
from src.core.check_items.catalog import load_catalog
from src.core.patrol.event_rule_compiler import compile_event_rule_intents


def test_frozen_semantics_survive_an_installed_catalog_upgrade(monkeypatch):
    from src.core.check_items import canonical

    item = _catalog_item("interface.runtime_statistics")
    frozen = canonical.freeze_rule_semantics(item.canonical_contract)

    def compile_context():
        return build_canonical_rule_context(
            check_item_id=item.id,
            check_item_version_id=item.version_id,
            result_mode=item.result_mode,
            assessment_mode=item.assessment_mode,
            canonical_contract=frozen,
        )

    before = compile_context()
    monkeypatch.setattr(
        canonical,
        "load_canonical_catalog",
        lambda: (_ for _ in ()).throw(AssertionError("must use frozen semantics")),
    )
    assert compile_context() == before


def test_incomplete_frozen_semantics_cannot_fall_back_silently():
    from src.core.check_items.canonical import freeze_rule_semantics, rule_semantics_catalog

    frozen = freeze_rule_semantics(_catalog_item("interface.runtime_statistics").canonical_contract)
    frozen["rule_semantics_snapshot"]["observations"] = []
    with pytest.raises(ValueError, match="do not cover"):
        rule_semantics_catalog(frozen)


def _catalog_item(check_item_id: str):
    return next(item for item in load_catalog().check_items if item.id == check_item_id)


def test_rule_context_is_derived_only_from_check_item_canonical_semantics():
    item = _catalog_item("interface.runtime_statistics")

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
    assert (
        "interface.runtime_statistics.line_status"
        in first["rule_asset_requirements"]["change_policy_observation_types"]
    )
    semantics = first["contract"]["state_semantics"]["line_status"]
    assert semantics["event_on_change"] is True
    assert semantics["event_constraints"]["required_event_classes"] == {
        "interface.runtime_statistics.line_status.changed": "change"
    }
    serialized = repr(first).casefold()
    assert "parser_template_id" not in serialized
    assert "dataset_id" not in serialized
    assert "raw_cli" not in serialized


def test_rule_context_rejects_collection_evidence_in_contract():
    item = _catalog_item("interface.runtime_statistics")
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
    item = _catalog_item("environment.fan")
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
            "observation_type": "environment.fan.state",
            "semantic_type": "state",
            "compare_mode": "state_transition",
        },
        rule_asset_requirements={
            "requires_current_state_rule": True,
            "requires_event_rule": False,
            "current_state_observation_types": ["environment.fan.state"],
        },
    )

    assert result["success"] is True
    assert result["compiled"] is True
    assert result["rules"][0]["condition"]["kind"] == "entity_status_down"
    assert result["rules"][0]["condition"]["values"] == [
        "absent",
        "down",
        "failed",
        "faulty",
    ]


def test_generates_change_and_event_candidates_without_collection_inputs():
    item = _catalog_item("interface.runtime_statistics")
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
    assert result["change_policies"][0]["source"] == ("check_item_all_value_changes")
    assert len(result["event_rules"]) == 19
    assert {rule["event_class"] for rule in result["event_rules"]} == {"change"}
    assert {rule["event_type"] for rule in result["event_rules"]} == {
        row["observation_type"] + ".changed" for row in item.canonical_contract["observations"]
        if row.get("change_tracking", True)
    }


def test_health_target_without_state_groups_or_thresholds_is_blocked():
    item = _catalog_item("environment.temperature")
    from copy import deepcopy
    from dataclasses import replace
    contract = deepcopy(item.canonical_contract)
    for row in contract['observations']:
        for key in ('health_policy', 'threshold_references', 'event_constraints', 'policy_constraints'):
            row.pop(key, None)
    item = replace(item, canonical_contract=contract)
    context = build_canonical_rule_context(
        check_item_id=item.id,
        check_item_version_id=item.version_id,
        result_mode=item.result_mode,
        assessment_mode=item.assessment_mode,
        canonical_contract=item.canonical_contract,
    )

    result = generate_canonical_rule_candidates(context)

    assert result["success"] is False
    assert result["blockers"] == [
        {
            "code": "canonical_rule_semantics_missing",
            "observation_type": "environment.temperature.current",
            "message": ("标准语义 B 声明需要健康判断，但尚未提供可执行的状态分组或阈值约束。"),
        }
    ]


@pytest.mark.parametrize(
    ("check_item_id", "observation_type", "warning_field", "alarm_field"),
    [
        (
            "system.cpu_usage",
            "device.cpu.utilization",
            "cpu_high_warning_percent",
            "cpu_high_alarm_percent",
        ),
        (
            "system.memory_usage",
            "device.memory.utilization",
            "memory_high_warning_percent",
            "memory_high_alarm_percent",
        ),
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
    assert result["success"] is True
    assert not result["change_policies"]
    health_rules = [row for row in result["event_rules"] if row["observation_type"] == observation_type]
    assert {row["condition"]["reference_field"] for row in health_rules} == {warning_field, alarm_field}
    assert all("threshold" not in row["condition"] for row in health_rules)
    target = next(row for row in item.canonical_contract['observations'] if row['observation_type'] == observation_type)
    assert target['health_policy']['baseline_mode'] == 'trend_only'


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
    assert result['success'] is True
    assert not any(row['observation_type'] == 'interface.optical.rx_power' for row in result['change_policies'])
    assert not any(row['event_type'] == 'OpticalRxPowerDegraded' for row in result['event_rules'])
    rx = next(row for row in item.canonical_contract['observations'] if row['observation_type'] == 'interface.optical.rx_power')
    assert rx['health_policy']['trend'] == {
        'kind': 'linear_power_drop', 'threshold_ratio': 0.1,
        'identity_fields': ['part_number', 'serial_number'], 'require_identity': False,
    }
