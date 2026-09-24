"""Self-registered inspection API; legacy URLs mount these same handlers."""

from fastapi import APIRouter, Depends

from src.platform.inventory import api as inventory

from . import batch_api, patrol_api, patrol_offline_api, patrol_reports_api
from .access import app_user
from .diagnostics import router as diagnosis_router

router = APIRouter(dependencies=[Depends(app_user)])
router.include_router(diagnosis_router, prefix="/diagnosis")
router.include_router(patrol_offline_api.router, prefix="/patrol/offline-jobs")
for source, old, new in (
    (patrol_api.router, "/api/v1/patrol", "/patrol"),
    (patrol_reports_api.router, "/api/v1/patrol", "/patrol"),
    (batch_api.router, "/api/v1/workflows/runs/patrol-batches", "/batches"),
    (inventory.router, "/api/v1/devices", "/devices"),
):
    for route in source.routes:
        suffix = route.path.removeprefix(old)
        if source is inventory.router:
            # Inventory mutations belong to device-management. Inspection selects and reads devices.
            allowed = (
                route.methods == {"GET"}
                or suffix in {"/patrol/preview", "/patrol/launch"}
                or suffix.endswith("/effective-plan")
            )
            if suffix.startswith(("/credentials", "/import")):
                allowed = False
            if not allowed:
                continue
        router.add_api_route(
            new + suffix,
            route.endpoint,
            methods=list(route.methods),
            dependencies=[Depends(app_user)],
        )


@router.get("/legacy-runs")
def legacy_runs(patrol_batch_id: str, limit: int = 200):
    runs = batch_api._list_patrol_batch_runs(patrol_batch_id)
    return [batch_api._run_summary_dict(row) for row in runs[:max(1, min(limit, 500))]]
