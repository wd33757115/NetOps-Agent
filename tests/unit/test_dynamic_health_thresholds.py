from __future__ import annotations

from src.core.check_items.canonical_rule_context import (
    build_canonical_rule_context,
    generate_canonical_rule_candidates,
)
from src.core.check_items.catalog import load_catalog
from src.core.check_items.current_state import _attach_baselines
from src.core.workflows.patrol_batch_service import evaluate_current_state


def _rx_rules() -> list[dict]:
    item = load_catalog().check_item("interface.optical_power")
    assert item is not None
    context = build_canonical_rule_context(
        check_item_id=item.id,
        check_item_version_id=item.version_id,
        result_mode=item.result_mode,
        assessment_mode=item.assessment_mode,
        canonical_contract=item.canonical_contract,
    )
    generated = generate_canonical_rule_candidates(context)
    return [
        {**rule, "rule_id": f"rx-{index}", "version_id": f"rx-{index}-v1", "digest": "a" * 64}
        for index, rule in enumerate(generated["event_rules"])
        if rule["observation_type"] == "interface.optical.rx_power"
    ]


def _observation(value: float, *, identity: bool = True) -> dict:
    context = {
        "rx_high_warning_dbm": 1.0,
        "rx_high_alarm_dbm": 2.0,
        "rx_low_warning_dbm": -10.0,
        "rx_low_alarm_dbm": -12.0,
    }
    if identity:
        context.update(part_number="SFP-10G-LR", serial_number="ABC123")
    return {
        "id": f"current-{value}",
        "device_id": "switch-1",
        "snapshot_id": "current-snapshot",
        "entity_type": "interface_optical",
        "entity_key": '["XGE1/0/1","aggregate"]',
        "field": "rx_power_dbm",
        "observation_type": "interface.optical.rx_power",
        "value": value,
        "unit": "dbm",
        "dimensions": {"channel": "aggregate"},
        "context": context,
        "observed_at": "2026-04-01T00:00:00+00:00",
    }


def _baseline() -> dict:
    row = _observation(-3.0)
    return {
        **row,
        "id": "baseline-rx",
        "snapshot_id": "baseline-snapshot",
        "observed_at": "2026-01-01T00:00:00+00:00",
    }


def _evaluate(value: float, *, identity: bool = True) -> dict:
    observations = _attach_baselines(
        [_observation(value, identity=identity)],
        [_baseline()],
    )
    return evaluate_current_state(
        observations,
        _rx_rules(),
        rule_source="test",
        rule_bundle_digest="b" * 64,
        runtime_bundle_version_id="bundle-v1",
    )


def test_linear_optical_loss_below_ten_percent_is_healthy():
    result = _evaluate(-3.3)

    assert result["finding_count"] == 0
    assert result["partial_threshold_observation_count"] == 0


def test_linear_optical_loss_above_ten_percent_is_degraded():
    result = _evaluate(-3.6)

    assert [row["event_type"] for row in result["findings"]] == [
        "OpticalRxPowerDegraded"
    ]
    binding = result["findings"][0]["assessment_basis"]["baseline_binding"]
    assert round(binding["loss_ratio"], 3) == 0.129


def test_optical_baseline_without_module_identity_is_unknown():
    result = _evaluate(-3.6, identity=False)

    assert result["finding_count"] == 0
    assert result["partial_threshold_observation_count"] == 1
    assert result["assessment_policy_blocking_reason_codes"] == [
        "baseline_module_identity_missing"
    ]
