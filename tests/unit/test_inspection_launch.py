from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from src.apps.inspection.backend import launch, online_execution
from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
from src.gateway import devices_api
from src.infrastructure.db import models as m
from test_inspection_online_reads import database, seed  # noqa: F401


@pytest.fixture
def admission(database, monkeypatch):
    monkeypatch.setattr(launch, "get_db_session", database)
    monkeypatch.setattr(online_execution, "get_db_session", database)
    submitted = []

    def start(payload, *, actor, batch_id, intent):
        submitted.append((payload, actor, intent))
        PatrolBatchManifestRepository().create_pending_batch(
            patrol_batch_id=batch_id,
            workflow_name="patrol-change-event",
            launch_mode="direct",
            patrol_mode="production",
            report_purpose="production_current",
            save_baseline=False,
            ticket_id=None,
            target_count=len(payload.device_ids),
            planned_rows=[{"device_id": value} for value in payload.device_ids],
            skipped_errors=[],
            target_snapshot={"selector": {"inspection": {**intent, "user_id": actor}}},
        )
        with database() as db:
            db.get(m.PatrolBatchRecord, batch_id).runtime_manifest_id = "manifest"
        return {
            "success": True,
            "patrol_batch_id": batch_id,
            "queued_count": len(payload.device_ids),
        }

    monkeypatch.setattr(devices_api, "_launch_patrol", start)
    return submitted


def test_launch_idempotency_keeps_one_batch_and_rejects_changed_scope(database, admission):
    payload = devices_api.DevicePatrolLaunchRequest(device_ids=["device"], request_id="request")
    first = launch.launch_patrol(payload, actor="actor")
    second = launch.launch_patrol(payload, actor="actor")
    assert first["patrol_batch_id"] == second["patrol_batch_id"]
    assert second["idempotent"]
    assert len(admission) == 1
    with pytest.raises(HTTPException, match="不同巡检范围"):
        launch.launch_patrol(payload.model_copy(update={"device_ids": ["other"]}), actor="actor")
    assert len(admission) == 1
    with database() as db:
        assert db.query(m.PatrolBatchRecord).count() == 1
        assert db.query(m.WorkflowRun).count() == 0


def test_repeat_freezes_new_batch_and_preserves_original_evidence(database, admission):
    run_id = seed(database, status="failed", phase="events")
    result = launch.repeat_batch("batch", actor="actor", request_id="retry", failed_only=True)
    assert result["new_patrol_batch_id"] != "batch"
    assert result["retried_count"] == 1
    payload, actor, origin = admission[0]
    assert payload.device_ids == ["device"]
    assert payload.launch_mode == "direct"
    assert actor == "actor"
    assert origin["source_batch_id"] == "batch"
    replay = launch.repeat_batch("batch", actor="actor", request_id="retry", failed_only=True)
    assert replay["new_patrol_batch_id"] == result["new_patrol_batch_id"]
    assert len(admission) == 1
    with database() as db:
        assert (
            db.get(m.PatrolRun, run_id).metadata_json["inspection_execution"]["status"] == "failed"
        )
        assert db.query(m.CommandSnapshot).count() == 1


def test_rerun_rejects_active_or_archived_batch(database, admission):
    seed(database)
    with pytest.raises(HTTPException, match="仍在执行"):
        launch.repeat_batch("batch", actor="actor")
    with database() as db:
        db.get(m.PatrolBatchRecord, "batch").archived = True
    with pytest.raises(HTTPException, match="归档"):
        launch.repeat_batch("batch", actor="actor")
    assert admission == []


def test_cancelled_task_cannot_retry_until_original_worker_exits(database, admission, monkeypatch):
    seed(database, status="cancelled")

    @contextmanager
    def busy(_task):
        raise online_execution.OnlinePatrolBusyError("active")
        yield

    monkeypatch.setattr(launch, "execution_lock", busy)
    with pytest.raises(HTTPException, match="尚未退出"):
        launch.repeat_batch("batch", actor="actor", failed_only=True)
    assert admission == []


def test_launch_requires_persisted_actor(database, admission):
    with pytest.raises(HTTPException) as error:
        launch.launch_patrol(
            devices_api.DevicePatrolLaunchRequest(device_ids=["device"]), actor=None
        )
    assert error.value.status_code == 403
    assert admission == []


def test_dispatch_cannot_claim_a_plan_before_its_runtime_is_frozen(database):
    seed(database, status="queued")
    with database() as db:
        batch = db.get(m.PatrolBatchRecord, "batch")
        batch.runtime_manifest_id = None
        batch.dispatch_status = "pending"
        target = db.query(m.PatrolBatchDeviceRecord).one()
        target.celery_task_id = None
        target.dispatch_status = "pending"
    assert PatrolBatchManifestRepository().claim_dispatch_targets("batch") == []


def test_public_launch_defaults_to_fixed_task_and_passes_frozen_scope(monkeypatch):
    preview = {
        "platform_readiness": {"can_launch": True},
        "check_item_mode": True,
        "rows": [{"device_id": "device"}],
        "target_count": 1,
        "errors": [],
        "error_count": 0,
        "target_devices": [{"device_id": "device"}],
        "check_item_ids_by_device": {"device": ["check"]},
    }
    monkeypatch.setattr(devices_api, "_patrol_launch_preview", lambda *_: preview)
    handler = Mock()
    handler.handle.return_value = {"success": True}
    monkeypatch.setattr(devices_api, "StartPatrolHandler", lambda **_: handler)
    payload = devices_api.DevicePatrolLaunchRequest(
        device_ids=["device"], scope_contract={"id": "scope"}
    )
    assert payload.launch_mode == "direct"
    devices_api._launch_patrol(payload, actor="actor", batch_id="batch", intent={})
    command = handler.handle.call_args.args[0]
    assert command.launch_mode == "direct"
    assert command.scope_contract == {"id": "scope"}
    assert command.check_item_ids_by_device == {"device": ["check"]}
    assert command.target_snapshot["selector"]["inspection"]["user_id"] == "actor"
