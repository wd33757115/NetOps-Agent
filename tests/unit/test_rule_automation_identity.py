from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.apps.rule_governance.backend.rule_control import automation
from src.infrastructure.db.models import (
    AuditLogRecord,
    ParserControlSetting,
    RuleGenerationJobRecord,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'automation.db'}")
    for model in (ParserControlSetting, AuditLogRecord, RuleGenerationJobRecord):
        model.__table__.create(engine)

    @contextmanager
    def scope():
        with Session(engine) as db, db.begin():
            yield db

    monkeypatch.setattr(automation, "get_db_session", scope)
    yield automation.RuleAssetAutomationSettingsRepository(), scope
    engine.dispose()


def test_policy_identity_is_trusted_and_background_result_cannot_replace_it(store):
    repo, scope = store
    initial = repo.set(
        {"mode": "generate_only", "execution_user_id": "forged"},
        actor="display",
        actor_user_id="owner",
        reason="开启候选生成流程",
    )
    assert initial["execution_user_id"] == initial["updated_by_user_id"] == "owner"
    updated = repo.set(
        {"mode": "disabled"},
        actor="second-display",
        actor_user_id="second",
        reason="暂停后台生成流程",
    )
    old_execution = {
        "actor_user_id": "owner",
        "policy_revision": initial["policy_revision"],
        "trigger": "schedule",
    }
    repo.record_run({"success": True, "mode": "generate_only", "execution": old_execution})
    current = repo.get()
    assert current["mode"] == "disabled"
    assert current["execution_user_id"] is None
    assert current["updated_by_user_id"] == "second"
    assert current["policy_revision"] == updated["policy_revision"] != initial["policy_revision"]
    assert current["last_run"]["execution"] == old_execution
    with scope() as db:
        audit = db.query(AuditLogRecord).filter_by(user_id="owner").one()
        assert audit.username == "display"
        assert audit.detail["policy_revision"] == initial["policy_revision"]


def test_legacy_policy_never_infers_identity_or_inherits_previous_owner(store):
    repo, scope = store
    repo.set(
        {"mode": "generate_only"}, actor="display", actor_user_id="owner", reason="开启候选生成流程"
    )
    result = repo.set({"mode": "generate_only"}, actor="same-display", reason="旧内部入口更新设置")
    assert result["execution_user_id"] is None
    assert result["updated_by_user_id"] is None
    with scope() as db:
        audit = db.query(AuditLogRecord).filter_by(username="same-display").one()
        assert audit.user_id is None


@pytest.mark.parametrize(
    "manual,principal", [(False, "policy-owner"), (True, "manual-user"), (True, None)]
)
def test_dispatch_and_result_keep_the_invoking_identity(store, monkeypatch, manual, principal):
    repo, scope = store
    policy = repo.set(
        {"mode": "generate_only"},
        actor="policy-display",
        actor_user_id="policy-owner",
        reason="开启候选生成流程",
    )
    activation = Mock()
    activation.start_generation.return_value = {"run_id": "workflow-id"}
    context = Mock()
    context.prepare.return_value = {"context_id": "context-id", "context": {"records": []}}
    service = automation.RuleAssetAutomationService(
        settings_repository=repo, activation_service=activation, context_service=context
    )
    monkeypatch.setattr(service, "_reconcile_stale_runs", lambda **_: {"recovered": []})
    monkeypatch.setattr(
        service,
        "_advance_pending_bundles",
        lambda **_: {"published": [], "failed": [], "review_required": []},
    )
    monkeypatch.setattr(service, "_active_generation_run_count", lambda: 0)
    monkeypatch.setattr(
        service,
        "_seed_candidates",
        lambda **_: [
            {
                "seed_id": "seed",
                "dataset_ids": ["sample"],
                "offline_job_id": "offline",
                "command": "show interfaces",
            }
        ],
    )
    monkeypatch.setattr(service, "_claim_seed", lambda *_, **__: True)
    monkeypatch.setattr(service, "_evidence_blockers", lambda *_, **__: [])
    monkeypatch.setattr(automation, "assess_context_quality", lambda *_, **__: {"passed": True})
    with scope() as db:
        db.add(RuleGenerationJobRecord(id="seed", job_type="RULE_ASSET_BUNDLE", status="draft"))
    kwargs = {"actor": "manual-display", "actor_user_id": principal} if manual else {}
    result = service.run_once(**kwargs)
    assert result["dispatched_count"] == 1
    invocation = activation.start_generation.call_args.kwargs
    assert invocation["actor_user_id"] == principal
    assert invocation["trigger"] == ("manual" if manual else "schedule")
    assert invocation["policy_revision"] == policy["policy_revision"]
    assert result["execution"]["actor_user_id"] == principal
    assert repo.get()["execution_user_id"] == "policy-owner"
    assert repo.get()["last_run"]["execution"] == result["execution"]
    with scope() as db:
        assert (
            db.get(RuleGenerationJobRecord, "seed").payload_json["automation"]["execution"]
            == result["execution"]
        )


def test_manual_legacy_invocation_does_not_borrow_policy_owner(store):
    repo, _ = store
    repo.set(
        {"mode": "disabled"}, actor="owner", actor_user_id="policy-owner", reason="暂停后台生成流程"
    )
    result = automation.RuleAssetAutomationService(settings_repository=repo).run_once(
        actor="legacy-display"
    )
    assert result["skipped"]
    assert result["execution"]["actor_user_id"] is None
    assert result["execution"]["trigger"] == "manual"


def test_api_uses_authenticated_principals_for_policy_and_manual_run(monkeypatch):
    from src.apps.rule_governance.backend import api
    from src.auth.models import CurrentUser

    user = CurrentUser(user_id="real-admin", username="display", role="admin")
    request = SimpleNamespace(state=SimpleNamespace(current_user=user))
    repo, service = Mock(), Mock()
    repo.status.return_value = {}
    monkeypatch.setattr(api, "RuleAssetAutomationSettingsRepository", lambda: repo)
    monkeypatch.setattr(api, "RuleAssetAutomationService", lambda: service)
    api.set_rule_asset_automation_control(
        request,
        api.RuleAssetAutomationControlRequest(mode="generate_only", reason="开启候选生成流程"),
        user,
    )
    assert repo.set.call_args.kwargs["actor_user_id"] == "real-admin"
    api.run_rule_asset_automation_now(user)
    service.run_once.assert_called_once_with(actor="display", actor_user_id="real-admin")


def test_workflow_passes_identity_to_persistence_without_sending_it_to_generators():
    from src.core.workflows.expression import resolve_inputs
    from src.core.workflows.registry import get_template, load_workflows

    load_workflows(force=True)
    template = get_template("rule-asset-generation")
    identity = {"actor_user_id": "policy-owner", "trigger": "schedule", "policy_revision": "v1"}
    env = {"context": {"rule_generation_identity": identity}, "steps": {}, "run": {}}
    for step in template.steps:
        inputs = resolve_inputs(step.inputs, env)
        if step.skill_name == "rule-asset-persister":
            assert inputs["generation_identity"] == identity
        else:
            assert "generation_identity" not in inputs


def test_postgres_first_writers_preserve_policy_and_last_run(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from uuid import uuid4

    from src.infrastructure.db.postgres import get_db_session

    key = "test-automation-identity-" + uuid4().hex
    monkeypatch.setattr(automation, "AUTOMATION_SETTING_KEY", key)
    repo = automation.RuleAssetAutomationSettingsRepository()
    barrier = Barrier(2)

    def configure():
        barrier.wait(timeout=10)
        repo.set(
            {"mode": "generate_only"},
            actor="test-display",
            actor_user_id="test-owner",
            reason="test policy identity",
        )

    def record():
        barrier.wait(timeout=10)
        repo.record_run({"success": True, "execution": {"actor_user_id": "earlier-owner"}})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(configure), pool.submit(record)]
            for future in futures:
                future.result(timeout=15)
        result = repo.get()
        assert result["execution_user_id"] == "test-owner"
        assert result["mode"] == "generate_only"
        assert result["last_run"]["execution"]["actor_user_id"] == "earlier-owner"
        with get_db_session() as db:
            assert db.query(ParserControlSetting).filter_by(key=key).count() == 1
    finally:
        with get_db_session() as db:
            db.query(AuditLogRecord).filter_by(resource_id=key).delete()
            db.query(ParserControlSetting).filter_by(key=key).delete()


@pytest.fixture(autouse=True)
def app_admission_for_business_unit_tests(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock
    from src.apps.rule_governance.backend import access
    monkeypatch.setattr(access, "authorize", Mock())
    monkeypatch.setattr(access, "require_running", lambda: None)
    monkeypatch.setattr(access, "execution_lock", lambda **kwargs: nullcontext())
