from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.apps.rule_governance.backend import access, source_tasks
from src.core.patrol import offline_jobs
from src.infrastructure.db.models import OfflinePatrolJobRecord, OfflinePatrolPeriodRecord


@pytest.fixture
def source(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'source.db'}")
    for model in (OfflinePatrolJobRecord, OfflinePatrolPeriodRecord):
        model.__table__.create(engine)

    @contextmanager
    def scope():
        with Session(engine) as db, db.begin():
            yield db

    with scope() as db:
        db.add(
            OfflinePatrolJobRecord(
                id="source",
                idempotency_key="source",
                name="evidence",
                owner_id="owner",
                purpose="rule_asset_source",
                job_type="history_compare",
                status="queued",
                execution_idempotency_key="execution",
                celery_task_id="preview",
            )
        )
        for role in ("baseline", "current"):
            db.add(
                OfflinePatrolPeriodRecord(
                    id=role,
                    job_id="source",
                    role=role,
                    label=role,
                    dataset_id=role,
                    observed_at=datetime.now(timezone.utc),
                )
            )
    monkeypatch.setattr(source_tasks, "get_db_session", scope)
    monkeypatch.setattr(offline_jobs, "get_db_session", scope)
    monkeypatch.setattr(access, "execution_lock", lambda **kw: nullcontext())
    monkeypatch.setattr(access, "require_running", lambda: None)
    monkeypatch.setattr(source_tasks, "execution_lock", lambda **kw: nullcontext())
    monkeypatch.setattr(source_tasks, "require_running", lambda: None)
    authorize = Mock()
    monkeypatch.setattr(source_tasks, "authorize", authorize)
    monkeypatch.setattr(access, "authorize", authorize)
    yield scope, authorize
    engine.dispose()


def test_fixed_source_import_claims_once_and_imports_all_periods(source, monkeypatch):
    scope, authorize = source
    prepared = Mock(return_value={"status": "completed", "sample_count": 4})
    monkeypatch.setattr(source_tasks, "prepare_source_samples", prepared)
    imported = Mock(return_value={"success": True, "snapshot_count": 2})
    monkeypatch.setattr(offline_jobs.OfflinePatrolJobService, "import_period", imported)
    assert source_tasks.import_source.run("source", "execution")["success"]
    assert {c.args[1] for c in imported.call_args_list} == {"baseline", "current"}
    assert all(
        c.kwargs["rule_source_execution_key"] == "execution" for c in imported.call_args_list
    )
    assert source_tasks.import_source.run("source", "execution")["ignored"]
    assert imported.call_count == 2
    prepared.assert_called_once_with("source", "execution")
    authorize.assert_called_with("owner")
    with scope() as db:
        job = db.get(OfflinePatrolJobRecord, "source")
        assert job.status == "completed"
        assert job.workflow_run_id is None
        assert job.result_json["snapshot_count"] == 4


def test_revoked_source_does_not_import_evidence(source, monkeypatch):
    scope, authorize = source
    authorize.side_effect = ValueError("revoked")
    imported = Mock()
    monkeypatch.setattr(offline_jobs.OfflinePatrolJobService, "import_period", imported)
    assert not source_tasks.import_source.run("source", "execution")["success"]
    imported.assert_not_called()
    with scope() as db:
        assert db.get(OfflinePatrolJobRecord, "source").status == "failed"


def test_generic_ingestor_and_stale_preview_cannot_enter_rule_source(source):
    scope, _ = source
    service = offline_jobs.OfflinePatrolJobService()
    with pytest.raises(ValueError, match="规则治理"):
        service.import_period("source", "baseline")
    assert service.run_preview("source")["ignored"]
    with scope() as db:
        db.get(OfflinePatrolJobRecord, "source").status = "failed"
    assert service.run_preview("source", rule_source_task_id="preview")["ignored"]
    assert source_tasks.import_source.run("source", "execution")["ignored"]


def test_unknown_legacy_identity_is_denied_without_authority_request(monkeypatch):
    call = Mock()
    monkeypatch.setattr(access, "authorize_worker", call)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        access.authorize(None)
    assert exc.value.status_code == 403
    call.assert_not_called()


def test_recovery_retains_interrupted_evidence_without_reimport(source, monkeypatch):
    from datetime import timedelta

    scope, _ = source
    with scope() as db:
        row = db.get(OfflinePatrolJobRecord, "source")
        row.status = "running"
        row.updated_at = datetime.now(timezone.utc) - timedelta(minutes=36)
        row.result_json = {"partial": "retained"}
    dispatch = Mock()
    monkeypatch.setattr(source_tasks, "dispatch", dispatch)
    assert source_tasks.recover_sources.run()["interrupted"] == ["source"]
    dispatch.assert_not_called()
    with scope() as db:
        row = db.get(OfflinePatrolJobRecord, "source")
        assert row.status == "failed" and row.result_json == {"partial": "retained"}


def test_parser_persistence_rechecks_current_owner_before_writing(monkeypatch):
    from src.apps.rule_governance.backend.parser_generation.publishing import persist_generation

    jobs, templates = Mock(), Mock()
    jobs.get_job.return_value = {
        "id": "parser",
        "agent_state_json": {"execution_identity": {"user_id": "revoked"}},
    }
    authorize = Mock(side_effect=ValueError("revoked"))
    monkeypatch.setattr(access, "authorize", authorize)
    with pytest.raises(ValueError, match="revoked"):
        persist_generation(
            job={"id": "parser"},
            spec=None,
            sample_set=None,
            outcome=None,
            publish=True,
            dry_run=False,
            job_repository=jobs,
            template_repository=templates,
        )
    authorize.assert_called_once_with("revoked")
    assert templates.mock_calls == []
