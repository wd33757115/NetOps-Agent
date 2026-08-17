from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "test" / "check_item_configuration_runtime_replay.py"
SPEC = importlib.util.spec_from_file_location(
    "check_item_configuration_runtime_replay_script",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_observed_at_override_requires_timezone() -> None:
    with pytest.raises(ValueError, match="include a timezone"):
        MODULE._parse_observed_at("2026-06-26T14:05:57")

    observed_at = MODULE._parse_observed_at("2026-06-26T14:05:57+08:00")

    assert observed_at is not None
    assert observed_at.utcoffset() == timedelta(hours=8)


def test_load_command_extracts_only_running_configuration(tmp_path: Path) -> None:
    source = tmp_path / "SW1-10.34.2.11.txt"
    source.write_text(
        "\n".join(
            [
                "<SW1>display current-configuration",
                "#",
                "sysname SW1",
                "#",
                "return",
                "<SW1>display version",
                "H3C Comware Software, Version 7.1",
            ]
        ),
        encoding="utf-8",
    )
    observed_at = MODULE._parse_observed_at("2026-06-26T14:05:57+08:00")

    result = MODULE._load_command(source, observed_at=observed_at)

    assert result["raw_text"].startswith("#\nsysname SW1")
    assert "display version" not in result["raw_text"]
    assert result["observed_at"] == observed_at
    assert result["observed_at_source"] == "explicit_regression_period_time"
    assert result["device_id"].startswith("device-")


def test_redaction_guard_rejects_unredacted_secret() -> None:
    MODULE._assert_preview_redacted(["snmp-agent community <redacted>"])

    with pytest.raises(AssertionError, match="unredacted secret"):
        MODULE._assert_preview_redacted(["snmp-agent community public"])
