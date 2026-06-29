from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.core.collection import (
    CollectionItem,
    CollectionPolicyResolver,
    ExecutionPlan,
    ExecutionPlanRepository,
    execution_plan_from_metadata,
)
from src.core.collection import policy as collection_policy
from src.core.collection.policy import CommandProfileRepository
from src.core.credentials import CredentialResolver
from src.core.device_ops.manager import DeviceInventoryManager
from src.core.device_ops.tools import ConfigBackupTool, PatrolTool
from src.infrastructure.db.models import (
    Base,
    CollectionExecutionPlanRecord,
    CommandProfileRecord,
    CommandProfileVersionRecord,
)


@contextmanager
def _session_scope(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _patch_collection_db(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'collection-policy.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            CollectionExecutionPlanRecord.__table__,
            CommandProfileRecord.__table__,
            CommandProfileVersionRecord.__table__,
        ],
    )
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS uq_command_profile_active_version"))
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(
        collection_policy,
        "get_db_session",
        lambda: _session_scope(session_factory),
    )
    return session_factory


def test_collection_policy_uses_configured_commands():
    plan = CollectionPolicyResolver().build_plan(
        device={
            "device_id": "device-1",
            "vendor": "Cisco",
            "command_file": "core-switch-patrol",
            "credential_ref": "env:NETOPS_PASSWORD",
            "attributes": {
                "patrol_commands": ["show version", "show inventory"],
                "patrol_profile_version_id": "profile-v2",
                "collector_options": {"read_timeout": 30, "netmiko_device_type": "generic"},
                "patrol_collector_options": {"read_timeout": 90, "netmiko_device_type": "cisco_ios"},
            },
        },
        action="patrol",
        mode="production",
    )

    assert plan.profile_name == "core-switch-patrol"
    assert plan.profile_version_id == "profile-v2"
    assert plan.policy_source == "device_attributes"
    assert plan.credential_ref == "env:NETOPS_PASSWORD"
    assert plan.collector_options == {"read_timeout": 90, "netmiko_device_type": "cisco_ios"}
    assert plan.command_texts() == ["show version", "show inventory"]
    assert plan.to_metadata()["collection_items"][0]["command"] == "show version"
    assert plan.to_metadata()["device_id"] == "device-1"
    assert plan.to_metadata()["action"] == "patrol"
    assert plan.to_metadata()["mode"] == "production"
    assert plan.to_metadata()["credential_ref"] == "env:NETOPS_PASSWORD"
    assert plan.to_metadata()["collector_options"]["read_timeout"] == 90


def test_collection_policy_defaults_by_vendor():
    plan = CollectionPolicyResolver().build_plan(
        device={"device_id": "device-1", "vendor": "Cisco", "attributes": {}},
        action="patrol",
        mode="production",
    )

    assert "show interface status" in plan.command_texts()
    assert plan.policy_source == "default"


def test_collection_policy_prefers_active_profile(monkeypatch):
    monkeypatch.setattr(
        CollectionPolicyResolver,
        "_load_active_profile",
        lambda self, profile_name, action: {
            "profile_version_id": "profile-version-1",
            "commands": ["show version", "show ip interface brief"],
        },
    )

    plan = CollectionPolicyResolver().build_plan(
        device={
            "device_id": "device-1",
            "vendor": "Cisco",
            "command_file": "active-core-profile",
            "attributes": {},
        },
        action="patrol",
        mode="production",
    )

    assert plan.profile_name == "active-core-profile"
    assert plan.profile_version_id == "profile-version-1"
    assert plan.policy_source == "device_profile"
    assert plan.command_texts() == ["show version", "show ip interface brief"]


def test_collection_policy_can_use_profile_binding(monkeypatch):
    monkeypatch.setattr(
        CollectionPolicyResolver,
        "_load_bound_profile",
        lambda self, device, action: {
            "binding_id": "binding-1",
            "selector": {"vendor": "Cisco", "role": "core"},
            "profile_name": "cisco-core",
            "profile_version_id": "profile-version-2",
            "commands": ["show version", "show inventory"],
        },
    )

    plan = CollectionPolicyResolver().build_plan(
        device={
            "device_id": "device-1",
            "vendor": "Cisco",
            "role": "core",
            "attributes": {},
        },
        action="patrol",
        mode="production",
    )

    assert plan.profile_name == "cisco-core"
    assert plan.profile_version_id == "profile-version-2"
    assert plan.policy_source == "profile_binding"
    assert plan.binding_id == "binding-1"
    assert plan.selector == {"vendor": "Cisco", "role": "core"}
    assert plan.command_texts() == ["show version", "show inventory"]


def test_collection_policy_selector_matches_device_context():
    resolver = CollectionPolicyResolver()

    assert resolver._selector_matches(
        {"vendor": "Cisco", "role": "core", "groups": ["dc-a", "core"]},
        {"vendor": "cisco", "role": "CORE", "group": "dc-a"},
    )
    assert not resolver._selector_matches(
        {"vendor": "Cisco", "role": "access", "groups": ["dc-a"]},
        {"vendor": "Cisco", "role": "core"},
    )


def test_command_profile_draft_versions_use_latest_version(monkeypatch, tmp_path):
    _patch_collection_db(monkeypatch, tmp_path)
    repository = CommandProfileRepository()

    first = repository.create_profile_version(
        name="core-patrol",
        action="patrol",
        commands=["show version"],
    )
    second = repository.create_profile_version(
        name="core-patrol",
        action="patrol",
        commands=["show inventory"],
    )

    versions = {
        item["profile_version_id"]: item
        for item in repository.list_profiles()
    }
    assert versions[first]["version"] == 1
    assert versions[second]["version"] == 2
    assert versions[first]["version_status"] == "draft"
    assert versions[second]["version_status"] == "draft"


def test_command_profile_rollback_reactivates_old_version(monkeypatch, tmp_path):
    _patch_collection_db(monkeypatch, tmp_path)
    repository = CommandProfileRepository()
    first = repository.upsert_active_profile(
        name="core-patrol",
        action="patrol",
        commands=["show version"],
    )
    second = repository.upsert_active_profile(
        name="core-patrol",
        action="patrol",
        commands=["show inventory"],
    )

    rolled_back = repository.rollback_profile_version(first)
    versions = {
        item["profile_version_id"]: item
        for item in repository.list_profiles()
    }

    assert rolled_back["profile_version_id"] == first
    assert rolled_back["version_status"] == "active"
    assert rolled_back["current_version"] == 1
    assert versions[first]["version_status"] == "active"
    assert versions[second]["version_status"] == "deprecated"


def test_command_profile_activation_requires_clean_coverage(monkeypatch, tmp_path):
    _patch_collection_db(monkeypatch, tmp_path)
    repository = CommandProfileRepository()
    version_id = repository.create_profile_version(
        name="core-patrol",
        action="patrol",
        commands=["show version"],
    )

    with pytest.raises(ValueError, match="impact evidence is required"):
        repository.validate_profile_activation(version_id)

    repository.record_profile_coverage(
        version_id,
        {
            "matched_device_count": 1,
            "blocked_device_count": 1,
            "blocked_command_count": 0,
        },
    )
    with pytest.raises(ValueError, match="blocked devices"):
        repository.validate_profile_activation(version_id)

    saved = repository.record_profile_coverage(
        version_id,
        {
            "matched_device_count": 1,
            "blocked_device_count": 0,
            "blocked_command_count": 0,
            "coverage_rate": 1.0,
        },
    )
    repository.validate_profile_activation(version_id)
    assert saved["coverage"]["coverage_rate"] == 1.0
    assert saved["coverage"]["recorded_at"]


def test_execution_plan_from_metadata_validates_and_round_trips():
    plan = execution_plan_from_metadata(
        {
            "device_id": "device-1",
            "action": "patrol",
            "mode": "production",
            "profile_name": "core",
            "profile_version_id": "profile-version-1",
            "credential_ref": "env:NETOPS_PASSWORD",
            "collector_options": {"read_timeout": 45, "netmiko_device_type": "hp_comware"},
            "policy_source": "profile_binding",
            "binding_id": "binding-1",
            "selector": {"vendor": "Cisco"},
            "collection_items": [
                {"command": "show version", "action": "patrol", "collection_item_id": "cmd-1"}
            ],
        }
    )

    assert plan.device_id == "device-1"
    assert plan.policy_source == "profile_binding"
    assert plan.binding_id == "binding-1"
    assert plan.credential_ref == "env:NETOPS_PASSWORD"
    assert plan.collector_options == {"read_timeout": 45, "netmiko_device_type": "hp_comware"}
    assert plan.commands[0].command == "show version"
    assert plan.commands[0].collection_item_id == "cmd-1"


def test_execution_plan_repository_persists_auditable_snapshot(monkeypatch, tmp_path):
    _patch_collection_db(monkeypatch, tmp_path)
    plan = ExecutionPlan(
        device_id="device-1",
        action="patrol",
        mode="production",
        profile_name="site-patrol",
        profile_version_id="profile-version-1",
        credential_ref="env:NETOPS_PASSWORD",
        collector_options={"read_timeout": 45},
        policy_source="profile_binding",
        binding_id="binding-1",
        selector={"site": "xian"},
        commands=(
            CollectionItem(command="show version", action="patrol", collection_item_id="cmd-1"),
        ),
    )

    repository = ExecutionPlanRepository()
    saved = repository.create_plan(
        plan,
        launch_source="workflow",
        patrol_batch_id="batch-1",
    )

    assert saved["execution_plan_id"]
    assert saved["device_id"] == "device-1"
    assert saved["credential_ref"] == "env:NETOPS_PASSWORD"
    assert saved["collector_options_json"] == {"read_timeout": 45}
    assert saved["selector_json"] == {"site": "xian"}
    assert saved["command_count"] == 1
    assert len(saved["plan_sha256"]) == 64
    assert saved["plan_json"]["collection_items"][0]["collection_item_id"] == "cmd-1"

    launched = repository.mark_launched(
        saved["execution_plan_id"],
        launch_source="workflow",
        patrol_batch_id="batch-1",
        workflow_run_id="workflow-1",
    )
    assert launched["status"] == "launched"
    assert launched["workflow_run_id"] == "workflow-1"
    assert repository.get_plan(saved["execution_plan_id"])["status"] == "launched"


def test_execution_plan_from_metadata_rejects_action_mismatch():
    with pytest.raises(ValueError, match="item action must match"):
        execution_plan_from_metadata(
            {
                "device_id": "device-1",
                "action": "patrol",
                "collection_items": [{"command": "show version", "action": "backup"}],
            }
        )


def test_credential_resolver_supports_only_explicit_refs(monkeypatch):
    monkeypatch.setenv("NETOPS_TEST_PASSWORD", "secret")

    assert CredentialResolver().resolve_password("env:NETOPS_TEST_PASSWORD") == "secret"
    assert CredentialResolver().resolve_password(None) is None
    with pytest.raises(ValueError, match="unsupported credential_ref"):
        CredentialResolver().resolve_password("plain:secret")


def test_credential_resolver_lists_and_inspects_env_refs(monkeypatch):
    monkeypatch.setenv("NETOPS_TEST_PASSWORD", "secret")
    monkeypatch.setenv("NETOPS_VISIBLE_TOKEN", "")
    monkeypatch.setenv("UNRELATED_PASSWORD", "hidden")

    resolver = CredentialResolver()
    refs = resolver.list_references()

    assert {"ref": "env:NETOPS_TEST_PASSWORD", "scheme": "env", "name": "NETOPS_TEST_PASSWORD", "available": True} in refs
    assert {"ref": "env:NETOPS_VISIBLE_TOKEN", "scheme": "env", "name": "NETOPS_VISIBLE_TOKEN", "available": False} in refs
    assert all("secret" not in str(item) for item in refs)
    assert all(item["ref"] != "env:UNRELATED_PASSWORD" for item in refs)
    assert resolver.inspect_reference("env:NETOPS_TEST_PASSWORD")["available"] is True
    assert resolver.inspect_reference("env:NETOPS_VISIBLE_TOKEN")["failure_code"] == "credential_unavailable"
    assert resolver.inspect_reference("plain:secret")["failure_code"] == "unsupported_scheme"


def test_device_inventory_manager_does_not_resolve_credentials(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "PE01",
                    "credential_ref": "env:NETOPS_TEST_PASSWORD",
                    "password": None,
                }
            ]

    manager = DeviceInventoryManager()
    manager.repository = FakeRepository()

    devices = manager.get_devices_by_filter(type("Filter", (), {})())

    assert devices[0]["credential_ref"] == "env:NETOPS_TEST_PASSWORD"
    assert devices[0]["password"] is None


def test_patrol_by_plan_executes_explicit_plan(tmp_path):
    captured: dict[str, object] = {}

    class FakeManager:
        def get_device_by_ip_or_name(self, identifier):
            return None

        def get_devices_by_filter(self, filter_params):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "model": "C2960",
                    "attributes": {},
                }
            ]

    class FakeCLICollector:
        def collect_command_outputs(self, *, device, execution_plan):
            captured["device"] = device
            captured["commands"] = execution_plan.command_texts()
            output = tmp_path / "patrol.txt"
            output.write_text("Command: show version\nOutput:\nVersion\n", encoding="utf-8")
            return output

    tool = PatrolTool(FakeManager(), cli_collector=FakeCLICollector())
    plan = ExecutionPlan(
        device_id="device-1",
        action="patrol",
        mode="parser_validation",
        profile_name="explicit",
        profile_version_id="profile-version-1",
        commands=(CollectionItem(command="show version", action="patrol"),),
        policy_source="explicit",
    )

    result = asyncio.run(tool.patrol_by_plan(plan))

    assert result.success is True
    assert result.total_devices == 1
    assert captured["commands"] == ["show version"]
    assert result.details[0]["execution_plan"]["policy_source"] == "explicit"


def test_backup_by_plan_executes_explicit_plan(tmp_path):
    captured: dict[str, object] = {}

    class FakeManager:
        def get_device_by_ip_or_name(self, identifier):
            return {
                "device_id": "device-1",
                "device_name": "SW1",
                "ip": "10.0.0.1",
                "vendor": "Cisco",
                "model": "C2960",
                "attributes": {},
            }

    class FakeCLICollector:
        def collect_command_outputs(self, *, device, execution_plan):
            captured["device"] = device
            captured["commands"] = execution_plan.command_texts()
            output = tmp_path / "backup.txt"
            output.write_text("Command: show running-config\nOutput:\nhostname SW1\n", encoding="utf-8")
            return output

    tool = ConfigBackupTool(FakeManager(), cli_collector=FakeCLICollector())
    plan = ExecutionPlan(
        device_id="device-1",
        action="backup",
        mode="production",
        profile_name="explicit-backup",
        profile_version_id="profile-version-2",
        commands=(CollectionItem(command="show running-config", action="backup"),),
        policy_source="explicit",
    )

    result = asyncio.run(tool.backup_by_plan(plan))

    assert result.success is True
    assert result.total_devices == 1
    assert captured["commands"] == ["show running-config"]
    assert result.details[0]["execution_plan"]["profile_name"] == "explicit-backup"
