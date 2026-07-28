"""Check-item patrol APIs for operational risks and remediation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.auth.dependencies import get_current_user, require_role
from src.auth.models import CurrentUser
from src.core.check_items.report_manifest import ReportManifestService
from src.core.check_items.report_release import (
    REQUIRED_DISCLOSURE_CONFIRMATIONS,
    ReportReleaseService,
)
from src.core.check_items.reporting import build_check_item_report_view
from src.core.check_items.reporting import CheckItemReportMaterializer
from src.core.check_items.risk import RiskCaseService
from src.gateway.audit_service import write_audit_log


router = APIRouter(
    prefix="/api/v1/patrol",
    tags=["Patrol Risk Management"],
    dependencies=[Depends(require_role(["admin", "operator"]))],
)


class RiskAcknowledgeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class RiskRemediationCreateRequest(BaseModel):
    action_text: str = Field(min_length=3, max_length=8000)
    owner: str | None = Field(default=None, max_length=128)
    due_at: datetime | None = None


class RiskRemediationUpdateRequest(BaseModel):
    status: Literal["in_progress", "completed", "cancelled"]
    reason: str = Field(min_length=3, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class RiskSuppressRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class ReportReleaseRequest(BaseModel):
    signoff_comment: str = Field(min_length=3, max_length=4000)
    disclosure_confirmations: list[
        Literal[
            "facts_reviewed",
            "unknown_scope_disclosed",
            "artifact_digests_verified",
        ]
    ] = Field(min_length=3, max_length=3)
    verified_artifact_sha256: dict[str, str] = Field(min_length=1)


def _actor(user: CurrentUser) -> str:
    return str(user.username or user.user_id)


def _raise_risk_error(exc: ValueError) -> None:
    detail = str(exc)
    if "not found" in detail:
        raise HTTPException(status_code=404, detail=detail) from exc
    if "cannot" in detail or "transition" in detail:
        raise HTTPException(status_code=409, detail=detail) from exc
    raise HTTPException(status_code=422, detail=detail) from exc


def _raise_report_error(exc: ValueError) -> None:
    detail = str(exc)
    if "not found" in detail:
        raise HTTPException(status_code=404, detail=detail) from exc
    if (
        "only a final" in detail
        or "must be attached" in detail
        or "do not match" in detail
        or "already has" in detail
        or "batch identity mismatch" in detail
        or "formal report is blocked" in detail
    ):
        raise HTTPException(status_code=409, detail=detail) from exc
    raise HTTPException(status_code=422, detail=detail) from exc


def _audit(
    request: Request,
    user: CurrentUser,
    *,
    action: str,
    risk_case_id: str,
    detail: dict,
    idempotency_key: str,
) -> None:
    write_audit_log(
        action=action,
        user_id=user.user_id,
        username=user.username,
        resource_type="risk_case",
        resource_id=risk_case_id,
        detail=detail,
        ip_address=request.client.host if request.client else None,
        idempotency_key=f"risk:{action}:{risk_case_id}:{idempotency_key}",
    )


def _report_release_payload(
    manifest: dict,
    release: dict | None,
) -> dict:
    report = build_check_item_report_view(manifest)
    return {
        "report_manifest_id": str(manifest.get("id") or ""),
        "patrol_batch_id": str(manifest.get("patrol_batch_id") or ""),
        "facts_status": str(manifest.get("status") or ""),
        "release_status": (
            str(release.get("status") or "released")
            if release
            else (
                "pending_signoff"
                if str(manifest.get("status") or "") == "final"
                else "facts_not_final"
            )
        ),
        "report_cutoff_at": manifest.get("report_cutoff_at"),
        "source_facts_digest": str(manifest.get("source_digest") or ""),
        "qualification_fact_digest": str(
            manifest.get("qualification_fact_digest") or ""
        ),
        "coverage_fact_digest": str(
            manifest.get("coverage_fact_digest") or ""
        ),
        "artifact_sha256": dict(
            manifest.get("artifact_sha256_json") or {}
        ),
        "artifact_object_keys": dict(
            manifest.get("artifact_object_keys_json") or {}
        ),
        "required_confirmations": sorted(
            REQUIRED_DISCLOSURE_CONFIRMATIONS
        ),
        "conclusion": report.get("conclusion"),
        "coverage": report.get("coverage"),
        "risk_verification_counts": report.get(
            "risk_verification_counts"
        ),
        "blocking_reason_counts": report.get(
            "blocking_reason_counts"
        ),
        "release": release,
    }


@router.get("/batches/{patrol_batch_id}/report-manifest")
def get_batch_report_manifest(
    patrol_batch_id: str,
    _user: CurrentUser = Depends(get_current_user),
):
    manifest = ReportManifestService().latest_for_batch(patrol_batch_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="report manifest not found")
    release = ReportReleaseService().get_for_manifest(str(manifest["id"]))
    return _report_release_payload(manifest, release)


@router.post("/batches/{patrol_batch_id}/reports/materialize")
def materialize_report(
    patrol_batch_id: str,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        result = CheckItemReportMaterializer().materialize(
            patrol_batch_id,
            generated_by=f"api:{_actor(user)}",
        )
    except ValueError as exc:
        _raise_report_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    manifest = dict(result["manifest"])
    release = ReportReleaseService().get_for_manifest(
        str(manifest["id"])
    )
    write_audit_log(
        action="patrol_report_materialize",
        user_id=user.user_id,
        username=user.username,
        resource_type="report_manifest",
        resource_id=str(manifest["id"]),
        detail={
            "patrol_batch_id": patrol_batch_id,
            "source_facts_digest": str(manifest["source_digest"]),
            "artifact_sha256": dict(
                manifest.get("artifact_sha256_json") or {}
            ),
        },
        ip_address=request.client.host if request.client else None,
        idempotency_key=(
            f"patrol:report-materialize:{patrol_batch_id}:"
            f"{idempotency_key}"
        ),
    )
    return {
        "success": True,
        **_report_release_payload(manifest, release),
    }


@router.get(
    "/batches/{patrol_batch_id}/reports/{report_manifest_id}/release"
)
def get_report_release(
    patrol_batch_id: str,
    report_manifest_id: str,
    _user: CurrentUser = Depends(get_current_user),
):
    manifest = ReportManifestService().get(report_manifest_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="report manifest not found")
    if str(manifest.get("patrol_batch_id") or "") != patrol_batch_id:
        raise HTTPException(
            status_code=409,
            detail="report manifest batch identity mismatch",
        )
    release = ReportReleaseService().get_for_manifest(report_manifest_id)
    return _report_release_payload(manifest, release)


@router.post(
    "/batches/{patrol_batch_id}/reports/{report_manifest_id}/release"
)
def release_report(
    patrol_batch_id: str,
    report_manifest_id: str,
    payload: ReportReleaseRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(require_role(["admin"])),
):
    manifest = ReportManifestService().get(report_manifest_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="report manifest not found")
    if str(manifest.get("patrol_batch_id") or "") != patrol_batch_id:
        raise HTTPException(
            status_code=409,
            detail="report manifest batch identity mismatch",
        )
    try:
        release = ReportReleaseService().release(
            report_manifest_id,
            signed_by=_actor(user),
            signoff_comment=payload.signoff_comment,
            disclosure_confirmations=payload.disclosure_confirmations,
            verified_artifact_sha256=payload.verified_artifact_sha256,
        )
    except ValueError as exc:
        _raise_report_error(exc)
    write_audit_log(
        action="patrol_report_release",
        user_id=user.user_id,
        username=user.username,
        resource_type="report_release",
        resource_id=str(release["id"]),
        detail={
            "patrol_batch_id": patrol_batch_id,
            "report_manifest_id": report_manifest_id,
            "source_facts_digest": release["source_facts_digest"],
            "release_digest": release["release_digest"],
            "artifact_sha256": dict(
                release.get("artifact_sha256_json") or {}
            ),
        },
        ip_address=request.client.host if request.client else None,
        idempotency_key=(
            f"patrol:report-release:{report_manifest_id}:"
            f"{idempotency_key}"
        ),
    )
    return {
        "success": True,
        **_report_release_payload(manifest, release),
    }


@router.get("/risk-cases")
def list_risk_cases(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    search: str | None = None,
    status: Literal[
        "open", "acknowledged", "recovering", "resolved", "suppressed"
    ]
    | None = None,
    severity: Literal["info", "warning", "minor", "major", "critical"] | None = None,
    remediation_status: Literal[
        "not_started", "planned", "in_progress", "completed", "cancelled"
    ]
    | None = None,
    check_item_id: str | None = None,
    device_id: str | None = None,
    owner: str | None = None,
    overdue_only: bool = False,
    _user: CurrentUser = Depends(get_current_user),
):
    return RiskCaseService().list_cases(
        page=page,
        page_size=page_size,
        search=search,
        status=status,
        severity=severity,
        remediation_status=remediation_status,
        check_item_id=check_item_id,
        device_id=device_id,
        owner=owner,
        overdue_only=overdue_only,
    )


@router.get("/risk-cases/{risk_case_id}")
def get_risk_case(
    risk_case_id: str,
    occurrence_page: int = Query(default=1, ge=1),
    occurrence_page_size: int = Query(default=50, ge=1, le=100),
    _user: CurrentUser = Depends(get_current_user),
):
    try:
        return RiskCaseService().get_case(
            risk_case_id,
            occurrence_page=occurrence_page,
            occurrence_page_size=occurrence_page_size,
        )
    except ValueError as exc:
        _raise_risk_error(exc)


@router.post("/risk-cases/{risk_case_id}/acknowledge")
def acknowledge_risk_case(
    risk_case_id: str,
    payload: RiskAcknowledgeRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        result = RiskCaseService().acknowledge(
            risk_case_id,
            actor=_actor(user),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_risk_error(exc)
    _audit(
        request,
        user,
        action="risk_acknowledge",
        risk_case_id=risk_case_id,
        detail={"reason": payload.reason},
        idempotency_key=idempotency_key,
    )
    return {"success": True, "risk_case": result}


@router.post("/risk-cases/{risk_case_id}/remediation")
def create_risk_remediation(
    risk_case_id: str,
    payload: RiskRemediationCreateRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(get_current_user),
):
    owner = str(payload.owner or _actor(user)).strip()
    try:
        remediation = RiskCaseService().create_remediation(
            risk_case_id,
            action_text=payload.action_text,
            owner=owner,
            created_by=_actor(user),
            due_at=payload.due_at,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_risk_error(exc)
    _audit(
        request,
        user,
        action="risk_remediation_create",
        risk_case_id=risk_case_id,
        detail={
            "remediation_id": remediation["id"],
            "owner": owner,
            "due_at": payload.due_at.isoformat() if payload.due_at else None,
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, "remediation": remediation}


@router.patch(
    "/risk-cases/{risk_case_id}/remediations/{remediation_id}"
)
def update_risk_remediation(
    risk_case_id: str,
    remediation_id: str,
    payload: RiskRemediationUpdateRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        remediation = RiskCaseService().update_remediation(
            risk_case_id,
            remediation_id,
            status=payload.status,
            actor=_actor(user),
            reason=payload.reason,
            evidence_refs=payload.evidence_refs,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_risk_error(exc)
    _audit(
        request,
        user,
        action="risk_remediation_update",
        risk_case_id=risk_case_id,
        detail={
            "remediation_id": remediation_id,
            "status": payload.status,
            "reason": payload.reason,
            "evidence_ref_count": len(payload.evidence_refs),
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, "remediation": remediation}


@router.post("/risk-cases/{risk_case_id}/suppress")
def suppress_risk_case(
    risk_case_id: str,
    payload: RiskSuppressRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        result = RiskCaseService().suppress(
            risk_case_id,
            actor=_actor(user),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_risk_error(exc)
    _audit(
        request,
        user,
        action="risk_suppress",
        risk_case_id=risk_case_id,
        detail={"reason": payload.reason},
        idempotency_key=idempotency_key,
    )
    return {"success": True, "risk_case": result}
