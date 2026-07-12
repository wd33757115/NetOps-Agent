"""Import the core API Gateway while optional AI/RAG/document packages are blocked."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.test.core_data_plane_smoke import OPTIONAL_PREFIXES, OptionalDependencyBlocker


def main() -> int:
    sys.meta_path.insert(0, OptionalDependencyBlocker())

    from src.gateway.main import app

    route_paths = {route.path for route in app.routes}
    required_routes = {
        "/api/v1/devices",
        "/api/v1/parser/templates",
        "/api/v1/workflows/runs",
    }
    missing = required_routes - route_paths
    if missing:
        raise RuntimeError(f"core Gateway routes are missing: {sorted(missing)}")
    print(
        f"core_gateway=ok routes={len(route_paths)} "
        f"blocked_optional_prefixes={len(OPTIONAL_PREFIXES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
