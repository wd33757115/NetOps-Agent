from __future__ import annotations

from src.core.rule_control.runtime_assets import RuntimeRuleAssetResolver


class FakeContractRepository:
    def __init__(self):
        self.calls = []

    def get_active_validation_dict(self, *, source_type, schema_id):
        self.calls.append((source_type, schema_id))
        if schema_id == "template-active":
            return {
                "source_type": source_type,
                "schema_id": schema_id,
                "entity_type": "interface",
                "primary_keys": ["interface"],
                "state_semantics": {
                    "protocol": {
                        "semantic_type": "state",
                        "observation_type": "interface.oper_status",
                    }
                },
            }
        return None

    def list_active_validation_dicts(self):
        return [self.get_active_validation_dict(source_type="cli", schema_id="template-active")]


class FakeChangePolicyRepository:
    def __init__(self):
        self.calls = []

    def get_active_policy(self, observation_type):
        self.calls.append(observation_type)
        if observation_type == "interface.oper_status":
            return {
                "policy_id": "policy-1",
                "version_id": "policy-version-1",
                "policy": {"compare_mode": "state"},
            }
        return None


class FakeEventRuleRepository:
    def __init__(self):
        self.calls = []

    def snapshot_active_rules(self):
        self.calls.append("snapshot_active_rules")
        return [
            {
                "rule_id": "InterfaceDown",
                "version_id": "rule-version-1",
                "event_type": "InterfaceDown",
                "version_status": "active",
                "condition": {"observation_types": ["interface.oper_status"]},
            }
        ]


class EmptyEventRuleRepository:
    def snapshot_active_rules(self):
        return []


class GovernedContractRepository:
    def get_active_validation_dict(self, *, source_type, schema_id):
        return {
            "source_type": source_type,
            "schema_id": schema_id,
            "entity_type": "device",
            "primary_keys": ["device_name"],
            "state_semantics": {
                "software_version": {
                    "semantic_type": "text",
                    "observation_type": "device.software_version",
                    "event_generation": True,
                    "event_role": "change_only",
                },
                "memory_size": {
                    "semantic_type": "gauge",
                    "observation_type": "device.memory_size",
                    "event_generation": True,
                    "event_role": "capacity",
                },
                "uptime": {
                    "semantic_type": "text",
                    "observation_type": "device.uptime",
                    "event_generation": True,
                    "event_role": "health",
                },
            },
        }


def test_runtime_rule_asset_resolver_reports_active_assets_and_missing_gaps():
    contracts = FakeContractRepository()
    policies = FakeChangePolicyRepository()
    rules = FakeEventRuleRepository()

    bundle = RuntimeRuleAssetResolver(
        contract_repository=contracts,
        change_policy_repository=policies,
        event_rule_repository=rules,
    ).load_bundle(
        observation_schemas=[
            ("cli", "template-active"),
            ("cli", "template-active"),
            ("cli", "template-missing"),
            ("", "ignored"),
        ],
        observation_types=[
            "interface.oper_status",
            "interface.oper_status",
            "device.cpu.utilization",
            "",
        ],
    )

    assert bundle.ready is False
    assert set(bundle.observation_contracts) == {"cli:template-active"}
    assert bundle.missing_observation_contracts == ["cli:template-missing"]
    assert set(bundle.change_policies) == {"interface.oper_status"}
    assert bundle.missing_change_policies == ["device.cpu.utilization"]
    assert bundle.event_rules[0]["version_status"] == "active"
    assert contracts.calls == [
        ("cli", "template-active"),
        ("cli", "template-missing"),
    ]
    assert policies.calls == ["interface.oper_status", "device.cpu.utilization"]
    assert rules.calls == ["snapshot_active_rules"]

    payload = bundle.to_dict()
    assert payload["ready"] is False
    assert payload["collection_ready"] is False
    assert payload["analysis_ready"] is False
    assert payload["event_evaluation_ready"] is False
    assert payload["missing_event_rules"] == ["device.cpu.utilization"]
    assert payload["digest"]
    assert payload["counts"] == {
        "observation_contracts": 1,
        "change_policies": 1,
        "event_rules": 1,
    }


def test_runtime_rule_asset_resolver_can_skip_event_rule_loading():
    rules = FakeEventRuleRepository()

    bundle = RuntimeRuleAssetResolver(
        contract_repository=FakeContractRepository(),
        change_policy_repository=FakeChangePolicyRepository(),
        event_rule_repository=rules,
    ).load_bundle(
        observation_schemas=[("cli", "template-active")],
        observation_types=["interface.oper_status"],
        include_event_rules=False,
    )

    assert bundle.ready is True
    assert bundle.collection_ready is True
    assert bundle.analysis_ready is True
    assert bundle.event_evaluation_ready is False
    assert bundle.event_rules == []
    assert rules.calls == []


def test_runtime_rule_asset_resolver_derives_policies_from_contract_semantics():
    policies = FakeChangePolicyRepository()

    bundle = RuntimeRuleAssetResolver(
        contract_repository=FakeContractRepository(),
        change_policy_repository=policies,
        event_rule_repository=FakeEventRuleRepository(),
    ).load_bundle(
        observation_schemas=[("cli", "template-active")],
        observation_types=[],
        include_event_rules=False,
    )

    assert bundle.ready is True
    assert bundle.event_evaluation_ready is False
    assert set(bundle.change_policies) == {"interface.oper_status"}
    assert policies.calls == ["interface.oper_status"]


def test_runtime_inventory_health_uses_all_active_assets_and_exact_rule_scope():
    bundle = RuntimeRuleAssetResolver(
        contract_repository=FakeContractRepository(),
        change_policy_repository=FakeChangePolicyRepository(),
        event_rule_repository=FakeEventRuleRepository(),
    ).load_inventory_bundle()

    assert bundle.collection_ready is True
    assert bundle.analysis_ready is True
    assert bundle.event_evaluation_ready is True
    assert bundle.observation_types == ["interface.oper_status"]
    assert bundle.missing_event_rules == []


def test_runtime_requires_only_executable_explicit_event_semantics():
    bundle = RuntimeRuleAssetResolver(
        contract_repository=GovernedContractRepository(),
        change_policy_repository=FakeChangePolicyRepository(),
        event_rule_repository=EmptyEventRuleRepository(),
    ).load_bundle(
        observation_schemas=[("cli", "template-version")],
        observation_types=[],
    )

    assert bundle.missing_event_rules == ["device.software_version"]
    assert "device.memory_size" not in bundle.missing_event_rules
    assert "device.uptime" not in bundle.missing_event_rules
