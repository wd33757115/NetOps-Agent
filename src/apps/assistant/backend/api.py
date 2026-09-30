import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from src.apps.materials.backend import import_service, library
from src.platform.authorization.client import authorize_worker, require_app

from . import evidence, history, tool_outputs, turns

router = APIRouter()
user = require_app("assistant")


@router.get("/documents")
def documents(actor=Depends(user)):
    return library.documents()


@router.get("/documents/{document_id}/revisions/{revision}/source")
def original_word(document_id: str, revision: str, actor=Depends(user)):
    filename, data = import_service.original(document_id, revision)
    return Response(data, media_type=import_service.MIME, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
    })


@router.get("/documents/{document_id}/sections")
def sections(
    document_id: str,
    revision: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    actor=Depends(user),
):
    try:
        return library.LibraryReader().list_sections(
            document_id, revision, offset=offset, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(404, "资料修订不存在") from exc


@router.get("/documents/{document_id}/sections/{section_id}")
def section(
    document_id: str,
    section_id: str,
    revision: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(4000, ge=1, le=8000),
    actor=Depends(user),
):
    try:
        return library.LibraryReader().read_section(
            document_id, revision, section_id, offset=offset, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(404, "章节或分页位置不存在") from exc


@router.get("/conversations")
def conversations(actor=Depends(user)):
    return history.listing(actor.user_id)


@router.post("/conversations")
def create_conversation(actor=Depends(user)):
    return history.create(actor.user_id)


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: str, actor=Depends(user)):
    latest_turn = turns.latest(actor.user_id, conversation_id)
    summaries = turns.message_progress(actor.user_id, conversation_id)
    messages = history.messages(actor.user_id, conversation_id)
    for message in messages:
        tool_outputs.mark_available(summaries.get(message["id"]), message["references"])
        if latest_turn and latest_turn.get("result_message_id") == message["id"]:
            tool_outputs.mark_available(latest_turn["progress"], message["references"])
    return {
        "messages": [
            {
                **message,
                "references": evidence.public_references(message["references"]),
                "progress": summaries.get(message["id"]),
            }
            for message in messages
        ],
        "latest_turn": latest_turn,
    }


@router.post("/conversations/{conversation_id}/questions")
async def question(conversation_id: str, body: turns.Question, actor=Depends(user)):
    run, created = await asyncio.to_thread(turns.begin, actor.user_id, conversation_id, body)
    if not created:
        return run
    return await turns.execute(actor.user_id, run, body)


@router.post("/turns/{run_id}/cancel")
def cancel(run_id: str, actor=Depends(user)):
    return turns.cancel(actor.user_id, run_id)


@router.get("/turns/{run_id}/steps/{step_id}/output")
def step_output(run_id: str, step_id: str, actor=Depends(user)):
    return turns.step_output(actor.user_id, run_id, step_id)


@router.get("/conversations/{conversation_id}/citations/{citation_id}")
def citation(conversation_id: str, citation_id: str, actor=Depends(user)):
    for message in history.messages(actor.user_id, conversation_id):
        for reference in message["references"]:
            if reference.get("id") == citation_id:
                try:
                    if reference.get("kind") == "business":
                        for app_id in evidence.source_apps(reference):
                            authorize_worker(actor.user_id, app_id)
                        return evidence.validate(reference)
                    library.LibraryReader().validate_citation(reference)
                except ValueError as exc:
                    raise HTTPException(409, "引用原始修订不可用") from exc
                return reference
    raise HTTPException(404, "本会话未引用该片段")
