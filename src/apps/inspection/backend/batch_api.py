"""Inspection batches, evidence downloads and historical compatibility reads."""

from __future__ import annotations
import csv
import hashlib
import io
import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from src.auth.models import CurrentUser
from src.core.check_items.batch_results import (
    CheckItemBatchResultService,
)
from src.core.check_items.report_contract import raw_evidence_set_digest
from src.core.check_items.report_manifest import ReportManifestService
from src.core.check_items.report_release import ReportReleaseService
from src.core.check_items.reporting import (
    ReportArtifact,
    load_attached_report_artifact,
)
from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
from src.core.patrol.leadership_report import (
    build_leadership_report,
    render_leadership_docx,
    render_leadership_json,
)
from src.core.patrol.patrol_report_service import PatrolReportService
from src.core.patrol.reporting import render_patrol_batch_xlsx
from src.core.workflows.engine import WorkflowEngine
from src.core.workflows.patrol_batch_projection import (
    MaterializePatrolBatchReportHandler,
)
from src.core.workflows.patrol_batch_service import (
    build_patrol_batch_summary,
    iter_patrol_event_export_rows,
    iter_patrol_raw_snapshot_rows,
    list_patrol_raw_output_artifacts,
    list_patrol_run_raw_output_artifacts,
    list_patrol_snapshot_run_ids,
)
from src.core.workflows.repository import (
    list_workflow_runs,
    update_run_context,
)
from src.observability.deprecations import observe_legacy_path

from .access import app_user

router = APIRouter(prefix="/api/v1/workflows", dependencies=[Depends(app_user)])

ACTIVE_PATROL_RUN_STATUSES = {
    "pending",
    "queued",
    "running",
    "in_progress",
    "collecting",
    "dispatching",
}


class WorkflowRetryRequest(BaseModel):
    user_id: str | None = None
    thread_id: str | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    device_id: str | None = None


class PatrolBatchGovernanceRequest(BaseModel):
    reason: str | None = Field(default=None, description="归档、恢复或保留策略调整原因")
    keep_until: str | None = Field(default=None, description="可选保留到期时间，ISO8601 字符串")
    force: bool = Field(default=False, description="是否允许归档仍在运行的批次")


class PatrolBatchCancelRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Operator-provided reason recorded in the audit trail",
    )


def _run_summary_dict(run) -> dict[str, Any]:
    context = run.context or {}
    return {
        "run_id": run.id,
        "template_name": run.template_name,
        "ticket_id": run.ticket_id,
        "source": run.source,
        "status": run.status,
        "current_step_index": run.current_step_index,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "device_id": context.get("device_id"),
        "device_name": context.get("device_name"),
        "patrol_batch_id": context.get("patrol_batch_id"),
        "retry_of_run_id": context.get("retry_of_run_id"),
        "rerun_of_batch_id": context.get("rerun_of_batch_id"),
        "rerun_of_run_id": context.get("rerun_of_run_id"),
        "patrol_batch_archived": bool(context.get("patrol_batch_archived")),
        "patrol_batch_archived_at": context.get("patrol_batch_archived_at"),
        "patrol_batch_archive_reason": context.get("patrol_batch_archive_reason"),
        "patrol_batch_keep_until": context.get("patrol_batch_keep_until"),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _patrol_batch_sort_key(item: dict[str, Any]) -> tuple[datetime, str]:
    raw_created_at = str(item.get("created_at") or "")
    try:
        created_at = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
    except ValueError:
        created_at = datetime.min.replace(tzinfo=timezone.utc)
    return created_at, str(item.get("patrol_batch_id") or "")


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="keep_until must be ISO8601") from exc


def _run_context(run) -> dict[str, Any]:
    return dict(run.context or {})


def _build_patrol_batch_report(patrol_batch_id: str, runs: list[Any]) -> str:
    return MaterializePatrolBatchReportHandler(
        summary_builder=_build_patrol_batch_summary,
        renderer=PatrolReportService.render_markdown,
        projection_version="patrol-technical-report/v2",
    ).handle(
        patrol_batch_id,
        runs,
        generated_at=datetime.now(timezone.utc),
    )


def _build_patrol_batch_summary(patrol_batch_id: str, runs: list[Any]) -> dict[str, Any]:
    return build_patrol_batch_summary(patrol_batch_id, runs)


def _build_patrol_batch_leadership_report(patrol_batch_id: str, runs: list[Any]) -> dict[str, Any]:
    summary = _build_patrol_batch_summary(patrol_batch_id, runs)
    return build_leadership_report(
        patrol_batch_id,
        summary,
        generated_at=datetime.now(timezone.utc),
    )


def _build_check_item_report_artifact(
    patrol_batch_id: str,
    artifact_name: str,
    *,
    report_manifest_id: str | None = None,
) -> tuple[dict[str, Any], ReportArtifact] | None:
    """Use frozen check-item facts when the batch has switched runtimes."""

    if not PatrolBatchManifestRepository().has_runtime_manifest(patrol_batch_id):
        return None
    manifest_service = ReportManifestService()
    manifest = (
        manifest_service.get(report_manifest_id)
        if report_manifest_id
        else manifest_service.latest_for_batch(patrol_batch_id)
    )
    if manifest is None:
        raise HTTPException(
            status_code=409,
            detail=("report artifacts have not been materialized; request report generation first"),
        )
    if str(manifest.get("patrol_batch_id") or "") != patrol_batch_id:
        raise HTTPException(
            status_code=409,
            detail="report manifest batch identity mismatch",
        )
    release = ReportReleaseService().get_for_manifest(str(manifest["id"]))
    artifact_keys = dict(manifest.get("artifact_object_keys_json") or {})
    artifact_digests = dict(manifest.get("artifact_sha256_json") or {})
    if release is not None:
        release_digests = dict(release.get("artifact_sha256_json") or {})
        if (
            str(release.get("source_facts_digest") or "")
            != str(manifest.get("source_digest") or "")
            or release_digests != artifact_digests
        ):
            raise HTTPException(
                status_code=409,
                detail=("released report metadata does not match its immutable manifest"),
            )
        try:
            artifact = load_attached_report_artifact(
                manifest,
                artifact_name,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        return {
            **manifest,
            "_delivery_status": "released",
            "_release_digest": str(release.get("release_digest") or ""),
        }, artifact
    if artifact_name in artifact_keys and artifact_name in artifact_digests:
        try:
            artifact = load_attached_report_artifact(
                manifest,
                artifact_name,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        return manifest, artifact
    raise HTTPException(
        status_code=409,
        detail=(f"report artifact {artifact_name!r} has not been materialized for this manifest"),
    )


def _check_item_report_response(
    manifest: dict[str, Any],
    artifact: ReportArtifact,
) -> Response:
    facts_status = str(manifest.get("status") or "draft")
    status = str(
        manifest.get("_delivery_status")
        or ("pending_signoff" if facts_status == "final" else "facts_not_final")
    )
    filename = artifact.filename
    if status != "released":
        stem, separator, suffix = filename.rpartition(".")
        qualifier = "preview" if facts_status == "final" else "partial"
        filename = f"{stem}-{qualifier}.{suffix}" if separator else f"{filename}-{qualifier}"
    return Response(
        content=artifact.content,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": (f'attachment; filename="{filename}"'),
            "X-NetOps-Report-Status": status,
            "X-NetOps-Report-Facts-Status": facts_status,
            "X-NetOps-Report-Source-Digest": str(manifest.get("source_digest") or ""),
            "X-NetOps-Report-Release-Digest": str(manifest.get("_release_digest") or ""),
            "X-NetOps-Report-Manifest-Id": str(manifest.get("id") or ""),
            "X-NetOps-Report-Version": str(manifest.get("version") or ""),
        },
    )


def _list_patrol_batch_runs(patrol_batch_id: str) -> list[Any]:
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    try:
        manifest_runs = PatrolBatchManifestRepository().workflow_runs(patrol_batch_id)
    except Exception:
        manifest_runs = None
    if manifest_runs is not None:
        return manifest_runs
    return list_workflow_runs(
        limit=1000,
        template_name="patrol-change-event",
        source="device-management",
        patrol_batch_id=patrol_batch_id,
    )


def _patrol_batch_manifest_exists(patrol_batch_id: str) -> bool:
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    try:
        return PatrolBatchManifestRepository().workflow_runs(patrol_batch_id) is not None
    except Exception:
        return False


def _direct_batch_executions(patrol_batch_id: str):
    from src.apps.inspection.backend.online_reads import read_batch_execution_rows

    return read_batch_execution_rows(patrol_batch_id)


def _snapshot_run_ids_for_runs(
    runs: list[Any],
    patrol_batch_id: str | None = None,
) -> list[str]:
    if patrol_batch_id and not runs:
        rows = _direct_batch_executions(patrol_batch_id)
        if rows is not None:
            return [row["snapshot_run_id"] for row in rows if row["snapshot_run_id"]]
    return list_patrol_snapshot_run_ids([str(run.id) for run in runs])


@router.get("/runs/patrol-batches")
async def list_patrol_batches(
    limit: int = 25,
    offset: int = 0,
    include_archived: bool = False,
    search: str | None = None,
    status: str | None = None,
):
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
    from src.core.patrol.legacy_batch_history import LegacyPatrolBatchHistoryRepository

    page_limit = max(1, min(limit, 100))
    page_offset = max(0, offset)
    repository = PatrolBatchManifestRepository()
    merge_window = page_offset + page_limit
    try:
        manifest_page = repository.list_batches_page(
            limit=merge_window,
            offset=0,
            include_archived=include_archived,
            search=search,
            status=status,
        )
    except Exception:
        manifest_page = {
            "items": [],
            "total": 0,
            "limit": merge_window,
            "offset": 0,
        }
    try:
        legacy_page = LegacyPatrolBatchHistoryRepository().list_batches_window(
            limit=merge_window,
            include_archived=include_archived,
            search=search,
            status=status,
        )
    except Exception:
        legacy_page = {"items": [], "total": 0}
    manifest_total = int(manifest_page.get("total") or 0)
    legacy_total = int(legacy_page.get("total") or 0)
    if legacy_total:
        observe_legacy_path(
            "legacy-patrol-batch-history-reader",
            "merge_legacy_batch_history",
            outcome="rows_returned",
        )
    merged_items = sorted(
        [*(manifest_page.get("items") or []), *(legacy_page.get("items") or [])],
        key=_patrol_batch_sort_key,
        reverse=True,
    )
    items = merged_items[page_offset : page_offset + page_limit]
    total = manifest_total + legacy_total
    return {
        "items": items,
        "total": total,
        "limit": page_limit,
        "offset": page_offset,
        "page": page_offset // page_limit + 1,
        "page_size": page_limit,
        "page_count": (total + page_limit - 1) // page_limit if total else 0,
        "legacy_total": legacy_total,
    }


@router.get("/runs/patrol-batches/{patrol_batch_id}/devices")
async def list_patrol_batch_devices(
    patrol_batch_id: str,
    limit: int = 50,
    offset: int = 0,
    cursor: str | None = None,
    search: str | None = None,
    selection_status: str | None = None,
):
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    try:
        result = PatrolBatchManifestRepository().list_devices(
            patrol_batch_id,
            limit=limit,
            offset=offset,
            cursor=cursor,
            search=search,
            selection_status=selection_status,
        )
    except Exception:
        result = None
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="device manifest is unavailable for this legacy patrol batch",
        )
    return result


@router.post("/runs/patrol-batches/{patrol_batch_id}/cancel")
async def cancel_patrol_batch(
    patrol_batch_id: str,
    request: PatrolBatchCancelRequest,
    user: CurrentUser | None = Depends(app_user),
):
    reason = request.reason.strip()
    if len(reason) < 3:
        raise HTTPException(
            status_code=422,
            detail="cancellation reason must contain at least 3 non-space characters",
        )
    actor = user.user_id if user else "system"
    runs = _list_patrol_batch_runs(patrol_batch_id)
    repository = PatrolBatchManifestRepository()
    dispatch_result = repository.cancel_dispatch(
        patrol_batch_id,
        actor=actor,
        reason=reason,
    )
    if dispatch_result is None and not runs:
        raise HTTPException(status_code=404, detail="patrol batch not found")
    if dispatch_result and dispatch_result.get("blocked_reason") == "archived":
        raise HTTPException(
            status_code=409,
            detail="patrol batch is archived; restore it before cancellation",
        )

    active_runs = [
        run for run in runs if str(run.status or "").lower() in ACTIVE_PATROL_RUN_STATUSES
    ]
    already_cancelled = bool(dispatch_result and dispatch_result.get("idempotent")) or (
        bool(runs)
        and not active_runs
        and any(str(run.status or "").lower() == "cancelled" for run in runs)
    )
    if (
        not active_runs
        and not already_cancelled
        and not bool(dispatch_result and dispatch_result.get("changed"))
    ):
        raise HTTPException(
            status_code=409,
            detail="patrol batch has no active or pending work to cancel",
        )

    run_ids = {str(run.id) for run in active_runs} | {
        str(value) for value in (dispatch_result or {}).get("workflow_run_ids", []) if value
    }
    cancellation_results = [
        WorkflowEngine.cancel_run(
            run_id,
            actor=actor,
            reason=reason,
        )
        for run_id in sorted(run_ids)
    ]

    revoked_task_ids: list[str] = []
    try:
        from src.core.celery_tasks.celery_app import celery

        for task_id in (dispatch_result or {}).get("celery_task_ids", []):
            celery.control.revoke(str(task_id), terminate=False)
            revoked_task_ids.append(str(task_id))
    except Exception:
        pass

    finalization_errors: list[dict[str, str]] = []
    if (dispatch_result or {}).get("runtime_manifest_id"):
        from src.core.check_items import CheckItemRunFinalizer

        finalizer = CheckItemRunFinalizer()
        for device_id in (dispatch_result or {}).get("device_ids", []):
            try:
                finalizer.fail_device(
                    patrol_batch_id=patrol_batch_id,
                    device_id=str(device_id),
                    reason_code="patrol_batch_cancelled",
                    status="cancelled",
                    evidence_refs=tuple(sorted(run_ids | set(revoked_task_ids))),
                )
            except Exception as exc:
                finalization_errors.append({"device_id": str(device_id), "error": str(exc)})

    request_terminal_report(
        patrol_batch_id,
    )
    try:
        from src.core.patrol.batch_events import publish_patrol_batch_event

        publish_patrol_batch_event(
            {
                "type": "batch_cancelled",
                "patrol_batch_id": patrol_batch_id,
                "cancelled_by": actor,
                "reason": reason,
                "cancelled_run_count": sum(
                    1 for item in cancellation_results if item.get("changed")
                ),
            }
        )
    except Exception:
        pass

    return {
        "success": True,
        "patrol_batch_id": patrol_batch_id,
        "status": "cancelled",
        "idempotent": already_cancelled,
        "cancelled_at": (dispatch_result or {}).get("cancelled_at") or _now_iso(),
        "cancelled_by": actor,
        "reason": reason,
        "affected_runs": len(run_ids),
        "cancelled_run_count": sum(1 for item in cancellation_results if item.get("changed")),
        "revoked_task_count": len(revoked_task_ids),
        "cancelled_device_count": len((dispatch_result or {}).get("device_ids", [])),
        "finalization_errors": finalization_errors,
    }


@router.post("/runs/patrol-batches/{patrol_batch_id}/archive")
async def archive_patrol_batch(
    patrol_batch_id: str,
    request: PatrolBatchGovernanceRequest = PatrolBatchGovernanceRequest(),
    user: CurrentUser | None = Depends(app_user),
):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    active = [run for run in runs if str(run.status or "").lower() in ACTIVE_PATROL_RUN_STATUSES]
    if not runs:
        active.extend(
            row
            for row in (_direct_batch_executions(patrol_batch_id) or [])
            if row["status"] not in {"completed", "failed", "cancelled"}
        )
    if active and not request.force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "patrol batch has active runs; use force=true if you really want to archive it"
                ),
                "active_run_count": len(active),
            },
        )
    if active and request.force:
        await cancel_patrol_batch(patrol_batch_id, PatrolBatchCancelRequest(reason="archive"), user)
    archived_at = _now_iso()
    actor = user.user_id if user else "system"
    for run in runs:
        context = _run_context(run)
        context["patrol_batch_archived"] = True
        context["patrol_batch_archived_at"] = archived_at
        context["patrol_batch_archived_by"] = actor
        context["patrol_batch_archive_reason"] = request.reason or "manual archive"
        if request.keep_until:
            context["patrol_batch_keep_until"] = request.keep_until
        update_run_context(run.id, context)
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    try:
        PatrolBatchManifestRepository().set_governance(
            patrol_batch_id,
            archived=True,
            actor=actor,
            reason=request.reason or "manual archive",
            keep_until=_parse_optional_datetime(request.keep_until),
        )
    except Exception as exc:
        raise HTTPException(503, "巡检批次归档状态保存失败") from exc
    return {
        "success": True,
        "patrol_batch_id": patrol_batch_id,
        "archived": True,
        "affected_runs": len(runs),
        "active_run_count": len(active),
        "archived_at": archived_at,
        "archived_by": actor,
        "reason": request.reason or "manual archive",
        "keep_until": request.keep_until,
    }


@router.post("/runs/patrol-batches/{patrol_batch_id}/restore")
async def restore_patrol_batch(
    patrol_batch_id: str,
    request: PatrolBatchGovernanceRequest = PatrolBatchGovernanceRequest(),
    user: CurrentUser | None = Depends(app_user),
):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    restored_at = _now_iso()
    actor = user.user_id if user else "system"
    for run in runs:
        context = _run_context(run)
        context["patrol_batch_archived"] = False
        context["patrol_batch_restored_at"] = restored_at
        context["patrol_batch_restored_by"] = actor
        context["patrol_batch_restore_reason"] = request.reason or "manual restore"
        if request.keep_until:
            context["patrol_batch_keep_until"] = request.keep_until
        update_run_context(run.id, context)
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    try:
        PatrolBatchManifestRepository().set_governance(
            patrol_batch_id,
            archived=False,
            actor=actor,
            reason=request.reason or "manual restore",
            keep_until=_parse_optional_datetime(request.keep_until),
        )
    except Exception as exc:
        raise HTTPException(503, "巡检批次归档状态保存失败") from exc
    return {
        "success": True,
        "patrol_batch_id": patrol_batch_id,
        "archived": False,
        "affected_runs": len(runs),
        "restored_at": restored_at,
        "restored_by": actor,
        "reason": request.reason or "manual restore",
        "keep_until": request.keep_until,
    }


@router.post("/runs/patrol-batches/{patrol_batch_id}/retry")
async def retry_patrol_batch(
    patrol_batch_id: str,
    request: WorkflowRetryRequest = WorkflowRetryRequest(),
    user: CurrentUser | None = Depends(app_user),
):
    from src.apps.inspection.backend.launch import repeat_batch

    return repeat_batch(
        patrol_batch_id,
        actor=user.user_id if user else None,
        request_id=request.request_id,
        failed_only=True,
        device_id=request.device_id,
    )


@router.post("/runs/patrol-batches/{patrol_batch_id}/rerun")
async def rerun_patrol_batch(
    patrol_batch_id: str,
    request: WorkflowRetryRequest = WorkflowRetryRequest(),
    user: CurrentUser | None = Depends(app_user),
):
    from src.apps.inspection.backend.launch import repeat_batch

    return repeat_batch(
        patrol_batch_id,
        actor=user.user_id if user else None,
        request_id=request.request_id,
        device_id=request.device_id,
    )


@router.get("/runs/patrol-batches/{patrol_batch_id}/summary")
async def get_patrol_batch_summary(patrol_batch_id: str):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    summary = _build_patrol_batch_summary(patrol_batch_id, runs)
    check_item_summary = CheckItemBatchResultService().summary(patrol_batch_id)
    if check_item_summary is not None:
        summary["check_item_summary"] = check_item_summary
        deliverables = dict(summary.get("deliverables") or {})
        deliverables["evidence_package_url"] = (
            f"/api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/evidence.zip"
        )
        summary["deliverables"] = deliverables
    return summary


@router.get("/runs/patrol-batches/{patrol_batch_id}/check-results")
async def list_patrol_batch_check_results(
    patrol_batch_id: str,
    limit: int = 50,
    offset: int = 0,
    cursor: str | None = None,
    search: str | None = None,
    assessment_status: str | None = None,
    completeness_status: str | None = None,
    source_identity_status: str | None = None,
    output_quality_status: str | None = None,
    freshness_status: str | None = None,
    coverage_status: str | None = None,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return CheckItemBatchResultService().list_results(
            patrol_batch_id,
            limit=limit,
            offset=offset,
            cursor=cursor,
            search=search,
            assessment_status=assessment_status,
            completeness_status=completeness_status,
            source_identity_status=source_identity_status,
            output_quality_status=output_quality_status,
            freshness_status=freshness_status,
            coverage_status=coverage_status,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = (
            404
            if "patrol batch not found" in message
            else 400
            if "cursor" in message or "offset exceeds" in message
            else 422
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/runs/patrol-batches/{patrol_batch_id}/check-results/{execution_id}")
async def get_patrol_batch_check_result_detail(
    patrol_batch_id: str,
    execution_id: str,
    entry_limit: int = Query(50, ge=1, le=200),
    entry_offset: int = Query(0, ge=0),
    _user: CurrentUser = Depends(app_user),
):
    try:
        return CheckItemBatchResultService().get_result_detail(
            patrol_batch_id,
            execution_id,
            entry_limit=entry_limit,
            entry_offset=entry_offset,
        )
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/runs/patrol-batches/{patrol_batch_id}/export.csv")
async def export_patrol_batch_csv(patrol_batch_id: str):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "patrol_batch_id",
            "run_id",
            "retry_of_run_id",
            "device_id",
            "device_name",
            "status",
            "current_step_index",
            "ticket_id",
            "created_at",
            "completed_at",
            "error_message",
            "execution_kind",
            "task_id",
            "execution_phase",
        ],
    )
    writer.writeheader()
    direct_rows = _direct_batch_executions(patrol_batch_id) if not runs else None
    items = (
        [
            {
                **row,
                "patrol_batch_id": patrol_batch_id,
                "execution_phase": row["phase"],
                "error_message": row.get("error"),
            }
            for row in direct_rows
        ]
        if direct_rows is not None
        else [_run_summary_dict(run) for run in runs]
    )
    for item in items:
        writer.writerow(
            {
                "patrol_batch_id": item.get("patrol_batch_id") or "",
                "run_id": item.get("run_id") or "",
                "retry_of_run_id": item.get("retry_of_run_id") or "",
                "execution_kind": item.get("execution_kind") or "workflow",
                "task_id": item.get("task_id") or "",
                "execution_phase": item.get("execution_phase") or "",
                "device_id": item.get("device_id") or "",
                "device_name": item.get("device_name") or "",
                "status": item.get("status") or "",
                "current_step_index": (
                    ""
                    if item.get("execution_kind") == "inspection"
                    else item.get("current_step_index") or 0
                ),
                "ticket_id": item.get("ticket_id") or "",
                "created_at": item.get("created_at") or "",
                "completed_at": item.get("completed_at") or "",
                "error_message": item.get("error_message") or "",
            }
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{patrol_batch_id}.csv"'},
    )


@router.get("/runs/patrol-batches/{patrol_batch_id}/raw-data.jsonl")
async def export_patrol_batch_raw_data(
    patrol_batch_id: str,
    _user: CurrentUser = Depends(app_user),
):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    snapshot_run_ids = _snapshot_run_ids_for_runs(runs, patrol_batch_id)

    def generate():
        for row in iter_patrol_raw_snapshot_rows(snapshot_run_ids):
            yield (
                json.dumps(
                    {"patrol_batch_id": patrol_batch_id, **row},
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{patrol_batch_id}-raw-data.jsonl"'},
    )


def _artifact_object_key(download_url: str | None) -> str | None:
    if not download_url:
        return None
    values = parse_qs(urlparse(str(download_url)).query).get("key") or []
    return str(values[0]) if values else None


def _safe_archive_component(value: Any, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._")
    return cleaned or fallback


def _list_all_patrol_batch_devices(patrol_batch_id: str) -> list[dict[str, Any]]:
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    repository = PatrolBatchManifestRepository()
    devices: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = repository.list_devices(patrol_batch_id, limit=200, offset=offset)
        if page is None:
            return []
        items = list(page.get("items") or [])
        devices.extend(items)
        offset += len(items)
        if not items or offset >= int(page.get("total") or 0):
            return devices


def _device_inventory_text(devices: list[dict[str, Any]]) -> bytes:
    fields = (
        "device_id",
        "device_name",
        "management_ip",
        "selection_status",
        "preflight_status",
        "connection_status",
        "workflow_status",
        "failure_code",
        "error_message",
        "raw_cli_file_count",
    )
    labels = (
        "设备ID",
        "设备名称",
        "管理地址",
        "选择状态",
        "预检状态",
        "登录状态",
        "执行状态",
        "失败代码",
        "失败原因",
        "原始文件数",
    )
    lines = ["\t".join(labels)]
    for device in devices:
        device = {
            **device,
            "workflow_status": device.get("execution_status") or device.get("workflow_status"),
        }
        lines.append(
            "\t".join(
                str("" if device.get(field) is None else device.get(field))
                .replace("\t", " ")
                .replace("\r", " ")
                .replace("\n", " ")
                for field in fields
            )
        )
    return ("\ufeff" + "\n".join(lines) + "\n").encode("utf-8")


def _write_original_cli_files(
    output: zipfile.ZipFile,
    archive_data: bytes,
    device_dir: str,
    *,
    file_hashes: dict[str, str] | None = None,
) -> int:
    written = 0
    try:
        with zipfile.ZipFile(io.BytesIO(archive_data)) as source:
            for member in source.infolist():
                member_path = Path(member.filename)
                if member.is_dir() or member_path.is_absolute() or ".." in member_path.parts:
                    continue
                if member_path.suffix.lower() not in {".txt", ".log"}:
                    continue
                relative_path = member_path.with_suffix(".txt").as_posix()
                destination = f"{device_dir}/{relative_path}"
                data = source.read(member)
                output.writestr(destination, data)
                if file_hashes is not None:
                    file_hashes[destination] = hashlib.sha256(data).hexdigest()
                written += 1
    except zipfile.BadZipFile:
        return 0
    return written


def _resolve_raw_cli_manifest(
    patrol_batch_id: str,
    report_manifest_id: str | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    service = ReportManifestService()
    manifest = (
        service.get(report_manifest_id)
        if report_manifest_id
        else service.latest_for_batch(patrol_batch_id)
    )
    if manifest is None:
        if report_manifest_id:
            raise HTTPException(status_code=404, detail="report manifest not found")
        return None, []
    if str(manifest.get("patrol_batch_id") or "") != patrol_batch_id:
        raise HTTPException(
            status_code=409,
            detail="report manifest does not belong to patrol batch",
        )
    evidence_index = [
        dict(item)
        for item in (manifest.get("source_facts_json") or {}).get("evidence_index") or []
        if isinstance(item, dict)
    ]
    expected_digest = str(manifest.get("raw_evidence_set_digest") or "")
    if not expected_digest or raw_evidence_set_digest(evidence_index) != expected_digest:
        raise HTTPException(
            status_code=409,
            detail="report manifest raw evidence digest mismatch",
        )
    return manifest, evidence_index


def _raw_cli_verification_text(
    *,
    patrol_batch_id: str,
    manifest: dict[str, Any] | None,
    evidence_index: list[dict[str, Any]],
    file_hashes: dict[str, str],
) -> bytes:
    lines = [
        "原始输出校验清单",
        f"巡检批次\t{patrol_batch_id}",
        f"报告Manifest\t{str((manifest or {}).get('id') or '-')}",
        f"报告版本\t{str((manifest or {}).get('version') or '-')}",
        f"原始证据集合摘要\t{str((manifest or {}).get('raw_evidence_set_digest') or '-')}",
        "",
        "归档文件\tSHA-256",
    ]
    lines.extend(f"{path}\t{digest}" for path, digest in sorted(file_hashes.items()))
    lines.extend(
        [
            "",
            "Snapshot ID\t设备\t管理地址\t命令\t原始输出SHA-256\t源文件SHA-256\tPatrolRun",
        ]
    )
    for item in sorted(
        evidence_index,
        key=lambda row: (
            str(row.get("device_name") or ""),
            str(row.get("canonical_command") or ""),
            str(row.get("snapshot_id") or ""),
        ),
    ):
        lines.append(
            "\t".join(
                str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")
                for value in (
                    item.get("snapshot_id"),
                    item.get("device_name"),
                    item.get("management_ip"),
                    item.get("canonical_command"),
                    item.get("raw_text_hash"),
                    item.get("source_file_hash"),
                    item.get("run_id"),
                )
            )
        )
    return ("\ufeff" + "\n".join(lines) + "\n").encode("utf-8")


@router.get("/runs/patrol-batches/{patrol_batch_id}/raw-cli.zip")
async def export_patrol_batch_raw_cli(
    patrol_batch_id: str,
    report_manifest_id: str | None = Query(default=None),
    _user: CurrentUser = Depends(app_user),
):
    from src.infrastructure.storage.minio_client import get_minio_storage

    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    manifest, evidence_index = _resolve_raw_cli_manifest(
        patrol_batch_id,
        report_manifest_id,
    )
    workflow_run_ids = [str(run.id) for run in runs]
    expected_snapshot_run_ids = sorted(
        {str(item.get("run_id") or "") for item in evidence_index if item.get("run_id")}
    )
    if manifest is not None:
        artifacts_by_run = list_patrol_run_raw_output_artifacts(expected_snapshot_run_ids)
        source_rows = [
            {
                "run_id": run_id,
                "artifact": artifacts_by_run.get(run_id),
                "device_id": next(
                    (
                        str(item.get("device_id") or "")
                        for item in evidence_index
                        if str(item.get("run_id") or "") == run_id
                    ),
                    run_id,
                ),
                "device_name": next(
                    (
                        str(item.get("device_name") or "")
                        for item in evidence_index
                        if str(item.get("run_id") or "") == run_id
                    ),
                    run_id,
                ),
            }
            for run_id in expected_snapshot_run_ids
        ]
    elif not runs and (direct_rows := _direct_batch_executions(patrol_batch_id)) is not None:
        artifacts_by_run = list_patrol_run_raw_output_artifacts(
            [row["snapshot_run_id"] for row in direct_rows if row["snapshot_run_id"]]
        )
        source_rows = [
            {**row, "artifact": artifacts_by_run.get(row["snapshot_run_id"])}
            for row in direct_rows
            if row["snapshot_run_id"]
        ]
    else:
        artifacts_by_run = list_patrol_raw_output_artifacts(workflow_run_ids)
        source_rows = [
            {
                "run_id": str(run.id),
                "artifact": artifacts_by_run.get(str(run.id)),
                "device_id": str((run.context or {}).get("device_id") or ""),
                "device_name": str(
                    (run.context or {}).get("device_name")
                    or (run.context or {}).get("device_id")
                    or run.id
                ),
            }
            for run in runs
        ]

    storage = get_minio_storage()
    if any(item.get("artifact") for item in source_rows) and not storage.is_ready():
        raise HTTPException(status_code=503, detail="MinIO is unavailable")

    temp = tempfile.NamedTemporaryFile(prefix="patrol-raw-cli-", suffix=".zip", delete=False)
    temp_path = Path(temp.name)
    temp.close()
    exported_file_counts: dict[str, int] = {}
    exported_hashes: dict[str, str] = {}
    actual_source_identities: set[tuple[str, str]] = set()
    try:
        with zipfile.ZipFile(
            temp_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as output:
            used_directories: set[str] = set()
            for source_row in source_rows:
                artifact = source_row.get("artifact")
                key = _artifact_object_key(artifact.get("download_url") if artifact else None)
                if not artifact or not key:
                    continue
                archive_data = storage.download_file(key)
                if archive_data is None:
                    continue
                device_id = str(source_row.get("device_id") or "")
                base_dir = _safe_archive_component(
                    source_row.get("device_name") or device_id,
                    "device",
                )
                device_dir = base_dir
                if device_dir.casefold() in used_directories:
                    suffix = _safe_archive_component(device_id, "duplicate")[-16:]
                    device_dir = f"{base_dir}-{suffix}"
                used_directories.add(device_dir.casefold())
                run_hashes: dict[str, str] = {}
                written = _write_original_cli_files(
                    output,
                    archive_data,
                    device_dir,
                    file_hashes=run_hashes,
                )
                exported_hashes.update(run_hashes)
                actual_source_identities.update(
                    (str(source_row.get("run_id") or ""), digest) for digest in run_hashes.values()
                )
                exported_file_counts[device_id] = written

            if manifest is not None:
                missing_source_hashes = [
                    str(item.get("snapshot_id") or "")
                    for item in evidence_index
                    if not item.get("source_file_hash")
                ]
                expected_source_identities = {
                    (
                        str(item.get("run_id") or ""),
                        str(item.get("source_file_hash") or ""),
                    )
                    for item in evidence_index
                    if item.get("run_id") and item.get("source_file_hash")
                }
                if missing_source_hashes:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "raw output source hashes are incomplete",
                            "snapshot_ids": missing_source_hashes[:20],
                        },
                    )
                if actual_source_identities != expected_source_identities:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "raw output files do not match frozen report evidence",
                            "missing_count": len(
                                expected_source_identities - actual_source_identities
                            ),
                            "unexpected_count": len(
                                actual_source_identities - expected_source_identities
                            ),
                        },
                    )

            devices = _list_all_patrol_batch_devices(patrol_batch_id)
            for device in devices:
                device["raw_cli_file_count"] = exported_file_counts.get(
                    str(device.get("device_id") or ""), 0
                )
            failed_devices = [
                device for device in devices if device.get("connection_status") == "failed"
            ]
            missing_output_devices = [
                device
                for device in devices
                if int(device.get("raw_cli_file_count") or 0) == 0
                and device.get("connection_status") != "failed"
            ]
            output.writestr("设备列表.txt", _device_inventory_text(devices))
            output.writestr(
                "登录失败设备列表.txt",
                _device_inventory_text(failed_devices),
            )
            output.writestr(
                "原始输出缺失设备列表.txt",
                _device_inventory_text(missing_output_devices),
            )
            output.writestr(
                "原始输出校验清单.txt",
                _raw_cli_verification_text(
                    patrol_batch_id=patrol_batch_id,
                    manifest=manifest,
                    evidence_index=evidence_index,
                    file_hashes=exported_hashes,
                ),
            )
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    def generate():
        try:
            with temp_path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    yield chunk
        finally:
            temp_path.unlink(missing_ok=True)

    headers = {
        "Content-Disposition": f'attachment; filename="{patrol_batch_id}-raw-output.zip"',
        "X-NetOps-Raw-File-Count": str(len(exported_hashes)),
    }
    if manifest is not None:
        headers.update(
            {
                "X-NetOps-Report-Manifest-Id": str(manifest.get("id") or ""),
                "X-NetOps-Report-Version": str(manifest.get("version") or ""),
                "X-NetOps-Raw-Evidence-Set-Digest": str(
                    manifest.get("raw_evidence_set_digest") or ""
                ),
            }
        )
    return StreamingResponse(
        generate(),
        media_type="application/zip",
        headers=headers,
    )


@router.get("/runs/patrol-batches/{patrol_batch_id}/events.csv")
async def export_patrol_batch_events(
    patrol_batch_id: str,
    _user: CurrentUser = Depends(app_user),
):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    snapshot_run_ids = _snapshot_run_ids_for_runs(runs, patrol_batch_id)
    device_names = {
        str((run.context or {}).get("device_id") or ""): str(
            (run.context or {}).get("device_name") or (run.context or {}).get("device_id") or ""
        )
        for run in runs
    }
    if not runs:
        device_names.update(
            {
                row["device_id"]: row["device_name"]
                for row in (_direct_batch_executions(patrol_batch_id) or [])
            }
        )
    output = io.StringIO()
    fieldnames = [
        "patrol_batch_id",
        "occurrence_id",
        "occurrence_kind",
        "event_id",
        "device_id",
        "device_name",
        "event_type",
        "severity",
        "status",
        "entity_type",
        "entity_key",
        "occurred_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for event in iter_patrol_event_export_rows(snapshot_run_ids, device_names):
        writer.writerow({"patrol_batch_id": patrol_batch_id, **event})
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{patrol_batch_id}-events.csv"'},
    )


@router.get("/runs/patrol-batches/{patrol_batch_id}/report.md")
async def export_patrol_batch_report(
    patrol_batch_id: str,
    report_manifest_id: str | None = Query(default=None),
    _user: CurrentUser = Depends(app_user),
):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    check_item_artifact = _build_check_item_report_artifact(
        patrol_batch_id,
        "markdown",
        report_manifest_id=report_manifest_id,
    )
    if check_item_artifact is not None:
        return _check_item_report_response(*check_item_artifact)
    markdown = _build_patrol_batch_report(patrol_batch_id, runs)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{patrol_batch_id}-report.md"'},
    )


@router.get("/runs/patrol-batches/{patrol_batch_id}/report.docx")
async def export_patrol_batch_leadership_report(
    patrol_batch_id: str,
    report_manifest_id: str | None = Query(default=None),
    _user: CurrentUser = Depends(app_user),
):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    check_item_artifact = _build_check_item_report_artifact(
        patrol_batch_id,
        "docx",
        report_manifest_id=report_manifest_id,
    )
    if check_item_artifact is not None:
        return _check_item_report_response(*check_item_artifact)
    report = _build_patrol_batch_leadership_report(patrol_batch_id, runs)
    content = render_leadership_docx(report)
    return Response(
        content=content,
        media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        headers={
            "Content-Disposition": (f'attachment; filename="{patrol_batch_id}-patrol-report.docx"')
        },
    )


@router.get("/runs/patrol-batches/{patrol_batch_id}/report.xlsx")
async def export_patrol_batch_technical_workbook(
    patrol_batch_id: str,
    report_manifest_id: str | None = Query(default=None),
    _user: CurrentUser = Depends(app_user),
):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    check_item_artifact = _build_check_item_report_artifact(
        patrol_batch_id,
        "excel",
        report_manifest_id=report_manifest_id,
    )
    if check_item_artifact is not None:
        return _check_item_report_response(*check_item_artifact)
    summary = _build_patrol_batch_summary(patrol_batch_id, runs)
    content = render_patrol_batch_xlsx(patrol_batch_id, summary)
    return Response(
        content=content,
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{patrol_batch_id}-technical-report.xlsx"'
            )
        },
    )


@router.get("/runs/patrol-batches/{patrol_batch_id}/report.json")
async def export_patrol_batch_leadership_report_json(
    patrol_batch_id: str,
    report_manifest_id: str | None = Query(default=None),
    _user: CurrentUser = Depends(app_user),
):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    check_item_artifact = _build_check_item_report_artifact(
        patrol_batch_id,
        "json",
        report_manifest_id=report_manifest_id,
    )
    if check_item_artifact is not None:
        return _check_item_report_response(*check_item_artifact)
    report = _build_patrol_batch_leadership_report(patrol_batch_id, runs)
    return Response(
        content=render_leadership_json(report),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (f'attachment; filename="{patrol_batch_id}-patrol-report.json"')
        },
    )


@router.get("/runs/patrol-batches/{patrol_batch_id}/evidence.zip")
async def export_patrol_batch_evidence_package(
    patrol_batch_id: str,
    report_manifest_id: str | None = Query(default=None),
    _user: CurrentUser = Depends(app_user),
):
    runs = _list_patrol_batch_runs(patrol_batch_id)
    if not runs and not _patrol_batch_manifest_exists(patrol_batch_id):
        raise HTTPException(status_code=404, detail="patrol batch not found")
    check_item_artifact = _build_check_item_report_artifact(
        patrol_batch_id,
        "evidence",
        report_manifest_id=report_manifest_id,
    )
    if check_item_artifact is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "evidence package requires a check-item runtime manifest; "
                "legacy batches keep their existing raw-output exports"
            ),
        )
    return _check_item_report_response(*check_item_artifact)


from .reports import request_terminal_report
