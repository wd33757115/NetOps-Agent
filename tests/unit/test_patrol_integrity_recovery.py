from __future__ import annotations

import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.core.parser.key_contract import EntityKeyError
from src.core.patrol.change_event_repository import ChangeEventRepository
from src.core.patrol.event_builder import NetworkEvent, _event_correlation_identity
from src.core.patrol.event_rules import EventRuleRepository
from src.core.patrol.repository import (
    AnalysisAttemptFencedError,
    AnalysisLeaseHeartbeat,
    PatrolRunFencedError,
    PatrolRepository,
    _as_datetime,
)
from src.core.state import normalizer
from src.core.state.repository import StateRepository
from src.infrastructure.db.models import (
    AuditLogRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventOutboxRecord,
    NetworkEventRecord,
    PatrolAnalysisAttemptRecord,
    PatrolRun,
)
from src.infrastructure.db.postgres import get_db_session


def test_normalize_run_commits_valid_collections_and_quarantines_invalid(monkeypatch):
    saved = {}
    failed = []

    class FakeStateRepository:
        def list_run_snapshot_ids(self, run_id, *, device_ids=None):
            return ["snapshot-good", "snapshot-bad"]

        def get_snapshot_context(self, snapshot_id):
            return {
                "mode": "production",
                "source": "test",
                "dataset_id": None,
                "snapshot": {
                    "id": snapshot_id,
                    "run_id": "run-1",
                    "device_id": "SW1",
                    "canonical_command": snapshot_id,
                    "observed_at": "2026-07-11T01:00:00+00:00",
                    "parse_status": "parsed",
                    "structured_json": [{"interface": "GigabitEthernet1/0/1"}],
                },
            }

        def save_observations(self, observations, *, update_current_state):
            saved["observations"] = observations
            saved["update_current_state"] = update_current_state
            return len(observations), len(observations)

    class FakePatrolRepository:
        def begin_analysis_attempt(self, **_kwargs):
            return {
                "reused": False,
                "in_progress": False,
                "attempt_id": "attempt-1",
                "attempt_number": 1,
                "owner_token": "owner-1",
                "rule_bundle": {},
                "rule_bundle_digest": "bundle-1",
            }

        def touch_analysis_attempt(self, *args, **kwargs):
            pass

        def complete_analysis_attempt(self, *args, **kwargs):
            pass

        def fail_analysis_attempt(self, *args, **kwargs):
            pass

        def update_analysis_status(self, *args, **kwargs):
            saved.setdefault("statuses", []).append(kwargs.get("analysis_status"))

        def mark_collection_analysis_failed(self, **kwargs):
            failed.append(kwargs)

    observation = SimpleNamespace(to_dict=lambda: {"id": "obs-good"})

    def build(snapshot, **_kwargs):
        if snapshot["id"] == "snapshot-bad":
            raise EntityKeyError("missing interface")
        return [observation]

    monkeypatch.setattr(normalizer, "StateRepository", FakeStateRepository)
    monkeypatch.setattr("src.core.patrol.repository.PatrolRepository", FakePatrolRepository)
    monkeypatch.setattr(normalizer, "observations_from_snapshot", build)

    result = normalizer.normalize_run("run-1")

    assert result["success"] is True
    assert result["partial"] is True
    assert result["successful_snapshot_count"] == 1
    assert result["failed_snapshot_count"] == 1
    assert saved["observations"] == [observation]
    assert saved["update_current_state"] is True
    assert failed[0]["snapshot_id"] == "snapshot-bad"


def test_analysis_lease_heartbeat_is_wall_clock_driven():
    touches = []

    class FakeRepository:
        def touch_analysis_attempt(self, attempt_id, **kwargs):
            touches.append((attempt_id, kwargs))

    heartbeat = AnalysisLeaseHeartbeat(
        FakeRepository(),
        {"attempt_id": "attempt-1", "attempt_number": 1, "owner_token": "owner-1"},
        interval_seconds=1,
    ).start()
    try:
        time.sleep(1.1)
    finally:
        heartbeat.stop()

    assert len(touches) >= 1


def test_tombstone_identity_is_deterministic():
    previous = {
        "device_id": "SW1",
        "entity_type": "interface_l3",
        "entity_key": '["Gi1/0/1"]',
        "field_name": "status",
        "canonical_command": "show ip interface brief",
        "mode": "production",
        "source": "test",
        "source_type": "cli",
        "snapshot_id": "snapshot-old",
        "observed_at": "2026-07-11T01:00:00+00:00",
        "metadata_json": {},
    }
    repository = StateRepository()
    first = repository.build_absence_observation(
        previous,
        run_id="run-1",
        snapshot_id="snapshot-new",
        observed_at="2026-07-11T02:00:00+00:00",
        missing_count=1,
        missing_confirmations=2,
    )
    second = repository.build_absence_observation(
        previous,
        run_id="run-1",
        snapshot_id="snapshot-new",
        observed_at="2026-07-11T02:00:00+00:00",
        missing_count=1,
        missing_confirmations=2,
    )

    assert first.idempotency_key == second.idempotency_key
    assert first.observation_id == second.observation_id


def test_correlation_dimensions_are_unambiguous_and_entity_specific():
    first = _event_correlation_identity(
        device_id="R1",
        entity_type="bgp_peer",
        entity_key='["VRF-A","10.0.0.1"]',
        change={"dimensions": {"vrf": "VRF-A", "peer_ip": "10.0.0.1"}},
        correlation_keys=("device_id", "vrf", "peer_ip"),
    )
    second = _event_correlation_identity(
        device_id="R1",
        entity_type="bgp_peer",
        entity_key='["VRF-B","20.0.0.1"]',
        change={"dimensions": {"vrf": "VRF-B", "peer_ip": "20.0.0.1"}},
        correlation_keys=("device_id", "vrf", "peer_ip"),
    )

    assert first[0] != second[0]
    assert first[1] == {"device_id": "R1", "vrf": "VRF-A", "peer_ip": "10.0.0.1"}


def test_active_event_keeps_opening_run_and_appends_occurrence():
    first_id = str(uuid.uuid4())
    second_id = str(uuid.uuid4())
    correlation_key = uuid.uuid4().hex
    repository = ChangeEventRepository()

    def event(event_id, run_id, change_id, observed_at):
        return NetworkEvent(
            event_id=event_id,
            event_type="InterfaceDown",
            severity="major",
            device_id="SW1",
            entity_type="interface_l2",
            entity_key='["Gi1/0/1"]',
            source_change_id=change_id,
            run_id=run_id,
            occurred_at=observed_at,
            status="open",
            payload={
                "change": {"change_id": change_id},
                "rule": {"event_class": "fault"},
                "correlation_key": correlation_key,
                "correlation_dimensions": {"device_id": "SW1"},
            },
        )

    try:
        assert (
            repository.save_events(
                [event(first_id, "run-A", "change-A", "2026-07-11T01:00:00+00:00")]
            )
            == 1
        )
        assert (
            repository.save_events(
                [event(second_id, "run-B", "change-B", "2026-07-11T01:05:00+00:00")]
            )
            == 0
        )
        with get_db_session() as db:
            row = db.get(NetworkEventRecord, first_id)
            assert row.run_id == "run-A"
            assert row.source_change_id == "change-A"
            assert row.last_seen_run_id == "run-B"
            assert row.latest_change_id == "change-B"
            assert row.occurrence_count == 2
            occurrences = (
                db.query(NetworkEventOccurrenceRecord)
                .filter(NetworkEventOccurrenceRecord.event_id == first_id)
                .order_by(NetworkEventOccurrenceRecord.observed_at)
                .all()
            )
            assert [item.run_id for item in occurrences] == ["run-A", "run-B"]
            assert [item.occurrence_kind for item in occurrences] == [
                "opening",
                "recurrence",
            ]
            outbox_rows = (
                db.query(NetworkEventOutboxRecord)
                .filter(NetworkEventOutboxRecord.event_id == first_id)
                .order_by(NetworkEventOutboxRecord.created_at)
                .all()
            )
            assert len(outbox_rows) == 2
            assert len({item.occurrence_id for item in outbox_rows}) == 2
            assert [item.payload_json["publication_kind"] for item in outbox_rows] == [
                "opening",
                "recurrence",
            ]
    finally:
        with get_db_session() as db:
            db.query(NetworkEventRecord).filter(NetworkEventRecord.id == first_id).delete(
                synchronize_session=False
            )


def test_create_run_is_idempotent_and_stale_run_is_reconciled():
    run_id = f"test-{uuid.uuid4()}"
    repository = PatrolRepository()
    try:
        repository.create_run(
            run_id=run_id,
            source="test",
            mode="production",
            logical_run_id="logical-1",
            lease_seconds=60,
        )
        repository.finish_run(
            run_id,
            collection_status="completed",
            analysis_status="completed",
        )
        repository.create_run(run_id=run_id, source="test", mode="production")
        assert repository.get_run(run_id)["status"] == "completed"

        with get_db_session() as db:
            row = db.get(PatrolRun, run_id)
            row.status = "running"
            row.collection_status = "running"
            row.analysis_status = "pending"
            row.completed_at = None
            row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        result = repository.reconcile_stale_runs()
        assert run_id in result["run_ids"]
        reconciled = repository.get_run(run_id)
        assert reconciled["status"] == "abandoned"
        assert reconciled["analysis_status"] == "failed"
    finally:
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(synchronize_session=False)


def test_worker_redelivery_abandons_running_attempt_and_allocates_next():
    logical_run_id = f"patrol-{uuid.uuid4()}"
    repository = PatrolRepository()
    try:
        repository.create_run(
            run_id=logical_run_id,
            source="device-patrol",
            mode="production",
            logical_run_id=logical_run_id,
            attempt_number=0,
            lease_seconds=900,
        )

        decision = repository.prepare_redelivery_attempt(logical_run_id)

        assert decision.action == "allocate_new_attempt"
        assert decision.attempt_number == 1
        assert repository.get_run(decision.run_id)["status"] == "reserved"
        abandoned = repository.get_run(logical_run_id)
        assert abandoned["status"] == "abandoned"
        assert abandoned["collection_status"] == "failed"
        assert abandoned["analysis_status"] == "failed"
        assert abandoned["metadata_json"]["abandoned_reason"] == "celery_worker_lost_redelivery"
    finally:
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.logical_run_id == logical_run_id).delete(
                synchronize_session=False
            )


def test_completed_patrol_redelivery_does_not_allocate_another_attempt():
    logical_run_id = f"patrol-{uuid.uuid4()}"
    repository = PatrolRepository()
    try:
        repository.create_run(
            run_id=logical_run_id,
            source="device-patrol",
            mode="production",
            logical_run_id=logical_run_id,
        )
        repository.finish_run(
            logical_run_id,
            collection_status="completed",
            analysis_status="completed",
        )
        repository.mark_celery_task_completed(logical_run_id)

        decision = repository.prepare_redelivery_attempt(logical_run_id)

        assert decision.action == "already_completed"
        assert decision.run_id == logical_run_id
        with get_db_session() as db:
            assert (
                db.query(PatrolRun)
                .filter(PatrolRun.logical_run_id == logical_run_id)
                .count()
                == 1
            )
    finally:
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.logical_run_id == logical_run_id).delete(
                synchronize_session=False
            )


def test_concurrent_event_writers_share_one_active_aggregate():
    correlation_key = uuid.uuid4().hex
    event_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    def save(index: int) -> int:
        event = NetworkEvent(
            event_id=event_ids[index],
            event_type="InterfaceDown",
            severity="major",
            device_id="SW1",
            entity_type="interface_l2",
            entity_key='["Gi1/0/9"]',
            source_change_id=f"change-{event_ids[index]}",
            run_id=f"run-{index}",
            occurred_at=f"2026-07-11T01:0{index}:00+00:00",
            status="open",
            payload={
                "change": {"change_id": f"change-{event_ids[index]}"},
                "rule": {"event_class": "fault"},
                "correlation_key": correlation_key,
                "correlation_dimensions": {"device_id": "SW1"},
            },
        )
        return ChangeEventRepository().save_events([event])

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, range(2)))
        assert sorted(results) == [0, 1]
        with get_db_session() as db:
            active = (
                db.query(NetworkEventRecord)
                .filter(
                    NetworkEventRecord.event_type == "InterfaceDown",
                    NetworkEventRecord.correlation_key == correlation_key,
                    NetworkEventRecord.status == "open",
                )
                .one()
            )
            assert active.occurrence_count == 2
            assert (
                db.query(NetworkEventOccurrenceRecord)
                .filter(NetworkEventOccurrenceRecord.event_id == active.id)
                .count()
                == 2
            )
    finally:
        with get_db_session() as db:
            db.query(NetworkEventRecord).filter(
                NetworkEventRecord.correlation_key == correlation_key
            ).delete(synchronize_session=False)


def test_redelivery_fences_live_attempt_owned_by_dead_worker():
    logical_run_id = f"patrol-{uuid.uuid4()}"
    repository = PatrolRepository()
    try:
        repository.create_run(
            run_id=logical_run_id,
            source="device-patrol",
            mode="production",
            logical_run_id=logical_run_id,
            worker_id="worker-A",
            lease_seconds=900,
        )

        decision = repository.prepare_redelivery_attempt(
            logical_run_id,
            worker_id="worker-B",
        )

        assert decision.action == "allocate_new_attempt"
        assert decision.attempt_number == 1
        assert repository.get_run(logical_run_id)["status"] == "abandoned"
        reserved = repository.get_run(decision.run_id)
        assert reserved["status"] == "reserved"
        assert reserved["worker_id"] == "worker-B"
    finally:
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.logical_run_id == logical_run_id).delete(
                synchronize_session=False
            )


def test_collection_owner_fences_stale_fact_writes_and_second_hard_kill():
    logical_run_id = f"patrol-{uuid.uuid4()}"
    repository = PatrolRepository()
    try:
        repository.create_run(
            run_id=logical_run_id,
            source="device-patrol",
            mode="production",
            logical_run_id=logical_run_id,
            worker_id="worker-A",
            owner_token="owner-A",
            lease_seconds=900,
        )
        first_takeover = repository.prepare_redelivery_attempt(
            logical_run_id, worker_id="worker-B"
        )

        with pytest.raises(PatrolRunFencedError):
            repository.save_collection_result(
                run_id=logical_run_id,
                device_id="SW1",
                canonical_command="show version",
                status="complete",
                owner_token="owner-A",
            )
        with pytest.raises(PatrolRunFencedError):
            repository.save_snapshot_idempotent(
                run_id=logical_run_id,
                device_id="SW1",
                device_name="SW1",
                command="show version",
                command_canonical="show version",
                raw_text="stale output",
                owner_token="owner-A",
            )
        with pytest.raises(PatrolRunFencedError):
            repository.finish_run(
                logical_run_id,
                collection_status="completed",
                owner_token="owner-A",
            )

        repository.create_run(
            run_id=first_takeover.run_id,
            source="device-patrol",
            mode="production",
            logical_run_id=logical_run_id,
            attempt_number=first_takeover.attempt_number,
            worker_id="worker-B",
            owner_token=first_takeover.owner_token,
            lease_seconds=900,
        )
        assert repository.get_run(first_takeover.run_id)["metadata_json"][
            "attempt_reserved"
        ] is False

        second_takeover = repository.prepare_redelivery_attempt(
            logical_run_id, worker_id="worker-C"
        )
        assert second_takeover.action == "allocate_new_attempt"
        assert second_takeover.attempt_number == 2
        assert repository.get_run(first_takeover.run_id)["status"] == "abandoned"
        with pytest.raises(PatrolRunFencedError):
            repository.finish_run(
                first_takeover.run_id,
                collection_status="completed",
                owner_token=first_takeover.owner_token,
            )
    finally:
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.logical_run_id == logical_run_id).delete(
                synchronize_session=False
            )


def test_terminal_collection_is_completion_truth_without_wrapper_marker():
    logical_run_id = f"patrol-{uuid.uuid4()}"
    repository = PatrolRepository()
    try:
        repository.create_run(
            run_id=logical_run_id,
            source="device-patrol",
            mode="production",
            logical_run_id=logical_run_id,
            worker_id="worker-A",
        )
        repository.finish_run(
            logical_run_id,
            collection_status="completed",
            analysis_status="pending",
        )

        decision = repository.prepare_redelivery_attempt(
            logical_run_id,
            worker_id="worker-B",
        )

        assert decision.action == "already_completed"
        assert decision.reason == "broker_redelivered_after_terminal_collection_commit"
        with get_db_session() as db:
            assert (
                db.query(PatrolRun)
                .filter(PatrolRun.logical_run_id == logical_run_id)
                .count()
                == 1
            )
    finally:
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.logical_run_id == logical_run_id).delete(
                synchronize_session=False
            )


def test_analysis_takeover_fences_late_complete_and_fail():
    run_id = f"analysis-{uuid.uuid4()}"
    repository = PatrolRepository()
    try:
        repository.create_run(run_id=run_id, source="test", mode="production")
        first = repository.begin_analysis_attempt(
            run_id=run_id,
            stage="change_detection",
            scope={"full_run": True, "input_revision_digest": "input-1"},
            rule_bundle={"change_policies": []},
            lease_seconds=30,
        )
        with get_db_session() as db:
            row = db.get(PatrolAnalysisAttemptRecord, first["attempt_id"])
            row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)

        second = repository.begin_analysis_attempt(
            run_id=run_id,
            stage="change_detection",
            scope={"full_run": True, "input_revision_digest": "input-1"},
            rule_bundle={"change_policies": [{"version_id": "new"}]},
        )
        assert second["attempt_number"] == 2
        assert second["owner_token"] != first["owner_token"]
        assert second["rule_bundle"] == first["rule_bundle"]

        repository.complete_analysis_attempt(
            second["attempt_id"],
            attempt_number=second["attempt_number"],
            owner_token=second["owner_token"],
            result={"success": True, "change_count": 1, "changes": [{"id": "c1"}]},
        )
        with pytest.raises(AnalysisAttemptFencedError):
            repository.complete_analysis_attempt(
                first["attempt_id"],
                attempt_number=first["attempt_number"],
                owner_token=first["owner_token"],
                result={"success": True, "change_count": 99},
            )
        with pytest.raises(AnalysisAttemptFencedError):
            repository.fail_analysis_attempt(
                first["attempt_id"],
                attempt_number=first["attempt_number"],
                owner_token=first["owner_token"],
                error="late failure",
            )
        with get_db_session() as db:
            row = db.get(PatrolAnalysisAttemptRecord, second["attempt_id"])
            assert row.status == "completed"
            assert row.result_json == {"success": True, "change_count": 1}
            assert row.result_digest
    finally:
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(
                synchronize_session=False
            )


def test_normalizing_lease_expiry_converges_run_to_failed():
    run_id = f"normalizing-{uuid.uuid4()}"
    repository = PatrolRepository()
    try:
        repository.create_run(run_id=run_id, source="test", mode="production")
        repository.update_analysis_status(run_id, analysis_status="normalizing")
        attempt = repository.begin_analysis_attempt(
            run_id=run_id,
            stage="normalizing",
            scope={"full_run": True, "input_revision_digest": "snapshots-1"},
            rule_bundle={"observation_contracts": "snapshot_bound"},
        )
        with get_db_session() as db:
            row = db.get(PatrolAnalysisAttemptRecord, attempt["attempt_id"])
            row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)

        result = repository.reconcile_stale_analysis_attempts()

        assert run_id in result["run_ids"]
        run = repository.get_run(run_id)
        assert run["analysis_status"] == "failed"
        assert run["metadata_json"]["analysis_failure_stage"] == "normalizing"
    finally:
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(
                synchronize_session=False
            )


def test_partial_scope_lease_expiry_does_not_fail_whole_run():
    run_id = f"partial-analysis-{uuid.uuid4()}"
    repository = PatrolRepository()
    try:
        repository.create_run(run_id=run_id, source="test", mode="production")
        repository.update_analysis_status(run_id, analysis_status="completed")
        attempt = repository.begin_analysis_attempt(
            run_id=run_id,
            stage="normalizing",
            scope={
                "full_run": False,
                "device_ids": ["SW1"],
                "input_revision_digest": "snapshots-sw1",
            },
            rule_bundle={"observation_contracts": "snapshot_bound"},
        )
        with get_db_session() as db:
            row = db.get(PatrolAnalysisAttemptRecord, attempt["attempt_id"])
            row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)

        result = repository.reconcile_stale_analysis_attempts()

        assert run_id in result["run_ids"]
        assert repository.get_run(run_id)["analysis_status"] == "completed"
    finally:
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(
                synchronize_session=False
            )


def test_late_recovery_and_fault_do_not_regress_projection():
    correlation_key = uuid.uuid4().hex
    repository = ChangeEventRepository()

    def event(event_type, change_id, observed_at, *, event_class="fault", resolves=None):
        return NetworkEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            severity="major",
            device_id="SW1",
            entity_type="interface_l2",
            entity_key='["Gi1/0/7"]',
            source_change_id=change_id,
            run_id=f"run-{change_id}",
            occurred_at=observed_at,
            status="open",
            payload={
                "change": {"change_id": change_id},
                "rule": {
                    "event_class": event_class,
                    "recovery": {"resolves": resolves or []},
                },
                "correlation_key": correlation_key,
                "correlation_dimensions": {"device_id": "SW1"},
            },
        )

    try:
        repository.save_events(
            [event("InterfaceDown", "fault-1", "2026-07-11T01:00:00+00:00")]
        )
        repository.save_events(
            [event("InterfaceDown", "fault-2", "2026-07-11T01:10:00+00:00")]
        )
        repository.save_events(
            [
                event(
                    "InterfaceUp",
                    "recovery-late",
                    "2026-07-11T01:05:00+00:00",
                    event_class="recovery",
                    resolves=["InterfaceDown"],
                )
            ]
        )
        with get_db_session() as db:
            fault = (
                db.query(NetworkEventRecord)
                .filter(
                    NetworkEventRecord.event_type == "InterfaceDown",
                    NetworkEventRecord.correlation_key == correlation_key,
                )
                .one()
            )
            assert fault.status == "open"
            late_recovery = (
                db.query(NetworkEventOccurrenceRecord)
                .filter(
                    NetworkEventOccurrenceRecord.event_id == fault.id,
                    NetworkEventOccurrenceRecord.occurrence_kind == "recovery",
                )
                .one()
            )
            assert late_recovery.payload_json["superseded"] is True

        repository.save_events(
            [
                event(
                    "InterfaceUp",
                    "recovery-current",
                    "2026-07-11T01:15:00+00:00",
                    event_class="recovery",
                    resolves=["InterfaceDown"],
                )
            ]
        )
        repository.save_events(
            [event("InterfaceDown", "fault-late", "2026-07-11T01:12:00+00:00")]
        )
        with get_db_session() as db:
            faults = (
                db.query(NetworkEventRecord)
                .filter(
                    NetworkEventRecord.event_type == "InterfaceDown",
                    NetworkEventRecord.correlation_key == correlation_key,
                )
                .all()
            )
            assert len(faults) == 1
            assert faults[0].status == "resolved"
            late_fault = (
                db.query(NetworkEventOccurrenceRecord)
                .filter(
                    NetworkEventOccurrenceRecord.event_id == faults[0].id,
                    NetworkEventOccurrenceRecord.source_change_id == "fault-late",
                )
                .one()
            )
            assert late_fault.payload_json["superseded"] is True
    finally:
        with get_db_session() as db:
            db.query(NetworkEventRecord).filter(
                NetworkEventRecord.correlation_key == correlation_key
            ).delete(synchronize_session=False)


def test_invalid_fact_timestamp_is_rejected():
    with pytest.raises(ValueError, match="invalid fact timestamp"):
        _as_datetime("not-a-timestamp")


def test_event_rule_manual_activation_requires_audited_override():
    suffix = uuid.uuid4().hex
    rule_name = f"pytest-force-activation-{suffix}"
    actor = f"pytest-{suffix}"
    repository = EventRuleRepository()
    version_id = repository.create_rule_version(
        name=rule_name,
        event_type="InterfaceDown",
        severity="major",
        condition={"kind": "transition", "from": ["up"], "to": ["down"]},
        created_by=actor,
    )
    rule_id = None
    try:
        with pytest.raises(ValueError, match="direct event rule activation is disabled"):
            repository.activate_rule_version(version_id)

        result = repository.force_activate_rule_version(
            version_id,
            actor=actor,
            reason="production incident emergency override",
        )

        assert result["version_id"] == version_id
        with get_db_session() as db:
            version = db.get(EventRuleVersionRecord, version_id)
            rule_id = version.rule_id
            assert version.status == "active"
            audit = (
                db.query(AuditLogRecord)
                .filter(AuditLogRecord.resource_id == version_id)
                .filter(AuditLogRecord.action == "event_rule_force_activate")
                .one()
            )
            assert audit.user_id == actor
            assert audit.detail["emergency_override"] is True
    finally:
        with get_db_session() as db:
            db.query(AuditLogRecord).filter(AuditLogRecord.resource_id == version_id).delete(
                synchronize_session=False
            )
            version = db.get(EventRuleVersionRecord, version_id)
            rule_id = rule_id or (version.rule_id if version else None)
            db.query(EventRuleVersionRecord).filter(EventRuleVersionRecord.id == version_id).delete(
                synchronize_session=False
            )
            if rule_id:
                db.query(EventRuleRecord).filter(EventRuleRecord.id == rule_id).delete(
                    synchronize_session=False
                )
