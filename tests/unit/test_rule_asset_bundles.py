from __future__ import annotations

import json
from types import SimpleNamespace

from src.core.rule_control.bundles import RuleAssetBundleService


def _bundle_row(child_ids: list[str]):
    return SimpleNamespace(
        id="bundle-1",
        status="pending_approval",
        source_ref="unit:bundle",
        payload_json={
            "child_job_ids": child_ids,
            "observation_type": "interface.oper_status",
            "command": "display interface brief",
        },
        validation_report_json={
            "success": True,
            "governance": {
                "engineering_review": {
                    "decision": "accepted",
                    "actor": "rule-engineer",
                    "reason": "validated candidate semantics",
                }
            },
            "bundle_gate": {"success": True, "blockers": []},
            "context_gate": {
                "success": True,
                "mode": "immutable_dataset_context",
                "context_id": "context-1",
                "context_digest": "context-digest-1",
                "replay_run_ids": ["run-a"],
                "privacy": {
                    "success": True,
                    "raw_cli_included": False,
                    "scope_type": "offline_period_authoritative_runs",
                    "source_snapshot_count": 6,
                    "candidate_record_count": 548,
                    "sampled_record_count": 354,
                },
                "records": [{"raw_text": "must never leave the service"}],
            },
            "historical_replay": {
                "success": True,
                "collision_count": 0,
                "sampled_change_count": 548,
                "matched_change_count": 6,
                "event_count": 6,
                "event_types": {
                    "transceiver_rx_power_low": 4,
                    "transceiver_rx_power_low_recovery": 2,
                },
                "matched_entity_types": {"transceiver_diagnostic": 6},
                "top_devices": {"device-a": 4, "device-b": 2},
                "replacement_rule_ids": ["old-rx-low", "old-rx-recovery"],
                "replacement_logical_keys": ["interface.optical.rx_power:low"],
                "scope": {
                    "mode": "run_ids",
                    "run_ids": ["run-a"],
                    "run_id_count": 1,
                },
            },
        },
        created_by="pytest",
        created_at=None,
        updated_at=None,
        completed_at=None,
    )


def _child(child_id: str, asset_type: str):
    return SimpleNamespace(
        id=child_id,
        job_type=asset_type,
        status="pending_approval",
        asset_type=asset_type,
        asset_id=f"asset-{child_id}",
        asset_version_id=f"version-{child_id}",
        asset_digest=f"digest-{child_id}",
        validation_report_json={"success": True},
    )


def _asset_states(children: dict[str, object]):
    return {
        child_id: {
            "exists": True,
            "status": "draft",
            "digest_matches": True,
        }
        for child_id in children
    }


def test_bundle_read_model_blocks_stale_report_when_children_are_missing():
    result = RuleAssetBundleService._serialize(
        _bundle_row(["contract", "policy", "rule"]),
        {},
    )

    assert result["release_ready"] is False
    assert "bundle child jobs are incomplete" in result["blockers"]
    assert result["steps"][-1]["status"] == "blocked"


def test_bundle_read_model_marks_current_complete_bundle_release_ready():
    children = {
        "contract": _child("contract", "OBSERVATION_CONTRACT"),
        "policy": _child("policy", "CHANGE_POLICY"),
        "rule": _child("rule", "EVENT_RULE"),
    }

    result = RuleAssetBundleService._serialize(
        _bundle_row(list(children)),
        children,
        _asset_states(children),
    )

    assert result["release_ready"] is True
    assert result["blockers"] == []
    assert result["asset_counts"] == {
        "OBSERVATION_CONTRACT": 1,
        "CHANGE_POLICY": 1,
        "EVENT_RULE": 1,
    }
    assert result["steps"][-1]["status"] == "ready"
    assert result["context_gate"] == {
        "success": True,
        "mode": "immutable_dataset_context",
        "context_id": "context-1",
        "context_digest": "context-digest-1",
        "errors": [],
        "replay_run_id_count": 1,
        "privacy": {
            "success": True,
            "raw_cli_included": False,
            "scope_type": "offline_period_authoritative_runs",
            "source_snapshot_count": 6,
            "candidate_record_count": 548,
            "sampled_record_count": 354,
        },
    }
    assert result["replay"]["event_types"] == {
        "transceiver_rx_power_low": 4,
        "transceiver_rx_power_low_recovery": 2,
    }
    assert result["replay"]["replacement_rule_ids"] == [
        "old-rx-low",
        "old-rx-recovery",
    ]
    assert "raw_text" not in json.dumps(result)


def test_bundle_read_model_exposes_immutable_offline_context_scope():
    children = {
        "contract": _child("contract", "OBSERVATION_CONTRACT"),
        "policy": _child("policy", "CHANGE_POLICY"),
        "rule": _child("rule", "EVENT_RULE"),
    }
    context = SimpleNamespace(
        offline_job_id="offline-q2-q3",
        dataset_ids_json=["dataset-q2", "dataset-q3"],
    )

    result = RuleAssetBundleService._serialize(
        _bundle_row(list(children)),
        children,
        _asset_states(children),
        {
            "context-1": {
                "record": context,
                "offline_job_ids": ["offline-q2-q3"],
            }
        },
    )

    assert result["offline_job_id"] == "offline-q2-q3"
    assert result["offline_job_ids"] == ["offline-q2-q3"]
    assert result["dataset_ids"] == ["dataset-q2", "dataset-q3"]


def test_bundle_read_model_blocks_when_persisted_asset_is_missing():
    children = {
        "contract": _child("contract", "OBSERVATION_CONTRACT"),
        "policy": _child("policy", "CHANGE_POLICY"),
        "rule": _child("rule", "EVENT_RULE"),
    }
    states = _asset_states(children)
    states["policy"] = {
        "exists": False,
        "status": None,
        "digest_matches": False,
    }

    result = RuleAssetBundleService._serialize(
        _bundle_row(list(children)),
        children,
        states,
    )

    assert result["release_ready"] is False
    assert "missing, mutated, invalid or not releasable" in result["blockers"][-1]


def test_bundle_read_model_blocks_replay_from_another_context():
    children = {
        "contract": _child("contract", "OBSERVATION_CONTRACT"),
        "policy": _child("policy", "CHANGE_POLICY"),
        "rule": _child("rule", "EVENT_RULE"),
    }
    row = _bundle_row(list(children))
    row.validation_report_json["historical_replay"]["scope"] = {
        "mode": "run_ids",
        "run_ids": ["run-b"],
        "run_id_count": 1,
    }

    result = RuleAssetBundleService._serialize(
        row,
        children,
        _asset_states(children),
    )

    assert result["release_ready"] is False
    assert "historical replay run_ids do not match immutable context" in result["blockers"]
    assert next(step for step in result["steps"] if step["key"] == "replay")["status"] == "blocked"


def test_bundle_read_model_requires_engineering_review_before_release():
    children = {
        "contract": _child("contract", "OBSERVATION_CONTRACT"),
        "policy": _child("policy", "CHANGE_POLICY"),
        "rule": _child("rule", "EVENT_RULE"),
    }
    row = _bundle_row(list(children))
    row.validation_report_json.pop("governance")

    result = RuleAssetBundleService._serialize(
        row,
        children,
        _asset_states(children),
    )

    assert result["release_ready"] is False
    assert "engineering review has not accepted the generated candidates" in result["blockers"]
    assert next(step for step in result["steps"] if step["key"] == "review")["status"] == "ready"


def test_bundle_read_model_blocks_successful_but_empty_historical_replay():
    children = {
        "contract": _child("contract", "OBSERVATION_CONTRACT"),
        "policy": _child("policy", "CHANGE_POLICY"),
        "rule": _child("rule", "EVENT_RULE"),
    }
    row = _bundle_row(list(children))
    row.validation_report_json["historical_replay"].update(
        sampled_change_count=0,
        total_change_count=0,
        matched_change_count=0,
        scope={
            "mode": "run_ids",
            "run_ids": ["run-a"],
            "run_id_count": 1,
            "sampled_run_ids": [],
            "sampled_run_id_count": 0,
            "missing_run_ids": ["run-a"],
        },
    )

    result = RuleAssetBundleService._serialize(
        row,
        children,
        _asset_states(children),
    )

    assert result["release_ready"] is False
    assert result["replay_gate"]["evidence_success"] is False
    assert "historical replay contains no Change evidence" in result["blockers"]
    assert next(step for step in result["steps"] if step["key"] == "replay")["status"] == "blocked"
