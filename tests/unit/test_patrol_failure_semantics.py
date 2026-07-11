# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from src.core.celery_tasks import tasks as celery_tasks
from src.core.collection import CollectionItem, ExecutionPlan
from src.core.device_ops.tools import DeviceFilter, PatrolTool
from src.core.ingestion.adapters.cli import (
    CLICommandCollector,
    command_collection_manifest_path,
    load_command_collection_manifest,
)
from src.core.patrol.change_event_repository import ChangeEventRepository
from src.core.patrol.concurrency import DistributedPatrolSemaphore
from src.core.patrol.event_builder import (
    build_events_from_changes,
    publish_event_outbox,
)
from src.core.patrol.event_rules import EventRule
from src.core.state.repository import StateRepository
from src.infrastructure.db.models import (
    CommandSnapshot,
    PatrolCollectionResult,
    PatrolRun,
    StateObservationRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _plan(*commands: str) -> ExecutionPlan:
    return ExecutionPlan(
        device_id="device-1",
        action="patrol",
        mode="parser_validation",
        profile_name="failure-semantics",
        profile_version_id=None,
        commands=tuple(CollectionItem(command=command, action="patrol") for command in commands),
    )


def test_command_failure_keeps_completed_output_and_writes_manifest(tmp_path):
    calls: list[str] = []

    class FakeConnection:
        def send_command(self, command, read_timeout=60):
            calls.append(command)
            if command == "show inventory":
                raise TimeoutError("read timeout")
            return f"output for {command}"

        def disconnect(self):
            raise RuntimeError("disconnect failed")

    collector = CLICommandCollector(
        output_root=tmp_path,
        connector=lambda _device: FakeConnection(),
    )
    output_file = collector.collect_command_outputs(
        device={"device_name": "SW1", "ip": "10.0.0.1"},
        execution_plan=_plan("show version", "show inventory", "show clock"),
    )

    assert calls == ["show version", "show inventory"]
    assert "output for show version" in output_file.read_text(encoding="utf-8")
    manifest = load_command_collection_manifest(output_file)
    assert manifest["status"] == "partial"
    assert [item["status"] for item in manifest["commands"]] == [
        "complete",
        "timeout",
        "not_executed",
    ]


def test_patrol_device_surfaces_command_level_partial_result(tmp_path):
    output_file = tmp_path / "sw1.txt"
    output_file.write_text("Command: show version\nOutput:\nok\n", encoding="utf-8")
    command_collection_manifest_path(output_file).write_text(
        """{
  "status": "partial",
  "commands": [
    {"command": "show version", "status": "complete"},
    {"command": "show inventory", "status": "timeout"}
  ]
}""",
        encoding="utf-8",
    )

    class FakeCollector:
        def collect_command_outputs(self, **_kwargs):
            return output_file

    result = asyncio.run(
        PatrolTool(object(), cli_collector=FakeCollector()).patrol_device(
            {"device_id": "device-1", "device_name": "SW1", "ip": "10.0.0.1"},
            mode="parser_validation",
            execution_plan=_plan("show version", "show inventory"),
        )
    )

    assert result["success"] is False
    assert result["collection_status"] == "partial"
    assert result["completed_commands"] == 1
    assert len(result["command_collection_results"]) == 2


def test_event_builder_loads_rules_once_per_change_batch(monkeypatch):
    calls = {"count": 0}

    def load_rules():
        calls["count"] += 1
        return [
            EventRule(
                rule_id="all",
                version=1,
                event_type="ObservedChange",
                severity="info",
                predicate=lambda _change: True,
            )
        ]

    monkeypatch.setattr("src.core.patrol.event_builder.load_event_rules", load_rules)
    changes = [
        {
            "change_id": f"change-{index}",
            "device_id": "SW1",
            "entity_type": "interface",
            "entity_key": f"Gi1/0/{index}",
            "field": "status",
            "old": "up",
            "new": "down",
        }
        for index in range(100)
    ]

    assert len(build_events_from_changes(changes)) == 100
    assert calls["count"] == 1


def test_outbox_publisher_reuses_canonical_event_identity(monkeypatch):
    captured = {}

    class FakeRepository:
        def claim_event_outbox(self, *, limit):
            assert limit == 10
            return "claim-1", [
                {
                    "id": "outbox-1",
                    "event_id": "event-1",
                    "event_type": "network.InterfaceDown",
                    "payload_json": {
                        "event_id": "event-1",
                        "run_id": "run-1",
                        "occurred_at": "2026-07-10T01:02:03+00:00",
                    },
                }
            ]

        def renew_event_outbox_claim(self, outbox_id, *, claim_token):
            assert (outbox_id, claim_token) == ("outbox-1", "claim-1")
            return True

        def mark_event_outbox_published(self, outbox_id, message_id, *, claim_token):
            captured["published"] = (outbox_id, message_id, claim_token)

        def mark_event_outbox_failed(self, *_args, **_kwargs):
            raise AssertionError("successful publish must not be marked failed")

    def publish(_stream, event):
        captured["event"] = event
        return "redis-1-0"

    monkeypatch.setattr("src.core.patrol.event_builder.ChangeEventRepository", FakeRepository)
    monkeypatch.setattr("src.core.events.bus.EventBus.publish", publish)

    assert publish_event_outbox(limit=10) == ["redis-1-0"]
    event = captured["event"]
    assert event.event_id == "event-1"
    assert event.occurred_at == datetime(2026, 7, 10, 1, 2, 3, tzinfo=timezone.utc)
    assert captured["published"] == ("outbox-1", "redis-1-0", "claim-1")


def test_distributed_patrol_lease_can_be_renewed(monkeypatch):
    calls = []

    class FakeRedis:
        def eval(self, script, number_of_keys, *args):
            calls.append((script, number_of_keys, args))
            return 1

        def zrem(self, key, token):
            calls.append(("zrem", key, token))

    redis = FakeRedis()
    monkeypatch.setattr("src.core.patrol.concurrency.get_redis", lambda: redis)
    semaphore = DistributedPatrolSemaphore(
        limit=2,
        lease_seconds=30,
        wait_seconds=1,
    )

    lease = semaphore.acquire()
    assert lease.mode == "distributed"
    assert semaphore.renew(lease) is True
    semaphore.release(lease)
    assert calls[-1] == ("zrem", lease.key, lease.token)


def test_patrol_batch_is_bounded_and_isolates_device_lease_failure():
    devices = [{"device_id": f"device-{index}", "device_name": f"SW{index}"} for index in range(4)]

    class FakeManager:
        def get_devices_by_filter(self, _filter):
            return devices

    class FakeSemaphore:
        def __init__(self):
            self.acquire_count = 0

        def acquire(self):
            self.acquire_count += 1
            if self.acquire_count == 1:
                raise TimeoutError("lease unavailable")
            return SimpleNamespace(effective_mode="distributed")

        @contextmanager
        def maintain(self, lease):
            yield lease

    tool = PatrolTool(FakeManager())
    tool.max_concurrency = 2
    tool.distributed_semaphore = FakeSemaphore()
    active = 0
    peak_active = 0

    async def patrol_device(device, *_args, **_kwargs):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {
            "inventory_device_id": device["device_id"],
            "success": True,
            "collection_status": "complete",
        }

    tool.patrol_device = patrol_device
    result = asyncio.run(tool.patrol_by_filter(DeviceFilter(), mode="production"))

    assert result.total_devices == 4
    assert result.success_devices == 3
    assert result.failed_devices == 1
    assert peak_active <= 2
    assert result.details[0]["failure_code"] == "concurrency_lease_timeout"


def test_patrol_worker_redelivery_allocates_new_attempt(monkeypatch):
    calls = []

    class FakeRepository:
        def prepare_redelivery_attempt(self, logical_run_id):
            calls.append(logical_run_id)
            return 4

    monkeypatch.setattr(celery_tasks, "PatrolRepository", FakeRepository)
    request = SimpleNamespace(
        id="task-1",
        retries=0,
        delivery_info={"redelivered": True},
    )

    assert celery_tasks._patrol_attempt_number(request) == 4
    assert calls == ["patrol-task-1"]
    assert celery_tasks.execute_device_patrol_task.acks_late is True
    assert celery_tasks.execute_device_patrol_task.reject_on_worker_lost is True


def test_change_decisions_are_persisted_with_one_bulk_statement(monkeypatch):
    statements = []

    class FakeDb:
        def execute(self, statement):
            statements.append(statement)

    @contextmanager
    def fake_session():
        yield FakeDb()

    monkeypatch.setattr(
        "src.core.patrol.change_event_repository.get_db_session",
        fake_session,
    )
    decisions = [
        {
            "run_id": "run-1",
            "current_observation_id": f"observation-{index}",
            "decision": "suppressed",
            "reason": "deadband",
            "entity_type": "interface_l2",
            "entity_key": f"Gi1/0/{index}",
            "field": "status",
        }
        for index in range(100)
    ]

    assert ChangeEventRepository().save_changes([], decisions=decisions) == 0
    assert len(statements) == 1
    assert "netops_network_change_decisions" in str(statements[0])


def test_observation_baseline_requires_collection_level_eligibility():
    suffix = str(uuid.uuid4())
    run_id = f"baseline-{suffix}"
    snapshot_id = str(uuid.uuid4())
    observation_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with get_db_session() as db:
        db.add(
            PatrolRun(
                id=run_id,
                logical_run_id=run_id,
                mode="production",
                source="test",
                status="completed",
                collection_status="completed",
                analysis_status="completed",
                baseline_eligible=True,
                run_purpose="scheduled_patrol",
                started_at=now,
                completed_at=now,
            )
        )
        db.flush()
        db.add(
            CommandSnapshot(
                id=snapshot_id,
                run_id=run_id,
                execution_item_id=f"item-{snapshot_id}",
                device_id="SW1",
                device_name="SW1",
                command="show interface status",
                canonical_command="show interface status",
                observed_at=now,
                raw_text="ok",
                raw_text_hash="0" * 64,
                parse_status="parsed",
                snapshot_kind="full",
            )
        )
        db.flush()
        db.add(
            PatrolCollectionResult(
                id=str(uuid.uuid4()),
                run_id=run_id,
                execution_item_id=f"item-{snapshot_id}",
                device_id="SW1",
                canonical_command="show interface status",
                status="parsed",
                authoritative_for_deletion=True,
                baseline_eligible=False,
                observed_at=now,
            )
        )
        db.add(
            StateObservationRecord(
                id=observation_id,
                idempotency_key=str(uuid.uuid4()),
                run_id=run_id,
                snapshot_id=snapshot_id,
                mode="production",
                source="test",
                source_type="cli",
                device_id="SW1",
                canonical_command="show interface status",
                entity_type="interface_l2",
                entity_key='["Gi1/0/1"]',
                field_name="status",
                observation_type="interface.oper_status",
                value_json="up",
                value_text="up",
                value_type="string",
                observation_kind="state",
                observed_at=now,
                confidence=1.0,
            )
        )

    try:
        repository = StateRepository()
        assert repository.list_observations_for_runs([run_id], baseline_only=True) == []
        with get_db_session() as db:
            row = (
                db.query(PatrolCollectionResult)
                .filter(PatrolCollectionResult.run_id == run_id)
                .one()
            )
            row.baseline_eligible = True
        rows = repository.list_observations_for_runs([run_id], baseline_only=True)
        assert [row["id"] for row in rows] == [observation_id]
    finally:
        with get_db_session() as db:
            db.query(StateObservationRecord).filter(StateObservationRecord.run_id == run_id).delete(
                synchronize_session=False
            )
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(synchronize_session=False)
