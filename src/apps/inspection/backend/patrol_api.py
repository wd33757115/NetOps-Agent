"""Check-item patrol APIs for operational risks and remediation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .access import app_user
from src.auth.models import CurrentUser
from src.core.celery_tasks.tasks import (
    materialize_patrol_batch_report_task,
)
from src.core.check_items.report_manifest import ReportManifestService
from src.core.check_items.report_claims import ReportClaimService
from src.core.check_items.report_content_review import (
    REQUIRED_CONTENT_CONFIRMATIONS,
    ReportContentReviewService,
)
from src.core.check_items.report_release import (
    REQUIRED_DISCLOSURE_CONFIRMATIONS,
    ReportReleaseService,
)
from src.core.check_items.scope_repository import PatrolScopeRepository
from src.core.check_items.source_qualification import (
    PatrolSourceQualificationService,
)
from src.core.check_items.reporting import build_check_item_report_view
from src.core.check_items.production_feedback import (
    ProductionFeedbackConflictError,
    ProductionFeedbackService,
)
from src.core.check_items.risk import RiskCaseService, RiskConflictError
from src.core.check_items.risk_association import (
    RiskAssociationConflictError,
    RiskEpisodeAssociationService,
)
from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
from src.gateway.audit_service import write_audit_log

router = APIRouter(
    prefix="/api/v1/patrol",
    tags=["Patrol Risk Management"],
    dependencies=[Depends(app_user)],
)

_CONTENT_REVIEW_UNSET = object()


class RiskAcknowledgeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class RiskRecoveryConfirmRequest(BaseModel):
    recovery_candidate_id: str = Field(min_length=16, max_length=256)
    reason: str = Field(min_length=3, max_length=2000)


class RiskRemediationCreateRequest(BaseModel):
    action_text: str = Field(min_length=3, max_length=8000)
    owner: str | None = Field(default=None, max_length=128)
    due_at: datetime | None = None


class RiskRemediationUpdateRequest(BaseModel):
    status: Literal["in_progress", "completed", "cancelled"]
    reason: str = Field(min_length=3, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class RiskRemediationAcceptRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=100)


class RiskClosureRequest(BaseModel):
    disposition: Literal[
        "remediation_accepted",
        "no_remediation_required",
    ]
    reason: str = Field(min_length=3, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=100)


class RiskSuppressRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class RiskAssociationProposeRequest(BaseModel):
    related_risk_case_id: str = Field(min_length=8, max_length=64)
    relation_type: Literal[
        "same_root_cause",
        "causal_symptom",
        "shared_incident",
    ]
    reason: str = Field(min_length=3, max_length=4000)
    evidence_refs: list[str] = Field(min_length=1, max_length=100)


class RiskAssociationReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=3, max_length=4000)


class RiskAssociationRevokeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=4000)


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


class ReportContentReviewDecisionRequest(BaseModel):
    review_item_ids: list[str] = Field(min_length=1, max_length=200)
    decision: Literal["accepted", "rejected"]
    review_comment: str = Field(min_length=3, max_length=4000)


class ReportContentAcceptanceRequest(BaseModel):
    acceptance_comment: str = Field(min_length=3, max_length=4000)
    confirmations: list[
        Literal[
            "machine_reconciliation_reviewed",
            "critical_unknown_and_lifecycle_reviewed",
            "sampled_facts_reviewed",
            "traceability_verified",
            "narrative_readability_accepted",
            "actions_actionable",
        ]
    ] = Field(min_length=6, max_length=6)
    verified_artifact_sha256: dict[str, str] = Field(min_length=1)


class OfflineCurrentAttestationRequest(BaseModel):
    offline_job_id: str = Field(min_length=8, max_length=128)
    completeness_statement: str = Field(min_length=10, max_length=4000)
    completeness_evidence_refs: list[str] = Field(
        min_length=1,
        max_length=100,
    )
    collection_tool: str = Field(min_length=2, max_length=256)
    collector_identity: str = Field(min_length=2, max_length=256)


class ProductionFeedbackSubmitRequest(BaseModel):
    execution_id: str = Field(min_length=8, max_length=64)
    assessment_id: str = Field(min_length=8, max_length=64)
    feedback_type: Literal[
        "false_positive",
        "missed_detection",
        "wrong_severity",
        "wrong_scope",
        "expected_behavior",
        "evidence_quality_issue",
    ]
    statement: str = Field(min_length=3, max_length=4000)
    event_occurrence_id: str | None = Field(default=None, max_length=64)
    risk_occurrence_id: str | None = Field(default=None, max_length=64)


class ProductionFeedbackTriageRequest(BaseModel):
    outcome: Literal["confirmed", "not_confirmed", "inconclusive"]
    reason: str = Field(min_length=3, max_length=4000)
    work_item_kind: Literal["rule_sample", "assessment_policy", "data_quality"] | None = None


class ProductionFeedbackCandidateLinkRequest(BaseModel):
    candidate_type: Literal["offline_job", "assessment_policy_version", "asset_gap"]
    candidate_id: str = Field(min_length=8, max_length=128)


def _actor(user: CurrentUser) -> str:
    return str(user.username or user.user_id)


def _raise_risk_error(exc: ValueError) -> None:
    detail = str(exc)
    if "not found" in detail:
        raise HTTPException(status_code=404, detail=detail) from exc
    if isinstance(exc, (RiskConflictError, RiskAssociationConflictError)):
        raise HTTPException(status_code=409, detail=detail) from exc
    if "cannot" in detail or "transition" in detail:
        raise HTTPException(status_code=409, detail=detail) from exc
    raise HTTPException(status_code=422, detail=detail) from exc


def _raise_feedback_error(exc: ValueError) -> None:
    detail = str(exc)
    if "not found" in detail:
        raise HTTPException(status_code=404, detail=detail) from exc
    if isinstance(exc, ProductionFeedbackConflictError):
        raise HTTPException(status_code=409, detail=detail) from exc
    raise HTTPException(status_code=422, detail=detail) from exc


def _feedback_audit(
    request: Request,
    user: CurrentUser,
    *,
    action: str,
    resource_id: str,
    detail: dict,
    idempotency_key: str,
) -> None:
    write_audit_log(
        action=action,
        user_id=user.user_id,
        username=user.username,
        resource_type="production_feedback",
        resource_id=resource_id,
        detail=detail,
        ip_address=request.client.host if request.client else None,
        idempotency_key=(f"production-feedback:{action}:{resource_id}:{idempotency_key}"),
    )


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
        or "report content machine validation" in detail
        or "content review" in detail
        or "content acceptance" in detail
        or "content has not been accepted" in detail
        or "immutable" in detail
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
    content_review: dict | None | object = _CONTENT_REVIEW_UNSET,
) -> dict:
    report = build_check_item_report_view(manifest)
    if content_review is _CONTENT_REVIEW_UNSET:
        content_review = ReportContentReviewService().get_for_manifest(
            str(manifest.get("id") or "")
        )
    normalized_content_review = dict(content_review) if isinstance(content_review, dict) else None
    content_review_status = str((normalized_content_review or {}).get("status") or "not_started")
    return {
        "report_manifest_id": str(manifest.get("id") or ""),
        "patrol_batch_id": str(manifest.get("patrol_batch_id") or ""),
        "version": int(manifest.get("version") or 0),
        "report_purpose": report.get("report_purpose"),
        "report_purpose_label": report.get("report_purpose_label"),
        "report_purpose_disclosure": report.get("report_purpose_disclosure"),
        "current_state_authoritative": report.get("current_state_authoritative"),
        "report_authority_context": report.get("report_authority_context"),
        "conclusion_permission": report.get("conclusion_permission"),
        "facts_status": str(manifest.get("status") or ""),
        "superseded": str(manifest.get("status") or "") == "superseded",
        "release_status": (
            str(release.get("status") or "released")
            if release
            else (
                "pending_signoff"
                if str(manifest.get("status") or "") == "final"
                and content_review_status == "accepted"
                else "pending_content_acceptance"
                if str(manifest.get("status") or "") == "final"
                else "facts_not_final"
            )
        ),
        "report_cutoff_at": manifest.get("report_cutoff_at"),
        "fact_ingestion_high_watermark": manifest.get("fact_ingestion_high_watermark"),
        "late_fact_count": int(
            dict(manifest.get("source_facts_json") or {}).get(
                "late_fact_count",
                0,
            )
            or 0
        ),
        "source_facts_digest": str(manifest.get("source_digest") or ""),
        "claim_ledger_digest": str(manifest.get("claim_ledger_digest") or ""),
        "claim_count": int(manifest.get("claim_count") or 0),
        "raw_evidence_set_digest": str(manifest.get("raw_evidence_set_digest") or ""),
        "qualification_fact_digest": str(manifest.get("qualification_fact_digest") or ""),
        "coverage_fact_digest": str(manifest.get("coverage_fact_digest") or ""),
        "artifact_sha256": dict(manifest.get("artifact_sha256_json") or {}),
        "artifact_object_keys": dict(manifest.get("artifact_object_keys_json") or {}),
        "required_confirmations": sorted(REQUIRED_DISCLOSURE_CONFIRMATIONS),
        "content_review_status": content_review_status,
        "content_review": normalized_content_review,
        "required_content_confirmations": sorted(REQUIRED_CONTENT_CONFIRMATIONS),
        "conclusion": report.get("conclusion"),
        "conclusion_dimensions": report.get("conclusion_dimensions"),
        "coverage": report.get("coverage"),
        "risk_verification_counts": report.get("risk_verification_counts"),
        "blocking_reason_counts": report.get("blocking_reason_counts"),
        "release": release,
    }


@router.get("/batches/{patrol_batch_id}/reports/{report_manifest_id}/content-review")
def get_report_content_review(
    patrol_batch_id: str,
    report_manifest_id: str,
    _user: CurrentUser = Depends(app_user),
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
        return ReportContentReviewService().preview(report_manifest_id)
    except ValueError as exc:
        _raise_report_error(exc)


@router.post("/batches/{patrol_batch_id}/reports/{report_manifest_id}/content-review/start")
def start_report_content_review(
    patrol_batch_id: str,
    report_manifest_id: str,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
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
        review = ReportContentReviewService().start(
            report_manifest_id,
            created_by=_actor(user),
        )
    except ValueError as exc:
        _raise_report_error(exc)
    write_audit_log(
        action="patrol_report_content_review_start",
        user_id=user.user_id,
        username=user.username,
        resource_type="report_content_review",
        resource_id=str(review["id"]),
        detail={
            "patrol_batch_id": patrol_batch_id,
            "report_manifest_id": report_manifest_id,
            "review_plan_digest": review["review_plan_digest"],
            "required_item_count": review["required_item_count"],
        },
        ip_address=request.client.host if request.client else None,
        idempotency_key=(
            f"patrol:report-content-review-start:{report_manifest_id}:{idempotency_key}"
        ),
    )
    return {"success": True, "review": review}


@router.get("/batches/{patrol_batch_id}/reports/{report_manifest_id}/content-review/items")
def list_report_content_review_items(
    patrol_batch_id: str,
    report_manifest_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    review_status: Literal["pending", "accepted", "rejected"] | None = None,
    category: str | None = Query(default=None, max_length=64),
    _user: CurrentUser = Depends(app_user),
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
        return ReportContentReviewService().list_items(
            report_manifest_id,
            page=page,
            page_size=page_size,
            review_status=review_status,
            category=category,
        )
    except ValueError as exc:
        _raise_report_error(exc)


@router.post(
    "/batches/{patrol_batch_id}/reports/{report_manifest_id}/content-review/items/decision"
)
def decide_report_content_review_items(
    patrol_batch_id: str,
    report_manifest_id: str,
    payload: ReportContentReviewDecisionRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
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
        review = ReportContentReviewService().decide_items(
            report_manifest_id,
            review_item_ids=payload.review_item_ids,
            decision=payload.decision,
            reviewed_by=_actor(user),
            review_comment=payload.review_comment,
        )
    except ValueError as exc:
        _raise_report_error(exc)
    write_audit_log(
        action="patrol_report_content_review_decision",
        user_id=user.user_id,
        username=user.username,
        resource_type="report_content_review",
        resource_id=str(review["id"]),
        detail={
            "patrol_batch_id": patrol_batch_id,
            "report_manifest_id": report_manifest_id,
            "decision": payload.decision,
            "review_item_ids": payload.review_item_ids,
        },
        ip_address=request.client.host if request.client else None,
        idempotency_key=(
            f"patrol:report-content-review-decision:{report_manifest_id}:{idempotency_key}"
        ),
    )
    return {"success": True, "review": review}


@router.post("/batches/{patrol_batch_id}/reports/{report_manifest_id}/content-review/accept")
def accept_report_content_review(
    patrol_batch_id: str,
    report_manifest_id: str,
    payload: ReportContentAcceptanceRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
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
        review = ReportContentReviewService().accept(
            report_manifest_id,
            accepted_by=_actor(user),
            acceptance_comment=payload.acceptance_comment,
            confirmations=payload.confirmations,
            verified_artifact_sha256=payload.verified_artifact_sha256,
        )
    except ValueError as exc:
        _raise_report_error(exc)
    write_audit_log(
        action="patrol_report_content_acceptance",
        user_id=user.user_id,
        username=user.username,
        resource_type="report_content_review",
        resource_id=str(review["id"]),
        detail={
            "patrol_batch_id": patrol_batch_id,
            "report_manifest_id": report_manifest_id,
            "acceptance_digest": review["acceptance_digest"],
            "review_plan_digest": review["review_plan_digest"],
        },
        ip_address=request.client.host if request.client else None,
        idempotency_key=(
            f"patrol:report-content-acceptance:{report_manifest_id}:{idempotency_key}"
        ),
    )
    return {"success": True, "review": review}


@router.get("/batches/{patrol_batch_id}/report-manifest")
def get_batch_report_manifest(
    patrol_batch_id: str,
    _user: CurrentUser = Depends(app_user),
):
    manifest = ReportManifestService().latest_for_batch(patrol_batch_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="report manifest not found")
    release = ReportReleaseService().get_for_manifest(str(manifest["id"]))
    return _report_release_payload(manifest, release)


@router.get("/batches/{patrol_batch_id}/report-claims")
def get_batch_report_claims(
    patrol_batch_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    search: str | None = Query(default=None, max_length=256),
    claim_scope: Literal[
        "network",
        "site",
        "device_group",
        "device",
        "check_item",
        "entity",
    ]
    | None = None,
    assertion_level: Literal[
        "observed",
        "assessed",
        "current",
        "historical",
        "limited",
    ]
    | None = None,
    _user: CurrentUser = Depends(app_user),
):
    manifest = ReportManifestService().latest_for_batch(patrol_batch_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="report manifest not found")
    # Building the view verifies the source, permission and ledger digests
    # before exposing normalized claim rows.
    build_check_item_report_view(manifest)
    result = ReportClaimService().page_for_manifest(
        str(manifest["id"]),
        page=page,
        page_size=page_size,
        search=search,
        claim_scope=claim_scope,
        assertion_level=assertion_level,
    )
    expected_count = int(manifest.get("claim_count") or 0)
    if int(result.pop("persisted_total")) != expected_count:
        raise HTTPException(
            status_code=409,
            detail="persisted report claims do not match the manifest",
        )
    return {
        "report_manifest_id": str(manifest["id"]),
        "patrol_batch_id": patrol_batch_id,
        "claim_ledger_digest": str(manifest.get("claim_ledger_digest") or ""),
        "count": expected_count,
        **result,
    }


@router.get("/batches/{patrol_batch_id}/scope")
def get_batch_patrol_scope(
    patrol_batch_id: str,
    _user: CurrentUser = Depends(app_user),
):
    scope = PatrolScopeRepository().get_for_batch(patrol_batch_id)
    if scope is None:
        raise HTTPException(
            status_code=404,
            detail="patrol scope contract not found",
        )
    return scope


@router.post(
    "/batches/{patrol_batch_id}/source-qualification",
    deprecated=True,
)
def attest_batch_offline_current_source(
    patrol_batch_id: str,
    payload: OfflineCurrentAttestationRequest,
    request: Request,
    user: CurrentUser = Depends(app_user),
):
    del payload, request, user
    raise HTTPException(
        status_code=409,
        detail={
            "code": "legacy_source_qualification_write_disabled",
            "message": (
                "use the offline job current-authority workflow; batch qualification is read-only"
            ),
            "details": {"patrol_batch_id": patrol_batch_id},
        },
    )


@router.get("/batches/{patrol_batch_id}/source-qualification")
def get_batch_source_qualification(
    patrol_batch_id: str,
    _user: CurrentUser = Depends(app_user),
):
    try:
        qualification = PatrolSourceQualificationService().get_for_batch(patrol_batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if qualification is None:
        raise HTTPException(
            status_code=404,
            detail="patrol source qualification not found",
        )
    return qualification


@router.get("/batches/{patrol_batch_id}/reports")
def list_batch_report_versions(
    patrol_batch_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _user: CurrentUser = Depends(app_user),
):
    manifests = ReportManifestService().list_for_batch(
        patrol_batch_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    releases = {
        str(item.get("report_manifest_id") or ""): item
        for item in ReportReleaseService().list_for_batch(patrol_batch_id)
    }
    manifest_ids = [str(item.get("id") or "") for item in manifests["items"]]
    content_reviews = ReportContentReviewService().list_for_manifest_ids(manifest_ids)
    return {
        "patrol_batch_id": patrol_batch_id,
        "total": int(manifests["total"]),
        "page": page,
        "page_size": page_size,
        "items": [
            _report_release_payload(
                manifest,
                releases.get(str(manifest.get("id") or "")),
                content_reviews.get(str(manifest.get("id") or "")),
            )
            for manifest in manifests["items"]
        ],
    }


@router.post(
    "/batches/{patrol_batch_id}/reports/materialize",
    status_code=202,
)
def materialize_report(
    patrol_batch_id: str,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    repository = PatrolBatchManifestRepository()
    state_before = repository.report_generation_state(patrol_batch_id)
    if state_before is None:
        raise HTTPException(status_code=404, detail="patrol batch not found")
    task_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (f"patrol-report-materialize|{patrol_batch_id}|{idempotency_key}"),
        )
    )
    generated_by = f"api:{_actor(user)}"
    accepted = repository.request_terminal_report(
        patrol_batch_id,
        allow_regenerate=True,
        idempotency_key=idempotency_key,
        task_id=task_id,
        requested_by=generated_by,
    )
    current_state = repository.report_generation_state(patrol_batch_id) or state_before
    if not accepted:
        existing_request = repository.report_request_state(
            patrol_batch_id,
            idempotency_key,
        )
        if existing_request is not None:
            return {
                "success": True,
                "accepted": False,
                "reason": "idempotent_replay",
                **existing_request,
            }
        if current_state["status"] in {"queued", "generating"}:
            return {
                "success": True,
                "accepted": False,
                "reason": "already_in_progress",
                **current_state,
            }
        raise HTTPException(
            status_code=409,
            detail=(
                "patrol batch is not terminal or its frozen runtime facts "
                "are incomplete; report generation cannot start"
            ),
        )
    request_id = str(current_state.get("request_id") or "")
    if not request_id:
        repository.mark_report_status(
            patrol_batch_id,
            "failed",
            error="report materialization request ledger is missing",
        )
        raise HTTPException(
            status_code=500,
            detail="report materialization request ledger is missing",
        )
    try:
        materialize_patrol_batch_report_task.apply_async(
            args=[patrol_batch_id, generated_by, request_id],
            queue="netops.patrol",
            task_id=task_id,
        )
    except Exception as exc:
        repository.mark_report_status(
            patrol_batch_id,
            "failed",
            error=f"report worker dispatch failed: {exc}",
            request_id=request_id,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    write_audit_log(
        action="patrol_report_materialize_requested",
        user_id=user.user_id,
        username=user.username,
        resource_type="patrol_batch",
        resource_id=patrol_batch_id,
        detail={
            "patrol_batch_id": patrol_batch_id,
            "request_id": request_id,
            "task_id": task_id,
            "generated_by": generated_by,
        },
        ip_address=request.client.host if request.client else None,
        idempotency_key=(f"patrol:report-materialize:{patrol_batch_id}:{idempotency_key}"),
    )
    return {
        "success": True,
        "accepted": True,
        "request_id": request_id,
        "task_id": task_id,
        **(repository.report_generation_state(patrol_batch_id) or current_state),
    }


@router.get("/batches/{patrol_batch_id}/reports/materialization-status")
def get_report_materialization_status(
    patrol_batch_id: str,
    _user: CurrentUser = Depends(app_user),
):
    state = PatrolBatchManifestRepository().report_generation_state(patrol_batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="patrol batch not found")
    return {"success": True, **state}


@router.get("/batches/{patrol_batch_id}/reports/{report_manifest_id}/release")
def get_report_release(
    patrol_batch_id: str,
    report_manifest_id: str,
    _user: CurrentUser = Depends(app_user),
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


@router.post("/batches/{patrol_batch_id}/reports/{report_manifest_id}/release")
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
    user: CurrentUser = Depends(app_user),
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
            "artifact_sha256": dict(release.get("artifact_sha256_json") or {}),
        },
        ip_address=request.client.host if request.client else None,
        idempotency_key=(f"patrol:report-release:{report_manifest_id}:{idempotency_key}"),
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
    status: Literal["open", "acknowledged", "recovering", "resolved", "suppressed"] | None = None,
    severity: Literal["info", "warning", "minor", "major", "critical"] | None = None,
    remediation_status: Literal[
        "not_started",
        "planned",
        "in_progress",
        "completed",
        "accepted",
        "cancelled",
    ]
    | None = None,
    check_item_id: str | None = None,
    device_id: str | None = None,
    owner: str | None = None,
    overdue_only: bool = False,
    _user: CurrentUser = Depends(app_user),
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
    _user: CurrentUser = Depends(app_user),
):
    try:
        return RiskCaseService().get_case(
            risk_case_id,
            occurrence_page=occurrence_page,
            occurrence_page_size=occurrence_page_size,
        )
    except ValueError as exc:
        _raise_risk_error(exc)


@router.get("/risk-cases/{risk_case_id}/associations")
def list_risk_episode_associations(
    risk_case_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status: Literal["pending_review", "approved", "rejected", "revoked"] | None = None,
    _user: CurrentUser = Depends(app_user),
):
    return RiskEpisodeAssociationService().list_associations(
        risk_case_id=risk_case_id,
        status=status,
        page=page,
        page_size=page_size,
    )


@router.post("/risk-cases/{risk_case_id}/associations")
def propose_risk_episode_association(
    risk_case_id: str,
    payload: RiskAssociationProposeRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    try:
        association = RiskEpisodeAssociationService().propose(
            primary_risk_case_id=risk_case_id,
            related_risk_case_id=payload.related_risk_case_id,
            relation_type=payload.relation_type,
            reason=payload.reason,
            evidence_refs=payload.evidence_refs,
            proposed_by=_actor(user),
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_risk_error(exc)
    _audit(
        request,
        user,
        action="risk_episode_association_propose",
        risk_case_id=risk_case_id,
        detail={
            "association_id": association["id"],
            "related_risk_case_id": payload.related_risk_case_id,
            "relation_type": payload.relation_type,
            "reason": payload.reason,
            "evidence_ref_count": len(payload.evidence_refs),
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, "association": association}


@router.post("/risk-episode-associations/{association_id}/review")
def review_risk_episode_association(
    association_id: str,
    payload: RiskAssociationReviewRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    try:
        association = RiskEpisodeAssociationService().review(
            association_id,
            decision=payload.decision,
            reviewed_by=_actor(user),
            reason=payload.reason,
        )
    except ValueError as exc:
        _raise_risk_error(exc)
    primary_risk_case_id = str(association.get("primary_risk_case_id") or "")
    _audit(
        request,
        user,
        action="risk_episode_association_review",
        risk_case_id=primary_risk_case_id,
        detail={
            "association_id": association_id,
            "decision": payload.decision,
            "reason": payload.reason,
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, "association": association}


@router.post("/risk-episode-associations/{association_id}/revoke")
def revoke_risk_episode_association(
    association_id: str,
    payload: RiskAssociationRevokeRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    try:
        association = RiskEpisodeAssociationService().revoke(
            association_id,
            revoked_by=_actor(user),
            reason=payload.reason,
        )
    except ValueError as exc:
        _raise_risk_error(exc)
    primary_risk_case_id = str(association.get("primary_risk_case_id") or "")
    _audit(
        request,
        user,
        action="risk_episode_association_revoke",
        risk_case_id=primary_risk_case_id,
        detail={
            "association_id": association_id,
            "reason": payload.reason,
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, "association": association}


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
    user: CurrentUser = Depends(app_user),
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


@router.post("/risk-cases/{risk_case_id}/confirm-recovery")
def confirm_risk_recovery(
    risk_case_id: str,
    payload: RiskRecoveryConfirmRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    try:
        result = RiskCaseService().confirm_recovery(
            risk_case_id,
            recovery_candidate_id=payload.recovery_candidate_id,
            actor=_actor(user),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_risk_error(exc)
    confirmation = dict(result.get("confirmation") or {})
    _audit(
        request,
        user,
        action="risk_recovery_confirm",
        risk_case_id=risk_case_id,
        detail={
            "recovery_candidate_id": payload.recovery_candidate_id,
            "candidate_kind": confirmation.get("kind"),
            "execution_id": confirmation.get("execution_id"),
            "assessment_id": confirmation.get("assessment_id"),
            "event_occurrence_id": confirmation.get("event_occurrence_id"),
            "reason": payload.reason,
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, **result}


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
    user: CurrentUser = Depends(app_user),
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


@router.patch("/risk-cases/{risk_case_id}/remediations/{remediation_id}")
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
    user: CurrentUser = Depends(app_user),
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


@router.post("/risk-cases/{risk_case_id}/remediations/{remediation_id}/accept")
def accept_risk_remediation(
    risk_case_id: str,
    remediation_id: str,
    payload: RiskRemediationAcceptRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    try:
        remediation = RiskCaseService().accept_remediation(
            risk_case_id,
            remediation_id,
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
        action="risk_remediation_accept",
        risk_case_id=risk_case_id,
        detail={
            "remediation_id": remediation_id,
            "reason": payload.reason,
            "evidence_ref_count": len(payload.evidence_refs),
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, "remediation": remediation}


@router.post("/risk-cases/{risk_case_id}/close")
def close_risk_case(
    risk_case_id: str,
    payload: RiskClosureRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    try:
        risk_case = RiskCaseService().close_case(
            risk_case_id,
            actor=_actor(user),
            reason=payload.reason,
            disposition=payload.disposition,
            evidence_refs=payload.evidence_refs,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_risk_error(exc)
    _audit(
        request,
        user,
        action="risk_close",
        risk_case_id=risk_case_id,
        detail={
            "disposition": payload.disposition,
            "reason": payload.reason,
            "evidence_ref_count": len(payload.evidence_refs),
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, "risk_case": risk_case}


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
    user: CurrentUser = Depends(app_user),
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


@router.get("/production-feedback")
def list_production_feedback(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    status: Literal["submitted", "triaged", "work_item_created", "closed", "rejected"]
    | None = None,
    feedback_type: Literal[
        "false_positive",
        "missed_detection",
        "wrong_severity",
        "wrong_scope",
        "expected_behavior",
        "evidence_quality_issue",
    ]
    | None = None,
    runtime_bundle_version_id: str | None = None,
    patrol_batch_id: str | None = None,
    _user: CurrentUser = Depends(app_user),
):
    return ProductionFeedbackService().list_feedback(
        page=page,
        page_size=page_size,
        status=status,
        feedback_type=feedback_type,
        runtime_bundle_version_id=runtime_bundle_version_id,
        patrol_batch_id=patrol_batch_id,
    )


@router.post("/production-feedback")
def submit_production_feedback(
    payload: ProductionFeedbackSubmitRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    try:
        feedback = ProductionFeedbackService().submit(
            execution_id=payload.execution_id,
            assessment_id=payload.assessment_id,
            feedback_type=payload.feedback_type,
            statement=payload.statement,
            event_occurrence_id=payload.event_occurrence_id,
            risk_occurrence_id=payload.risk_occurrence_id,
            reported_by=_actor(user),
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_feedback_error(exc)
    _feedback_audit(
        request,
        user,
        action="submit",
        resource_id=str(feedback["id"]),
        detail={
            "feedback_type": payload.feedback_type,
            "execution_id": payload.execution_id,
            "assessment_id": payload.assessment_id,
            "event_occurrence_id": payload.event_occurrence_id,
            "risk_occurrence_id": payload.risk_occurrence_id,
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, "feedback": feedback}


@router.post("/production-feedback/work-items/{work_item_id}/candidate")
def link_production_feedback_candidate(
    work_item_id: str,
    payload: ProductionFeedbackCandidateLinkRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    try:
        work_item = ProductionFeedbackService().link_candidate(
            work_item_id,
            candidate_type=payload.candidate_type,
            candidate_id=payload.candidate_id,
            linked_by=_actor(user),
        )
    except ValueError as exc:
        _raise_feedback_error(exc)
    _feedback_audit(
        request,
        user,
        action="link_candidate",
        resource_id=work_item_id,
        detail={
            "candidate_type": payload.candidate_type,
            "candidate_id": payload.candidate_id,
            "candidate_ref_digest": work_item.get("candidate_ref_digest"),
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, "work_item": work_item}


@router.post("/production-feedback/{feedback_id}/triage")
def triage_production_feedback(
    feedback_id: str,
    payload: ProductionFeedbackTriageRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    user: CurrentUser = Depends(app_user),
):
    try:
        feedback = ProductionFeedbackService().triage(
            feedback_id,
            outcome=payload.outcome,
            reason=payload.reason,
            work_item_kind=payload.work_item_kind,
            triaged_by=_actor(user),
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_feedback_error(exc)
    _feedback_audit(
        request,
        user,
        action="triage",
        resource_id=feedback_id,
        detail={
            "outcome": payload.outcome,
            "work_item_kind": payload.work_item_kind,
        },
        idempotency_key=idempotency_key,
    )
    return {"success": True, "feedback": feedback}


@router.get("/production-feedback/context/{execution_id}")
def get_production_feedback_context(
    execution_id: str,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return ProductionFeedbackService().context_for_execution(execution_id)
    except ValueError as exc:
        _raise_feedback_error(exc)


@router.get("/production-feedback/{feedback_id}")
def get_production_feedback(
    feedback_id: str,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return ProductionFeedbackService().get(feedback_id)
    except ValueError as exc:
        _raise_feedback_error(exc)
