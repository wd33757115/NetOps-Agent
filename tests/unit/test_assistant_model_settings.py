"""Model and reasoning effort chosen per conversation, read by the server per turn."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.apps.assistant.backend import api, history, model_settings, turns
from src.platform.agent_runtime.base import AgentEvent, RunContext
from tests.unit.test_assistant_package import database as database  # noqa: F401
from tests.unit.test_dsh_runtime import response


def choose(conversation, model="deepseek-v4-pro", effort="high", actor="alice"):
    return model_settings.save(
        actor, conversation, model_settings.Choice(model=model, reasoning_effort=effort)
    )


def test_untouched_conversation_runs_the_default_with_thinking_off(database):
    conversation = history.create("alice")["id"]
    options = model_settings.options()
    assert {"deepseek-v4-flash", "deepseek-v4-pro"} <= {m["id"] for m in options["models"]}
    assert [e["id"] for e in options["reasoning_efforts"]] == ["off", "low", "high", "max"]
    assert options["default"]["reasoning_effort"] == "off"
    view = model_settings.get("alice", conversation)
    assert view["reasoning_effort"] == "off" and view["chosen"] is False
    assert model_settings.for_run("alice", conversation)[1] == "off"


def test_a_choice_applies_to_its_own_conversation_only(database):
    first, second = history.create("alice")["id"], history.create("alice")["id"]
    assert choose(first) == {
        "model": "deepseek-v4-pro", "reasoning_effort": "high", "chosen": True
    }
    assert model_settings.for_run("alice", first) == ("deepseek-v4-pro", "high")
    assert model_settings.for_run("alice", second)[1] == "off"
    # Choosing again replaces the choice.
    choose(first, model="deepseek-v4-flash", effort="off")
    assert model_settings.for_run("alice", first) == ("deepseek-v4-flash", "off")


def test_unknown_values_and_other_peoples_conversations_are_refused(database):
    conversation = history.create("alice")["id"]
    with pytest.raises(ValueError):
        choose(conversation, model="gpt-x")
    with pytest.raises(ValueError):
        choose(conversation, effort="medium")
    with pytest.raises(HTTPException) as missing:
        choose(conversation, actor="bob")
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as refused:
        api.choose_model(
            conversation,
            model_settings.Choice(model="gpt-x", reasoning_effort="off"),
            SimpleNamespace(user_id="alice"),
        )
    assert refused.value.status_code == 422


def test_a_turn_runs_with_its_conversations_choice(database):
    conversation = history.create("alice")["id"]
    choose(conversation, effort="max")
    seen = []

    class Runtime:
        async def start(self, context, messages, tools):
            seen.append(context)
            yield AgentEvent(context.run_id, "run_started", {"executor": "test"})
            yield AgentEvent(context.run_id, "text_delta", {"text": "好的"})
            yield AgentEvent(context.run_id, "completed", {})

    body = turns.Question(request_id="settings", text="你好")
    run, _ = turns.begin("alice", conversation, body)
    asyncio.run(turns.execute("alice", run, body, agent_runtime=Runtime()))
    assert (seen[0].model, seen[0].reasoning_effort) == ("deepseek-v4-pro", "max")


def test_a_reasoning_effort_turns_thinking_on_for_that_run_only(tmp_path, monkeypatch):
    from src.apps.assistant.backend.workspace import DocumentWorkspace
    from src.apps.materials.backend.documents import DocumentReader
    from src.platform.agent_runtime.dsh import (
        MAX_OUTPUT_TOKENS,
        THINKING_OUTPUT_TOKENS,
        DshRuntime,
    )

    monkeypatch.setattr(
        "src.apps.assistant.backend.workspace.registry.available_tools", lambda **kw: []
    )
    (tmp_path / "sources").mkdir()
    bodies = []

    async def model(body):
        bodies.append(body)
        return response({"content": "ok"}, "stop")

    async def run(**choice):
        catalog = DocumentWorkspace(
            "alice", reader=DocumentReader(tmp_path / "sources"), check=lambda: None
        )
        context = RunContext("r", "alice", "c", timeout_seconds=30, **choice)
        return [e async for e in DshRuntime(transport=model).start(context, [], catalog)]

    events = asyncio.run(run(model="deepseek-v4-pro", reasoning_effort="high"))
    assert events[-1].kind == "completed"
    thinking = bodies[-1]
    assert thinking["model"] == "deepseek-v4-pro"
    assert thinking["thinking"] == {"type": "enabled"} and thinking["reasoning_effort"] == "high"
    assert thinking["max_tokens"] == THINKING_OUTPUT_TOKENS
    metrics = next(e.data for e in events if e.kind == "metrics")
    assert (metrics["model"], metrics["reasoning_effort"]) == ("deepseek-v4-pro", "high")

    asyncio.run(run())
    plain = bodies[-1]
    assert plain["thinking"] != {"type": "enabled"} and "reasoning_effort" not in plain
    assert plain["max_tokens"] == MAX_OUTPUT_TOKENS


def test_an_unknown_effort_never_reaches_a_run():
    with pytest.raises(ValueError):
        RunContext("r", "alice", "c", reasoning_effort="medium")
