from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from src.auth.models import CurrentUser
from src.platform.authorization.client import require_app

from . import block_template, request_draft, service, storage
from .schemas import BlockRequest, LedgerDraft, LedgerUpload, PolicyRequest, TicketRequest
from .schemas import Request as RetryRequest

router = APIRouter(tags=["Firewall policy"])
actor = require_app("firewall-policy")


def attachment(data, filename, content_type):
    return Response(
        data,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
        },
    )


@router.get("/template")
def template(user: CurrentUser = Depends(actor)):
    path = Path(__file__).parents[1] / "resources/firewall-policy-request.xlsx"
    return attachment(
        path.read_bytes(),
        "policies.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def drafted(user, body):
    """The ledger's rows for one access; a missing ledger, grant or match is a plain 422."""
    try:
        return request_draft.build(user, body)
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/ledger")
def ledger(user: CurrentUser = Depends(actor)):
    return {"available": request_draft.available(user)}


@router.post("/ledger/preview")
def ledger_preview(body: LedgerDraft, user: CurrentUser = Depends(actor)):
    rows, notes, _ = drafted(user, body)
    return {"rows": rows, "notes": notes}


@router.post("/ledger/file")
def ledger_file(body: LedgerDraft, user: CurrentUser = Depends(actor)):
    _, _, data = drafted(user, body)
    return attachment(data, "策略申请表.xlsx", request_draft.XLSX)


@router.post("/ledger/uploads", status_code=201)
def ledger_upload(body: LedgerUpload, user: CurrentUser = Depends(actor)):
    """The drafted sheet saved as this user's upload, ready for an ordinary policy run."""
    rows, notes, data = drafted(user, body)
    saved = service.save_upload(
        user.user_id, f"{body.ticket_id}-策略申请表.xlsx", "policy", data, body.request_id
    )
    return {**saved, "rows": rows, "notes": notes}


@router.post("/uploads", status_code=201)
async def upload(
    request: Request,
    filename: str = Query(max_length=255),
    kind: str = Query(pattern="^(policy|topology|block|whitelist)$"),
    request_id: str = Query(min_length=1, max_length=100),
    user: CurrentUser = Depends(actor),
):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > storage.MAX_UPLOAD:
            raise HTTPException(413, "文件不可超过 10 MB")
    return await run_in_threadpool(
        service.save_upload, user.user_id, filename, kind, bytes(data), request_id
    )


@router.post("/runs", status_code=202)
def submit(body: PolicyRequest, user: CurrentUser = Depends(actor)):
    return service.submit_policy(user.user_id, body)


@router.get("/runs")
def history(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    kind: str = Query("policy", pattern="^(policy|block)$"),
    user: CurrentUser = Depends(actor),
):
    return service.history(user.user_id, offset, limit, kind)


@router.get("/block/template")
def block_list_template(user: CurrentUser = Depends(actor)):
    return attachment(
        block_template.build(),
        "ip-block.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/block/devices")
def block_devices(user: CurrentUser = Depends(actor)):
    return {"items": service.block_devices()}


@router.post("/block/runs", status_code=202)
def submit_block(body: BlockRequest, user: CurrentUser = Depends(actor)):
    return service.submit_block(user.user_id, body)


@router.get("/runs/{run_id}")
def read(run_id: str, user: CurrentUser = Depends(actor)):
    return service.read(user.user_id, run_id)


@router.post("/runs/{run_id}/tickets", status_code=202)
def ticket(run_id: str, body: TicketRequest, user: CurrentUser = Depends(actor)):
    return service.submit_ticket(user.user_id, run_id, body)


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, user: CurrentUser = Depends(actor)):
    return service.cancel(user.user_id, run_id)


@router.post("/runs/{run_id}/retry", status_code=202)
def retry(run_id: str, body: RetryRequest, user: CurrentUser = Depends(actor)):
    return service.retry(user.user_id, run_id, body)


@router.get("/runs/{run_id}/artifacts/{key}")
def download(run_id: str, key: str, user: CurrentUser = Depends(actor)):
    return attachment(*service.download(user.user_id, run_id, key))
