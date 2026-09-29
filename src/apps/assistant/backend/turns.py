"""Bounded HTTP chat turns, without a new job executor or automatic retries."""

import asyncio
import contextlib
import json
import logging
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from src.apps.materials.backend.documents import FILTERS, digest
from src.platform.agent_runtime.base import AgentEvent, RunContext
from src.platform.authorization.client import authority_session, authorize_worker

from . import history, progress
from .models import Turn
from .repository import now, sessions
from .workspace import DocumentWorkspace

ACTIVE = ("running", "cancel_requested")


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4000)
    filters: dict[str, str] = Field(default_factory=dict)
    diagnostic_sessions: list[str] = Field(default_factory=list, max_length=2)


def expire(session):
    session.execute(
        update(Turn)
        .where(Turn.status.in_(ACTIVE), Turn.expires_at < now())
        .values(status="interrupted", active_conversation=None, error_code="runtime_interrupted")
    )


def view(row):
    return {
        "progress": progress.snapshot(row),
        **{
            k: getattr(row, k)
            for k in (
                "id",
                "conversation_id",
                "status",
                "result_message_id",
                "error_code",
                "event_count",
            )
        },
    }


def record_progress(actor_id, run_id, event):
    with sessions() as session:
        row = session.scalar(
            select(Turn).where(Turn.id == run_id, Turn.owner_id == actor_id).with_for_update()
        )
        if row and row.status == "running":
            return progress.advance(row, event)
        return progress.snapshot(row) if row else None


def message_progress(actor_id, conversation_id):
    with sessions() as session:
        history.owned(session, actor_id, conversation_id)
        return {
            row.result_message_id: progress.snapshot(row)
            for row in session.scalars(
                select(Turn).where(
                    Turn.conversation_id == conversation_id,
                    Turn.owner_id == actor_id,
                    Turn.result_message_id.is_not(None),
                )
            )
        }


def begin(actor_id, conversation_id, body):
    if set(body.filters) - FILTERS or any(len(v) > 256 for v in body.filters.values()):
        raise HTTPException(422, "不支持的资料过滤条件")
    signature = digest(
        json.dumps(
            {"conversation": conversation_id, "text": body.text, "filters": body.filters,
             "diagnostic_sessions": body.diagnostic_sessions},
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    with sessions() as session:
        expire(session)
        conversation = history.owned(session, actor_id, conversation_id)
        existing = session.scalar(
            select(Turn).where(Turn.owner_id == actor_id, Turn.request_id == body.request_id)
        )
        if existing:
            if existing.input_digest != signature:
                raise HTTPException(409, "请求编号已用于不同问题")
            return view(existing), False
        try:
            with session.begin_nested():
                row = Turn(
                    id=uuid4().hex,
                    owner_id=actor_id,
                    conversation_id=conversation_id,
                    request_id=body.request_id,
                    input_digest=signature,
                    status="running",
                    active_conversation=conversation_id,
                    created_at=now(),
                    event_count=0,
                    expires_at=now() + timedelta(seconds=110),
                )
                session.add(row)
                session.flush()
        except IntegrityError as exc:
            raise HTTPException(409, "该会话已有正在运行的问题，请刷新后重试") from exc
        history.add(session, conversation_id, "user", body.text)
        conversation.status = "assistant"
        if conversation.title in {"新问答", "新对话"}:
            conversation.title = body.text[:60]
        conversation.updated_at = now()
        return view(row), True


def read(actor_id, run_id):
    with sessions() as session:
        expire(session)
        row = session.get(Turn, run_id)
        if not row or row.owner_id != actor_id:
            raise HTTPException(404, "运行不存在")
        return view(row)


def latest(actor_id, conversation_id):
    with sessions() as session:
        history.owned(session, actor_id, conversation_id)
        expire(session)
        row = session.scalar(
            select(Turn)
            .where(Turn.conversation_id == conversation_id)
            .order_by(Turn.created_at.desc())
            .limit(1)
        )
        return view(row) if row else None


def cancel(actor_id, run_id):
    with sessions() as session:
        row = session.get(Turn, run_id)
        if not row or row.owner_id != actor_id:
            raise HTTPException(404, "运行不存在")
        session.execute(
            update(Turn)
            .where(Turn.id == run_id, Turn.status == "running")
            .values(status="cancel_requested")
        )
    return read(actor_id, run_id)


def finish(actor_id, run_id, status, text, references, count, code=None):
    with sessions() as session:
        row = session.scalar(
            select(Turn).where(Turn.id == run_id, Turn.owner_id == actor_id).with_for_update()
        )
        if row is None or row.status not in ACTIVE:
            return
        if row.status == "cancel_requested":
            status, code = "cancelled", "cancelled"
        if status != "completed":
            text, references = (
                progress.ERRORS.get(code, progress.STATUS.get(status, "本次问答未完成")) + "。",
                [],
            )
        # Claim the terminal transition atomically against cancellation/drain.
        changed = session.execute(
            update(Turn)
            .where(Turn.id == run_id, Turn.status == row.status)
            .values(status=status, active_conversation=None, error_code=code, event_count=count)
        )
        if changed.rowcount != 1:
            return
        message_id = history.add(session, row.conversation_id, "assistant", text, references)
        row.status = status
        data = progress.snapshot(row)
        data["ended_at"] = progress.stamp()
        session.execute(
            update(Turn)
            .where(Turn.id == run_id)
            .values(result_message_id=message_id, progress_json=data)
        )
        logging.getLogger(__name__).info(
            "assistant.turn.finished",
            extra={
                "run_id": run_id,
                "status": status,
                "error_code": code,
                "metrics": data.get("metrics", {}),
            },
        )


def runtime():
    from src.platform.agent_runtime.dsh import DshRuntime

    return DshRuntime()


async def execute(actor_id, run, body, *, agent_runtime=None, on_event=None):
    async with authority_session():
        return await _execute(actor_id, run, body, agent_runtime=agent_runtime, on_event=on_event)


async def _execute(actor_id, run, body, *, agent_runtime=None, on_event=None):
    try:
        executor = agent_runtime if agent_runtime is not None else runtime()
    except Exception:
        await asyncio.to_thread(
            finish, actor_id, run["id"], "failed", "", [], 0, "model_configuration_unavailable"
        )
        return await asyncio.to_thread(read, actor_id, run["id"])
    context = RunContext(run["id"], actor_id, run["conversation_id"])

    def check():
        authorize_worker(actor_id, "assistant")
        if read(actor_id, run["id"])["status"] != "running":
            raise PermissionError("Turn no longer active")

    if body.diagnostic_sessions:
        from .diagnostic_workspace import DiagnosticWorkspace
        tools = DiagnosticWorkspace(actor_id, filters=body.filters, check=check,
            session_ids=body.diagnostic_sessions, run_id=run["id"])
    else:
        tools = DocumentWorkspace(actor_id, filters=body.filters, check=check)
    terminal, text, references, count, code = "failed", [], [], 0, "runtime_failed"

    async def monitor():
        while True:
            await asyncio.sleep(1)
            try:
                await asyncio.to_thread(authorize_worker, actor_id, "assistant")
                active = (await asyncio.to_thread(read, actor_id, run["id"]))["status"] == "running"
            except Exception:
                active = False
            if not active:
                if await executor.cancel(context.run_id, actor_id=actor_id):
                    return

    task = asyncio.create_task(monitor())
    try:
        rows = await asyncio.to_thread(history.messages, actor_id, run["conversation_id"])
        messages = []
        remaining = 16000
        for row in reversed(rows[-10:]):
            if len(row["content"]) > remaining:
                break
            remaining -= len(row["content"])
            messages.insert(0, {"role": row["role"], "content": row["content"]})
        await asyncio.to_thread(authorize_worker, actor_id, "assistant")
        if (await asyncio.to_thread(read, actor_id, run["id"]))["status"] != "running":
            raise PermissionError("Turn no longer active")
        async for event in executor.start(context, messages, tools):
            count += 1
            if event.kind in {"run_started", "status", "tool_started", "tool_finished", "metrics"}:
                data = await asyncio.to_thread(record_progress, actor_id, run["id"], event)
                if on_event is not None and data:
                    on_event(AgentEvent(run["id"], "progress", data))
            if event.kind == "citation":
                references.append(event.data)
            elif event.kind == "text_delta":
                text.append(event.data["text"])
            elif event.kind in {"completed", "failed", "cancelled"}:
                terminal, code = event.kind, event.data.get("code")
        # Revoke access even if it changed between the last tool and final answer.
        await asyncio.to_thread(authorize_worker, actor_id, "assistant")
    except asyncio.CancelledError:
        terminal, code = "cancelled", "request_disconnected"
    except Exception:
        terminal, code = "failed", "runtime_or_authorization_unavailable"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.to_thread(
            finish, actor_id, run["id"], terminal, "".join(text), references, count, code
        )
    return await asyncio.to_thread(read, actor_id, run["id"])


def drain():
    with sessions() as session:
        expire(session)
        session.execute(
            update(Turn).where(Turn.status == "running").values(status="cancel_requested")
        )


def pending():
    with sessions() as session:
        expire(session)
        return session.scalar(select(func.count()).select_from(Turn).where(Turn.status.in_(ACTIVE)))
