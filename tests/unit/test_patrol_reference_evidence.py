from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.apps.inspection.backend.patrol import reference_evidence as reader
from src.infrastructure.db.models import (
    CommandSnapshot as Snapshot,
)
from src.infrastructure.db.models import (
    NetworkChangeRecord as Change,
)
from src.infrastructure.db.models import (
    NetworkEventOccurrenceRecord as Occurrence,
)
from src.infrastructure.db.models import (
    OfflinePatrolJobRecord as Job,
)
from src.infrastructure.db.models import (
    OfflinePatrolPeriodRecord as Period,
)
from src.infrastructure.db.models import (
    PatrolAnalysisAttemptRecord as Attempt,
)
from src.infrastructure.db.models import (
    StateObservationRecord as Observation,
)
from src.platform.evidence import offline_jobs


@pytest.fixture
def evidence_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'evidence.db'}")
    for model in (Job, Period, Snapshot, Change, Observation, Attempt, Occurrence):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def session():
        with factory.begin() as db:
            yield db

    monkeypatch.setattr(reader, "get_db_session", session)
    monkeypatch.setattr(offline_jobs, "get_db_session", session)
    now = datetime.now(timezone.utc)
    with session() as db:
        db.add(
            Job(
                id="job",
                idempotency_key="job",
                purpose="offline_patrol",
                job_type="history_compare",
                name="sample",
                owner_id="owner",
                status="completed",
            )
        )
        for role in ("current", "baseline"):
            db.add(
                Period(
                    id=role,
                    job_id="job",
                    role=role,
                    label=role,
                    observed_at=now,
                    dataset_id=role,
                    patrol_run_id=role,
                )
            )
            db.add(
                Snapshot(
                    id=role,
                    run_id=role,
                    device_id="device",
                    device_name="TEST",
                    execution_item_id=role,
                    command="display arp",
                    canonical_command="display arp",
                    observed_at=now,
                    raw_text=f"IP MAC\n192.168.1.1 MAC-{role}\n",
                    raw_text_hash=role,
                    parse_status="parsed",
                    structured_json={"large": "payload"},
                )
            )
        db.add(
            Change(
                id="change",
                idempotency_key="change",
                run_id="current",
                device_id="device",
                canonical_command="display arp",
                entity_type="arp",
                entity_key='["192.168.1.1"]',
                field="MAC",
                change_type="changed",
                previous_snapshot_id="baseline",
                current_snapshot_id="current",
                detected_at=now,
            )
        )
    queries = []
    event.listen(
        engine, "before_cursor_execute", lambda conn, cursor, sql, *args: queries.append(sql)
    )
    yield session, queries
    engine.dispose()


def load():
    return reader.reference_evidence("job", "change", owner_id="owner")


def test_no_event_changes_have_both_raw_sides_without_reading_whole_report(evidence_db):
    _, queries = evidence_db
    result = load()
    entry = result["items"][0]
    assert entry["full_source"]["baseline"].endswith("MAC-baseline\n")
    assert entry["full_source"]["current"].endswith("MAC-current\n")
    assert entry["rows"] and entry["lookup_reference"] == "change"
    assert "不代表故障" in entry["note"]
    assert not any("anomaly_span" in (row.get("current") or {}) for row in entry["rows"])
    assert not any(
        "structured_json" in q or "result_json" in q or "rule_bundle_json" in q for q in queries
    )
    assert not any("FROM netops_state_observations" in q for q in queries)


def test_unauthorized_user_is_rejected_before_evidence_queries(evidence_db):
    _, queries = evidence_db
    with pytest.raises(PermissionError):
        reader.reference_evidence("job", "change", owner_id="other")
    assert len(queries) == 1


@pytest.mark.parametrize(
    "field,value",
    [("run_id", "outside"), ("device_id", "other"), ("canonical_command", "display version")],
)
def test_wrong_snapshot_scope_is_rejected(evidence_db, field, value):
    session, _ = evidence_db
    with session() as db:
        setattr(db.get(Snapshot, "baseline"), field, value)
    with pytest.raises(ValueError, match="周期不一致"):
        load()


def test_change_from_other_job_is_not_accepted(evidence_db):
    session, _ = evidence_db
    with session() as db:
        db.get(Change, "change").run_id = "outside"
    with pytest.raises(KeyError):
        load()


def test_added_object_can_read_unique_baseline_command_and_digest_changes(evidence_db):
    session, _ = evidence_db
    before = load()["source_digest"]
    with session() as db:
        db.get(Change, "change").previous_snapshot_id = None
    assert load()["items"][0]["baseline"]["snapshot_id"] == "baseline"
    with session() as db:
        db.get(Snapshot, "current").raw_text += "another line\n"
    assert load()["source_digest"] != before


def test_missing_baseline_does_not_invent_a_comparison(evidence_db):
    session, _ = evidence_db
    with session() as db:
        db.delete(db.get(Snapshot, "baseline"))
    result = load()["items"][0]
    assert result["full_source"]["baseline"] is None
    assert result["excerpt_comparison"] == "unavailable"
