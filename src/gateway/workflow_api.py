# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 查询与管理 API。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from src.auth.dependencies import get_optional_user, require_role
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
from src.core.workflows.dsl import GenerateOptions, WorkflowDSL
from src.core.workflows.engine import WorkflowEngine
from src.core.workflows.events import get_workflow_timeline
from src.core.workflows.generator import generate_and_persist, preview_workflow
from src.core.workflows.manager import (
    get_plugin_detail,
    preview_chat_intent,
    save_plugin,
    template_to_summary,
    validate_plugin_files,
    validate_workflow_yaml,
)
from src.core.workflows.mapping import build_mapping_suggestions
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
from src.core.workflows.registry import (
    WORKFLOWS_ROOT,
    get_template,
    list_templates,
    load_workflows,
    missing_test_context,
)
from src.core.workflows.repository import (
    get_workflow_run,
    list_child_runs,
    list_workflow_runs,
    list_workflow_steps,
    update_run_context,
)
from src.core.workflows.stream import stream_workflow_events
from src.gateway.schemas import (
    WorkflowChildRunSummary,
    WorkflowRunResponse,
    WorkflowStepResponse,
    WorkflowTimelineEvent,
)
from src.observability.deprecations import observe_legacy_path
from src.observability.langfuse import get_trace_url

# Compatibility routes only; all patrol business handlers live in the inspection App.
from src.apps.inspection.backend import batch_api as _patrol_api
from src.apps.inspection.backend.batch_api import (
    PatrolBatchCancelRequest,
    PatrolBatchGovernanceRequest,
    WorkflowRetryRequest,
    _artifact_object_key,
    _build_check_item_report_artifact,
    _build_patrol_batch_leadership_report,
    _build_patrol_batch_report,
    _build_patrol_batch_summary,
    _check_item_report_response,
    _device_inventory_text,
    _direct_batch_executions,
    _list_all_patrol_batch_devices,
    _list_patrol_batch_runs,
    _now_iso,
    _parse_optional_datetime,
    _patrol_batch_manifest_exists,
    _patrol_batch_sort_key,
    _raw_cli_verification_text,
    _resolve_raw_cli_manifest,
    _run_context,
    _run_summary_dict,
    _safe_archive_component,
    _snapshot_run_ids_for_runs,
    _write_original_cli_files,
    archive_patrol_batch,
    cancel_patrol_batch,
    export_patrol_batch_csv,
    export_patrol_batch_events,
    export_patrol_batch_evidence_package,
    export_patrol_batch_leadership_report,
    export_patrol_batch_leadership_report_json,
    export_patrol_batch_raw_cli,
    export_patrol_batch_raw_data,
    export_patrol_batch_report,
    export_patrol_batch_technical_workbook,
    get_patrol_batch_check_result_detail,
    get_patrol_batch_summary,
    list_patrol_batch_check_results,
    list_patrol_batch_devices,
    list_patrol_batches,
    rerun_patrol_batch,
    restore_patrol_batch,
    retry_patrol_batch,
)

router = APIRouter(prefix="/api/v1/workflows", tags=["Workflows"])

ACTIVE_PATROL_RUN_STATUSES = {
    "pending",
    "queued",
    "running",
    "in_progress",
    "collecting",
    "dispatching",
}


class ValidateWorkflowRequest(BaseModel):
    workflow_yaml: str = Field(..., description="WORKFLOW.yaml 内容")
    chat_intent_yaml: str | None = None


class SaveWorkflowPluginRequest(BaseModel):
    name: str = Field(..., description="插件目录名")
    category: str = Field(default="itsm", description="workflows 下子目录")
    files: dict[str, str] = Field(..., description="WORKFLOW.yaml / CHAT.intent.yaml 等")


class ChatIntentPreviewRequest(BaseModel):
    query: str = Field(..., description="测试话术")
    workflow_name: str | None = None
    chat_intent_yaml: str | None = None
    context: dict[str, Any] | None = None


class WorkflowTestRunRequest(BaseModel):
    template_name: str
    context: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = None
    thread_id: str | None = None








class WorkflowPreviewRequest(BaseModel):
    dsl: WorkflowDSL
    options: GenerateOptions = Field(default_factory=GenerateOptions)












def _is_patrol_batch_archived(runs: list[Any]) -> bool:
    return bool(runs) and all(bool(_run_context(run).get("patrol_batch_archived")) for run in runs)


def _assert_patrol_batch_not_archived(runs: list[Any]) -> None:
    if _is_patrol_batch_archived(runs):
        raise HTTPException(
            status_code=409, detail="patrol batch is archived; restore it before retry or rerun"
        )




















def _retry_existing_run(run, request: WorkflowRetryRequest, user: CurrentUser | None) -> str:
    context = dict(run.context or {})
    context["retry_of_run_id"] = run.id
    context.pop("langfuse_trace_id", None)
    context.pop("langfuse_workflow_root_span_id", None)
    user_id = request.user_id or (user.user_id if user else run.user_id)
    thread_id = request.thread_id or run.thread_id
    return WorkflowEngine.start(
        run.template_name,
        context,
        source=run.source or "retry",
        user_id=user_id,
        thread_id=thread_id,
        parent_run_id=run.id,
    )


def _rerun_existing_run(
    run,
    request: WorkflowRetryRequest,
    user: CurrentUser | None,
    *,
    new_batch_id: str,
    old_batch_id: str,
) -> str:
    context = dict(run.context or {})
    context["patrol_batch_id"] = new_batch_id
    context["rerun_of_batch_id"] = old_batch_id
    context["rerun_of_run_id"] = run.id
    context.pop("retry_of_run_id", None)
    context.pop("langfuse_trace_id", None)
    context.pop("langfuse_workflow_root_span_id", None)
    user_id = request.user_id or (user.user_id if user else run.user_id)
    thread_id = request.thread_id or run.thread_id
    return WorkflowEngine.start(
        run.template_name,
        context,
        source=run.source or "batch-rerun",
        user_id=user_id,
        thread_id=thread_id,
        parent_run_id=run.id,
    )


class WorkflowGenerateRequest(BaseModel):
    dsl: WorkflowDSL
    options: GenerateOptions = Field(default_factory=lambda: GenerateOptions(persist=True))


class InferMappingsRequest(BaseModel):
    dsl: WorkflowDSL


class ImportWorkflowRequest(BaseModel):
    bundle: dict[str, Any]
    overwrite: bool = False


class PublishWorkflowRequest(BaseModel):
    change_summary: str | None = None


class PublishToMarketRequest(BaseModel):
    title: str | None = None


class WorkflowDryRunRequest(BaseModel):
    dsl: WorkflowDSL
    context: dict[str, Any] = Field(default_factory=dict)
    auto_map_inputs: bool = True


class ChatIntentSuggestRequest(BaseModel):
    description: str = Field(..., min_length=2, description="自然语言触发场景描述")
    workflow_name: str = Field(..., description="Workflow 插件名")
    use_llm: bool = True


def _run_to_response(run, *, include_timeline: bool = True) -> WorkflowRunResponse:
    steps = list_workflow_steps(run.id)
    ctx = run.context or {}
    trace_id = ctx.get("langfuse_trace_id")
    timeline = get_workflow_timeline(run.id) if include_timeline else []
    children = list_child_runs(run.id)
    return WorkflowRunResponse(
        run_id=run.id,
        template_name=run.template_name,
        ticket_id=run.ticket_id,
        source=run.source,
        status=run.status,
        current_step_index=run.current_step_index,
        error_message=run.error_message,
        context=ctx,
        steps=[
            WorkflowStepResponse(
                step_index=s.step_index,
                step_name=s.step_name,
                skill_name=s.skill_name,
                status=s.status,
                celery_task_id=s.celery_task_id,
                output_artifacts=s.output_artifacts,
                error_message=s.error_message,
                started_at=s.started_at,
                completed_at=s.completed_at,
            )
            for s in steps
        ],
        timeline=[WorkflowTimelineEvent(**e) for e in timeline],
        child_runs=[
            WorkflowChildRunSummary(
                run_id=c.id,
                template_name=c.template_name,
                status=c.status,
                error_message=c.error_message,
            )
            for c in children
        ],
        langfuse_trace_id=str(trace_id) if trace_id else None,
        langfuse_url=get_trace_url(str(trace_id)) if trace_id else None,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.get("/templates")
async def list_workflow_templates():
    load_workflows(force=True)
    return [template_to_summary(t) for t in list_templates()]


@router.post("/preview")
async def preview_workflow_from_dsl(request: WorkflowPreviewRequest):
    """DSL → 插件 YAML 预览（不落盘）。"""
    opts = request.options.model_copy(update={"persist": False})
    return preview_workflow(request.dsl, options=opts)


@router.post("/generate")
async def generate_workflow_from_dsl(
    request: WorkflowGenerateRequest,
    user: CurrentUser | None = Depends(get_optional_user),
):
    """DSL → 生成插件文件，可选落盘与热加载。"""
    opts = request.options
    if opts.publish and (not user or not user.is_admin()):
        raise HTTPException(status_code=403, detail="发布 Workflow 需要 admin 权限")

    result = generate_and_persist(
        request.dsl,
        options=opts,
        user_id=user.user_id if user else None,
    )
    if not result.get("success") and result.get("validation", {}).get("errors"):
        raise HTTPException(
            status_code=400,
            detail={
                "message": result.get("message", "生成失败"),
                "validation": result.get("validation"),
            },
        )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "生成失败"))
    return result


@router.post("/infer-mappings")
async def infer_workflow_mappings(request: InferMappingsRequest):
    """返回各步骤的智能 inputs 映射建议。"""
    return {"suggestions": build_mapping_suggestions(request.dsl.steps)}


@router.post("/dry-run")
async def dry_run_workflow_api(request: WorkflowDryRunRequest):
    """模拟执行 Workflow（不启动 Celery）。"""
    from src.core.workflows.dry_run import dry_run_workflow

    return dry_run_workflow(
        request.dsl,
        request.context,
        auto_map_inputs=request.auto_map_inputs,
    )


@router.post("/chat-intent/suggest-nl")
async def suggest_chat_intent_from_nl_api(request: ChatIntentSuggestRequest):
    """自然语言描述 → CHAT.intent 规则草稿。"""
    from src.core.workflows.chat_intent_nl import suggest_chat_intent_from_nl

    return suggest_chat_intent_from_nl(
        request.description,
        request.workflow_name,
        use_llm=request.use_llm,
    )


@router.get("/expression-hints")
async def get_workflow_expression_hints(
    step_name: str | None = None,
    skill: str | None = None,
    workflow_name: str | None = None,
):
    """
    返回指定步骤可用的表达式提示（context / 上游 steps / 建议映射）。

    可通过 query 传 step_name + 临时 DSL 字段，或传 workflow_name 加载已注册插件。
    """
    from src.core.workflows.expression_hints import build_expression_hints

    if workflow_name:
        tpl = get_template(workflow_name)
        if not tpl:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_name}' 不存在")
        from src.core.workflows.dsl import WorkflowMetaDSL, WorkflowStepDSL

        dsl = WorkflowDSL(
            meta=WorkflowMetaDSL(name=tpl.name, description=tpl.description, version=tpl.version),
            steps=[
                WorkflowStepDSL(
                    id=s.name,
                    name=s.name,
                    label=s.label or s.name,
                    skill=s.skill_name,
                    when=s.when,
                )
                for s in tpl.steps
            ],
        )
        return build_expression_hints(dsl, step_name=step_name, skill=skill)

    raise HTTPException(
        status_code=400,
        detail="请使用 POST /expression-hints/preview 传入 DSL，或指定 workflow_name",
    )


class ExpressionHintsPreviewRequest(BaseModel):
    dsl: WorkflowDSL
    step_name: str | None = None
    skill: str | None = None


@router.post("/expression-hints/preview")
async def preview_expression_hints(request: ExpressionHintsPreviewRequest):
    """基于 DSL 预览表达式提示。"""
    from src.core.workflows.expression_hints import build_expression_hints

    return build_expression_hints(
        request.dsl,
        step_name=request.step_name,
        skill=request.skill,
    )


@router.get("/categories")
async def list_workflow_categories():
    """列出 workflows/ 下已有分类目录。"""
    load_workflows(force=True)
    categories = sorted(
        {p.name for p in WORKFLOWS_ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")}
    )
    return {"categories": categories or ["itsm", "custom"]}


# ---------------------------------------------------------------------------
# 插件治理（Phase 3）：元数据 / 版本 / 发布 / 导入导出 / 模板市场
# ---------------------------------------------------------------------------


@router.get("/plugins")
async def list_workflow_plugins():
    """列出插件（文件 + DB 元数据）。"""
    from src.core.workflows.versioning import list_plugins_enriched

    return list_plugins_enriched()


@router.get("/plugins/{name}/versions")
async def list_plugin_versions_api(name: str, limit: int = 50):
    from src.core.workflows.metadata_repo import list_plugin_versions, version_to_dict

    tpl = get_template(name)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"Workflow '{name}' 不存在")
    versions = list_plugin_versions(name, limit=limit)
    return [version_to_dict(v) for v in versions]


@router.get("/plugins/{name}/versions/diff")
async def diff_plugin_versions_api(name: str, v1: int, v2: int, file_key: str = "WORKFLOW.yaml"):
    from src.core.workflows.versioning import diff_plugin_versions

    try:
        return diff_plugin_versions(name, v1, v2, file_key=file_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/plugins/{name}/export")
async def export_workflow_plugin(name: str, format: str = "json"):
    from src.core.workflows.versioning import export_plugin_bundle, export_plugin_zip_bytes

    try:
        if format == "zip":
            data = export_plugin_zip_bytes(name)
            return Response(
                content=data,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
            )
        return export_plugin_bundle(name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/import")
async def import_workflow_plugin(
    request: ImportWorkflowRequest,
    user: CurrentUser | None = Depends(get_optional_user),
):
    from src.core.workflows.versioning import import_plugin_bundle

    result = import_plugin_bundle(
        request.bundle,
        overwrite=request.overwrite,
        user_id=user.user_id if user else None,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "导入失败"))
    return result


@router.post("/plugins/{name}/submit-review")
async def submit_plugin_review(
    name: str,
    user: CurrentUser = Depends(require_role(["admin", "operator"])),
):
    from src.core.workflows.metadata_repo import transition_plugin_status, upsert_plugin_metadata

    tpl = get_template(name)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"Workflow '{name}' 不存在")
    try:
        meta = transition_plugin_status(name, "review", user_id=user.user_id)
    except LookupError:
        meta = upsert_plugin_metadata(
            name,
            category=tpl.plugin_dir.parent.name,
            description=tpl.description,
            status="review",
            user_id=user.user_id,
        )
    return {"success": True, "status": meta.status, "message": "已提交审核"}


@router.post("/plugins/{name}/publish")
async def publish_workflow_plugin(
    name: str,
    request: PublishWorkflowRequest = PublishWorkflowRequest(),
    user: CurrentUser = Depends(require_role(["admin"])),
):
    from src.core.workflows.versioning import publish_plugin

    result = publish_plugin(name, user_id=user.user_id, change_summary=request.change_summary)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "发布失败"))
    return result


@router.post("/plugins/{name}/reject")
async def reject_plugin_review(
    name: str,
    user: CurrentUser = Depends(require_role(["admin"])),
):
    from src.core.workflows.metadata_repo import transition_plugin_status

    try:
        meta = transition_plugin_status(name, "draft", user_id=user.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "status": meta.status, "message": "已退回 draft"}


@router.post("/plugins/{name}/archive")
async def archive_workflow_plugin(
    name: str,
    user: CurrentUser = Depends(require_role(["admin"])),
):
    from src.core.workflows.metadata_repo import transition_plugin_status

    try:
        meta = transition_plugin_status(name, "archived", user_id=user.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "status": meta.status}


@router.delete("/plugins/{name}")
async def delete_workflow_plugin(
    name: str,
    user: CurrentUser = Depends(require_role(["admin", "operator"])),
):
    from src.core.workflows.versioning import delete_plugin

    result = delete_plugin(name, user_id=user.user_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message", "删除失败"))
    return result


@router.post("/plugins/{name}/publish-to-market")
async def publish_plugin_to_market_api(
    name: str,
    request: PublishToMarketRequest = PublishToMarketRequest(),
    user: CurrentUser = Depends(require_role(["admin", "operator"])),
):
    from src.core.workflows.marketplace import publish_plugin_to_market

    result = publish_plugin_to_market(name, title=request.title, user_id=user.user_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "发布失败"))
    return result


@router.get("/market/templates")
async def list_market_templates(category: str | None = None, featured: bool = False):
    from src.core.workflows.marketplace import list_market_templates

    return list_market_templates(category=category, featured_only=featured)


@router.get("/market/templates/{template_id}")
async def get_market_template_detail(template_id: str):
    from src.core.workflows.marketplace import get_market_template, increment_market_use

    detail = get_market_template(template_id)
    if not detail:
        raise HTTPException(status_code=404, detail="模板不存在")
    increment_market_use(template_id)
    return detail


@router.get("/templates/{name}")
async def get_workflow_template(name: str):
    detail = get_plugin_detail(name)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Workflow 模板 '{name}' 不存在")
    return detail


@router.get("/templates/{name}/dsl")
async def get_workflow_template_dsl(name: str):
    """将已有插件 YAML 反解析为 WorkflowDSL，供 Wizard 编辑。"""
    from src.core.workflows.generator import dsl_from_plugin_files
    from src.core.workflows.registry import get_template

    tpl = get_template(name)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"Workflow 模板 '{name}' 不存在")
    detail = get_plugin_detail(name)
    assert detail is not None
    category = tpl.plugin_dir.parent.name if tpl.plugin_dir.parent != WORKFLOWS_ROOT else "itsm"
    dsl = dsl_from_plugin_files(detail["files"], category=category)
    return {
        "success": True,
        "dsl": dsl.model_dump(),
        "chat_intent_yaml": detail["files"].get("CHAT.intent.yaml") or "",
        "webhook_yaml": detail["files"].get("ITSM.webhook.yaml") or "",
    }


@router.post("/reload")
async def reload_workflows():
    from src.core.workflows.reload_bus import broadcast_workflow_reload

    stats = broadcast_workflow_reload(source="api")
    return {
        "success": True,
        "count": stats.get("templates", 0),
        "intents": stats.get("intents", 0),
        "message": "Workflow 插件已重载（含多 Worker 广播）",
    }


@router.post("/validate")
async def validate_workflow(request: ValidateWorkflowRequest):
    if request.chat_intent_yaml:
        return validate_plugin_files(
            {"WORKFLOW.yaml": request.workflow_yaml, "CHAT.intent.yaml": request.chat_intent_yaml}
        )
    return validate_workflow_yaml(request.workflow_yaml)


@router.post("/templates", status_code=201)
async def create_workflow_plugin(request: SaveWorkflowPluginRequest):
    result = save_plugin(request.name, category=request.category, files=request.files)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "保存失败"))
    return result


@router.put("/templates/{name}")
async def update_workflow_plugin(name: str, request: SaveWorkflowPluginRequest):
    tpl = get_template(name)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"Workflow 模板 '{name}' 不存在")
    category = request.category or tpl.plugin_dir.parent.name
    result = save_plugin(name, category=category, files=request.files)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "保存失败"))
    return result


@router.post("/chat-intent/preview")
async def preview_chat_intent_match(request: ChatIntentPreviewRequest):
    return preview_chat_intent(
        request.query,
        workflow_name=request.workflow_name,
        chat_intent_yaml=request.chat_intent_yaml,
        context=request.context,
    )


@router.get("/runs")
async def list_runs(
    limit: int = 50,
    template_name: str | None = None,
    ticket_id: str | None = None,
    source: str | None = None,
    status: str | None = None,
    device_id: str | None = None,
    patrol_batch_id: str | None = None,
):
    runs = list_workflow_runs(
        limit=limit,
        template_name=template_name,
        ticket_id=ticket_id,
        source=source,
        status=status,
        device_id=device_id,
        patrol_batch_id=patrol_batch_id,
    )
    return [_run_summary_dict(r) for r in runs]
































def _device_inventory_csv(devices: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    fields = [
        "device_id",
        "device_name",
        "management_ip",
        "selection_status",
        "preflight_status",
        "connection_status",
        "workflow_status",
        "execution_status",
        "execution_phase",
        "celery_task_id",
        "snapshot_run_id",
        "failure_code",
        "error_message",
        "raw_cli_file_count",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(devices)
    return ("\ufeff" + output.getvalue()).encode("utf-8")
























@router.post("/runs/test")
async def test_run_workflow(
    request: WorkflowTestRunRequest,
    user: CurrentUser | None = Depends(get_optional_user),
):
    tpl = get_template(request.template_name)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"未知 Workflow: {request.template_name}")
    ctx = dict(request.context)
    missing = missing_test_context(tpl, ctx)
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Workflow 试跑上下文缺少必填字段",
                "missing_context": missing,
            },
        )
    user_id = request.user_id or (user.user_id if user else None)
    try:
        run_id = WorkflowEngine.start(
            request.template_name,
            ctx,
            source="test",
            user_id=user_id,
            thread_id=request.thread_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run = get_workflow_run(run_id)
    return {
        "success": True,
        "run_id": run_id,
        "message": f"已启动试跑 Workflow `{request.template_name}`",
        "query_endpoint": f"/api/v1/workflows/{run_id}",
        "run": _run_to_response(run) if run else None,
    }


@router.post("/{run_id}/retry")
async def retry_workflow_run(
    run_id: str,
    request: WorkflowRetryRequest = WorkflowRetryRequest(),
    user: CurrentUser | None = Depends(get_optional_user),
):
    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow 不存在")
    if run.status not in {"failed", "cancelled"}:
        raise HTTPException(
            status_code=400, detail="only failed or cancelled workflow runs can be retried"
        )
    try:
        new_run_id = _retry_existing_run(run, request, user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    new_run = get_workflow_run(new_run_id)
    return {
        "success": True,
        "run_id": new_run_id,
        "retry_of_run_id": run.id,
        "message": f"已重新启动 Workflow `{run.template_name}`",
        "query_endpoint": f"/api/v1/workflows/{new_run_id}",
        "run": _run_to_response(new_run) if new_run else None,
    }


@router.get("/{run_id}/timeline")
async def get_workflow_timeline_api(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow 不存在")
    events = get_workflow_timeline(run_id)
    ctx = run.context or {}
    trace_id = ctx.get("langfuse_trace_id")
    return {
        "run_id": run_id,
        "status": run.status,
        "events": events,
        "langfuse_trace_id": trace_id,
        "langfuse_url": get_trace_url(str(trace_id)) if trace_id else None,
    }


@router.get("/{run_id}/events/stream")
async def stream_workflow_run_events(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow 不存在")

    async def event_generator():
        async for chunk in stream_workflow_events(run_id):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow 不存在")
    return _run_to_response(run)



for _route in _patrol_api.router.routes:
    router.routes.append(_route)
