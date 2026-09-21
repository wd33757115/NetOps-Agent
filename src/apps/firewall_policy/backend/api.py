from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from src.auth.models import CurrentUser
from src.platform.authorization.client import require_app

from . import service, storage
from .schemas import PolicyRequest, TicketRequest
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


@router.post("/uploads", status_code=201)
async def upload(
    request: Request,
    filename: str = Query(max_length=255),
    kind: str = Query(pattern="^(policy|topology)$"),
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
    user: CurrentUser = Depends(actor),
):
    return service.history(user.user_id, offset, limit)


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
