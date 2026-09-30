import json

import pytest
from fastapi import HTTPException

from src.apps.assistant.backend import history, turns
from src.platform.agent_runtime import timeline
from src.platform.agent_runtime.session_store import key
from tests.unit.test_agent_timeline import write_trace
from tests.unit.test_assistant_package import database as database


def test_owner_sees_what_the_model_said_before_each_round_of_calls(database, tmp_path, monkeypatch):
    monkeypatch.setattr(timeline, "SESSIONS", tmp_path)
    conversation = history.create("alice")["id"]
    run, _ = turns.begin("alice", conversation, turns.Question(request_id="think", text="诊断"))
    assert turns.thinking("alice", run["id"]) == {"steps": []}  # no trace yet

    runs = tmp_path / key(json.dumps(["alice", conversation])) / "scopes" / "scope" / "runs"
    runs.mkdir(parents=True)
    write_trace(runs / f"{key(run['id'])}.jsonl")
    assert turns.thinking("alice", run["id"]) == {
        "steps": [{"call_ids": ["c1", "c2"], "said": "先在网关上分段测试", "reasoning": "先分段"}]
    }
    with pytest.raises(HTTPException) as denied:
        turns.thinking("bob", run["id"])
    assert denied.value.status_code == 404
