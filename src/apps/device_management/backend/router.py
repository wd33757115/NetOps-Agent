"""Device operations with one App admission. No rule-governance grant required."""
from fastapi import APIRouter, Depends
from .access import app_user
from .api import router as inventory

router = APIRouter(dependencies=[Depends(app_user)])
for route in inventory.routes:
    suffix = route.path.removeprefix("/api/v1/devices")
    if suffix.startswith("/patrol/"):
        continue
    router.add_api_route("/devices" + suffix, route.endpoint, methods=list(route.methods))

# Reuse audited collection-profile operations. These endpoints have no rule App
# dependency; registration here explicitly admits the device App, never the full
# rule router or rule-generation/publishing operations.
from src.apps.rule_governance.backend import api as collection
for path, methods, endpoint in (
    ("/command-profiles", ["GET"], collection.list_command_profiles),
    ("/command-profiles", ["POST"], collection.upsert_command_profile),
    ("/command-profiles/impact-preview", ["POST"], collection.preview_command_profile_impact),
    ("/command-profiles/{profile_version_id}/activate", ["POST"], collection.activate_command_profile),
    ("/command-profile-bindings", ["POST"], collection.upsert_command_profile_binding),
    ("/command-profile-bindings/{binding_id}/enable", ["POST"], collection.enable_command_profile_binding),
):
    router.add_api_route(path, endpoint, methods=methods)
