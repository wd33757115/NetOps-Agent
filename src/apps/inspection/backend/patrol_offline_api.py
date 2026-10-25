"""Offline inspection and its current-authority lifecycle."""

from __future__ import annotations

from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from .access import app_user
from src.auth.models import CurrentUser
from src.core.check_items.current_authority import (
    COMPLETENESS_EVIDENCE_OPTIONS,
    CURRENT_AUTHORITY_REQUEST_SCHEMA,
    OBSERVED_AT_EVIDENCE_OPTIONS,
    CurrentAuthorityError,
    PatrolCurrentAuthorityService,
)
from src.gateway.offline_job_api import _audit, _detail_or_http, build_workspace_router

current_authority_routes = APIRouter()


class CurrentAuthorityPrepareRequest(BaseModel):
    completeness_statement: str = Field(default="", max_length=4000)
    completeness_evidence_refs: list[str] = Field(
        default_factory=list,
        max_length=20,
    )
    observed_at_evidence_refs: list[str] = Field(
        default_factory=list,
        max_length=20,
    )
    collection_tool: str = Field(default="", max_length=256)
    collector_identity: str = Field(default="", max_length=256)
    scope_contract: dict[str, Any] | None = None


class CurrentAuthorityAttestRequest(BaseModel):
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=5, max_length=4000)


class CurrentAuthorityRevokeRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=4000)


def _authority_job(job_id):
    from src.platform.evidence.offline_jobs import OfflinePatrolJobService

    try:
        detail = OfflinePatrolJobService().get_job(job_id, purpose="offline_patrol")
    except KeyError as exc:
        raise HTTPException(404, "巡检作业不存在") from exc
    if detail.get("report_intent") != "current_attestation":
        raise HTTPException(409, "此作业未提交当前证据核验")
    return detail


def _current_authority_error(exc: CurrentAuthorityError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail=exc.to_detail(),
    )


@current_authority_routes.get("/{job_id}/current-authority")
def get_offline_current_authority(
    job_id: str,
    user: CurrentUser = Depends(app_user),
):
    job = _authority_job(job_id)
    authority = PatrolCurrentAuthorityService().get_for_job(job_id)
    if authority is not None:
        return authority
    return {
        "schema_version": CURRENT_AUTHORITY_REQUEST_SCHEMA,
        "offline_job_id": job_id,
        "report_intent": str(job.get("report_intent") or "historical"),
        "status": "not_requested",
        "blocker_codes": [],
        "blockers": [],
        "proof_options": {
            "observed_at_evidence_refs": list(OBSERVED_AT_EVIDENCE_OPTIONS),
            "completeness_evidence_refs": list(COMPLETENESS_EVIDENCE_OPTIONS),
        },
    }


@current_authority_routes.post(
    "/{job_id}/current-authority/prepare",
    status_code=status.HTTP_201_CREATED,
)
def prepare_offline_current_authority(
    job_id: str,
    payload: CurrentAuthorityPrepareRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    _detail_or_http(job_id, user)
    try:
        result = PatrolCurrentAuthorityService().prepare(
            offline_job_id=job_id,
            requested_by_principal_id=user.user_id,
            requested_by_display=user.username,
            idempotency_key=idempotency_key,
            completeness_statement=payload.completeness_statement,
            completeness_evidence_refs=(payload.completeness_evidence_refs),
            observed_at_evidence_refs=(payload.observed_at_evidence_refs),
            collection_tool=payload.collection_tool,
            collector_identity=payload.collector_identity,
            scope_contract=payload.scope_contract,
        )
    except CurrentAuthorityError as exc:
        raise _current_authority_error(exc) from exc
    if result.get("idempotent_replay"):
        response.status_code = status.HTTP_200_OK
    _audit(
        request,
        user,
        "offline_patrol_current_authority_prepare",
        job_id,
        {
            "request_id": result.get("request_id"),
            "request_digest": result.get("request_digest"),
            "status": result.get("status"),
            "blocker_codes": result.get("blocker_codes"),
        },
    )
    return result


@current_authority_routes.post(
    "/{job_id}/current-authority/attest",
    status_code=status.HTTP_202_ACCEPTED,
)
def attest_offline_current_authority(
    job_id: str,
    payload: CurrentAuthorityAttestRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    _authority_job(job_id)
    try:
        result = PatrolCurrentAuthorityService().attest(
            offline_job_id=job_id,
            request_digest=payload.request_digest,
            attested_by_principal_id=user.user_id,
            attested_by_display=user.username,
            idempotency_key=idempotency_key,
            reason=payload.reason,
        )
    except CurrentAuthorityError as exc:
        raise _current_authority_error(exc) from exc
    _audit(
        request,
        user,
        "offline_patrol_current_authority_attest",
        job_id,
        {
            "request_id": result.get("request_id"),
            "qualification_id": result.get("qualification_id"),
            "candidate_batch_id": result.get("candidate_batch_id"),
            "status": result.get("status"),
        },
    )
    return result


@current_authority_routes.post("/{job_id}/current-authority/revoke")
def revoke_offline_current_authority(
    job_id: str,
    payload: CurrentAuthorityRevokeRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    _authority_job(job_id)
    try:
        result = PatrolCurrentAuthorityService().revoke(
            offline_job_id=job_id,
            revoked_by_principal_id=user.user_id,
            revoked_by_display=user.username,
            idempotency_key=idempotency_key,
            reason=payload.reason,
        )
    except CurrentAuthorityError as exc:
        raise _current_authority_error(exc) from exc
    _audit(
        request,
        user,
        "offline_patrol_current_authority_revoke",
        job_id,
        {
            "request_id": result.get("request_id"),
            "qualification_id": result.get("qualification_id"),
            "status": result.get("status"),
        },
    )
    return result


router = build_workspace_router(
    "offline_patrol",
    read_user=app_user,
    write_user=app_user,
    admission=app_user,
    extra_routes=current_authority_routes,
)


@router.get("/{job_id}/artifacts/saved-report")
def saved_report(job_id: str, user: CurrentUser = Depends(app_user)):
    from src.infrastructure.storage.minio_client import get_minio_storage

    detail = _detail_or_http(job_id, user)
    artifact = (detail.get("artifacts") or {}).get("report") or {}
    key = str(artifact.get("object_key") or "")
    if not key.startswith(f"inspection/offline/{job_id}/"):
        raise HTTPException(404, "报告尚未生成")
    content = get_minio_storage().download_file(key)
    if content is None:
        raise HTTPException(404, "报告文件不存在")
    from urllib.parse import quote

    name = quote(str(artifact.get("filename") or "patrol-report.md"))
    return Response(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"},
    )


# The specific saved artifact route must precede the generic kind route.
router.routes.insert(0, router.routes.pop())
