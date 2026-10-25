"""Versioned policy revision explicitly requested by the patrol operator."""

from copy import deepcopy
from dataclasses import asdict

from src.apps.rule_governance.backend.check_items.catalog import (
    CheckItemCatalog,
    build_check_item_definition,
    build_collection_implementation,
)

REVISION = "device-limits-with-directional-trends-v2"
TARGETS = {"system.cpu_usage", "system.memory_usage", "interface.optical_power"}


def revise_health_catalog(catalog: CheckItemCatalog) -> CheckItemCatalog:
    """Preserve manual command associations; re-pin existing adapters to new semantics."""
    items = []
    changed = {}
    for item in catalog.check_items:
        raw = deepcopy(asdict(item))
        if item.id in TARGETS:
            dirty = False
            for row in raw["canonical_contract"].get("observations", []):
                field = row.get("canonical_field")
                policy = None
                if (field == "memory_usage_percent" and "window" not in row.get("dimensions", [])
                        and "window" not in row.get("fixed_dimensions", {})):
                    row["dimensions"] = [*row.get("dimensions", []), "window"]
                    dirty = True
                if field in {"cpu_usage_percent", "memory_usage_percent"}:
                    prefix = "cpu" if field.startswith("cpu") else "memory"
                    policy = {
                        "revision": REVISION,
                        "window": "5m",
                        "baseline_mode": "trend_only",
                        "trend": {
                            "kind": "utilization_increase",
                            "threshold_ratio": 0.10,
                            "min_increase_points": 10,
                        },
                        "device_thresholds": {
                            "high": [
                                f"{prefix}_high_warning_percent",
                                f"{prefix}_high_alarm_percent",
                            ]
                        },
                    }
                elif field in {"rx_power_dbm", "tx_power_dbm"}:
                    prefix = field[:2]
                    policy = {
                        "revision": REVISION,
                        "baseline_mode": "trend_only",
                        "trend": {
                            "kind": "linear_power_drop",
                            "threshold_ratio": 0.10,
                            "identity_fields": ["part_number", "serial_number"],
                            "require_identity": False,
                        },
                        "device_thresholds": {
                            "high": [f"{prefix}_high_warning_dbm", f"{prefix}_high_alarm_dbm"],
                            "low": [f"{prefix}_low_warning_dbm", f"{prefix}_low_alarm_dbm"],
                        },
                    }
                    # Trend notices must not also be generated as legacy fault events.
                    if row.pop("baseline_comparison", None):
                        dirty = True
                    constraints = row.get("event_constraints") or {}
                    removed = (constraints.get("role_event_types") or {}).pop(
                        "baseline_degradation", None
                    )
                    if removed:
                        constraints["required_event_types"] = [
                            t for t in constraints.get("required_event_types", []) if t != removed
                        ]
                        constraints.get("required_severities", {}).pop(removed, None)
                        constraints.get("required_event_classes", {}).pop(removed, None)
                        constraints.get("required_condition_kinds", {}).pop(removed, None)
                if policy and row.get("health_policy") != policy:
                    row["health_policy"] = policy
                    dirty = True
            if dirty:
                raw["version"] += 1
                item = build_check_item_definition(raw)
                changed[item.id] = item.version_id
        items.append(item)
    implementations = []
    versions = {item.id: item.version_id for item in items}
    for implementation in catalog.implementations:
        raw = deepcopy(asdict(implementation))
        raw["commands"] = list(raw["commands"])
        # Existing declared associations are the sole authority for re-binding.
        if set(implementation.check_item_ids) & TARGETS:
            dirty = bool(set(implementation.check_item_ids) & changed.keys())
            for output in raw["adapter_spec"].get("outputs", []):
                check_id = output["check_item_id"]
                same_name = (
                    next(i for i in items if i.id == check_id)
                    .canonical_contract.get("value_contract") == "same-name/v1"
                )
                if check_id == "system.memory_usage" and not same_name:
                    binding = (
                        (output.get("projection") or {})
                        .get("fields", {})
                        .get("memory_usage_percent")
                    )
                    if binding and not binding.get("dimensions"):
                        # Existing bindings represent the instantaneous Mem row, not a 5m sample.
                        binding["dimensions"] = {"window": {"literal": "instant"}}
                        dirty = True
                if (
                    check_id in TARGETS
                    and output.get("check_item_version_id") != versions[check_id]
                ):
                    output["check_item_version_id"] = versions[check_id]
                    dirty = True
            if dirty:
                raw["version"] += 1
                implementation = build_collection_implementation(
                    raw, known_check_item_ids=set(versions)
                )
        implementations.append(implementation)
    return CheckItemCatalog(
        catalog_version=catalog.catalog_version + 1,
        check_items=tuple(items),
        implementations=tuple(implementations),
    )
