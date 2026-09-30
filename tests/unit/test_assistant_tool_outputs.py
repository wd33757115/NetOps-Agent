import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.apps.assistant.backend import api, evidence, history, tool_outputs, turns
from src.apps.assistant.backend.models import Turn
from src.apps.assistant.backend.repository import now
from src.platform.agent_runtime.base import AgentEvent
from tests.unit.test_assistant_package import database as database


def test_callback_links_server_snapshot_and_never_copies_callback_data():
    reference = evidence.capture("topology__locate", {"address": "1.1.1.2"})
    output = json.dumps(
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "data": "untrusted-client-value",
                            "citation": reference["citation"],
                        }
                    ),
                }
            ]
        }
    )
    found = tool_outputs.from_callback(
        "mcp__netops__topology__locate",
        output,
        {reference["id"]: reference},
    )
    assert found == reference and "untrusted-client-value" not in found["excerpt"]
    assert (
        tool_outputs.from_callback("topology__trace_path", output, {reference["id"]: reference})
        is None
    )
    assert tool_outputs.from_callback("topology__locate", output, {}) is None


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_protected_results_survive_finish_but_never_enter_public_progress(
    database, monkeypatch, terminal
):
    conversation = history.create("alice")["id"]
    body = turns.Question(request_id="output", text="诊断")
    run, _ = turns.begin("alice", conversation, body)
    reference = evidence.capture(
        "device_access__run",
        {
            "host": "10.10.1.1",
            "commands": [{"command": "display interface", "output": "protected-raw-output"}],
        },
    )

    class Runtime:
        async def start(self, context, messages, tools):
            yield AgentEvent(
                run["id"],
                "tool_started",
                {
                    "call_id": "device",
                    "tool": "mcp__netops__device_access__run",
                },
            )
            yield AgentEvent(
                run["id"],
                "tool_finished",
                {
                    "call_id": "device",
                    "success": True,
                    "evidence": reference,
                },
            )
            yield AgentEvent(run["id"], terminal, {})

    events = []
    result = asyncio.run(
        turns.execute("alice", run, body, agent_runtime=Runtime(), on_event=events.append)
    )
    assert result["progress"]["steps"][0]["has_output"]
    assert "protected-raw-output" not in json.dumps(result)
    assert "protected-raw-output" not in json.dumps([event.data for event in events])
    assert "protected-raw-output" not in json.dumps(
        api.conversation(conversation, SimpleNamespace(user_id="alice")), default=str
    )
    with database() as session:
        assert session.get(Turn, run["id"]).progress_json["outputs"]["device"] == reference
    assert "protected-raw-output" in turns.step_output("alice", run["id"], "device")["text"]
    with pytest.raises(HTTPException) as error:
        turns.step_output("bob", run["id"], "device")
    assert error.value.status_code == 404

    def revoked(actor, app):
        assert app == "device-access"
        raise HTTPException(403, "revoked")

    monkeypatch.setattr(turns, "authorize_worker", revoked)
    with pytest.raises(HTTPException) as error:
        turns.step_output("alice", run["id"], "device")
    assert error.value.status_code == 403


def test_business_preview_keeps_large_json_and_command_truncation_flags():
    reference = evidence.capture(
        "device_access__run",
        {
            "host": "10.10.1.1",
            "commands": [
                {"command": f"display {number}", "output": "x" * 3000, "output_truncated": True}
                for number in range(5)
            ],
            "token": "PRIVATE",
        },
    )
    result = tool_outputs.preview(reference)
    value = json.loads(result["text"])
    assert len(result["text"]) > 12000 and len(value["commands"]) == 5
    assert value["commands"][-1]["output_truncated"] and "PRIVATE" not in result["text"]
    assert not result["truncated"]


def test_comparison_step_rechecks_inspection_permission(database, monkeypatch):
    conversation = history.create("alice")["id"]
    run, _ = turns.begin("alice", conversation, turns.Question(request_id="compare", text="对比"))
    reference = evidence.capture("device_access__compare_baseline", {"baseline": "protected-config"})
    with database() as session:
        session.get(Turn, run["id"]).progress_json = {
            "steps": [{"id": "compare", "evidence_id": reference["id"], "status": "completed"}],
            "outputs": {"compare": reference},
        }

    checked = []

    def authorize(actor, app):
        checked.append(app)
        if app == "inspection":
            raise HTTPException(403, "revoked")

    monkeypatch.setattr(turns, "authorize_worker", authorize)
    with pytest.raises(HTTPException) as error:
        turns.step_output("alice", run["id"], "compare")
    assert error.value.status_code == 403
    assert checked == ["device-access", "inspection"]


def test_legacy_results_recover_only_unique_tool_and_time_matches(database):
    conversation = history.create("alice")["id"]
    run, _ = turns.begin("alice", conversation, turns.Question(request_id="legacy", text="诊断"))
    references = [evidence.capture("device_access__run", {"host": "10.10.1.1", "commands": []})]
    before, after = (
        (now() - timedelta(minutes=1)).isoformat(),
        (now() + timedelta(minutes=1)).isoformat(),
    )
    step = {
        "id": "device",
        "operation": "device_access__run({})",
        "status": "completed",
        "started_at": before,
        "ended_at": after,
    }
    with database() as session:
        session.get(Turn, run["id"]).progress_json = {
            "steps": [
                step,
                {
                    **step,
                    "id": "missing",
                    "operation": "topology__locate({})",
                },
            ]
        }
    turns.finish("alice", run["id"], "completed", "答案", references, 4)
    public = api.conversation(conversation, SimpleNamespace(user_id="alice"))
    steps = public["messages"][-1]["progress"]["steps"]
    assert steps[0]["has_output"] and not steps[1].get("has_output")
    assert (
        json.loads(turns.step_output("alice", run["id"], "device")["text"])["host"] == "10.10.1.1"
    )
    with pytest.raises(HTTPException) as error:
        turns.step_output("alice", run["id"], "missing")
    assert error.value.status_code == 404
    ambiguous = {"steps": [step, {**step, "id": "overlap"}]}
    assert tool_outputs.reference_for(step, ambiguous, references) is None
    assert tool_outputs.reference_for(step, {"steps": [step]}, references + references) is None
    stale = {**step, "ended_at": before}
    assert tool_outputs.reference_for(stale, {"steps": [stale]}, references) is None
