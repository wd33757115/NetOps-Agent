import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.apps.assistant.backend import history, turns
from src.apps.materials.backend import library
from src.gateway import chat_stream
from src.gateway.schemas import ChatRequest
from src.infrastructure.db.models import Conversation
from tests.unit.test_assistant_package import ReadingRuntime, payload
from tests.unit.test_assistant_package import database as database


def test_old_stream_and_rest_use_same_dsh_history(database, monkeypatch):
    monkeypatch.setattr(turns, "runtime", ReadingRuntime)
    library.import_document("alice", payload())
    actor = SimpleNamespace(user_id="alice")
    # Existing owned conversations remain usable without claiming an unowned conversation.
    conversation = history.create("alice")["id"]
    with database() as session:
        session.get(Conversation, conversation).status = "active"
    request = ChatRequest(query="接口检查", thread_id=conversation)
    run, body = chat_stream.prepare(request, actor)

    async def collect():
        return [e async for e in chat_stream.stream_chat(request, actor, run, body)]

    events = asyncio.run(collect())
    assert events[-1]["event"] == "final_answer", events[-1]
    final = json.loads(events[-1]["data"])
    progress = [json.loads(e["data"]) for e in events if e["event"] == "progress"]
    assert progress[0]["status"] == "running"
    assert any(p["steps"] and p["steps"][0]["status"] == "running" for p in progress)
    assert progress[-1]["status"] == "completed"
    assert progress[-1]["steps"][0]["status"] == "completed"
    assert final["thread_id"] == conversation
    assert final["references"] and final["agent_type"] == "assistant"
    result = asyncio.run(chat_stream.chat(request, actor))
    assert result.thread_id == conversation and result.references
    assert len(history.messages("alice", conversation)) == 4


def test_legacy_chat_cannot_read_another_owner(database):
    conversation = history.create("alice")["id"]
    request = ChatRequest(query="question", thread_id=conversation, user_id="alice")
    with pytest.raises(HTTPException) as error:
        chat_stream.prepare(request, SimpleNamespace(user_id="bob"))
    assert error.value.status_code == 404
    with database() as session:
        session.get(Conversation, conversation).user_id = None
    with pytest.raises(HTTPException):
        chat_stream.prepare(request, SimpleNamespace(user_id="alice"))


def test_legacy_file_path_does_not_enter_agent(database):
    with pytest.raises(HTTPException) as error:
        chat_stream.prepare(
            ChatRequest(query="read", uploaded_file_path="C:/private/file"),
            SimpleNamespace(user_id="alice"),
        )
    assert error.value.status_code == 422
