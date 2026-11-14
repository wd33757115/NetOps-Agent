"""拓扑绘图的后端：AI 编辑通道（画布数据由前端直读拓扑发现的公开 API）。

两块职责：
- /ai-edit：指令 + 当前画布 XML → DSH（drawio 模式）→ 新画布 XML；
- /health：存根。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.auth.models import CurrentUser
from src.platform.authorization.client import require_app

router = APIRouter(tags=["拓扑绘图"])
actor = require_app("topology-draw")


@router.get("/health")
def health(user: CurrentUser = Depends(actor)):
    return {"app": "topology-draw", "status": "ok"}


class AiEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=2000)
    xml: str = Field(min_length=1, max_length=200_000)
    network_id: str = Field(min_length=1, max_length=64)


@router.post("/ai-edit")
async def ai_edit(body: AiEdit, user: CurrentUser = Depends(actor)):
    from . import service

    try:
        return await service.ai_edit(user.user_id, body.instruction, body.xml, body.network_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


class AiEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=2000)
    xml: str = Field(min_length=1, max_length=200_000)
    network_id: str = Field(min_length=1, max_length=64)


@router.post("/ai-edit")
async def ai_edit(body: AiEdit, user: CurrentUser = Depends(actor)):
    from . import service

    try:
        return await service.ai_edit(user.user_id, body.instruction, body.xml, body.network_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
