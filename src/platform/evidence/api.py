"""Shared offline job transport, bound to a purpose by its owning router."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from src.auth.dependencies import get_current_user, require_role
from src.auth.models import CurrentUser
from src.core.patrol.offline_jobs import OfflinePatrolJobService
from src.core.patrol.offline_tasks import preview_offline_patrol_job
from src.gateway.audit_service import write_audit_log
from src.infrastructure.db.models import FileMetadata, OfflinePatrolJobRecord
from src.infrastructure.db.postgres import get_db_session
from src.storage import folder_service
from src.storage.permissions import check_file_access
from src.storage.schemas import FolderCreateRequest


class OfflinePatrolPeriodRequest(BaseModel):
    role: Literal["sample", "baseline", "current"]
    label: str = Field(min_length=1, max_length=128)
    observed_at: str


class OfflinePatrolJobCreateRequest(BaseModel):
    job_type: Literal["parser_validation", "single_patrol", "history_compare"]
    name: str = Field(default="离线文件任务", min_length=1, max_length=256)
    periods: list[OfflinePatrolPeriodRequest]
    vendor: str | None = None
    model: str | None = None
    publish_events: bool = False
    report_intent: Literal["historical", "current_attestation"] = (
        "historical"
    )
    idempotency_key: str = Field(
        default_factory=lambda: uuid.uuid4().hex, min_length=8, max_length=128
    )


class OfflinePatrolSourceAttachRequest(BaseModel):
    period_role: Literal["sample", "baseline", "current"]
    file_id: str
    relative_path: str = Field(min_length=1, max_length=1024)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    size_bytes: int = Field(default=0, ge=0)


class OfflinePatrolBindingItem(BaseModel):
    binding_id: str
    inventory_device_id: str | None = None


class OfflinePatrolBindingConfirmRequest(BaseModel):
    bindings: list[OfflinePatrolBindingItem]


class OfflineOutputDispositionConfirmRequest(BaseModel):
    review_item_ids: list[str] = Field(min_length=1, max_length=500)
    reason: str = Field(default="", max_length=1000)


class OfflinePatrolExecuteRequest(BaseModel):
    idempotency_key: str = Field(
        default_factory=lambda: uuid.uuid4().hex, min_length=8, max_length=128
    )


def _api_purpose(request: Request) -> str:
    purpose = getattr(request.state, "offline_job_purpose", None)
    if purpose not in {"rule_asset_source", "offline_patrol"}:
        raise HTTPException(500, "offline workspace has not been bound")
    return purpose


def _audit(
    request: Request,
    user: CurrentUser,
    action: str,
    job_id: str,
    detail: dict[str, Any] | None = None,
) -> None:
    purpose = _api_purpose(request)
    action_suffix = action.removeprefix("rule_asset_source_").removeprefix("offline_patrol_")
    write_audit_log(
        action=f"{purpose}_{action_suffix}",
        user_id=user.user_id,
        username=user.username,
        resource_type=f"{purpose}_job",
        resource_id=job_id,
        detail=detail,
        ip_address=request.client.host if request.client else None,
    )


def _detail_or_http(job_id: str, user: CurrentUser) -> dict[str, Any]:
    try:
        return OfflinePatrolJobService().get_job(
            job_id,
            owner_id=user.user_id,
            is_admin=user.is_admin(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def build_workspace_router(
    purpose: Literal["rule_asset_source", "offline_patrol"],
    *,
    read_user=get_current_user,
    write_user=None,
    extra_routes: APIRouter | None = None,
    admission=None,
) -> APIRouter:
    """Bind workspace identity and allow its owner to supply admission dependencies."""
    if purpose not in {"rule_asset_source", "offline_patrol"}:
        raise ValueError("unsupported offline workspace")
    write_user = write_user or require_role(["admin", "operator"])

    def bind_scope(request: Request, user: CurrentUser = Depends(read_user)):
        request.state.offline_job_purpose = purpose
        job_id = str(request.path_params.get("job_id") or "").strip()
        if not job_id:
            return
        try:
            OfflinePatrolJobService().check_access(
                job_id,
                owner_id=(
                    None if purpose == "offline_patrol"
                    and "/current-authority" in request.url.path else user.user_id
                ),
                is_admin=user.is_admin(),
                purpose=purpose,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    dependencies = ([Depends(admission)] if admission is not None else [])
    dependencies.append(Depends(bind_scope))
    routes = APIRouter(dependencies=dependencies)

    @routes.post("", status_code=status.HTTP_201_CREATED)
    def create_offline_patrol_job(
        request: Request,
        payload: OfflinePatrolJobCreateRequest,
        user: CurrentUser = Depends(write_user),
    ):
        service = OfflinePatrolJobService()
        try:
            purpose = _api_purpose(request)
            if purpose == "rule_asset_source" and payload.publish_events:
                raise ValueError("rule asset source jobs cannot publish production events")
            if purpose == "rule_asset_source" and payload.job_type == "single_patrol":
                raise ValueError("single_patrol belongs to the offline patrol workspace")
            if purpose == "offline_patrol" and payload.job_type == "parser_validation":
                raise ValueError("parser_validation belongs to the Parser asset workspace")
            job = service.create_job(
                owner_id=user.user_id,
                idempotency_key=payload.idempotency_key,
                job_type=payload.job_type,
                name=payload.name,
                periods=[item.model_dump() for item in payload.periods],
                folder_id=None,
                purpose=purpose,
                report_intent=payload.report_intent,
                vendor=payload.vendor,
                model=payload.model,
                publish_events=payload.publish_events,
            )
            if not job.get("folder_id"):
                with get_db_session() as db:
                    folder = folder_service.create_folder(
                        db,
                        user,
                        FolderCreateRequest(
                            name=job["id"],
                            visibility="private",
                        ),
                    )
                    row = db.get(OfflinePatrolJobRecord, job["id"])
                    row.folder_id = folder.id
                job = service.get_job(job["id"], owner_id=user.user_id, is_admin=user.is_admin())
            _audit(request, user, "rule_asset_source_create", job["id"], {"job_type": payload.job_type})
            return job
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


    @routes.get("")
    def list_offline_patrol_jobs(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(25, ge=1, le=100),
        search: str | None = None,
        status_filter: str | None = Query(None, alias="status"),
        job_type: str | None = None,
        archived: bool = False,
        user: CurrentUser = Depends(read_user),
    ):
        return OfflinePatrolJobService().list_jobs(
            owner_id=user.user_id,
            is_admin=user.is_admin(),
            page=page,
            page_size=page_size,
            search=search,
            status=status_filter,
            job_type=job_type,
            purpose=_api_purpose(request),
            archived=archived,
        )


    @routes.get("/{job_id}")
    def get_offline_patrol_job(job_id: str, user: CurrentUser = Depends(read_user)):
        return _detail_or_http(job_id, user)


    @routes.get("/{job_id}/replay-summary")
    @routes.get("/{job_id}/analysis-summary", deprecated=True)
    def get_offline_patrol_analysis_summary(
        job_id: str,
        analysis_attempt_id: str | None = None,
        user: CurrentUser = Depends(read_user),
    ):
        detail = _detail_or_http(job_id, user)
        try:
            if detail.get("status") != "completed":
                raise ValueError("任务完成后才能生成分析摘要")
            return OfflinePatrolJobService().build_leadership_report_data(
                job_id,
                detail=detail,
                analysis_attempt_id=analysis_attempt_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @routes.get("/{job_id}/comparison-evidence/{change_id}")
    def get_offline_patrol_comparison_evidence(
        job_id: str,
        change_id: str,
        user: CurrentUser = Depends(read_user),
    ):
        _detail_or_http(job_id, user)
        try:
            return OfflinePatrolJobService().get_comparison_evidence(job_id, change_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @routes.post("/{job_id}/sources", status_code=status.HTTP_201_CREATED)
    def attach_offline_patrol_source(
        job_id: str,
        request: Request,
        payload: OfflinePatrolSourceAttachRequest,
        user: CurrentUser = Depends(write_user),
    ):
        _detail_or_http(job_id, user)
        try:
            with get_db_session() as db:
                file_row = db.get(FileMetadata, payload.file_id)
                if not file_row:
                    raise HTTPException(status_code=404, detail="storage file not found")
                check_file_access(db, user, file_row, write=False)
            result = OfflinePatrolJobService().attach_source(
                job_id=job_id,
                period_role=payload.period_role,
                file_id=payload.file_id,
                relative_path=payload.relative_path,
                sha256=payload.sha256.lower() if payload.sha256 else None,
                size_bytes=payload.size_bytes,
            )
            _audit(request, user, "rule_asset_source_attach", job_id, {"file_id": payload.file_id})
            return result
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @routes.delete("/{job_id}/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove_offline_patrol_source(
        job_id: str,
        source_id: str,
        request: Request,
        user: CurrentUser = Depends(write_user),
    ):
        _detail_or_http(job_id, user)
        try:
            OfflinePatrolJobService().remove_source(job_id=job_id, source_id=source_id)
            _audit(request, user, "rule_asset_source_remove", job_id, {"source_id": source_id})
            return Response(status_code=204)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @routes.post("/{job_id}/preview", status_code=status.HTTP_202_ACCEPTED)
    def preview_offline_patrol_job_endpoint(
        job_id: str,
        request: Request,
        user: CurrentUser = Depends(write_user),
    ):
        _detail_or_http(job_id, user)
        task_id = uuid.uuid4().hex
        service = OfflinePatrolJobService()
        try:
            service.mark_preview_queued(job_id, task_id)
            if _api_purpose(request) == "rule_asset_source":
                from src.apps.rule_governance.backend.source_tasks import preview_source
                task, queue = preview_source, "app.rule-governance"
            else:
                task, queue = preview_offline_patrol_job, "netops.ai"
            task.apply_async(
                args=[job_id],
                queue=queue,
                task_id=task_id,
            )
            _audit(request, user, "rule_asset_source_preview", job_id, {"celery_task_id": task_id})
            return {
                "success": True,
                "job_id": job_id,
                "celery_task_id": task_id,
                "status": "previewing",
            }
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            service.mark_preview_dispatch_failed(job_id, task_id, f"{type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="unable to dispatch preview task") from exc


    @routes.post("/{job_id}/device-bindings/confirm")
    def confirm_offline_patrol_bindings(
        job_id: str,
        request: Request,
        payload: OfflinePatrolBindingConfirmRequest,
        user: CurrentUser = Depends(write_user),
    ):
        _detail_or_http(job_id, user)
        try:
            result = OfflinePatrolJobService().confirm_bindings(
                job_id=job_id,
                actor=user.user_id,
                bindings=[item.model_dump() for item in payload.bindings],
            )
            _audit(request, user, "rule_asset_source_binding_confirm", job_id, {"count": len(result)})
            return {"success": True, "items": result}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @routes.post("/{job_id}/output-dispositions/confirm")
    def confirm_offline_output_dispositions(
        job_id: str,
        request: Request,
        payload: OfflineOutputDispositionConfirmRequest,
        user: CurrentUser = Depends(write_user),
    ):
        _detail_or_http(job_id, user)
        try:
            result = OfflinePatrolJobService().confirm_output_dispositions(
                job_id=job_id,
                actor=user.user_id,
                review_item_ids=payload.review_item_ids,
                reason=payload.reason,
            )
            _audit(
                request,
                user,
                "rule_asset_source_output_disposition_confirm",
                job_id,
                {
                    "review_item_ids": payload.review_item_ids,
                    "reason": payload.reason,
                },
            )
            return result
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @routes.post("/{job_id}/execute", status_code=status.HTTP_202_ACCEPTED)
    def execute_offline_patrol_job(
        job_id: str,
        request: Request,
        payload: OfflinePatrolExecuteRequest,
        user: CurrentUser = Depends(write_user),
    ):
        _detail_or_http(job_id, user)
        try:
            result = OfflinePatrolJobService().start_execution(
                job_id=job_id,
                owner_id=user.user_id,
                execution_idempotency_key=payload.idempotency_key,
                is_admin=user.is_admin(),
            )
            _audit(request, user, "rule_asset_source_execute", job_id, result)
            return result
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @routes.post("/{job_id}/cancel")
    def cancel_offline_patrol_job(
        job_id: str,
        request: Request,
        user: CurrentUser = Depends(write_user),
    ):
        _detail_or_http(job_id, user)
        try:
            task_id = OfflinePatrolJobService().cancel(job_id)
            if task_id:
                preview_offline_patrol_job.app.control.revoke(task_id, terminate=False)
            _audit(request, user, "rule_asset_source_cancel", job_id)
            return {"success": True, "job_id": job_id, "status": "cancelled"}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


    @routes.post("/{job_id}/retry")
    def retry_offline_patrol_job(
        job_id: str,
        request: Request,
        user: CurrentUser = Depends(write_user),
    ):
        _detail_or_http(job_id, user)
        try:
            OfflinePatrolJobService().retry(job_id)
            _audit(request, user, "rule_asset_source_retry", job_id)
            return _detail_or_http(job_id, user)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @routes.post("/{job_id}/archive")
    def archive_offline_patrol_job(
        job_id: str,
        request: Request,
        user: CurrentUser = Depends(write_user),
    ):
        _detail_or_http(job_id, user)
        try:
            OfflinePatrolJobService().archive(job_id, archived=True)
            _audit(request, user, "rule_asset_source_archive", job_id)
            return {"success": True, "job_id": job_id, "archived": True}
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @routes.post("/{job_id}/restore")
    def restore_offline_patrol_job(
        job_id: str,
        request: Request,
        user: CurrentUser = Depends(write_user),
    ):
        _detail_or_http(job_id, user)
        try:
            OfflinePatrolJobService().archive(job_id, archived=False)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _audit(request, user, "rule_asset_source_restore", job_id)
        return {"success": True, "job_id": job_id, "archived": False}


    @routes.get("/{job_id}/artifacts/{kind}")
    def download_offline_patrol_artifact(
        job_id: str,
        kind: Literal["raw", "changes", "events", "report", "excel"],
        user: CurrentUser = Depends(read_user),
    ):
        _detail_or_http(job_id, user)
        try:
            if kind == "raw":
                filename, content = OfflinePatrolJobService().build_original_bundle(job_id)
                media_type = "application/zip"
            else:
                filename, media_type, content = OfflinePatrolJobService().build_result_export(
                    job_id, kind
                )
            return Response(
                content=content,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    if extra_routes is not None:
        routes.include_router(extra_routes)
    return routes
