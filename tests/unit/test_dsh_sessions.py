import asyncio
import json
import pytest
from types import SimpleNamespace

from src.apps.assistant.backend.workspace import DocumentWorkspace
from src.apps.materials.backend.documents import DocumentReader
from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime
from src.platform.agent_runtime.session_store import native_logs
from src.platform.agent_runtime.session_store import session_home, scope_key
from tests.unit.test_dsh_runtime import response


def test_native_session_survives_two_sdk_processes(tmp_path, monkeypatch):
    source = tmp_path / "sources"
    source.mkdir()
    monkeypatch.setattr(
        "src.apps.assistant.backend.workspace.registry.available_tools", lambda **kw: []
    )
    requests = []

    async def model(body):
        requests.append(body)
        return response({"content": "remembered blue"}, "stop")

    async def run():
        messages = []
        for n, question in enumerate(["My test color is blue", "What was my test color?"]):
            messages.append({"role": "user", "content": question})
            catalog = DocumentWorkspace("alice", reader=DocumentReader(source), check=lambda: None)
            runtime = DshRuntime(transport=model, sessions_root=tmp_path / "sessions")
            events = [
                e
                async for e in runtime.start(
                    RunContext(f"run{n}", "alice", "conversation", timeout_seconds=20),
                    messages,
                    catalog,
                )
            ]
            assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
            messages.append({"role": "assistant", "content": "remembered blue"})

    asyncio.run(run())
    assert len(requests) == 2
    history = json.dumps(requests[1]["messages"])
    assert "My test color is blue" in history
    assert "remembered blue" in history
    assert history.count("What was my test color?") == 1
    assert history.count("My test color is blue") == 1
    assert list(native_logs(tmp_path / "sessions"))
    traces = list((tmp_path / "sessions").glob("*/scopes/*/runs/*.jsonl"))
    assert len(traces) == 2
    assert all("session.event" in p.read_text(encoding="utf8") for p in traces)


def test_session_isolation_lock_and_workspace_refresh(tmp_path):
    with session_home(tmp_path, "alice", "chat") as home:
        (home / "documents").mkdir()
        (home / "documents" / "old.txt").write_text("revoked")
        with pytest.raises(OSError):
            with session_home(tmp_path, "alice", "chat"):
                pass
        with session_home(tmp_path, "bob", "chat") as other:
            assert other != home
    with session_home(tmp_path, "alice", "chat") as resumed:
        assert resumed == home and not (resumed / "documents").exists()


def test_scope_changes_with_materials_permissions_and_connection_selection():
    catalog = SimpleNamespace(files={"v1/section": None}, filters={}, connection_ids=[])
    original = scope_key(catalog, ["read"])
    assert original != scope_key(catalog, ["read", "business"])
    catalog.files = {"v2/section": None}
    assert original != scope_key(catalog, ["read"])
    catalog.files = {"v1/section": None}
    catalog.connection_ids = ["device-a"]
    assert original != scope_key(catalog, ["read"])
