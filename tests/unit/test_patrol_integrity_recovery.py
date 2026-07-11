from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.core.parser.key_contract import EntityKeyError
from src.core.patrol.change_event_repository import ChangeEventRepository
from src.core.patrol.event_builder import NetworkEvent, _event_correlation_identity
from src.core.patrol.event_rules import EventRuleRepository
from src.core.patrol.repository import PatrolRepository, _as_datetime
from src.core.state import normalizer
from src.core.state.repository import StateRepository
from src.infrastructure.db.models import (
    AuditLogRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
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
                },
            }

        def save_observations(self, observations, *, update_current_state):
            saved["observations"] = observations
            saved["update_current_state"] = update_current_state
            return len(observations), len(observations)

    class FakePatrolRepository:
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
