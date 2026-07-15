"""Verify the deterministic patrol data plane does not import optional product roles."""

from __future__ import annotations

import importlib
import importlib.abc
import json
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


OPTIONAL_PREFIXES = (
    "chromadb",
    "docx",
    "langchain",
    "langgraph",
    "langfuse",
    "llama_index",
    "openpyxl",
    "qdrant_client",
    "streamlit",
)


class OptionalDependencyBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path=None, target=None):  # noqa: ANN001
        if any(
            fullname == prefix or fullname.startswith(f"{prefix}.")
            for prefix in OPTIONAL_PREFIXES
        ):
            raise ModuleNotFoundError(
                f"optional dependency {fullname!r} was imported by the core data plane"
            )
        return None


def main() -> int:
    sys.meta_path.insert(0, OptionalDependencyBlocker())

    for module_name in (
        "src.core.celery_tasks.celery_app",
        "src.core.celery_tasks.tasks",
        "src.core.workflows.tasks",
        "src.core.parser.tasks",
        "src.core.patrol.tasks",
    ):
        importlib.import_module(module_name)

    from src.core.parser.runtime_bundle import ParserRuntimeBundle
    from src.core.patrol.handlers import (
        BuildEventsHandler,
        DetectChangesHandler,
        NormalizePatrolHandler,
    )
    from src.core.patrol.patrol_report_service import PatrolReportService
    from src.core.patrol.run_coordinator import PatrolRunCoordinator
    from src.core.patrol.run_status_projector import PatrolRunStatusProjector

    normalized = NormalizePatrolHandler(
        executor=lambda payload: {**payload, "normalized": True}
    ).handle({"snapshot_run_id": "smoke-run"})
    changes = DetectChangesHandler(executor=lambda payload: {**payload, "change_count": 1}).handle(
        normalized
    )
    events = BuildEventsHandler(executor=lambda payload: {**payload, "event_count": 1}).handle(
        changes
    )
    readiness = PatrolRunStatusProjector.project(
        [{"parse_status": "parsed", "fact_status": "ready"}]
    )

    assert events["event_count"] == 1
    assert readiness.parser_readiness_status == "ready"
    assert readiness.fact_readiness_status == "ready"
    conclusion = PatrolReportService.conclusion(
        {
            "status_counts": {"completed": 1},
            "quality": {"status": "ready"},
            "no_event_summary": {},
            "change_count": 0,
            "event_count": 0,
        }
    )
    assert conclusion["status"] == "未发现变化（健康未判定）"
    assert conclusion["trust"] == "中"
    assert ParserRuntimeBundle.__module__ == "src.core.parser.runtime_bundle"
    assert PatrolRunCoordinator.__module__ == "src.core.patrol.run_coordinator"

    print(
        json.dumps(
            {
                "status": "ok",
                "blocked_optional_prefixes": list(OPTIONAL_PREFIXES),
                "parser_readiness": readiness.parser_readiness_status,
                "fact_readiness": readiness.fact_readiness_status,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
