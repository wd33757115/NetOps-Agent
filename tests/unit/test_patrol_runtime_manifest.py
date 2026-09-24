from __future__ import annotations

import hashlib
import json

import pytest

from src.core.patrol import change_detector, event_builder
from src.core.patrol.runtime_manifest import (
    comparison_scopes_for_device,
    first_observation_collection_action_ids,
    resolve_patrol_runtime_rule_assets,
    validate_patrol_runtime_manifest,
)
from types import SimpleNamespace as RuntimeRuleAssetBundle


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
    # Existing serialized reports remain verifiable without a legacy DB resolver.
    from src.core.patrol.runtime_manifest import _digest, _manifest_payload
    manifest = {"version": 1, "parser_bundle": _ParserBundle().to_dict(),
                "rule_assets": vars(_Resolver().load_bundle(observation_schemas=[("cli", "template-1")])), "readiness": {}}
    manifest["digest"] = _digest(_manifest_payload(manifest))
    return manifest


def test_runtime_manifest_detects_tampering():
    manifest = _manifest()
    assert validate_patrol_runtime_manifest(manifest)["digest"] == manifest["digest"]

    manifest["rule_assets"]["change_policies"] = {}
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_patrol_runtime_manifest(manifest)


def _check_item_manifest() -> dict:
    payload = {
        "schema_version": 2,
        "patrol_batch_id": "batch-1",
        "runtime_bundle_refs": [
            {
                "bundle_id": "bundle-1",
                "bundle_version_id": "bundle-version-1",
                "digest": "bundle-digest-1",
                "device_id": "device-1",
                "check_item_version_id": "check-version-1",
                "implementation_version_id": "implementation-version-1",
                "entity_identity_policy_version_id": "identity-version-1",
            }
        ],
        "comparison_contexts": [
            {
                "id": "context-1",
                "digest": "context-digest-1",
                "mode": "no_baseline",
                "comparison_status": "first_observation",
                "device_id": "device-1",
                "check_item_version_id": "check-version-1",
                "baseline_run_id": None,
                "baseline_snapshot_ids": [],
                "selection_reason": "first_observation",
            }
        ],
        "decisions": [
            {
                "device_id": "device-1",
                "check_item_id": "system.cpu_usage",
                "check_item_version_id": "check-version-1",
                "execution_id": "execution-1",
                "comparison_context_id": "context-1",
                "comparison_context_digest": "context-digest-1",
                "collection_action_ids": ["action-1"],
            }
        ],
        "collection_actions": [
            {
                "id": "action-1",
                "device_id": "device-1",
                "bindings": [
                    {"check_item_version_id": "check-version-1"}
                ],
            }
        ],
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
    return {
        **payload,
        "digest": digest,
        "manifest_id": "manifest-1",
        "manifest_kind": "check_item_runtime",
    }


class _CheckItemBundleService:
    def get_rule_assets(self, version_id):
        assert version_id == "bundle-version-1"
        return {
            "bundle": {
                "bundle_id": "bundle-1",
                "version_id": "bundle-version-1",
                "digest": "bundle-digest-1",
                "check_item_version_id": "check-version-1",
                "implementation_version_id": "implementation-version-1",
                "entity_identity_policy_version_id": "identity-version-1",
            },
            "change_policies": [
                {"version_id": "policy-version-1", "observation_type": "cpu"}
            ],
            "event_rules": [
                {"version_id": "rule-version-1", "event_type": "CpuHigh"}
            ],
        }


def test_check_item_manifest_resolves_exact_frozen_rule_assets():
    manifest = _check_item_manifest()

    assert validate_patrol_runtime_manifest(manifest)["digest"] == manifest["digest"]
    assets = resolve_patrol_runtime_rule_assets(
        manifest,
        bundle_service=_CheckItemBundleService(),
    )

    assert list(assets["change_policies"]) == ["policy-version-1"]
    assert assets["event_rules"][0]["version_id"] == "rule-version-1"
    assert assets["change_policies_by_check_item_version_id"] == {
        "check-version-1": [
            {
                "version_id": "policy-version-1",
                "observation_type": "cpu",
            }
        ]
    }
    assert assets["event_rules_by_check_item_version_id"] == {
        "check-version-1": [
            {
                "version_id": "rule-version-1",
                "event_type": "CpuHigh",
            }
        ]
    }
    assert assets[
        "change_policies_by_device_and_check_item_version_id"
    ] == {
        "device-1": {
            "check-version-1": [
                {
                    "version_id": "policy-version-1",
                    "observation_type": "cpu",
                }
            ]
        }
    }
    assert assets[
        "event_rules_by_device_and_check_item_version_id"
    ] == {
        "device-1": {
            "check-version-1": [
                {
                    "version_id": "rule-version-1",
                    "event_type": "CpuHigh",
                }
            ]
        }
    }
    assert first_observation_collection_action_ids(
        manifest,
        device_id="device-1",
    ) == {"action-1"}
    assert comparison_scopes_for_device(
        manifest,
        device_id="device-1",
    ) == [
        {
            "device_id": "device-1",
            "check_item_id": "system.cpu_usage",
            "check_item_version_id": "check-version-1",
            "check_execution_id": "execution-1",
            "comparison_context_id": "context-1",
            "comparison_context_digest": "context-digest-1",
            "comparison_status": "first_observation",
            "selection_reason": "first_observation",
            "baseline_run_id": None,
            "baseline_snapshot_ids": [],
            "collection_action_ids": ["action-1"],
            "runtime_bundle_version_id": None,
        }
    ]


def test_check_item_manifest_detects_signed_payload_tampering():
    manifest = _check_item_manifest()
    manifest["patrol_batch_id"] = "batch-2"

    with pytest.raises(ValueError, match="digest mismatch"):
        validate_patrol_runtime_manifest(manifest)


def test_check_item_manifest_detects_runtime_bundle_digest_mismatch():
    manifest = _check_item_manifest()
    manifest["runtime_bundle_refs"][0]["digest"] = "different"
    unsigned = {
        key: value
        for key, value in manifest.items()
        if key not in {"digest", "manifest_id", "manifest_kind"}
    }
    manifest["digest"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(ValueError, match="RuntimeBundle digest mismatch"):
        resolve_patrol_runtime_rule_assets(
            manifest,
            bundle_service=_CheckItemBundleService(),
        )


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
            "_analysis_updates_run_status": False,
            "runtime_asset_manifest": _manifest(),
        }
    )

    assert result["event_count"] == 1
    assert result["events"][0]["event_type"] == "InterfaceDown"
