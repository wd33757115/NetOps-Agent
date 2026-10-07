from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError

from src.auth.models import CurrentUser
from src.platform.authorization.client import require_app
from src.platform.device_access.contracts import DeviceAccessError

from . import logins, service
from .schemas import Credentials, Discovery, Import, Resolve

router = APIRouter(tags=["Topology"])
actor = require_app("topology")


def mutation(user):
    if not user.can_execute_skills():
        raise HTTPException(403, "当前账号不能执行设备操作")


def call(fn, *args):
    try:
        return fn(*args)
    except DeviceAccessError as exc:
        raise HTTPException(422, {"code": exc.failure_code, "message": str(exc)}) from None
    except IntegrityError:
        raise HTTPException(409, "任务或节点状态已变化，请刷新后重试") from None


@router.get("/default-login")
def read_default(user: CurrentUser = Depends(actor)):
    return logins.read_default(user.user_id)


@router.put("/default-login")
def save_default(body: Credentials, user: CurrentUser = Depends(actor)):
    mutation(user)
    return call(logins.save_default, user.user_id, body)


@router.get("/networks")
def networks(user: CurrentUser = Depends(actor)):
    return service.networks(user.user_id)


@router.post("/networks/{network_id}/activate")
def activate(network_id: str, user: CurrentUser = Depends(actor)):
    return service.activate(user.user_id, network_id)


@router.get("/networks/{network_id}/runs")
def runs(network_id: str, user: CurrentUser = Depends(actor)):
    return service.runs(user.user_id, network_id)


@router.post("/runs", status_code=202)
def submit(body: Discovery, user: CurrentUser = Depends(actor)):
    mutation(user)
    return call(service.submit, user.user_id, body)


@router.post("/imports", status_code=201)
def import_data(body: Import, user: CurrentUser = Depends(actor)):
    mutation(user)
    return call(service.import_captures, user.user_id, body)


@router.get("/runs/{run_id}")
def read(run_id: str, user: CurrentUser = Depends(actor)):
    return service.read(user.user_id, run_id)


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, user: CurrentUser = Depends(actor)):
    mutation(user)
    return service.cancel(user.user_id, run_id)


@router.post("/runs/{run_id}/targets/{target_id}/resolve")
def resolve(run_id: str, target_id: str, body: Resolve, user: CurrentUser = Depends(actor)):
    mutation(user)
    return call(service.resolve, user.user_id, run_id, target_id, body)


@router.get("/snapshots/{snapshot_id}")
def snapshot(snapshot_id: str, user: CurrentUser = Depends(actor)):
    return service.snapshot(user.user_id, snapshot_id)


@router.get("/snapshots/{snapshot_id}/locate")
def locate(
    snapshot_id: str,
    q: str = Query(min_length=1, max_length=64),
    user: CurrentUser = Depends(actor),
):
    return service.locate(user.user_id, snapshot_id, q)


@router.get("/snapshots/{snapshot_id}/state")
def snapshot_state(snapshot_id: str, user: CurrentUser = Depends(actor)):
    return service.snapshot_state(user.user_id, snapshot_id)


@router.get("/snapshots/{snapshot_id}/devices/{address}")
def device_model(snapshot_id: str, address: str, user: CurrentUser = Depends(actor)):
    return service.device_model(user.user_id, snapshot_id, address)


@router.get("/snapshots/{snapshot_id}/path")
def trace(
    snapshot_id: str,
    src: str = Query(min_length=1, max_length=64),
    dst: str = Query(min_length=1, max_length=64),
    src_vrf: str | None = Query(default=None, min_length=1, max_length=64),
    dst_vrf: str | None = Query(default=None, min_length=1, max_length=64),
    protocol: str | None = Query(default=None, min_length=1, max_length=8),
    src_port: int | None = Query(default=None, ge=1, le=65535),
    dst_port: int | None = Query(default=None, ge=1, le=65535),
    user: CurrentUser = Depends(actor),
):
    return service.trace(
        user.user_id,
        snapshot_id,
        src,
        dst,
        src_vrf,
        dst_vrf,
        flow={"protocol": protocol, "src_port": src_port, "dst_port": dst_port},
    )


@router.get("/snapshots/{snapshot_id}/services")
def services(snapshot_id: str, user: CurrentUser = Depends(actor)):
    return service.services(user.user_id, snapshot_id)


@router.get("/snapshots/{snapshot_id}/rfc8345")
def export(snapshot_id: str, user: CurrentUser = Depends(actor)):
    return service.export(user.user_id, snapshot_id)


@router.get("/snapshots/{snapshot_id}/diff/{before_id}")
def compare(snapshot_id: str, before_id: str, user: CurrentUser = Depends(actor)):
    return service.compare(user.user_id, before_id, snapshot_id)


@router.get("/issues")
def collection_issues(
    network_id: str | None = None,
    include_cleared: bool = False,
    user: CurrentUser = Depends(actor),
):
    return service.collection_issues(user.user_id, network_id, include_cleared)


@router.get("/evidence/{observation_id}")
def evidence(observation_id: str, user: CurrentUser = Depends(actor)):
    return call(service.evidence, user.user_id, observation_id)
