import argparse
import json
from types import SimpleNamespace

from scripts.test import phase1_6_e2e_validation


def test_phase1_6_validation_main_reports_prerequisite_failure(monkeypatch, capsys):
    def fail_run(_args: argparse.Namespace):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr(phase1_6_e2e_validation, "run", fail_run)
    monkeypatch.setattr(
        "sys.argv",
        ["phase1_6_e2e_validation.py"],
    )

    exit_code = phase1_6_e2e_validation.main()

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output["success"] is False
    assert output["error_type"] == "RuntimeError"
    assert "postgres unavailable" in output["error"]
    assert any("docker compose" in step for step in output["recovery"])


def test_phase1_6_validation_main_reports_real_postgres_failure(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["phase1_6_e2e_validation.py"],
    )

    exit_code = phase1_6_e2e_validation.main()

    output = json.loads(capsys.readouterr().out)
    if exit_code == 0:
        assert output["success"] is True
        return
    assert exit_code == 2
    assert output["success"] is False
    assert "postgres" in output
    assert any("alembic upgrade head" in step for step in output["recovery"])


def test_snapshot_summary_groups_parser_coverage_gaps():
    rows = [
        SimpleNamespace(
            parse_status="parsed",
            vendor="Cisco",
            model="N9K-C9364C",
            canonical_command="show version",
        ),
        SimpleNamespace(
            parse_status="parse_empty",
            vendor="Cisco",
            model="N9K-C9364C",
            canonical_command="show vrrp brief",
        ),
        SimpleNamespace(
            parse_status="template_missing",
            vendor="Cisco",
            model="N9K-C9364C",
            canonical_command="show inventory",
        ),
        SimpleNamespace(
            parse_status="template_missing",
            vendor="Cisco",
            model="N9K-C9364C",
            canonical_command="show inventory",
        ),
        SimpleNamespace(
            parse_status="parse_failed",
            vendor="H3C",
            model="S6860-54HF",
            canonical_command="display fan",
        ),
        SimpleNamespace(
            parse_status="ignored",
            vendor="H3C",
            model="S6860-54HF",
            canonical_command="display logbuffer",
        ),
    ]

    summary = phase1_6_e2e_validation._snapshot_summary_from_rows(rows)

    assert summary["snapshot_count"] == 6
    assert summary["eligible_snapshot_count"] == 5
    assert summary["structured_snapshot_count"] == 2
    assert summary["coverage_rate"] == 0.4
    assert summary["template_missing_by_command"] == {"show inventory": 2}
    assert summary["template_missing_by_model"] == {"Cisco N9K-C9364C": 2}
    assert summary["parse_failed_by_command"] == {"display fan": 1}
