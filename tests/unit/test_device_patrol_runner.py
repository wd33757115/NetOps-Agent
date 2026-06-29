# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.device_ops.tools import ActionType, TaskResult
from src.core.patrol import runner as patrol_runner
from src.core.patrol.runner import DevicePatrolRunner, DevicePatrolRunRequest


def test_device_patrol_runner_executes_explicit_plan_and_persists_snapshots():
    captured: dict[str, object] = {}

    class FakeDBManager:
        pass

    class FakeDeviceFilter:
        def __init__(self, **kwargs):
            captured["filter_params"] = kwargs

    class FakePatrolTool:
        def __init__(self, db_manager):
            captured["db_manager"] = db_manager

        async def patrol_by_plan(self, execution_plan, save_baseline):
            captured["plan"] = execution_plan
            captured["save_baseline"] = save_baseline
            return TaskResult(
                success=True,
                action=ActionType.PATROL,
                total_devices=1,
                success_devices=1,
                failed_devices=0,
                message="patrol completed: 1/1",
                details=[
                    {
                        "device_name": "SW1",
                        "ip": "10.0.0.1",
                        "vendor": "Cisco",
                        "model": "C2960",
                        "output_file": "sw1.txt",
                    }
                ],
                output_files=["sw1.txt"],
            )

        async def patrol_by_filter(self, filter_params, save_baseline, mode):
            raise AssertionError("explicit execution plan should be preferred")

    def fake_snapshot_persister(**kwargs):
        captured["snapshot_kwargs"] = kwargs
        return {
            "snapshot_run_id": kwargs["snapshot_run_id"],
            "snapshot_storage": "postgresql",
            "legacy_db_path_ignored": None,
            "snapshot_devices": 1,
            "snapshot_commands": 3,
            "snapshot_errors": [],
        }

    runner = DevicePatrolRunner(
        tools_loader=lambda: SimpleNamespace(
            DBManager=FakeDBManager,
            DeviceFilter=FakeDeviceFilter,
            PatrolTool=FakePatrolTool,
        ),
        minio_storage_factory=lambda: None,
        snapshot_persister=fake_snapshot_persister,
    )

    result = runner.run(
        DevicePatrolRunRequest(
            ticket_id="T-1",
            save_baseline=True,
            task_id="celery-1",
            execution_plan_id="plan-1",
            execution_plan={
                "device_id": "device-1",
                "action": "patrol",
                "mode": "parser_validation",
                "profile_name": "explicit",
                "collection_items": [{"command": "show version", "action": "patrol"}],
                "policy_source": "explicit",
            },
        )
    )

    assert result["status"] == "success"
    assert result["mode"] == "parser_validation"
    assert result["snapshot_run_id"] == "patrol-celery-1"
    assert result["snapshot_commands"] == 3
    assert result["execution_plan_id"] == "plan-1"
    assert result["raw_outputs_archive"] is None
    assert captured["save_baseline"] is True
    assert captured["plan"].device_id == "device-1"
    assert captured["plan"].command_texts() == ["show version"]
    assert captured["snapshot_kwargs"]["ticket_id"] == "T-1"
    assert captured["snapshot_kwargs"]["mode"] == "parser_validation"
    assert captured["snapshot_kwargs"]["execution_plan_id"] == "plan-1"


def test_device_patrol_runner_fails_when_snapshot_persist_fails():
    class FakeDBManager:
        pass

    class FakeDeviceFilter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakePatrolTool:
        def __init__(self, db_manager):
            self.db_manager = db_manager

        async def patrol_by_filter(self, filter_params, save_baseline, mode):
            return TaskResult(
                success=True,
                action=ActionType.PATROL,
                total_devices=1,
                success_devices=1,
                details=[{"output_file": "missing.txt"}],
                output_files=["missing.txt"],
            )

    def failing_snapshot_persister(**kwargs):
        return {
            "snapshot_run_id": kwargs["snapshot_run_id"],
            "snapshot_storage": "postgresql",
            "legacy_db_path_ignored": None,
            "snapshot_devices": 0,
            "snapshot_commands": 0,
            "snapshot_errors": ["missing.txt: parse failed"],
        }

    runner = DevicePatrolRunner(
        tools_loader=lambda: SimpleNamespace(
            DBManager=FakeDBManager,
            DeviceFilter=FakeDeviceFilter,
            PatrolTool=FakePatrolTool,
        ),
        minio_storage_factory=lambda: None,
        snapshot_persister=failing_snapshot_persister,
    )

    with pytest.raises(RuntimeError, match="missing.txt: parse failed"):
        runner.run(DevicePatrolRunRequest(filter_params={"vendor": "Cisco"}))


def test_device_patrol_runner_surfaces_partial_parser_health():
    class FakeDBManager:
        pass

    class FakeDeviceFilter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakePatrolTool:
        def __init__(self, db_manager):
            self.db_manager = db_manager

        async def patrol_by_filter(self, filter_params, save_baseline, mode):
            return TaskResult(
                success=True,
                action=ActionType.PATROL,
                total_devices=1,
                success_devices=1,
                failed_devices=0,
                details=[{"output_file": "sw1.txt"}],
                output_files=["sw1.txt"],
            )

    def partial_snapshot_persister(**kwargs):
        return {
            "snapshot_run_id": kwargs["snapshot_run_id"],
            "snapshot_storage": "postgresql",
            "legacy_db_path_ignored": None,
            "snapshot_devices": 1,
            "snapshot_commands": 2,
            "snapshot_errors": [],
            "snapshot_health": "partial",
            "parsed_commands": 1,
            "failed_parse_commands": 1,
            "coverage_violations": [],
            "parse_violations": [{"canonical_command": "show version"}],
            "production_parser_ready": False,
        }

    runner = DevicePatrolRunner(
        tools_loader=lambda: SimpleNamespace(
            DBManager=FakeDBManager,
            DeviceFilter=FakeDeviceFilter,
            PatrolTool=FakePatrolTool,
        ),
        minio_storage_factory=lambda: None,
        snapshot_persister=partial_snapshot_persister,
    )

    result = runner.run(DevicePatrolRunRequest(filter_params={"vendor": "Cisco"}))

    assert result["status"] == "partial"
    assert result["data_quality_status"] == "partial"
    assert result["production_parser_ready"] is False
    assert result["failed_parse_commands"] == 1
    assert result["parse_violations"] == [{"canonical_command": "show version"}]


def test_device_patrol_runner_can_replay_execution_plan_by_id(monkeypatch):
    captured: dict[str, object] = {}

    class FakePlanRepository:
        def get_plan(self, plan_id):
            assert plan_id == "plan-1"
            return {
                "execution_plan_id": plan_id,
                "plan_json": {
                    "device_id": "device-1",
                    "action": "patrol",
                    "mode": "production",
                    "profile_name": "site-patrol",
                    "policy_source": "profile_binding",
                    "collection_items": [{"command": "show version", "action": "patrol"}],
                },
            }

    class FakeDBManager:
        pass

    class FakeDeviceFilter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakePatrolTool:
        def __init__(self, db_manager):
            self.db_manager = db_manager

        async def patrol_by_plan(self, execution_plan, save_baseline):
            captured["plan"] = execution_plan
            return TaskResult(
                success=True,
                action=ActionType.PATROL,
                total_devices=1,
                success_devices=1,
                failed_devices=0,
                details=[],
                output_files=[],
            )

    monkeypatch.setattr(patrol_runner, "ExecutionPlanRepository", FakePlanRepository)

    runner = DevicePatrolRunner(
        tools_loader=lambda: SimpleNamespace(
            DBManager=FakeDBManager,
            DeviceFilter=FakeDeviceFilter,
            PatrolTool=FakePatrolTool,
        ),
        minio_storage_factory=lambda: None,
        snapshot_persister=lambda **kwargs: {
            "snapshot_run_id": kwargs["snapshot_run_id"],
            "snapshot_storage": "postgresql",
            "legacy_db_path_ignored": None,
            "snapshot_devices": 0,
            "snapshot_commands": 0,
            "snapshot_errors": [],
        },
    )

    result = runner.run(DevicePatrolRunRequest(execution_plan_id="plan-1", task_id="celery-1"))

    assert result["status"] == "success"
    assert result["execution_plan_id"] == "plan-1"
    assert captured["plan"].command_texts() == ["show version"]
