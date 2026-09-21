"""Rule-source evidence and production operations owned by rule governance."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from pydantic import BaseModel, Field

from src.apps.rule_governance.backend.rule_control.command_asset_units import (
    RuleAssetCommandUnitService,
)
from src.apps.rule_governance.backend.rule_control.production_pipeline import (
    RuleAssetProductionPipelineService,
)
from src.apps.rule_governance.backend.access import app_user
from src.auth.models import CurrentUser
from src.gateway.offline_job_api import _audit, _detail_or_http, build_workspace_router

asset_source_routes = APIRouter()

class CommandAssetNotApplicableConfirmRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


class CommandAssetCheckItemAssociationRequest(BaseModel):
    check_item_id: str = Field(min_length=1, max_length=128)
    auto_continue: bool = False


class CommandAutopilotControlRequest(BaseModel):
    action: Literal["start", "pause", "resume", "authorize"]


class CommandAutopilotProjectionRequest(BaseModel):
    proposal: dict[str, Any]


@asset_source_routes.get("/{job_id}/asset-production-pipeline")
def get_rule_asset_source_production_pipeline(
    job_id: str,
    user: CurrentUser = Depends(app_user),
):
    _detail_or_http(job_id, user)
    try:
        return RuleAssetProductionPipelineService().get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@asset_source_routes.post("/{job_id}/command-asset-units/refresh")
def refresh_rule_asset_source_command_units(
    job_id: str,
    request: Request,
    user: CurrentUser = Depends(app_user),
):
    _detail_or_http(job_id, user)
    try:
        result = RuleAssetCommandUnitService().reconcile(job_id)
        _audit(
            request,
            user,
            "rule_asset_source_command_assets_refresh",
            job_id,
            {"unit_count": result["unit_count"]},
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@asset_source_routes.get("/{job_id}/command-asset-units")
def list_rule_asset_source_command_units(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    stage: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
    asset_mode: str | None = None,
    user: CurrentUser = Depends(app_user),
):
    _detail_or_http(job_id, user)
    try:
        return RuleAssetCommandUnitService().list_units(
            job_id,
            page=page,
            page_size=page_size,
            search=search,
            status=status_filter,
            stage=stage,
            vendor=vendor,
            model=model,
            asset_mode=asset_mode,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@asset_source_routes.get("/{job_id}/command-asset-units/{unit_id}")
def get_rule_asset_source_command_unit(
    job_id: str,
    unit_id: str,
    evidence_page: int = Query(1, ge=1),
    evidence_page_size: int = Query(25, ge=1, le=100),
    user: CurrentUser = Depends(app_user),
):
    _detail_or_http(job_id, user)
    try:
        return RuleAssetCommandUnitService().get_unit(
            job_id,
            unit_id,
            evidence_page=evidence_page,
            evidence_page_size=evidence_page_size,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@asset_source_routes.post(
    "/{job_id}/command-asset-units/{unit_id}/confirm-not-applicable"
)
def confirm_rule_asset_source_command_unit_not_applicable(
    job_id: str,
    unit_id: str,
    payload: CommandAssetNotApplicableConfirmRequest,
    request: Request,
    user: CurrentUser = Depends(app_user),
):
    _detail_or_http(job_id, user)
    try:
        result = RuleAssetCommandUnitService().confirm_not_applicable(
            job_id,
            unit_id,
            actor=user.username,
            reason=payload.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(
        request,
        user,
        "rule_asset_command_not_applicable_confirmed",
        job_id,
        {
            "unit_id": unit_id,
            "decision": result.get("decision"),
        },
    )
    return result


@asset_source_routes.post(
    "/{job_id}/command-asset-units/{unit_id}/check-item-association"
)
def associate_rule_asset_source_command_unit_check_item(
    job_id: str,
    unit_id: str,
    payload: CommandAssetCheckItemAssociationRequest,
    request: Request,
    user: CurrentUser = Depends(app_user),
):
    """Bind a sample command to B without creating a production adapter."""

    _detail_or_http(job_id, user)
    try:
        result = RuleAssetCommandUnitService().associate_unclassified_unit_to_check_item(
            job_id,
            unit_id,
            check_item_id=payload.check_item_id,
            actor=user.username,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(
        request,
        user,
        "rule_asset_sample_command_check_item_associated",
        job_id,
        {
            "unit_id": unit_id,
            "check_item_id": payload.check_item_id,
            "parser_job_id": result.get("parser_job_id"),
        },
    )
    if payload.auto_continue:
        from src.core.rule_control.command_autopilot import CommandAutopilotService
        try:
            result["autopilot"] = CommandAutopilotService().control(job_id, unit_id, action="start", actor=user.username,
                actor_user_id=user.user_id, authorize_publish=True)
        except ValueError as exc:
            # The manual binding has succeeded; do not invite a duplicate binding retry.
            result["autopilot_error"] = str(exc)
    return result


@asset_source_routes.get("/{job_id}/command-asset-units/{unit_id}/autopilot")
def get_command_autopilot(job_id: str, unit_id: str, user: CurrentUser = Depends(app_user)):
    from src.core.rule_control.command_autopilot import CommandAutopilotService
    _detail_or_http(job_id, user)
    return CommandAutopilotService().get(job_id, unit_id)


@asset_source_routes.post("/{job_id}/command-asset-units/{unit_id}/autopilot")
def control_command_autopilot(job_id: str, unit_id: str, payload: CommandAutopilotControlRequest,
                              request: Request, user: CurrentUser = Depends(app_user)):
    from src.core.rule_control.command_autopilot import CommandAutopilotService
    _detail_or_http(job_id, user)
    try:
        result = CommandAutopilotService().control(job_id, unit_id, action=payload.action, actor=user.username,
            actor_user_id=user.user_id, authorize_publish=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(request, user, "rule_asset_source_autopilot_control", job_id, {"unit_id": unit_id, "action": payload.action})
    return result


@asset_source_routes.put("/{job_id}/command-asset-units/{unit_id}/autopilot/projection")
def edit_command_autopilot_projection(job_id: str, unit_id: str, payload: CommandAutopilotProjectionRequest,
                                     request: Request, user: CurrentUser = Depends(app_user)):
    from src.core.rule_control.command_autopilot import CommandAutopilotService
    _detail_or_http(job_id, user)
    try:
        result = CommandAutopilotService().edit_projection(job_id, unit_id, proposal=payload.proposal,
            actor=user.username, actor_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(request, user, "rule_asset_source_autopilot_edit", job_id, {"unit_id": unit_id})
    return result


@asset_source_routes.delete("/{job_id}/command-asset-units/{unit_id}")
def delete_rule_asset_source_unclassified_command_unit(
    job_id: str,
    unit_id: str,
    request: Request,
    user: CurrentUser = Depends(app_user),
):
    """Remove one sample-generated, still-unclassified command from the inbox."""

    _detail_or_http(job_id, user)
    try:
        result = RuleAssetCommandUnitService().delete_unclassified_unit(
            job_id,
            unit_id,
            actor=user.username,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(
        request,
        user,
        "rule_asset_unclassified_command_deleted",
        job_id,
        {"unit_id": unit_id, "asset_key": result.get("asset_key")},
    )
    return result


router = build_workspace_router(
    "rule_asset_source", extra_routes=asset_source_routes, admission=app_user, read_user=app_user, write_user=app_user,
)
