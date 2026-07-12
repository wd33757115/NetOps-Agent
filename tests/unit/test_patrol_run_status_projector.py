from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from src.core.patrol.run_status_projector import PatrolRunStatusProjector


def test_projector_treats_not_applicable_consistently_as_ready():
    result = PatrolRunStatusProjector.project(
        [
            SimpleNamespace(parse_status="not_applicable", fact_status="not_applicable"),
            SimpleNamespace(parse_status="ignored", fact_status="ignored"),
        ]
    )

    assert result.parser_readiness_status == "ready"
    assert result.fact_readiness_status == "ready"


def test_projector_reports_partial_and_blocked_readiness():
    partial = PatrolRunStatusProjector.project(
        [
            {"parse_status": "parsed", "fact_status": "ready"},
            {"parse_status": "template_missing", "fact_status": "blocked_by_parser"},
        ]
    )
    blocked = PatrolRunStatusProjector.project(
        [{"parse_status": "parse_failed", "fact_status": "blocked_by_parser"}]
    )

    assert partial.parser_readiness_status == "partial"
    assert partial.fact_readiness_status == "partial"
    assert blocked.parser_readiness_status == "blocked"
    assert blocked.fact_readiness_status == "blocked"


def test_patrol_data_plane_does_not_depend_on_gateway_or_agents():
    root = Path(__file__).resolve().parents[2]
    forbidden = ("src.gateway", "src.agents")
    violations: list[str] = []
    for package in (root / "src/core/patrol", root / "src/core/state"):
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.startswith(forbidden):
                        violations.append(f"{path.relative_to(root)} imports {name}")

    assert violations == []
