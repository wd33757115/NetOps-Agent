from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items.rollout_capture import (
    ProductionPatrolCaptureAdapter,
)
from src.infrastructure.db.models import (
    Base,
    CollectionExecutionPlanRecord,
    CommandSnapshot,
    NetworkDevice,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolRun,
)


@contextmanager
def _session_scope(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _runtime(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'capture.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            NetworkDevice.__table__,
            CollectionExecutionPlanRecord.__table__,
            PatrolBatchRecord.__table__,
            PatrolBatchDeviceRecord.__table__,
            PatrolRun.__table__,
            CommandSnapshot.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)

    def scope():
        return _session_scope(factory)

    return scope


class _Plan:
    def to_metadata(self):
        return {
            "device_id": "device-1",
            "action": "patrol",
            "mode": "parser_validation",
            "profile_name": "existing-profile",
            "credential_ref": "vault/device-1",
            "collector_options": {"timeout": 30},
            "collection_items": [{"command": "unrelated"}],
        }


class _Resolver:
    def build_plan(self, *, device, action, mode):
        assert device["device_id"] == "device-1"
        assert action == "patrol"
        assert mode == "parser_validation"
        return _Plan()


class _StartHandler:
    def __init__(self, captured):
        self._captured = captured

    def handle(self, command):
        self._captured["command"] = command
        return {
            "patrol_batch_id": "rollout-capture-execution-1",
            "dispatch_status": "queued",
            "queued_count": 1,
        }


def _selection(*commands, commands_by_track=None):
    return {
        "schema_version": "rollout-stage-selection/v2",
        "execution_mode": "live_capture",
        "selection_basis_digest": "a" * 64,
        "targets": [
            {
                "target_key": "target-1",
                "device_id": "device-1",
                "check_item_version_id": "check-1",
                "implementation_version_id": "impl-1",
                "commands": list(commands),
                **(
                    {"commands_by_track": commands_by_track}
                    if commands_by_track is not None
                    else {}
                ),
            }
        ],
    }


def test_live_capture_start_uses_exact_frozen_commands_and_shadow_purpose(
    tmp_path,
):
    scope = _runtime(tmp_path)
    now = datetime.now(timezone.utc)
    with scope() as db:
        db.add(
            NetworkDevice(
                id="device-1",
                device_name="core-1",
                management_ip="192.0.2.1",
                credential_ref="vault/device-1",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
    captured = {}
    adapter = ProductionPatrolCaptureAdapter(
        scope,
        start_handler_factory=lambda _batch_id: _StartHandler(captured),
        policy_resolver_factory=lambda: _Resolver(),
    )
    result = adapter.start(
        execution_id="execution-1",
        selection_manifest=_selection(
            "display cpu-usage",
            "display memory",
            commands_by_track={
                "baseline": ["display cpu-usage"],
                "candidate": ["display memory"],
            },
        ),
    )
    assert result["status"] == "capturing"
    command = captured["command"]
    assert command.workflow_name == "rollout-live-capture"
    assert command.launch_mode == "direct"
    assert command.mode == "parser_validation"
    assert command.report_purpose == "shadow_validation"
    assert command.check_item_ids_by_device is None
    assert [
        item["command"]
        for item in command.rows[0]["plan"]["collection_items"]
    ] == ["display cpu-usage", "display memory"]
    assert command.rows[0]["plan"]["runtime_manifest_id"] is None

    # Exercise the real admission handler: a fake capture adapter alone used to
    # hide an invalid launch_mode and prevented every live capture from starting.
    from unittest.mock import Mock
    from src.core.patrol.handlers import StartPatrolHandler

    plans, batches = Mock(), Mock()
    plans.create_plan.return_value = {"execution_plan_id": "plan-1"}
    started = StartPatrolHandler(
        plan_repository=plans,
        batch_repository=batches,
        batch_dispatcher=lambda batch_id: "dispatch-1",
    ).handle(command)
    assert started["launch_mode"] == "direct"
    assert started["queued_count"] == 1
    assert batches.create_pending_batch.call_args.kwargs["report_purpose"] == "shadow_validation"


def test_live_capture_poll_returns_target_bound_snapshots(tmp_path):
    scope = _runtime(tmp_path)
    now = datetime.now(timezone.utc)
    with scope() as db:
        db.add_all(
            [
                NetworkDevice(
                    id="device-1",
                    device_name="core-1",
                    management_ip="192.0.2.1",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                PatrolBatchRecord(
                    patrol_batch_id="capture-1",
                    workflow_name="rollout-live-capture",
                    launch_mode="task",
                    patrol_mode="parser_validation",
                    report_purpose="shadow_validation",
                    target_type="rollout_stage",
                    target_label="Rollout stage",
                    selector_json={},
                    target_count=1,
                    launched_count=1,
                    skipped_count=0,
                    save_baseline=False,
                    dispatch_status="completed",
                    created_at=now,
                    updated_at=now,
                ),
                PatrolRun(
                    id="snapshot-run-1",
                    mode="parser_validation",
                    source="device-patrol",
                    status="completed",
                    collection_status="completed",
                    parser_readiness_status="ready",
                    fact_readiness_status="ready",
                    analysis_status="completed",
                    baseline_eligible=False,
                    run_purpose="scheduled_patrol",
                    logical_run_id="patrol-task-1",
                    attempt_number=0,
                    owner_generation=1,
                    device_count=1,
                    command_count=1,
                    started_at=now,
                    completed_at=now,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                PatrolBatchDeviceRecord(
                    id="batch-target-1",
                    patrol_batch_id="capture-1",
                    device_id="device-1",
                    device_name="core-1",
                    management_ip="192.0.2.1",
                    selection_status="selected",
                    preflight_status="ready",
                    celery_task_id="task-1",
                    dispatch_status="dispatched",
                    dispatch_attempts=1,
                    created_at=now,
                    updated_at=now,
                ),
                CommandSnapshot(
                    id="snapshot-1",
                    run_id="snapshot-run-1",
                    inventory_device_id="device-1",
                    device_id="device-1",
                    execution_item_id="execution-item-1",
                    device_name="core-1",
                    management_ip="192.0.2.1",
                    command="display cpu-usage",
                    canonical_command="display cpu-usage",
                    observed_at=now,
                    raw_text="CPU usage: 12%",
                    raw_text_hash="b" * 64,
                    parse_status="parsed",
                    output_status="normal",
                    snapshot_kind="full",
                    created_at=now,
                ),
            ]
        )

    adapter = ProductionPatrolCaptureAdapter(scope)
    result = adapter.poll(
        capture_batch_id="capture-1",
        selection_manifest=_selection("display cpu-usage"),
    )
    assert result["status"] == "completed"
    assert result["snapshot_ids"] == ["snapshot-1"]
    assert result["snapshot_bindings"]["target-1"]["periods"][0][
        "period_key"
    ] == "snapshot-run-1"
    assert result["snapshot_bindings"]["target-1"][
        "snapshot_ids_by_track"
    ] == {
        "baseline": ["snapshot-1"],
        "candidate": ["snapshot-1"],
    }

    incomplete = adapter.poll(
        capture_batch_id="capture-1",
        selection_manifest=_selection(
            "display cpu-usage",
            "display memory",
        ),
    )
    assert incomplete["status"] == "failed"
    assert incomplete["failure_code"] == "capture_evidence_incomplete"
