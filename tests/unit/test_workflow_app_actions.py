"""A Workflow step can call an App action (skill: app:<app-id>/<action>)."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from src.core.workflows import app_steps
from src.gateway import itsm_webhook_handler
from src.platform.tools import actions

RUN = SimpleNamespace(id="run-1", user_id="7")


def test_the_firewall_app_offers_its_two_actions():
    assert {"app:firewall-policy/generate_policy", "app:firewall-policy/write_change_ticket"} <= set(
        actions.catalog()
    )
    assert actions.parse("app:firewall-policy/generate_policy") == ("firewall-policy", "generate_policy")
    assert actions.parse("app:Firewall/x") is None
    assert actions.is_action("app:x/y") and not actions.is_action("config-diff-tool")


def test_an_action_needs_the_runs_user():
    with pytest.raises(PermissionError, match="需要运行所属的用户"):
        actions.invoke("app:firewall-policy/generate_policy", {}, actor_id=None, run_id="run-1")
    with pytest.raises(LookupError):
        actions.invoke("app:nowhere/none", {}, actor_id="7", run_id="run-1")


def test_files_become_step_artifacts(monkeypatch):
    stored = []
    monkeypatch.setattr(actions, "invoke", lambda reference, params, *, actor_id, run_id: {
        "success": True, "message": "已生成", "policy_run_id": "a" * 32,
        "files": {"config_zip": ("REQ-1-策略配置.zip", "application/zip", b"zip")},
    })
    monkeypatch.setattr(app_steps, "_store", lambda *args: stored.append(args) or {
        "file_key": "k", "download_url": "/api/artifacts/download/?key=k", "filename": args[3]})

    result = app_steps.run_app_step(RUN, 0, "policy_generation", "app:firewall-policy/generate_policy", {})

    assert result["success"] is True and result["policy_run_id"] == "a" * 32
    assert "files" not in result
    assert result["artifacts"]["config_zip"]["filename"] == "REQ-1-策略配置.zip"
    assert stored[0][:3] == ("run-1", "policy_generation", "config_zip")


@pytest.mark.parametrize("error, code", [
    (HTTPException(404, "应用不可用或当前账号没有使用权限"), "app_action_denied"),
    (PermissionError("应用动作需要运行所属的用户"), "app_action_unavailable"),
    (RuntimeError("策略生成失败：申请表为空"), "app_action_failed"),
])
def test_failures_end_the_step_with_a_reason(monkeypatch, error, code):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(actions, "invoke", fail)
    result = app_steps.run_app_step(RUN, 1, "change_ticket", "app:firewall-policy/write_change_ticket", {})
    assert result["success"] is False and result["failure_code"] == code
    assert result["error"]


def test_bad_inputs_name_the_field(monkeypatch):
    class Needs(BaseModel):
        policy_run_id: str

    def invalid(*args, **kwargs):
        Needs.model_validate({})

    monkeypatch.setattr(actions, "invoke", invalid)
    result = app_steps.run_app_step(RUN, 1, "change_ticket", "app:firewall-policy/write_change_ticket", {})
    assert result["failure_code"] == "app_action_invalid_input"
    assert "policy_run_id" in result["error"]


def test_a_webhook_without_an_account_is_refused(monkeypatch):
    started = []
    monkeypatch.setattr(itsm_webhook_handler.WorkflowEngine, "start",
                        lambda *args, **kwargs: started.append(kwargs) or "run-9")

    with pytest.raises(HTTPException) as refused:
        asyncio.run(itsm_webhook_handler.handle_itsm_webhook("demo-text-report", {"text": "x"}, None))
    assert refused.value.status_code == 401

    user = SimpleNamespace(user_id="7")
    response = asyncio.run(itsm_webhook_handler.handle_itsm_webhook(
        "demo-text-report", {"ticket_id": "DEMO-9", "text": "x"}, user))
    assert response.status_code == 202
    # The run belongs to the account, so its steps use its grants and it gets the notices.
    assert started == [{"source": "itsm_webhook", "user_id": "7"}]
