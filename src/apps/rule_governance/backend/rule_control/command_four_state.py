"""Per-command four-state readiness for device effective plans."""

from __future__ import annotations

from typing import Any


def command_four_states(
    *,
    coverage: list[dict[str, Any]],
    runtime_asset_health: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Combine parser coverage with runtime rule asset health per command."""
    health = dict(runtime_asset_health or {})
    missing_policies = {
        str(item).strip() for item in (health.get("missing_change_policies") or []) if item
    }
    missing_rules = {
        str(item).strip() for item in (health.get("missing_event_rules") or []) if item
    }
    rows: list[dict[str, Any]] = []
    for item in coverage:
        command = str(item.get("command") or item.get("canonical_command") or "").strip()
        template_id = str(item.get("template_id") or "").strip()
        parser_status = (
            "ready"
            if item.get("policy_status") == "eligible" and item.get("coverage_status") == "ready"
            else "ignored"
            if item.get("policy_status") != "eligible"
            else str(item.get("coverage_status") or "missing")
        )
        projection_status = (
            "ready"
            if parser_status == "ready" and health.get("analysis_ready") is True
            else "missing"
            if parser_status == "ready"
            else "blocked"
        )
        # Policies/rules are observation-scoped; surface aggregate readiness when parser ready.
        policy_status = (
            "ready"
            if health.get("analysis_ready") is True and projection_status == "ready"
            else "missing"
            if projection_status == "ready"
            else "blocked"
        )
        event_rule_status = (
            "ready"
            if health.get("event_evaluation_ready") is True and policy_status == "ready"
            else "missing"
            if policy_status == "ready"
            else "blocked"
        )
        if missing_policies and policy_status == "ready":
            policy_status = "partial"
        if missing_rules and event_rule_status == "ready":
            event_rule_status = "partial"
        next_action = "查看详情"
        if parser_status not in {"ready", "ignored"}:
            next_action = "补齐 Parser 模板"
        elif projection_status != "ready":
            next_action = "核对检查项命令适配器"
        elif policy_status not in {"ready", "partial"}:
            next_action = "生成 ChangePolicy"
        elif event_rule_status not in {"ready", "partial"}:
            next_action = "生成 EventRule"
        rows.append(
            {
                "command": command,
                "template_id": template_id or None,
                "parser_status": parser_status,
                "projection_status": projection_status,
                "policy_status": policy_status,
                "event_rule_status": event_rule_status,
                "next_action": next_action,
                "focus": {
                    "workspace": "inventory" if parser_status == "ready" else "inbox",
                    "subtab": "check-items" if parser_status == "ready" else "jobs",
                    "command": command,
                },
            }
        )
    return rows
