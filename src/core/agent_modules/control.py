# SPDX-License-Identifier: Apache-2.0

"""Runtime governance for pluggable Agent modules."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.infrastructure.db.models import AgentModuleControlRecord
from src.infrastructure.db.postgres import get_db_session

AGENT_MODULE_IDS = {
    "parser-control",
    "state-normalizer",
    "change-detector",
    "event-builder",
    "device-management",
    "collection-profile",
    "report-diff",
}

AGENT_MODULE_CONTROL_STATUSES = {"enabled", "disabled", "maintenance"}


class AgentModuleGovernanceError(ValueError):
    """Raised when a workflow is blocked by Agent module governance."""

    def __init__(self, message: str, violations: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.violations = violations


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value.isoformat() if hasattr(value, "isoformat") else None


def module_control_dict(row: AgentModuleControlRecord | None, module_id: str) -> dict[str, Any]:
    if row is None:
        return {
            "module_id": module_id,
            "status": "enabled",
            "maintenance_reason": None,
            "maintenance_starts_at": None,
            "maintenance_ends_at": None,
            "updated_by": None,
            "updated_at": None,
        }
    return {
        "module_id": row.module_id,
        "status": row.status,
        "maintenance_reason": row.maintenance_reason,
        "maintenance_starts_at": _iso(row.maintenance_starts_at),
        "maintenance_ends_at": _iso(row.maintenance_ends_at),
        "updated_by": row.updated_by,
        "updated_at": _iso(row.updated_at),
    }


def required_modules_for_workflow(template_name: str, context: dict[str, Any] | None = None) -> list[str]:
    context = context or {}
    if template_name != "patrol-change-event":
        return []
    modules = ["device-management", "collection-profile", "parser-control"]
    mode = str(context.get("launch_mode") or context.get("workflow_launch_mode") or "workflow")
    if mode == "workflow":
        modules.extend(["state-normalizer", "change-detector", "event-builder"])
    if "report" in template_name.lower():
        modules.append("report-diff")
    return list(dict.fromkeys(modules))


def agent_module_control_violations(module_ids: list[str]) -> list[dict[str, Any]]:
    if not module_ids:
        return []
    with get_db_session() as db:
        rows = (
            db.query(AgentModuleControlRecord)
            .filter(AgentModuleControlRecord.module_id.in_(module_ids))
            .all()
        )
    controls = {row.module_id: module_control_dict(row, row.module_id) for row in rows}
    violations: list[dict[str, Any]] = []
    for module_id in module_ids:
        control = controls.get(module_id, module_control_dict(None, module_id))
        status = str(control.get("status") or "enabled")
        if status == "enabled":
            continue
        reason = control.get("maintenance_reason")
        starts_at = control.get("maintenance_starts_at")
        ends_at = control.get("maintenance_ends_at")
        window = ""
        if starts_at or ends_at:
            window = f"；窗口 {starts_at or '-'} 至 {ends_at or '-'}"
        violations.append(
            {
                "failure_code": "agent_module_not_enabled",
                "module_id": module_id,
                "status": status,
                "maintenance_reason": reason,
                "maintenance_starts_at": starts_at,
                "maintenance_ends_at": ends_at,
                "message": (
                    f"Agent 模块 {module_id} 当前为 {status}"
                    + (f"：{reason}" if reason else "")
                    + window
                ),
            }
        )
    return violations


def enforce_workflow_modules_enabled(template_name: str, context: dict[str, Any] | None = None) -> None:
    violations = agent_module_control_violations(required_modules_for_workflow(template_name, context))
    if violations:
        modules = ", ".join(
            f"{item['module_id']}={item['status']}" for item in violations
        )
        raise AgentModuleGovernanceError(
            f"Workflow {template_name} 被 Agent 模块治理阻断: {modules}",
            violations,
        )
