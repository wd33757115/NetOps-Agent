import asyncio
import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.apps.assistant.backend import api, history, turns
from src.apps.materials.backend import library
from src.apps.assistant.backend.models import Turn
from src.apps.assistant.backend.repository import now
from src.infrastructure.db.models import Conversation, Message
from src.platform.agent_runtime.dsh import DshRuntime
from src.platform.kernel.migrations import migrate
from src.platform.kernel.registry import discover
from tests.unit.test_dsh_runtime import response


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite'}")
    Conversation.__table__.create(engine)
    Message.__table__.create(engine)
    manifest = discover()["assistant"]
    migrate(manifest, engine)
    migrate(discover()["materials"], engine)
    assert migrate(manifest, engine, check=True) == "assistant_0002"

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    for module in (library, history, turns):
        monkeypatch.setattr(module, "sessions", sessions)
    monkeypatch.setattr(
        turns, "authorize_worker", lambda actor, app: SimpleNamespace(user_id=actor)
    )
    monkeypatch.setattr("src.platform.tools.registry.available_tools", lambda **kwargs: [])
    yield sessions
    engine.dispose()


def payload(text="# 接口\n执行 display interface 检查接口。", **kwargs):
    return library.ImportDocument(
        title="手册", text=text, metadata={"software_version": "8.2.1"}, **kwargs
    )


def test_immutable_revisions_and_stale_update(database):
    doc = library.import_document("alice", payload())
    reader = library.LibraryReader()
    hit = reader.search("接口", filters={"software_version": "8.2.1"})["items"][0]
    old = reader.read_section(doc["id"], doc["revision"], hit["section_id"])
    revised = library.import_document(
        "bob", payload("# 更新\n新的版本内容", expected_revision=doc["revision"]), doc["id"]
    )
    assert revised["revision"] != doc["revision"]
    reader.validate_citation(old["citation"])
    assert reader.load(doc["id"])[0] == revised["revision"]
    assert reader.search("接口")["items"] == []
    with pytest.raises(HTTPException) as exc:
        library.import_document("alice", payload(expected_revision=doc["revision"]), doc["id"])
    assert exc.value.status_code == 409
    with pytest.raises(ValueError):
        reader.load("../../private.md")


def test_conversations_are_owner_bound_and_not_legacy_claimable(database, monkeypatch):
    conversation = history.create("alice")
    with pytest.raises(HTTPException):
        history.messages("bob", conversation["id"])
    assert history.listing("bob") == []
    from src.gateway import conversation_service

    monkeypatch.setattr(conversation_service, "get_db_session", database)
    old = conversation_service.ConversationService()
    assert old.get_conversation(conversation["id"], "alice")["id"] == conversation["id"]
    assert old.get_conversation_for_user(conversation["id"], "bob") is None
    assert old.get_conversations("bob") == []


def test_request_deduplication_busy_cancel_and_no_late_completion(database):
    conversation = history.create("alice")["id"]
    body = turns.Question(request_id="one", text="接口")
    run, created = turns.begin("alice", conversation, body)
    assert created
    assert turns.begin("alice", conversation, body) == (run, False)
    with pytest.raises(HTTPException):
        turns.begin("alice", conversation, turns.Question(request_id="one", text="different"))
    with pytest.raises(HTTPException):
        turns.begin("alice", conversation, turns.Question(request_id="two", text="接口"))
    with pytest.raises(HTTPException):
        turns.cancel("bob", run["id"])
    assert turns.cancel("alice", run["id"])["status"] == "cancel_requested"
    turns.finish("alice", run["id"], "completed", "late answer", [{"id": "bad"}], 10)
    assert turns.read("alice", run["id"])["status"] == "cancelled"
    messages = history.messages("alice", conversation)
    assert len(messages) == 2 and "late" not in messages[-1]["content"]
    assert messages[-1]["references"] == []
    turns.finish("alice", run["id"], "completed", "duplicate", [], 10)
    assert len(history.messages("alice", conversation)) == 2


def test_crash_expiry_and_drain(database):
    conversation = history.create("alice")["id"]
    run, _ = turns.begin("alice", conversation, turns.Question(request_id="one", text="接口"))
    assert turns.pending() == 1
    turns.drain()
    assert turns.read("alice", run["id"])["status"] == "cancel_requested"
    with database() as session:
        row = session.get(Turn, run["id"])
        row.expires_at = now() - timedelta(seconds=1)
    assert turns.pending() == 0
    assert turns.read("alice", run["id"])["status"] == "interrupted"
    assert turns.begin("alice", conversation, turns.Question(request_id="two", text="接口"))[1]


class ReadingRuntime(DshRuntime):
    def __init__(self, *, delay=0):
        self.calls, self.delay = 0, delay
        super().__init__(transport=self.model)

    async def model(self, body):
        await asyncio.sleep(self.delay)
        self.calls += 1
        filename = next(iter(self.workspace.files))
        if self.calls == 1:
            return response(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "read1",
                            "type": "function",
                            "function": {
                                "name": "read",
                                "arguments": json.dumps(
                                    {"file_path": filename, "offset": 1, "limit": 2}
                                ),
                            },
                        }
                    ]
                },
                "tool_calls",
            )
        return response({"content": f"请检查接口 [{filename}:L1-L2]"}, "stop")

    async def start(self, context, messages, workspace):
        self.workspace = workspace
        async for event in super().start(context, messages, workspace):
            yield event


def test_real_service_persists_answer_and_opens_exact_citation(database):
    doc = library.import_document("alice", payload())
    conversation = history.create("alice")["id"]
    body = turns.Question(request_id="one", text="如何检查接口？")
    run, _ = turns.begin("alice", conversation, body)
    result = asyncio.run(turns.execute("alice", run, body, agent_runtime=ReadingRuntime()))
    assert result["status"] == "completed"
    messages = history.messages("alice", conversation)
    assert len(messages) == 2
    reference = messages[-1]["references"][0]
    library.import_document(
        "alice", payload("# 新版本\n改变内容", expected_revision=doc["revision"]), doc["id"]
    )
    assert (
        api.citation(conversation, reference["id"], SimpleNamespace(user_id="alice")) == reference
    )
    with pytest.raises(HTTPException):
        api.citation(conversation, reference["id"], SimpleNamespace(user_id="bob"))
    with pytest.raises(HTTPException):
        api.citation(conversation, "doc-fake", SimpleNamespace(user_id="alice"))


def test_revoked_user_cannot_publish_answer(database, monkeypatch):
    library.import_document("alice", payload())
    conversation = history.create("alice")["id"]
    body = turns.Question(request_id="one", text="接口")
    run, _ = turns.begin("alice", conversation, body)

    def denied(actor, app):
        raise HTTPException(403)

    monkeypatch.setattr(turns, "authorize_worker", denied)
    result = asyncio.run(turns.execute("alice", run, body, agent_runtime=ReadingRuntime()))
    assert result["status"] == "failed"
    assert history.messages("alice", conversation)[-1]["references"] == []


def test_routes_enforce_app_admission(database):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/apps/assistant")
    from src.apps.materials.backend import api as materials_api
    app.include_router(materials_api.router, prefix="/api/apps/materials")
    client = TestClient(app)
    assert client.get("/api/apps/assistant/documents").status_code == 401
    app.dependency_overrides[api.user] = lambda: SimpleNamespace(user_id="alice")
    app.dependency_overrides[materials_api.user] = lambda: SimpleNamespace(user_id="alice")
    doc = client.post("/api/apps/materials/documents", json=payload().model_dump())
    assert doc.status_code == 200
    row = doc.json()
    assert client.get(
        f"/api/apps/assistant/documents/{row['id']}/sections", params={"revision": row["revision"]}
    ).json()["items"]
    assert (
        client.post(
            "/api/apps/materials/documents", json={**payload().model_dump(), "actor_id": "bob"}
        ).status_code
        == 422
    )
    app.dependency_overrides[api.user] = lambda: (_ for _ in ()).throw(HTTPException(403))
    assert client.get("/api/apps/assistant/documents").status_code == 403


def test_cancel_persistent_turn_during_model_call(database):
    async def run():
        conversation = history.create("alice")["id"]
        body = turns.Question(request_id="slow", text="question")
        row, _ = turns.begin("alice", conversation, body)
        task = asyncio.create_task(
            turns.execute("alice", row, body, agent_runtime=ReadingRuntime(delay=5))
        )
        await asyncio.sleep(0.05)
        turns.cancel("alice", row["id"])
        result = await asyncio.wait_for(task, 3)
        assert result["status"] == "cancelled"
        assert turns.pending() == 0

    asyncio.run(run())


def test_model_configuration_failure_finishes_immediately(database, monkeypatch):
    def invalid():
        raise ValueError("invalid model config")

    monkeypatch.setattr(turns, "runtime", invalid)
    conversation = history.create("alice")["id"]
    body = turns.Question(request_id="invalid", text="question")
    row, _ = turns.begin("alice", conversation, body)
    result = asyncio.run(turns.execute("alice", row, body))
    assert result["status"] == "failed"
    assert turns.pending() == 0
