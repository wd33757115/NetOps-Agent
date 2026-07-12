import ast
from pathlib import Path

from src.core.patrol.patrol_report_service import PatrolReportService


def _summary(**overrides):
    value = {
        "status_counts": {"completed": 1},
        "quality": {"status": "ready"},
        "no_event_summary": {"missing_rule_count": 0},
        "suspected_format_change_count": 0,
        "event_count": 0,
        "change_count": 0,
    }
    value.update(overrides)
    return value


def test_report_conclusion_prioritizes_failed_workflow_over_events():
    result = PatrolReportService.conclusion(
        _summary(status_counts={"completed": 1, "failed": 1}, event_count=2)
    )

    assert result == {
        "status": "执行失败",
        "trust": "低",
        "reason": "存在未完成的巡检工作流",
    }


def test_report_conclusion_does_not_treat_no_change_as_health():
    result = PatrolReportService.conclusion(_summary())

    assert result["status"] == "未发现变化（健康未判定）"
    assert "健康" in result["reason"]


def test_report_conclusion_degrades_when_event_rules_are_missing():
    result = PatrolReportService.conclusion(
        _summary(no_event_summary={"missing_rule_count": 3})
    )

    assert result["status"] == "部分可信，需复核"
    assert "3 条变化" in result["reason"]


def test_workflow_gateway_does_not_own_patrol_report_persistence_or_markdown():
    gateway = Path("src/gateway/workflow_api.py")
    source = gateway.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(gateway))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "src.infrastructure.db.models" not in imported_modules
    assert "src.infrastructure.db.postgres" not in imported_modules
    assert "# 网络设备巡检报告" not in source
    assert "## 一、巡检结论" not in source
