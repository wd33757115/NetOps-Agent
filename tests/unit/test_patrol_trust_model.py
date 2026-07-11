from __future__ import annotations

from types import SimpleNamespace

from src.core.patrol.change_detector import detect_observation_changes_against_history
from src.core.patrol.event_rules import EventRule, load_event_rules, match_event_rules
from src.core.state.normalizer import observations_from_snapshot


def test_observation_semantics_are_first_class(monkeypatch):
    monkeypatch.setattr(
        "src.core.state.normalizer._template_contract",
        lambda _template_id: {
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "state_semantics": {
                "protocol": {
                    "semantic_type": "state",
                    "observation_type": "interface.oper_status",
                    "unit": "state",
                }
            },
        },
    )
    observations = observations_from_snapshot(
        {
            "id": "snapshot-1",
            "run_id": "run-1",
            "device_id": "external-sw1",
            "inventory_device_id": "inventory-sw1",
            "canonical_command": "show ip interface brief",
            "structured_json": [{"interface": "Gi1/0/1", "protocol": "up"}],
            "observed_at": "2026-07-10T01:00:00+00:00",
            "parser_template_id": "template-1",
        },
        mode="production",
        source="device-patrol",
        dataset_id="dataset-1",
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.device_id == "inventory-sw1"
    assert observation.observation_type == "interface.oper_status"
    assert observation.semantic_type == "state"
    assert observation.unit == "state"


def test_complete_empty_collection_creates_missing_then_confirmed_absence(monkeypatch):
    prior = {
        "id": "obs-prior",
        "run_id": "run-prior",
        "snapshot_id": "snapshot-prior",
        "mode": "production",
        "source": "device-patrol",
        "source_type": "cli",
        "device_id": "inventory-sw1",
        "canonical_command": "show ip interface brief",
        "entity_type": "interface_l3",
        "entity_key": '["Gi1/0/1"]',
        "field_name": "protocol",
        "observation_type": "interface.oper_status",
        "value_json": "up",
        "value_number": None,
        "observation_kind": "state",
        "observed_at": "2026-07-10T01:00:00+00:00",
        "confidence": 1.0,
        "metadata_json": {},
    }
    recorded = []
    persisted_batches = []

    class FakeStateRepository:
        def list_run_observations(self, run_id, *, device_ids=None):
            return []

        def list_prior_observations_for_run(self, run_id):
            return [prior]

        def build_absence_observation(self, previous, **kwargs):
            recorded.append(kwargs)
            presence = "absent" if kwargs["missing_count"] >= 2 else "missing_candidate"
            data = {
                **previous,
                "id": f"tombstone-{kwargs['missing_count']}",
                "run_id": kwargs["run_id"],
                "snapshot_id": kwargs["snapshot_id"],
                "value_json": None,
                "value_number": None,
                "observation_kind": "tombstone",
                "observed_at": kwargs["observed_at"],
                "metadata_json": {
                    "presence_state": presence,
                    "missing_count": kwargs["missing_count"],
                },
            }
            return SimpleNamespace(to_dict=lambda: data)

    class FakePatrolRepository:
        def begin_analysis_attempt(self, *, rule_bundle, **_kwargs):
            return {
                "reused": False,
                "in_progress": False,
                "attempt_id": "analysis-attempt-1",
                "attempt_number": 1,
                "owner_token": "owner-1",
                "rule_bundle": rule_bundle,
                "rule_bundle_digest": "bundle-digest-1",
            }

        def touch_analysis_attempt(self, *_args, **_kwargs):
            return None

        def complete_analysis_attempt(self, *_args, **_kwargs):
            return None

        def fail_analysis_attempt(self, *_args, **_kwargs):
            return None

        def update_analysis_status(self, *args, **kwargs):
            return None

        def get_run(self, run_id):
            return {"run_purpose": "scheduled_patrol"}

        def list_collection_results(self, run_id):
            return [
                {
                    "device_id": "inventory-sw1",
                    "canonical_command": "show ip interface brief",
                    "authoritative_for_deletion": True,
                    "observed_at": "2026-07-10T02:00:00+00:00",
                    "metadata_json": {"snapshot_id": "snapshot-current"},
                }
            ]

    monkeypatch.setattr(
        "src.core.patrol.change_detector.StateRepository", FakeStateRepository
    )
    monkeypatch.setattr(
        "src.core.patrol.change_detector.PatrolRepository", FakePatrolRepository
    )
    monkeypatch.setattr(
        "src.core.patrol.change_detector.save_changes",
        lambda changes, **kwargs: persisted_batches.append(kwargs) or len(changes),
    )

    first = detect_observation_changes_against_history(current_run_id="run-current")
    assert first["missing_observations"] == 1
    assert first["deleted_observations"] == 0
    assert recorded[0]["missing_count"] == 1
    assert len(persisted_batches[0]["derived_observations"]) == 1

    prior["observation_kind"] = "tombstone"
    prior["metadata_json"] = {
        "presence_state": "missing_candidate",
        "missing_count": 1,
    }
    second = detect_observation_changes_against_history(current_run_id="run-next")
    assert second["missing_observations"] == 0
    assert second["deleted_observations"] == 1
    assert recorded[1]["missing_count"] == 2
    assert len(persisted_batches[1]["derived_observations"]) == 1


def test_event_rules_support_multiple_matches_and_explicit_exclusivity(monkeypatch):
    rules = [
        EventRule("specific", 1, "CriticalUplinkDown", "critical", lambda _c: True, priority=200),
        EventRule("generic", 1, "InterfaceDown", "major", lambda _c: True, priority=100),
    ]
    monkeypatch.setattr("src.core.patrol.event_rules.load_event_rules", lambda: rules)
    assert [rule.rule_id for rule in match_event_rules({})] == ["specific", "generic"]

    rules[0] = EventRule(
        "specific", 1, "CriticalUplinkDown", "critical", lambda _c: True,
        priority=200, exclusive=True,
    )
    assert [rule.rule_id for rule in match_event_rules({})] == ["specific"]


def test_event_rule_runtime_is_fail_closed_without_explicit_builtin(monkeypatch):
    class EmptyRepository:
        def list_active_rules(self):
            return []

    monkeypatch.setattr(
        "src.core.patrol.event_rules.EventRuleRepository", EmptyRepository
    )
    assert load_event_rules(allow_builtin_fallback=False) == []
    assert load_event_rules(allow_builtin_fallback=True)
