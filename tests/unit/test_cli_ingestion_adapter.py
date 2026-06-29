# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from src.core.collection import CollectionItem, ExecutionPlan
from src.core.ingestion.adapters.cli import CLICollectionPlan, CLICommandCollector


def test_cli_command_collector_writes_command_report(tmp_path):
    calls: dict[str, object] = {"commands": [], "disconnected": False, "device": None}

    class FakeConnection:
        def send_command(self, command, read_timeout=60):
            calls["commands"].append((command, read_timeout))
            return f"output for {command}"

        def disconnect(self):
            calls["disconnected"] = True

    collector = CLICommandCollector(
        output_root=tmp_path,
        connector=lambda device: calls.update({"device": device}) or FakeConnection(),
    )
    plan = ExecutionPlan(
        device_id="device-1",
        action="patrol",
        mode="parser_validation",
        profile_name="test-profile",
        profile_version_id="profile-version-1",
        credential_ref="env:PLAN_PASSWORD",
        collector_options={"read_timeout": 12, "netmiko_device_type": "hp_comware"},
        commands=(
            CollectionItem(command="show version", action="patrol"),
            CollectionItem(command="show inventory", action="patrol"),
        ),
    )

    result = collector.collect(
        CLICollectionPlan(
            device={"device_name": "SW1", "ip": "10.0.0.1"},
            execution_plan=plan,
        )
    )

    effective_device = calls["device"]
    assert isinstance(effective_device, dict)
    assert effective_device["credential_ref"] == "env:PLAN_PASSWORD"
    assert effective_device["attributes"]["netmiko_device_type"] == "hp_comware"
    assert result.source_type == "cli"
    assert result.evidence_type == "cli_command_output"
    assert result.command_count == 2
    assert result.output_file is not None
    assert result.output_file.exists()
    assert calls["commands"] == [
        ("show version", 12),
        ("show inventory", 12),
    ]
    assert calls["disconnected"] is True
    text = result.output_file.read_text(encoding="utf-8")
    assert "Command: show version" in text
    assert "Output:\noutput for show inventory" in text
