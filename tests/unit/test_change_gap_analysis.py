"""变化缺口分析真实断言单测（SQLite fixture）。

覆盖 no_event 分类逻辑、dataset_ids 自动回填与 seed_jobs 幂等。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.rule_control import change_gap_analysis
from src.core.rule_control.change_gap_analysis import ChangeGapAnalysisService
from src.infrastructure.db.models import Base, NetworkChangeRecord, PatrolRun


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


class _FakeEventRuleRepository:
    active_rules: list[dict[str, Any]] = []

    def snapshot_active_rules(self):
        return list(self.active_rules)


class _FakeJobRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.existing: dict[str, dict[str, Any]] = {}

    def find_job(self, *, job_type, source_ref, status_not_in=None):
        return self.existing.get(source_ref)

    def create_job(self, **kwargs):
        job = {"id": f"job-{len(self.created) + 1}", **kwargs}
        self.created.append(job)
        return job


def _change(index: int, **overrides) -> NetworkChangeRecord:
    values: dict[str, Any] = {
        "id": f"chg-{index}",
        "idempotency_key": f"chg-key-{index}",
        "run_id": f"run-{index}",
        "device_id": f"dev-{index}",
        "canonical_command": "display interface",
        "entity_type": "interface",
        "entity_key": f"GE1/0/{index}",
        "field": "crc_errors",
        "observation_type": "interface_error_metric",
        "change_type": "modified",
        "event_status": "no_event",
        "detected_at": datetime.now(timezone.utc),
        "dimensions_json": {"vendor": "huawei"},
    }
    values.update(overrides)
    return NetworkChangeRecord(**values)


def _fixture(monkeypatch, tmp_path, *, active_rules=None):
    engine = create_engine(f"sqlite:///{tmp_path / 'change-gap.db'}")
    Base.metadata.create_all(
        engine,
        tables=[NetworkChangeRecord.__table__, PatrolRun.__table__],
    )
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(
        change_gap_analysis,
        "get_db_session",
        lambda: _session_scope(session_factory),
    )
    _FakeEventRuleRepository.active_rules = list(active_rules or [])
    monkeypatch.setattr(
        change_gap_analysis,
        "EventRuleRepository",
        _FakeEventRuleRepository,
    )
    fake_jobs = _FakeJobRepository()
    monkeypatch.setattr(
        change_gap_analysis,
        "RuleGenerationJobRepository",
        lambda: fake_jobs,
    )
    return session_factory, fake_jobs


def _add_run(db, run_id: str, dataset_id: str | None) -> None:
    db.add(
        PatrolRun(
            id=run_id,
            mode="offline",
            source="test",
            dataset_id=dataset_id,
            logical_run_id=run_id,
        )
    )


def test_analyze_classifies_missing_event_rules_when_no_active_rules(
    monkeypatch, tmp_path
) -> None:
    session_factory, _ = _fixture(monkeypatch, tmp_path, active_rules=[])
    with _session_scope(session_factory) as db:
        db.add_all([_change(1), _change(2)])
    result = ChangeGapAnalysisService().analyze(days=30)
    assert result["change_count"] == 2
    assert result["by_reason"] == {"no_active_event_rules": 2}
    gap = result["gaps"][0]
    assert gap["observation_type"] == "interface_error_metric"
    assert gap["gap_kind"] == "missing_event_rules"
    assert gap["suggested_job_type"] == "EVENT_RULE"


def test_analyze_classifies_policy_gap_for_threshold_unknown(monkeypatch, tmp_path) -> None:
    session_factory, _ = _fixture(
        monkeypatch, tmp_path, active_rules=[{"rule_id": "r1"}]
    )
    with _session_scope(session_factory) as db:
        db.add_all(
            [
                _change(1, decision_reason="threshold_unknown"),
                _change(2, decision_reason="threshold_unknown"),
            ]
        )
    result = ChangeGapAnalysisService().analyze(days=30)
    gap = result["gaps"][0]
    assert gap["gap_kind"] == "policy_or_threshold"
    assert gap["suggested_job_type"] == "CHANGE_POLICY"


def test_analyze_backfills_dataset_ids_from_change_runs(monkeypatch, tmp_path) -> None:
    session_factory, _ = _fixture(monkeypatch, tmp_path, active_rules=[])
    with _session_scope(session_factory) as db:
        _add_run(db, "run-1", "dataset-a")
        _add_run(db, "run-2", "dataset-b")
        _add_run(db, "run-3", None)
        db.add_all(
            [
                _change(1, run_id="run-1"),
                _change(2, run_id="run-2"),
                _change(3, run_id="run-3"),
            ]
        )
    result = ChangeGapAnalysisService().analyze(days=30)
    gap = result["gaps"][0]
    assert sorted(gap["dataset_ids"]) == ["dataset-a", "dataset-b"]


def test_seed_jobs_creates_job_with_candidate_datasets_and_skips_existing(
    monkeypatch, tmp_path
) -> None:
    session_factory, fake_jobs = _fixture(monkeypatch, tmp_path, active_rules=[])
    with _session_scope(session_factory) as db:
        _add_run(db, "run-1", "dataset-a")
        db.add_all([_change(1, run_id="run-1"), _change(2, run_id="run-1")])
    service = ChangeGapAnalysisService()

    result = service.seed_jobs(days=30, limit=5)
    assert result["created_count"] == 1
    job = fake_jobs.created[0]
    assert job["job_type"] == "EVENT_RULE"
    assert job["source_ref"] == "change_gap:EVENT_RULE:interface_error_metric"
    assert job["payload"]["candidate_dataset_ids"] == ["dataset-a"]
    assert job["payload"]["missing_context"] == ["records", "historical_values"]
    assert job["validation_report"]["ready_for_generation"] is False

    # 已存在同 source_ref 的 Job 时应跳过而不是重复创建
    fake_jobs.existing[job["source_ref"]] = {"id": job["id"]}
    result = service.seed_jobs(days=30, limit=5)
    assert result["created_count"] == 0
    assert result["skipped_count"] == 1
