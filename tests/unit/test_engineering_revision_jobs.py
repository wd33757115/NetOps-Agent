from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.apps.rule_governance.backend.rule_control import engineering_revision as revision
from src.apps.rule_governance.backend.rule_control import engineering_revision_async as tasks
from src.apps.rule_governance.backend.rule_control.engineering_revision_jobs import (
    HARD_LIMIT,
    EngineeringRevisionJobs,
    RevisionExecutionStoppedError,
    RevisionRequestConflictError,
)
from src.infrastructure.db.models import ParserControlSetting, RuleGenerationJobRecord


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'revision.db'}")
    for model in (ParserControlSetting, RuleGenerationJobRecord):
        model.__table__.create(engine)

    @contextmanager
    def scope():
        with Session(engine) as db, db.begin():
            yield db

    with scope() as db:
        db.add(
            RuleGenerationJobRecord(
                id="bundle", job_type="RULE_ASSET_BUNDLE", status="pending_approval"
            )
        )
    clock = [datetime.now(timezone.utc)]
    send = Mock()
    jobs = EngineeringRevisionJobs(session_scope=scope, dispatch=send, clock=lambda: clock[0])
    service = Mock()
    service.revise.return_value = {"success": True, "bundle": {"id": "replacement"}}
    monkeypatch.setattr(revision, "RuleAssetEngineeringRevisionService", lambda: service)
    monkeypatch.setattr(tasks, "EngineeringRevisionJobs", lambda: jobs)
    yield jobs, scope, send, clock, service
    engine.dispose()


def submit(jobs, **changes):
    return jobs.submit(
        "bundle",
        **{
            "contract": {"field": "status"},
            "change_policies": [],
            "event_rules": [],
            "actor": "display",
            "actor_user_id": "owner",
            "reason": "fix the contract",
            "idempotency_key": "request-123",
            **changes,
        },
    )


def test_idempotent_submit_is_durable_and_content_conflicts_are_rejected(env):
    jobs, scope, send, _, _ = env
    first = submit(jobs)
    assert submit(jobs)["task_id"] == first["task_id"]
    assert send.call_count == 1
    with pytest.raises(RevisionRequestConflictError):
        submit(jobs, contract={"changed": True})
    restarted = EngineeringRevisionJobs(session_scope=scope)
    assert restarted.read(first["task_id"], user_id="owner")["status"] == "PENDING"
    assert restarted.pending_count() == 1


def test_queries_and_idempotency_are_scoped_to_owner(env):
    jobs, _, _, _, service = env
    first = submit(jobs)
    other = submit(jobs, actor_user_id="other")
    assert first["task_id"] != other["task_id"]
    with pytest.raises(KeyError):
        jobs.read(first["task_id"], user_id="other")
    assert len(jobs.list_for_bundle("bundle", user_id="owner")["items"]) == 1
    assert len(jobs.list_for_bundle("bundle", user_id="admin", is_admin=True)["items"]) == 2
    for item in (first, other):
        jobs.run(item["task_id"])
        assert service.revise.call_args.kwargs["idempotency_key"] == item["task_id"]


def test_worker_claims_once_and_results_survive_backend_expiry(env):
    jobs, scope, _, _, service = env
    task = submit(jobs)["task_id"]

    def execute(*args, **kwargs):
        assert kwargs["actor_user_id"] == "owner"
        assert jobs.run(task)["ignored"]
        kwargs["checkpoint"]("recording")
        return {"success": True, "bundle": {"id": "replacement"}}

    service.revise.side_effect = execute
    assert jobs.run(task)["successful"]
    assert jobs.run(task)["ignored"]
    assert submit(jobs)["successful"]
    restarted = EngineeringRevisionJobs(session_scope=scope)
    assert (
        restarted.read(task, user_id="owner")["result"]["result"]["bundle"]["id"] == "replacement"
    )
    assert restarted.pending_count() == 0
    assert service.revise.call_count == 1


def test_broker_failure_keeps_request_for_beat_recovery(env):
    jobs, _, send, clock, _ = env
    send.side_effect = RuntimeError("broker unavailable")
    task = submit(jobs)["task_id"]
    assert jobs.read(task, user_id="owner")["dispatch_error"]
    assert jobs.read(task, user_id="owner")["status"] == "PENDING"
    send.side_effect = None
    clock[0] += timedelta(seconds=31)
    assert jobs.reconcile()["dispatched"] == 1
    assert send.call_args.kwargs["task_id"] == task
    assert send.call_args.kwargs["kwargs"] == {"task_id": task}


@pytest.mark.parametrize(
    "phase,state",
    [("validating", "failed"), ("persisting", "needs_attention"), ("recording", "needs_attention")],
)
def test_failure_after_asset_writes_is_never_automatically_replayed(env, phase, state):
    jobs, _, _, clock, service = env
    task = submit(jobs)["task_id"]

    def execute(*args, **kwargs):
        kwargs["checkpoint"](phase)
        raise ValueError("validation or persistence failure")

    service.revise.side_effect = execute
    assert jobs.run(task)["state"] == state
    clock[0] += timedelta(seconds=HARD_LIMIT + 120)
    assert jobs.reconcile()["dispatched"] == 0
    assert jobs.run(task)["ignored"]
    assert jobs.pending_count() == (1 if state == "needs_attention" else 0)


def test_expired_lease_rejects_late_writes_and_remains_in_drain_count(env):
    jobs, _, _, clock, _ = env
    task = submit(jobs)["task_id"]
    state = jobs._claim(task)
    clock[0] += timedelta(seconds=HARD_LIMIT + 61)
    assert jobs.reconcile()["needs_attention"] == 1
    with pytest.raises(RevisionExecutionStoppedError):
        jobs.checkpoint(task, state["lease_token"], "persisting")
    assert not jobs._finish(task, state["lease_token"], status="succeeded")
    assert jobs.read(task, user_id="owner")["state"] == "needs_attention"
    assert jobs.pending_count() == 1


@pytest.mark.parametrize("principal", [None, "legacy-owner"])
def test_old_seven_argument_delivery_is_adopted_and_runs_once(env, principal):
    jobs, _, _, _, service = env
    task = tasks.validate_rule_asset_engineering_revision_task
    task.push_request(id="legacy-delivery")
    try:
        args = (
            "bundle",
            {"field": "status"},
            [],
            [],
            "old-display",
            "old revision reason",
            "legacy-key",
        )
        kwargs = {} if principal is None else {"actor_user_id": principal}
        assert task.run(*args, **kwargs)["successful"]
        assert task.run(*args, **kwargs)["ignored"]
    finally:
        task.pop_request()
    assert service.revise.call_count == 1
    assert service.revise.call_args.kwargs["actor_user_id"] == principal
    assert service.revise.call_args.kwargs["idempotency_key"] == "legacy-key"
    assert jobs.read("legacy-delivery", user_id="admin", is_admin=True)["successful"]
    if principal is None:
        with pytest.raises(KeyError):
            jobs.read("legacy-delivery", user_id="old-display")


def test_new_delivery_cannot_execute_another_task_identity(env):
    jobs, _, _, _, service = env
    task_id = submit(jobs)["task_id"]
    tasks.execute_engineering_revision.push_request(id="different-delivery")
    try:
        assert not tasks.execute_engineering_revision.run(task_id)["success"]
    finally:
        tasks.execute_engineering_revision.pop_request()
    service.revise.assert_not_called()


def test_http_reads_use_authenticated_owner_and_never_guess_from_redis(env, monkeypatch):
    from fastapi import HTTPException

    from src.apps.rule_governance.backend import api
    from src.apps.rule_governance.backend.rule_control import engineering_revision_jobs
    from src.auth.models import CurrentUser

    jobs, _, _, _, _ = env
    task = submit(jobs)["task_id"]
    monkeypatch.setattr(engineering_revision_jobs, "EngineeringRevisionJobs", lambda: jobs)
    user = CurrentUser(user_id="other", username="display", role="operator")
    request = SimpleNamespace(state=SimpleNamespace(current_user=user))
    with pytest.raises(HTTPException) as error:
        api.get_engineering_revision_task(task, request)
    assert error.value.status_code == 404
    user.user_id = "owner"
    assert api.get_engineering_revision_task(task, request)["status"] == "PENDING"


def test_history_never_hides_unsettled_jobs_behind_recent_completed_jobs(env):
    jobs, _, _, clock, _ = env
    first = submit(jobs)["task_id"]
    for i in range(21):
        clock[0] += timedelta(seconds=1)
        task = submit(jobs, idempotency_key=f"completed-{i}")["task_id"]
        jobs.run(task)
    assert jobs.list_for_bundle("bundle", user_id="owner")["items"][0]["task_id"] == first
    assert jobs.list_for_bundle("bundle", user_id=None)["items"] == []


def interrupted(env):
    jobs = env[0]
    task = submit(jobs)["task_id"]
    state = jobs._claim(task)
    jobs._finish(task, state["lease_token"], status="needs_attention", error="worker stopped")
    return task, state


def test_resolution_is_audited_idempotent_and_never_approves_assets(env):
    jobs, scope, _, _, _ = env
    task, state = interrupted(env)
    with scope() as db:
        db.add(
            RuleGenerationJobRecord(
                id="partial-bundle",
                job_type="RULE_ASSET_BUNDLE",
                status="rejected",
                source_ref=f"engineering_revision:bundle:{task}",
                payload_json={"child_job_ids": []},
            )
        )
        db.add(
            RuleGenerationJobRecord(
                id="partial-child",
                job_type="EVENT_RULE",
                status="validating",
                source_ref="rule_asset_bundle:partial-bundle",
            )
        )
    evidence = jobs.inspection(task, user_id="owner")
    assert evidence["can_resolve"]
    assert {r["id"] for r in evidence["records"]} == {"partial-bundle", "partial-child"}
    args = dict(
        user_id="owner",
        actor="display",
        note="已核查部分候选，保留用于后续修复",
        evidence_digest=evidence["digest"],
    )
    result = jobs.resolve(task, **args)
    assert result["state"] == "resolved"
    assert not result["successful"]
    assert result["resolution"]["actor_user_id"] == "owner"
    assert result["error"] == "worker stopped"
    assert jobs.resolve(task, **args) == result
    assert jobs.pending_count() == 0
    assert not jobs._finish(task, state["lease_token"], status="succeeded")
    with pytest.raises(RevisionExecutionStoppedError), jobs.write_guard(task, state["lease_token"]):
        pytest.fail("closed task must not enter a write stage")
    with scope() as db:
        assert db.get(RuleGenerationJobRecord, "partial-bundle").status == "rejected"
        assert db.get(RuleGenerationJobRecord, "partial-child").status == "validating"
    with pytest.raises(RevisionRequestConflictError):
        jobs.resolve(task, **{**args, "note": "不能覆盖既有结案记录"})


def test_resolution_rejects_stale_evidence_and_wrong_owner(env):
    jobs, scope, _, _, _ = env
    task, _ = interrupted(env)
    evidence = jobs.inspection(task, user_id="owner")
    with scope() as db:
        db.add(
            RuleGenerationJobRecord(
                id="new-evidence",
                job_type="RULE_ASSET_BUNDLE",
                status="rejected",
                source_ref=f"engineering_revision:bundle:{task}",
            )
        )
    args = dict(
        user_id="owner", actor="display", note="已核查相关候选", evidence_digest=evidence["digest"]
    )
    with pytest.raises(RevisionRequestConflictError):
        jobs.resolve(task, **args)
    with pytest.raises(KeyError):
        jobs.inspection(task, user_id="other")
    with pytest.raises(KeyError):
        jobs.resolve(task, **{**args, "user_id": "other"})
    assert jobs.pending_count() == 1
    fresh = jobs.inspection(task, user_id="admin", is_admin=True)
    result = jobs.resolve(
        task, **{**args, "user_id": "admin", "is_admin": True, "evidence_digest": fresh["digest"]}
    )
    assert result["resolution"]["actor_user_id"] == "admin"


@pytest.mark.parametrize(
    "state_name,version", [("running", 1), ("queued", 1), ("failed", 1), ("needs_attention", None)]
)
def test_resolution_cannot_close_active_or_unfenced_execution(env, state_name, version):
    jobs, scope, _, _, _ = env
    task, _ = interrupted(env)
    with scope() as db:
        row = db.get(ParserControlSetting, "engineering-revision-job:" + task)
        row.value_json = {**row.value_json, "status": state_name, "write_fencing_version": version}
    evidence = jobs.inspection(task, user_id="owner")
    assert not evidence["can_resolve"]
    with pytest.raises(RevisionRequestConflictError):
        jobs.resolve(
            task,
            user_id="owner",
            actor="display",
            note="已核查相关候选",
            evidence_digest=evidence["digest"],
        )


def test_persister_uses_execution_guard_and_rejects_late_or_unrelated_writes(env, monkeypatch):
    from src.apps.rule_governance.backend.generators.rule_asset_persister.scripts import (
        run as persister,
    )
    from src.apps.rule_governance.backend.rule_control import engineering_revision_jobs

    jobs = env[0]
    task = submit(jobs)["task_id"]
    state = jobs._claim(task)
    monkeypatch.setattr(engineering_revision_jobs, "EngineeringRevisionJobs", lambda: jobs)
    persist = Mock(return_value={"success": True})
    monkeypatch.setattr(persister, "_run_persist", persist)
    params = {
        "source_ref": f"engineering_revision:bundle:{task}",
        "revision_execution": {"task_id": task, "lease_token": state["lease_token"]},
    }
    assert persister.run_persist(params)["success"]
    with pytest.raises(RevisionExecutionStoppedError):
        persister.run_persist({**params, "source_ref": "unrelated"})
    jobs._finish(task, state["lease_token"], status="needs_attention")
    with pytest.raises(RevisionExecutionStoppedError):
        persister.run_persist(params)
    assert persist.call_count == 1


def test_resolution_http_uses_server_actor_and_validates_state(env, monkeypatch):
    from fastapi import HTTPException

    from src.apps.rule_governance.backend import api
    from src.apps.rule_governance.backend.rule_control import engineering_revision_jobs
    from src.auth.models import CurrentUser

    jobs = env[0]
    task, _ = interrupted(env)
    monkeypatch.setattr(engineering_revision_jobs, "EngineeringRevisionJobs", lambda: jobs)
    user = CurrentUser(user_id="owner", username="trusted-display", role="operator")
    request = SimpleNamespace(state=SimpleNamespace(current_user=user))
    evidence = api.inspect_engineering_revision_task(task, request)
    payload = api.EngineeringRevisionResolutionRequest(
        evidence_digest=evidence["digest"], note="已核查未发现相关候选"
    )
    result = api.resolve_engineering_revision_task(task, request, payload)
    assert result["resolution"]["actor"] == "trusted-display"
    assert result["resolution"]["actor_user_id"] == "owner"
    user.user_id = "other"
    with pytest.raises(HTTPException) as error:
        api.resolve_engineering_revision_task(task, request, payload)
    assert error.value.status_code == 404


def test_postgres_concurrent_submissions_and_claims_have_one_winner():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event
    from uuid import uuid4

    from src.apps.rule_governance.backend.rule_control.engineering_revision_jobs import PREFIX
    from src.infrastructure.db.postgres import get_db_session

    bundle_id = "test-revision-" + uuid4().hex
    jobs = EngineeringRevisionJobs(dispatch=Mock())
    with get_db_session() as db:
        db.add(
            RuleGenerationJobRecord(
                id=bundle_id, job_type="RULE_ASSET_BUNDLE", status="pending_approval"
            )
        )
    barrier = Barrier(2)

    def send_request():
        barrier.wait(timeout=10)
        return jobs.submit(
            bundle_id,
            contract={"test": True},
            change_policies=[],
            event_rules=[],
            actor="test",
            actor_user_id="test-owner",
            reason="test revision",
            idempotency_key="test-request",
        )["task_id"]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send_request), pool.submit(send_request)]
            ids = [future.result(timeout=15) for future in futures]
        assert ids[0] == ids[1]
        assert jobs.send.call_count == 1
        barrier = Barrier(2)

        def claim():
            barrier.wait(timeout=10)
            return jobs._claim(ids[0]) is not None

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim), pool.submit(claim)]
            assert sorted(future.result(timeout=15) for future in futures) == [False, True]
        with get_db_session() as db:
            token = db.get(ParserControlSetting, PREFIX + ids[0]).value_json["lease_token"]
        writing, release = Event(), Event()

        def hold_write_stage():
            with jobs.write_guard(ids[0], token):
                writing.set()
                assert release.wait(timeout=10)

        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(hold_write_stage)
            try:
                assert writing.wait(timeout=10)
                with pytest.raises(KeyError):
                    jobs.inspection(ids[0], user_id="other")
                with pytest.raises(RevisionRequestConflictError, match="仍在写入"):
                    jobs.inspection(ids[0], user_id="test-owner")
                with pytest.raises(RevisionRequestConflictError, match="仍在写入"):
                    jobs.resolve(
                        ids[0],
                        user_id="test-owner",
                        actor="test",
                        note="核查并发写入测试",
                        evidence_digest="stale",
                    )
            finally:
                release.set()
            writer.result(timeout=10)
        jobs._finish(ids[0], token, status="needs_attention")
        evidence = jobs.inspection(ids[0], user_id="test-owner")
        barrier = Barrier(2)

        def resolve_once(note):
            barrier.wait(timeout=10)
            try:
                jobs.resolve(
                    ids[0],
                    user_id="test-owner",
                    actor="test",
                    note=note,
                    evidence_digest=evidence["digest"],
                )
                return True
            except RevisionRequestConflictError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(resolve_once, "第一个核查处理说明"),
                pool.submit(resolve_once, "第二个核查处理说明"),
            ]
            assert sorted(future.result(timeout=15) for future in futures) == [False, True]
        with pytest.raises(RevisionExecutionStoppedError), jobs.write_guard(ids[0], token):
            pytest.fail("late writer entered after resolution")
    finally:
        with get_db_session() as db:
            db.query(ParserControlSetting).filter(
                ParserControlSetting.key.startswith(PREFIX),
                ParserControlSetting.value_json["bundle_id"].as_string() == bundle_id,
            ).delete(synchronize_session=False)
            db.query(RuleGenerationJobRecord).filter_by(id=bundle_id).delete()


@pytest.fixture(autouse=True)
def app_admission_for_business_unit_tests(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock
    from src.apps.rule_governance.backend import access
    monkeypatch.setattr(access, "authorize", Mock())
    monkeypatch.setattr(access, "require_running", lambda: None)
    monkeypatch.setattr(access, "execution_lock", lambda **kwargs: nullcontext())
