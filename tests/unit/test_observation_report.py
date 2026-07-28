from __future__ import annotations

from src.core.patrol.observation_report import build_observation_report_tables


def _fact(
    observation_id: str,
    *,
    vendor: str,
    field_name: str,
    observation_type: str,
    value: object,
    dimensions: dict | None = None,
    entity_type: str = "device",
    entity_key: str = "__singleton__",
):
    return {
        "id": observation_id,
        "device_id": f"{vendor}-device",
        "vendor": vendor,
        "model": "test",
        "entity_type": entity_type,
        "entity_key": entity_key,
        "field_name": field_name,
        "observation_type": observation_type,
        "dimensions_json": dimensions or {},
        "value_number": value if isinstance(value, (int, float)) else None,
        "value_text": value if isinstance(value, str) else None,
        "observed_at": "2026-07-27T10:00:00+08:00",
    }


def test_cross_vendor_cpu_facts_share_one_report_schema():
    tables = build_observation_report_tables(
        [
            _fact(
                "h3c-cpu",
                vendor="H3C",
                field_name="cpu_5s",
                observation_type="device.cpu.utilization",
                value=24,
                dimensions={"scope": "system", "window": "5s"},
            ),
            _fact(
                "cisco-cpu",
                vendor="Cisco",
                field_name="cpu_total_5_sec",
                observation_type="device.cpu.utilization",
                value=18,
                dimensions={"scope": "system", "window": "5s"},
            ),
        ]
    )

    cpu = next(table for table in tables if table.key == "cpu")
    assert cpu.headers[5] == "CPU 5秒(%)"
    assert {row[5] for row in cpu.rows} == {18, 24}
    assert not any(table.key == "other_structured" for table in tables)


def test_unknown_canonical_fact_is_preserved_in_other_and_evidence_tables():
    tables = build_observation_report_tables(
        [
            _fact(
                "custom",
                vendor="H3C",
                field_name="session_count",
                observation_type="device.vpn.session_count",
                value=8,
            )
        ]
    )

    other = next(table for table in tables if table.key == "other_structured")
    evidence = next(table for table in tables if table.key == "evidence_index")
    assert other.rows[0][5] == "device.vpn.session_count"
    assert evidence.rows == other.rows
