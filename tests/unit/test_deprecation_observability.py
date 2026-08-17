from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scripts.deprecations.export_runtime_evidence import build_report
from src.infrastructure.db.models import LegacyPathObservationRecord
from src.observability.deprecations import (
    LegacyPathObservationRepository,
    latest_legacy_path_observation,
    observe_legacy_path,
)


def test_legacy_path_observation_records_only_governance_labels():
    candidate_id = "legacy-test-path"
    operation = "read"
    before = latest_legacy_path_observation(candidate_id, operation)

    observe_legacy_path(candidate_id, operation, persist=False)

    observed = latest_legacy_path_observation(candidate_id, operation)
    assert observed is not None
    assert observed["candidate_id"] == candidate_id
    assert observed["operation"] == operation
    assert observed["outcome"] == "used"
    assert observed["process_count"] == int((before or {}).get("process_count") or 0) + 1
    assert set(observed) == {
        "candidate_id",
        "operation",
        "outcome",
        "process_count",
        "last_observed_at",
    }


def test_legacy_path_observation_rejects_empty_metric_labels():
    try:
        observe_legacy_path("", "read", persist=False)
    except ValueError as exc:
        assert "labels must be non-empty" in str(exc)
    else:
        raise AssertionError("empty labels must be rejected")


def test_legacy_path_observation_is_durable_and_window_counted():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    LegacyPathObservationRecord.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    @contextmanager
    def session_scope():
        session = session_factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    repository = LegacyPathObservationRepository(session_scope=session_scope)
    now = datetime.now(timezone.utc)
    repository.record(
        candidate_id="legacy-test-path",
        operation="read",
        outcome="used",
        observed_at=now,
    )

    assert repository.count_window(
        candidate_id="legacy-test-path",
        started_at=now - timedelta(seconds=1),
        ended_at=now + timedelta(seconds=1),
    ) == 1
    assert repository.count_window(
        candidate_id="another-path",
        started_at=now - timedelta(seconds=1),
        ended_at=now + timedelta(seconds=1),
    ) == 0


def test_runtime_evidence_export_reports_two_distinct_release_cycles():
    class Repository:
        def count_window(self, **kwargs):
            return 1 if kwargs["started_at"].month == 8 else 0

    report = build_report(
        candidate={
            "id": "legacy-test-path",
            "telemetry_metric": "netops_legacy_path_call_count",
        },
        windows=[
            {
                "release_cycle": "2026.08",
                "started_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
                "ended_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
            },
            {
                "release_cycle": "2026.09",
                "started_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
                "ended_at": datetime(2026, 9, 30, tzinfo=timezone.utc),
            },
        ],
        repository=Repository(),
        generated_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )

    assert [item["call_count"] for item in report["windows"]] == [1, 0]
    assert report["runtime_traffic_zero"] is False


def test_every_registered_legacy_candidate_has_an_observation_call_site():
    root = Path(__file__).resolve().parents[2]
    registry = yaml.safe_load(
        (root / "docs" / "implementation" / "deprecations.yaml").read_text(
            encoding="utf-8"
        )
    )

    for candidate in registry["candidates"]:
        source = "\n".join(
            (root / path).read_text(encoding="utf-8")
            for path in candidate["code_refs"]
        )
        assert f'"{candidate["id"]}"' in source
