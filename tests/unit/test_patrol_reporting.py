from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook
from src.core.patrol import reporting
from src.core.patrol.reporting import render_patrol_batch_xlsx, render_run_report


def _raw_change():
    return {
        "change_id": "raw-change-1",
        "device_id": "SW1",
        "command": "show running-config",
        "previous_snapshot_id": "snapshot-before",
        "current_snapshot_id": "snapshot-after",
        "dimensions": {
            "unstructured_diff": {
                "changed": True,
                "profile": "configuration",
                "profile_label": "Configuration",
                "added_line_count": 1,
                "removed_line_count": 1,
                "changed_line_count": 2,
                "similarity_ratio": 0.5,
                "categories": [
                    {"name": "security", "label": "Security", "line_count": 2}
                ],
                "preview": [
                    {
                        "operation": "added",
                        "category": "security",
                        "category_label": "Security",
                        "text": "snmp-agent community <redacted> ro",
                    }
                ],
            }
        },
    }


def test_render_run_report_summarizes_events(monkeypatch):
    class FakeRepository:
        def list_changes(self, *, run_id=None, device_id=None):
            return [_raw_change()]

        def list_events(self, *, run_id=None, device_id=None):
            return [
                {
                    "event_id": "event-1",
                    "event_type": "InterfaceDown",
                    "severity": "major",
                    "device_id": "SW1",
                    "entity_key": '["Eth1/1"]',
                    "occurred_at": "2026-06-20T10:00:00+00:00",
                },
                {
                    "event_id": "event-2",
                    "event_type": "CPUHigh",
                    "severity": "warning",
                    "device_id": "SW1",
                    "entity_key": "__singleton__",
                    "occurred_at": "2026-06-20T10:05:00+00:00",
                },
            ]

        def list_run_event_facts(self, *, run_id=None, device_id=None):
            return self.list_events(run_id=run_id, device_id=device_id)

        def summarize_run_events(self, *, run_id=None, device_id=None):
            return {
                "created_event_count": 2,
                "updated_event_count": 0,
                "recovered_event_count": 0,
                "open_after_run_count": 2,
                "current_open_event_count": 2,
            }

    monkeypatch.setattr(reporting, "ChangeEventRepository", FakeRepository)

    result = render_run_report(run_id="run-1")

    assert result["success"] is True
    assert result["change_count"] == 1
    assert result["event_count"] == 2
    assert result["event_types"] == {"InterfaceDown": 1, "CPUHigh": 1}
    assert result["unstructured_comparison"]["changed_output_count"] == 1
    assert "- New events: 2" in result["markdown"]
    assert "## Event Details" in result["markdown"]
    assert "## Non-structured Output Changes" in result["markdown"]
    assert "<redacted>" in result["markdown"]


def test_render_run_report_writes_html_and_xlsx(monkeypatch, tmp_path):
    class FakeRepository:
        def list_changes(self, *, run_id=None, device_id=None):
            return [_raw_change()]

        def list_events(self, *, run_id=None, device_id=None):
            return [
                {
                    "event_id": "event-1",
                    "event_type": "InterfaceDown",
                    "severity": "major",
                    "device_id": "SW1",
                    "entity_key": '["Eth1/1"]',
                    "occurred_at": "2026-06-20T10:00:00+00:00",
                }
            ]

        def list_run_event_facts(self, *, run_id=None, device_id=None):
            return self.list_events(run_id=run_id, device_id=device_id)

        def summarize_run_events(self, *, run_id=None, device_id=None):
            return {
                "created_event_count": 1,
                "updated_event_count": 0,
                "recovered_event_count": 0,
                "open_after_run_count": 1,
                "current_open_event_count": 1,
            }

    class FakeStateRepository:
        def list_run_observations(self, run_id, *, device_ids=None):
            assert run_id == "run-1"
            assert device_ids is None
            return [
                {
                    "id": "obs-cpu-5s",
                    "device_id": "SW1",
                    "vendor": "Cisco",
                    "model": "NEXUS9000",
                    "entity_type": "device",
                    "entity_key": "__singleton__",
                    "observation_type": "device.cpu.utilization",
                    "dimensions_json": {"scope": "system", "window": "5s"},
                    "value_number": 24,
                    "unit": "percent",
                    "canonical_command": "show processes cpu",
                    "field_name": "cpu_total_5_sec",
                    "parser_template_id": "parser-1",
                    "evidence_id": "evidence-1",
                    "observed_at": "2026-06-20T10:00:00+00:00",
                }
            ]

    monkeypatch.setattr(reporting, "ChangeEventRepository", FakeRepository)
    monkeypatch.setattr(reporting, "StateRepository", FakeStateRepository)
    html_path = tmp_path / "report.html"
    xlsx_path = tmp_path / "report.xlsx"

    html_result = render_run_report(
        run_id="run-1",
        output_path=str(html_path),
        output_format="html",
    )
    xlsx_result = render_run_report(
        run_id="run-1",
        output_path=str(xlsx_path),
        output_format="xlsx",
    )

    assert "<table>" in html_result["content"]
    assert "Non-structured Output Changes" in html_result["content"]
    assert "&lt;redacted&gt;" in html_result["content"]
    assert html_path.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert xlsx_result["output_path"] == str(xlsx_path)
    assert xlsx_result["observation_count"] == 1
    assert xlsx_path.read_bytes().startswith(b"PK")
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    assert {"总览", "CPU", "证据索引", "非结构化变更"}.issubset(
        set(workbook.sheetnames)
    )
    assert workbook["CPU"]["F2"].value == 24
    assert "<redacted>" in str(workbook["非结构化变更"]["F2"].value)


def test_render_patrol_batch_xlsx_unifies_cross_vendor_facts(monkeypatch):
    class FakeStateRepository:
        def list_observations_for_runs(self, run_ids):
            assert run_ids == ["snapshot-1", "snapshot-2"]
            return [
                {
                    "id": "h3c-cpu",
                    "device_id": "H3C-1",
                    "vendor": "H3C",
                    "model": "S5590",
                    "entity_type": "device",
                    "entity_key": "__singleton__",
                    "observation_type": "device.cpu.utilization",
                    "dimensions_json": {"scope": "system", "window": "5s"},
                    "value_number": 24,
                    "unit": "percent",
                    "canonical_command": "display cpu-usage",
                    "field_name": "five_sec",
                    "observed_at": "2026-07-27T10:00:00+08:00",
                },
                {
                    "id": "cisco-cpu",
                    "device_id": "Cisco-1",
                    "vendor": "Cisco",
                    "model": "NEXUS9000",
                    "entity_type": "device",
                    "entity_key": "__singleton__",
                    "observation_type": "device.cpu.utilization",
                    "dimensions_json": {"scope": "system", "window": "5s"},
                    "value_number": 18,
                    "unit": "percent",
                    "canonical_command": "show processes cpu",
                    "field_name": "cpu_total_5_sec",
                    "observed_at": "2026-07-27T10:00:00+08:00",
                },
            ]

    monkeypatch.setattr(reporting, "StateRepository", FakeStateRepository)
    summary = {
        "runs": [
            {"snapshot_run_id": "snapshot-2"},
            {"snapshot_run_id": "snapshot-1"},
        ],
        "device_count": 2,
        "changes": [],
        "events": [],
        "current_findings": [],
        "quality": {
            "status": "ready",
            "parser_ready_rate": 1.0,
            "observation_count": 2,
        },
        "unstructured_comparison": {},
        "assessment": {
            "status": "本次覆盖范围内未发现异常",
            "trust": "高",
            "reason": "活动规则已完成评估",
        },
        "current_state_rule_coverage": {
            "available": True,
            "evaluation_rate": 1.0,
        },
    }

    content = render_patrol_batch_xlsx("batch-1", summary)

    assert content.startswith(b"PK")
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    assert {
        "总览",
        "当前风险",
        "事件",
        "变化",
        "CPU",
        "证据索引",
    }.issubset(set(workbook.sheetnames))
    cpu_rows = list(workbook["CPU"].iter_rows(values_only=True))
    assert cpu_rows[0][5] == "CPU 5秒(%)"
    assert {row[5] for row in cpu_rows[1:]} == {18, 24}
    assert {row[-4] for row in cpu_rows[1:]} == {
        "覆盖范围内未发现当前异常项"
    }
