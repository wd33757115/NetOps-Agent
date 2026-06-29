from __future__ import annotations

import pytest

from src.core.patrol.change_detector import detect_observation_changes_against_history
from src.core.patrol.change_detector import detect_changes_from_params
from src.core.state import normalizer
from src.core.state.normalizer import normalize_snapshot, observations_from_snapshot


def test_template_contract_prefers_observation_contract(monkeypatch):
    class FakeContractRepository:
        def get_active_validation_dict(self, *, source_type, schema_id):
            assert source_type == "cli"
            assert schema_id == "template-1"
            return {
                "contract_id": "contract-1",
                "source_type": "cli",
                "schema_id": "template-1",
                "entity_type": "interface_l3",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "status", "protocol"],
                "ignore_fields": ["description"],
                "contract_version": 7,
            }

    class ParserRepositoryShouldNotBeUsed:
        def get_template(self, template_id):
            raise AssertionError("ObservationContract should be resolved first")

    monkeypatch.setattr(
        normalizer,
        "ObservationContractRepository",
        FakeContractRepository,
    )
    monkeypatch.setattr(
        normalizer,
        "ParserRepository",
        ParserRepositoryShouldNotBeUsed,
    )

    contract = normalizer._template_contract("template-1")

    assert contract["contract_id"] == "contract-1"
    assert contract["entity_type"] == "interface_l3"
    assert contract["primary_keys"] == ["interface"]


def test_observations_from_snapshot_uses_template_contract(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda template_id: {
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "ignore_fields": ["description"],
            "contract_version": 3,
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-1",
            "run_id": "run-1",
            "device_id": "SW1",
            "vendor": "Cisco",
            "model": "N9K-C9364C",
            "canonical_command": "show ip interface brief",
            "parser_template_id": "template-1",
            "parser_version": "v1",
            "contract_version": 3,
            "source_type": "cli",
            "evidence_id": "ev-cli-test",
            "schema_id": "template-1",
            "observed_at": "2026-06-20T10:00:00+00:00",
            "structured_json": [
                {
                    "interface": "Eth1/1",
                    "status": "up",
                    "protocol": "down",
                    "description": "server",
                }
            ],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    assert [item.field_name for item in observations] == ["protocol", "status"]
    assert observations[0].entity_type == "interface_l3"
    assert observations[0].entity_key == '["Eth1/1"]'
    assert observations[0].contract_version == 3
    assert observations[0].source_type == "cli"
    assert observations[0].evidence_id == "ev-cli-test"
    assert observations[0].schema_id == "template-1"
    assert observations[0].metadata["evidence_id"] == "ev-cli-test"


def test_observations_from_snapshot_skips_interface_header_artifact(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda template_id: {
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "contract_version": 3,
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-1",
            "run_id": "run-1",
            "device_id": "SW1",
            "vendor": "H3C",
            "model": "S6860-54HF",
            "canonical_command": "display interface brief",
            "parser_template_id": "template-1",
            "parser_version": "v1",
            "observed_at": "2026-06-20T10:00:00+00:00",
            "structured_json": [
                {
                    "interface": "Interface",
                    "status": "Link",
                    "protocol": "Protocol",
                    "description": "IP      Description",
                },
                {
                    "interface": "Vlan1",
                    "status": "UP",
                    "protocol": "UP",
                    "description": "",
                },
            ],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    assert {item.entity_key for item in observations} == {'["Vlan1"]'}


def test_observations_from_snapshot_applies_default_state_normalization(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda template_id: {
            "entity_type": "device",
            "primary_keys": [],
            "contract_version": 1,
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-1",
            "run_id": "run-1",
            "device_id": "SW1",
            "vendor": "Cisco",
            "model": "N9K-C9364C",
            "canonical_command": "show version",
            "parser_template_id": "template-1",
            "parser_version": "v1",
            "contract_version": 1,
            "observed_at": "2026-06-20T10:00:00+00:00",
            "structured_json": {
                "model": "N9K-C9364C",
                "uptime": "174 day(s)",
                "description": "--",
            },
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    by_field = {item.field_name: item for item in observations}
    assert "uptime" not in by_field
    assert by_field["description"].value == ""


def test_normalize_snapshot_rejects_validation_current_state(monkeypatch):
    class FakeRepository:
        def get_snapshot_context(self, snapshot_id):
            return {
                "mode": "parser_validation",
                "source": "test",
                "dataset_id": "dataset-1",
                "snapshot": {
                    "id": snapshot_id,
                    "run_id": "run-1",
                    "device_id": "SW1",
                    "vendor": "Cisco",
                    "model": "N9K-C9364C",
                    "canonical_command": "show version",
                    "parser_template_id": None,
                    "parser_version": None,
                    "contract_version": None,
                    "observed_at": "2026-06-20T10:00:00+00:00",
                    "structured_json": {"model": "N9K-C9364C"},
                },
            }

    monkeypatch.setattr(normalizer, "StateRepository", FakeRepository)

    with pytest.raises(ValueError, match="parser_validation"):
        normalize_snapshot("snapshot-1", update_current_state=True)


def test_observation_change_detector_uses_observation_ids(monkeypatch):
    class FakeRepository:
        def list_run_observations(self, run_id):
            return [
                {
                    "id": "obs-current",
                    "run_id": run_id,
                    "snapshot_id": "snapshot-current",
                    "mode": "production",
                    "device_id": "SW1",
                    "canonical_command": "show ip interface brief",
                    "entity_type": "interface_l3",
                    "entity_key": '["Eth1/1"]',
                    "field_name": "protocol",
                    "value_json": "down",
                    "value_number": None,
                    "observed_at": "2026-06-20T10:00:00+00:00",
                    "confidence": 1.0,
                },
                {
                    "id": "obs-added",
                    "run_id": run_id,
                    "snapshot_id": "snapshot-current",
                    "mode": "production",
                    "device_id": "SW1",
                    "canonical_command": "show ip interface brief",
                    "entity_type": "interface_l3",
                    "entity_key": '["Eth1/2"]',
                    "field_name": "protocol",
                    "value_json": "up",
                    "value_number": None,
                    "observed_at": "2026-06-20T10:00:00+00:00",
                    "confidence": 1.0,
                },
                {
                    "id": "obs-new-command",
                    "run_id": run_id,
                    "snapshot_id": "snapshot-current",
                    "mode": "production",
                    "device_id": "SW1",
                    "canonical_command": "show processes memory",
                    "entity_type": "memory",
                    "entity_key": '["slot1"]',
                    "field_name": "usage",
                    "value_json": 25,
                    "value_number": 25.0,
                    "observed_at": "2026-06-20T10:00:00+00:00",
                    "confidence": 1.0,
                },
            ]

        def find_previous_observation(self, observation):
            raise AssertionError("state change detection should use batched prior observations")

        def list_prior_observations_for_run(self, run_id):
            return [
                {
                    "id": "obs-previous",
                    "run_id": "run-previous",
                    "snapshot_id": "snapshot-previous",
                    "mode": "production",
                    "device_id": "SW1",
                    "canonical_command": "show ip interface brief",
                    "entity_type": "interface_l3",
                    "entity_key": '["Eth1/1"]',
                    "field_name": "protocol",
                    "value_json": "up",
                    "value_number": None,
                    "observed_at": "2026-06-20T09:00:00+00:00",
                    "confidence": 1.0,
                },
                {
                    "id": "obs-deleted",
                    "run_id": "run-previous",
                    "snapshot_id": "snapshot-previous",
                    "mode": "production",
                    "device_id": "SW1",
                    "canonical_command": "show ip interface brief",
                    "entity_type": "interface_l3",
                    "entity_key": '["Eth1/3"]',
                    "field_name": "protocol",
                    "value_json": "up",
                    "value_number": None,
                    "observed_at": "2026-06-20T09:00:00+00:00",
                    "confidence": 1.0,
                },
                {
                    "id": "obs-command-not-collected",
                    "run_id": "run-previous",
                    "snapshot_id": "snapshot-previous",
                    "mode": "production",
                    "device_id": "SW1",
                    "canonical_command": "show processes cpu",
                    "entity_type": "cpu",
                    "entity_key": '["slot1"]',
                    "field_name": "cpu_5s",
                    "value_json": 15,
                    "value_number": 15.0,
                    "observed_at": "2026-06-20T09:00:00+00:00",
                    "confidence": 1.0,
                },
            ]

    saved = {}
    def fake_save_changes(changes):
        saved["changes"] = changes
        return len(changes)

    monkeypatch.setattr(
        "src.core.patrol.change_detector.StateRepository", FakeRepository
    )
    monkeypatch.setattr(
        "src.core.patrol.change_detector.save_changes",
        fake_save_changes,
    )

    result = detect_observation_changes_against_history(
        current_run_id="run-current",
        persist=True,
    )

    assert result["change_count"] == 3
    assert result["added_observations"] == 1
    assert result["deleted_observations"] == 1
    assert all(
        change["previous_observation_id"] != "obs-command-not-collected"
        for change in result["changes"]
    )
    assert all(
        change["current_observation_id"] != "obs-new-command"
        for change in result["changes"]
    )
    change = result["changes"][0]
    assert change["source"] == "state_observation"
    assert change["previous_observation_id"] == "obs-previous"
    assert change["current_observation_id"] == "obs-current"
    assert saved["changes"][0].old_value == "up"


def test_observation_change_detector_accepts_explicit_previous_run(monkeypatch):
    calls = {}

    class FakeRepository:
        def list_run_observations(self, run_id):
            return [
                {
                    "id": "obs-current",
                    "run_id": run_id,
                    "snapshot_id": "snapshot-current",
                    "mode": "parser_validation",
                    "device_id": "SW1",
                    "canonical_command": "show version",
                    "entity_type": "device",
                    "entity_key": "__singleton__",
                    "field_name": "model",
                    "value_json": "new-model",
                    "value_number": None,
                    "observed_at": "2026-06-20T10:00:00+00:00",
                    "confidence": 1.0,
                }
            ]

        def list_observations_for_runs(self, run_ids):
            calls["run_ids"] = run_ids
            return [
                {
                    "id": "obs-previous",
                    "run_id": run_ids[0],
                    "snapshot_id": "snapshot-previous",
                    "mode": "parser_validation",
                    "device_id": "SW1",
                    "canonical_command": "show version",
                    "entity_type": "device",
                    "entity_key": "__singleton__",
                    "field_name": "model",
                    "value_json": "old-model",
                    "value_number": None,
                    "observed_at": "2026-06-20T09:00:00+00:00",
                    "confidence": 1.0,
                }
            ]

        def list_prior_observations_for_run(self, run_id):
            raise AssertionError("explicit previous_run_id should not scan all history")

    monkeypatch.setattr(
        "src.core.patrol.change_detector.StateRepository", FakeRepository
    )
    monkeypatch.setattr(
        "src.core.patrol.change_detector.save_changes",
        lambda changes: len(changes),
    )

    result = detect_observation_changes_against_history(
        current_run_id="run-current",
        previous_run_id="run-previous",
        persist=True,
    )

    assert calls["run_ids"] == ["run-previous"]
    assert result["previous_run_id"] == "run-previous"
    assert result["change_count"] == 1
    assert result["changes"][0]["old"] == "old-model"
    assert result["changes"][0]["new"] == "new-model"


def test_state_observation_mode_takes_precedence_over_snapshot_run_diff(monkeypatch):
    calls = {}

    def fake_observation_diff(*, current_run_id, previous_run_id=None, persist=True):
        calls["observation"] = (current_run_id, previous_run_id, persist)
        return {"success": True, "change_count": 0, "changes": []}

    class FakePatrolRepository:
        def list_run_snapshots(self, *args, **kwargs):
            raise AssertionError("state observation mode must not list snapshots")

    monkeypatch.setattr(
        "src.core.patrol.change_detector.detect_observation_changes_against_history",
        fake_observation_diff,
    )
    monkeypatch.setattr(
        "src.core.patrol.change_detector.PatrolRepository",
        FakePatrolRepository,
    )

    result = detect_changes_from_params(
        {
            "previous_run_id": "run-previous",
            "current_run_id": "run-current",
            "use_state_observations": True,
            "persist": False,
        }
    )

    assert result["success"] is True
    assert calls["observation"] == ("run-current", "run-previous", False)
