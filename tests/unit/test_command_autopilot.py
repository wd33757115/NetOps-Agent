from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items.assessment_policy import canonical_digest
from src.core.rule_control.command_autopilot import CommandAutopilotService, task_key
from src.core.rule_control.autopilot_authorization import require_autopilot_release
from src.infrastructure.db.models import Base, ParserControlSetting, RuleAssetCommandUnitRecord, CheckItemVersionRecord, ParserTemplateJob


@pytest.fixture
def environment(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'autopilot.db'}")
    Base.metadata.create_all(engine, tables=[ParserControlSetting.__table__, RuleAssetCommandUnitRecord.__table__, CheckItemVersionRecord.__table__, ParserTemplateJob.__table__])
    factory = sessionmaker(bind=engine)
    @contextmanager
    def scope():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
    with scope() as db:
        db.add(RuleAssetCommandUnitRecord(id="unit", offline_job_id="source", asset_key="asset", vendor="h3c", profile="s6850",
            canonical_command="display device", handling="evidence", asset_mode="observation_only", status="ready", current_stage="parser"))
        db.add(CheckItemVersionRecord(id="version", check_item_id="inventory", version=1, result_mode="evidence_only",
                                     assessment_mode="evidence", status="active", digest="human-digest", canonical_contract_json={}, report_projection="auto", created_by="human"))
    dispatcher = Mock()
    service = CommandAutopilotService(session_scope=scope, dispatch=dispatcher)
    inputs = {"unit": {"asset_key": "asset", "vendor": "h3c", "models": ["s6850"], "canonical_command": "display device"},
              "check_item_id": "inventory", "check_item_version_id": "version", "check_item_digest": "human-digest", "result_mode": "evidence_only"}
    service._inputs = Mock(return_value=inputs)
    yield service, scope, dispatcher
    engine.dispose()


def test_start_is_idempotent_and_never_changes_human_definition(environment):
    service, scope, dispatch = environment
    first = service.control("source", "unit", action="start", actor="admin", authorize_publish=True)
    second = service.control("source", "unit", action="start", actor="operator")
    assert first["run_id"] == second["run_id"]
    assert second["publish_grant"]["by"] == "admin"
    assert dispatch.call_count == 1
    assert "lease_token" not in first
    with scope() as db:
        assert db.get(CheckItemVersionRecord, "version").digest == "human-digest"


def test_pause_fences_inflight_result_and_next_step(environment):
    service, scope, dispatch = environment
    service.control("source", "unit", action="start", actor="admin", authorize_publish=True)
    def step(state, checkpoint):
        service.control("source", "unit", action="pause", actor="engineer")
        return {"stage": "projection", "outputs": {"incorrect": "late result"}}
    service.executor = SimpleNamespace(step=step)
    result = service.advance(task_key("source", "unit"))
    assert result["status"] == "paused"
    assert result["outputs"] == {}
    assert result["can_edit"]
    assert service.advance(task_key("source", "unit")) == {"skipped": True}
    assert dispatch.call_count == 1


def test_definition_drift_blocks_before_any_generator(environment):
    service, _, _ = environment
    service.control("source", "unit", action="start", actor="admin", authorize_publish=True)
    service._inputs.return_value = {**service._inputs.return_value, "check_item_digest": "new-human-definition"}
    service.executor = Mock()
    result = service.advance(task_key("source", "unit"))
    assert result["status"] == "needs_attention"
    service.executor.step.assert_not_called()


def test_changed_scope_revokes_old_admin_delegation(environment):
    service, _, _ = environment
    service.control("source", "unit", action="start", actor="admin", authorize_publish=True)
    service.control("source", "unit", action="pause", actor="operator")
    service._inputs.return_value = {**service._inputs.return_value, "check_item_version_id": "new-version"}
    result = service.control("source", "unit", action="resume", actor="operator")
    assert result["publish_grant"] is None
    assert result["stage"] == "rules"


def test_queue_failure_is_durable_and_recoverable(environment):
    service, _, dispatch = environment
    dispatch.side_effect = RuntimeError("broker unavailable")
    state = service.control("source", "unit", action="start", actor="operator")
    assert state["status"] == "queued" and state["error"]
    dispatch.side_effect = None
    assert service.recover() == 1


def test_manual_projection_requires_pause_and_invalidates_downstream(environment):
    service, scope, _ = environment
    service.control("source", "unit", action="start", actor="operator")
    with pytest.raises(ValueError, match="暂停"):
        service.edit_projection("source", "unit", proposal={"outputs": []}, actor="operator")
    service.control("source", "unit", action="pause", actor="operator")
    with scope() as db:
        row = db.get(ParserControlSetting, task_key("source", "unit"))
        row.value_json = {**row.value_json, "outputs": {"parser": {"template_id": "parser"}, "implementation": {"id": "old"}}}
    result = service.edit_projection("source", "unit", proposal={"outputs": []}, actor="operator")
    assert result["stage"] == "projection"
    assert result["outputs"]["projection"]["manual"]
    assert "implementation" not in result["outputs"]


@pytest.mark.parametrize("corruption", ["no_grant", "expired", "expired_lease", "scope", "paused", "wrong_asset", "wrong_token", "changed_check_item"])
def test_automatic_release_requires_live_scoped_admin_authority(environment, corruption):
    service, scope, _ = environment
    service.control("source", "unit", action="start", actor="admin", authorize_publish=True)
    key = task_key("source", "unit")
    with scope() as db:
        row = db.get(ParserControlSetting, key)
        state = {**row.value_json, "stage": "review", "status": "running", "lease_token": "secret", "lease_until": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(), "outputs": {"rules": {"bundle_id": "bundle"}}}
        if corruption == "no_grant": state["publish_grant"] = None
        if corruption == "expired": state["publish_grant"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        if corruption == "expired_lease": state["lease_until"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        if corruption == "scope": state["publish_grant"]["scope_digest"] = "forged"
        if corruption == "paused": state["status"] = "paused"
        if corruption == "changed_check_item": db.get(CheckItemVersionRecord, "version").digest = "different"
        row.value_json = state
    with scope() as db, pytest.raises(ValueError):
        require_autopilot_release(db, (key, "wrong" if corruption == "wrong_token" else "secret"), kind="rules",
                                 asset_id="other" if corruption == "wrong_asset" else "bundle")


def test_release_authority_is_machine_delegation_not_a_human_review(environment):
    service, scope, _ = environment
    service.control("source", "unit", action="start", actor="admin", authorize_publish=True)
    key = task_key("source", "unit")
    with scope() as db:
        row = db.get(ParserControlSetting, key)
        row.value_json = {**row.value_json, "stage": "review", "status": "running", "lease_token": "secret", "lease_until": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(), "outputs": {"rules": {"bundle_id": "bundle"}}}
    with scope() as db:
        authority = require_autopilot_release(db, (key, "secret"), kind="rules", asset_id="bundle")
    assert authority["mode"] == "administrator_delegated_automation"
    assert authority["authorized_by"] == "admin"
    assert "human_semantic_review" not in authority


def test_pause_invalidates_child_spec_and_parser_task_identity(environment):
    from src.core.parser.repository import ParserJobRepository, ParserJobStateError
    from unittest.mock import patch
    service, scope, _ = environment
    service.control("source", "unit", action="start", actor="admin", authorize_publish=True)
    with scope() as db:
        db.add(ParserTemplateJob(id="parser-job", job_type="GENERATE", vendor="h3c", profile="s6850", canonical_command="display device",
            output_signature="sample", idempotency_key="parser-job", status="running", celery_task_id="old-worker",
            agent_state_json={"parser_spec_suggestion": {"task_id": "old-suggestion", "status": "running"}}))
        row = db.get(ParserControlSetting, task_key("source", "unit"))
        inputs = {**row.value_json["inputs"], "unit": {**row.value_json["inputs"]["unit"], "parser_job_id": "parser-job"}}
        row.value_json = {**row.value_json, "stage": "parser", "inputs": inputs}
    service.control("source", "unit", action="pause", actor="operator")
    with scope() as db:
        job = db.get(ParserTemplateJob, "parser-job")
        assert job.status == "cancelled" and job.celery_task_id is None
    with patch("src.core.parser.repository.get_db_session", scope), pytest.raises(ParserJobStateError, match="stale"):
        ParserJobRepository().record_parser_spec_suggestion("parser-job", task_id="old-suggestion", status="ready", suggestion={})


def test_reconciled_template_does_not_revoke_admin_scope(environment):
    service, _, _ = environment
    first = service.control("source", "unit", action="start", actor="admin", authorize_publish=True)
    service.control("source", "unit", action="pause", actor="operator")
    service._inputs.return_value = {**service._inputs.return_value, "unit": {**service._inputs.return_value["unit"], "parser_template_id": "new-parser"}}
    result = service.control("source", "unit", action="resume", actor="operator")
    assert result["run_id"] == first["run_id"]
    assert result["publish_grant"] == first["publish_grant"]


def test_api_operator_cannot_grant_automatic_publication(monkeypatch):
    from fastapi import HTTPException
    from src.gateway import offline_patrol_api as api
    monkeypatch.setattr(api, "_detail_or_http", Mock())
    with pytest.raises(HTTPException) as denied:
        api.control_command_autopilot("source", "unit", api.CommandAutopilotControlRequest(action="authorize"),
                                      request=Mock(), user=SimpleNamespace(username="operator", is_admin=lambda: False))
    assert denied.value.status_code == 403
