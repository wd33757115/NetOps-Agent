from __future__ import annotations

from src.core.patrol import reporting
from src.core.patrol.reporting import render_run_report


def test_render_run_report_summarizes_events(monkeypatch):
    class FakeRepository:
        def list_changes(self, *, run_id=None, device_id=None):
            return [{"change_id": "change-1"}]

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

    monkeypatch.setattr(reporting, "ChangeEventRepository", FakeRepository)

    result = render_run_report(run_id="run-1")

    assert result["success"] is True
    assert result["change_count"] == 1
    assert result["event_count"] == 2
    assert result["event_types"] == {"InterfaceDown": 1, "CPUHigh": 1}
    assert "## Event Details" in result["markdown"]


def test_render_run_report_writes_html_and_xlsx(monkeypatch, tmp_path):
    class FakeRepository:
        def list_changes(self, *, run_id=None, device_id=None):
            return [{"change_id": "change-1"}]

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

    monkeypatch.setattr(reporting, "ChangeEventRepository", FakeRepository)
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
    assert html_path.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert xlsx_result["output_path"] == str(xlsx_path)
    assert xlsx_path.read_bytes().startswith(b"PK")
