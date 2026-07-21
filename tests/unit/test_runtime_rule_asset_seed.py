from __future__ import annotations

from types import SimpleNamespace

from src.auth.models import CurrentUser
from src.core.rule_control.runtime_seed import RuntimeRuleAssetSeedService
from src.gateway import parser_api


def _admin_request(username: str = "operator"):
    return SimpleNamespace(
        state=SimpleNamespace(
            current_user=CurrentUser(
                user_id=f"user:{username}", username=username, role="admin"
            )
        )
    )


class FakeChangePolicyRepository:
    def __init__(self):
        self.active = {}
        self.created = []
        self.activated = []

    def get_active_policy(self, observation_type):
        return self.active.get(observation_type)

    def create_policy_version(self, **kwargs):
        version_id = f"version-{len(self.created) + 1}"
        payload = {
            "version_id": version_id,
            "observation_type": kwargs["policy"]["observation_type"],
            "version_status": kwargs["status"],
        }
        self.created.append(kwargs)
        return payload

    def activate_policy_version(self, version_id, *, actor=None, reason=None):
        assert actor == "pytest"
        assert reason == "restore runtime baseline"
        created = self.created[int(version_id.split("-")[-1]) - 1]
        observation_type = created["policy"]["observation_type"]
        active = {
            "version_id": version_id,
            "observation_type": observation_type,
            "version_status": "active",
        }
        self.active[observation_type] = active
        self.activated.append(version_id)
        return active


class FakeEventRuleRepository:
    def __init__(self):
        self.calls = 0

    def ensure_default_rules(self, *, actor=None, reason=None):
        assert actor == "pytest"
        assert reason == "restore runtime baseline"
        self.calls += 1
        return 11


def test_runtime_rule_asset_seed_creates_and_skips_builtin_assets():
    policies = FakeChangePolicyRepository()
    rules = FakeEventRuleRepository()
    service = RuntimeRuleAssetSeedService(
        change_policy_repository=policies,
        event_rule_repository=rules,
    )

    first = service.seed_builtin_assets(actor="pytest", reason="restore runtime baseline")
    second = service.seed_builtin_assets(actor="pytest", reason="restore runtime baseline")

    assert first["success"] is True
    assert first["event_rules_created"] == 11
    assert first["change_policies_created"] == first["change_policies_active"]
    assert "interface.oper_status" in first["observation_types"]
    assert all(item["created_by"] == "pytest" for item in policies.created)
    assert len(policies.activated) == first["change_policies_active"]
    assert second["change_policies_created"] == 0
    assert set(second["change_policies_skipped"]) == set(first["observation_types"])
    assert rules.calls == 2


def test_runtime_rule_asset_seed_endpoint_uses_service(monkeypatch):
    class FakeSeedService:
        def seed_builtin_assets(self, *, actor, reason):
            assert actor == "operator"
            assert reason == "restore runtime baseline"
            return {"success": True, "change_policies_active": 10}

    monkeypatch.setattr(parser_api, "RuntimeRuleAssetSeedService", FakeSeedService)

    result = parser_api.seed_builtin_runtime_rule_assets(
        _admin_request(),
        parser_api.RuntimeRuleAssetSeedRequest(reason="restore runtime baseline"),
    )

    assert result == {"success": True, "change_policies_active": 10}
