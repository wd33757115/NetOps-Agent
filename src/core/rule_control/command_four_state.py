"""Per-command four-state readiness for device effective plans."""

from __future__ import annotations

from typing import Any

from src.core.rule_control.runtime_assets import RuntimeRuleAssetResolver


def command_four_states(
    *,
    coverage: list[dict[str, Any]],
    runtime_asset_health: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Combine parser coverage with runtime rule asset health per command."""
    health = dict(runtime_asset_health or {})
    missing_contracts = {
        str(item).strip() for item in (health.get("missing_observation_contracts") or []) if item
    }
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
        contract_status = (
            "ready"
            if template_id and f"cli:{template_id}" not in missing_contracts and parser_status == "ready"
            else "missing"
            if parser_status == "ready"
            else "blocked"
        )
        # Policies/rules are observation-scoped; surface aggregate readiness when parser ready.
        policy_status = (
            "ready"
            if health.get("analysis_ready") is True and contract_status == "ready"
            else "missing"
            if contract_status == "ready"
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
        elif contract_status != "ready":
            next_action = "生成 ObservationContract"
        elif policy_status not in {"ready", "partial"}:
            next_action = "生成 ChangePolicy"
        elif event_rule_status not in {"ready", "partial"}:
            next_action = "生成 EventRule"
        rows.append(
            {
                "command": command,
                "template_id": template_id or None,
                "parser_status": parser_status,
                "contract_status": contract_status,
                "policy_status": policy_status,
                "event_rule_status": event_rule_status,
                "next_action": next_action,
                "focus": {
                    "workspace": "inventory" if parser_status == "ready" else "inbox",
                    "subtab": "contracts" if parser_status == "ready" else "jobs",
                    "command": command,
                },
            }
        )
    return rows


def enrich_runtime_health_for_device(
    *,
    mode: str,
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve runtime assets and attach missing lists for four-state mapping."""
    schemas = []
    for item in coverage:
        template_id = str(item.get("template_id") or "").strip()
        if (
            item.get("policy_status") == "eligible"
            and item.get("coverage_status") == "ready"
            and template_id
        ):
            schemas.append(("cli", template_id))
    if not schemas:
        return {
            "status": "not_required" if not coverage else "degraded",
            "collection_ready": False,
            "analysis_ready": False,
            "event_evaluation_ready": False,
            "missing_observation_contracts": [],
            "missing_change_policies": [],
            "missing_event_rules": [],
        }
    try:
        resolved = RuntimeRuleAssetResolver().load_bundle(observation_schemas=schemas)
        payload = resolved.to_dict()
        payload["status"] = (
            "ready"
            if payload.get("event_evaluation_ready")
            else "degraded"
            if payload.get("collection_ready")
            else "blocked"
        )
        return payload
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "collection_ready": False,
            "analysis_ready": False,
            "event_evaluation_ready": False,
            "missing_observation_contracts": [],
            "missing_change_policies": [],
            "missing_event_rules": [],
        }
