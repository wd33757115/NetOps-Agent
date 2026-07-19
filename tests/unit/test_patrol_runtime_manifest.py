from __future__ import annotations

import pytest

from src.core.patrol import change_detector, event_builder
from src.core.patrol.runtime_manifest import (
    build_patrol_runtime_manifest,
    validate_patrol_runtime_manifest,
)
from src.core.rule_control.runtime_assets import RuntimeRuleAssetBundle


class _ParserBundle:
    digest = "parser-digest"

    def to_dict(self):
        return {
            "vendor": "H3C",
            "model": "S5130",
            "family": "Comware",
            "templates": {
                "display interface brief": {
                    "template_id": "template-1",
                    "version": "1",
                    "sha256": "template-sha",
                }
            },
            "digest": self.digest,
        }


class _Resolver:
    def load_bundle(self, **kwargs):
        assert kwargs["observation_schemas"] == [("cli", "template-1")]
        return RuntimeRuleAssetBundle(
            observation_contracts={
                "cli:template-1": {
                    "contract_id": "contract-1",
                    "state_semantics": {
                        "status": {"observation_type": "interface.oper_status"}
                    },
                }
            },
            change_policies={
                "interface.oper_status": {
                    "policy_id": "policy-1",
                    "version_id": "policy-version-1",
                    "observation_type": "interface.oper_status",
                    "policy": {
                        "observation_type": "interface.oper_status",
                        "semantic_type": "state",
                        "compare_mode": "exact",
                    },
                }
            },
            event_rules=[
                {
                    "rule_id": "interface-down",
                    "rule_name": "interface-down",
                    "version": 1,
                    "event_type": "InterfaceDown",
                    "severity": "high",
                    "condition": {
                        "kind": "transition",
                        "fields": ["status"],
                        "from_group": "up",
                        "to_group": "down",
                    },
                    "effective_condition": {
                        "kind": "transition",
                        "fields": ["status"],
                        "from_group": "up",
                        "to_group": "down",
                    },
                    "priority": 100,
                    "exclusive": True,
                    "event_class": "fault",
                    "correlation_keys": [
                        "device_id",
                        "entity_type",
                        "entity_key",
                        "field",
                    ],
                }
            ],
            digest="rules-digest",
        )


def _manifest():
    return build_patrol_runtime_manifest(_ParserBundle(), resolver=_Resolver())


def test_runtime_manifest_detects_tampering():
    manifest = _manifest()
    assert validate_patrol_runtime_manifest(manifest)["digest"] == manifest["digest"]

    manifest["rule_assets"]["change_policies"] = {}
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_patrol_runtime_manifest(manifest)


def test_change_detector_receives_manifest_policies(monkeypatch):
    captured = {}

    def detect(**kwargs):
        captured.update(kwargs)
        return {"success": True, "changes": []}

    monkeypatch.setattr(
        change_detector,
        "detect_observation_changes_against_history",
        detect,
    )

    result = change_detector.detect_changes_from_params(
        {
            "current_run_id": "run-1",
            "use_state_observations": True,
            "runtime_asset_manifest": _manifest(),
        }
    )

    assert result["success"] is True
    assert captured["policy_bundle"][0]["version_id"] == "policy-version-1"


def test_event_builder_uses_manifest_rules_without_runtime_reload(monkeypatch):
    monkeypatch.setattr(
        event_builder.EventRuleRepository,
        "snapshot_active_rules",
        lambda _self: (_ for _ in ()).throw(AssertionError("must use frozen rules")),
    )
    result = event_builder.build_events_from_params(
        {
            "run_id": "run-1",
            "changes": [
                {
                    "change_id": "change-1",
                    "run_id": "run-1",
                    "device_id": "device-1",
                    "entity_type": "interface_l2",
                    "entity_key": "GigabitEthernet1/0/1",
                    "field": "status",
                    "old": "up",
                    "new": "down",
                    "change_type": "modified",
                }
            ],
            "persist": False,
            "runtime_asset_manifest": _manifest(),
        }
    )

    assert result["event_count"] == 1
    assert result["events"][0]["event_type"] == "InterfaceDown"
