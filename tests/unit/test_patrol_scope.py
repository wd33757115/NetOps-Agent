from __future__ import annotations

from src.core.check_items.scope import resolve_patrol_scope


VERSIONS = {
    "system.cpu_usage": "cpu-v1",
    "system.memory_usage": "memory-v1",
    "interface.oper_status": "interface-v1",
}


def _contract() -> dict:
    return {
        "schema_version": "patrol-scope-contract/v1",
        "scope_contract_id": "core-quarterly",
        "scope_contract_version_id": "scope-version-3",
        "version": 3,
        "device_role_rules": [
            {
                "roles": ["core"],
                "required_check_items": [
                    "system.cpu_usage",
                    "system.memory_usage",
                ],
                "recommended_check_items": [
                    "interface.oper_status",
                ],
            }
        ],
        "allowed_exclusion_codes": ["approved_maintenance"],
        "claim_boundary": "selected_network_scope",
    }


def test_required_unselected_pair_stays_visible_and_unresolved() -> None:
    result = resolve_patrol_scope(
        contract=_contract(),
        devices=[{"device_id": "d1", "device_role": "core"}],
        selected_check_item_ids_by_device={
            "d1": ["system.cpu_usage"],
        },
        check_item_version_ids=VERSIONS,
    )

    assert not result.scope_reconciled
    assert [
        (item["check_item_id"], item["decision"])
        for item in result.decisions
    ] == [
        ("system.cpu_usage", "included"),
        ("system.memory_usage", "unresolved"),
    ]
    assert len(result.intended_target_pairs) == 2
    assert len(result.selected_target_pairs) == 1


def test_approved_exclusion_reconciles_required_pair() -> None:
    result = resolve_patrol_scope(
        contract=_contract(),
        devices=[{"device_id": "d1", "role": "core"}],
        selected_check_item_ids_by_device={
            "d1": ["system.cpu_usage"],
        },
        check_item_version_ids=VERSIONS,
        exclusions={
            "d1\u001fsystem.memory_usage": {
                "reason_code": "approved_maintenance",
                "approved_by": "scope-approver",
            }
        },
    )

    assert result.scope_reconciled
    memory = next(
        item
        for item in result.decisions
        if item["check_item_id"] == "system.memory_usage"
    )
    assert memory["decision"] == "approved_exclusion"


def test_unapproved_exclusion_cannot_hide_required_pair() -> None:
    result = resolve_patrol_scope(
        contract=_contract(),
        devices=[{"device_id": "d1", "role": "core"}],
        selected_check_item_ids_by_device={
            "d1": ["system.cpu_usage"],
        },
        check_item_version_ids=VERSIONS,
        exclusions={
            "d1\u001fsystem.memory_usage": {
                "reason_code": "approved_maintenance",
            }
        },
    )

    memory = next(
        item
        for item in result.decisions
        if item["check_item_id"] == "system.memory_usage"
    )
    assert memory["decision"] == "unresolved"
    assert memory["reason_codes"] == ["scope_exclusion_not_approved"]


def test_ad_hoc_scope_is_reconciled_but_limited_to_selected_scope() -> None:
    result = resolve_patrol_scope(
        contract=None,
        devices=[{"device_id": "d1"}],
        selected_check_item_ids_by_device={
            "d1": ["system.cpu_usage"],
        },
        check_item_version_ids=VERSIONS,
    )

    assert result.scope_reconciled
    assert result.claim_boundary == "selected_scope_only"
    assert result.contract["origin"] == "legacy_profile_selection"
    assert result.contract["scope_contract_id"].startswith(
        "ad-hoc-selected-scope:"
    )


def test_contract_without_matching_device_role_is_not_reconciled() -> None:
    result = resolve_patrol_scope(
        contract=_contract(),
        devices=[{"device_id": "d1", "role": "access"}],
        selected_check_item_ids_by_device={
            "d1": ["interface.oper_status"],
        },
        check_item_version_ids=VERSIONS,
    )

    assert not result.scope_reconciled
    assert result.unmatched_device_ids == ("d1",)
