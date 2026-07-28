"""Runtime resolver for approved rule assets.

This module is intentionally read-only. It does not generate, validate, submit,
or approve candidates. Production code can use it to inspect which active rule
assets are available before falling back to degraded deterministic behavior.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from src.core.observation.repository import ObservationContractRepository
from src.core.patrol.change_policy_repository import ChangePolicyRepository
from src.core.patrol.event_rules import EventRuleRepository
from src.core.rule_control.planning import event_generation_decision


@dataclass(frozen=True)
class RuntimeRuleAssetBundle:
    """Snapshot of active assets needed by deterministic runtime paths."""

    observation_contracts: dict[str, dict[str, Any]] = field(default_factory=dict)
    change_policies: dict[str, dict[str, Any]] = field(default_factory=dict)
    event_rules: list[dict[str, Any]] = field(default_factory=list)
    missing_observation_contracts: list[str] = field(default_factory=list)
    missing_change_policies: list[str] = field(default_factory=list)
    missing_event_rules: list[str] = field(default_factory=list)
    observation_types: list[str] = field(default_factory=list)
    digest: str = ""

    @property
    def ready(self) -> bool:
        """Backward-compatible alias for analysis readiness."""
        return self.analysis_ready

    @property
    def collection_ready(self) -> bool:
        return bool(self.observation_contracts) and not self.missing_observation_contracts

    @property
    def analysis_ready(self) -> bool:
        return (
            self.collection_ready
            and bool(self.observation_types)
            and not self.missing_change_policies
        )

    @property
    def event_evaluation_ready(self) -> bool:
        return (
            self.analysis_ready
            and not self.missing_event_rules
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "collection_ready": self.collection_ready,
            "analysis_ready": self.analysis_ready,
            "event_evaluation_ready": self.event_evaluation_ready,
            "digest": self.digest,
            "observation_contracts": self.observation_contracts,
            "change_policies": self.change_policies,
            "event_rules": self.event_rules,
            "missing_observation_contracts": self.missing_observation_contracts,
            "missing_change_policies": self.missing_change_policies,
            "missing_event_rules": self.missing_event_rules,
            "observation_types": self.observation_types,
            "counts": {
                "observation_contracts": len(self.observation_contracts),
                "change_policies": len(self.change_policies),
                "event_rules": len(self.event_rules),
            },
        }


class RuntimeRuleAssetResolver:
    """Resolve active rule assets for deterministic production execution."""

    def __init__(
        self,
        *,
        contract_repository: ObservationContractRepository | None = None,
        change_policy_repository: ChangePolicyRepository | None = None,
        event_rule_repository: EventRuleRepository | None = None,
    ) -> None:
        self.contract_repository = contract_repository or ObservationContractRepository()
        self.change_policy_repository = change_policy_repository or ChangePolicyRepository()
        self.event_rule_repository = event_rule_repository or EventRuleRepository()

    def load_bundle(
        self,
        *,
        observation_schemas: Iterable[tuple[str, str]] = (),
        observation_types: Iterable[str] = (),
        include_event_rules: bool = True,
    ) -> RuntimeRuleAssetBundle:
        contracts: dict[str, dict[str, Any]] = {}
        missing_contracts: list[str] = []
        for source_type, schema_id in _unique_pairs(observation_schemas):
            key = f"{source_type}:{schema_id}"
            contract = self.contract_repository.get_active_validation_dict(
                source_type=source_type,
                schema_id=schema_id,
            )
            if contract is None:
                missing_contracts.append(key)
            else:
                contracts[key] = contract

        explicit_observation_types = _unique_strings(observation_types)
        inferred_observation_types = _observation_types_from_contracts(contracts.values())
        requested_observation_types = _unique_strings(
            [*explicit_observation_types, *inferred_observation_types]
        )
        policies: dict[str, dict[str, Any]] = {}
        missing_policies: list[str] = []
        for observation_type in requested_observation_types:
            active = self.change_policy_repository.get_active_policy(observation_type)
            if active is None:
                missing_policies.append(observation_type)
            else:
                policies[observation_type] = active

        event_rules = (
            self.event_rule_repository.snapshot_active_rules()
            if include_event_rules
            else []
        )
        declared_event_types = _event_rule_observation_types(event_rules)
        required_event_types = _unique_strings(
            [
                *explicit_observation_types,
                *_event_observation_types_from_contracts(contracts.values()),
            ]
        )
        missing_event_rules = (
            [
                observation_type
                for observation_type in required_event_types
                if observation_type not in declared_event_types
            ]
            if include_event_rules
            else list(required_event_types)
        )
        payload = {
            "observation_contracts": contracts,
            "change_policies": policies,
            "event_rules": event_rules,
            "missing_observation_contracts": missing_contracts,
            "missing_change_policies": missing_policies,
            "missing_event_rules": missing_event_rules,
            "observation_types": requested_observation_types,
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return RuntimeRuleAssetBundle(
            observation_contracts=contracts,
            change_policies=policies,
            event_rules=event_rules,
            missing_observation_contracts=missing_contracts,
            missing_change_policies=missing_policies,
            missing_event_rules=missing_event_rules,
            observation_types=requested_observation_types,
            digest=digest,
        )

    def load_inventory_bundle(self) -> RuntimeRuleAssetBundle:
        contracts = self.contract_repository.list_active_validation_dicts()
        schemas = [
            (str(item.get("source_type") or ""), str(item.get("schema_id") or ""))
            for item in contracts
        ]
        observation_types = _observation_types_from_contracts(contracts)
        return self.load_bundle(
            observation_schemas=schemas,
            observation_types=[],
            include_event_rules=True,
        )


def _unique_pairs(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for source_type, schema_id in items:
        item = (str(source_type or "").strip(), str(schema_id or "").strip())
        if not item[0] or not item[1] or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _unique_strings(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in items:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _observation_types_from_contracts(
    contracts: Iterable[dict[str, Any]],
) -> list[str]:
    values: list[str] = []
    for contract in contracts:
        semantics = contract.get("state_semantics")
        if not isinstance(semantics, dict):
            semantics = contract.get("state_semantics_json")
        if not isinstance(semantics, dict):
            continue
        for item in semantics.values():
            if not isinstance(item, dict):
                continue
            observation_type = str(item.get("observation_type") or "").strip()
            if observation_type:
                values.append(observation_type)
    return _unique_strings(values)


def _event_rule_observation_types(rules: Iterable[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for rule in rules:
        condition = rule.get("condition")
        if not isinstance(condition, dict):
            condition = rule.get("condition_json")
        if not isinstance(condition, dict):
            continue
        declared = condition.get("observation_types")
        if not isinstance(declared, list):
            continue
        values.update(str(item).strip() for item in declared if str(item).strip())
    return values


def _event_observation_types_from_contracts(
    contracts: Iterable[dict[str, Any]],
) -> list[str]:
    """Return only observation types whose Contract semantics require events."""

    values: list[str] = []
    for contract in contracts:
        semantics = contract.get("state_semantics")
        if not isinstance(semantics, dict):
            semantics = contract.get("state_semantics_json")
        if not isinstance(semantics, dict):
            continue
        for item in semantics.values():
            if not isinstance(item, dict):
                continue
            observation_type = str(item.get("observation_type") or "").strip()
            if not observation_type:
                continue
            # Contracts created before event intent governance did not carry this
            # flag. Preserve their historical "every observation needs a rule"
            # behavior while governing all newly generated explicit intent.
            if "event_generation" not in item:
                values.append(observation_type)
                continue
            enabled, _ = event_generation_decision(item)
            if enabled:
                values.append(observation_type)
    return _unique_strings(values)
