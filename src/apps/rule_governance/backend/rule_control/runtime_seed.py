"""Seed deterministic runtime rule assets through the rule control plane."""

from __future__ import annotations

from typing import Any

from src.core.patrol.change_policy_repository import ChangePolicyRepository
from src.core.patrol.event_rules import EventRuleRepository

BUILTIN_CHANGE_POLICIES: tuple[dict[str, Any], ...] = (
    {
        "name": "policy:device.cpu.utilization",
        "observation_type": "device.cpu.utilization",
        "semantic_type": "gauge",
        "compare_mode": "threshold_and_deadband",
        "thresholds": {"high": 90},
        "recovery_threshold": 85,
        "deadband": 5,
        "minimum_duration": "0s",
        "reason": "builtin runtime seed for CPU high/recovered events",
    },
    {
        "name": "policy:device.cpu.idle",
        "observation_type": "device.cpu.idle",
        "semantic_type": "gauge",
        "compare_mode": "numeric_delta",
        "deadband": 5,
        "minimum_duration": "0s",
        "reason": "builtin runtime seed for system resource drift",
    },
    {
        "name": "policy:device.memory.used",
        "observation_type": "device.memory.used",
        "semantic_type": "gauge",
        "compare_mode": "numeric_delta",
        "deadband": 524288,
        "minimum_duration": "0s",
        "reason": "builtin runtime seed for memory usage drift",
    },
    {
        "name": "policy:device.memory.free_ratio",
        "observation_type": "device.memory.free_ratio",
        "semantic_type": "gauge",
        "compare_mode": "threshold_and_deadband",
        "thresholds": {"high": 90},
        "recovery_threshold": 85,
        "deadband": 5,
        "minimum_duration": "0s",
        "reason": "builtin runtime seed for memory percentage events",
    },
    {
        "name": "policy:environment.fan.state",
        "observation_type": "environment.fan.state",
        "semantic_type": "state",
        "compare_mode": "state_transition",
        "reason": "builtin runtime seed for fan failed/recovered events",
    },
    {
        "name": "policy:environment.power.state",
        "observation_type": "environment.power.state",
        "semantic_type": "state",
        "compare_mode": "state_transition",
        "reason": "builtin runtime seed for power supply failed/recovered events",
    },
    {
        "name": "policy:environment.sensor.state",
        "observation_type": "environment.sensor.state",
        "semantic_type": "state",
        "compare_mode": "state_transition",
        "reason": "builtin runtime seed for environment sensor state events",
    },
    {
        "name": "policy:redundancy.vrrp.state",
        "observation_type": "redundancy.vrrp.state",
        "semantic_type": "state",
        "compare_mode": "state_transition",
        "reason": "builtin runtime seed for VRRP state events",
    },
    {
        "name": "policy:redundancy.state",
        "observation_type": "redundancy.state",
        "semantic_type": "state",
        "compare_mode": "state_transition",
        "reason": "builtin runtime seed for stack/vPC/MLAG state events",
    },
)


class RuntimeRuleAssetSeedService:
    """Create and activate baseline ChangePolicy/EventRule assets idempotently."""

    def __init__(
        self,
        *,
        change_policy_repository: ChangePolicyRepository | None = None,
        event_rule_repository: EventRuleRepository | None = None,
    ) -> None:
        self.change_policy_repository = change_policy_repository or ChangePolicyRepository()
        self.event_rule_repository = event_rule_repository or EventRuleRepository()

    def seed_builtin_assets(
        self,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        event_rules_created = self.event_rule_repository.ensure_default_rules(
            actor=actor,
            reason=reason,
        )
        policies: list[dict[str, Any]] = []
        skipped: list[str] = []
        for policy in BUILTIN_CHANGE_POLICIES:
            observation_type = str(policy["observation_type"])
            active = self.change_policy_repository.get_active_policy(observation_type)
            if active is not None:
                skipped.append(observation_type)
                policies.append(active)
                continue
            created = self.change_policy_repository.create_policy_version(
                name=str(policy["name"]),
                policy={key: value for key, value in policy.items() if key != "name"},
                description=str(policy.get("reason") or ""),
                status="pending_approval",
                validation_report={
                    "success": True,
                    "source": "builtin-runtime-rule-asset-seed",
                },
                created_by=actor or "runtime-rule-asset-seed",
                version=1,
            )
            activated = self.change_policy_repository.activate_policy_version(
                str(created["version_id"]),
                actor=actor,
                reason=reason,
            )
            policies.append(activated)

        return {
            "success": True,
            "event_rules_created": event_rules_created,
            "change_policies_active": len(policies),
            "change_policies_created": len(policies) - len(skipped),
            "change_policies_skipped": skipped,
            "observation_types": [
                str(item["observation_type"]) for item in policies if item.get("observation_type")
            ],
        }
