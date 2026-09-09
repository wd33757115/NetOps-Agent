from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.patrol.raw_importer import _preview_parse_status


def test_abbreviated_commands_keep_their_check_item_binding_and_occurrence_order():
    from src.core.patrol.command_splitter import split_cli_capture
    from src.core.patrol.raw_importer import _align_collection_statuses

    capture = "命令: dis transceiver diagnosis interface\n输出:\noptic\n命令: display cpu\n输出:\ncpu\n命令: dis transceiver diagnosis interface\n输出:\noptic2"
    blocks = split_cli_capture(capture)
    statuses = [
        {
            "command": "dis transceiver diagnosis interface",
            "check_item_bindings": [{"check_item_id": "interface.optical_power"}],
            "ordinal": 1,
        },
        {"command": "display cpu", "ordinal": 2},
        {"command": "display transceiver diagnosis interface", "ordinal": 3},
    ]
    matched = _align_collection_statuses(blocks, statuses)
    assert [row["ordinal"] for row in matched] == [1, 2, 3]
    assert matched[0]["check_item_bindings"][0]["check_item_id"] == "interface.optical_power"


@pytest.mark.parametrize(
    ("policy_status", "identity_status", "parser_status", "failure_code", "expected"),
    [
        ("eligible", "known", "success", None, "ready"),
        ("eligible", "known", "empty", None, "ready"),
        ("eligible", "known", "unavailable", "template_missing", "template_missing"),
        ("eligible", "known", "failed", "textfsm_error", "parse_failed"),
        ("eligible", "known", "unavailable", "unsupported_command", "ignored"),
        ("raw", "known", "not_required", None, "raw_compare"),
        ("ignored", "known", "unavailable", None, "ignored"),
        ("eligible", "unknown", "unavailable", None, "identity_unknown"),
    ],
)
def test_preview_parse_status_matches_snapshot_quality_buckets(
    policy_status,
    identity_status,
    parser_status,
    failure_code,
    expected,
):
    parsed = SimpleNamespace(
        policy_status=policy_status,
        identity_status=identity_status,
        parser_status=parser_status,
        failure_code=failure_code,
    )

    assert _preview_parse_status(parsed) == expected


def test_command_set_analysis_binding_preserves_collection_scope_and_failures(monkeypatch):
    import src.core.patrol.raw_importer as importer
    rows = [{"command": "show version", "status": "success", "execution_item_id": "a"},
            {"command": "show inventory", "status": "failed", "error": "timeout", "execution_item_id": "b"}]
    binding = {"check_item_id": "device.version", "implementation_version_id": "adapter-1"}
    def routes(**kwargs):
        assert kwargs["commands"] == ["show version", "show inventory"]
        return [{"command": "show version", "check_item_bindings": [binding], "selected_for_bindings": [binding]}]
    monkeypatch.setattr(importer, "_resolve_offline_direct_projection_bindings", routes)
    result = importer._attach_command_analysis_routes(rows, identity=object(), device_id="d1", commands=[row["command"] for row in rows])
    assert [row["command"] for row in result] == [row["command"] for row in rows]
    assert result[0]["selected_for_bindings"] == [binding]
    assert result[1] == rows[1]
    assert "check_item_bindings" not in rows[0]
    # Frozen routes are authoritative; never resolve replacement assets.
    assert importer._attach_command_analysis_routes(result, identity=None, device_id="d1", commands=[]) == result
