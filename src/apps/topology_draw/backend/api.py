"""Display-only health stub.

The drawing app's data comes from the topology app's public HTTP API, read by the
frontend through the gateway with the user's session; this backend exists because a
package manifest requires a router, and stays out of the twin's data path.
"""

from fastapi import APIRouter, Depends

from src.auth.models import CurrentUser
from src.platform.authorization.client import require_app

router = APIRouter(tags=["拓扑绘图"])
actor = require_app("topology-draw")


@router.get("/health")
def health(user: CurrentUser = Depends(actor)):
    return {"app": "topology-draw", "status": "ok"}
