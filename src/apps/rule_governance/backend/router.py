from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError

from .access import app_user
from .api import router as parser
from .rule_control.generation_jobs import GenerationJobs
from .source_api import router as sources

router = APIRouter()
router.include_router(sources, prefix="/parser/asset-source-jobs")
router.include_router(parser)

from src.gateway.devices_api import router as inventory

for route in inventory.routes:
    suffix = route.path.removeprefix("/api/v1/devices")
    if suffix.startswith("/patrol/") or suffix.endswith("/effective-plan"):
        continue
    router.add_api_route(
        "/devices" + suffix,
        route.endpoint,
        methods=list(route.methods),
        dependencies=[Depends(app_user)],
    )


class Resolution(BaseModel):
    note: str = Field(min_length=5, max_length=2000)


@router.get("/generation-runs/latest/{seed_id}")
def latest_generation(seed_id: str, user=Depends(app_user)):
    return GenerationJobs().latest(seed_id, user_id=user.user_id, is_admin=user.is_admin())


@router.get("/generation-runs/{run_id}")
def generation_run(run_id: str, user=Depends(app_user)):
    try:
        return GenerationJobs().read(run_id, user_id=user.user_id, is_admin=user.is_admin())
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/generation-runs/{run_id}/resolve")
def resolve_generation(run_id: str, body: Resolution, user=Depends(app_user)):
    try:
        return GenerationJobs().close(run_id, user_id=user.user_id, note=body.note)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except OperationalError as exc:
        raise HTTPException(409, "生成任务仍在写入，请稍后重试") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
