from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.apps.rule_governance.backend.generators.runner import GeneratorExecutionError
from src.apps.rule_governance.backend.parser import repository as repositories
from src.apps.rule_governance.backend.parser import worker
from src.infrastructure.db.models import (
    AuditLogRecord,
    ParserModelCallLedger,
    ParserTemplateJob,
)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'parser-worker.db'}")
    for model in (ParserTemplateJob, ParserModelCallLedger, AuditLogRecord):
        model.__table__.create(engine)

    @contextmanager
    def scope():
        with Session(engine) as db, db.begin():
            yield db

    monkeypatch.setattr(repositories, "get_db_session", scope)
    monkeypatch.setattr(worker, "_refresh_rule_asset_projection", Mock())
    run = Mock(return_value={"success": True})
    monkeypatch.setattr(worker, "execute_generator", run)
    with scope() as db:
        db.add(
            ParserTemplateJob(
                id="job",
                job_type="CREATE_TEMPLATE",
                vendor="test",
                profile="switch",
                canonical_command="show status",
                output_signature="sig",
                idempotency_key="job",
                status="pending",
                pipeline_version="simple_v2",
                celery_task_id="delivery",
                agent_state_json={
                    "execution_identity": {"user_id": "owner", "task_id": "delivery"},
                    "parser_spec_suggestion": {
                        "status": "queued",
                        "task_id": "delivery",
                        "requested_by_user_id": "owner",
                    },
                },
            )
        )
    yield repositories.ParserJobRepository(), scope, run
    engine.dispose()


def deliver(**params):
    return worker.execute_parser_delivery(task_id="delivery", params={"job_id": "job", **params})


def test_generation_claims_once_and_preserves_stored_identity(environment):
    repo, _, run = environment
    assert deliver(actor_user_id="forged", user_id="forged")["success"]
    state = repo.get_job_lifecycle("job")
    assert state["status"] == "succeeded"
    assert state["execution_identity"]["user_id"] == "owner"
    assert run.call_args.args[1] == {"job_id": "job"}
    assert not deliver()["success"]
    assert run.call_count == 1


@pytest.mark.parametrize("kind", ["cancelled", "other_delivery", "missing"])
def test_invalid_or_old_delivery_never_calls_generator(environment, kind):
    repo, scope, run = environment
    with scope() as db:
        job = db.get(ParserTemplateJob, "job")
        if kind == "cancelled":
            job.status = "cancelled"
        elif kind == "other_delivery":
            job.celery_task_id = "other"
        else:
            db.delete(job)
    assert not deliver()["success"]
    run.assert_not_called()


def test_generator_error_retains_reason_code(environment):
    repo, _, run = environment
    run.side_effect = GeneratorExecutionError(
        "conflict", payload={"failure_code": "contract_conflict"}
    )
    assert deliver()["failure_code"] == "contract_conflict"
    assert repo.get_job_lifecycle("job")["failure_code"] == "contract_conflict"


@pytest.mark.parametrize("failure", [SoftTimeLimitExceeded(), RuntimeError("worker fault")])
def test_worker_failure_is_persisted_then_reraised(environment, failure):
    repo, _, run = environment
    run.side_effect = failure
    with pytest.raises(type(failure)):
        deliver()
    state = repo.get_job_lifecycle("job")
    assert state["status"] == "failed"
    assert state["failure_code"] == (
        "worker_timeout" if isinstance(failure, SoftTimeLimitExceeded) else "worker_exception"
    )


@pytest.mark.parametrize("result_kind", ["success", "failure"])
def test_reassignment_between_read_and_write_cannot_be_overwritten(
    environment, monkeypatch, result_kind
):
    repo, scope, run = environment
    original = repositories.ParserJobRepository.transition_job_status
    if result_kind == "failure":
        run.side_effect = GeneratorExecutionError("old failure")

    def transition(self, job_id, status, *args, **kwargs):
        if status in {"succeeded", "failed"}:
            with scope() as db:
                job = db.get(ParserTemplateJob, job_id)
                job.status, job.celery_task_id = "pending", "replacement-delivery"
                job.agent_state_json = {
                    "execution_identity": {
                        "user_id": "new-owner",
                        "task_id": "replacement-delivery",
                    }
                }
        return original(self, job_id, status, *args, **kwargs)

    monkeypatch.setattr(repositories.ParserJobRepository, "transition_job_status", transition)
    assert not deliver()["success"]
    state = repo.get_job_lifecycle("job")
    assert state["status"] == "pending"
    assert state["celery_task_id"] == "replacement-delivery"
    assert state["execution_identity"]["user_id"] == "new-owner"


def test_cancel_while_generator_runs_discards_late_result(environment):
    repo, scope, run = environment

    def generate(*args):
        with scope() as db:
            db.get(ParserTemplateJob, "job").status = "cancelled"
        return {"success": True}

    run.side_effect = generate
    assert deliver()["error"] == "stale_generation_discarded"
    assert repo.get_job_lifecycle("job")["status"] == "cancelled"


def test_suggestion_claim_and_completion_survive_duplicate_delivery(environment):
    repo, _, run = environment

    def generate(*args):
        claimed = repo.get_job_lifecycle("job")["parser_spec_suggestion"]
        assert claimed["worker_claimed_at"]
        assert claimed["requested_by_user_id"] == "owner"
        duplicate = deliver(operation="suggest_spec", suggestion_task_id="delivery")
        assert duplicate["ignored"]
        repo.record_parser_spec_suggestion(
            "job", task_id="delivery", status="ready", suggestion={"rationale": "done"}
        )
        return {"success": True}

    run.side_effect = generate
    assert deliver(operation="suggest_spec", suggestion_task_id="delivery")["success"]
    state = repo.get_job_lifecycle("job")
    assert state["status"] == "pending"  # Suggestion never takes over generation's lifecycle.
    assert state["parser_spec_suggestion"]["status"] == "ready"
    assert not deliver(operation="suggest_spec", suggestion_task_id="delivery")["success"]
    assert run.call_count == 1


@pytest.mark.parametrize("status", ["running", "failed"])
def test_late_suggestion_callback_cannot_overwrite_terminal_result(environment, status):
    repo, _, _ = environment
    repo.record_parser_spec_suggestion(
        "job", task_id="delivery", status="ready", suggestion={"rationale": "done"}
    )
    with pytest.raises(repositories.ParserJobStateError, match="terminal"):
        repo.record_parser_spec_suggestion("job", task_id="delivery", status=status)
    assert repo.get_job_lifecycle("job")["parser_spec_suggestion"]["result"]["rationale"] == "done"


@pytest.mark.parametrize("persist_ready", [False, True])
def test_suggestion_timeout_does_not_destroy_already_persisted_result(environment, persist_ready):
    repo, _, run = environment

    def generate(*args):
        if persist_ready:
            repo.record_parser_spec_suggestion(
                "job", task_id="delivery", status="ready", suggestion={"rationale": "durable"}
            )
        raise GeneratorExecutionError(
            "timeout", payload={"failure_code": "skill_execution_timeout"}
        )

    run.side_effect = generate
    assert not deliver(operation="suggest_spec", suggestion_task_id="delivery")["success"]
    assert repo.get_job_lifecycle("job")["parser_spec_suggestion"]["status"] == (
        "ready" if persist_ready else "failed"
    )


def test_mismatched_suggestion_delivery_never_claims(environment):
    repo, _, run = environment
    assert (
        deliver(operation="suggest_spec", suggestion_task_id="forged")["error"]
        == "stale_suggestion_discarded"
    )
    run.assert_not_called()
    assert repo.get_job_lifecycle("job")["parser_spec_suggestion"]["status"] == "queued"


def test_old_task_name_uses_package_worker_with_original_delivery_id(environment):
    from src.core.workflows.tasks import execute_skill_task

    repo, _, run = environment
    execute_skill_task.push_request(id="delivery")
    try:
        result = execute_skill_task.run("textfsm-generator", job_id="job")
    finally:
        execute_skill_task.pop_request()
    assert result["success"]
    assert repo.get_job_lifecycle("job")["status"] == "succeeded"
    run.assert_called_once()


def test_package_task_is_registered_with_ai_queue_and_specific_limits(environment):
    from src.apps.rule_governance.backend.parser.tasks import execute_parser_task
    from src.common.config import get_settings
    from src.core.celery_tasks.celery_app import celery

    assert celery.tasks[worker.PARSER_EXECUTION_TASK] is execute_parser_task._get_current_object()
    assert execute_parser_task.queue == "app.rule-governance"
    assert (
        execute_parser_task.soft_time_limit
        == get_settings().PARSER_GENERATION_TASK_SOFT_TIME_LIMIT_SECONDS
    )
    assert (
        execute_parser_task.time_limit == get_settings().PARSER_GENERATION_TASK_TIME_LIMIT_SECONDS
    )
    execute_parser_task.push_request(id="delivery")
    try:
        assert execute_parser_task.run(job_id="job")["success"]
    finally:
        execute_parser_task.pop_request()


def test_postgres_concurrent_suggestion_claim_has_one_winner():
    """Verify the production database lock, which SQLite cannot exercise."""
    from src.infrastructure.db.postgres import get_db_session

    job_id, task_id = "test-parser-claim-" + uuid4().hex, uuid4().hex
    with get_db_session() as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                vendor="test",
                profile="test",
                canonical_command="show test",
                output_signature=job_id,
                idempotency_key=job_id,
                status="failed",
                pipeline_version="simple_v2",
                agent_state_json={
                    "parser_spec_suggestion": {
                        "status": "queued",
                        "task_id": task_id,
                        "requested_by_user_id": "test-initiator",
                    }
                },
            )
        )
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=10)
        try:
            repositories.ParserJobRepository().claim_parser_spec_suggestion(job_id, task_id=task_id)
            return True
        except repositories.ParserJobStateError:
            return False

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(claim), pool.submit(claim)]
            assert sorted(future.result(timeout=15) for future in results) == [False, True]
        with get_db_session() as db:
            state = db.get(ParserTemplateJob, job_id).agent_state_json["parser_spec_suggestion"]
            assert state["status"] == "running"
            assert state["requested_by_user_id"] == "test-initiator"
    finally:
        with get_db_session() as db:
            db.query(ParserTemplateJob).filter_by(id=job_id).delete()


@pytest.fixture(autouse=True)
def app_admission_for_business_unit_tests(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock
    from src.apps.rule_governance.backend import access
    monkeypatch.setattr(access, "authorize", Mock())
    monkeypatch.setattr(access, "require_running", lambda: None)
    monkeypatch.setattr(access, "execution_lock", lambda **kwargs: nullcontext())
