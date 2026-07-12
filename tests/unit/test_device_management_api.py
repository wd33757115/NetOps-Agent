from __future__ import annotations

import base64
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest

from src.gateway import devices_api
from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkChangeRecord,
    NetworkEventRecord,
    PatrolRun,
)
from src.infrastructure.db.postgres import get_db_session


@pytest.fixture(autouse=True)
def _disable_agent_module_guard(monkeypatch):
    monkeypatch.setattr(devices_api, "_agent_module_control_violations", lambda module_ids: [])


def test_list_devices_redacts_password(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["vendor"] == "Cisco"
            assert kwargs["platform"] is None
            assert kwargs["device_type"] is None
            assert kwargs["status"] is None
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "password": "secret",
                    "credential_ref": "env:NETOPS_PASSWORD",
                    "vendor": "Cisco",
                    "groups": ["core"],
                }
            ]

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.list_devices(vendor="Cisco", limit=100, offset=0)

    assert result["total"] == 1
    assert result["items"][0]["device_id"] == "device-1"
    assert "password" not in result["items"][0]
    assert result["items"][0]["credential_ref"] == "env:NETOPS_PASSWORD"


def test_device_import_template_exposes_columns_and_csv():
    result = devices_api.get_device_import_template()

    assert result["filename"] == "device-import-template.csv"
    assert result["required_columns"] == ["device_name", "management_ip"]
    assert "device_name,management_ip" in result["csv"]
    assert any(column["name"] == "credential_ref" for column in result["columns"])
    assert any(column["name"] == "output_path" for column in result["columns"])


def test_agent_module_health_endpoint_uses_runtime_builder(monkeypatch):
    @contextmanager
    def fake_session():
        yield object()

    monkeypatch.setattr(devices_api, "get_db_session", fake_session)
    monkeypatch.setattr(
        devices_api,
        "_build_agent_module_health",
        lambda db: [
            {
                "module_id": "parser-control",
                "design_status": "active",
                "runtime_status": "healthy",
                "metrics": [{"label": "已激活模板", "value": 2, "status": "ok"}],
                "checks": [{"name": "模板资产", "status": "ok", "message": "存在已激活模板"}],
            }
        ],
    )

    result = devices_api.get_agent_module_health()

    assert result["items"][0]["module_id"] == "parser-control"
    assert result["items"][0]["runtime_status"] == "healthy"
    assert result["items"][0]["metrics"][0]["value"] == 2
    assert "generated_at" in result


def test_agent_module_includes_runtime_evidence():
    result = devices_api._agent_module(
        module_id="change-detector",
        design_status="active",
        checks=[devices_api._module_check(name="变化生成", status="ok", message="已有变化")],
        metrics=[devices_api._metric("变化数", 1)],
        evidence=[
            devices_api._evidence(
                kind="network_change",
                status="ok",
                title="最近网络变化",
                object_id="change-1",
                summary="PE01 bgp_state",
                target_url="/devices?tab=overview",
            )
        ],
    )

    assert result["runtime_status"] == "healthy"
    assert result["evidence"][0]["kind"] == "network_change"
    assert result["evidence"][0]["object_id"] == "change-1"
    assert result["evidence"][0]["target_url"] == "/devices?tab=overview"


def test_agent_module_control_requires_reason_when_disabled():
    try:
        devices_api._set_agent_module_control(
            None,
            "parser-control",
            devices_api.AgentModuleControlRequest(status="disabled"),
            updated_by="admin",
        )
    except devices_api.HTTPException as exc:
        assert exc.status_code == 400
        assert "maintenance_reason" in exc.detail
    else:
        raise AssertionError("expected HTTPException")


def test_agent_module_control_creates_record():
    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDb:
        def __init__(self):
            self.added = None
            self.committed = False

        def query(self, _model):
            return FakeQuery()

        def add(self, row):
            self.added = row

        def commit(self):
            self.committed = True

        def refresh(self, _row):
            return None

    db = FakeDb()

    result = devices_api._set_agent_module_control(
        db,
        "parser-control",
        devices_api.AgentModuleControlRequest(
            status="maintenance",
            maintenance_reason="版本升级窗口",
            maintenance_starts_at=datetime(2026, 6, 23, 22, 0, tzinfo=timezone.utc),
            maintenance_ends_at=datetime(2026, 6, 23, 23, 0, tzinfo=timezone.utc),
        ),
        updated_by="admin",
    )

    assert result["module_id"] == "parser-control"
    assert result["status"] == "maintenance"
    assert result["maintenance_reason"] == "版本升级窗口"
    assert result["maintenance_starts_at"].startswith("2026-06-23T22:00:00")
    assert result["maintenance_ends_at"].startswith("2026-06-23T23:00:00")
    assert result["updated_by"] == "admin"
    assert db.added is not None
    assert db.committed is True


def test_agent_module_control_rejects_invalid_maintenance_window():
    try:
        devices_api._set_agent_module_control(
            None,
            "parser-control",
            devices_api.AgentModuleControlRequest(
                status="maintenance",
                maintenance_reason="版本升级窗口",
                maintenance_starts_at=datetime(2026, 6, 23, 23, 0, tzinfo=timezone.utc),
                maintenance_ends_at=datetime(2026, 6, 23, 22, 0, tzinfo=timezone.utc),
            ),
            updated_by="admin",
        )
    except devices_api.HTTPException as exc:
        assert exc.status_code == 400
        assert "maintenance_ends_at" in exc.detail
    else:
        raise AssertionError("expected HTTPException")


def test_preview_device_import_reports_header_errors():
    try:
        devices_api.preview_device_import(
            devices_api.DeviceImportRequest(
                content="hostname,unknown_column\nSW1,value\n",
            )
        )
    except devices_api.HTTPException as exc:
        detail = exc.detail
    else:
        raise AssertionError("expected HTTPException")

    assert detail["message"] == "设备导入表头校验失败"
    assert "management_ip" in detail["errors"][0]
    assert any("unknown_column" in error for error in detail["errors"])
    assert "supported_columns" in detail


def test_list_device_groups_returns_static_asset_groups(monkeypatch):
    class FakeRepository:
        def list_groups(self):
            return ["H3C", "gns3", "lab"]

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    assert devices_api.list_device_groups() == {"items": ["H3C", "gns3", "lab"]}


def test_list_devices_can_include_patrol_readiness(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "password": "secret",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                    "command_file": "cisco-access",
                    "attributes": {},
                }
            ]

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(
        devices_api.CollectionPolicyResolver,
        "_load_active_profile",
        lambda self, profile_name, action: {
            "profile_version_id": "profile-version-1",
            "commands": ["show version", "show inventory"],
        },
    )
    monkeypatch.setattr(
        devices_api,
        "_production_parser_coverage",
        lambda device, commands: [
            {
                "canonical_command": "show version",
                "policy_status": "eligible",
                "coverage_status": "ready",
            },
            {
                "canonical_command": "show inventory",
                "policy_status": "eligible",
                "coverage_status": "template_missing",
            },
        ],
    )

    result = devices_api.list_devices(
        include_patrol_readiness=True,
        limit=100,
        offset=0,
    )

    device = result["items"][0]
    readiness = device["patrol_readiness"]
    assert "password" not in device
    assert readiness["status"] == "degraded"
    assert readiness["command_count"] == 2
    assert readiness["eligible_count"] == 2
    assert readiness["ready_count"] == 1
    assert readiness["blocked_count"] == 1
    assert readiness["policy_source"] == "device_profile"
    assert readiness["violations"][0]["canonical_command"] == "show inventory"


def test_list_devices_reuses_one_policy_resolver_for_500_readiness_rows(monkeypatch):
    devices = [
        {
            "device_id": f"device-{index}",
            "device_name": f"SW{index}",
            "ip": f"192.0.2.{(index % 250) + 1}",
            "password": "secret",
            "attributes": {},
        }
        for index in range(500)
    ]

    class FakeRepository:
        def get_devices_by_filter(self, **_kwargs):
            return devices

    resolver_instances = []

    class FakeResolver:
        def __init__(self):
            resolver_instances.append(self)

    seen_resolvers = []

    def readiness(_device, _mode, *, policy_resolver):
        seen_resolvers.append(policy_resolver)
        return {"status": "ready"}

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)
    monkeypatch.setattr(devices_api, "_patrol_readiness", readiness)

    result = devices_api.list_devices(
        include_patrol_readiness=True,
        limit=500,
        offset=0,
    )

    assert len(result["items"]) == 500
    assert len(resolver_instances) == 1
    assert set(map(id, seen_resolvers)) == {id(resolver_instances[0])}


def test_list_devices_uses_one_projection_batch_for_500_warm_rows(monkeypatch):
    devices = [
        {
            "device_id": f"device-{index}",
            "device_name": f"SW{index}",
            "ip": f"192.0.2.{(index % 250) + 1}",
            "vendor": "Cisco",
            "model": "C2960",
            "attributes": {},
        }
        for index in range(500)
    ]

    class FakeDeviceRepository:
        def get_devices_by_filter(self, **_kwargs):
            return devices

    projection_reads = []

    class FakeProjectionRepository:
        def asset_revision(self):
            return "assets-v1"

        def list_for_devices(self, device_ids, *, mode):
            projection_reads.append((device_ids, mode))
            return {
                device["device_id"]: {
                    "status": "ready",
                    "source_digest": devices_api.readiness_source_digest(
                        device,
                        mode=mode,
                        asset_revision="assets-v1",
                    ),
                }
                for device in devices
            }

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeDeviceRepository)
    monkeypatch.setattr(
        devices_api,
        "DeviceReadinessProjectionRepository",
        FakeProjectionRepository,
    )
    monkeypatch.setattr(
        devices_api,
        "_patrol_readiness",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("warm projection must avoid live readiness resolution")
        ),
    )

    result = devices_api.list_devices(
        include_patrol_readiness=True,
        limit=500,
        offset=0,
    )

    assert len(result["items"]) == 500
    assert len(projection_reads) == 1
    assert len(projection_reads[0][0]) == 500


def test_list_devices_patrol_readiness_blocks_missing_runtime_rule_assets(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "password": "secret",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                    "command_file": "cisco-access",
                    "attributes": {},
                }
            ]

    class FakeBundle:
        def to_dict(self):
            return {
                "ready": False,
                "observation_contracts": {"cli:template-1": {"entity_type": "interface"}},
                "change_policies": {},
                "event_rules": [{"rule_id": "InterfaceDown"}],
                "missing_observation_contracts": [],
                "missing_change_policies": ["interface.oper_status"],
                "counts": {
                    "observation_contracts": 1,
                    "change_policies": 0,
                    "event_rules": 1,
                },
            }

    class FakeRuntimeResolver:
        def load_bundle(self, *, observation_schemas, include_event_rules):
            assert observation_schemas == [("cli", "template-1")]
            assert include_event_rules is True
            return FakeBundle()

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "RuntimeRuleAssetResolver", FakeRuntimeResolver)
    monkeypatch.setattr(
        devices_api.CollectionPolicyResolver,
        "_load_active_profile",
        lambda self, profile_name, action: {
            "profile_version_id": "profile-version-1",
            "commands": ["show version"],
        },
    )
    monkeypatch.setattr(
        devices_api,
        "_production_parser_coverage",
        lambda device, commands: [
            {
                "canonical_command": "show version",
                "policy_status": "eligible",
                "coverage_status": "ready",
                "template_id": "template-1",
            },
        ],
    )

    result = devices_api.list_devices(
        include_patrol_readiness=True,
        limit=100,
        offset=0,
    )

    readiness = result["items"][0]["patrol_readiness"]
    assert readiness["status"] == "blocked"
    assert readiness["ready_count"] == 1
    assert readiness["blocked_count"] == 0
    assert readiness["runtime_asset_health"]["status"] == "blocked"
    assert readiness["runtime_asset_health"]["missing_change_policies"] == ["interface.oper_status"]


def test_preview_device_plan_returns_execution_plan(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["device_id"] == "device-1"
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "attributes": {"collector_options": {"read_timeout": 60}},
                }
            ]

    class FakePlan:
        def to_metadata(self):
            return {
                "device_id": "device-1",
                "action": "patrol",
                "mode": "production",
                "profile_name": "explicit",
                "collection_items": [{"command": "show version", "action": "patrol"}],
            }

        def command_texts(self):
            return ["show version"]

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            assert device["device_id"] == "device-1"
            assert action == "patrol"
            assert mode == "production"
            return FakePlan()

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)
    monkeypatch.setattr(
        devices_api,
        "_production_parser_coverage",
        lambda device, commands: [
            {
                "canonical_command": "show version",
                "policy_status": "eligible",
                "coverage_status": "ready",
                "template_id": "template-1",
            }
        ],
    )

    result = devices_api.preview_device_plan(
        "device-1",
        devices_api.DevicePlanPreviewRequest(action="patrol", mode="production"),
    )

    assert result["success"] is True
    assert result["commands"] == ["show version"]
    assert result["plan"]["device_id"] == "device-1"
    assert result["parser_coverage"][0]["template_id"] == "template-1"
    assert result["coverage_summary"]["status"] == "blocked"
    assert result["coverage_summary"]["ready_count"] == 1
    assert (
        result["coverage_summary"]["connection_issues"][0]["failure_code"] == "missing_credential"
    )


def test_device_connectivity_test_blocks_preflight_issue(monkeypatch):
    health_updates = []

    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["device_id"] == "device-1"
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                }
            ]

        def record_connection_health(self, device_id, **kwargs):
            health_updates.append((device_id, kwargs))
            return {
                "device_id": device_id,
                "device_name": "SW1",
                "last_connection_status": kwargs["status"],
                "last_connection_failure_code": kwargs["failure_code"],
            }

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.test_device_connectivity_endpoint("device-1")

    assert result["success"] is False
    assert result["failure_code"] == "collector_preflight_failed"
    assert result["connection_issues"][0]["failure_code"] == "missing_credential"
    assert result["device"]["last_connection_status"] == "preflight_failed"
    assert "password" not in result["device"]
    assert health_updates == [
        (
            "device-1",
            {
                "status": "preflight_failed",
                "failure_code": "missing_credential",
                "error": "credential_ref is required before patrol launch",
                "source": "connectivity-test",
            },
        )
    ]


def test_device_connectivity_test_uses_collector_connection_path(monkeypatch):
    health_updates = []

    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "password": "secret",
                }
            ]

        def record_connection_health(self, device_id, **kwargs):
            health_updates.append((device_id, kwargs))
            return {
                "device_id": device_id,
                "device_name": "SW1",
                "last_connection_status": kwargs["status"],
                "last_connection_latency_ms": kwargs["latency_ms"],
                "password": "secret",
            }

    def fake_test_device_connectivity(device):
        assert device["device_id"] == "device-1"
        return {
            "success": True,
            "failure_code": None,
            "device_name": "SW1",
            "ip": "10.0.0.1",
            "device_type": "cisco_ios",
            "prompt": "SW1#",
            "elapsed_seconds": 0.01,
        }

    import src.core.device_ops.tools as tools

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(tools, "test_device_connectivity", fake_test_device_connectivity)

    result = devices_api.test_device_connectivity_endpoint("device-1")

    assert result["success"] is True
    assert result["prompt"] == "SW1#"
    assert result["device_type"] == "cisco_ios"
    assert result["connection_issues"] == []
    assert result["device"]["last_connection_status"] == "connected"
    assert result["device"]["last_connection_latency_ms"] == 10
    assert "password" not in result["device"]
    assert health_updates == [
        (
            "device-1",
            {
                "status": "connected",
                "failure_code": None,
                "error": None,
                "latency_ms": 10,
                "source": "connectivity-test",
                "metadata": {
                    "device_type": "cisco_ios",
                    "prompt": "SW1#",
                },
            },
        )
    ]


def test_upsert_device_returns_redacted_device(monkeypatch):
    class FakeRepository:
        def upsert_device(self, **kwargs):
            assert kwargs["device_name"] == "SW1"
            assert kwargs["management_ip"] == "10.0.0.1"
            assert kwargs["groups"] == ["core"]
            return "device-1"

        def get_devices_by_filter(self, **kwargs):
            assert kwargs["device_id"] == "device-1"
            return [{"device_id": "device-1", "device_name": "SW1", "password": "secret"}]

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.upsert_device(
        devices_api.DeviceUpsertRequest(
            device_name="SW1",
            management_ip="10.0.0.1",
            groups=["core"],
        ),
        None,
    )

    assert result["success"] is True
    assert result["device_id"] == "device-1"
    assert "password" not in result["device"]


def test_upsert_device_writes_audit_log(monkeypatch):
    from types import SimpleNamespace

    from src.auth.models import CurrentUser

    audit_calls = []

    class FakeRepository:
        def upsert_device(self, **kwargs):
            return "device-1"

        def get_devices_by_filter(self, **kwargs):
            return [{"device_id": "device-1", "device_name": "SW1", "password": "secret"}]

    request = SimpleNamespace(
        state=SimpleNamespace(
            current_user=CurrentUser(user_id="user-1", username="admin", role="admin")
        ),
        client=SimpleNamespace(host="127.0.0.1"),
    )

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "write_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    result = devices_api.upsert_device(
        devices_api.DeviceUpsertRequest(
            device_name="SW1",
            management_ip="10.0.0.1",
            password="secret",
        ),
        request,
    )

    assert result["success"] is True
    assert audit_calls[0]["action"] == "device_upsert"
    assert audit_calls[0]["user_id"] == "user-1"
    assert audit_calls[0]["resource_id"] == "device-1"
    assert audit_calls[0]["ip_address"] == "127.0.0.1"
    assert "password" not in audit_calls[0]["detail"]


def test_update_device_uses_patch_payload(monkeypatch):
    class FakeRepository:
        def update_device(self, device_id, **kwargs):
            assert device_id == "device-1"
            assert kwargs == {
                "platform": "ios",
                "device_type": "access_switch",
                "site": "xian",
                "groups": ["core", "edge"],
            }
            return {
                "device_id": "device-1",
                "device_name": "SW1",
                "site": "xian",
                "platform": "ios",
                "device_type": "access_switch",
                "groups": ["core", "edge"],
                "password": "secret",
            }

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.update_device(
        "device-1",
        devices_api.DeviceUpdateRequest(
            platform="ios",
            device_type="access_switch",
            site="xian",
            groups=["core", "edge"],
        ),
        None,
    )

    assert result["success"] is True
    assert result["device"]["site"] == "xian"
    assert result["device"]["platform"] == "ios"
    assert result["device"]["device_type"] == "access_switch"
    assert "password" not in result["device"]


def test_update_device_status(monkeypatch):
    class FakeRepository:
        def set_device_status(self, device_id, status):
            assert device_id == "device-1"
            assert status == "inactive"
            return {"device_id": "device-1", "status": "inactive"}

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.update_device_status(
        "device-1",
        devices_api.DeviceStatusRequest(status="inactive"),
        None,
    )

    assert result["success"] is True
    assert result["device"]["status"] == "inactive"


def test_delete_device_soft_deletes(monkeypatch):
    class FakeRepository:
        def delete_device(self, device_id):
            assert device_id == "device-1"
            return {"device_id": "device-1", "status": "deleted", "password": "secret"}

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.delete_device("device-1", None)

    assert result["success"] is True
    assert result["device"]["status"] == "deleted"
    assert "password" not in result["device"]


def test_update_device_groups(monkeypatch):
    class FakeRepository:
        def set_device_groups(self, device_id, groups):
            assert device_id == "device-1"
            assert groups == ["core", "xian"]
            return {"device_id": "device-1", "groups": groups}

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.update_device_groups(
        "device-1",
        devices_api.DeviceGroupsRequest(groups=["core", "xian"]),
        None,
    )

    assert result["success"] is True
    assert result["device"]["groups"] == ["core", "xian"]


def test_preview_device_import_marks_create_update_and_invalid(monkeypatch):
    class FakeRepository:
        def get_device_by_ip_or_name(self, identifier):
            if identifier == "10.0.0.2":
                return {"device_id": "existing-1", "device_name": "SW2"}
            return None

        def get_devices_by_filter(self, **kwargs):
            return []

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    csv_text = "\n".join(
        [
            "device_name,management_ip,vendor,model,platform,device_type,os_version,serial_number,groups",
            'SW1,10.0.0.1,Cisco,WS-C2960XR-24TS-I,ios,access_switch,15.2,FOC1234,"core,xian"',
            "SW2,10.0.0.2,H3C,edge",
            "BROKEN,,Cisco,core",
        ]
    )
    result = devices_api.preview_device_import(devices_api.DeviceImportRequest(content=csv_text))

    assert result["total"] == 3
    assert result["create_count"] == 1
    assert result["update_count"] == 1
    assert result["invalid_count"] == 1
    assert result["rows"][0]["action"] == "create"
    assert result["rows"][0]["device"]["groups"] == ["core", "xian"]
    assert result["rows"][0]["device"]["platform"] == "ios"
    assert result["rows"][0]["device"]["device_type"] == "access_switch"
    assert result["rows"][0]["device"]["os_version"] == "15.2"
    assert result["rows"][0]["device"]["serial_number"] == "FOC1234"
    assert result["rows"][1]["action"] == "update"
    assert result["rows"][1]["existing_device_id"] == "existing-1"
    assert result["rows"][2]["action"] == "invalid"


def test_preview_device_import_accepts_xlsx(monkeypatch):
    from openpyxl import Workbook

    class FakeRepository:
        def get_device_by_ip_or_name(self, identifier):
            return None

        def get_devices_by_filter(self, **kwargs):
            return []

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "device_name",
            "management_ip",
            "vendor",
            "model",
            "groups",
            "attributes_json",
        ]
    )
    sheet.append(
        [
            "SW-XLSX-1",
            "10.0.0.11",
            "Cisco",
            "WS-C2960XR-24TS-I",
            "core,xian",
            '{"collector_options":{"read_timeout":60}}',
        ]
    )
    buffer = BytesIO()
    workbook.save(buffer)

    result = devices_api.preview_device_import(
        devices_api.DeviceImportRequest(
            content=base64.b64encode(buffer.getvalue()).decode("ascii"),
            format="xlsx",
            filename="devices.xlsx",
        )
    )

    assert result["total"] == 1
    assert result["create_count"] == 1
    assert result["invalid_count"] == 0
    assert result["rows"][0]["device"]["device_name"] == "SW-XLSX-1"
    assert result["rows"][0]["device"]["groups"] == ["core", "xian"]
    assert result["rows"][0]["device"]["attributes"] == {"collector_options": {"read_timeout": 60}}


def test_apply_device_import_upserts_valid_rows(monkeypatch):
    calls = []

    class FakeRepository:
        def get_device_by_ip_or_name(self, identifier):
            return None

        def get_devices_by_filter(self, **kwargs):
            if "device_id" in kwargs:
                return [
                    {"device_id": kwargs["device_id"], "device_name": "SW1", "password": "secret"}
                ]
            return []

        def upsert_device(self, **kwargs):
            calls.append(kwargs)
            return f"device-{len(calls)}"

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    csv_text = "\n".join(
        [
            "device_name,management_ip,vendor,attributes_json",
            'SW1,10.0.0.1,Cisco,"{""collector_options"": {""read_timeout"": 60}}"',
            "BROKEN,,Cisco,{}",
        ]
    )
    result = devices_api.apply_device_import(
        devices_api.DeviceImportRequest(content=csv_text, dry_run=False),
        None,
    )

    assert result["success"] is True
    assert result["applied_count"] == 1
    assert result["invalid_count"] == 1
    assert calls[0]["device_name"] == "SW1"
    assert calls[0]["attributes"] == {"collector_options": {"read_timeout": 60}}
    assert "password" not in result["applied"][0]["device"]


def test_preview_device_bulk_update_uses_filters_and_append_groups(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["vendor"] == "Cisco"
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "groups": ["core"],
                    "password": "secret",
                }
            ]

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.preview_device_bulk_update(
        devices_api.DeviceBulkRequest(
            filters=devices_api.DeviceBulkFilters(vendor="Cisco"),
            update=devices_api.DeviceBulkUpdate(
                command_profile="xian-core",
                platform="ios",
                device_type="access_switch",
                groups=["xian"],
                group_mode="append",
            ),
        )
    )

    assert result["success"] is True
    assert result["target_count"] == 1
    assert result["changed_fields"] == ["command_profile", "platform", "device_type", "groups"]
    assert result["rows"][0]["update"]["platform"] == "ios"
    assert result["rows"][0]["update"]["device_type"] == "access_switch"
    assert result["rows"][0]["update"]["groups"] == ["core", "xian"]
    assert "password" not in result["rows"][0]["current"]


def test_apply_device_bulk_update_updates_targets(monkeypatch):
    calls = []

    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            if kwargs.get("device_id") == "device-1":
                return [
                    {
                        "device_id": "device-1",
                        "device_name": "SW1",
                        "ip": "10.0.0.1",
                        "groups": ["core"],
                    }
                ]
            return []

        def update_device(self, device_id, **kwargs):
            calls.append((device_id, kwargs))
            return {
                "device_id": device_id,
                "device_name": "SW1",
                "command_file": kwargs["command_profile"],
                "groups": kwargs["groups"],
                "password": "secret",
            }

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.apply_device_bulk_update(
        devices_api.DeviceBulkRequest(
            device_ids=["device-1"],
            update=devices_api.DeviceBulkUpdate(
                command_profile="xian-core",
                platform="ios",
                device_type="access_switch",
                groups=["core", "xian"],
                group_mode="replace",
            ),
        ),
        None,
    )

    assert result["success"] is True
    assert result["applied_count"] == 1
    assert calls == [
        (
            "device-1",
            {
                "command_profile": "xian-core",
                "platform": "ios",
                "device_type": "access_switch",
                "groups": ["core", "xian"],
            },
        )
    ]
    assert "password" not in result["devices"][0]


def test_apply_device_bulk_update_can_bind_credential_ref(monkeypatch):
    calls = []

    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "groups": [],
                }
            ]

        def update_device(self, device_id, **kwargs):
            calls.append((device_id, kwargs))
            return {
                "device_id": device_id,
                "device_name": "SW1",
                "credential_ref": kwargs["credential_ref"],
                "password": "secret",
            }

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.apply_device_bulk_update(
        devices_api.DeviceBulkRequest(
            device_ids=["device-1"],
            update=devices_api.DeviceBulkUpdate(credential_ref="env:NETOPS_PASSWORD"),
        ),
        None,
    )

    assert result["success"] is True
    assert calls == [("device-1", {"credential_ref": "env:NETOPS_PASSWORD"})]
    assert result["devices"][0]["credential_ref"] == "env:NETOPS_PASSWORD"
    assert "password" not in result["devices"][0]


def test_apply_device_bulk_update_does_not_touch_attributes(monkeypatch):
    calls = []

    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "groups": [],
                    "attributes": {"owner": "netops"},
                }
            ]

        def update_device(self, device_id, **kwargs):
            calls.append((device_id, kwargs))
            return {
                "device_id": device_id,
                "device_name": "SW1",
                "credential_ref": kwargs["credential_ref"],
                "attributes": {"owner": "netops"},
                "password": "secret",
            }

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.apply_device_bulk_update(
        devices_api.DeviceBulkRequest(
            device_ids=["device-1"],
            update=devices_api.DeviceBulkUpdate(
                credential_ref="env:NETOPS_PASSWORD",
            ),
        ),
        None,
    )

    assert result["success"] is True
    assert calls == [
        (
            "device-1",
            {
                "credential_ref": "env:NETOPS_PASSWORD",
            },
        )
    ]
    assert result["devices"][0]["credential_ref"] == "env:NETOPS_PASSWORD"
    assert result["devices"][0]["attributes"] == {"owner": "netops"}
    assert "password" not in result["devices"][0]


def test_preview_device_bulk_update_uses_dynamic_group(monkeypatch):
    class FakeRepository:
        def resolve_dynamic_group_devices(self, group_id):
            assert group_id == "group-1"
            return (
                {"id": "group-1", "name": "XiAn Access", "status": "active"},
                [
                    {
                        "device_id": "device-1",
                        "device_name": "SW1",
                        "ip": "10.0.0.1",
                        "groups": [],
                        "password": "secret",
                    }
                ],
            )

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.preview_device_bulk_update(
        devices_api.DeviceBulkRequest(
            dynamic_group_id="group-1",
            update=devices_api.DeviceBulkUpdate(site="xian"),
        )
    )

    assert result["success"] is True
    assert result["target_count"] == 1
    assert result["rows"][0]["device_id"] == "device-1"
    assert "password" not in result["rows"][0]["current"]


def test_device_dynamic_group_crud_and_preview(monkeypatch):
    calls = []

    class FakeRepository:
        def list_dynamic_groups(self, *, status=None):
            assert status == "active"
            return [
                {
                    "id": "group-1",
                    "name": "XiAn Access",
                    "status": "active",
                    "selector": {"site": "xian"},
                }
            ]

        def upsert_dynamic_group(self, **kwargs):
            calls.append(kwargs)
            return {
                "id": kwargs.get("group_id") or "group-1",
                "name": kwargs["name"],
                "description": kwargs.get("description"),
                "selector": kwargs["selector"],
                "status": kwargs["status"],
            }

        def set_dynamic_group_status(self, group_id, status):
            assert group_id == "group-1"
            assert status == "disabled"
            return {
                "id": group_id,
                "name": "XiAn Access",
                "status": status,
                "selector": {"site": "xian"},
            }

        def resolve_dynamic_group_devices(self, group_id):
            assert group_id == "group-1"
            return (
                {
                    "id": "group-1",
                    "name": "XiAn Access",
                    "status": "active",
                    "selector": {"site": "xian"},
                },
                [{"device_id": "device-1", "device_name": "SW1", "password": "secret"}],
            )

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    listed = devices_api.list_device_dynamic_groups(status="active")
    created = devices_api.upsert_device_dynamic_group(
        devices_api.DeviceDynamicGroupRequest(
            name="XiAn Access",
            selector={"site": "xian", "device_type": "access_switch"},
            status="active",
        ),
        None,
    )
    updated = devices_api.update_device_dynamic_group(
        "group-1",
        devices_api.DeviceDynamicGroupRequest(
            name="XiAn Access",
            selector={"site": "xian"},
            status="active",
        ),
        None,
    )
    disabled = devices_api.update_device_dynamic_group_status(
        "group-1",
        devices_api.DeviceStatusRequest(status="disabled"),
        None,
    )
    preview = devices_api.preview_device_dynamic_group("group-1", limit=50)

    assert listed["items"][0]["id"] == "group-1"
    assert created["group"]["selector"]["device_type"] == "access_switch"
    assert updated["group"]["id"] == "group-1"
    assert disabled["group"]["status"] == "disabled"
    assert preview["total"] == 1
    assert "password" not in preview["items"][0]
    assert calls[1]["group_id"] == "group-1"


def test_device_credentials_api_lists_and_inspects_refs(monkeypatch):
    monkeypatch.setenv("NETOPS_PASSWORD", "secret")

    listed = devices_api.list_device_credentials()
    inspected = devices_api.inspect_device_credential(
        devices_api.CredentialInspectRequest(credential_ref="env:NETOPS_PASSWORD")
    )
    unsupported = devices_api.inspect_device_credential(
        devices_api.CredentialInspectRequest(credential_ref="plain:secret")
    )

    assert any(
        item["ref"] == "env:NETOPS_PASSWORD" and item["available"] for item in listed["items"]
    )
    assert all("secret" not in str(item) for item in listed["items"])
    assert inspected["supported"] is True
    assert inspected["available"] is True
    assert unsupported["failure_code"] == "unsupported_scheme"


def test_preview_device_patrol_launch_builds_execution_plans(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["device_id"] == "device-1"
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "password": "secret",
                }
            ]

    class FakePlan:
        commands = ["show version", "show inventory"]
        profile_version_id = "profile-version-1"
        profile_name = "site-patrol"
        policy_source = "profile_binding"

        def to_metadata(self):
            return {
                "device_id": "device-1",
                "action": "patrol",
                "mode": "production",
                "profile_version_id": self.profile_version_id,
                "profile_name": self.profile_name,
                "policy_source": self.policy_source,
                "collection_items": [{"command": item} for item in self.commands],
            }

        def command_texts(self):
            return self.commands

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            assert device["device_id"] == "device-1"
            assert action == "patrol"
            assert mode == "production"
            return FakePlan()

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)
    monkeypatch.setattr(devices_api, "_production_parser_coverage", lambda device, commands: [])

    result = devices_api.preview_device_patrol_launch(
        devices_api.DevicePatrolLaunchRequest(device_ids=["device-1"])
    )

    assert result["success"] is True
    assert result["target_count"] == 1
    assert result["ready_count"] == 1
    assert result["rows"][0]["command_count"] == 2
    assert "password" not in result["rows"][0]["device"]


def test_preview_device_patrol_launch_blocks_disabled_agent_module(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "password": "secret",
                }
            ]

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(
        devices_api,
        "_agent_module_control_violations",
        lambda module_ids: [
            {
                "failure_code": "agent_module_not_enabled",
                "module_id": "change-detector",
                "status": "maintenance",
                "maintenance_reason": "规则升级窗口",
                "message": "Agent 模块 change-detector 当前为 maintenance：规则升级窗口",
            }
        ],
    )

    result = devices_api.preview_device_patrol_launch(
        devices_api.DevicePatrolLaunchRequest(device_ids=["device-1"])
    )

    assert result["success"] is False
    assert result["ready_count"] == 0
    assert result["error_count"] == 1
    assert result["module_violations"][0]["module_id"] == "change-detector"
    assert result["errors"][0]["failure_code"] == "agent_module_not_ready"
    assert result["errors"][0]["module_violations"][0]["status"] == "maintenance"


def test_preview_device_patrol_launch_blocks_production_without_active_profile(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "password": "secret",
                }
            ]

    class FakePlan:
        commands = ["show version"]
        profile_version_id = None
        profile_name = "default-patrol"
        policy_source = "default"

        def to_metadata(self):
            return {
                "device_id": "device-1",
                "action": "patrol",
                "mode": "production",
                "profile_name": self.profile_name,
                "policy_source": self.policy_source,
                "collection_items": [{"command": item} for item in self.commands],
            }

        def command_texts(self):
            return self.commands

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            return FakePlan()

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)
    monkeypatch.setattr(devices_api, "_production_parser_coverage", lambda device, commands: [])

    result = devices_api.preview_device_patrol_launch(
        devices_api.DevicePatrolLaunchRequest(device_ids=["device-1"], mode="production")
    )

    assert result["success"] is False
    assert result["ready_count"] == 0
    assert result["error_count"] == 1
    assert result["errors"][0]["failure_code"] == "command_profile_not_ready"
    assert (
        result["errors"][0]["plan_violations"][0]["failure_code"]
        == "active_command_profile_required"
    )


def test_preview_device_patrol_launch_blocks_empty_plan_in_validation_mode(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "password": "secret",
                }
            ]

    class FakePlan:
        commands = []
        profile_version_id = None
        profile_name = "missing-profile"
        policy_source = "default"

        def to_metadata(self):
            return {
                "device_id": "device-1",
                "action": "patrol",
                "mode": "parser_validation",
                "profile_name": self.profile_name,
                "policy_source": self.policy_source,
                "collection_items": [],
            }

        def command_texts(self):
            return []

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            return FakePlan()

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)

    result = devices_api.preview_device_patrol_launch(
        devices_api.DevicePatrolLaunchRequest(device_ids=["device-1"], mode="parser-validation")
    )

    assert result["success"] is False
    assert result["ready_count"] == 0
    assert result["error_count"] == 1
    assert result["errors"][0]["failure_code"] == "command_profile_not_ready"
    assert (
        result["errors"][0]["plan_violations"][0]["failure_code"] == "collection_commands_required"
    )


def test_preview_device_patrol_launch_warns_without_blocking_missing_parser_coverage(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                    "password": "secret",
                }
            ]

    class FakePlan:
        commands = ["show inventory"]
        profile_version_id = "profile-version-1"
        profile_name = "site-patrol"
        policy_source = "profile_binding"

        def to_metadata(self):
            return {
                "device_id": "device-1",
                "action": "patrol",
                "mode": "production",
                "profile_version_id": self.profile_version_id,
                "profile_name": self.profile_name,
                "policy_source": self.policy_source,
                "collection_items": [{"command": item} for item in self.commands],
            }

        def command_texts(self):
            return self.commands

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            return FakePlan()

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)
    monkeypatch.setattr(
        devices_api,
        "_production_parser_coverage",
        lambda device, commands: [
            {
                "canonical_command": "show inventory",
                "policy_status": "eligible",
                "coverage_status": "missing",
            }
        ],
    )

    result = devices_api.preview_device_patrol_launch(
        devices_api.DevicePatrolLaunchRequest(device_ids=["device-1"], mode="production")
    )

    assert result["success"] is True
    assert result["ready_count"] == 1
    assert result["error_count"] == 0
    assert result["errors"] == []
    assert result["rows"][0]["coverage_violations"][0]["canonical_command"] == "show inventory"


def test_preview_device_patrol_launch_blocks_missing_runtime_rule_assets(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                    "password": "secret",
                }
            ]

    class FakePlan:
        commands = ["show inventory"]
        profile_version_id = "profile-version-1"
        profile_name = "site-patrol"
        policy_source = "profile_binding"

        def to_metadata(self):
            return {
                "device_id": "device-1",
                "action": "patrol",
                "mode": "production",
                "profile_version_id": self.profile_version_id,
                "profile_name": self.profile_name,
                "policy_source": self.policy_source,
                "collection_items": [{"command": item} for item in self.commands],
            }

        def command_texts(self):
            return self.commands

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            return FakePlan()

    class FakeBundle:
        def to_dict(self):
            return {
                "ready": False,
                "observation_contracts": {"cli:template-1": {"entity_type": "interface"}},
                "change_policies": {},
                "event_rules": [],
                "missing_observation_contracts": [],
                "missing_change_policies": ["interface.oper_status"],
                "counts": {
                    "observation_contracts": 1,
                    "change_policies": 0,
                    "event_rules": 0,
                },
            }

    class FakeRuntimeResolver:
        def load_bundle(self, *, observation_schemas, include_event_rules):
            assert observation_schemas == [("cli", "template-1")]
            assert include_event_rules is True
            return FakeBundle()

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)
    monkeypatch.setattr(devices_api, "RuntimeRuleAssetResolver", FakeRuntimeResolver)
    monkeypatch.setattr(
        devices_api,
        "_production_parser_coverage",
        lambda device, commands: [
            {
                "canonical_command": "show inventory",
                "policy_status": "eligible",
                "coverage_status": "ready",
                "template_id": "template-1",
            }
        ],
    )

    result = devices_api.preview_device_patrol_launch(
        devices_api.DevicePatrolLaunchRequest(device_ids=["device-1"], mode="production")
    )

    assert result["success"] is False
    assert result["ready_count"] == 0
    assert result["error_count"] == 1
    assert result["errors"][0]["failure_code"] == "runtime_rule_assets_not_ready"
    assert result["errors"][0]["runtime_asset_health"]["status"] == "blocked"
    assert result["errors"][0]["runtime_asset_health"]["missing_change_policies"] == [
        "interface.oper_status"
    ]


def test_preview_device_patrol_launch_blocks_missing_credential(monkeypatch):
    health_updates = []

    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                }
            ]

        def record_connection_health(self, device_id, **kwargs):
            health_updates.append((device_id, kwargs))
            return {"device_id": device_id, "last_connection_status": kwargs["status"]}

    class FakePlan:
        commands = ["show version"]
        profile_version_id = "profile-version-1"
        profile_name = "site-patrol"
        policy_source = "profile_binding"

        def to_metadata(self):
            return {
                "device_id": "device-1",
                "action": "patrol",
                "mode": "production",
                "profile_version_id": self.profile_version_id,
                "profile_name": self.profile_name,
                "policy_source": self.policy_source,
                "collection_items": [{"command": item} for item in self.commands],
            }

        def command_texts(self):
            return self.commands

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            return FakePlan()

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)
    monkeypatch.setattr(devices_api, "_production_parser_coverage", lambda device, commands: [])

    result = devices_api.preview_device_patrol_launch(
        devices_api.DevicePatrolLaunchRequest(device_ids=["device-1"], mode="production")
    )

    assert result["success"] is False
    assert result["ready_count"] == 0
    assert result["error_count"] == 1
    assert result["errors"][0]["failure_code"] == "collector_preflight_failed"
    assert result["errors"][0]["connection_issues"][0]["failure_code"] == "missing_credential"
    assert health_updates == [
        (
            "device-1",
            {
                "status": "preflight_failed",
                "failure_code": "missing_credential",
                "error": "credential_ref is required before patrol launch",
                "source": "patrol-preflight",
            },
        )
    ]


def test_list_devices_filters_by_connection_status(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["connection_status"] == "failed"
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "last_connection_status": "failed",
                    "password": "secret",
                }
            ]

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.list_devices(
        connection_status="failed",
        limit=100,
        offset=0,
    )

    assert result["total"] == 1
    assert result["items"][0]["last_connection_status"] == "failed"
    assert "password" not in result["items"][0]


def test_list_device_connection_health_returns_history(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["device_id"] == "device-1"
            assert kwargs["status"] is None
            return [{"device_id": "device-1", "device_name": "SW1", "password": "secret"}]

        def list_connection_health(self, device_id, *, limit):
            assert device_id == "device-1"
            assert limit == 25
            return [
                {
                    "id": "health-1",
                    "device_id": "device-1",
                    "status": "failed",
                    "failure_code": "auth_failed",
                    "error": "login failed",
                    "latency_ms": None,
                    "source": "device-patrol",
                    "metadata": {},
                    "checked_at": "2026-06-21T10:00:00+00:00",
                }
            ]

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)

    result = devices_api.list_device_connection_health("device-1", limit=25)

    assert "password" not in result["device"]
    assert result["items"][0]["source"] == "device-patrol"
    assert result["items"][0]["failure_code"] == "auth_failed"


def test_list_device_execution_plans_returns_auditable_records(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["device_id"] == "device-1"
            assert kwargs["status"] is None
            return [{"device_id": "device-1", "device_name": "SW1", "password": "secret"}]

    def fake_plans(device_id, *, limit):
        assert device_id == "device-1"
        assert limit == 5
        return [
            {
                "execution_plan_id": "plan-1",
                "device_id": "device-1",
                "status": "launched",
                "command_count": 2,
                "plan_sha256": "a" * 64,
                "credential_ref": "env:NETOPS_PASSWORD",
            }
        ]

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "_list_execution_plans_for_device", fake_plans)

    result = devices_api.list_device_execution_plans("device-1", limit=5)

    assert "password" not in result["device"]
    assert result["items"][0]["execution_plan_id"] == "plan-1"
    assert result["items"][0]["credential_ref"] == "env:NETOPS_PASSWORD"


def test_list_device_evidence_timeline_redacts_device(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["device_id"] == "device-1"
            assert kwargs["status"] is None
            return [{"device_id": "device-1", "device_name": "SW1", "password": "secret"}]

    timeline_calls = []

    def fake_timeline(
        device,
        *,
        limit,
        workflow_run_id=None,
        patrol_batch_id=None,
    ):
        assert device["password"] == "secret"
        timeline_calls.append((limit, workflow_run_id, patrol_batch_id))
        return {
            "device": devices_api._redact_device(device),
            "items": [
                {
                    "workflow_run_id": "workflow-1",
                    "snapshot_run_id": "patrol-1",
                    "snapshot_count": 2,
                    "observation_count": 4,
                    "change_count": 1,
                    "event_count": 1,
                }
            ],
            "limit": limit,
        }

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "_device_evidence_timeline", fake_timeline)

    result = devices_api.list_device_evidence_timeline("device-1", limit=5)
    devices_api.list_device_evidence_timeline(
        "device-1",
        limit=1,
        workflow_run_id="run-1",
        patrol_batch_id="batch-1",
    )

    assert "password" not in result["device"]
    assert result["items"][0]["snapshot_run_id"] == "patrol-1"
    assert timeline_calls == [(5, None, None), (1, "run-1", "batch-1")]


def test_device_api_has_no_known_mojibake_tokens():
    source = Path(devices_api.__file__).read_text(encoding="utf-8")
    suspicious = (
        "\ufffd",
        "锛",
        "銆",
        "鈫",
        "缁撴",
        "鏈€",
        "楠岃瘉",
        "澶辫触",
        "鍙樺寲",
        "璁惧",
        "鎶ュ憡",
        "灏氭湭",
    )

    assert not [token for token in suspicious if token in source]


def test_device_timeline_includes_direct_patrol_and_legacy_opening_event():
    run_id = f"timeline-{uuid.uuid4()}"
    snapshot_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    change_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    device_id = f"device-{uuid.uuid4()}"
    with get_db_session() as db:
        db.add(
            PatrolRun(
                id=run_id,
                logical_run_id=run_id,
                mode="production",
                source="device-patrol",
                status="completed",
                collection_status="completed",
                analysis_status="completed",
                baseline_eligible=False,
                run_purpose="scheduled_patrol",
                started_at=now,
                completed_at=now,
            )
        )
        db.flush()
        db.add(
            CommandSnapshot(
                id=snapshot_id,
                run_id=run_id,
                execution_item_id=f"item-{snapshot_id}",
                device_id=device_id,
                device_name="SW1",
                command="show interface status",
                canonical_command="show interface status",
                observed_at=now,
                raw_text="Gi1/0/1 connected",
                raw_text_hash="a" * 64,
                parse_status="parsed",
                snapshot_kind="full",
            )
        )
        db.add(
            NetworkChangeRecord(
                id=change_id,
                idempotency_key=uuid.uuid4().hex,
                run_id=run_id,
                device_id=device_id,
                canonical_command="show interface transceiver detail",
                entity_type="interface_optical",
                entity_key='["Gi1/0/1"]',
                field="rx_power_dbm",
                observation_type="interface.optical.rx_power",
                old_value_json=-15.0,
                new_value_json=-18.0,
                old_number=-15.0,
                new_number=-18.0,
                change_type="modified",
                source="patrol",
                event_status="event_created",
                change_policy_id="optical-policy",
                change_policy_version_id="optical-policy-v2",
                comparison_type="threshold",
                decision_reason="change_policy_matched",
                policy_snapshot_json={"thresholds": {"low": -17.0}, "recovery_threshold": -15.0},
                detected_at=now,
                processed_at=now,
                confidence=1.0,
            )
        )
        db.add(
            NetworkEventRecord(
                id=event_id,
                idempotency_key=uuid.uuid4().hex,
                event_type="OpticalRxPowerLow",
                severity="major",
                device_id=device_id,
                entity_type="interface_optical",
                entity_key='["Gi1/0/1"]',
                correlation_key=uuid.uuid4().hex,
                source_change_id=change_id,
                run_id=run_id,
                latest_change_id=None,
                last_seen_run_id=run_id,
                occurred_at=now,
                first_seen_at=now,
                last_seen_at=now,
                occurrence_count=1,
                status="open",
                payload_json={"rule": {"rule_id": "optical-rx-low", "version": 3}},
                created_at=now,
            )
        )

    try:
        result = devices_api._device_evidence_timeline(
            {"device_id": device_id, "device_name": "SW1"},
            limit=5,
        )

        assert result["items"][0]["snapshot_run_id"] == run_id
        assert result["items"][0]["workflow_run_id"] is None
        assert result["items"][0]["snapshot_count"] == 1
        assert result["items"][0]["event_count"] == 1
        assert result["items"][0]["events"][0]["event_id"] == event_id
        assert result["items"][0]["changes"][0]["old_number"] == -15.0
        assert result["items"][0]["changes"][0]["new_number"] == -18.0
        assert result["items"][0]["changes"][0]["change_policy_version_id"] == "optical-policy-v2"
        assert result["items"][0]["events"][0]["new_number"] == -18.0
        assert result["items"][0]["events"][0]["event_rule_id"] == "optical-rx-low"
        assert result["items"][0]["events"][0]["event_rule_version"] == 3
        assert result["items"][0]["events"][0]["policy_snapshot"]["thresholds"]["low"] == -17.0
    finally:
        with get_db_session() as db:
            db.query(NetworkEventRecord).filter(NetworkEventRecord.id == event_id).delete(
                synchronize_session=False
            )
            db.query(NetworkChangeRecord).filter(NetworkChangeRecord.id == change_id).delete(
                synchronize_session=False
            )
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(synchronize_session=False)


def test_launch_device_patrol_submits_one_task_per_plan(monkeypatch):
    submitted = []

    class FakeTaskResult:
        id = "task-1"

    class FakeTask:
        def apply_async(self, *, kwargs, queue):
            submitted.append((kwargs, queue))
            return FakeTaskResult()

    class FakeTasksModule:
        execute_device_patrol_task = FakeTask()

    class FakePlanRepository:
        def create_plan(self, plan, **kwargs):
            assert kwargs["launch_source"] == "direct"
            return {"execution_plan_id": "plan-1"}

        def mark_launched(self, plan_id, **kwargs):
            assert plan_id == "plan-1"
            assert kwargs["launch_source"] == "direct"
            assert kwargs["celery_task_id"] == "task-1"
            return {"execution_plan_id": plan_id}

        def mark_failed(self, plan_id, **kwargs):
            raise AssertionError("direct launch should not fail")

    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "password": "secret",
                }
            ]

    class FakePlan:
        commands = ["show version"]
        profile_version_id = "profile-version-1"
        profile_name = "site-patrol"
        policy_source = "profile_binding"

        def to_metadata(self):
            return {
                "device_id": "device-1",
                "action": "patrol",
                "mode": "production",
                "profile_version_id": self.profile_version_id,
                "profile_name": self.profile_name,
                "policy_source": self.policy_source,
                "collection_items": [
                    {"command": item, "action": "patrol"} for item in self.commands
                ],
            }

        def command_texts(self):
            return self.commands

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            return FakePlan()

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)
    monkeypatch.setattr(devices_api, "ExecutionPlanRepository", FakePlanRepository)
    monkeypatch.setattr(devices_api, "_production_parser_coverage", lambda device, commands: [])

    import sys

    monkeypatch.setitem(sys.modules, "src.core.celery_tasks.tasks", FakeTasksModule())

    result = devices_api.launch_device_patrol(
        devices_api.DevicePatrolLaunchRequest(
            device_ids=["device-1"],
            ticket_id="PATROL-1",
            save_baseline=True,
            launch_mode="direct",
        ),
        None,
    )

    assert result["success"] is True
    assert result["launched_count"] == 1
    assert result["patrol_batch_id"].startswith("patrol-batch-")
    assert result["tasks"][0]["celery_task_id"] == "task-1"
    assert result["tasks"][0]["patrol_batch_id"] == result["patrol_batch_id"]
    assert result["tasks"][0]["execution_plan_id"] == "plan-1"
    assert submitted == [
        (
            {
                "execution_plan": {
                    "device_id": "device-1",
                    "action": "patrol",
                    "mode": "production",
                    "profile_version_id": "profile-version-1",
                    "profile_name": "site-patrol",
                    "policy_source": "profile_binding",
                    "collection_items": [{"command": "show version", "action": "patrol"}],
                },
                "execution_plan_id": "plan-1",
                "ticket_id": "PATROL-1",
                "save_baseline": True,
                "mode": "production",
                "patrol_batch_id": result["patrol_batch_id"],
            },
            "netops.patrol",
        )
    ]


def test_launch_device_patrol_starts_workflow_by_default(monkeypatch):
    started = []

    class FakePlanRepository:
        def create_plan(self, plan, **kwargs):
            assert kwargs["launch_source"] == "workflow"
            return {"execution_plan_id": "plan-1"}

        def mark_launched(self, plan_id, **kwargs):
            assert plan_id == "plan-1"
            assert kwargs["launch_source"] == "workflow"
            assert kwargs["workflow_run_id"] == "workflow-run-1"
            return {"execution_plan_id": plan_id}

        def mark_failed(self, plan_id, **kwargs):
            raise AssertionError("workflow launch should not fail")

    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "password": "secret",
                }
            ]

    class FakePlan:
        commands = ["show version"]
        profile_version_id = "profile-version-1"
        profile_name = "site-patrol"
        policy_source = "profile_binding"

        def to_metadata(self):
            return {
                "device_id": "device-1",
                "action": "patrol",
                "mode": "production",
                "profile_name": self.profile_name,
                "profile_version_id": self.profile_version_id,
                "policy_source": self.policy_source,
                "collection_items": [{"command": "show version", "action": "patrol"}],
            }

        def command_texts(self):
            return self.commands

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            return FakePlan()

    class FakeWorkflowEngine:
        @staticmethod
        def start(template_name, context, *, source):
            started.append((template_name, context, source))
            return "workflow-run-1"

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)
    monkeypatch.setattr(devices_api, "ExecutionPlanRepository", FakePlanRepository)
    monkeypatch.setattr(devices_api, "_production_parser_coverage", lambda device, commands: [])

    import src.core.workflows.engine as workflow_engine

    monkeypatch.setattr(workflow_engine, "WorkflowEngine", FakeWorkflowEngine)

    result = devices_api.launch_device_patrol(
        devices_api.DevicePatrolLaunchRequest(
            device_ids=["device-1"],
            ticket_id="PATROL-1",
            save_baseline=True,
        ),
        None,
    )

    assert result["success"] is True
    assert result["launch_mode"] == "workflow"
    assert result["patrol_batch_id"].startswith("patrol-batch-")
    assert result["runs"][0]["workflow_run_id"] == "workflow-run-1"
    assert result["runs"][0]["patrol_batch_id"] == result["patrol_batch_id"]
    assert result["runs"][0]["execution_plan_id"] == "plan-1"
    assert result["tasks"] == []
    assert started[0][0] == "patrol-change-event"
    assert started[0][1]["execution_plan"]["device_id"] == "device-1"
    assert started[0][1]["execution_plan_id"] == "plan-1"
    assert started[0][1]["ticket_id"] == "PATROL-1"
    assert started[0][1]["save_baseline"] is True
    assert started[0][1]["patrol_batch_id"] == result["patrol_batch_id"]
    assert started[0][2] == "device-management"


def test_launch_device_patrol_skips_failed_preflight_when_some_devices_ready(monkeypatch):
    started = []

    class FakePlanRepository:
        def create_plan(self, plan, **kwargs):
            assert kwargs["launch_source"] == "workflow"
            return {"execution_plan_id": "plan-1"}

        def mark_launched(self, plan_id, **kwargs):
            return {"execution_plan_id": plan_id}

        def mark_failed(self, plan_id, **kwargs):
            raise AssertionError("ready workflow launch should not fail")

    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            devices = [
                {
                    "device_id": "device-ready",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "password": "secret",
                    "status": "active",
                },
                {
                    "device_id": "device-blocked",
                    "device_name": "SW2",
                    "ip": "10.0.0.2",
                    "credential_ref": "env:MISSING_PASSWORD",
                    "status": "active",
                },
            ]
            if kwargs.get("device_id"):
                return [device for device in devices if device["device_id"] == kwargs["device_id"]]
            return devices

        def record_connection_health(self, *args, **kwargs):
            return None

    class FakePlan:
        commands = ["show version"]
        profile_version_id = "profile-version-1"
        profile_name = "site-patrol"
        policy_source = "profile_binding"

        def __init__(self, device_id):
            self.device_id = device_id

        def to_metadata(self):
            return {
                "device_id": self.device_id,
                "action": "patrol",
                "mode": "parser_validation",
                "profile_name": self.profile_name,
                "profile_version_id": self.profile_version_id,
                "policy_source": self.policy_source,
                "collection_items": [{"command": "show version", "action": "patrol"}],
            }

        def command_texts(self):
            return self.commands

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            return FakePlan(device["device_id"])

    class FakeWorkflowEngine:
        @staticmethod
        def start(template_name, context, *, source):
            started.append((template_name, context, source))
            return "workflow-run-1"

    monkeypatch.setattr(devices_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(devices_api, "CollectionPolicyResolver", FakeResolver)
    monkeypatch.setattr(devices_api, "ExecutionPlanRepository", FakePlanRepository)

    import src.core.workflows.engine as workflow_engine

    monkeypatch.setattr(workflow_engine, "WorkflowEngine", FakeWorkflowEngine)

    result = devices_api.launch_device_patrol(
        devices_api.DevicePatrolLaunchRequest(
            device_ids=["device-ready", "device-blocked"],
            mode="parser_validation",
            launch_mode="workflow",
        ),
        None,
    )

    assert result["success"] is True
    assert result["launched_count"] == 1
    assert result["skipped_count"] == 1
    assert result["skipped_errors"][0]["device_id"] == "device-blocked"
    assert started[0][1]["device_id"] == "device-ready"
