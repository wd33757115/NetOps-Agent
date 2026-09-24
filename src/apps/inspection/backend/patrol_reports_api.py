"""Local report exports and explicit, user-scoped AI drafting consent."""

from hashlib import sha256
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from src.auth.models import CurrentUser
from src.core.patrol import report_drafting as drafting
from src.core.patrol.detail_data import load_detail_data, load_detail_source
from src.core.patrol.detail_workbook import render_detail_workbook
from src.core.patrol.report_evidence import build_excerpts
from src.core.patrol.report_findings import project_findings
from src.core.patrol.report_renderer import render_report
from src.core.patrol.report_service import report_context
from src.core.patrol.report_templates import (
    DEFAULT_TEMPLATE,
    MAX_UPLOAD,
    import_template,
    load_template,
    template_catalog,
)
from src.core.patrol.report_view import project_report

from .access import app_user
from .patrol.reference_evidence import reference_evidence

router = APIRouter(prefix="/api/v1/patrol", tags=["Patrol reports"])
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(default=DEFAULT_TEMPLATE, max_length=64)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=8)
    draft_id: str | None = Field(default=None, max_length=64)
    bundle: bool = False


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(default=DEFAULT_TEMPLATE, max_length=64)
    sections: list[Literal["narrative.summary", "changes.summary"]] = Field(
        default_factory=lambda: list(drafting.ALLOWED_SECTIONS), min_length=1, max_length=2
    )
    digest: str = Field(default="", max_length=64)


def context(job_id, user, loader=None):
    try:
        return (loader or report_context)(job_id, owner_id=user.user_id, is_admin=user.is_admin())
    except KeyError as exc:
        raise HTTPException(404, "巡检任务不存在") from exc
    except PermissionError as exc:
        raise HTTPException(403, "无权访问该巡检任务") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def checked(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/report-templates")
def templates(user: CurrentUser = Depends(app_user)):
    return template_catalog()


@router.get("/report-templates/{template_id}/file")
def download_template(template_id: str, user: CurrentUser = Depends(app_user)):
    return Response(
        checked(load_template, template_id),
        media_type=DOCX,
        headers={"Content-Disposition": 'attachment; filename="patrol-template.docx"'},
    )


@router.post("/report-templates")
async def upload_template(file: UploadFile = File(...), user: CurrentUser = Depends(app_user)):
    content = await file.read(MAX_UPLOAD + 1)
    return await run_in_threadpool(
        checked,
        import_template,
        content,
        (file.filename or "巡检模板").removesuffix(".docx"),
        user.user_id,
    )


@router.get("/offline-jobs/{job_id}/report")
def preview_report(job_id: str, user: CurrentUser = Depends(app_user)):
    report, source = context(job_id, user)
    view = project_findings(report, source, {}, project_report(report, source))
    excerpts = build_excerpts(report)
    return {
        "text": {k: view["scalars"][k] for k in drafting.ALLOWED_SECTIONS},
        "conclusion": view["scalars"]["conclusion.text"],
        "limitations": view["scalars"]["narrative.limitations"],
        "evidence_count": len(excerpts),
    }


@router.get("/offline-jobs/{job_id}/report/evidence")
def evidence(
    job_id: str,
    offset: int = Query(0, ge=0),
    reference: str | None = Query(None, min_length=1, max_length=256),
    user: CurrentUser = Depends(app_user),
):
    if reference is not None:
        result = context(job_id, user, lambda jid, **kw: reference_evidence(jid, reference, **kw))
        return {
            "total": len(result["items"]), "source_digest": result["source_digest"],
            "items": [{k: v for k, v in entry.items() if k != "full_source"}
                      for entry in result["items"][offset:offset + 10]],
        }
    report, _ = context(job_id, user)
    entries = build_excerpts(report)
    return {
        "total": len(entries),
        "source_digest": drafting.source_digest(report),
        "items": [
            {k: v for k, v in e.items() if k != "full_source"}
            for e in entries[offset : offset + 10]
        ],
    }


@router.get("/offline-jobs/{job_id}/report/details")
def detail_excel(job_id: str, user: CurrentUser = Depends(app_user)):
    source = context(job_id, user, load_detail_source)
    data = checked(load_detail_data, source)
    content = checked(render_detail_workbook, {}, source, data)
    return Response(
        content,
        media_type=XLSX,
        headers={"Content-Disposition": 'attachment; filename="patrol-details.xlsx"'},
    )


@router.get("/offline-jobs/{job_id}/report/evidence/{entry_id}/full")
def full_evidence(
    job_id: str,
    entry_id: str,
    source_digest: str = Query(..., min_length=64, max_length=64),
    reference: str | None = Query(None, min_length=1, max_length=256),
    user: CurrentUser = Depends(app_user),
):
    if reference is not None:
        result = context(job_id, user, lambda jid, **kw: reference_evidence(jid, reference, **kw))
        if result["source_digest"] != source_digest:
            raise HTTPException(409, "分析结果已变化，请重新加载证据列表")
        entry = next((entry for entry in result["items"] if entry["id"] == entry_id), None)
        if entry is None:
            raise HTTPException(404, "证据不存在")
        return entry["full_source"]
    report, _ = context(job_id, user)
    if drafting.source_digest(report) != source_digest:
        raise HTTPException(409, "分析结果已变化，请重新加载证据列表")
    entry = next((e for e in build_excerpts(report) if e["id"] == entry_id), None)
    if entry is None:
        raise HTTPException(404, "证据不存在")
    return entry["full_source"]


@router.post("/offline-jobs/{job_id}/report/export")
def export(job_id: str, options: Options, user: CurrentUser = Depends(app_user)):
    if any(
        k
        not in {
            "project",
            "period_label",
            "recipient",
            "issuer",
            "author",
            "reviewer",
            "report_number",
            "revision",
        }
        or len(v) > 256
        for k, v in options.metadata.items()
    ):
        raise HTTPException(400, "报告信息字段无效或过长")
    report, source = context(job_id, user)
    template = checked(load_template, options.template_id)
    drafts = (
        checked(
            drafting.accepted_text,
            options.draft_id,
            job_id,
            user.user_id,
            report,
            options.template_id,
            sha256(template).hexdigest(),
        )
        if options.draft_id
        else {}
    )
    details = (
        checked(render_detail_workbook, report, source, checked(load_detail_data, source))
        if options.bundle and source.get("periods")
        else None
    )
    content = checked(
        render_report,
        template,
        report,
        source,
        options.metadata,
        drafts,
        bundle=options.bundle,
        detail_workbook=details,
    )
    suffix = "zip" if options.bundle else "docx"
    return Response(
        content,
        media_type="application/zip" if options.bundle else DOCX,
        headers={"Content-Disposition": f'attachment; filename="patrol-report.{suffix}"'},
    )


@router.post("/offline-jobs/{job_id}/report/drafts/preview")
def prepare(job_id: str, options: DraftRequest, user: CurrentUser = Depends(app_user)):
    report, _ = context(job_id, user)
    template = checked(load_template, options.template_id)
    return checked(
        drafting.prepare_draft,
        report,
        job_id,
        user.user_id,
        options.template_id,
        sha256(template).hexdigest(),
        options.sections,
    )


@router.post("/offline-jobs/{job_id}/report/drafts/{draft_id}/generate")
async def generate(
    job_id: str, draft_id: str, options: DraftRequest, user: CurrentUser = Depends(app_user)
):
    report, _ = await run_in_threadpool(context, job_id, user)
    template = await run_in_threadpool(checked, load_template, options.template_id)
    try:
        return await drafting.generate_draft(
            draft_id, job_id, user.user_id, options.digest, report, sha256(template).hexdigest()
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/offline-jobs/{job_id}/report/drafts/{draft_id}/accept")
def accept(
    job_id: str, draft_id: str, options: DraftRequest, user: CurrentUser = Depends(app_user)
):
    report, _ = context(job_id, user)
    template = checked(load_template, options.template_id)
    return checked(
        drafting.accept_draft, draft_id, job_id, user.user_id, report, sha256(template).hexdigest()
    )
