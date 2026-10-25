# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skills 管理 API 路由。"""

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.gateway.schemas import (
    CreateSkillRequest,
    SkillContentRequest,
    SkillFileUploadRequest,
    SkillToggleRequest,
    UpdateSkillRequest,
)
from src.skills.skill_manager import get_skill_manager

router = APIRouter(prefix="/api/v1/skills", tags=["Skills"])


class SkillTestRunRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class SkillRolloutUpdateRequest(BaseModel):
    rollout_status: str | None = Field(None, description="draft/canary/stable/deprecated")
    enabled_ratio: int | None = Field(None, ge=0, le=100, description="灰度比例 0-100")
    min_platform_version: str | None = None
    enabled: bool | None = None


class ArchiveExecutionsRequest(BaseModel):
    before_days: int | None = Field(None, ge=1, le=3650)
    batch_size: int | None = Field(None, ge=1, le=5000)


def _manager():
    return get_skill_manager()


def _ensure_success(result: dict, default_status: int = 400):
    if not result.get("success", True):
        raise HTTPException(status_code=default_status, detail=result.get("message", "操作失败"))
    return result


@router.get("/catalog/stats")
async def catalog_stats():
    from src.skill_system.catalog.service import SkillCatalogService

    return SkillCatalogService.get_stats()


@router.post("/catalog/reindex")
async def catalog_reindex(force: bool = False):
    from src.skill_system import get_skill_system
    from src.skill_system.catalog import sync_and_index

    skill_system = get_skill_system()
    stats = sync_and_index(skill_system.loader.list_all_metadata(), index=not force)
    if force:
        from src.skill_system.catalog.service import SkillCatalogService

        stats.update(SkillCatalogService.index_embeddings(force=True))
    return {"success": True, **stats}


@router.patch("/catalog/{skill_name}/rollout")
async def update_skill_rollout(skill_name: str, request: SkillRolloutUpdateRequest):
    from src.skill_system.catalog.repository import get_catalog_entry, update_catalog_rollout

    if not get_catalog_entry(skill_name):
        raise HTTPException(status_code=404, detail=f"Catalog 中不存在 Skill: {skill_name}")
    updated = update_catalog_rollout(
        skill_name,
        rollout_status=request.rollout_status,
        enabled_ratio=request.enabled_ratio,
        min_platform_version=request.min_platform_version,
        enabled=request.enabled,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="更新灰度配置失败")
    return {"success": True, "catalog": updated}


@router.post("/governance/archive-executions")
async def archive_skill_executions_api(
    request: ArchiveExecutionsRequest = ArchiveExecutionsRequest(),
):
    from src.core.skills.archive import archive_skill_executions

    result = archive_skill_executions(
        before_days=request.before_days,
        batch_size=request.batch_size,
    )
    if result.get("error"):
        raise HTTPException(status_code=503, detail=result["error"])
    return {"success": True, **result}


@router.get("")
async def list_skills():
    return _manager().list_all_skills()


@router.get("/stats")
async def skill_stats():
    return _manager().get_stats()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_skill(request: CreateSkillRequest):
    data = request.model_dump(exclude_none=True)
    return _ensure_success(_manager().create_skill(data))


@router.get("/{skill_name}/content")
async def get_skill_content(skill_name: str):
    content = _manager().get_skill_content(skill_name)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' 不存在")
    return {"name": skill_name, "content": content}


@router.put("/{skill_name}/content")
async def save_skill_content(skill_name: str, request: SkillContentRequest):
    return _ensure_success(_manager().save_skill_content(skill_name, request.content))


@router.get("/{skill_name}/files")
async def list_skill_files(skill_name: str):
    result = _manager().list_skill_files(skill_name)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message", "Skill 不存在"))
    return result


@router.post("/{skill_name}/files")
async def upload_skill_file(skill_name: str, request: SkillFileUploadRequest):
    return _ensure_success(
        _manager().upload_skill_file(
            skill_name,
            request.folder,
            request.filename,
            request.file_content,
        )
    )


@router.patch("/{skill_name}/toggle")
async def toggle_skill(skill_name: str, request: SkillToggleRequest):
    return _ensure_success(_manager().toggle_skill(skill_name, request.enabled))


@router.post("/{skill_name}/reload")
async def reload_skill(skill_name: str):
    return _ensure_success(_manager().reload_skill(skill_name))


@router.post("/{skill_name}/test-run")
def test_run_skill(skill_name: str, request: SkillTestRunRequest = SkillTestRunRequest()):
    """同步试跑 Skill（用于向导内测试）。"""
    from src.platform.kernel.registry import migrated_skill_app
    if migrated_skill_app(skill_name):
        raise HTTPException(410, "该能力已迁入业务应用，请从工作台使用")
    from src.core.skills.executor import SkillExecutionError, execute_skill

    params = request.params
    try:
        result = execute_skill(skill_name, params)
    except SkillExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": result.get("success", True), "result": result}


@router.get("/{skill_name}/schema")
async def get_skill_schema(skill_name: str):
    """返回 Skill I/O schema，供 Workflow Builder 参数配置。"""
    schema = _manager().get_skill_schema(skill_name)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' 不存在")
    return schema


@router.post("/reload-all")
async def reload_all_skills():
    return _ensure_success(_manager().reload_all())


@router.put("/{skill_name}")
async def update_skill(skill_name: str, request: UpdateSkillRequest):
    data = request.model_dump(exclude_none=True)
    data["name"] = skill_name
    return _ensure_success(_manager().update_skill(skill_name, data))


@router.delete("/{skill_name}", status_code=status.HTTP_200_OK)
async def delete_skill(skill_name: str):
    return _ensure_success(_manager().delete_skill(skill_name))
