"""Legacy REST/SSE shapes backed by the same Assistant App and DSH turns."""

import asyncio
import contextlib
import json
from uuid import uuid4

from fastapi import HTTPException

from src.apps.assistant.backend import evidence, history, progress, turns
from src.gateway.schemas import ChatResponse


def prepare(request, user):
    if request.uploaded_file_path:
        raise HTTPException(422, "请用聊天输入框的附件按钮上传文件（存入网盘）")
    body = turns.Question(
        request_id=uuid4().hex, text=request.query, filters=request.metadata_filters or {},
        attachments=request.attachments,
    )
    conversation_id = request.thread_id or history.create(user.user_id)["id"]
    run, _ = turns.begin(user.user_id, conversation_id, body)
    return run, body


def result(user, run):
    if run["status"] != "completed":
        message = progress.ERRORS.get(run.get("error_code")) or progress.STATUS.get(
            run["status"], "本次问答未完成，请查看运行状态"
        )
        raise HTTPException(503, message)
    rows = history.messages(user.user_id, run["conversation_id"])
    message = next(row for row in rows if row["id"] == run["result_message_id"])
    return ChatResponse(
        response=message["content"],
        thread_id=run["conversation_id"],
        agent_type="assistant",
        references=evidence.public_references(message["references"]),
    )


async def chat(request, user):
    run, body = await asyncio.to_thread(prepare, request, user)
    completed = await turns.execute(user.user_id, run, body)
    return await asyncio.to_thread(result, user, completed)


def sse(event, data):
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


async def stream_chat(request, user, run, body):
    queue = asyncio.Queue()
    task = asyncio.create_task(turns.execute(user.user_id, run, body, on_event=queue.put_nowait))
    try:
        yield sse(
            "trace_start",
            {
                "conversation_id": run["conversation_id"],
                "thread_id": run["conversation_id"],
                "run_id": run["id"],
                "executor": "dsh",
            },
        )
        yield sse("progress", run["progress"])
        while not task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), 0.1)
            except TimeoutError:
                continue
            if event.kind == "progress":
                yield sse("progress", event.data)
        completed = await task
        yield sse("progress", completed["progress"])
        payload = (await asyncio.to_thread(result, user, completed)).model_dump()
        yield sse("final_answer", {**payload, "conversation_id": run["conversation_id"]})
    except HTTPException as exc:
        yield sse("error", {"message": exc.detail, "run_id": run["id"]})
    finally:
        if not task.done():
            await asyncio.to_thread(turns.cancel, user.user_id, run["id"])
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
