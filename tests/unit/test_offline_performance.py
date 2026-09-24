"""Regression checks for bounded job payloads and honest execution counts."""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from scripts.maintenance.compact_offline_job_results import compact_result
from src.apps.inspection.backend import offline_tasks
from src.infrastructure.db.models import OfflinePatrolJobRecord as Job
from src.platform.evidence import offline_jobs


def test_checkpoint_result_does_not_duplicate_persisted_evidence():
    result = {"success": True, "observation_count": 100000, "partial": True,
              "failed_snapshot_count": 3, "results": [{"observations": ["large"]}],
              "changes": ["large"], "events": ["large"]}
    summary = offline_tasks._require_success(result, "normalize")
    assert summary == {"success": True, "observation_count": 100000, "partial": True,
                       "failed_snapshot_count": 3}
    assert "results" in result
    migrated = compact_result({"success": True, "steps": {"current_normalization": result}})
    assert migrated["steps"]["current_normalization"] == summary
    assert compact_result(migrated) == migrated
    with pytest.raises(ValueError, match="normalize: broken"):
        offline_tasks._require_success({"success": False, "error": "broken"}, "normalize")


@pytest.mark.parametrize("status,bucket", [
    ("completed", "completed_count"), ("failed", "failed_count"),
    ("cancelled", "cancelled_count"), ("running", "active_count"),
    ("queued", "active_count"), ("draft", "skipped_count"),
])
def test_execution_counts_are_disjoint(status, bucket):
    counts = offline_jobs._execution_counts(status, 20)
    assert counts[bucket] == 20
    assert sum(counts[k] for k in (
        "completed_count", "failed_count", "skipped_count", "cancelled_count", "active_count"
    )) == 20


def test_access_check_does_not_select_results_or_preview(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'access.db'}")
    Job.__table__.create(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def session():
        with factory.begin() as db:
            yield db

    monkeypatch.setattr(offline_jobs, "get_db_session", session)
    with session() as db:
        db.add(Job(id="j", idempotency_key="j", purpose="offline_patrol",
                   job_type="single_patrol", name="sample", owner_id="owner"))
    queries = []
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, statement, *args:
                 queries.append(statement))
    service = offline_jobs.OfflinePatrolJobService()
    service.check_access("j", owner_id="owner", purpose="offline_patrol")
    with pytest.raises(PermissionError):
        service.check_access("j", owner_id="other")
    with pytest.raises(KeyError):
        service.check_access("j", owner_id="owner", purpose="rule_asset_source")
    with pytest.raises(KeyError):
        service.check_access("missing", owner_id="owner")
    assert not any("result_json" in q or "preview_json" in q for q in queries)
    engine.dispose()


def test_domain_resolution_scales_with_commands_not_observation_count(monkeypatch):
    from src.apps.inspection.backend import patrol_batch_service as module

    calls = []
    original = module.report_domain

    def resolve(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(module, "report_domain", resolve)
    module.PatrolBatchService.domain_checks(
        observations=[{"entity_type": "interface", "entity_key": str(i), "field": "status",
                       "command": "show interface", "value": "up"} for i in range(1000)],
        command_details=[], changes=[], events=[],
    )
    assert len(calls) == 1


def test_offline_normalization_flushes_each_command_before_reading_next(monkeypatch):
    from unittest.mock import Mock

    from src.core.patrol import repository
    from src.core.state import normalizer

    saved = []

    class StateStore:
        def save_observations(self, observations, *, update_current_state):
            assert update_current_state is False
            saved.extend(observations)
            return len(observations), 0

    def build(store, snapshot_id, **kwargs):
        index = int(snapshot_id)
        assert len(saved) == index
        return ({"success": True, "snapshot_id": snapshot_id, "observation_count": 1,
                 "normalization_status": "normalized", "fact_status": "ready",
                 "observations": [{"large": "duplicated data"}]}, [index], False)

    monkeypatch.setattr(normalizer, "StateRepository", StateStore)
    monkeypatch.setattr(repository, "PatrolRepository", Mock)
    monkeypatch.setattr(normalizer, "_build_snapshot_normalization", build)
    result = normalizer._normalize_run_impl(
        "run", snapshot_ids=[str(i) for i in range(100)], update_current_state=False,
        device_ids=None, update_run_status=False, include_observations=False,
    )
    assert result["created_count"] == result["observation_count"] == 100
    assert result["failed_snapshot_count"] == 0
    assert all("observations" not in r for r in result["results"])
