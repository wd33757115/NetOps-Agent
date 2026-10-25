from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from src.platform.authorization.client import require_app

from . import circuits, importer, inventory, resilience, service
from .schemas import (
    AddressFields,
    AllocateIn,
    CircuitIn,
    CircuitTypeIn,
    HostIn,
    LevelsIn,
    NetworkIn,
    PopIn,
    PrefixFields,
    PrefixIn,
    ProviderIn,
    RegionIn,
    ReservationIn,
    RiskIn,
    SiteIn,
    SpaceIn,
    SubsystemIn,
    SystemIn,
    ZoneIn,
)

router = APIRouter()
user = require_app("resource-ledger")
IMPORT_LIMIT = 10 * 1024 * 1024


@router.get("/overview")
def overview(actor=Depends(user)):
    return service.overview(actor)


@router.post("/regions")
def create_region(body: RegionIn, actor=Depends(user)):
    return service.save_region(actor, body)


@router.put("/regions/{region_id}")
def update_region(region_id: str, body: RegionIn, actor=Depends(user)):
    return service.save_region(actor, body, region_id)


@router.delete("/regions/{region_id}")
def delete_region(region_id: str, actor=Depends(user)):
    return service.delete_region(actor, region_id)


@router.post("/sites")
def create_site(body: SiteIn, actor=Depends(user)):
    return service.save_site(actor, body)


@router.put("/sites/{site_id}")
def update_site(site_id: str, body: SiteIn, actor=Depends(user)):
    return service.save_site(actor, body, site_id)


@router.delete("/sites/{site_id}")
def delete_site(site_id: str, actor=Depends(user)):
    return service.delete_site(actor, site_id)


@router.get("/spaces")
def spaces(actor=Depends(user)):
    return service.spaces(actor)


@router.post("/spaces")
def create_space(body: SpaceIn, actor=Depends(user)):
    return service.save_space(actor, body)


@router.put("/spaces/{space_id}")
def update_space(space_id: str, body: SpaceIn, actor=Depends(user)):
    return service.save_space(actor, body, space_id)


@router.delete("/spaces/{space_id}")
def delete_space(space_id: str, actor=Depends(user)):
    return service.delete_space(actor, space_id)


@router.put("/levels")
def save_levels(body: LevelsIn, actor=Depends(user)):
    return service.save_levels(actor, body)


@router.get("/resources")
def resources(actor=Depends(user)):
    return service.resources(actor)


@router.get("/zones")
def zones(actor=Depends(user)):
    return service.zones_page(actor)


@router.post("/zones")
def create_zone(body: ZoneIn, actor=Depends(user)):
    return service.save_zone(actor, body)


@router.put("/zones/{zone_id}")
def update_zone(zone_id: str, body: ZoneIn, actor=Depends(user)):
    return service.save_zone(actor, body, zone_id)


@router.delete("/zones/{zone_id}")
def delete_zone(zone_id: str, actor=Depends(user)):
    return service.delete_zone(actor, zone_id)


@router.post("/prefixes")
def create_prefix(body: PrefixIn, actor=Depends(user)):
    return service.create_prefix(actor, body)


@router.get("/prefixes/{prefix_id}")
def prefix_detail(prefix_id: str, actor=Depends(user)):
    return service.prefix_detail(actor, prefix_id)


@router.get("/prefixes/{prefix_id}/tree")
def subtree(prefix_id: str, actor=Depends(user)):
    return service.subtree(actor, prefix_id)


@router.get("/prefixes/{prefix_id}/carve")
def carve_options(
    prefix_id: str,
    level_id: str | None = Query(None, max_length=32),
    length: int | None = Query(None, ge=1, le=32),
    actor=Depends(user),
):
    return service.carve_options(actor, prefix_id, level_id, length)


@router.put("/prefixes/{prefix_id}")
def update_prefix(prefix_id: str, body: PrefixFields, actor=Depends(user)):
    return service.update_prefix(actor, prefix_id, body)


@router.delete("/prefixes/{prefix_id}")
def delete_prefix(prefix_id: str, actor=Depends(user)):
    return service.delete_prefix(actor, prefix_id)


@router.post("/prefixes/{prefix_id}/addresses")
def allocate(prefix_id: str, body: AllocateIn, actor=Depends(user)):
    return service.allocate(actor, prefix_id, body)


@router.put("/addresses/{address_id}")
def update_address(address_id: str, body: AddressFields, actor=Depends(user)):
    return service.update_address(actor, address_id, body)


@router.delete("/addresses/{address_id}")
def release_address(address_id: str, actor=Depends(user)):
    return service.release_address(actor, address_id)


@router.post("/prefixes/{prefix_id}/reservations")
def reserve(prefix_id: str, body: ReservationIn, actor=Depends(user)):
    return service.reserve(actor, prefix_id, body)


@router.delete("/reservations/{reservation_id}")
def unreserve(reservation_id: str, actor=Depends(user)):
    return service.unreserve(actor, reservation_id)


@router.get("/systems")
def systems(actor=Depends(user)):
    return inventory.systems_page(actor)


@router.post("/systems")
def create_system(body: SystemIn, actor=Depends(user)):
    return inventory.save_system(actor, body)


@router.put("/systems/{system_id}")
def update_system(system_id: str, body: SystemIn, actor=Depends(user)):
    return inventory.save_system(actor, body, system_id)


@router.delete("/systems/{system_id}")
def delete_system(system_id: str, actor=Depends(user)):
    return inventory.delete_system(actor, system_id)


@router.post("/subsystems")
def create_subsystem(body: SubsystemIn, actor=Depends(user)):
    return inventory.save_subsystem(actor, body)


@router.put("/subsystems/{subsystem_id}")
def update_subsystem(subsystem_id: str, body: SubsystemIn, actor=Depends(user)):
    return inventory.save_subsystem(actor, body, subsystem_id)


@router.delete("/subsystems/{subsystem_id}")
def delete_subsystem(subsystem_id: str, actor=Depends(user)):
    return inventory.delete_subsystem(actor, subsystem_id)


@router.get("/hosts")
def hosts(
    system_id: str | None = Query(None, max_length=32),
    subsystem_id: str | None = Query(None, max_length=32),
    status: str | None = Query(None, max_length=10),
    environment: str | None = Query(None, max_length=10),
    q: str | None = Query(None, max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    actor=Depends(user),
):
    return inventory.hosts_page(actor, system_id, subsystem_id, status, environment, q, offset, limit)


@router.get("/hosts/suggest-ip")
def suggest_ip(
    system_id: str = Query(..., max_length=32),
    subsystem_id: str | None = Query(None, max_length=32),
    taken: list[str] = Query([], max_length=16),
    actor=Depends(user),
):
    return inventory.suggest_ip(actor, system_id, subsystem_id, taken)


@router.get("/hosts/{host_id}")
def host_detail(host_id: str, actor=Depends(user)):
    return inventory.host_detail(actor, host_id)


@router.post("/hosts")
def create_host(body: HostIn, actor=Depends(user)):
    return inventory.save_host(actor, body)


@router.put("/hosts/{host_id}")
def update_host(host_id: str, body: HostIn, actor=Depends(user)):
    return inventory.save_host(actor, body, host_id)


@router.delete("/hosts/{host_id}")
def delete_host(host_id: str, actor=Depends(user)):
    return inventory.delete_host(actor, host_id)


@router.get("/circuit-options")
def circuit_options(actor=Depends(user)):
    return circuits.options(actor)


@router.get("/circuits")
def circuit_list(
    q: str | None = Query(None, max_length=100),
    provider_id: str | None = Query(None, max_length=32),
    type_id: str | None = Query(None, max_length=32),
    status: str | None = Query(None, max_length=10),
    site_id: str | None = Query(None, max_length=32),
    single_point: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    actor=Depends(user),
):
    return circuits.circuits_page(actor, q, provider_id, type_id, status, site_id, offset, limit, single_point)


@router.post("/circuits")
def create_circuit(body: CircuitIn, actor=Depends(user)):
    return circuits.save_circuit(actor, body)


@router.get("/circuits/{circuit_id}")
def circuit_detail(circuit_id: str, actor=Depends(user)):
    return circuits.circuit_detail(actor, circuit_id)


@router.put("/circuits/{circuit_id}")
def update_circuit(circuit_id: str, body: CircuitIn, actor=Depends(user)):
    return circuits.save_circuit(actor, body, circuit_id)


@router.delete("/circuits/{circuit_id}")
def delete_circuit(circuit_id: str, actor=Depends(user)):
    return circuits.delete_circuit(actor, circuit_id)


@router.get("/providers")
def providers(actor=Depends(user)):
    return circuits.providers_page(actor)


@router.post("/providers")
def create_provider(body: ProviderIn, actor=Depends(user)):
    return circuits.save_provider(actor, body)


@router.put("/providers/{provider_id}")
def update_provider(provider_id: str, body: ProviderIn, actor=Depends(user)):
    return circuits.save_provider(actor, body, provider_id)


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: str, actor=Depends(user)):
    return circuits.delete_provider(actor, provider_id)


@router.post("/provider-networks")
def create_network(body: NetworkIn, actor=Depends(user)):
    return circuits.save_network(actor, body)


@router.put("/provider-networks/{network_id}")
def update_network(network_id: str, body: NetworkIn, actor=Depends(user)):
    return circuits.save_network(actor, body, network_id)


@router.delete("/provider-networks/{network_id}")
def delete_network(network_id: str, actor=Depends(user)):
    return circuits.delete_network(actor, network_id)


@router.post("/pops")
def create_pop(body: PopIn, actor=Depends(user)):
    return circuits.save_pop(actor, body)


@router.put("/pops/{pop_id}")
def update_pop(pop_id: str, body: PopIn, actor=Depends(user)):
    return circuits.save_pop(actor, body, pop_id)


@router.delete("/pops/{pop_id}")
def delete_pop(pop_id: str, actor=Depends(user)):
    return circuits.delete_pop(actor, pop_id)


@router.get("/circuit-types")
def circuit_types(actor=Depends(user)):
    return circuits.types_page(actor)


@router.post("/circuit-types")
def create_circuit_type(body: CircuitTypeIn, actor=Depends(user)):
    return circuits.save_type(actor, body)


@router.put("/circuit-types/{type_id}")
def update_circuit_type(type_id: str, body: CircuitTypeIn, actor=Depends(user)):
    return circuits.save_type(actor, body, type_id)


@router.delete("/circuit-types/{type_id}")
def delete_circuit_type(type_id: str, actor=Depends(user)):
    return circuits.delete_type(actor, type_id)


@router.get("/risks")
def risks(actor=Depends(user)):
    return circuits.risks_page(actor)


@router.post("/risks")
def create_risk(body: RiskIn, actor=Depends(user)):
    return circuits.save_risk(actor, body)


@router.put("/risks/{risk_id}")
def update_risk(risk_id: str, body: RiskIn, actor=Depends(user)):
    return circuits.save_risk(actor, body, risk_id)


@router.delete("/risks/{risk_id}")
def delete_risk(risk_id: str, actor=Depends(user)):
    return circuits.delete_risk(actor, risk_id)


@router.get("/resilience")
def site_resilience(
    q: str | None = Query(None, max_length=64),
    kind: str | None = Query(None, max_length=20),
    state: str | None = Query(None, pattern="^(cut|single|clear|idle|none)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    actor=Depends(user),
):
    return resilience.resilience_page(actor, q, kind, state, offset, limit)


@router.get("/impact")
def failure_impact(
    kind: str | None = Query(None, pattern="^(circuit|provider|network|pop|device|risk)$"),
    id: str | None = Query(None, max_length=200),
    actor=Depends(user),
):
    return resilience.impact_page(actor, kind, id)


@router.get("/search")
def search(q: str = Query("", max_length=200), actor=Depends(user)):
    return service.search(actor, q)


@router.get("/changes")
def changes(
    entity: str | None = Query(None, max_length=20),
    q: str | None = Query(None, max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    actor=Depends(user),
):
    return service.changes(actor, entity, q, limit, offset)


@router.get("/import/template")
def import_template(actor=Depends(user)):
    return Response(
        importer.template(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%E8%B5%84%E6%BA%90%E5%8F%B0%E8%B4%A6%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.xlsx"},
    )


@router.get("/export")
def export(actor=Depends(user)):
    return Response(
        importer.export(actor),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%E8%B5%84%E6%BA%90%E5%8F%B0%E8%B4%A6.xlsx"},
    )


async def _workbook(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > IMPORT_LIMIT:
            raise HTTPException(413, "导入文件不能超过 10 MB")
    if not data:
        raise HTTPException(422, "请上传导入文件")
    return bytes(data)


@router.post("/import/preview")
async def import_preview(request: Request, actor=Depends(user)):
    data = await _workbook(request)
    return await run_in_threadpool(importer.run, actor, data, False)


@router.post("/import/commit")
async def import_commit(request: Request, actor=Depends(user)):
    data = await _workbook(request)
    return await run_in_threadpool(importer.run, actor, data, True)
