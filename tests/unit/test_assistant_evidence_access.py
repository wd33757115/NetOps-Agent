from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.apps.assistant.backend import api, evidence, history, turns
from src.gateway import conversation_service
from tests.unit.test_assistant_package import database as database


def test_excerpt_requires_current_source_app_grant_and_is_not_leaked_by_history(
    database, monkeypatch
):
    conversation = history.create("alice")["id"]
    run, _ = turns.begin("alice", conversation, turns.Question(request_id="x", text="诊断"))
    reference = evidence.capture("inspection__read_snapshot", {"text": "protected-raw-evidence"})
    turns.finish("alice", run["id"], "completed", "历史诊断结论", [reference], 5)
    actor = SimpleNamespace(user_id="alice")
    public = api.conversation(conversation, actor)["messages"][-1]["references"][0]
    assert "excerpt" not in public
    monkeypatch.setattr(conversation_service, "get_db_session", database)
    legacy = conversation_service.ConversationService().get_messages(conversation)[-1][
        "references"
    ][0]
    assert "excerpt" not in legacy
    monkeypatch.setattr(api, "authorize_worker", lambda user, app: None)
    assert api.citation(conversation, reference["id"], actor)["excerpt"] == reference["excerpt"]

    def revoked(*args):
        raise HTTPException(403, "revoked")

    monkeypatch.setattr(api, "authorize_worker", revoked)
    with pytest.raises(HTTPException) as error:
        api.citation(conversation, reference["id"], actor)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        api.citation(conversation, reference["id"], SimpleNamespace(user_id="bob"))
    assert error.value.status_code == 404


@pytest.mark.parametrize("revoked_app", ["inspection", "device-access"])
def test_comparison_citation_requires_both_source_apps(database, monkeypatch, revoked_app):
    conversation = history.create("alice")["id"]
    run, _ = turns.begin("alice", conversation, turns.Question(request_id="compare", text="对比"))
    reference = evidence.capture("device_access__compare_config", {"baseline": "protected-config"})
    turns.finish("alice", run["id"], "completed", "配置对比", [reference], 5)

    def authorize(actor, app):
        if app == revoked_app:
            raise HTTPException(403, "revoked")

    monkeypatch.setattr(api, "authorize_worker", authorize)
    with pytest.raises(HTTPException) as error:
        api.citation(conversation, reference["id"], SimpleNamespace(user_id="alice"))
    assert error.value.status_code == 403


def test_comparison_final_answer_rechecks_both_source_apps(monkeypatch):
    from src.apps.assistant.backend.workspace import DocumentWorkspace

    reference = evidence.capture("device_access__compare_config", {"baseline": "protected-config"})
    workspace = DocumentWorkspace("alice", reader=object(), check=lambda: None)
    workspace.business_evidence[reference["id"]] = reference
    checked = []

    def authorize(actor, app):
        checked.append(app)
        if app == "inspection":
            raise HTTPException(403, "revoked")

    monkeypatch.setattr("src.apps.assistant.backend.workspace.authorize_worker", authorize)
    with pytest.raises(HTTPException) as error:
        workspace.references("配置变化 " + reference["citation"])
    assert error.value.status_code == 403
    assert checked == ["device-access", "inspection"]
