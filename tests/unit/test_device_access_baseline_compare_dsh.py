"""Official DSH compares real stored/live fixtures and persists an authorized citation."""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.apps.assistant.backend import api, evidence, history, turns
from src.apps.assistant.backend.diagnostic_workspace import DiagnosticWorkspace
from src.apps.assistant.backend.models import Turn
from src.apps.materials.backend.documents import DocumentReader
from src.infrastructure.db.models import CommandSnapshot, Conversation, Message
from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime
from src.platform.tools import registry
from tests.unit.test_device_access_baseline_compare import COMMAND, HOST, ROUTES
from tests.unit.test_device_access_baseline_compare import config_lab as config_lab
from tests.unit.test_dsh_runtime import response

COMPARE = "mcp__netops__device_access__compare_baseline"
SUBMIT = "mcp__netops__submit_answer"


def tool_call(name, arguments, identifier):
    return response(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": identifier,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        },
        "tool_calls",
    )


def test_dsh_comparison_reads_sqlite_runs_audited_fixture_and_persists_citation(
    config_lab, tmp_path, monkeypatch
):
    with config_lab.database() as session:
        for model in (Conversation, Message, Turn):
            model.__table__.create(session.get_bind())
    for module in (history, turns):
        monkeypatch.setattr(module, "sessions", config_lab.database)
    monkeypatch.setattr(api, "authorize_worker", config_lab.authorize)
    source = tmp_path / "source"
    source.mkdir()
    conversation_id = history.create("alice")["id"]
    run, _ = turns.begin(
        "alice", conversation_id, turns.Question(request_id="compare", text="比较接口配置")
    )
    workspace = DiagnosticWorkspace(
        "alice", run_id=run["id"], reader=DocumentReader(source), check=lambda: None
    )
    # Fixture for this turn's already-authorized topology observation; the model
    # still cannot supply platform, turn ID, arbitrary commands or a new host.
    workspace.widen({"collected": True, "address": HOST, "platform": "comware"})
    requests, answer = [], None

    async def model(body):
        nonlocal answer
        requests.append(body)
        tools = {tool["function"]["name"]: tool["function"] for tool in body["tools"]}
        assert COMPARE in tools and SUBMIT in tools
        assert "mcp__netops__inspection__launch" not in tools
        assert "mcp__netops__inspection__read_baseline" not in tools
        properties = tools[COMPARE]["parameters"]["properties"]
        assert not {"platform", "platforms", "turn_id"} & set(properties)
        assert "command" not in properties and "configuration" not in properties
        if len(requests) == 1:
            return tool_call(
                COMPARE,
                {"hosts": [HOST], "purpose": "核对基线与现场接口配置"},
                "compare",
            )
        assert len(requests) == 2
        visible = json.dumps(body["messages"], ensure_ascii=False)
        assert "baseline-config" in visible and "port access vlan 200" in visible
        citations = set(re.findall(r"evidence-[a-f0-9]{24}", visible))
        assert len(citations) == 1
        answer = (
            "指定离线基线与现场接口配置存在差异：VLAN 100 变为 200。"
            "这项配置差异尚不能证明故障根因。"
            f"[{next(iter(citations))}]"
        )
        return tool_call(SUBMIT, {"status": "answered", "answer": answer}, "submit")

    async def start():
        runtime = DshRuntime(transport=model)
        events = [
            event
            async for event in runtime.start(
                RunContext(run["id"], "alice", conversation_id, timeout_seconds=25),
                [{"role": "user", "content": "比较指定基线与当前接口配置"}],
                workspace,
            )
        ]
        assert not runtime.active
        return events

    events = asyncio.run(start())
    assert events[-1].kind == "completed", [(event.kind, event.data) for event in events]
    assert len(requests) == 2
    assert sorted(config_lab.sent) == sorted([COMMAND, ROUTES])
    assert config_lab.connects == config_lab.closes == 1
    references = [event.data for event in events if event.kind == "citation"]
    assert len(references) == 1
    reference = evidence.validate(references[0])
    assert reference["tool"] == "device_access__compare_baseline"
    data = json.loads(reference["excerpt"])
    [config] = [row for row in data["devices"][0]["commands"] if row["command"] == COMMAND]
    assert config["status"] == "changed" and config["baseline"]["snapshot_id"] == "baseline-config"
    assert config["evidence_ref"].startswith("device-access:audit:")
    assert workspace.prepared_answer == (answer, references)
    turns.finish("alice", run["id"], "completed", answer, references, len(events))

    workspace.business_evidence.clear()
    with config_lab.database() as db:
        db.get(CommandSnapshot, "baseline-config").raw_text = "source later changed"
    actor = SimpleNamespace(user_id="alice")
    assert api.citation(conversation_id, reference["id"], actor) == reference
    public = api.conversation(conversation_id, actor)["messages"][-1]
    assert public["content"] == answer and "excerpt" not in public["references"][0]
    config_lab.grants.remove("inspection")
    with pytest.raises(HTTPException) as denied:
        api.citation(conversation_id, reference["id"], actor)
    assert denied.value.status_code == 403
    assert sorted(config_lab.sent) == sorted([COMMAND, ROUTES])


def test_comparison_is_only_in_scoped_diagnosis_and_launch_stays_closed(config_lab, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    workspace = DiagnosticWorkspace(
        "alice", run_id="scope-test", reader=DocumentReader(source), check=lambda: None
    )
    definitions = {tool["name"]: tool for tool in workspace.definitions()}
    assert definitions["device_access__compare_baseline"]["mutates"]
    assert "inspection__launch" not in definitions
    arguments = config_lab.body.model_dump(exclude={"platforms", "turn_id"})
    with pytest.raises(PermissionError):
        workspace.invoke("device_access__compare_baseline", arguments)
    with pytest.raises(PermissionError):
        workspace.invoke("inspection__launch", {})
    with pytest.raises(PermissionError, match="read tools only"):
        registry.invoke("inspection__launch", {}, actor_id="alice", read_only=True)
    assert config_lab.connects == 0
