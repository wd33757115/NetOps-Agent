"""CLI exit gate for formal patrol production readiness."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.production_readiness import (  # noqa: E402
    PatrolProductionReadinessService,
)


def main() -> int:
    result = PatrolProductionReadinessService().evaluate()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["can_launch"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
