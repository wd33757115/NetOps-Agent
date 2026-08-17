from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from src.core.patrol.event_rules import EventRuleDefinition
from src.core.patrol.rule_replay import (
    replay_event_rule_definitions,
    replay_event_rule_definitions_against_changes,
)
from src.infrastructure.db.models import NetworkChangeRecord
from src.infrastructure.db.postgres import get_db_session


def test_event_rule_replay_matches_historical_change_without_private_payload():
    change_id = str(uuid.uuid4())
    excluded_change_id = str(uuid.uuid4())
    key = hashlib.sha256(change_id.encode("utf-8")).hexdigest()
    with get_db_session() as db:
        db.add(
            NetworkChangeRecord(
                id=change_id,
                idempotency_key=key,
                run_id="pytest-rule-replay",
                device_id="lab-device-1",
                canonical_command="display cpu",
                entity_type="cpu",
                entity_key="slot=1",
                field="cpu_5s",
                old_value_json=30,
                new_value_json=95,
                change_type="modified",
                source="pytest",
                event_status="pending",
                detected_at=datetime.now(timezone.utc),
                confidence=1.0,
            )
        )
        db.add(
            NetworkChangeRecord(
                id=excluded_change_id,
                idempotency_key=hashlib.sha256(excluded_change_id.encode("utf-8")).hexdigest(),
                run_id="pytest-rule-replay-excluded",
                device_id="lab-device-excluded",
                canonical_command="display cpu",
                entity_type="cpu",
                entity_key="slot=1",
                field="cpu_5s",
                old_value_json=30,
                new_value_json=96,
                change_type="modified",
                source="pytest",
                event_status="pending",
                detected_at=datetime.now(timezone.utc),
                confidence=1.0,
            )
        )
    try:
        result = replay_event_rule_definitions(
            [
                EventRuleDefinition(
                    rule_id="cpu-high-candidate",
                    version=1,
                    event_type="CPUHigh",
                    severity="warning",
                    condition={
                        "kind": "threshold",
                        "entity_types": ["cpu"],
                        "fields": ["cpu_5s"],
                        "gte": 90,
                    },
                )
            ],
            include_active_collisions=False,
            limit=100,
            run_ids=["pytest-rule-replay"],
        )

        assert result["success"] is True
        assert result["sampled_change_count"] == 1
        assert result["matched_change_count"] == 1
        assert result["event_types"]["CPUHigh"] == 1
        assert result["matched_entity_types"]["cpu"] == 1
        assert result["top_devices"] == {"lab-device-1": 1}
        assert result["scope"] == {
            "mode": "run_ids",
            "run_ids": ["pytest-rule-replay"],
            "run_id_count": 1,
            "sampled_run_ids": ["pytest-rule-replay"],
            "sampled_run_id_count": 1,
            "missing_run_ids": [],
        }
        assert result["unexpected_matches"] == []
    finally:
        with get_db_session() as db:
            db.query(NetworkChangeRecord).filter(
                NetworkChangeRecord.id.in_([change_id, excluded_change_id])
            ).delete(synchronize_session=False)


def test_in_memory_event_rule_replay_reports_real_matches_and_rate():
    definition = EventRuleDefinition(
        rule_id="cpu-high-candidate",
        version=1,
        event_type="CPUHigh",
        severity="warning",
        condition={
            "kind": "threshold",
            "entity_types": ["cpu"],
            "fields": ["cpu_5s"],
            "gte": 90,
        },
    )
    result = replay_event_rule_definitions_against_changes(
        [definition],
        [
            {
                "change_id": "change-high",
                "run_id": "period-q3",
                "device_id": "device-a",
                "entity_type": "cpu",
                "entity_key": "slot=1",
                "field": "cpu_5s",
                "old": 30,
                "new": 95,
                "change_type": "modified",
                "detected_at": "2026-07-01T00:00:00+00:00",
            },
            {
                "change_id": "change-normal",
                "run_id": "period-q3",
                "device_id": "device-b",
                "entity_type": "cpu",
                "entity_key": "slot=1",
                "field": "cpu_5s",
                "old": 30,
                "new": 45,
                "change_type": "modified",
                "detected_at": "2026-07-03T00:00:00+00:00",
            },
        ],
        include_active_collisions=False,
    )

    assert result["success"] is True
    assert result["sampled_change_count"] == 2
    assert result["matched_change_count"] == 1
    assert result["event_count"] == 1
    assert result["event_types"] == {"CPUHigh": 1}
    assert result["collision_count"] == 0
    assert result["estimated_daily_event_count"] == 0.5
    assert result["estimated_daily_event_count_basis"] == "observed_window"
    assert result["estimated_daily_event_count_reliable"] is False
    assert result["representativeness"]["match_rate"] == 0.5
    assert result["representativeness"]["window_days"] == 2.0
    assert result["representativeness"]["sparse_history"] is True


def test_candidate_contract_projects_pre_contract_state_changes_for_replay():
    definition = EventRuleDefinition(
        rule_id="transceiver-restored-candidate",
        version=1,
        event_type="TransceiverRestored",
        severity="info",
        condition={
            "kind": "transition",
            "entity_types": ["interface_optical"],
            "fields": ["status"],
            "from_group": "down",
            "to_group": "up",
        },
    )
    result = replay_event_rule_definitions_against_changes(
        [definition],
        [
            {
                "change_id": "change-restored",
                "run_id": "period-q3",
                "device_id": "device-a",
                "entity_type": "interface_transceiver",
                "entity_key": '["GE1/0/1"]',
                "field": "status",
                "old": "The transceiver is absent.",
                "new": "Current diagnostic parameters:",
                "change_type": "modified",
            }
        ],
        include_active_collisions=False,
        candidate_contract={
            "record_sets": [
                {
                    "name": "interface_transceiver_status",
                    "entity_type": "interface_transceiver",
                    "canonical_entity_type": "interface_optical",
                    "primary_keys": ["interface_name"],
                    "entity_dimensions": {
                        "interface": {"field": "interface_name"},
                    },
                    "state_semantics": {
                        "status": {
                            "observation_type": "interface.optical.presence",
                            "value_mapping": {
                                "The transceiver is absent.": "absent",
                                "Current diagnostic parameters:": "present",
                            },
                        }
                    },
                }
            ]
        },
    )

    assert result["matched_change_count"] == 1
    assert result["event_types"] == {"TransceiverRestored": 1}
    assert result["matched_entity_types"] == {"interface_optical": 1}
    assert result["candidate_projection"] == {
        "enabled": True,
        "projected_change_count": 1,
    }


def test_in_memory_event_rule_replay_blocks_candidate_collisions():
    shared_condition = {
        "kind": "threshold",
        "entity_types": ["cpu"],
        "fields": ["cpu_5s"],
        "gte": 90,
    }
    result = replay_event_rule_definitions_against_changes(
        [
            EventRuleDefinition(
                rule_id="cpu-high-a",
                version=1,
                event_type="CPUHighA",
                severity="warning",
                condition=shared_condition,
            ),
            EventRuleDefinition(
                rule_id="cpu-high-b",
                version=1,
                event_type="CPUHighB",
                severity="warning",
                condition=shared_condition,
            ),
        ],
        [
            {
                "change_id": "change-high",
                "run_id": "period-q3",
                "device_id": "device-a",
                "entity_type": "cpu",
                "entity_key": "slot=1",
                "field": "cpu_5s",
                "old": 30,
                "new": 95,
                "change_type": "modified",
            }
        ],
        include_active_collisions=False,
    )

    assert result["success"] is False
    assert result["matched_change_count"] == 1
    assert result["collision_count"] == 1
    assert result["unexpected_matches"][0]["collision_type"] == (
        "candidate_candidate"
    )


def test_event_rule_replay_preserves_dynamic_threshold_context_and_dimensions():
    change_id = str(uuid.uuid4())
    run_id = f"pytest-optical-rule-replay-{uuid.uuid4()}"
    with get_db_session() as db:
        db.add(
            NetworkChangeRecord(
                id=change_id,
                idempotency_key=hashlib.sha256(change_id.encode("utf-8")).hexdigest(),
                run_id=run_id,
                device_id="lab-device-optical",
                canonical_command="inspect optical power",
                entity_type="transceiver_diagnostic",
                entity_key='["port-1"]',
                dimensions_json={
                    "interface_name": "port-1",
                    "value_index": 2,
                    "_observation_context": {"alarm_low_rx_power_dbm": "-10.50"},
                },
                field="channel_rx_power_dbm",
                observation_type="interface.optical.rx_power",
                old_value_json="-5.00",
                new_value_json="-12.00",
                change_type="modified",
                source="pytest",
                event_status="pending",
                detected_at=datetime.now(timezone.utc),
                confidence=1.0,
            )
        )
    try:
        result = replay_event_rule_definitions(
            [
                EventRuleDefinition(
                    rule_id="optical-rx-low-candidate",
                    version=1,
                    event_type="OpticalRxPowerLow",
                    severity="major",
                    condition={
                        "kind": "threshold_field_lte",
                        "entity_types": ["transceiver_diagnostic"],
                        "fields": ["channel_rx_power_dbm"],
                        "reference_field": "alarm_low_rx_power_dbm",
                    },
                )
            ],
            include_active_collisions=False,
            limit=100,
            run_ids=[run_id],
        )

        assert result["success"] is True
        assert result["matched_change_count"] == 1
        assert result["event_types"] == {"OpticalRxPowerLow": 1}
    finally:
        with get_db_session() as db:
            db.query(NetworkChangeRecord).filter(NetworkChangeRecord.id == change_id).delete()


def test_event_rule_replay_treats_same_rule_id_as_version_replacement(monkeypatch):
    change_id = str(uuid.uuid4())
    run_id = f"pytest-rule-replacement-{uuid.uuid4()}"
    with get_db_session() as db:
        db.add(
            NetworkChangeRecord(
                id=change_id,
                idempotency_key=hashlib.sha256(change_id.encode("utf-8")).hexdigest(),
                run_id=run_id,
                device_id="lab-device-1",
                canonical_command="inspect metric",
                entity_type="metric",
                entity_key="slot=1",
                field="value",
                old_value_json=80,
                new_value_json=95,
                change_type="modified",
                source="pytest",
                event_status="pending",
                detected_at=datetime.now(timezone.utc),
                confidence=1.0,
            )
        )
    monkeypatch.setattr(
        "src.core.patrol.rule_replay.EventRuleRepository.list_versions",
        lambda _self, **_kwargs: [
            {
                "rule_id": "metric-high-id",
                "rule_name": "metric-high",
                "version": 1,
                "event_type": "MetricHigh",
                "severity": "warning",
                "condition": {
                    "kind": "threshold",
                    "entity_types": ["metric"],
                    "fields": ["value"],
                    "gte": 90,
                },
                "event_class": "fault",
            }
        ],
    )
    try:
        result = replay_event_rule_definitions(
            [
                EventRuleDefinition(
                    rule_id="metric-high",
                    version=2,
                    event_type="MetricHigh",
                    severity="warning",
                    condition={
                        "kind": "threshold",
                        "entity_types": ["metric"],
                        "fields": ["value"],
                        "gte": 90,
                    },
                )
            ],
            include_active_collisions=True,
            limit=100,
            run_ids=[run_id],
        )

        assert result["success"] is True
        assert result["collision_count"] == 0
        assert result["replacement_rule_ids"] == ["metric-high"]
    finally:
        with get_db_session() as db:
            db.query(NetworkChangeRecord).filter(NetworkChangeRecord.id == change_id).delete()


def test_event_rule_replay_treats_renamed_semantic_slot_as_version_replacement(
    monkeypatch,
):
    change_id = str(uuid.uuid4())
    run_id = f"pytest-rule-logical-replacement-{uuid.uuid4()}"
    with get_db_session() as db:
        db.add(
            NetworkChangeRecord(
                id=change_id,
                idempotency_key=hashlib.sha256(change_id.encode("utf-8")).hexdigest(),
                run_id=run_id,
                device_id="lab-device-1",
                canonical_command="inspect metric",
                entity_type="metric",
                entity_key="slot=1",
                field="value",
                old_value_json=80,
                new_value_json=95,
                change_type="modified",
                source="pytest",
                event_status="pending",
                detected_at=datetime.now(timezone.utc),
                confidence=1.0,
            )
        )
    monkeypatch.setattr(
        "src.core.patrol.rule_replay.EventRuleRepository.list_versions",
        lambda _self, **_kwargs: [
            {
                "rule_id": "legacy-rule-id",
                "rule_name": "generic-metric.weak-model-high-label.v1",
                "version": 1,
                "event_type": "LegacyMetricAboveLimit",
                "severity": "warning",
                "condition": {
                    "kind": "threshold",
                    "entity_types": ["metric"],
                    "fields": ["value"],
                    "gte": 90,
                },
                "event_class": "fault",
            }
        ],
    )
    try:
        result = replay_event_rule_definitions(
            [
                EventRuleDefinition(
                    rule_id="generic-metric.high",
                    version=1,
                    event_type="MetricHighV2",
                    severity="major",
                    condition={
                        "kind": "threshold",
                        "entity_types": ["metric"],
                        "fields": ["value"],
                        "gte": 85,
                    },
                )
            ],
            include_active_collisions=True,
            limit=100,
            run_ids=[run_id],
        )

        assert result["success"] is True
        assert result["collision_count"] == 0
        assert result["replacement_rule_ids"] == ["generic-metric.weak-model-high-label.v1"]
        assert len(result["replacement_logical_keys"]) == 1
        assert result["replacement_logical_keys"] == result["candidate_logical_keys"]
    finally:
        with get_db_session() as db:
            db.query(NetworkChangeRecord).filter(NetworkChangeRecord.id == change_id).delete()
