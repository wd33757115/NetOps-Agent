# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.backup.runner import DeviceBackupRunner, DeviceBackupRunRequest
from src.core.device_ops.tools import ActionType, TaskResult


def test_device_backup_runner_executes_explicit_plan():
    captured: dict[str, object] = {}

    class FakeDBManager:
        pass

    class FakeDeviceFilter:
        def __init__(self, **kwargs):
            captured["filter_params"] = kwargs

    class FakeBackupTool:
        def __init__(self, db_manager):
            captured["db_manager"] = db_manager

        async def backup_by_plan(self, execution_plan):
            captured["plan"] = execution_plan
            return TaskResult(
                success=True,
                action=ActionType.BACKUP,
                total_devices=1,
                success_devices=1,
                failed_devices=0,
                message="backup completed: 1/1",
                details=[{"device_name": "SW1", "output_file": "sw1.cfg"}],
                output_files=["sw1.cfg"],
            )

        async def backup_by_filter(self, filter_params):
            raise AssertionError("explicit execution plan should be preferred")

    runner = DeviceBackupRunner(
        tools_loader=lambda: SimpleNamespace(
            DBManager=FakeDBManager,
            DeviceFilter=FakeDeviceFilter,
            ConfigBackupTool=FakeBackupTool,
        ),
        minio_storage_factory=lambda: None,
    )

    result = runner.run(
        DeviceBackupRunRequest(
            ticket_id="B-1",
            execution_plan={
                "device_id": "device-1",
                "action": "backup",
                "mode": "production",
                "profile_name": "backup-profile",
                "collection_items": [
                    {"command": "show running-config", "action": "backup"}
                ],
                "policy_source": "explicit",
            },
        )
    )

    assert result["status"] == "success"
    assert result["action"] == "backup"
    assert result["ticket_id"] == "B-1"
    assert result["download_url"] is None
    assert result["total_devices"] == 1
    assert captured["plan"].device_id == "device-1"
    assert captured["plan"].command_texts() == ["show running-config"]


def test_device_backup_runner_archives_when_minio_is_available(tmp_path):
    uploaded: dict[str, object] = {}
    output = tmp_path / "sw1.cfg"
    output.write_text("hostname SW1\n", encoding="utf-8")

    class FakeMinio:
        def is_ready(self):
            return True

        def upload_file(self, object_name, file_obj):
            uploaded["object_name"] = object_name
            uploaded["bytes"] = file_obj.read()
            return True

        def get_presigned_url(self, object_name, expires):
            uploaded["expires"] = expires
            return f"https://minio.local/{object_name}"

    class FakeBackupTool:
        def __init__(self, db_manager):
            self.db_manager = db_manager

        async def backup_by_plan(self, execution_plan):
            raise AssertionError("filter path should be used")

        async def backup_by_filter(self, filter_params):
            return TaskResult(
                success=True,
                action=ActionType.BACKUP,
                total_devices=1,
                success_devices=1,
                output_files=[str(output)],
                message="backup completed: 1/1",
            )

    runner = DeviceBackupRunner(
        tools_loader=lambda: SimpleNamespace(
            DBManager=lambda: object(),
            DeviceFilter=lambda **kwargs: kwargs,
            ConfigBackupTool=FakeBackupTool,
        ),
        minio_storage_factory=lambda: FakeMinio(),
    )

    result = runner.run(DeviceBackupRunRequest(ticket_id="B-2"))

    assert result["download_url"] == "https://minio.local/config_backup/B-2/config_backup_B-2.zip"
    assert uploaded["object_name"] == "config_backup/B-2/config_backup_B-2.zip"
    assert uploaded["bytes"]
    assert uploaded["expires"] == 3600 * 24 * 7


def test_device_backup_runner_raises_failed_backup():
    class FakeBackupTool:
        def __init__(self, db_manager):
            self.db_manager = db_manager

        async def backup_by_plan(self, execution_plan):
            raise AssertionError("filter path should be used")

        async def backup_by_filter(self, filter_params):
            return TaskResult(
                success=False,
                action=ActionType.BACKUP,
                total_devices=1,
                failed_devices=1,
                message="backup failed",
            )

    runner = DeviceBackupRunner(
        tools_loader=lambda: SimpleNamespace(
            DBManager=lambda: object(),
            DeviceFilter=lambda **kwargs: kwargs,
            ConfigBackupTool=FakeBackupTool,
        ),
        minio_storage_factory=lambda: None,
    )

    with pytest.raises(RuntimeError, match="backup failed"):
        runner.run(DeviceBackupRunRequest(filter_params={"vendor": "Cisco"}))
