from __future__ import annotations

from src.apps.rule_governance.backend.check_items.system_items import system_check_item_ids
from src.core.check_items.catalog import load_catalog
from src.core.check_items.presentation import (
    check_item_display_description,
    check_item_display_name,
    supported_check_item_presentations,
)


def test_active_catalog_has_complete_chinese_presentation_coverage():
    catalog = load_catalog()

    # System base check items (topology discovery) carry Chinese names themselves.
    system = system_check_item_ids()
    assert set(supported_check_item_presentations()) == {
        item.id for item in catalog.check_items
    } - system
    assert all(
        any("一" <= char <= "鿿" for char in item.name)
        for item in catalog.check_items
        if item.id in system
    )
    assert check_item_display_name(
        "system.cpu_usage",
        "CPU utilization",
    ) == "CPU 使用率"
    assert check_item_display_description(
        "system.cpu_usage",
        "Evaluate CPU utilization.",
    ) == "评估设备、槽位或处理器的 CPU 使用率。"


def test_unknown_check_item_uses_catalog_fallback_without_changing_identity():
    assert check_item_display_name(
        "vendor.future_check",
        "Future check",
    ) == "Future check"
    assert check_item_display_description(
        "vendor.future_check",
        "Future description.",
    ) == "Future description."
    assert check_item_display_name("vendor.future_check") == "vendor.future_check"
