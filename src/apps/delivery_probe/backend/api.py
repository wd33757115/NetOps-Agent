from fastapi import APIRouter, Depends

from src.platform.authorization.client import require_app

from . import service

router = APIRouter()
user = require_app("delivery-probe")


@router.post("/runs")
def submit(payload: service.Submit, actor=Depends(user)):
    return service.submit(actor, payload)


@router.get("/runs/{run_id}")
def read(run_id: str, actor=Depends(user)):
    return service.read(actor, run_id)
