from __future__ import annotations

from src.gateway import parser_api


def test_effective_plan_preview_returns_commands(monkeypatch):
    class FakePlan:
        def to_metadata(self):
            return {
                "profile_name": "core",
                "profile_version_id": "v1",
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

    monkeypatch.setattr(parser_api, "CollectionPolicyResolver", FakeResolver)

    result = parser_api.effective_plan_preview(
        parser_api.EffectivePlanPreviewRequest(
            device={"device_id": "device-1"},
            action="patrol",
            mode="production",
        )
    )

    assert result["success"] is True
    assert result["commands"] == ["show version"]


def test_event_rule_bootstrap_endpoint(monkeypatch):
    class FakeRepository:
        def ensure_default_rules(self):
            return 9

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    assert parser_api.bootstrap_event_rules() == {"success": True, "created": 9}


def test_event_rule_test_endpoint_matches_samples():
    result = parser_api.test_event_rule(
        parser_api.EventRuleTestRequest(
            condition={
                "kind": "threshold",
                "fields": ["cpu_5s"],
                "gte": 90,
            },
            samples=[
                {"field": "cpu_5s", "new": 95},
                {"field": "cpu_5s", "new": 20},
                {"field": "memory", "new": 95},
            ],
        )
    )

    assert result["success"] is True
    assert result["sample_count"] == 3
    assert result["matched_count"] == 1
    assert result["unmatched_count"] == 2
    assert [row["matched"] for row in result["rows"]] == [True, False, False]


def test_event_rule_save_creates_draft_by_default(monkeypatch):
    class FakeRepository:
        def create_rule_version(
            self,
            *,
            name,
            event_type,
            severity,
            condition,
            description,
            created_by,
            status,
        ):
            assert name == "cpu-high-custom"
            assert event_type == "CPUHigh"
            assert severity == "warning"
            assert condition == {"kind": "threshold"}
            assert description is None
            assert created_by is None
            assert status == "draft"
            return "event-rule-version-1"

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    result = parser_api.upsert_event_rule(
        parser_api.EventRuleUpsertRequest(
            name="cpu-high-custom",
            event_type="CPUHigh",
            severity="warning",
            condition={"kind": "threshold"},
        )
    )

    assert result == {"success": True, "version_id": "event-rule-version-1"}


def test_event_rule_activate_endpoint(monkeypatch):
    class FakeRepository:
        def activate_rule_version(self, version_id):
            assert version_id == "event-rule-version-1"
            return {
                "version_id": version_id,
                "version_status": "active",
            }

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    result = parser_api.activate_event_rule("event-rule-version-1")

    assert result["version_status"] == "active"


def test_event_rule_deprecate_endpoint(monkeypatch):
    class FakeRepository:
        def deprecate_rule_version(self, version_id):
            assert version_id == "event-rule-version-1"
            return {
                "version_id": version_id,
                "version_status": "deprecated",
            }

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    result = parser_api.deprecate_event_rule("event-rule-version-1")

    assert result["version_status"] == "deprecated"


def test_command_profile_save_creates_draft_by_default(monkeypatch):
    class FakeRepository:
        def create_profile_version(self, *, name, action, commands, created_by, status):
            assert name == "core-patrol"
            assert action == "patrol"
            assert commands == ["show version"]
            assert created_by is None
            assert status == "draft"
            return "profile-version-1"

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.upsert_command_profile(
        parser_api.CommandProfileUpsertRequest(
            name="core-patrol",
            commands=["show version"],
        )
    )

    assert result == {
        "success": True,
        "profile_version_id": "profile-version-1",
        "status": "draft",
    }


def test_command_profile_activate_endpoint(monkeypatch):
    calls = []

    class FakeRepository:
        def validate_profile_activation(self, profile_version_id):
            calls.append(("validate", profile_version_id))

        def activate_profile_version(self, profile_version_id):
            calls.append(("activate", profile_version_id))
            assert profile_version_id == "profile-version-1"
            return {
                "profile_version_id": profile_version_id,
                "version_status": "active",
            }

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.activate_command_profile("profile-version-1")

    assert result["version_status"] == "active"
    assert calls == [
        ("validate", "profile-version-1"),
        ("activate", "profile-version-1"),
    ]


def test_command_profile_rollback_endpoint(monkeypatch):
    class FakeRepository:
        def rollback_profile_version(self, profile_version_id):
            assert profile_version_id == "profile-version-1"
            return {
                "profile_version_id": profile_version_id,
                "version_status": "active",
            }

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.rollback_command_profile("profile-version-1")

    assert result["version_status"] == "active"


def test_command_profile_binding_disable_endpoint(monkeypatch):
    class FakeRepository:
        def set_binding_status(self, binding_id, status):
            assert binding_id == "binding-1"
            assert status == "disabled"
            return {"id": binding_id, "status": status}

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.disable_command_profile_binding("binding-1")

    assert result == {"id": "binding-1", "status": "disabled"}


def test_command_profile_binding_endpoint(monkeypatch):
    class FakeRepository:
        def upsert_binding(self, *, profile_version_id, selector, priority, status):
            assert profile_version_id == "profile-version-1"
            assert selector == {"vendor": "Cisco", "role": "core"}
            assert priority == 10
            assert status == "active"
            return "binding-1"

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.upsert_command_profile_binding(
        parser_api.CommandProfileBindingUpsertRequest(
            profile_version_id="profile-version-1",
            selector={"vendor": "Cisco", "role": "core"},
            priority=10,
        )
    )

    assert result == {"success": True, "binding_id": "binding-1"}


def test_command_profile_impact_preview_with_inline_commands(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["status"] == "active"
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                    "role": "core",
                    "groups": ["xian"],
                },
                {
                    "device_id": "device-2",
                    "device_name": "SW2",
                    "ip": "10.0.0.2",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                    "role": "access",
                    "groups": ["xian"],
                },
            ]

    monkeypatch.setattr(parser_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(
        parser_api.PatrolTool,
        "_production_coverage",
        staticmethod(
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
            ]
        ),
    )

    result = parser_api.preview_command_profile_impact(
        parser_api.CommandProfileImpactPreviewRequest(
            profile_name="core-patrol",
            commands=["show version", "show inventory"],
            selector={"role": "core"},
        )
    )

    assert result["success"] is True
    assert result["matched_device_count"] == 1
    assert result["blocked_device_count"] == 1
    assert result["eligible_command_count"] == 2
    assert result["ready_command_count"] == 1
    assert result["blocked_command_count"] == 1
    assert result["coverage_rate"] == 0.5
    assert result["rows"][0]["device_id"] == "device-1"
    assert result["rows"][0]["violations"][0]["canonical_command"] == "show inventory"


def test_command_profile_impact_preview_can_load_profile_version(monkeypatch):
    saved = []

    class FakeProfileRepository:
        def get_profile_version(self, profile_version_id):
            assert profile_version_id == "profile-version-1"
            return {
                "profile_name": "site-patrol",
                "profile_version_id": profile_version_id,
                "items": [
                    {"action": "patrol", "command": "show version"},
                    {"action": "backup", "command": "show running-config"},
                ],
            }

        def record_profile_coverage(self, profile_version_id, coverage):
            saved.append((profile_version_id, coverage))
            return {"profile_version_id": profile_version_id, "coverage": coverage}

    class FakeDeviceRepository:
        def get_devices_by_filter(self, **kwargs):
            return [{"device_id": "device-1", "device_name": "SW1", "vendor": "Cisco"}]

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeProfileRepository)
    monkeypatch.setattr(parser_api, "DeviceRepository", FakeDeviceRepository)
    monkeypatch.setattr(
        parser_api.PatrolTool,
        "_production_coverage",
        staticmethod(
            lambda device, commands: [
                {
                    "canonical_command": commands[0],
                    "policy_status": "eligible",
                    "coverage_status": "ready",
                }
            ]
        ),
    )

    result = parser_api.preview_command_profile_impact(
        parser_api.CommandProfileImpactPreviewRequest(
            profile_version_id="profile-version-1",
            save_evidence=True,
        )
    )

    assert result["profile_name"] == "site-patrol"
    assert result["profile_version_id"] == "profile-version-1"
    assert result["command_count"] == 1
    assert result["ready_device_count"] == 1
    assert result["rows"][0]["plan"]["profile_version_id"] == "profile-version-1"
    assert result["evidence_saved"] is True
    assert saved[0][0] == "profile-version-1"
    assert saved[0][1]["blocked_device_count"] == 0
