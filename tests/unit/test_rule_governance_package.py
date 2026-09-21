from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.apps.rule_governance.backend.rule_control import generation_jobs as module
from src.apps.rule_governance.backend.rule_control.generation import generate
from src.infrastructure.db.models import ParserControlSetting, RuleGenerationJobRecord


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'generation.db'}")
    for model in (ParserControlSetting, RuleGenerationJobRecord):
        model.__table__.create(engine)

    @contextmanager
    def scope():
        with Session(engine) as db, db.begin():
            yield db

    with scope() as db:
        db.add(RuleGenerationJobRecord(id="seed", job_type="OBSERVATION_CONTRACT"))
    monkeypatch.setattr(module, "get_db_session", scope)
    authorize = Mock()
    monkeypatch.setattr(module, "authorize", authorize)
    dispatch = Mock()
    monkeypatch.setattr(module.GenerationJobs, "dispatch", dispatch)
    yield module.GenerationJobs(), scope, authorize, dispatch
    engine.dispose()


def test_fixed_sequence_preserves_required_validation_and_replay():
    calls = []

    def step(name, generator, params):
        calls.append((name, params))
        return {"success": True, "proposal": {"type": "cpu"}, "rules": [{"id": "cpu"}]}

    generate({"rule_asset_requirements": {"requires_change_policy": True,
              "requires_event_rule_assets": True, "requires_historical_replay": True}}, step)
    assert [name for name, _ in calls] == ["observation_contract", "rule_target_planning", "change_policy",
                                         "event_rule", "event_rule_validation", "event_rule_replay", "persist_assets"]
    assert calls[4][1]["candidate_rules"] == [{"id": "cpu"}]
    assert calls[-1][1]["event_rule_validation"]["success"]


def test_observation_only_does_not_create_unrequested_policies():
    step = Mock(return_value={"success": True})
    generate({"rule_asset_requirements": {}}, step)
    assert [c.args[0] for c in step.call_args_list] == ["observation_contract", "rule_target_planning", "persist_assets"]


def test_submission_is_durable_and_deduplicated(jobs):
    service, scope, _, dispatch = jobs
    run = service.submit("seed", {}, user_id="1")
    assert service.submit("seed", {}, user_id="1") == run
    assert service.read(run)["status"] == "queued"
    assert dispatch.call_count == 1
    with scope() as db:
        payload = db.get(RuleGenerationJobRecord, "seed").payload_json
        assert payload["latest_generation_run_id"] == run
        assert [item["run_id"] for item in payload["generation_workflow_runs"]] == [run]
    with pytest.raises(ValueError):
        service.submit("seed", {"different": True}, user_id="1")


def test_queued_worker_rechecks_revocation_before_generating(jobs, monkeypatch):
    service, _, authorize, _ = jobs
    run = service.submit("seed", {}, user_id="1")
    authorize.side_effect = ValueError("grant revoked")
    execute = Mock()
    monkeypatch.setattr(module, "execute_generator", execute)
    assert service.run(run)["status"] == "failed"
    execute.assert_not_called()


def test_delivery_runs_once_and_keeps_stage_results(jobs, monkeypatch):
    service, _, _, _ = jobs
    run = service.submit("seed", {"rule_asset_requirements": {}}, user_id="1")
    execute = Mock(return_value={"success": True})
    monkeypatch.setattr(module, "execute_generator", execute)
    assert service.run(run)["status"] == "completed"
    assert len(service.read(run)["steps"]) == 3
    assert service.run(run)["ignored"]
    assert execute.call_count == 3
    assert execute.call_args.args[1]["generation_execution"]["run_id"] == run


def test_partial_persistence_is_not_replayed_and_closed_token_cannot_write(jobs, monkeypatch):
    service, _, _, _ = jobs
    run = service.submit("seed", {}, user_id="1")
    monkeypatch.setattr(module, "execute_generator", Mock(side_effect=[{"success": True}, {"success": True}, RuntimeError("partial write")]))
    assert service.run(run)["status"] == "needs_attention"
    token = service.read(run)["token"]
    service.close(run, user_id="1", note="inspected partial assets")
    with pytest.raises(ValueError):
        with service.write_guard(run, token):
            pytest.fail("closed generation wrote assets")


@pytest.mark.parametrize("name", ["rule-asset-generation", "rule-asset-submit", "rule-asset-approve"])
def test_generic_workflow_entry_is_retired(name):
    from src.core.workflows.engine import WorkflowEngine
    with pytest.raises(ValueError, match="规则治理"):
        WorkflowEngine.start(name, {})


def test_manifest_owns_tasks_and_schedules_once():
    from src.core.celery_tasks.celery_app import celery
    from src.platform.kernel.registry import discover, task_configuration
    manifest = discover()["rule-governance"]
    modules, routes, schedules = task_configuration()
    assert manifest.data["backend"]["task_names"]
    assert all(routes[t]["queue"] == "app.rule-governance" for t in manifest.data["backend"]["task_names"])
    for item in manifest.data["backend"]["schedules"]:
        assert sum(entry["task"] == item["task"] for entry in celery.conf.beat_schedule.values()) == 1
    assert "src.apps.rule_governance.backend.tasks" in modules


def test_handoff_rejects_old_worker_on_either_legacy_queue():
    from src.platform.kernel.readiness import check_worker_reports
    from src.platform.kernel.registry import discover
    manifest = discover()["rule-governance"]
    report = {"apps": {manifest.id: manifest.digest}, "tasks": manifest.data["backend"]["task_names"]}
    for queue in ("netops.ai", "netops.patrol"):
        queues = {"new": [{"name": "app.rule-governance"}], "old": [{"name": queue}]}
        with pytest.raises(ValueError, match="mismatch"):
            check_worker_reports(manifest, queues, {"new": report})
        assert set(check_worker_reports(manifest, queues, {"new": report, "old": report})) == {"new", "old"}


def test_execution_lock_prevents_drain_while_child_is_alive():
    from src.apps.rule_governance.backend.access import execution_lock
    with execution_lock():
        with pytest.raises(ValueError, match="排空"):
            with execution_lock(exclusive=True):
                pytest.fail("drain raced a running generator")
    with execution_lock(exclusive=True):
        pass
