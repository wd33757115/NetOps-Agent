from fastapi import APIRouter, Depends, Query

from src.platform.authorization.client import require_app

from . import service

router = APIRouter()
user = require_app(service.APP_ID)


@router.get("/definitions")
def definitions(actor=Depends(user)):
    return service.list_definitions(actor)


@router.get("/definitions/{definition_id}")
def definition(definition_id: str, actor=Depends(user)):
    return service.read_definition(actor, definition_id)


@router.get("/definitions/{definition_id}/versions/{version}")
def definition_version(definition_id: str, version: int, actor=Depends(user)):
    return service.read_version(actor, definition_id, version)


@router.get("/check-items/{check_item}/contract")
def contract(check_item: str, vendor: str, platform: str, actor=Depends(user)):
    return service.read_contract(actor, check_item, vendor, platform)


@router.put("/definitions/{definition_id}/draft")
def save_draft(definition_id: str, payload: service.Draft, actor=Depends(user)):
    return service.save_draft(actor, definition_id, payload)


@router.post("/definitions/{definition_id}/discard")
def discard_draft(definition_id: str, payload: service.Discard, actor=Depends(user)):
    return service.discard_draft(actor, definition_id, payload)


@router.post("/definitions/{definition_id}/publish")
def publish(definition_id: str, payload: service.Publish, actor=Depends(user)):
    return service.publish(actor, definition_id, payload)


@router.get("/devices")
def devices(actor=Depends(user)):
    return service.list_devices(actor)


@router.post("/devices/{device_id}/capabilities")
def discover(device_id: str, actor=Depends(user)):
    return service.discover(actor, device_id)


@router.post("/devices/{device_id}/browse")
def browse(device_id: str, payload: service.BrowseRequest, actor=Depends(user)):
    return service.browse(actor, device_id, payload)


@router.get("/devices/{device_id}/capabilities")
def capabilities(device_id: str, actor=Depends(user)):
    return service.read_capabilities(actor, device_id)


@router.post("/trials")
def trial(payload: service.TrialRequest, actor=Depends(user)):
    return service.trial(actor, payload)


@router.get("/trials")
def trials(
    definition_id: str | None = None, limit: int = Query(50, ge=1, le=200), actor=Depends(user)
):
    return service.list_trials(actor, definition_id, limit)


@router.get("/trials/{trial_id}")
def read_trial(trial_id: str, actor=Depends(user)):
    return service.read_trial(actor, trial_id)
