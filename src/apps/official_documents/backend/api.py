from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from src.platform.authorization.client import require_app
get_current_user = require_app("official-documents")
from src.auth.models import CurrentUser
from src.apps.official_documents.backend import service as svc
from src.apps.official_documents.backend.models import OfficialDocument as Document, OfficialDocumentVersion as Version, OfficialDocumentOperation as Operation
from src.apps.official_documents.backend.schemas import CreateRequest, SaveRequest, RevisionRequest, AssistantRequest
from pydantic import Field
from typing import Literal


def session():
    from src.infrastructure.db.postgres import get_db_session
    with get_db_session() as s:
        yield s


def enabled():
    if os.getenv("OFFICIAL_DOCUMENTS_ENABLED", "true").lower() == "false":
        raise HTTPException(404, "公文工作台未启用")


router = APIRouter(tags=["Official documents"], dependencies=[Depends(enabled)])


class MetadataRequest(RevisionRequest):
    action: Literal["finalize", "reopen"]


class RestoreRequest(RevisionRequest):
    version: int = Field(ge=1)


class ApplyRequest(RevisionRequest):
    issue_id: str | None = None


class ExportRequest(RevisionRequest):
    version: int = Field(ge=1)
    # Accept legacy clients; all new exports use the same unmarked Word format.
    draft: bool = False


@router.get("")
def list_documents(q: str = Query("", max_length=500), doc_type: str = "", status: str = "", deleted: bool = False, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    query = select(Document).where(Document.owner_id == user.user_id, Document.deleted_at.is_not(None) if deleted else Document.deleted_at.is_(None))
    if q:
        query = query.where(Document.title.contains(q, autoescape=True))
    if doc_type:
        query = query.where(Document.document_type == doc_type)
    if status:
        query = query.where(Document.status == status)
    total = s.scalar(select(func.count()).select_from(query.subquery()))
    rows = s.scalars(query.order_by(Document.updated_at.desc(), Document.id).offset(offset).limit(limit))
    return {"items": [svc.describe(s, d, content=False) for d in rows], "total": total}


@router.post("", status_code=201)
def create_document(body: CreateRequest, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    try:
        return svc.create(s, user.user_id, body)
    except IntegrityError:
        s.rollback()
        return svc.create(s, user.user_id, body)


@router.get("/templates")
def templates(user: CurrentUser = Depends(get_current_user)):
    from src.apps.official_documents.backend.templates import catalog
    return catalog()


@router.get("/{document_id}")
def detail(document_id: str, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    return svc.describe(s, svc.owned(s, document_id, user.user_id))


@router.put("/{document_id}/content")
def save(document_id: str, body: SaveRequest, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    return svc.save(s, svc.owned(s, document_id, user.user_id, lock=True), user.user_id, body)


@router.patch("/{document_id}")
def metadata(document_id: str, body: MetadataRequest, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    return svc.mutate(s, svc.owned(s, document_id, user.user_id, lock=True), user.user_id, body, body.action)


@router.delete("/{document_id}")
def delete(document_id: str, body: RevisionRequest, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    return svc.mutate(s, svc.owned(s, document_id, user.user_id, deleted=True, lock=True), user.user_id, body, "delete")


@router.post("/{document_id}/undelete")
def undelete(document_id: str, body: RevisionRequest, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    return svc.mutate(s, svc.owned(s, document_id, user.user_id, deleted=True, lock=True), user.user_id, body, "undelete")


@router.get("/{document_id}/versions")
def versions(document_id: str, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    doc = svc.owned(s, document_id, user.user_id)
    rows = s.scalars(select(Version).where(Version.document_id == doc.id).order_by(Version.version.desc()).limit(200))
    return [{"version": v.version, "change_source": v.change_source, "change_summary": v.change_summary, "created_at": v.created_at} for v in rows]


@router.get("/{document_id}/versions/{number}")
def version_detail(document_id: str, number: int, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    v = svc.version(s, svc.owned(s, document_id, user.user_id), number)
    return {"version": v.version, "content": v.content_json}


@router.post("/{document_id}/restore")
def restore(document_id: str, body: RestoreRequest, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    doc = svc.owned(s, document_id, user.user_id, lock=True)
    v = svc.version(s, doc, body.version)
    return svc.save(s, doc, user.user_id, SaveRequest(base_revision=body.base_revision, request_id=body.request_id, content=v.content_json), source="restore", summary=f"恢复版本 v{body.version}", force=True)


@router.get("/{document_id}/messages")
def messages(document_id: str, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    from src.infrastructure.db.models import Message
    doc = svc.owned(s, document_id, user.user_id)
    rows = list(s.scalars(select(Message).where(Message.conversation_id == doc.conversation_id, Message.is_deleted.is_(False)).order_by(Message.created_at.desc()).limit(100)))
    return [{"id": m.id, "role": m.role, "content": m.content} for m in reversed(rows)]


@router.get("/{document_id}/operations")
def operations(document_id: str, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    doc = svc.owned(s, document_id, user.user_id)
    return [svc.operation_data(o) for o in s.scalars(select(Operation).where(Operation.document_id == doc.id).order_by(Operation.created_at.desc()).limit(100))]


@router.post("/{document_id}/assistant", status_code=202)
def assistant(document_id: str, body: AssistantRequest, background: BackgroundTasks, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    op = svc.submit(s, svc.owned(s, document_id, user.user_id, lock=True), user.user_id, body)
    data = svc.operation_data(op)
    s.commit()
    from src.apps.official_documents.backend.operations import dispatch
    if data["status"] == "queued":
        background.add_task(dispatch, data["id"])
    return data


@router.get("/{document_id}/operations/{op_id}")
def operation(document_id: str, op_id: str, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    return svc.operation_data(svc.operation(s, svc.owned(s, document_id, user.user_id), op_id))


@router.post("/{document_id}/operations/{op_id}/apply")
def apply(document_id: str, op_id: str, body: ApplyRequest, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    doc = svc.owned(s, document_id, user.user_id, lock=True)
    try:
        return svc.accept(s, doc, user.user_id, svc.operation(s, doc, op_id, lock=True), body, body.issue_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{document_id}/operations/{op_id}/cancel")
def cancel(document_id: str, op_id: str, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    doc = svc.owned(s, document_id, user.user_id, lock=True)
    op = svc.operation(s, doc, op_id, lock=True)
    if op.status in ("queued", "running", "proposed", "needs_input"):
        op.status = "cancelled"
    return svc.operation_data(op)


@router.post("/{document_id}/exports", status_code=202)
def export(document_id: str, body: ExportRequest, background: BackgroundTasks, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    doc = svc.owned(s, document_id, user.user_id, lock=True)
    op = svc.submit(s, doc, user.user_id, body.model_copy(update={"draft": False}), kind="export", export_version=body.version)
    data = svc.operation_data(op)
    s.commit()
    from src.apps.official_documents.backend.operations import dispatch
    if data["status"] == "queued":
        background.add_task(dispatch, data["id"])
    return data


@router.get("/{document_id}/exports/{op_id}/download")
def download(document_id: str, op_id: str, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    doc = svc.owned(s, document_id, user.user_id)
    op = svc.operation(s, doc, op_id)
    if op.kind != "export" or op.status != "completed":
        raise HTTPException(409, "导出尚未完成")
    from src.infrastructure.storage.minio_client import get_minio_storage
    data = get_minio_storage().download_file(op.result_json["object_key"])
    if data is None:
        raise HTTPException(503, "文件暂不可用，请重新导出")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc.title[:100])}-v{op.base_version}.docx", "Cache-Control": "no-store"})


@router.get("/{document_id}/operations/{op_id}/events")
def events(document_id: str, op_id: str, request: Request, user: CurrentUser = Depends(get_current_user), s=Depends(session)):
    svc.operation(s, svc.owned(s, document_id, user.user_id), op_id)
    s.close()  # Each poll acquires its own short transaction.
    async def stream():
        from src.apps.official_documents.backend.operations import db_session
        previous = None
        for _ in range(150):
            try:
                await get_current_user(request)
                def read():
                    with db_session() as local:
                        return svc.operation_data(svc.operation(local, svc.owned(local, document_id, user.user_id), op_id))
                data = await asyncio.to_thread(read)
            except HTTPException:
                return
            encoded = json.dumps(jsonable_encoder(data), ensure_ascii=False)
            if encoded != previous:
                yield f"event: operation\ndata: {encoded}\n\n"
                previous = encoded
            else:
                yield ": heartbeat\n\n"
            if data["status"] not in ("queued", "running"):
                return
            await asyncio.sleep(2)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
