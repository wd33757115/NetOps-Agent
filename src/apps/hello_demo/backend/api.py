"""The smallest application the platform runs: one route, answered for whoever may use it.

The kernel mounts this router at /api/apps/hello-demo and puts the App admission
check in front of it, so a request only arrives here from a signed-in user who
has been granted the application while it is enabled.
"""

from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends

from src.platform.authorization.client import require_app

router = APIRouter()
user = require_app("hello-demo")
VERSION = yaml.safe_load((Path(__file__).parents[1] / "app.yaml").read_text(encoding="utf-8"))["version"]


@router.get("/greeting")
def greeting(actor=Depends(user)):
    return {
        "message": f"你好，{actor.username}",
        "version": VERSION,
        "served_at": datetime.now(timezone.utc).isoformat(),
        # Added in 0.2.0, to show an upgrade reaching both ends.
        "role": actor.role,
    }
