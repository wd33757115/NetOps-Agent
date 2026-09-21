"""Regression checks for the authoring/runtime package transition."""

import importlib
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "first",
    [
        "src.apps.rule_governance.backend.api",
        "src.gateway.parser_api",
        "src.apps.rule_governance.backend.check_items.catalog",
        "src.apps.rule_governance.backend.rule_control.command_asset_units",
        "src.apps.rule_governance.runtime",
        "src.platform.inventory.identity_discovery",
    ],
)
def test_fresh_import_order_preserves_business_types_and_tasks(first):
    # A warm pytest interpreter can conceal cycles or duplicate exception classes.
    code = """
import importlib, sys
importlib.import_module(sys.argv[1])
pairs = [
 ('src.gateway.parser_api', 'src.apps.rule_governance.backend.api'),
 ('src.core.parser.repository', 'src.apps.rule_governance.backend.parser.repository'),
 ('src.core.parser.tasks', 'src.apps.rule_governance.backend.parser.tasks'),
 ('src.core.parser_generation.compilers.table', 'src.apps.rule_governance.backend.parser_generation.compilers.table'),
 ('src.core.check_items.runtime_bundle', 'src.apps.rule_governance.backend.check_items.runtime_bundle'),
 ('src.core.rule_control.tasks', 'src.apps.rule_governance.backend.rule_control.tasks'),
 ('src.core.device_ops.repository', 'src.platform.inventory.repository'),
 ('src.core.credentials.service', 'src.platform.inventory.credentials.service'),
]
for old, new in pairs:
    assert importlib.import_module(old) is importlib.import_module(new), (old, new)
from src.core.parser.repository import ParserJobStateError
from src.apps.rule_governance.backend.api import ParserJobStateError as HandledError
assert ParserJobStateError is HandledError
from src.core.parser.tasks import dispatch_parser_jobs
assert dispatch_parser_jobs.name == 'dispatch_parser_jobs'
from src.core.rule_control.tasks import advance_command_autopilot
assert advance_command_autopilot.name == 'advance_command_autopilot'
"""
    result = subprocess.run(
        [sys.executable, "-c", code, first],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    assert result.returncode == 0, result.stderr


def test_resources_keep_historical_template_paths_outside_package():
    from src.apps.rule_governance.backend.parser.assets import TemplateAssetStore
    from src.apps.rule_governance.backend.parser.policy import SHARED_PARSER_CONFIG_ROOT
    from src.apps.rule_governance.backend.parser.textfsm_dialect import load_dialect_prompt
    from src.apps.rule_governance.backend.parser.value_mapping_config import (
        DEFAULT_VALUE_MAPPING_CONFIG_PATH,
    )
    from src.apps.rule_governance.paths import PACKAGE_ROOT

    assert TemplateAssetStore().root == ROOT / "src/skills/shared/textfsm-templates"
    assert not TemplateAssetStore().root.is_relative_to(PACKAGE_ROOT)
    assert DEFAULT_VALUE_MAPPING_CONFIG_PATH == ROOT / "config/parser_value_mappings.yaml"
    assert SHARED_PARSER_CONFIG_ROOT.is_relative_to(PACKAGE_ROOT)
    assert (SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml").is_file()
    assert "2.1.0" in load_dialect_prompt()


def test_runtime_contract_excludes_authoring_and_app_admission(monkeypatch):
    from src.apps.rule_governance import runtime
    from src.apps.rule_governance.backend.check_items import runtime_bundle

    calls = []

    class FrozenAssets:
        def get_version(self, version_id):
            calls.append(version_id)
            return {"version_id": version_id, "version_status": "deprecated"}

        def get_rule_assets(self, version_id):
            return {"bundle": self.get_version(version_id), "event_rules": []}

    monkeypatch.setattr(runtime_bundle, "CheckItemRuntimeBundleService", FrozenAssets)
    reader = runtime.CheckItemRuntimeReader()
    assert reader.get_version("historical-v1")["version_status"] == "deprecated"
    assert reader.get_rule_assets("historical-v1")["bundle"]["version_id"] == "historical-v1"
    assert calls == ["historical-v1", "historical-v1"]
    for operation in ("create_version", "activate", "rollback", "assemble", "approve"):
        assert not hasattr(reader, operation)
    assert "CheckItemRuntimeBundleService" not in runtime.__all__


def test_credential_metadata_does_not_return_values(monkeypatch):
    from src.platform.inventory.credentials import CredentialResolver

    secret = "p4-test-device-secret"
    monkeypatch.setenv("NETOPS_P4_TEST_PASSWORD", secret)
    resolver = CredentialResolver()
    reference = "env:NETOPS_P4_TEST_PASSWORD"
    metadata = next(item for item in resolver.list_references() if item["ref"] == reference)
    assert metadata["available"] is True
    assert secret not in repr(metadata)
    assert secret not in repr(resolver.inspect_reference(reference))
    assert resolver.resolve_password(reference) == secret
    legacy = importlib.import_module("src.core.credentials.service")
    assert legacy.CredentialResolver is CredentialResolver


def test_inventory_delete_preserves_audit_snapshot_in_same_transaction(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from src.infrastructure.db.models import (
        NetworkDevice,
        NetworkDeviceGroup,
        NetworkDeviceGroupMember,
        NetworkDeviceTombstone,
    )
    from src.platform.inventory import repository

    engine = create_engine("sqlite://")
    for model in (
        NetworkDevice,
        NetworkDeviceGroup,
        NetworkDeviceGroupMember,
        NetworkDeviceTombstone,
    ):
        model.__table__.create(engine)

    @contextmanager
    def scope():
        with Session(engine) as session, session.begin():
            yield session

    monkeypatch.setattr(repository, "get_db_session", scope)
    with scope() as session:
        session.add(
            NetworkDevice(
                id="device-p4",
                device_name="test-switch",
                management_ip="192.0.2.10",
                vendor="Cisco",
                credential_ref="env:NETOPS_TEST_PASSWORD",
                attributes_json={"source": "fixture"},
            )
        )
    try:
        removed = repository.DeviceRepository().delete_device("device-p4")
        assert removed["device_name"] == "test-switch"
        with scope() as session:
            assert session.get(NetworkDevice, "device-p4") is None
            snapshot = session.query(NetworkDeviceTombstone).one()
            assert snapshot.snapshot_json == removed
            assert snapshot.snapshot_json["password"] is None
            assert snapshot.snapshot_json["attributes"] == {"source": "fixture"}
        assert repository.DeviceRepository().delete_device("device-p4") is None
    finally:
        engine.dispose()
