import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from src.platform.authorization.client import require_app

from . import docsets, governance, import_service, library, model_aliases
from .docx_import import MAX_UPLOAD

router = APIRouter()
user = require_app("materials")
JOB = Path(pattern=r"^[0-9a-f]{32}$")
DOCUMENT = Path(pattern=r"^[0-9a-f]{32}$")
PAGE = Query(min_length=1, max_length=512)
REVISION = Query(None, pattern=r"^[0-9a-f]{64}$")


def figure_response(content):
    data, media_type = content
    return Response(
        data,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"},
    )


@router.post("/docsets/uploads")
def docset_upload(body: docsets.UploadRequest, actor=Depends(user)):
    return docsets.create_upload(actor.user_id, body)


@router.post("/docsets/imports")
def docset_start(body: docsets.StartImport, actor=Depends(user)):
    return docsets.start(body.job_id)


@router.get("/docsets/imports")
def docset_jobs(actor=Depends(user)):
    return docsets.jobs()


@router.get("/docsets/imports/{job_id}")
def docset_job(job_id: str = JOB, actor=Depends(user)):
    return docsets.job(job_id)


@router.get("/docsets/imports/{job_id}/pages")
def docset_staged_page(job_id: str = JOB, path: str = PAGE, actor=Depends(user)):
    return docsets.staged_page(job_id, path)


@router.get("/docsets/imports/{job_id}/titles")
def docset_staged_titles(
    job_id: str = JOB, q: str = Query("", max_length=100), actor=Depends(user)
):
    return docsets.staged_titles(job_id, q)


@router.get("/docsets/imports/{job_id}/model-suggestions")
def docset_staged_models(
    job_id: str = JOB, vendor: str = Query("", max_length=256), actor=Depends(user)
):
    return model_aliases.for_import(job_id, vendor)


@router.get("/docsets/imports/{job_id}/figures/{name}")
def docset_staged_figure(job_id: str = JOB, name: str = Path(max_length=80), actor=Depends(user)):
    return figure_response(docsets.staged_figure(job_id, name))


@router.post("/docsets/imports/{job_id}/publish")
def docset_publish(body: docsets.Publication, job_id: str = JOB, actor=Depends(user)):
    return docsets.request_publish(actor.user_id, job_id, body)


@router.delete("/docsets/imports/{job_id}")
def docset_cancel(job_id: str = JOB, actor=Depends(user)):
    return docsets.cancel(job_id)


@router.get("/docsets/{document_id}/toc")
def docset_toc(
    document_id: str = DOCUMENT,
    revision: str | None = REVISION,
    parent: int = Query(-1, ge=-1),
    actor=Depends(user),
):
    return docsets.toc(document_id, revision, parent)


@router.get("/docsets/{document_id}/toc/locate")
def docset_locate(
    document_id: str = DOCUMENT,
    path: str = PAGE,
    revision: str | None = REVISION,
    actor=Depends(user),
):
    return docsets.locate(document_id, path, revision)


@router.get("/docsets/{document_id}/pages")
def docset_page(
    document_id: str = DOCUMENT,
    path: str = PAGE,
    revision: str | None = REVISION,
    actor=Depends(user),
):
    return docsets.page(document_id, path, revision)


@router.get("/docsets/{document_id}/figures/{name}")
def docset_figure(
    document_id: str = DOCUMENT, name: str = Path(max_length=80), actor=Depends(user)
):
    return figure_response(docsets.figure(document_id, name))


@router.get("/docsets/{document_id}/model-suggestions")
def docset_models(document_id: str = DOCUMENT, actor=Depends(user)):
    return model_aliases.for_docset(document_id)


@router.get("/docsets/{document_id}/compare/page")
def docset_page_diff(
    document_id: str = DOCUMENT, path: str = PAGE, before: str = Query(pattern=r"^[0-9a-f]{64}$"),
    after: str = Query(pattern=r"^[0-9a-f]{64}$"), actor=Depends(user),
):
    return docsets.page_diff(document_id, path, before, after)


@router.get("/docsets/{document_id}/search")
def docset_search(
    document_id: str = DOCUMENT,
    q: str = Query(min_length=1, max_length=200),
    revision: str | None = REVISION,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    actor=Depends(user),
):
    return docsets.search(document_id, q, revision, offset, limit)


@router.post("/docsets/{document_id}/revisions/{revision}/source-link")
def docset_source_link(
    document_id: str = DOCUMENT,
    revision: str = Path(pattern=r"^[0-9a-f]{64}$"),
    actor=Depends(user),
):
    return docsets.source_link(document_id, revision)


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
