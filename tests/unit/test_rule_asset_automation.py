from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.core.celery_tasks.celery_app import celery
from src.core.rule_control.automation import (
    DEFAULT_AUTOMATION_SETTINGS,
    RuleAssetAutomationService,
    _as_utc,
    build_unattended_intents,
    evaluate_unattended_publish_gate,
    validate_automation_settings,
)


def test_validate_automation_settings_normalizes_and_bounds_values():
    settings = validate_automation_settings(
        {
            "mode": "AUTO_PUBLISH",
            "max_concurrency": 50,
            "max_generation_attempts": 0,
            "min_period_count": 0,
            "allow_critical": 1,
        }
    )

    assert settings["mode"] == "generate_only"
    assert settings["max_concurrency"] == 10
    assert settings["max_generation_attempts"] == 1
    assert settings["min_period_count"] == 2
    assert settings["stale_run_seconds"] == 1800
    assert settings["allow_critical"] is True

    with pytest.raises(ValueError, match="unsupported rule asset automation mode"):
        validate_automation_settings({"mode": "publish_everything"})


def test_naive_postgres_timestamp_uses_configured_storage_timezone():
    normalized = _as_utc(datetime(2026, 8, 10, 5, 35, 37))

    assert normalized == datetime(2026, 8, 9, 21, 35, 37, tzinfo=timezone.utc)


def test_unattended_intents_are_generic_and_evidence_aware():
    intents = build_unattended_intents(
        {
            "field_profiles": {
                "cpu_user_pct": {
                    "numeric_count": 20,
                    "distinct_count": 5,
                },
                "slot": {
                    "numeric_count": 20,
                    "distinct_count": 1,
                },
            }
        }
    )

    assert "cpu_user_pct" in intents["policy_intent"]
    assert "fault rule and a matching recovery rule" in intents["event_intent"]
    assert "software, firmware" in intents["event_intent"]
    assert "Cisco" not in intents["event_intent"]
    assert "H3C" not in intents["event_intent"]


def _bundle_fixture():
    context_gate = {
        "success": True,
        "mode": "immutable_dataset_context",
        "context_id": "context-1",
        "replay_run_ids": ["run-1", "run-2"],
    }
    replay = {
        "success": True,
        "sampled_change_count": 3,
        "total_change_count": 3,
        "matched_change_count": 2,
        "collision_count": 0,
        "estimated_daily_event_count": 12,
        "scope": {
            "mode": "run_ids",
            "run_ids": ["run-1", "run-2"],
            "run_id_count": 2,
            "sampled_run_ids": ["run-1", "run-2"],
            "sampled_run_id_count": 2,
            "missing_run_ids": [],
        },
    }
    bundle = SimpleNamespace(
        id="bundle-1",
        status="pending_approval",
        payload_json={"child_job_ids": ["contract", "policy", "event"]},
        validation_report_json={
            "success": True,
            "bundle_gate": {
                "success": True,
                "asset_counts": {
                    "OBSERVATION_CONTRACT": 1,
                    "CHANGE_POLICY": 1,
                    "EVENT_RULE": 1,
                },
            },
            "context_gate": context_gate,
            "historical_replay": replay,
        },
    )
    context = SimpleNamespace(
        id="context-1",
        offline_job_id="offline-1",
        dataset_ids_json=["q1", "q2"],
        privacy_report_json={
            "success": True,
            "raw_cli_included": False,
            "scope_type": "offline_period_authoritative_runs",
            "sampled_record_count": 30,
        },
    )
    seed = SimpleNamespace(
        id="seed-1",
        payload_json={
            "automation": {
                "managed_by": "rule-asset-unattended",
                "requested_mode": "generate_only",
            }
        },
    )
    unit = SimpleNamespace(device_count=3)
    children = [
        SimpleNamespace(
            id="event",
            job_type="EVENT_RULE",
            payload_json={"rule": {"severity": "warning"}},
        )
    ]
    return bundle, context, seed, unit, children


def test_unattended_publish_gate_requires_strict_offline_replay_evidence():
    bundle, context, seed, unit, children = _bundle_fixture()
    settings = {
        **DEFAULT_AUTOMATION_SETTINGS,
        "mode": "generate_only",
    }

    gate = evaluate_unattended_publish_gate(
        bundle=bundle,
        context=context,
        seed=seed,
        unit=unit,
        settings=settings,
        children=children,
        source_integrity={"success": True},
    )

    assert gate["success"] is True
    assert gate["matched_change_count"] == 2
    assert gate["collision_count"] == 0


def test_unattended_readiness_gate_routes_single_period_to_revision():
    bundle, context, seed, unit, children = _bundle_fixture()
    context.dataset_ids_json = ["q1"]
    children[0].payload_json["rule"]["severity"] = "critical"
    settings = {
        **DEFAULT_AUTOMATION_SETTINGS,
        "mode": "generate_only",
    }

    gate = evaluate_unattended_publish_gate(
        bundle=bundle,
        context=context,
        seed=seed,
        unit=unit,
        settings=settings,
        children=children,
        source_integrity={"success": True},
    )

    assert gate["success"] is False
    assert "insufficient independent patrol periods" in gate["blockers"]
    assert "critical EventRules require human approval" not in gate["blockers"]
    assert gate["publish_authority"] == "human_only"


def test_unattended_readiness_gate_uses_evidence_only_asset_profile():
    bundle, context, seed, unit, _children = _bundle_fixture()
    requirements = {
        "schema_version": "rule-asset-requirements/v1",
        "requires_observation_contract": True,
        "requires_change_policy": False,
        "requires_current_state_rule": False,
        "requires_event_rule": False,
        "requires_event_rule_assets": False,
        "requires_historical_replay": False,
    }
    bundle.payload_json = {
        "child_job_ids": ["contract"],
        "asset_requirements": requirements,
    }
    bundle.validation_report_json["bundle_gate"] = {
        "success": True,
        "asset_counts": {"OBSERVATION_CONTRACT": 1},
        "asset_requirements": requirements,
    }
    bundle.validation_report_json["historical_replay"] = None
    context.dataset_ids_json = ["q1"]
    context.privacy_report_json["sampled_record_count"] = 1
    unit.device_count = 1
    children = [
        SimpleNamespace(
            id="contract",
            job_type="OBSERVATION_CONTRACT",
            payload_json={},
        )
    ]

    gate = evaluate_unattended_publish_gate(
        bundle=bundle,
        context=context,
        seed=seed,
        unit=unit,
        settings={
            **DEFAULT_AUTOMATION_SETTINGS,
            "mode": "generate_only",
        },
        children=children,
        source_integrity={"success": True},
    )

    assert gate["success"] is True
    assert gate["asset_requirements"]["requires_historical_replay"] is False
    assert gate["publish_authority"] == "human_only"


def test_disabled_automation_does_not_dispatch_work():
    recorded = []

    class FakeSettings:
        def get(self):
            return {**DEFAULT_AUTOMATION_SETTINGS, "mode": "disabled"}

        def record_run(self, result):
            recorded.append(result)

    service = RuleAssetAutomationService(settings_repository=FakeSettings())

    result = service.run_once()

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["dispatched_count"] == 0
    assert recorded[0]["reason"] == "rule asset automation is disabled"


def test_evidence_blocked_seed_is_counted_as_human_review(monkeypatch):
    monkeypatch.setattr("src.apps.rule_governance.backend.access.authorize", lambda user_id: None)
    recorded = []

    class FakeSettings:
        def get(self):
            return {**DEFAULT_AUTOMATION_SETTINGS, "mode": "generate_only"}

        def record_run(self, result):
            recorded.append(result)

    service = RuleAssetAutomationService(settings_repository=FakeSettings())
    monkeypatch.setattr(
        service,
        "_advance_pending_bundles",
        lambda **_: {
            "published": [],
            "review_required": [],
            "failed": [],
            "published_count": 0,
        },
    )
    monkeypatch.setattr(
        service,
        "_dispatch_ready_seeds",
        lambda **_: {
            "dispatched": [],
            "review_required": [
                {
                    "seed_id": "seed-1",
                    "reason": "generation evidence gate blocked",
                    "blockers": ["Change/Event 生成需要更多独立采集周期"],
                }
            ],
            "failed": [],
        },
    )

    result = service.run_once()

    assert result["review_required_count"] == 1
    assert result["review_required"][0]["seed_id"] == "seed-1"
    assert "review_required=1" in result["summary"]
    assert recorded[0]["review_required_count"] == 1


def test_celery_beat_schedules_rule_asset_automation_on_app_queue():
    schedule = celery.conf.beat_schedule["app.rule-governance.reconcile-rule-asset-automation"]

    assert schedule["task"] == "reconcile_rule_asset_automation"
    assert schedule["schedule"] == 30.0
    assert schedule["options"]["queue"] == "app.rule-governance"
    assert celery.conf.task_routes["reconcile_rule_asset_automation"]["queue"] == "app.rule-governance"
