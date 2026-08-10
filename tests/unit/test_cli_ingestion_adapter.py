# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from src.core.collection import CollectionItem, ExecutionPlan
from src.core.ingestion.adapters.cli import (
    CLICollectionPlan,
    CLICommandCollector,
    load_command_collection_manifest,
)


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


def test_cli_command_collector_retries_transient_connection_failure(tmp_path):
    calls: dict[str, object] = {"connects": 0, "commands": []}

    class FakeConnection:
        def send_command(self, command, read_timeout=60):
            calls["commands"].append(command)
            return f"output for {command}"

        def disconnect(self):
            pass

    def flaky_connector(device):
        calls["connects"] = int(calls["connects"]) + 1
        if calls["connects"] == 1:
            raise RuntimeError("Error reading SSH protocol banner")
        return FakeConnection()

    collector = CLICommandCollector(output_root=tmp_path, connector=flaky_connector)
    plan = ExecutionPlan(
        device_id="device-1",
        action="patrol",
        mode="parser_validation",
        profile_name="test-profile",
        profile_version_id=None,
        collector_options={"connection_attempts": 2, "connection_retry_delay": 0},
        commands=(CollectionItem(command="display version", action="patrol"),),
    )

    output_file = collector.collect_command_outputs(
        device={"device_name": "H3C-1", "ip": "10.10.1.1"},
        execution_plan=plan,
    )

    assert calls["connects"] == 2
    assert calls["commands"] == ["display version"]
    assert output_file.exists()


def test_cli_collector_publishes_atomic_incremental_manifest(tmp_path):
    progress = []

    class FakeConnection:
        def send_command(self, command, read_timeout=60):
            return f"output for {command}"

        def disconnect(self):
            pass

    def on_progress(**kwargs):
        manifest = load_command_collection_manifest(kwargs["output_file"])
        progress.append(manifest)

    collector = CLICommandCollector(
        output_root=tmp_path,
        connector=lambda _device: FakeConnection(),
        progress_callback=on_progress,
    )
    output_file = collector.collect_command_outputs(
        device={"device_name": "SW1", "ip": "10.0.0.1"},
        execution_plan=ExecutionPlan(
            device_id="device-1",
            action="patrol",
            mode="parser_validation",
            profile_name="test",
            profile_version_id=None,
            commands=(
                CollectionItem(command="show version", action="patrol"),
                CollectionItem(command="show clock", action="patrol"),
            ),
        ),
    )

    assert [len(item["commands"]) for item in progress] == [1, 2]
    assert all(item["status"] == "running" for item in progress)
    final = load_command_collection_manifest(output_file)
    assert final["status"] == "complete"
    assert final["completed_at"]


def test_cli_collector_continues_after_progress_persistence_failure(tmp_path):
    commands = []
    callback_calls = 0

    class FakeConnection:
        def send_command(self, command, read_timeout=60):
            commands.append(command)
            return f"output for {command}"

        def disconnect(self):
            pass

    def fail_first_progress(**_kwargs):
        nonlocal callback_calls
        callback_calls += 1
        if callback_calls == 1:
            raise ConnectionError("postgres unavailable")

    collector = CLICommandCollector(
        output_root=tmp_path,
        connector=lambda _device: FakeConnection(),
        progress_callback=fail_first_progress,
    )
    output_file = collector.collect_command_outputs(
        device={"device_name": "SW1", "ip": "10.0.0.1"},
        execution_plan=ExecutionPlan(
            device_id="device-1",
            action="patrol",
            mode="parser_validation",
            profile_name="test",
            profile_version_id=None,
            commands=(
                CollectionItem(command="show version", action="patrol"),
                CollectionItem(command="show clock", action="patrol"),
            ),
        ),
    )

    manifest = load_command_collection_manifest(output_file)
    assert commands == ["show version", "show clock"]
    assert callback_calls == 2
    assert manifest["status"] == "complete"
    assert manifest["commands"][0]["persistence_status"] == "failed"
    assert manifest["commands"][1]["persistence_status"] == "persisted"


def test_cli_manifest_preserves_duplicate_command_item_identity(tmp_path):
    class FakeConnection:
        def send_command(self, command, read_timeout=60):
            return f"output for {command}"

        def disconnect(self):
            pass

    collector = CLICommandCollector(
        output_root=tmp_path,
        connector=lambda _device: FakeConnection(),
    )
    output_file = collector.collect_command_outputs(
        device={"device_name": "SW1", "ip": "10.0.0.1"},
        execution_plan=ExecutionPlan(
            device_id="device-1",
            action="patrol",
            mode="parser_validation",
            profile_name="repeat-sampling",
            profile_version_id=None,
            commands=(
                CollectionItem(
                    command="show interface transceiver detail",
                    action="patrol",
                    collection_item_id="optical-before",
                ),
                CollectionItem(
                    command="show interface transceiver detail",
                    action="patrol",
                    collection_item_id="optical-after",
                ),
            ),
        ),
    )

    commands = load_command_collection_manifest(output_file)["commands"]
    assert [item["execution_item_id"] for item in commands] == [
        "optical-before",
        "optical-after",
    ]
    assert [item["command_ordinal"] for item in commands] == [0, 1]


def test_check_item_collection_skips_fallback_after_usable_primary(tmp_path):
    calls = []
    binding = {
        "check_item_version_id": "check-cpu-v1",
        "implementation_version_id": "impl-cpu-v1",
        "runtime_bundle_version_id": "bundle-cpu-v1",
    }

    class FakeConnection:
        def send_command(self, command, read_timeout=60):
            calls.append(command)
            return "CPU usage: 20%"

        def disconnect(self):
            pass

    output_file = CLICommandCollector(
        output_root=tmp_path,
        connector=lambda _device: FakeConnection(),
    ).collect_command_outputs(
        device={"device_name": "SW1", "ip": "10.0.0.1"},
        execution_plan=ExecutionPlan(
            device_id="device-1",
            action="patrol",
            mode="production",
            profile_name="check-items",
            profile_version_id="profile-v1",
            commands=(
                CollectionItem(
                    command="display cpu-usage",
                    action="patrol",
                    collection_item_id="action-primary",
                    fallback_rank=0,
                    check_item_bindings=(binding,),
                ),
                CollectionItem(
                    command="display cpu",
                    action="patrol",
                    collection_item_id="action-fallback",
                    fallback_rank=1,
                    check_item_bindings=({**binding, "fallback_rank": 1},),
                ),
            ),
        ),
    )

    manifest = load_command_collection_manifest(output_file)
    assert calls == ["display cpu-usage"]
    assert [item["status"] for item in manifest["commands"]] == [
        "complete",
        "ignored_legacy_fallback",
    ]
    assert manifest["status"] == "complete"
    assert manifest["command_selection"] == {
        "policy": "primary_only",
        "unsupported_action": "log_only",
    }
    assert manifest["check_item_fallback"]["enabled"] is False
    assert manifest["check_item_fallback"]["satisfied_binding_count"] == 1


def test_check_item_collection_logs_primary_failure_without_using_fallback(tmp_path):
    calls = []
    binding = {
        "check_item_version_id": "check-cpu-v1",
        "implementation_version_id": "impl-cpu-v1",
        "runtime_bundle_version_id": "bundle-cpu-v1",
    }

    class FakeConnection:
        def send_command(self, command, read_timeout=60):
            calls.append(command)
            if command == "display cpu-usage":
                return "% Unrecognized command found at '^' position."
            return "CPU usage: 35%"

        def disconnect(self):
            pass

    output_file = CLICommandCollector(
        output_root=tmp_path,
        connector=lambda _device: FakeConnection(),
    ).collect_command_outputs(
        device={"device_name": "SW1", "ip": "10.0.0.1"},
        execution_plan=ExecutionPlan(
            device_id="device-1",
            action="patrol",
            mode="production",
            profile_name="check-items",
            profile_version_id="profile-v1",
            commands=(
                CollectionItem(
                    command="display cpu-usage",
                    action="patrol",
                    fallback_rank=0,
                    check_item_bindings=(binding,),
                ),
                CollectionItem(
                    command="display cpu",
                    action="patrol",
                    fallback_rank=1,
                    check_item_bindings=({**binding, "fallback_rank": 1},),
                ),
            ),
        ),
    )

    manifest = load_command_collection_manifest(output_file)
    assert calls == ["display cpu-usage"]
    assert [item["status"] for item in manifest["commands"]] == [
        "command_unsupported",
        "ignored_legacy_fallback",
    ]
    assert not manifest["commands"][1]["selected_for_bindings"]
    assert manifest["status"] == "failed"
