from __future__ import annotations

from copy import deepcopy

import pytest

from src.core.check_items.catalog import load_catalog
from src.core.check_items.report_projection import (
    build_report_projection_snapshot,
    load_report_projection_catalog,
    project_report_tables,
    validate_report_projection_snapshot,
)


def _cpu_snapshot() -> dict:
    return build_report_projection_snapshot(
        {
            "check-item:system.cpu_usage:v5": {
                "check_item_id": "system.cpu_usage",
                "report_projection": "cpu",
            }
        }
    )


def _cpu_matrix(execution_id: str, device_name: str) -> dict:
    return {
        "execution_id": execution_id,
        "device_id": f"device-{execution_id}",
        "device_name": device_name,
        "management_ip": "192.0.2.1",
        "check_item_version_id": "check-item:system.cpu_usage:v5",
        "execution_status": "completed",
        "assessment_status": "healthy",
        "completeness_status": "complete",
        "comparison_status": "comparable",
        "threshold_unknown_count": 0,
        "blocking_reason_codes": [],
        "runtime_bundle_digest": "a" * 64,
    }


def test_projection_catalog_covers_every_current_check_item_reference():
    check_item_catalog = load_catalog()
    projection_catalog = load_report_projection_catalog()

    # CheckItems using auto projection do not need a separately registered profile.
    assert projection_catalog.profiles
    snapshot = build_report_projection_snapshot(
        {
            item.version_id: {
                "check_item_id": item.id,
                "report_projection": item.report_projection,
                "version": item.version,
                "result_mode": item.result_mode,
                "canonical_contract": item.canonical_contract,
            }
            for item in check_item_catalog.check_items
        }
    )
    validate_report_projection_snapshot(snapshot)
    assert len(snapshot["bindings"]) == len(check_item_catalog.check_items)


def test_cross_vendor_cpu_facts_share_one_stable_report_schema():
    report = {
        "report_projection_snapshot": _cpu_snapshot(),
        "matrix": [
            _cpu_matrix("h3c", "H3C-Core"),
            _cpu_matrix("cisco", "Cisco-Core"),
        ],
        "observations": [
            {
                "id": "obs-h3c",
                "execution_ids": ["h3c"],
                "snapshot_id": "snapshot-h3c",
                "device_id": "device-h3c",
                "entity_type": "cpu_scope",
                "entity_key": '["system","0"]',
                "observation_type": "device.cpu.utilization",
                "dimensions": {"window": "5m"},
                "value_number": 27,
                "observed_at": "2026-08-01T01:00:00Z",
                "parser_template_id": "parser-h3c",
            },
            {
                "id": "obs-cisco",
                "execution_ids": ["cisco"],
                "snapshot_id": "snapshot-cisco",
                "device_id": "device-cisco",
                "entity_type": "cpu_scope",
                "entity_key": '["system","0"]',
                "observation_type": "device.cpu.utilization",
                "dimensions": {"window": "5m"},
                "value_number": 41,
                "observed_at": "2026-08-01T01:00:00Z",
                "parser_template_id": "parser-cisco",
            },
        ],
        "changes": [],
    }

    tables = project_report_tables(report)

    assert len(tables) == 1
    table = tables[0]
    assert table.profile_ref == "cpu@1"
    assert table.sheet_name == "CPU"
    cpu_5m_index = table.column_ids.index("cpu_5m_pct")
    assert {row[cpu_5m_index] for row in table.rows} == {27, 41}
    assert all("command" not in column for column in table.column_ids)


def test_projection_keeps_an_unassessed_target_when_no_fact_exists():
    matrix = _cpu_matrix("missing", "No-Data-Core")
    matrix.update(
        {
            "assessment_status": "unable_to_determine",
            "completeness_status": "insufficient",
            "blocking_reason_codes": ["no_observations"],
        }
    )
    tables = project_report_tables(
        {
            "report_projection_snapshot": _cpu_snapshot(),
            "matrix": [matrix],
            "observations": [],
            "changes": [],
        }
    )

    assert len(tables[0].rows) == 1
    row = dict(zip(tables[0].column_ids, tables[0].rows[0], strict=True))
    assert row["device_name"] == "No-Data-Core"
    assert row["cpu_5m_pct"] is None
    assert row["assessment_status"] == "unable_to_determine"
    assert row["blocking_reason_codes"] == ["no_observations"]


def test_projection_snapshot_tampering_is_rejected():
    snapshot = deepcopy(_cpu_snapshot())
    snapshot["profiles"]["cpu@1"]["canonical_columns"][0]["source"] = (
        "device.memory.utilization"
    )

    with pytest.raises(ValueError, match="digest"):
        validate_report_projection_snapshot(snapshot)


def test_automatic_projection_freezes_new_check_item_columns():
    version_id = "check-item:system.custom_cpu:v1"
    snapshot = build_report_projection_snapshot(
        {
            version_id: {
                "check_item_id": "system.custom_cpu",
                "version": 1,
                "name": "Custom CPU health",
                "result_mode": "structured",
                "report_projection": "auto",
                "canonical_contract": {
                    "entity_type": "cpu_scope",
                    "identity_dimensions": ["scope", "scope_id"],
                    "observations": [
                        {
                            "observation_type": "device.cpu.utilization",
                            "dimensions": ["window"],
                        }
                    ],
                },
            }
        }
    )

    validate_report_projection_snapshot(snapshot)
    profile_ref = snapshot["bindings"][version_id]["profile_ref"]
    assert profile_ref == "auto.system.custom_cpu@1"
    assert snapshot["profiles"][profile_ref]["projection_strategy"] == (
        "canonical_contract_auto"
    )
    column_ids = {
        item["column_id"]
        for item in snapshot["profiles"][profile_ref]["canonical_columns"]
    }
    assert "device_cpu_utilization_window_5m" in column_ids
    assert snapshot["sheets"]["auto_system_custom_cpu"] == "Custom CPU health"

    matrix = _cpu_matrix("custom", "Custom-Core")
    matrix["check_item_version_id"] = version_id
    tables = project_report_tables(
        {
            "report_projection_snapshot": snapshot,
            "matrix": [matrix],
            "observations": [
                {
                    "id": "obs-custom",
                    "execution_ids": ["custom"],
                    "snapshot_id": "snapshot-custom",
                    "entity_type": "cpu_scope",
                    "entity_key": '["system","0"]',
                    "observation_type": "device.cpu.utilization",
                    "dimensions": {"window": "5m"},
                    "value_number": 33,
                    "observed_at": "2026-08-09T01:00:00Z",
                    "parser_template_id": "parser-custom",
                }
            ],
            "changes": [],
        }
    )
    row = dict(zip(tables[0].column_ids, tables[0].rows[0], strict=True))
    assert row["device_cpu_utilization_window_5m"] == 33
