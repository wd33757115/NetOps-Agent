from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.parser import repository as parser_repository
from src.core.parser.repository import ParserJobRepository, ParserJobStateError
from src.core.parser_generation.publishing import RepositoryModelCallController
from src.core.parser_generation.service import ModelCallCompletion
from src.infrastructure.db.models import (
    AuditLogRecord,
    Base,
    ParserJobSample,
    ParserModelCallLedger,
    ParserTemplate,
    ParserTemplateJob,
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


@pytest.fixture
def sqlite_repository(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'parser-job-recovery.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            ParserTemplateJob.__table__,
            ParserJobSample.__table__,
            ParserModelCallLedger.__table__,
            ParserTemplate.__table__,
            AuditLogRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(
        parser_repository,
        "get_db_session",
        lambda: _session_scope(session_factory),
    )
    # SQLite 没有 localtimestamp()，用本地时钟替代 PostgreSQL 双时钟。
    monkeypatch.setattr(
        parser_repository,
        "database_clocks",
        lambda db: (datetime.now(timezone.utc), datetime.now()),
    )
    return ParserJobRepository(), session_factory


def _insert_job(session_factory, *, status: str, started_minutes_ago: int | None):
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    started_at = (
        datetime.now() - timedelta(minutes=started_minutes_ago)
        if started_minutes_ago is not None
        else None
    )
    with _session_scope(session_factory) as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                vendor="H3C",
                profile="S5130",
                canonical_command="display interface brief",
                output_signature="sig",
                idempotency_key=uuid.uuid4().hex,
                status=status,
                pipeline_version="simple_v2",
                celery_task_id="celery-task-1",
                started_at=started_at,
                dispatched_at=started_at,
                updated_at=started_at or datetime.now(),
            )
        )
    return job_id


def test_recover_marks_stale_running_job_failed_and_allows_retry(sqlite_repository):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="running", started_minutes_ago=31)

    item = repository.recover_stale_job(
        job_id,
        actor="operator",
        reason="worker crashed; recover the stuck generation",
    )

    assert item["status"] == "failed"
    assert item["failure_code"] == "stale_running_recovered"
    assert item["celery_task_id"] is None
    assert item["recovered_celery_task_id"] == "celery-task-1"
    with _session_scope(session_factory) as db:
        audit = db.query(AuditLogRecord).one()
        assert audit.action == "parser_job_stale_recovered"
        assert audit.username == "operator"
        assert audit.detail["previous_status"] == "running"
        assert audit.detail["stale_celery_task_id"] == "celery-task-1"

    retried = repository.retry_job(job_id)
    assert retried["status"] == "pending"
    assert retried["last_error"] is None


def test_retry_preserves_adopted_semantic_plan_and_clears_run_state(sqlite_repository):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="failed", started_minutes_ago=31)
    semantic_suggestion = {
        "status": "ready",
        "result": {
            "parser_spec": {
                "entity": "interface",
                "primary_keys": ["interface"],
                "fields": {"interface": {"required": True, "type": "interface"}},
            },
            "sample_suggestions": [
                {
                    "sample_id": "sample-1",
                    "semantic_plan": {"record_unit": "one interface row"},
                }
            ],
        },
    }
    with _session_scope(session_factory) as db:
        job = db.get(ParserTemplateJob, job_id)
        job.agent_state_json = {
            "parser_spec_suggestion": semantic_suggestion,
            "generation_run_id": "old-run",
            "last_diagnostic": "old failure",
        }
        job.agent_budget_json = {"model_calls_used": 2}
        job.model_calls_used = 2

    retried = repository.retry_job(job_id)

    assert retried["status"] == "pending"
    assert retried["agent_state_json"] == {
        "parser_spec_suggestion": semantic_suggestion,
        "execution_identity": {"user_id": None, "display": None, "task_id": None},
    }
    assert retried["agent_budget_json"] is None
    assert retried["model_calls_used"] == 0


def test_dispatch_does_not_overwrite_an_existing_task_owner(sqlite_repository):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="pending", started_minutes_ago=None)

    with pytest.raises(ParserJobStateError, match="already dispatched"):
        repository.mark_job_dispatched(job_id, "celery-task-duplicate")


def test_running_job_can_only_be_claimed_by_its_pending_task_owner(sqlite_repository):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="pending", started_minutes_ago=None)

    with pytest.raises(ParserJobStateError, match="different Celery task"):
        repository.transition_job_status(
            job_id,
            "running",
            expected_status="pending",
            expected_celery_task_id="celery-task-duplicate",
        )

    claimed = repository.transition_job_status(
        job_id,
        "running",
        expected_status="pending",
        expected_celery_task_id="celery-task-1",
    )
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1

    with pytest.raises(ParserJobStateError, match="expected pending, found running"):
        repository.transition_job_status(
            job_id,
            "running",
            expected_status="pending",
            expected_celery_task_id="celery-task-1",
        )


def test_explicit_retry_resets_run_budget_but_preserves_global_call_history(
    sqlite_repository,
):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="failed", started_minutes_ago=31)

    first = repository.reserve_model_call(
        job_id,
        generation_run_id="generation-run-1",
        max_calls=3,
    )
    assert first["call_number"] == 1
    assert first["run_call_number"] == 1

    repository.retry_job(job_id)
    second = repository.reserve_model_call(
        job_id,
        generation_run_id="generation-run-2",
        max_calls=3,
    )

    assert second["call_number"] == 2
    assert second["run_call_number"] == 1
    with _session_scope(session_factory) as db:
        rows = (
            db.query(ParserModelCallLedger)
            .filter(ParserModelCallLedger.job_id == job_id)
            .order_by(ParserModelCallLedger.call_number)
            .all()
        )
        assert [row.generation_run_id for row in rows] == [
            "generation-run-1",
            "generation-run-2",
        ]
        assert rows[0].status == "provider_failed"
        assert rows[0].result_json["aborted_by_job_terminal"] is True


def test_generation_progress_heartbeats_and_terminal_transition_close_reservations(
    sqlite_repository,
):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="running", started_minutes_ago=1)
    reservation = repository.reserve_model_call(
        job_id,
        generation_run_id="generation-run-1",
        max_calls=3,
    )

    progress = repository.update_generation_progress(
        job_id,
        generation_run_id="generation-run-1",
        stage="model_generating",
        attempt_number=1,
        deadline_at="2026-08-02T10:30:00+00:00",
        request_deadline_at="2026-08-02T10:08:00+00:00",
        details={"model_calls_used": 1, "model_call_limit": 3},
    )

    assert progress["agent_state_json"]["generation_progress"]["stage"] == "model_generating"
    assert progress["agent_state_json"]["generation_progress"]["heartbeat_at"]
    terminal = repository.transition_job_status(
        job_id,
        "failed",
        "provider request exceeded its deadline",
        failure_code="model_timeout",
    )
    assert terminal["agent_state_json"]["generation_progress"]["stage"] == "failed"
    assert terminal["agent_state_json"]["generation_progress"]["terminal_reason"] == "model_timeout"
    with _session_scope(session_factory) as db:
        call = db.get(ParserModelCallLedger, reservation["id"])
        assert call.status == "provider_failed"
        assert call.failure_signature == "model_timeout"
        assert call.completed_at is not None


def test_model_heartbeat_does_not_regress_replay_stage_or_restore_request_deadline(
    sqlite_repository,
):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="running", started_minutes_ago=1)
    repository.update_generation_progress(
        job_id,
        generation_run_id="generation-run-1",
        stage="model_generating",
        attempt_number=1,
        request_deadline_at="2026-08-02T10:08:00+00:00",
        details={"model_calls_used": 1},
    )
    repository.update_generation_progress(
        job_id,
        generation_run_id="generation-run-1",
        stage="sample_replaying",
        attempt_number=1,
        details={"candidate_sha256": "a" * 64},
    )

    heartbeat = repository.update_generation_progress(
        job_id,
        generation_run_id="generation-run-1",
        stage="model_generating",
        attempt_number=1,
        request_deadline_at="2026-08-02T10:08:00+00:00",
        details={"heartbeat_only": True},
    )
    progress = heartbeat["agent_state_json"]["generation_progress"]

    assert progress["stage"] == "sample_replaying"
    assert progress["request_deadline_at"] is None
    assert progress["details"]["candidate_sha256"] == "a" * 64
    assert progress["details"]["heartbeat_only"] is True

    resumed = repository.update_generation_progress(
        job_id,
        generation_run_id="generation-run-1",
        stage="semantic_reviewing",
        attempt_number=1,
        details={"reviewer": "independent"},
    )
    resumed_progress = resumed["agent_state_json"]["generation_progress"]

    assert resumed_progress["stage"] == "semantic_reviewing"
    assert resumed_progress["details"]["reviewer"] == "independent"
    assert "heartbeat_only" not in resumed_progress["details"]


def test_model_call_heartbeat_keeps_the_original_request_deadline():
    class RecordingRepository:
        def __init__(self):
            self.progress_updates = []

        def reserve_model_call(self, *_args, **_kwargs):
            return {
                "id": "model-call-1",
                "call_number": 1,
                "run_call_number": 1,
                "generation_run_id": "generation-run-1",
            }

        def update_generation_progress(self, *_args, **kwargs):
            self.progress_updates.append(kwargs)

        def complete_model_call(self, *_args, **_kwargs):
            return None

    repository = RecordingRepository()
    controller = RepositoryModelCallController(
        job_id="parser-job-1",
        repository=repository,  # type: ignore[arg-type]
        heartbeat_seconds=3600,
        request_timeout_seconds=60,
    )

    reservation = controller.reserve("generation-run-1")
    controller._progress(reservation, heartbeat_only=True)
    controller.complete(reservation, ModelCallCompletion(status="succeeded"))

    deadlines = [
        update["request_deadline_at"] for update in repository.progress_updates
    ]
    assert len(deadlines) == 2
    assert deadlines[0] == deadlines[1]


def test_semantic_review_failure_is_persisted_as_model_call_outcome(
    sqlite_repository,
):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="running", started_minutes_ago=1)
    reservation = repository.reserve_model_call(
        job_id,
        generation_run_id="generation-run-1",
        max_calls=3,
    )
    semantic_review = {
        "passed": False,
        "summary": "The candidate omitted wrapped active VLAN values.",
        "diagnostics": [
            {
                "code": "truncated_multiline_value",
                "target": "record_boundary",
            }
        ],
    }

    completed = repository.complete_model_call(
        reservation["id"],
        status="semantic_review_failed",
        model_name="deepseek-v4-pro",
        candidate_sha256="a" * 64,
        failure_signature="truncated_multiline_value",
        result={"semantic_review": semantic_review},
    )

    assert completed["status"] == "semantic_review_failed"
    assert completed["failure_signature"] == "truncated_multiline_value"
    assert completed["result_json"]["semantic_review"] == semantic_review
    assert completed["completed_at"] is not None


def test_retryable_provider_failure_releases_run_budget_but_keeps_ledger(
    sqlite_repository,
):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="running", started_minutes_ago=1)
    first = repository.reserve_model_call(
        job_id,
        generation_run_id="generation-run-1",
        max_calls=3,
    )
    repository.complete_model_call(
        first["id"],
        status="provider_failed",
        error="provider timeout",
        result={"retryable": True},
    )

    released = repository.release_model_call_budget(first["id"])
    second = repository.reserve_model_call(
        job_id,
        generation_run_id="generation-run-1",
        max_calls=3,
    )

    assert released["model_calls_used"] == 0
    assert released["result_json"]["budget_released"] is True
    assert second["run_call_number"] == 1
    assert second["call_number"] == 2
    with _session_scope(session_factory) as db:
        first_row = db.get(ParserModelCallLedger, first["id"])
        assert first_row.status == "provider_failed"
        assert first_row.result_json["budget_released"] is True


def test_recover_rejects_fresh_running_job(sqlite_repository):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="running", started_minutes_ago=1)

    with pytest.raises(ParserJobStateError, match="still running"):
        repository.recover_stale_job(job_id, actor="operator", reason="early click")

    with _session_scope(session_factory) as db:
        job = db.get(ParserTemplateJob, job_id)
        assert job.status == "running"
        assert job.celery_task_id == "celery-task-1"


def test_recover_rejects_terminal_job(sqlite_repository):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="failed", started_minutes_ago=90)

    with pytest.raises(ParserJobStateError, match="does not need recovery"):
        repository.recover_stale_job(job_id, actor="operator", reason="noop")


def test_durable_candidate_reconciles_stranded_running_job(sqlite_repository):
    repository, session_factory = sqlite_repository
    job_id = _insert_job(session_factory, status="running", started_minutes_ago=5)
    attempt_id = f"attempt-{uuid.uuid4().hex[:12]}"
    with _session_scope(session_factory) as db:
        job = db.get(ParserTemplateJob, job_id)
        job.best_attempt_id = attempt_id
        db.add(
            ParserTemplate(
                id=f"template-{uuid.uuid4().hex[:12]}",
                vendor="H3C",
                model="S5130",
                profile="S5130",
                canonical_command="display interface brief",
                category="interface",
                version=f"simple-{attempt_id}",
                asset_path=f"postgresql://parser-template/{uuid.uuid4().hex}",
                template_text="Value interface (\\S+)\\n\\nStart\\n ^${interface} -> Record\\n",
                sha256="a" * 64,
                status="pending_approval",
                source_job_id=job_id,
                validation_json={
                    "simple_parser": {
                        "status": "candidate_ready",
                        "best_attempt_id": attempt_id,
                    },
                    "regression_validation": {"passed": True},
                },
                created_at=datetime.now() - timedelta(minutes=2),
            )
        )

    assert repository.finalize_durable_successes(grace_seconds=60) == [job_id]
    lifecycle = repository.get_job_lifecycle(job_id)
    assert lifecycle is not None
    assert lifecycle["status"] == "succeeded"
    assert lifecycle["last_error"] is None
    with _session_scope(session_factory) as db:
        audit = (
            db.query(AuditLogRecord)
            .filter(AuditLogRecord.action == "parser_job_durable_success_reconciled")
            .one()
        )
        assert audit.resource_id == job_id
        assert audit.detail["best_attempt_id"] == attempt_id


def _insert_suggestion_job(session_factory, *, suggestion_minutes_ago: int):
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    requested_at = (
        datetime.now(timezone.utc) - timedelta(minutes=suggestion_minutes_ago)
    ).isoformat()
    with _session_scope(session_factory) as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                vendor="H3C",
                profile="S5130",
                canonical_command="display interface brief",
                output_signature="sig",
                idempotency_key=uuid.uuid4().hex,
                status="failed",
                pipeline_version="simple_v2",
                agent_state_json={
                    "parser_spec_suggestion": {
                        "status": "running",
                        "task_id": "old-task",
                        "requested_at": requested_at,
                        "updated_at": requested_at,
                    }
                },
            )
        )
        db.add(
            ParserJobSample(
                id=f"sample-{uuid.uuid4().hex[:12]}",
                job_id=job_id,
                snapshot_id="snapshot-1",
                device_id="SW1",
                output_signature="sig",
                sanitization_status="sanitized",
                sanitized_text="Interface Link Protocol\nGE1/0/1 UP UP\n",
            )
        )
    return job_id


def test_stale_spec_suggestion_can_be_requeued(sqlite_repository):
    repository, session_factory = sqlite_repository
    job_id = _insert_suggestion_job(session_factory, suggestion_minutes_ago=31)

    item = repository.queue_parser_spec_suggestion(
        job_id,
        task_id="new-task",
        actor="engineer",
        reason="recover a lost suggestion worker",
    )

    suggestion = item["agent_state_json"]["parser_spec_suggestion"]
    assert suggestion["status"] == "queued"
    assert suggestion["task_id"] == "new-task"
    assert suggestion["stage"] == "semantic_queued"
    assert suggestion["heartbeat_at"] == suggestion["requested_at"]
    assert suggestion["deadline_at"] > suggestion["requested_at"]
    assert suggestion["elapsed_ms"] == 0


def test_fresh_spec_suggestion_still_blocks_requeue(sqlite_repository):
    repository, session_factory = sqlite_repository
    job_id = _insert_suggestion_job(session_factory, suggestion_minutes_ago=1)

    with pytest.raises(ParserJobStateError, match="already running"):
        repository.queue_parser_spec_suggestion(
            job_id,
            task_id="new-task",
            actor="engineer",
            reason="early requeue",
        )


def test_spec_suggestion_heartbeat_preserves_fixed_deadline(sqlite_repository):
    repository, session_factory = sqlite_repository
    job_id = _insert_suggestion_job(session_factory, suggestion_minutes_ago=31)
    queued = repository.queue_parser_spec_suggestion(
        job_id,
        task_id="new-task",
        actor="engineer",
        reason="start semantic analysis",
    )
    original_deadline = queued["agent_state_json"]["parser_spec_suggestion"][
        "deadline_at"
    ]

    first = repository.record_parser_spec_suggestion(
        job_id,
        task_id="new-task",
        status="running",
    )
    second = repository.record_parser_spec_suggestion(
        job_id,
        task_id="new-task",
        status="running",
    )

    assert first["agent_state_json"]["parser_spec_suggestion"]["deadline_at"] == (
        original_deadline
    )
    assert second["agent_state_json"]["parser_spec_suggestion"]["deadline_at"] == (
        original_deadline
    )


def test_spec_suggestion_heartbeat_backfills_legacy_missing_deadline(
    sqlite_repository,
):
    repository, session_factory = sqlite_repository
    job_id = _insert_suggestion_job(session_factory, suggestion_minutes_ago=1)

    first = repository.record_parser_spec_suggestion(
        job_id,
        task_id="old-task",
        status="running",
    )
    first_state = first["agent_state_json"]["parser_spec_suggestion"]
    second = repository.record_parser_spec_suggestion(
        job_id,
        task_id="old-task",
        status="running",
    )

    assert first_state["deadline_at"] > first_state["requested_at"]
    assert second["agent_state_json"]["parser_spec_suggestion"]["deadline_at"] == (
        first_state["deadline_at"]
    )


def test_suggestion_identity_is_persisted_with_task_and_audit(sqlite_repository):
    repository, factory = sqlite_repository
    job_id = _insert_suggestion_job(factory, suggestion_minutes_ago=31)
    repository.queue_parser_spec_suggestion(job_id, task_id="identity-task", actor="renamable",
        actor_user_id="stable-id", reason="request a fresh semantic plan")
    restored = ParserJobRepository().record_parser_spec_suggestion(job_id, task_id="identity-task", status="running")
    suggestion = restored["agent_state_json"]["parser_spec_suggestion"]
    assert suggestion["requested_by_user_id"] == "stable-id"
    assert suggestion["requested_by"] == "renamable"
    with _session_scope(factory) as db:
        audit = db.query(AuditLogRecord).filter_by(action="parser_spec_suggestion_queued").one()
        assert audit.user_id == "stable-id"
        assert audit.username == "renamable"


@pytest.mark.parametrize("explicit_dispatch", [False, True])
def test_retry_identity_survives_beat_dispatch_or_is_replaced_by_explicit_dispatch(sqlite_repository, monkeypatch, explicit_dispatch):
    repository, factory = sqlite_repository
    job_id = _insert_job(factory, status="failed", started_minutes_ago=None)
    repository.retry_job(job_id, actor="retry-user", actor_user_id="retry-id", reason="retry failed run")
    monkeypatch.setattr(parser_repository, "_parser_generation_readiness", lambda *args: {"ready": True})
    context = {"actor": "dispatcher", "actor_user_id": "dispatcher-id"} if explicit_dispatch else {}
    dispatched = repository.mark_job_dispatched(job_id, "new-delivery", **context)
    identity = dispatched["agent_state_json"]["execution_identity"]
    assert identity["task_id"] == "new-delivery"
    assert identity["user_id"] == ("dispatcher-id" if explicit_dispatch else "retry-id")
    with _session_scope(factory) as db:
        audit = db.query(AuditLogRecord).filter_by(action="parser_job_retry").one()
        assert audit.user_id == "retry-id"
    repository.transition_job_status(job_id, "failed", "worker failed")
    retried = repository.retry_job(job_id, actor="legacy-service", reason="legacy retry")
    assert retried["agent_state_json"]["execution_identity"]["user_id"] is None
    unknown = repository.mark_job_dispatched(job_id, "legacy-delivery")
    assert unknown["agent_state_json"]["execution_identity"]["user_id"] is None
