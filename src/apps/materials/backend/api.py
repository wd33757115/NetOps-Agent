import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from src.platform.authorization.client import require_app

from . import governance, import_service, library
from .docx_import import MAX_UPLOAD

router = APIRouter()
user = require_app("materials")


@router.get("/documents")
def documents(actor=Depends(user)):
    return library.documents(include_inactive=True)


@router.patch("/documents/{document_id}/status")
def change_status(document_id: str, body: governance.StatusChange, actor=Depends(user)):
    return governance.change_status(document_id, body, body.status)


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, body: governance.ExpectedDocument, actor=Depends(user)):
    return governance.change_status(document_id, body, "deleted")


@router.patch("/documents/{document_id}/metadata")
def update_metadata(document_id: str, body: governance.MetadataChange, actor=Depends(user)):
    return governance.update_metadata(actor.user_id, document_id, body)


@router.get("/documents/{document_id}/compare")
def compare(
    document_id: str,
    before: str,
    after: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=50),
    actor=Depends(user),
):
    return governance.compare(document_id, before, after, offset, limit)


@router.get("/documents/{document_id}/revisions")
def revisions(
    document_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    actor=Depends(user),
):
    return library.revisions(document_id, offset, limit)


async def upload_bytes(file):
    data = await file.read(MAX_UPLOAD + 1)
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Word文件须大于0字节且不超过16MB")
    return data


@router.post("/documents/preview")
async def preview_word(file: UploadFile = File(...), actor=Depends(user)):
    return await asyncio.to_thread(import_service.preview, await upload_bytes(file), file.filename)


@router.post("/documents/import")
async def import_word(
    file: UploadFile = File(...), options: str = Form(..., max_length=64000), actor=Depends(user)
):
    try:
        plan = import_service.Publication.model_validate_json(options)
        return await asyncio.to_thread(
            import_service.publish, actor.user_id, await upload_bytes(file), file.filename, plan
        )
    except ValidationError as exc:
        raise HTTPException(422, "导入参数无效") from exc


@router.get("/documents/{document_id}/revisions/{revision}/source")
def original_word(document_id: str, revision: str, actor=Depends(user)):
    filename, data = import_service.original(document_id, revision)
    return Response(
        data,
        media_type=import_service.MIME,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.post("/documents")
def create_document(body: library.ImportDocument, actor=Depends(user)):
    return library.import_document(actor.user_id, body)


@router.put("/documents/{document_id}")
def revise_document(document_id: str, body: library.ImportDocument, actor=Depends(user)):
    return library.import_document(actor.user_id, body, document_id)


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
