from __future__ import annotations

from datetime import datetime, timezone

from src.core.check_items.assessment_policy import (
    build_target_assessment_policy_snapshot,
    canonical_digest,
    resolve_assessment_basis,
)
from src.core.check_items.catalog import load_catalog
import pytest

CPU_CONTRACT = {
    "entity_type": "cpu_scope",
    "observations": [
        {
            "observation_type": "device.cpu.utilization",
            "semantic_type": "gauge",
            "unit": "percent",
            "policy_constraints": {
                "required_thresholds": {"high": 80},
                "required_deadband": 5,
            },
        }
    ],
}


def _approved_source(
    policy_id: str,
    *,
    value: float | None = None,
    expected_states: list[str] | None = None,
) -> dict:
    source = {
        "policy_id": policy_id,
        "version": 1,
        "status": "approved",
        "approved_by": "network-sme",
        "approved_at": "2026-08-02T10:00:00+00:00",
        "check_item_id": "system.cpu_usage",
        "observation_type": "device.cpu.utilization",
        "scope": {},
        "thresholds": (
            {"high": {"value": value, "unit": "percent", "hysteresis": 5}}
            if value is not None
            else {}
        ),
        "expected_states": expected_states or [],
        "evidence_refs": [f"approval:{policy_id}"],
    }
    source["digest"] = canonical_digest(
        {
            key: value
            for key, value in source.items()
            if key
            not in {"digest", "status", "approved_at", "approved_by"}
        }
    )
    return source


def _resign(source: dict) -> dict:
    source["digest"] = canonical_digest(
        {
            key: value
            for key, value in source.items()
            if key
            not in {"digest", "status", "approved_at", "approved_by"}
        }
    )
    return source


def _cpu_observation(*, native_threshold: float | None = None) -> dict:
    context = {}
    if native_threshold is not None:
        context = {
            "native_high": native_threshold,
            "threshold_source": "device_output",
            "threshold_reference_sources": {
                "high": {"field_name": "native_high", "source": "same_record"}
            },
        }
    return {
        "id": "observation-cpu-1",
        "device_id": "device-1",
        "entity_type": "cpu_scope",
        "entity_key": "system",
        "observation_type": "device.cpu.utilization",
        "field": "cpu_usage",
        "value": 88,
        "unit": "percent",
        "observed_at": datetime(2026, 8, 2, 10, tzinfo=timezone.utc),
        "context": context,
    }


def _cpu_rule() -> dict:
    return {
        "rule_id": "cpu-high",
        "version_id": "cpu-high-v1",
        "version": 1,
        "digest": "a" * 64,
        "condition": {
            "kind": "threshold",
            "threshold_role": "high",
            "fields": ["cpu_usage"],
            "gte": 80,
        },
    }


def _resolve(snapshot: dict, observation: dict | None = None):
    return resolve_assessment_basis(
        rule=_cpu_rule(),
        observation=observation or _cpu_observation(),
        policy_snapshot=snapshot,
        runtime_bundle_version_id="bundle-v1",
        runtime_bundle_digest="b" * 64,
    )


def test_device_output_has_priority_over_inventory_scope_and_catalog() -> None:
    device = {
        "id": "device-1",
        "site": "xian",
        "role": "core",
        "attributes": {
            "assessment_policy_bindings": [
                _approved_source("inventory-cpu", value=90)
            ]
        },
    }
    scope_source = _approved_source("site-cpu", value=85)
    scope_source["scope"] = {"sites": ["xian"]}
    _resign(scope_source)
    snapshot = build_target_assessment_policy_snapshot(
        check_item_id="system.cpu_usage",
        check_item_version_id="system.cpu_usage-v5",
        canonical_contract=CPU_CONTRACT,
        device=device,
        scope_overrides=[scope_source],
    )

    result = _resolve(snapshot, _cpu_observation(native_threshold=95))

    assert result.status == "resolved"
    assert result.effective_condition["gte"] == 95
    assert result.basis["threshold_binding"]["source_kind"] == "device_output"
    considered = result.basis["threshold_binding"]["considered_sources"]
    assert [item["source_kind"] for item in considered] == [
        "device_output",
        "inventory_profile",
        "scope_override",
        "catalog_default",
    ]
    assert [item["selected"] for item in considered] == [
        True,
        False,
        False,
        False,
    ]
    assert "None" not in result.basis["evidence_refs"]


def test_inventory_profile_beats_scope_override_and_catalog_default() -> None:
    device = {
        "id": "device-1",
        "site": "xian",
        "attributes": {
            "assessment_policy_bindings": [
                _approved_source("inventory-cpu", value=90)
            ]
        },
    }
    scope_source = _approved_source("site-cpu", value=85)
    scope_source["scope"] = {"sites": ["xian"]}
    _resign(scope_source)
    snapshot = build_target_assessment_policy_snapshot(
        check_item_id="system.cpu_usage",
        check_item_version_id="system.cpu_usage-v5",
        canonical_contract=CPU_CONTRACT,
        device=device,
        scope_overrides=[scope_source],
    )

    result = _resolve(snapshot)

    assert result.effective_condition["gte"] == 90
    assert result.basis["threshold_binding"]["source_kind"] == "inventory_profile"


@pytest.mark.parametrize(
    ("check_item_id", "observation_type", "field", "reference_field"),
    [
        ("system.cpu_usage", "device.cpu.utilization", "cpu_usage_percent", "cpu_high_warning_percent"),
        ("system.memory_usage", "device.memory.utilization", "memory_usage_percent", "memory_high_warning_percent"),
    ],
)
def test_device_profiles_resolve_different_cpu_and_memory_thresholds(
    check_item_id: str,
    observation_type: str,
    field: str,
    reference_field: str,
) -> None:
    item = load_catalog().check_item(check_item_id)
    assert item is not None
    resolved_values = []
    for device_id, threshold in (("model-a", 80), ("model-b", 95)):
        source = _approved_source(f"{device_id}-threshold", value=threshold)
        source["check_item_id"] = check_item_id
        source["observation_type"] = observation_type
        _resign(source)
        snapshot = build_target_assessment_policy_snapshot(
            check_item_id=check_item_id,
            check_item_version_id=item.version_id,
            canonical_contract=item.canonical_contract,
            device={
                "id": device_id,
                "attributes": {"assessment_policy_bindings": [source]},
            },
        )
        observation = {
            **_cpu_observation(),
            "device_id": device_id,
            "observation_type": observation_type,
            "field": field,
            "context": {},
        }
        rule = {
            **_cpu_rule(),
            "condition": {
                "kind": "threshold_field_gte",
                "threshold_role": "high",
                "fields": [field],
                "reference_field": reference_field,
            },
        }
        result = resolve_assessment_basis(
            rule=rule,
            observation=observation,
            policy_snapshot=snapshot,
            runtime_bundle_version_id="bundle-v1",
            runtime_bundle_digest="b" * 64,
        )
        resolved_values.append(result.effective_condition["gte"])
        assert result.basis["threshold_binding"]["source_kind"] == "inventory_profile"

    assert resolved_values == [80.0, 95.0]


def test_scope_override_beats_explicit_catalog_default() -> None:
    source = _approved_source("site-cpu", value=85)
    source["scope"] = {"sites": ["xian"]}
    _resign(source)
    snapshot = build_target_assessment_policy_snapshot(
        check_item_id="system.cpu_usage",
        check_item_version_id="system.cpu_usage-v5",
        canonical_contract=CPU_CONTRACT,
        device={"id": "device-1", "site": "xian"},
        scope_overrides=[source],
    )

    result = _resolve(snapshot)

    assert result.effective_condition["gte"] == 85
    assert result.basis["threshold_binding"]["source_kind"] == "scope_override"


def test_equal_priority_conflict_fails_closed() -> None:
    snapshot = build_target_assessment_policy_snapshot(
        check_item_id="system.cpu_usage",
        check_item_version_id="system.cpu_usage-v5",
        canonical_contract=CPU_CONTRACT,
        device={
            "id": "device-1",
            "attributes": {
                "assessment_policy_bindings": [
                    _approved_source("inventory-a", value=90),
                    _approved_source("inventory-b", value=95),
                ]
            },
        },
    )

    result = _resolve(snapshot)

    assert result.status == "conflict"
    assert result.blocker_code == "assessment_policy_conflict"
    assert result.should_evaluate is False
    assert len(result.basis["threshold_binding"]["conflicts"]) == 2


def test_required_device_threshold_missing_fails_closed() -> None:
    optical_contract = {
        "entity_type": "interface_optical",
        "observations": [
            {
                "observation_type": "interface.optical.rx_power",
                "semantic_type": "gauge",
                "unit": "dbm",
                "policy_constraints": {
                    "require_structured_threshold_references": True,
                    "required_threshold_reference_any": ["low", "critical_low"],
                },
            }
        ],
    }
    snapshot = build_target_assessment_policy_snapshot(
        check_item_id="interface.optical_power",
        check_item_version_id="interface.optical_power-v6",
        canonical_contract=optical_contract,
        device={"id": "device-1"},
    )
    rule = {
        "rule_id": "optical-rx-low",
        "version_id": "optical-rx-low-v1",
        "digest": "c" * 64,
        "condition": {
            "kind": "threshold_field_lte",
            "threshold_role": "low",
            "fields": ["rx_power_dbm"],
            "reference_field": "rx_low_warning_dbm",
        },
    }
    observation = {
        **_cpu_observation(),
        "observation_type": "interface.optical.rx_power",
        "entity_type": "interface_optical",
        "entity_key": "Ethernet1/1",
        "field": "rx_power_dbm",
        "value": -12,
        "unit": "dbm",
        "context": {},
    }

    result = resolve_assessment_basis(
        rule=rule,
        observation=observation,
        policy_snapshot=snapshot,
        runtime_bundle_version_id="bundle-v1",
        runtime_bundle_digest="b" * 64,
    )

    assert result.status == "missing"
    assert result.blocker_code == "assessment_policy_missing"


def test_expected_operational_intent_is_required_for_current_state_fault() -> None:
    interface_contract = {
        "entity_type": "interface",
        "observations": [
            {
                "observation_type": "interface.oper_status",
                "semantic_type": "state",
                "event_constraints": {"allow_initial_transition_fault": False},
            }
        ],
    }
    snapshot = build_target_assessment_policy_snapshot(
        check_item_id="interface.oper_status",
        check_item_version_id="interface.oper_status-v4",
        canonical_contract=interface_contract,
        device={"id": "device-1"},
    )
    rule = {
        "rule_id": "interface-down",
        "version_id": "interface-down-v1",
        "digest": "d" * 64,
        "condition": {
            "kind": "transition",
            "fields": ["status"],
            "to_group": "down",
        },
    }
    observation = {
        **_cpu_observation(),
        "observation_type": "interface.oper_status",
        "entity_type": "interface",
        "entity_key": "Ethernet1/1",
        "field": "status",
        "value": "down",
        "unit": None,
    }

    result = resolve_assessment_basis(
        rule=rule,
        observation=observation,
        policy_snapshot=snapshot,
        runtime_bundle_version_id="bundle-v1",
        runtime_bundle_digest="b" * 64,
    )

    assert result.status == "missing"
    assert result.blocker_code == "expected_state_missing"


def test_expired_relevant_policy_source_fails_closed() -> None:
    source = _approved_source("inventory-cpu", value=90)
    source["expires_at"] = "2026-08-03T00:00:00+00:00"
    _resign(source)
    snapshot = build_target_assessment_policy_snapshot(
        check_item_id="system.cpu_usage",
        check_item_version_id="system.cpu_usage-v5",
        canonical_contract=CPU_CONTRACT,
        device={
            "id": "device-1",
            "attributes": {"assessment_policy_bindings": [source]},
        },
        policy_effective_at="2026-08-03T00:00:00+00:00",
    )

    result = _resolve(snapshot)

    assert result.status == "conflict"
    assert result.blocker_code == "assessment_policy_source_invalid"
    assert result.basis["policy_resolution"]["conflicts"][0][
        "reason_code"
    ] == "policy_expired"


def test_invalid_source_for_another_observation_does_not_poison_cpu() -> None:
    unrelated = _approved_source("memory-draft", value=90)
    unrelated["observation_type"] = "device.memory.utilization"
    unrelated["status"] = "draft"
    _resign(unrelated)
    snapshot = build_target_assessment_policy_snapshot(
        check_item_id="system.cpu_usage",
        check_item_version_id="system.cpu_usage-v5",
        canonical_contract=CPU_CONTRACT,
        device={
            "id": "device-1",
            "attributes": {"assessment_policy_bindings": [unrelated]},
        },
        policy_effective_at="2026-08-03T00:00:00+00:00",
    )

    result = _resolve(snapshot)

    assert result.status == "resolved"
    assert result.effective_condition["gte"] == 80
    assert result.basis["threshold_binding"]["source_kind"] == "catalog_default"


def test_unknown_legacy_applicability_fails_closed_before_assessment() -> None:
    snapshot = build_target_assessment_policy_snapshot(
        check_item_id="interface.optical_power",
        check_item_version_id="interface.optical_power-v1",
        canonical_contract={
            "observations": [
                {
                    "observation_type": "interface.optical.rx_power",
                    "unit": "dBm",
                }
            ]
        },
        device={"device_id": "device-without-capability-inventory"},
        applicability_requirement={"capability": "optical_diagnostics"},
        policy_effective_at=datetime.now(timezone.utc),
    )

    assert snapshot["applicability_decision"]["status"] == "unknown"

    result = resolve_assessment_basis(
        rule={
            "rule_id": "optical-low",
            "version_id": "optical-low-v1",
            "digest": "r" * 64,
            "condition": {
                "kind": "threshold_lte",
                "threshold_role": "low",
            },
        },
        observation={
            "id": "optical-observation",
            "snapshot_id": "snapshot-optical",
            "device_id": "device-without-capability-inventory",
            "entity_type": "interface_optical",
            "entity_key": "Ethernet1/1",
            "observation_type": "interface.optical.rx_power",
            "value": -18,
            "unit": "dBm",
        },
        policy_snapshot=snapshot,
        runtime_bundle_version_id="bundle-optical-v1",
        runtime_bundle_digest="b" * 64,
    )

    assert result.status == "unknown"
    assert result.blocker_code == "assessment_applicability_unknown"
    assert result.should_evaluate is False
    assert result.basis["applicability_decision"]["status"] == "unknown"
