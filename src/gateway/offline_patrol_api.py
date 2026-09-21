"""Legacy URL assembly; implementation belongs to the individual workspaces."""
from fastapi import APIRouter

from src.apps.rule_governance.backend import source_api
from src.gateway import offline_job_api, patrol_offline_api

router = APIRouter()
router.include_router(source_api.router, prefix="/api/v1/parser/asset-source-jobs", tags=["Rule Asset Sources"])
router.include_router(patrol_offline_api.router, prefix="/api/v1/patrol/offline-jobs", tags=["Offline Patrol"])


def __getattr__(name):
    for module in (source_api, patrol_offline_api, offline_job_api):
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
