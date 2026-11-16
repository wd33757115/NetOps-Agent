"""拓扑绘图的后端：AI 编辑（SSE 流式）+ 图资产（版本化落库）。

- POST /ai-edit：SSE 事件流（started/reasoning/stage/done|error），
  成功后自动保存为新版本；
- GET /diagram：当前图 + 版本列表；
- POST /diagram/save：保存新版本（baseline/manual/import）；
- POST /diagram/restore：把历史版本复制成新版本（历史不可变）；
- GET /health：存根。
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.auth.models import CurrentUser
from src.platform.authorization.client import require_app

from . import repository

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


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/ai-edit")
async def ai_edit(body: AiEdit, request: Request, user: CurrentUser = Depends(actor)):
    from . import service

    async def stream():
        try:
            async for event in service.ai_edit_stream(
                user.user_id, body.instruction, body.xml, body.network_id
            ):
                yield _sse(event)
        except Exception as exc:  # 流式通道里的最后兜底：错误也要以 SSE 形式送达
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class DiagramSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network_id: str = Field(min_length=1, max_length=64)
    xml: str = Field(min_length=1, max_length=200_000)
    label: str = Field(default="", max_length=128)
    source: str = "manual"


class DiagramRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network_id: str = Field(min_length=1, max_length=64)
    version_id: str = Field(min_length=8, max_length=32)


@router.get("/diagram")
def get_diagram(network_id: str, user: CurrentUser = Depends(actor)):
    return repository.load_diagram(user.user_id, network_id) or {"xml": None, "versions": []}


@router.post("/diagram/save")
def save_diagram(body: DiagramSave, user: CurrentUser = Depends(actor)):
    version_id = repository.save_version(
        user.user_id, body.network_id, body.xml, body.label, body.source
    )
    return {"versionId": version_id}


@router.post("/diagram/restore")
def restore_diagram(body: DiagramRestore, user: CurrentUser = Depends(actor)):
    xml = repository.load_version_xml(user.user_id, body.network_id, body.version_id)
    original = repository.load_diagram(user.user_id, body.network_id)
    label = "回退"
    for row in (original or {}).get("versions", []):
        if row["id"] == body.version_id:
            label = f"回退到 {row['label']}"
            break
    version_id = repository.save_version(
        user.user_id, body.network_id, xml, label, "import"
    )
    return {"versionId": version_id, "xml": xml}
