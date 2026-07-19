from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.core.workflows import patrol_batch_service
from src.core.workflows.patrol_batch_service import (
    _serialize_event_occurrence,
    build_patrol_batch_summary,
    build_patrol_pipeline_summary,
)


def _step(index, name, skill, status, started, duration=None, error=None):
    return SimpleNamespace(
        step_index=index,
        step_name=name,
        skill_name=skill,
        status=status,
        started_at=started,
        completed_at=started + timedelta(seconds=duration) if duration is not None else None,
        error_message=error,
    )


def test_pipeline_summary_aggregates_parallel_device_runs_and_stage_latency():
    started = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    runs = [
        SimpleNamespace(id="run-1", context={"device_id": "d1", "device_name": "SW-1"}),
        SimpleNamespace(id="run-2", context={"device_id": "d2", "device_name": "SW-2"}),
    ]
    steps_by_run = {
        "run-1": [
            _step(0, "patrol", "device-patrol", "completed", started, 10),
            _step(1, "state_normalization", "state-normalizer", "completed", started, 2),
        ],
        "run-2": [
            _step(0, "patrol", "device-patrol", "completed", started, 20),
            _step(
                1,
                "state_normalization",
                "state-normalizer",
                "failed",
                started,
                3,
                "contract unavailable",
            ),
        ],
    }

    result = build_patrol_pipeline_summary(runs, steps_by_run)

    assert result["total_devices"] == 2
    assert result["bottleneck_stage_label"] == "命令采集与解析"
    assert result["stages"][0]["completed_count"] == 2
    assert result["stages"][0]["duration"] == {
        "sample_count": 2,
        "average_seconds": 15.0,
        "p95_seconds": 20.0,
        "max_seconds": 20.0,
    }
    assert result["stages"][1]["status"] == "degraded"
    assert result["stages"][1]["error_examples"][0]["device_name"] == "SW-2"


def test_empty_manifest_summary_is_not_reported_as_ready(monkeypatch):
    class EmptyQuery:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return []

    class FakeSession:
        def query(self, _model):
            return EmptyQuery()

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(patrol_batch_service, "get_db_session", lambda: FakeSessionContext())

    result = build_patrol_batch_summary("batch-empty", [])

    assert result["run_count"] == 0
    assert result["quality"]["status"] == "not_started"
    assert result["pipeline"]["stages"] == []


def test_pipeline_summary_keeps_same_named_steps_with_different_skills_separate():
    started = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    runs = [SimpleNamespace(id="run-1", context={"device_id": "d1"})]
    steps = {
        "run-1": [
            _step(1, "normalize", "state-normalizer-v1", "completed", started, 1),
            _step(1, "normalize", "state-normalizer-v2", "failed", started, 2),
        ]
    }

    result = build_patrol_pipeline_summary(runs, steps)

    assert [stage["key"] for stage in result["stages"]] == [
        "1:normalize:state-normalizer-v1",
        "1:normalize:state-normalizer-v2",
    ]
    assert result["stages"][0]["completed_count"] == 1
    assert result["stages"][1]["failed_count"] == 1


def test_event_occurrence_serialization_is_independent_of_mutable_aggregate_state():
    observed_at = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    occurrence = SimpleNamespace(
        id="occurrence-1",
        occurrence_kind="opening",
        source_change_id="change-1",
        severity="high",
        observed_at=observed_at,
        payload_json={
            "event_type": "InterfaceDown",
            "device_id": "device-1",
            "entity_type": "interface_l2",
            "entity_key": "GigabitEthernet1/0/1",
            "new": "down",
            "aggregate_status_after_occurrence": "open",
        },
    )
    aggregate = SimpleNamespace(
        id="event-1",
        event_type="InterfaceDown",
        device_id="device-1",
        entity_type="interface_l2",
        entity_key="GigabitEthernet1/0/1",
        status="resolved",
        severity="low",
        payload_json={"new": "up"},
    )

    result = _serialize_event_occurrence(occurrence, aggregate)

    assert result["occurrence_id"] == "occurrence-1"
    assert result["status"] == "open"
    assert result["severity"] == "high"
    assert result["current_value"] == "down"
    assert result["occurred_at"] == observed_at.isoformat()
