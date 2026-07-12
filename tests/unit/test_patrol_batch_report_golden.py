from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.gateway import workflow_api


class _FixedDateTime:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 7, 12, tzinfo=timezone.utc).astimezone(tz)


def test_empty_patrol_batch_markdown_matches_golden(monkeypatch):
    summary = {
        "run_count": 0,
        "device_count": 0,
        "change_count": 0,
        "event_count": 0,
        "status_counts": {},
        "runs": [],
        "quality": {
            "status": "ready",
            "command_count": 0,
            "execution_success_count": 0,
            "parse_status_counts": {},
            "output_status_counts": {},
            "parser_eligible_count": 0,
            "parser_ready_count": 0,
            "parser_ready_rate": 1.0,
            "observation_count": 0,
            "snapshot_count": 0,
            "duplicate_snapshot_count": 0,
        },
        "no_event_summary": {
            "total": 0,
            "by_reason": {},
            "missing_rule_count": 0,
            "evaluated_no_match_count": 0,
        },
        "suspected_format_change_count": 0,
        "events": [],
        "changes": [],
        "action_items": [],
    }
    monkeypatch.setattr(workflow_api, "datetime", _FixedDateTime)
    monkeypatch.setattr(
        workflow_api,
        "_build_patrol_batch_summary",
        lambda patrol_batch_id, runs: summary,
    )

    actual = workflow_api._build_patrol_batch_report("batch-golden", [])
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "patrol_batch_report_empty_golden.md"
    )
    expected = fixture.read_text(encoding="utf-8").replace("\r\n", "\n")

    assert actual == expected
