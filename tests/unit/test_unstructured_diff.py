from __future__ import annotations

from src.core.parser.policy import ParserPolicy
from src.core.patrol.change_detector import detect_changes_between_snapshots
from src.core.patrol.domain import SnapshotRecord
from src.core.patrol.unstructured_diff import (
    build_unstructured_diff,
    resolve_unstructured_diff_profile,
    summarize_unstructured_changes,
)


class _PolicyRepository:
    @staticmethod
    def list_command_aliases():
        return []


def _snapshot(
    snapshot_id: str,
    raw_text: str,
    *,
    command: str = "show running-config",
) -> SnapshotRecord:
    return SnapshotRecord(
        snapshot_id=snapshot_id,
        run_id=snapshot_id,
        device_id="SW1",
        device_name="SW1",
        ip="192.0.2.1",
        vendor="Cisco",
        model="C9300",
        command=command,
        command_canonical=command,
        parser_name=None,
        parser_version=None,
        observed_at="2026-07-19T00:00:00+00:00",
        structured_json=[{"raw_text_hash": snapshot_id}],
        raw_text=raw_text,
        raw_text_hash=snapshot_id,
        parse_status="raw_hashed",
        metadata={"handling": "raw_hash_diff", "comparison_profile": "configuration"},
    )


def test_configuration_diff_suppresses_transport_only_noise():
    result = build_unstructured_diff(
        "hostname SW1\r\ninterface Gi1/0/1   \r\n",
        "hostname SW1\ninterface Gi1/0/1\n\n",
        command="show running-config",
    )

    assert result["raw_changed"] is True
    assert result["changed"] is False
    assert result["noise_only"] is True


def test_configuration_diff_redacts_credentials_and_classifies_changes():
    result = build_unstructured_diff(
        "interface Gi1/0/1\n description old\nsnmp-server community public ro",
        "interface Gi1/0/1\n shutdown\nsnmp-server community private ro",
        command="show running-config",
    )

    assert result["changed"] is True
    assert result["added_line_count"] == 2
    assert result["removed_line_count"] == 2
    assert {item["label"] for item in result["categories"]} >= {
        "接口配置",
        "安全与访问控制",
    }
    preview = "\n".join(item["text"] for item in result["preview"])
    assert "public" not in preview
    assert "private" not in preview
    assert "<redacted>" in preview


def test_log_window_normalizes_timestamps_but_keeps_new_messages():
    unchanged = build_unstructured_diff(
        "%Oct 23 10:14:23:906 2025 SW1 IFNET/3/PHY_UPDOWN: interface GE1/0/1 down",
        "%Oct 24 11:15:24:100 2025 SW1 IFNET/3/PHY_UPDOWN: interface GE1/0/1 down",
        command="display logbuffer level 3",
    )
    changed = build_unstructured_diff(
        "%Oct 23 10:14:23:906 2025 SW1 IFNET/3/PHY_UPDOWN: interface GE1/0/1 down",
        "%Oct 24 11:15:24:100 2025 SW1 IFNET/3/PHY_UPDOWN: interface GE1/0/2 down",
        command="display logbuffer level 3",
    )

    assert unchanged["changed"] is False
    assert unchanged["noise_only"] is True
    assert changed["changed"] is True
    assert changed["comparison_mode"] == "rolling_window"
    assert changed["categories"][0]["label"] == "接口与链路日志"


def test_log_preview_prioritizes_current_messages_and_redacts_identifiers():
    result = build_unstructured_diff(
        "Overwritten messages: 12\n"
        "%Oct 23 10:14:23:906 2025 SW1 IFNET/3/PHY_UPDOWN: interface GE1/0/1 down",
        "Overwritten messages: 99\n"
        "%Oct 24 11:15:24:100 2025 SW1 SHELL/4/SHELL_CMD_MATCHFAIL: "
        "-User=admin-IPAddr=10.7.102.152; Command display secret failed",
        command="display logbuffer level 4",
    )

    preview = "\n".join(item["text"] for item in result["preview"])
    assert result["changed"] is True
    assert result["ignored_line_count"] == 2
    assert result["preview"][0]["operation"] == "added"
    assert result["preview"][0]["category_label"] == "巡检命令与采集日志"
    assert "admin" not in preview
    assert "10.7.102.152" not in preview
    assert "User=<redacted>" in preview
    assert "IPAddr=<ip>" in preview
    assert "Overwritten messages" not in preview


def test_policy_pattern_routes_logbuffer_to_non_structured_comparison():
    decision = ParserPolicy(repository=_PolicyRepository()).decide(
        vendor="H3C",
        model="S5130S",
        family="Comware7",
        command="display logbuffer level 3",
    )
    profile = resolve_unstructured_diff_profile("display logbuffer level 3")

    assert decision.enabled is False
    assert decision.handling == "raw_hash_diff"
    assert decision.comparison_profile == "log_window"
    assert decision.comparison_profile_version == 1
    assert profile and profile["mode"] == "rolling_window"


def test_snapshot_change_keeps_hashes_and_adds_safe_diff_evidence():
    changes = detect_changes_between_snapshots(
        _snapshot("a" * 64, "interface Gi1/0/1\n description old"),
        _snapshot("b" * 64, "interface Gi1/0/1\n shutdown"),
    )

    assert len(changes) == 1
    assert changes[0].old_value == "a" * 64
    assert changes[0].new_value == "b" * 64
    diff = changes[0].dimensions["unstructured_diff"]
    assert diff["changed"] is True
    assert changes[0].comparison_type == "unstructured_ordered_lines"


def test_summary_aggregates_only_effective_unstructured_changes():
    change = detect_changes_between_snapshots(
        _snapshot("a" * 64, "hostname SW1"),
        _snapshot("b" * 64, "hostname SW2"),
    )[0].to_dict()
    change["id"] = change.pop("change_id")
    change["device_name"] = "SW1"
    summary = summarize_unstructured_changes(
        [change],
        collected_output_count=2,
        compared_output_count=2,
    )

    assert summary["changed_output_count"] == 1
    assert summary["unchanged_output_count"] == 1
    assert summary["items"][0]["command"] == "show running-config"
