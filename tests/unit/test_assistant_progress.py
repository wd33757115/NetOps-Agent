import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.apps.assistant.backend import api, history, turns
from src.apps.assistant.backend.models import Turn
from src.apps.assistant.backend.repository import now
from src.platform.agent_runtime.base import AgentEvent
from tests.unit.test_assistant_package import database as database


def begin():
    conversation = history.create("alice")["id"]
    body = turns.Question(request_id="one", text="检查接口")
    return turns.begin("alice", conversation, body)[0], body


def event(run, kind, **data):
    return AgentEvent(run["id"], kind, data)


def test_progress_persists_for_each_answer_without_raw_payloads(database):
    run, body = begin()

    class Runtime:
        async def start(self, context, messages, tools):
            yield event(run, "run_started")
            yield event(run, "status", message="正在分析问题")
            yield event(
                run,
                "tool_started",
                call_id="one",
                tool="read",
                detail="接口检查",
                operation='read({"file_path": "manual/line-1.txt", "offset": 1, "limit": 20})',
                arguments={"secret": "PRIVATE"},
                reasoning="PRIVATE",
            )
            yield event(
                run,
                "tool_finished",
                call_id="one",
                success=True,
                value="PRIVATE",
                summary="读取 20 行",
            )
            yield event(run, "text_delta", text="回答")
            yield event(run, "completed")

    events = []
    result = asyncio.run(
        turns.execute("alice", run, body, agent_runtime=Runtime(), on_event=events.append)
    )
    assert all(e.kind == "progress" for e in events)
    assert events[2].data["steps"][0]["status"] == "running"
    assert result["progress"]["steps"][0]["status"] == "completed"
    assert '"offset": 1' in result["progress"]["steps"][0]["operation"]
    assert result["progress"]["steps"][0]["summary"] == "读取 20 行"
    assert result["progress"]["ended_at"]
    assert "PRIVATE" not in json.dumps(result)
    turns.begin("alice", run["conversation_id"], turns.Question(request_id="two", text="下一题"))
    saved = api.conversation(run["conversation_id"], SimpleNamespace(user_id="alice"))
    assert saved["messages"][1]["progress"] == result["progress"]
    with pytest.raises(HTTPException):
        api.conversation(run["conversation_id"], SimpleNamespace(user_id="bob"))


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "interrupted"])
def test_terminal_closes_unfinished_tool_and_cannot_be_overwritten(database, terminal):
    run, _ = begin()
    start = event(run, "tool_started", call_id="same", tool="grep")
    turns.record_progress("alice", run["id"], start)
    turns.record_progress("alice", run["id"], start)
    if terminal == "cancelled":
        turns.cancel("alice", run["id"])
        turns.finish("alice", run["id"], "completed", "late", [], 4)
    elif terminal == "interrupted":
        with database() as session:
            session.get(Turn, run["id"]).expires_at = now() - timedelta(seconds=1)
    else:
        turns.finish("alice", run["id"], terminal, "", [], 4)
    result = turns.read("alice", run["id"])
    assert result["status"] == terminal
    assert len(result["progress"]["steps"]) == 1
    assert result["progress"]["steps"][0]["status"] == "interrupted"
    turns.record_progress(
        "alice", run["id"], event(run, "tool_finished", call_id="same", success=True)
    )
    assert turns.read("alice", run["id"])["progress"] == result["progress"]


def test_legacy_rows_without_progress_remain_readable(database):
    run, _ = begin()
    with database() as session:
        assert session.get(Turn, run["id"]).progress_json is None
    assert turns.read("alice", run["id"])["progress"]["steps"] == []


def test_output_limit_reason_is_shared_by_history_progress_and_chat(database):
    from src.apps.assistant.backend import progress
    from src.gateway.chat_stream import result

    run, _ = begin()
    turns.record_progress(
        "alice",
        run["id"],
        event(
            run,
            "metrics",
            model_finish_reason="length",
            sdk_finish_reason="error",
            max_output_tokens=8192,
        ),
    )
    turns.finish(
        "alice", run["id"], "failed", "PRIVATE partial config", [], 5, "model_output_truncated"
    )
    saved = turns.read("alice", run["id"])
    expected = progress.ERRORS["model_output_truncated"]
    assert saved["progress"]["message"] == expected
    assert saved["progress"]["metrics"]["model_finish_reason"] == "length"
    assert history.messages("alice", run["conversation_id"])[-1]["content"] == expected + "。"
    with pytest.raises(HTTPException) as error:
        result(SimpleNamespace(user_id="alice"), saved)
    assert error.value.detail == expected
    assert "PRIVATE" not in str(saved)


def test_migration_preserves_existing_turns(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, text

    from src.platform.kernel.migrations import configuration, migrate, version_table
    from src.platform.kernel.registry import discover

    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.sqlite'}")
    manifest = discover()["assistant"]
    config, _ = configuration(manifest)
    with engine.begin() as connection:
        config.attributes.update(connection=connection, version_table=version_table(manifest))
        command.upgrade(config, "assistant_0001")
        connection.execute(
            text("""INSERT INTO app_assistant_turns
            (id, owner_id, conversation_id, request_id, input_digest, status,
             expires_at, created_at, event_count)
            VALUES ('old', 'alice', 'chat', 'old', 'digest', 'completed',
                    '2026-09-21 00:00:00', '2026-09-21 00:00:00', 5)""")
        )
    assert migrate(manifest, engine) == "assistant_0002"
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status, event_count, progress_json FROM app_assistant_turns WHERE id='old'"
            )
        ).one()
        assert tuple(row) == ("completed", 5, None)
    engine.dispose()
