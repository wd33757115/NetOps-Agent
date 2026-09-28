from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.observation.semantic_projection import SemanticProjectionError
from src.core.patrol.change_detector import (
    ExplicitBaselineNotReadyError,
    detect_changes_from_params,
    detect_observation_changes_against_history,
)
from src.core.patrol.repository import AnalysisAttemptInProgressError
from src.core.state import normalizer
from src.core.state.domain import StateObservation
from src.core.state.normalizer import (
    _build_snapshot_normalization,
    normalize_run,
    normalize_snapshot,
    observations_from_snapshot,
)
from src.core.state.observation_applicability import APPLICABILITY_CONTEXT_KEY
from src.core.state.repository import StateRepository


class _AnalysisPatrolRepository:
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

    def update_analysis_status(self, *_args, **_kwargs):
        return None

    def get_run(self, _run_id):
        return {"run_purpose": "scheduled_patrol"}

    def list_collection_results(self, _run_id):
        return []


def _install_analysis_runtime(monkeypatch):
    monkeypatch.setattr(
        "src.core.patrol.repository.PatrolRepository",
        _AnalysisPatrolRepository,
    )
    monkeypatch.setattr(
        "src.core.patrol.change_detector.PatrolRepository",
        _AnalysisPatrolRepository,
    )


@pytest.mark.parametrize(
    ("parse_status", "normalization_status", "fact_status", "reason"),
    [
        ("template_missing", "blocked", "blocked_by_parser", "blocked_by_parser"),
        ("parse_failed", "blocked", "blocked_by_parser", "blocked_by_parser"),
        ("identity_unknown", "blocked", "blocked_by_identity", "blocked_by_identity"),
        ("ignored", "skipped", "ignored", "ignored_by_policy"),
        ("not_applicable", "skipped", "not_applicable", "not_applicable"),
        ("empty_unverified", "blocked", "blocked_by_output", "empty_unverified"),
        ("command_error", "blocked", "blocked_by_output", "command_error"),
        ("truncated", "blocked", "blocked_by_output", "truncated"),
        ("paged", "blocked", "blocked_by_output", "paged"),
        ("parse_empty", "empty", "empty", "legitimate_empty"),
    ],
)
def test_normalizer_classifies_zero_observation_snapshots(
    parse_status,
    normalization_status,
    fact_status,
    reason,
):
    class FakeRepository:
        def get_snapshot_context(self, snapshot_id):
            return {
                "mode": "production",
                "source": "test",
                "dataset_id": None,
                "snapshot": {
                    "id": snapshot_id,
                    "run_id": "run-1",
                    "parse_status": parse_status,
                    "structured_json": [] if parse_status == "parse_empty" else None,
                },
            }

    result, observations, _ = _build_snapshot_normalization(
        FakeRepository(),
        "snapshot-1",
        update_current_state=None,
    )

    assert result["success"] is True
    assert result["normalization_status"] == normalization_status
    assert result["fact_status"] == fact_status
    assert result["no_observation_reason"] == reason
    assert observations == []


def test_raw_hash_diff_normalizes_only_the_digest():
    observations = observations_from_snapshot(
        {
            "id": "snapshot-raw-hash",
            "run_id": "run-1",
            "device_id": "SW1",
            "inventory_device_id": None,
            "vendor": "H3C",
            "model": "S6860",
            "canonical_command": "display current-configuration",
            "observed_at": "2026-07-12T00:00:00+00:00",
            "structured_json": [{"raw_text_hash": "a" * 64}],
            "parser_template_id": None,
            "parser_version": None,
            "metadata_json": {
                "handling": "raw_hash_diff",
                "comparison_profile": "configuration",
                "comparison_profile_version": 1,
            },
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    assert len(observations) == 1
    assert observations[0].field_name == "raw_text_hash"
    assert observations[0].entity_key == '["display current-configuration"]'
    assert observations[0].value == "a" * 64
    assert observations[0].parser_template_id is None
    assert observations[0].metadata["unstructured_comparison"] == {
        "profile": "configuration",
        "profile_version": 1,
    }


def test_command_adapter_projects_named_record_set_to_canonical_observation(
    monkeypatch,
):
    implementation = SimpleNamespace(
        id="cisco.nxos.show_system_resources",
        version_id="adapter-v1",
        adapter_spec={
            "schema_version": "command-adapter/v1",
            "parser": {"template_id": "parser-system-resources-v1"},
            "outputs": [
                {
                    "check_item_id": "system.cpu_usage",
                    "source_record_set": "cpu_cores",
                    "fields": {
                        "scope": {"literal": "processor"},
                        "scope_id": "cpu_id",
                        "cpu_usage_percent": {
                            "expression": "100 - cpu_idle_percent"
                        },
                    },
                }
            ],
        },
    )
    check_item = SimpleNamespace(
        id="system.cpu_usage",
        version=8,
        version_id="cpu-v8",
        digest="a" * 64,
        canonical_contract={
            "entity_type": "cpu_scope",
            "identity_dimensions": ["scope", "scope_id"],
            "observations": [
                {
                    "canonical_field": "cpu_usage_percent",
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "unit": "percent",
                }
            ],
        },
    )
    monkeypatch.setattr(
        normalizer.CheckItemCatalogRepository,
        "get_implementation_version_context",
        lambda _self, _version_id: (implementation, (check_item,)),
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-adapter",
            "run_id": "run-1",
            "device_id": "switch-1",
            "inventory_device_id": "inventory-1",
            "vendor": "Cisco",
            "model": "N9K",
            "canonical_command": "show system resources",
            "observed_at": "2026-08-10T00:00:00+00:00",
            "structured_json": {
                "format": "netops.structured_record_sets/v1",
                "record_sets": {
                    "cpu_cores": [
                        {"cpu_id": "0", "cpu_idle_percent": "92.5"}
                    ]
                },
            },
            "implementation_version_id": "adapter-v1",
            "parser_template_id": "parser-system-resources-v1",
            "parser_version": "1",
            "raw_text_hash": "b" * 64,
            "metadata_json": {},
        },
        mode="production",
        source="test",
        dataset_id="dataset-1",
    )

    assert len(observations) == 1
    assert observations[0].field_name == "cpu_usage_percent"
    assert observations[0].value == 7.5
    assert observations[0].entity_key == '["processor","0"]'
    assert observations[0].metadata["check_item_id"] == "system.cpu_usage"
    assert observations[0].metadata["command_adapter_version_id"] == "adapter-v1"


def test_device_level_projection_deduplicates_repeated_parser_records():
    observations = observations_from_snapshot(
        {
            "id": "snapshot-version-repeated",
            "run_id": "run-1",
            "device_id": "router-1",
            "vendor": "H3C",
            "model": "MSR5620",
            "canonical_command": "display version",
            "observed_at": "2026-07-28T00:00:00+00:00",
            "structured_json": [
                {"software_version": "7.1.064", "slot": "0"},
                {"software_version": "7.1.064", "slot": "1"},
            ],
            "parser_template_id": "parser-version",
            "parser_version": "1",
            "metadata_json": {},
        },
        mode="production",
        source="test",
        dataset_id=None,
        _contract_override={
            "entity_type": "device",
            "primary_keys": [],
            "ignore_fields": ["slot"],
            "state_semantics": {
                "software_version": {
                    "observation_type": "device.software.version",
                    "semantic_type": "state",
                }
            },
        },
    )

    assert len(observations) == 1
    assert observations[0].value == "7.1.064"
    assert observations[0].entity_key == "__singleton__"


def test_device_level_projection_blocks_conflicting_same_snapshot_values():
    with pytest.raises(
        SemanticProjectionError,
        match="conflicting canonical values",
    ):
        observations_from_snapshot(
            {
                "id": "snapshot-version-conflict",
                "run_id": "run-1",
                "device_id": "router-1",
                "vendor": "H3C",
                "model": "MSR5620",
                "canonical_command": "display version",
                "observed_at": "2026-07-28T00:00:00+00:00",
                "structured_json": [
                    {"software_version": "7.1.064", "slot": "0"},
                    {"software_version": "7.1.065", "slot": "1"},
                ],
                "parser_template_id": "parser-version",
                "parser_version": "1",
                "metadata_json": {},
            },
            mode="production",
            source="test",
            dataset_id=None,
            _contract_override={
                "entity_type": "device",
                "primary_keys": [],
                "ignore_fields": ["slot"],
                "state_semantics": {
                    "software_version": {
                        "observation_type": "device.software.version",
                        "semantic_type": "state",
                    }
                },
            },
        )


def test_optical_observation_carries_authoritative_device_threshold_context(
    monkeypatch,
):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda *_args, **_kwargs: {
            "contract_id": "contract-optical",
            "contract_sha256": "a" * 64,
            "entity_type": "interface_optical",
            "primary_keys": ["interface"],
            "contract_version": 1,
            "state_semantics": {
                "rx_power_dbm": {
                    "semantic_type": "gauge",
                    "unit": "dbm",
                    "observation_type": "interface.optical.rx_power",
                    "context_fields": [
                        "rx_low_warning_dbm",
                        "rx_low_alarm_dbm",
                    ],
                    "threshold_references": {
                        "low": {
                            "field_name": "rx_low_warning_dbm",
                            "context_key": "threshold.low",
                        },
                        "critical_low": {
                            "field_name": "rx_low_alarm_dbm",
                            "context_key": "threshold.critical_low",
                        },
                    },
                }
            },
            "validators": {
                "rx_power_dbm": {"type": "number"},
                "rx_low_warning_dbm": {"type": "number"},
                "rx_low_alarm_dbm": {"type": "number"},
            },
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-optical",
            "run_id": "run-1",
            "device_id": "SW1",
            "vendor": "H3C",
            "model": "S5130S-54S-HI-G",
            "canonical_command": "display transceiver diagnosis interface",
            "parser_template_id": "template-optical",
            "parser_version": "1",
            "observed_at": "2026-07-18T00:00:00+00:00",
            "structured_json": [
                {
                    "interface": "Ten-GigabitEthernet1/0/49",
                    "rx_power_dbm": "-15.50",
                    "rx_low_warning_dbm": "-15.00",
                    "rx_low_alarm_dbm": "-18.01",
                }
            ],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    rx = next(item for item in observations if item.field_name == "rx_power_dbm")
    context = rx.metadata[APPLICABILITY_CONTEXT_KEY]
    assert context["rx_low_warning_dbm"] == -15.0
    assert context["rx_low_alarm_dbm"] == -18.01
    assert context["threshold.low"] == -15.0
    assert context["threshold.critical_low"] == -18.01
    assert context["threshold_source"] == "device_output"
    assert context["threshold_confidence"] == "authoritative"
    assert context["threshold_fields"] == [
        "threshold.critical_low",
        "threshold.low",
    ]


def test_observations_from_snapshot_skip_blank_textfsm_fields(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda *_args, **_kwargs: {
            "entity_type": "interface_optical",
            "primary_keys": ["interface"],
            "contract_version": 1,
            "state_semantics": {
                "rx_power_dbm": {
                    "semantic_type": "gauge",
                    "unit": "dbm",
                    "observation_type": "interface.optical.rx_power",
                },
                "transceiver_present": {
                    "semantic_type": "state",
                    "observation_type": "interface.optical.presence",
                },
            },
            "validators": {
                "rx_power_dbm": {"type": "number"},
                "transceiver_present": {
                    "type": "enum",
                    "values": ["present", "absent"],
                },
            },
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-optical-blanks",
            "run_id": "run-1",
            "device_id": "SW1",
            "vendor": "H3C",
            "model": "S5130S-54S-HI-G",
            "canonical_command": "display transceiver diagnosis interface",
            "parser_template_id": "template-optical",
            "parser_version": "1",
            "observed_at": "2026-07-18T00:00:00+00:00",
            "structured_json": [
                {
                    "interface": "Ten-GigabitEthernet1/0/49",
                    "rx_power_dbm": "-5.34",
                    "transceiver_present": "",
                },
                {
                    "interface": "Ten-GigabitEthernet1/0/51",
                    "rx_power_dbm": " ",
                    "transceiver_present": "absent",
                },
            ],
        },
        mode="parser_validation",
        source="test",
        dataset_id="dataset-optical",
    )

    assert [(item.field_name, item.value) for item in observations] == [
        ("rx_power_dbm", -5.34),
        ("transceiver_present", "absent"),
    ]


def test_contract_can_map_blank_parser_status_to_present(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda *_args, **_kwargs: {
            "entity_type": "interface_optical",
            "primary_keys": ["interface"],
            "contract_version": 1,
            "state_semantics": {
                "status": {
                    "semantic_type": "state",
                    "observation_type": "interface.optical.presence",
                    "emit_when_empty": True,
                    "value_mapping": {
                        "": "present",
                        "The transceiver is absent.": "absent",
                    },
                    "on_unmapped": "reject",
                },
            },
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-optical-presence",
            "run_id": "run-1",
            "device_id": "SW1",
            "vendor": "H3C",
            "model": "S5130S-54S-HI-G",
            "canonical_command": "display transceiver diagnosis interface",
            "parser_template_id": "template-optical",
            "parser_version": "1",
            "observed_at": "2026-07-18T00:00:00+00:00",
            "structured_json": [
                {"interface": "Ten-GigabitEthernet1/0/49", "status": ""},
                {
                    "interface": "Ten-GigabitEthernet1/0/51",
                    "status": "The transceiver is absent.",
                },
            ],
        },
        mode="parser_validation",
        source="test",
        dataset_id="dataset-optical",
    )

    assert [(item.entity_key, item.value) for item in observations] == [
        ('["Ten-GigabitEthernet1/0/49"]', "present"),
        ('["Ten-GigabitEthernet1/0/51"]', "absent"),
    ]


def _state_observation(observed_at: str, value: str = "up") -> StateObservation:
    return StateObservation(
        observation_id=f"obs-{observed_at}",
        idempotency_key=f"idem-{observed_at}",
        state_key="SW1|interface|Eth1/1|status",
        run_id="run-1",
        snapshot_id="snapshot-1",
        dataset_id=None,
        mode="production",
        source="test",
        source_type="cli",
        evidence_id="evidence-1",
        schema_id="template-1",
        device_id="SW1",
        vendor="Cisco",
        model="N9K-C9364C",
        canonical_command="show interface status",
        entity_type="interface_l2",
        entity_key='["Eth1/1"]',
        field_name="status",
        value=value,
        value_text=value,
        value_number=None,
        value_type="string",
        observation_kind="state",
        observed_at=observed_at,
        parser_template_id="template-1",
        parser_version="v1",
        contract_version=1,
        confidence=1.0,
        metadata={},
    )


class _CurrentStateDb:
    def __init__(self, scalar):
        self.scalar = scalar
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append(statement)

        class Result:
            def __init__(self, scalar):
                self.scalar = scalar

            def scalar_one_or_none(self):
                return self.scalar

        return Result(self.scalar)


def test_current_state_upsert_is_atomic_and_guards_observed_at():
    db = _CurrentStateDb("state-row-id")

    updated = StateRepository()._upsert_current_state(
        db,
        _state_observation("2026-06-20T10:10:00+00:00", value="up"),
    )

    sql = str(db.statements[0]).lower()
    assert updated == 1
    assert "on conflict" in sql
    assert "state_key" in sql
    assert "excluded.observed_at > netops_current_state.observed_at" in sql


def test_current_state_upsert_reports_zero_when_db_skips_stale_observation():
    db = _CurrentStateDb(None)

    updated = StateRepository()._upsert_current_state(
        db,
        _state_observation("2026-06-20T10:00:00+00:00", value="up"),
    )

    assert updated == 0


def test_state_observation_insert_uses_conflict_do_nothing():
    db = _CurrentStateDb("obs-id")

    inserted = StateRepository()._insert_observation(
        db,
        _state_observation("2026-06-20T10:00:00+00:00", value="up"),
    )

    sql = str(db.statements[0]).lower()
    assert inserted is True
    assert "on conflict" in sql
    assert "do nothing" in sql
    assert "idempotency_key" in sql


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
        "ParserTemplateRepository",
        ParserRepositoryShouldNotBeUsed,
    )

    contract = normalizer._template_contract("template-1")

    assert contract["contract_id"] == "contract-1"
    assert contract["entity_type"] == "interface_l3"
    assert contract["primary_keys"] == ["interface"]


def test_template_contract_resolves_exact_snapshot_version(monkeypatch):
    class FakeContractRepository:
        def get_validation_dict(self, *, source_type, schema_id, version):
            assert (source_type, schema_id, version) == ("cli", "template-1", 3)
            return {
                "contract_id": "contract-v3",
                "entity_type": "interface_l3",
                "primary_keys": ["interface"],
                "contract_version": 3,
            }

    class ParserRepositoryShouldNotBeUsed:
        def get_template(self, template_id):
            raise AssertionError("exact ObservationContract should be resolved first")

    monkeypatch.setattr(
        normalizer,
        "ObservationContractRepository",
        FakeContractRepository,
    )
    monkeypatch.setattr(
        normalizer,
        "ParserTemplateRepository",
        ParserRepositoryShouldNotBeUsed,
    )

    contract = normalizer._template_contract("template-1", contract_version=3)

    assert contract["contract_id"] == "contract-v3"
    assert contract["contract_version"] == 3


def test_template_contract_fails_closed_when_snapshot_version_is_missing(monkeypatch):
    class FakeContractRepository:
        def get_validation_dict(self, **_kwargs):
            return None

    class FakeParserRepository:
        def get_template(self, _template_id):
            return None

    monkeypatch.setattr(
        normalizer,
        "ObservationContractRepository",
        FakeContractRepository,
    )
    monkeypatch.setattr(normalizer, "ParserTemplateRepository", FakeParserRepository)

    with pytest.raises(
        normalizer.ObservationContractVersionError,
        match="cli:template-1 v3",
    ):
        normalizer._template_contract("template-1", contract_version=3)


def test_observations_from_snapshot_uses_active_semantic_contract_not_parser_version(
    monkeypatch,
):
    calls = []

    def active_contract(template_id, **kwargs):
        calls.append((template_id, kwargs))
        return {
            "contract_id": "contract-active-v7",
            "contract_sha256": "a" * 64,
            "schema_id": "template-1",
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "ignore_fields": ["description"],
            "contract_version": 7,
        }

    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        active_contract,
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
    assert calls == [("template-1", {})]
    assert observations[0].contract_version == 7
    assert observations[0].source_type == "cli"
    assert observations[0].evidence_id == "ev-cli-test"
    assert observations[0].schema_id == "template-1"
    assert observations[0].metadata["evidence_id"] == "ev-cli-test"
    assert observations[0].metadata["parser_contract_version"] == 3
    assert observations[0].metadata["observation_contract_id"] == "contract-active-v7"
    assert observations[0].metadata["observation_contract_version"] == 7
    assert observations[0].metadata["observation_contract_digest"] == "a" * 64


def test_observations_from_snapshot_can_pin_semantic_contract_for_replay(monkeypatch):
    calls = []

    def pinned_contract(template_id, **kwargs):
        calls.append((template_id, kwargs))
        return {
            "schema_id": template_id,
            "entity_type": "device",
            "primary_keys": [],
            "contract_version": kwargs["contract_version"],
        }

    monkeypatch.setattr(normalizer, "_template_contract", pinned_contract)

    observations = observations_from_snapshot(
        {
            "id": "snapshot-replay",
            "run_id": "run-replay",
            "device_id": "device-1",
            "canonical_command": "show version",
            "parser_template_id": "template-1",
            "contract_version": 3,
            "observed_at": "2026-06-20T10:00:00+00:00",
            "structured_json": {"model": "model-1"},
        },
        mode="parser_validation",
        source="test",
        dataset_id="dataset-1",
        observation_contract_version=6,
    )

    assert calls == [("template-1", {"contract_version": 6})]
    assert observations[0].contract_version == 6
    assert observations[0].metadata["parser_contract_version"] == 3


def test_observations_from_snapshot_uses_contract_frozen_by_runtime_bundle(
    monkeypatch,
):
    class FrozenContract:
        def to_validation_dict(self):
            return {
                "contract_id": "contract-frozen-v8",
                "contract_sha256": "b" * 64,
                "schema_id": "template-1",
                "entity_type": "interface_l3",
                "primary_keys": ["interface"],
                "contract_version": 8,
            }

    class FakeContractRepository:
        def get_by_id(self, contract_id):
            assert contract_id == "contract-frozen-v8"
            return FrozenContract()

    monkeypatch.setattr(
        normalizer,
        "ObservationContractRepository",
        FakeContractRepository,
    )
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda *_args, **_kwargs: pytest.fail(
            "active contract lookup must not run for frozen production facts"
        ),
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-frozen-contract",
            "run_id": "run-frozen-contract",
            "device_id": "device-1",
            "canonical_command": "show ip interface brief",
            "parser_template_id": "template-1",
            "contract_version": 3,
            "observed_at": "2026-07-31T10:00:00+00:00",
            "structured_json": {"interface": "Eth1/1", "status": "up"},
            "metadata_json": {
                "observation_contract_refs": [
                    {
                        "contract_id": "contract-frozen-v8",
                        "schema_id": "template-1",
                        "digest": "b" * 64,
                    }
                ]
            },
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    assert observations[0].contract_version == 8
    assert observations[0].metadata["parser_contract_version"] == 3
    assert (
        observations[0].metadata["observation_contract_id"]
        == "contract-frozen-v8"
    )


def test_observations_from_snapshot_applies_validator_semantics(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda template_id, **_kwargs: {
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "validators": {
                "ip_address": {"type": "ip_or_unassigned"},
                "usage": {"type": "percent"},
            },
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-1",
            "run_id": "run-1",
            "device_id": "SW1",
            "canonical_command": "display ip interface brief",
            "parser_template_id": "template-1",
            "observed_at": "2026-06-20T10:00:00+00:00",
            "structured_json": [
                {
                    "interface": "GE1/0",
                    "ip_address": "10.10.1.1/024",
                    "usage": "56.7",
                }
            ],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    by_field = {item.field_name: item for item in observations}
    assert by_field["ip_address"].value == "10.10.1.1/24"
    assert by_field["ip_address"].semantic_type == "ip_address"
    assert by_field["usage"].unit == "%"
    assert by_field["usage"].semantic_type == "percentage"


def test_observations_carry_only_governed_applicability_context(monkeypatch):
    applicability = {
        "version": 1,
        "on_missing_context": "reject",
        "exclude_when": [
            {
                "code": "measurement_not_applicable",
                "when": {
                    "all": [
                        {"field": "bias_ma", "op": "lte", "value": 0},
                        {
                            "field": "rx_power_dbm",
                            "op": "equals_field",
                            "other_field": "tx_power_dbm",
                        },
                    ]
                },
            }
        ],
    }
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda template_id, **_kwargs: {
            "entity_type": "interface_optical",
            "primary_keys": ["interface"],
            "state_semantics": {
                "rx_power_dbm": {
                    "semantic_type": "gauge",
                    "observation_type": "interface.optical.rx_power",
                    "applicability": applicability,
                }
            },
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-optical",
            "run_id": "run-1",
            "device_id": "SW1",
            "canonical_command": "display transceiver diagnosis interface",
            "parser_template_id": "template-optical",
            "observed_at": "2026-07-14T00:00:00+00:00",
            "structured_json": [
                {
                    "interface": "Twenty-FiveGigE1/0/21",
                    "bias_ma": "0.00",
                    "rx_power_dbm": "-40.00",
                    "tx_power_dbm": "-40.00",
                    "description": "must-not-be-copied",
                }
            ],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    rx = next(item for item in observations if item.field_name == "rx_power_dbm")
    assert rx.metadata["_observation_context"] == {
        "bias_ma": "0.00",
        "rx_power_dbm": "-40.00",
        "tx_power_dbm": "-40.00",
    }
    assert "description" not in rx.metadata["_observation_context"]


def test_list_values_expand_into_dimensioned_scalar_observations(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda template_id, **_kwargs: {
            "entity_type": "measurement_group",
            "primary_keys": ["name"],
            "ignore_fields": ["limits"],
            "validators": {
                "readings": {"type": "number"},
                "limits": {"type": "number"},
            },
            "state_semantics": {
                "readings": {
                    "semantic_type": "gauge",
                    "observation_type": "measurement.reading",
                    "applicability": {
                        "version": 1,
                        "on_missing_context": "allow",
                        "exclude_when": [
                            {
                                "code": "above_limit",
                                "when": {"field": "limits", "op": "lte", "value": 0},
                            }
                        ],
                    },
                }
            },
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-list",
            "run_id": "run-1",
            "device_id": "device-1",
            "canonical_command": "inspect measurements",
            "parser_template_id": "template-list",
            "observed_at": "2026-07-14T00:00:00+00:00",
            "structured_json": [
                {
                    "name": "group-a",
                    "readings": ["1.5", "2.5"],
                    "limits": ["10", "20"],
                }
            ],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    assert [item.value_number for item in observations] == [1.5, 2.5]
    assert len({item.state_key for item in observations}) == 2
    assert [item.metadata["dimensions"]["value_index"] for item in observations] == [1, 2]
    assert [item.metadata["_observation_context"]["limits"] for item in observations] == [
        10.0,
        20.0,
    ]
    assert all(item.value_type == "number" for item in observations)


def test_contract_context_fields_are_copied_for_dynamic_thresholds(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda template_id, **_kwargs: {
            "entity_type": "generic_sensor",
            "primary_keys": ["sensor_id"],
            "validators": {
                "measurement": {"type": "number"},
                "low_reference": {"type": "number"},
            },
            "state_semantics": {
                "measurement": {
                    "semantic_type": "gauge",
                    "observation_type": "generic.measurement",
                    "context_fields": ["low_reference"],
                    "comparison_role": "observation",
                    "monitor": True,
                },
                "low_reference": {
                    "semantic_type": "gauge",
                    "comparison_role": "threshold",
                    "monitor": False,
                },
            },
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-dynamic-threshold",
            "run_id": "run-1",
            "device_id": "device-1",
            "canonical_command": "inspect sensor",
            "parser_template_id": "template-sensor",
            "observed_at": "2026-07-14T00:00:00+00:00",
            "structured_json": [
                {
                    "sensor_id": "sensor-a",
                    "measurement": "-12.5",
                    "low_reference": "-10.0",
                }
            ],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    measurement = next(item for item in observations if item.field_name == "measurement")
    assert measurement.metadata["_observation_context"] == {"low_reference": -10.0}
    assert [item.field_name for item in observations] == ["measurement"]


def test_observations_from_snapshot_prefers_inventory_device_id(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda template_id, **_kwargs: {
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "contract_version": 3,
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-1",
            "run_id": "run-1",
            "inventory_device_id": "inventory-device-1",
            "device_id": "SW1-10.0.0.1",
            "vendor": "Cisco",
            "model": "N9K-C9364C",
            "canonical_command": "show ip interface brief",
            "parser_template_id": "template-1",
            "parser_version": "v1",
            "observed_at": "2026-06-20T10:00:00+00:00",
            "structured_json": [{"interface": "Eth1/1", "status": "up", "protocol": "up"}],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    assert {item.device_id for item in observations} == {"inventory-device-1"}
    assert {item.metadata["external_device_id"] for item in observations} == {"SW1-10.0.0.1"}
    assert {item.metadata["identity_status"] for item in observations} == {"inventory_bound"}


def test_observations_from_snapshot_skips_interface_header_artifact(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda template_id, **_kwargs: {
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
        lambda template_id, **_kwargs: {
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


def test_vendor_native_cpu_fields_share_canonical_state_identity(monkeypatch):
    contracts = {
        "template-h3c": {
            "entity_type": "device",
            "primary_keys": [],
            "validators": {"cpu_5s": {"type": "percent"}},
            "state_semantics": {
                "cpu_5s": {
                    "semantic_type": "percentage",
                    "unit": "percent",
                    "observation_type": "device.cpu.utilization",
                    "dimensions": {"scope": "system", "window": "5s"},
                }
            },
        },
        "template-cisco": {
            "entity_type": "device",
            "primary_keys": [],
            "validators": {"cpu_total_5_sec": {"type": "percent"}},
            "state_semantics": {
                "cpu_total_5_sec": {
                    "semantic_type": "percentage",
                    "unit": "percent",
                    "observation_type": "device.cpu.utilization",
                    "dimensions": {"scope": "system", "window": "5s"},
                }
            },
        },
    }
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda template_id, **_kwargs: contracts[template_id],
    )
    common = {
        "run_id": "run-1",
        "device_id": "device-1",
        "inventory_device_id": "inventory-device-1",
        "observed_at": "2026-07-27T00:00:00+00:00",
    }

    h3c = observations_from_snapshot(
        {
            **common,
            "id": "snapshot-h3c",
            "vendor": "H3C",
            "model": "S5130",
            "canonical_command": "display cpu-usage",
            "parser_template_id": "template-h3c",
            "structured_json": [{"cpu_5s": "24"}],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )[0]
    cisco = observations_from_snapshot(
        {
            **common,
            "id": "snapshot-cisco",
            "vendor": "Cisco",
            "model": "NEXUS9000",
            "canonical_command": "show processes cpu",
            "parser_template_id": "template-cisco",
            "structured_json": [{"cpu_total_5_sec": "18"}],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )[0]

    assert h3c.field_name == "cpu_5s"
    assert cisco.field_name == "cpu_total_5_sec"
    assert h3c.observation_type == cisco.observation_type == "device.cpu.utilization"
    assert h3c.dimensions == cisco.dimensions == {"scope": "system", "window": "5s"}
    assert h3c.dimension_signature == cisco.dimension_signature
    assert h3c.state_key == cisco.state_key


def test_source_cpu_keys_project_to_governed_canonical_entity(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda *_args, **_kwargs: {
            "entity_type": "cpu_record",
            "primary_keys": ["slot", "cpu"],
            "canonical_entity_type": "cpu_scope",
            "entity_identity_fields": ["scope", "scope_id"],
            "entity_dimensions": {
                "scope": {"literal": "processor"},
                "scope_id": {
                    "op": "concat",
                    "args": [
                        {"literal": "slot:"},
                        {"field": "slot"},
                        {"literal": "/cpu:"},
                        {"field": "cpu"},
                    ],
                },
            },
            "validators": {"usage_5s": {"type": "percent"}},
            "state_semantics": {
                "usage_5s": {
                    "semantic_type": "gauge",
                    "unit": "percent",
                    "observation_type": "device.cpu.utilization",
                    "dimensions": {"window": "5s"},
                }
            },
        },
    )

    observation = observations_from_snapshot(
        {
            "id": "snapshot-cpu-scope",
            "run_id": "run-1",
            "device_id": "device-1",
            "inventory_device_id": "inventory-device-1",
            "vendor": "H3C",
            "model": "MSR5620",
            "canonical_command": "display cpu",
            "parser_template_id": "template-cpu",
            "observed_at": "2026-07-29T00:00:00+00:00",
            "structured_json": [
                {"slot": "01", "cpu": "2", "usage_5s": "24"}
            ],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )[0]

    assert observation.entity_type == "cpu_scope"
    assert observation.entity_key == '["processor","slot:01/cpu:2"]'
    assert observation.metadata["source_entity_type"] == "cpu_record"
    assert observation.metadata["source_entity_key"] == '["01","2"]'
    assert observation.metadata["canonical_identity"]["values"] == {
        "scope": "processor",
        "scope_id": "slot:01/cpu:2",
    }


def test_device_entity_identity_comes_from_inventory_context(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda *_args, **_kwargs: {
            "entity_type": "device",
            "primary_keys": [],
            "canonical_entity_type": "device",
            "entity_identity_fields": ["device_id"],
            "entity_dimensions": {
                "device_id": {"context": "device_id"},
            },
            "state_semantics": {
                "software_version": {
                    "semantic_type": "state",
                    "observation_type": "device.software.version",
                }
            },
        },
    )

    observation = observations_from_snapshot(
        {
            "id": "snapshot-device-context",
            "run_id": "run-1",
            "device_id": "external-device-1",
            "inventory_device_id": "inventory-device-1",
            "vendor": "H3C",
            "model": "MSR5620",
            "canonical_command": "display version",
            "parser_template_id": "template-version",
            "observed_at": "2026-07-29T00:00:00+00:00",
            "structured_json": [{"software_version": "7.1.064"}],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )[0]

    assert observation.device_id == "inventory-device-1"
    assert observation.entity_type == "device"
    assert observation.entity_key == '["inventory-device-1"]'
    assert observation.metadata["canonical_identity"]["values"] == {
        "device_id": "inventory-device-1",
    }


def test_state_normalizer_applies_governed_memory_formula(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda *_args, **_kwargs: {
            "entity_type": "device",
            "primary_keys": [],
            "validators": {"free_ratio": {"type": "percent"}},
            "state_semantics": {
                "free_ratio": {
                    "semantic_type": "percentage",
                    "unit": "percent",
                    "observation_type": "device.memory.utilization",
                    "dimensions": {"scope": "system"},
                    "transform": {
                        "op": "subtract",
                        "args": [{"literal": 100}, {"field": "free_ratio"}],
                    },
                }
            },
        },
    )

    observation = observations_from_snapshot(
        {
            "id": "snapshot-memory",
            "run_id": "run-1",
            "device_id": "device-1",
            "canonical_command": "display memory",
            "parser_template_id": "template-memory",
            "observed_at": "2026-07-27T00:00:00+00:00",
            "structured_json": [{"free_ratio": "52.7%"}],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )[0]

    assert observation.field_name == "free_ratio"
    assert observation.observation_type == "device.memory.utilization"
    assert observation.value_number == pytest.approx(47.3)
    assert observation.metadata["source_value"] == pytest.approx(52.7)


def test_state_normalizer_preserves_numeric_unavailability_without_converting_to_zero(
    monkeypatch,
):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda *_args, **_kwargs: {
            "entity_type": "interface_optical_channel",
            "primary_keys": ["interface", "channel"],
            "validators": {
                "rx_power_dbm": {
                    "type": "number",
                    "unavailable_tokens": ["N/A", "--"],
                }
            },
            "state_semantics": {
                "rx_power_dbm": {
                    "semantic_type": "measurement",
                    "unit": "dBm",
                    "observation_type": "interface.optical.rx_power",
                }
            },
        },
    )

    observation = observations_from_snapshot(
        {
            "id": "snapshot-optical-unavailable",
            "run_id": "run-1",
            "device_id": "device-1",
            "canonical_command": "display transceiver diagnosis interface",
            "parser_template_id": "template-optical",
            "observed_at": "2026-08-02T00:00:00+00:00",
            "structured_json": [
                {
                    "interface": "FortyGigE1/0/1",
                    "channel": "1",
                    "rx_power_dbm": "N/A",
                }
            ],
        },
        mode="production",
        source="test",
        dataset_id=None,
    )[0]

    assert observation.value is None
    assert observation.value_number is None
    assert observation.metadata["availability"] == "unavailable"
    assert observation.metadata["unavailable_token"] == "N/A"


def test_state_normalizer_handles_multiple_record_sets_from_one_command(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda *_args, **_kwargs: {
            "contract_id": "contract-version",
            "contract_version": 2,
            "record_sets": [
                {
                    "name": "device_version",
                    "entity_type": "device",
                    "primary_keys": [],
                    "required_fields": ["software_version"],
                    "optional_fields": ["software_release"],
                    "state_semantics": {
                        "software_version": {
                            "semantic_type": "text",
                            "observation_type": "device.software.version",
                        },
                        "software_release": {
                            "semantic_type": "text",
                            "observation_type": "device.software.release",
                        },
                    },
                },
                {
                    "name": "slot_inventory",
                    "entity_type": "device_slot",
                    "primary_keys": ["slot"],
                    "required_fields": ["slot", "board_type"],
                    "optional_fields": [],
                    "state_semantics": {
                        "slot": {"semantic_type": "identity"},
                        "board_type": {
                            "semantic_type": "text",
                            "observation_type": "device.slot.board_type",
                        },
                    },
                },
            ],
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-version",
            "run_id": "run-1",
            "device_id": "device-1",
            "canonical_command": "display version",
            "parser_template_id": "template-version",
            "observed_at": "2026-07-27T00:00:00+00:00",
            "structured_json": {
                "record_sets": {
                    "device_version": [
                        {
                            "software_version": "7.1.064",
                            "software_release": "0821P18",
                        }
                    ],
                    "slot_inventory": [
                        {"slot": "0", "board_type": "MPU-60"},
                        {"slot": "1", "board_type": "MPU-60"},
                    ],
                }
            },
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    assert len(observations) == 4
    assert {item.entity_type for item in observations} == {"device", "device_slot"}
    assert {
        item.metadata["record_set_name"] for item in observations
    } == {"device_version", "slot_inventory"}
    board_facts = [
        item
        for item in observations
        if item.observation_type == "device.slot.board_type"
    ]
    assert len(board_facts) == 2
    assert board_facts[0].state_key != board_facts[1].state_key
    assert board_facts[0].idempotency_key != board_facts[1].idempotency_key


def test_state_normalizer_joins_cross_record_device_thresholds(monkeypatch):
    monkeypatch.setattr(
        normalizer,
        "_template_contract",
        lambda *_args, **_kwargs: {
            "contract_id": "contract-optical-multi",
            "contract_version": 1,
            "record_sets": [
                {
                    "name": "measurements",
                    "entity_type": "interface_optical",
                    "primary_keys": ["interface", "channel"],
                    "required_fields": ["interface", "channel", "rx_power"],
                    "optional_fields": [],
                    "validators": {"rx_power": {"type": "number"}},
                    "state_semantics": {
                        "interface": {"semantic_type": "identity"},
                        "channel": {"semantic_type": "identity"},
                        "rx_power": {
                            "semantic_type": "gauge",
                            "unit": "dbm",
                            "observation_type": "interface.optical.rx_power",
                            "threshold_references": {
                                "critical_low": {
                                    "field_name": "rx_power_low",
                                    "context_key": "threshold.critical_low",
                                    "source": "cross_record_set_reference",
                                    "record_set_name": "thresholds",
                                    "join_keys": {"interface": "interface"},
                                    "relation_name": "measurement_thresholds",
                                }
                            },
                        },
                    },
                },
                {
                    "name": "thresholds",
                    "entity_type": "interface_threshold",
                    "primary_keys": ["interface"],
                    "required_fields": ["interface", "rx_power_low"],
                    "optional_fields": [],
                    "validators": {"rx_power_low": {"type": "number"}},
                    "state_semantics": {
                        "interface": {"semantic_type": "identity"},
                        "rx_power_low": {
                            "semantic_type": "gauge",
                            "comparison_role": "threshold",
                            "monitor": False,
                        },
                    },
                },
            ],
            "record_set_relations": [
                {
                    "name": "measurement_thresholds",
                    "source_record_set": "measurements",
                    "target_record_set": "thresholds",
                    "join_keys": {"interface": "interface"},
                    "threshold_bindings": {
                        "rx_power": {"critical_low": "rx_power_low"}
                    },
                    "match_policy": "partial",
                }
            ],
        },
    )

    observations = observations_from_snapshot(
        {
            "id": "snapshot-optical-multi",
            "run_id": "run-1",
            "device_id": "device-1",
            "canonical_command": "inspect optics",
            "parser_template_id": "template-optical-multi",
            "observed_at": "2026-07-27T00:00:00+00:00",
            "structured_json": {
                "record_sets": {
                    "measurements": [
                        {
                            "interface": "Eth1/1",
                            "channel": "1",
                            "rx_power": "-13.5",
                        },
                        {
                            "interface": "Eth1/2",
                            "channel": "1",
                            "rx_power": "-8.0",
                        },
                    ],
                    "thresholds": [
                        {"interface": "Eth1/1", "rx_power_low": "-12.0"}
                    ],
                }
            },
        },
        mode="production",
        source="test",
        dataset_id=None,
    )

    assert len(observations) == 2
    by_entity = {item.entity_key: item for item in observations}
    matched = by_entity['["Eth1/1","1"]']
    matched_context = matched.metadata[APPLICABILITY_CONTEXT_KEY]
    assert matched_context["threshold.critical_low"] == -12.0
    assert matched_context["threshold_source"] == "device_output"
    assert matched_context["threshold_reference_sources"]["critical_low"] == {
        "source": "cross_record_set_reference",
        "record_set_name": "thresholds",
        "relation_name": "measurement_thresholds",
        "field_name": "rx_power_low",
    }
    unmatched = by_entity['["Eth1/2","1"]']
    assert unmatched.metadata[APPLICABILITY_CONTEXT_KEY] == {}


def test_normalize_run_can_limit_to_selected_devices(monkeypatch):
    _install_analysis_runtime(monkeypatch)
    calls = {}

    class FakeRepository:
        def list_run_snapshot_ids(self, run_id, *, device_ids=None):
            calls["run_id"] = run_id
            calls["device_ids"] = device_ids
            return ["snapshot-1"]

        def save_observations(self, observations, *, update_current_state):
            calls["saved"] = (observations, update_current_state)
            return len(observations), 0

    def fake_build(repository, snapshot_id, *, update_current_state=None, include_observations=True):
        assert snapshot_id == "snapshot-1"
        assert update_current_state is False
        assert include_observations is True
        observations = [
            _state_observation("2026-06-20T10:00:00+00:00", value="up"),
            _state_observation("2026-06-20T10:01:00+00:00", value="down"),
        ]
        return (
            {
                "success": True,
                "observation_count": 2,
                "created_count": 0,
                "current_state_updates": 0,
            },
            observations,
            False,
        )

    monkeypatch.setattr(normalizer, "StateRepository", FakeRepository)
    monkeypatch.setattr(normalizer, "_build_snapshot_normalization", fake_build)

    result = normalize_run(
        "run-1",
        update_current_state=False,
        device_ids=["SW1", "SW2"],
    )

    assert calls["run_id"] == "run-1"
    assert calls["device_ids"] == ["SW1", "SW2"]
    assert calls["saved"][1] is False
    assert result["device_ids"] == ["SW1", "SW2"]
    assert result["observation_count"] == 2


def test_observation_change_detector_uses_observation_ids(monkeypatch):
    _install_analysis_runtime(monkeypatch)

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

    def fake_save_changes(changes, **kwargs):
        saved["changes"] = changes
        saved["derived_observations"] = kwargs.get("derived_observations")
        saved["decisions"] = kwargs.get("decisions")
        return len(changes)

    monkeypatch.setattr("src.core.patrol.change_detector.StateRepository", FakeRepository)
    monkeypatch.setattr(
        "src.core.patrol.change_detector.save_changes",
        fake_save_changes,
    )

    result = detect_observation_changes_against_history(
        current_run_id="run-current",
        persist=True,
    )

    assert result["change_count"] == 3
    assert result["initial_observations"] == 1
    assert result["added_observations"] == 1
    assert result["deleted_observations"] == 0
    assert all(
        change["previous_observation_id"] != "obs-command-not-collected"
        for change in result["changes"]
    )
    assert any(
        change["current_observation_id"] == "obs-new-command" and change["change_type"] == "initial"
        for change in result["changes"]
    )
    change = result["changes"][0]
    assert change["source"] == "state_observation"
    assert change["previous_observation_id"] == "obs-previous"
    assert change["current_observation_id"] == "obs-current"
    assert saved["changes"][0].old_value == "up"


def test_observation_change_detector_accepts_explicit_previous_run(monkeypatch):
    _install_analysis_runtime(monkeypatch)
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

        def list_observations_for_runs(self, run_ids, *, baseline_only=False):
            calls["run_ids"] = run_ids
            calls["baseline_only"] = baseline_only
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

    monkeypatch.setattr("src.core.patrol.change_detector.StateRepository", FakeRepository)
    monkeypatch.setattr(
        "src.core.patrol.change_detector.save_changes",
        lambda changes, **_kwargs: len(changes),
    )

    result = detect_observation_changes_against_history(
        current_run_id="run-current",
        previous_run_id="run-previous",
        persist=True,
    )

    assert calls["run_ids"] == ["run-previous"]
    assert calls["baseline_only"] is False
    assert result["previous_run_id"] == "run-previous"
    assert result["change_count"] == 1
    assert result["changes"][0]["old"] == "old-model"
    assert result["changes"][0]["new"] == "new-model"


def test_observation_change_detector_rejects_unnormalized_explicit_baseline(monkeypatch):
    class FakeStateRepository:
        def list_run_observations(self, _run_id):
            return []

    class FakePatrolRepository(_AnalysisPatrolRepository):
        def get_run(self, run_id):
            return {
                "id": run_id,
                "mode": "production",
                "analysis_status": "pending" if run_id == "run-previous" else "normalized",
                "run_purpose": "historical_compare",
            }

    monkeypatch.setattr("src.core.patrol.change_detector.StateRepository", FakeStateRepository)
    monkeypatch.setattr("src.core.patrol.change_detector.PatrolRepository", FakePatrolRepository)

    with pytest.raises(ExplicitBaselineNotReadyError, match="complete normalization"):
        detect_observation_changes_against_history(
            current_run_id="run-current",
            previous_run_id="run-previous",
        )


def test_observation_change_detector_allows_explicit_partial_normalization(monkeypatch):
    class FakeStateRepository:
        def list_run_observations(self, _run_id):
            return []

        def list_observations_for_runs(self, _run_ids, **_kwargs):
            return []

    class FakePatrolRepository(_AnalysisPatrolRepository):
        def get_run(self, run_id):
            return {
                "id": run_id,
                "mode": "production",
                "analysis_status": "normalized_partial",
                "run_purpose": "historical_compare",
                "metadata_json": {"offline_job_id": "offline-job-1"},
            }

    monkeypatch.setattr("src.core.patrol.change_detector.StateRepository", FakeStateRepository)
    monkeypatch.setattr("src.core.patrol.change_detector.PatrolRepository", FakePatrolRepository)

    result = detect_observation_changes_against_history(
        current_run_id="run-current",
        previous_run_id="run-previous",
        allow_partial_normalization=True,
    )

    assert result["success"] is True
    assert result["change_count"] == 0


def test_observation_change_detector_requires_explicit_longitudinal_offline_mode(
    monkeypatch,
):
    class FakeStateRepository:
        def list_run_observations(self, _run_id):
            return []

        def list_observations_for_runs(self, _run_ids, **_kwargs):
            return []

    class FakePatrolRepository:
        def get_run(self, run_id):
            previous = run_id == "run-q2"
            return {
                "id": run_id,
                "mode": "parser_validation",
                "analysis_status": "completed",
                "started_at": (
                    "2026-04-30T10:00:00+08:00" if previous else "2026-06-26T10:00:00+08:00"
                ),
                "metadata_json": {
                    "offline_job_id": "offline-q2" if previous else "offline-q3",
                    "period_role": "current",
                },
            }

        def list_collection_results(self, _run_id):
            return []

    monkeypatch.setattr("src.core.patrol.change_detector.StateRepository", FakeStateRepository)
    monkeypatch.setattr(
        "src.core.patrol.change_detector.PatrolRepository",
        FakePatrolRepository,
    )

    with pytest.raises(ExplicitBaselineNotReadyError, match="different jobs"):
        detect_observation_changes_against_history(
            current_run_id="run-q3",
            previous_run_id="run-q2",
            persist=False,
        )

    result = detect_observation_changes_against_history(
        current_run_id="run-q3",
        previous_run_id="run-q2",
        persist=False,
        allow_longitudinal_offline_comparison=True,
    )

    assert result["success"] is True
    assert result["previous_run_id"] == "run-q2"


def test_longitudinal_offline_comparison_rejects_reversed_time(monkeypatch):
    class FakeStateRepository:
        def list_run_observations(self, _run_id):
            return []

        def list_observations_for_runs(self, _run_ids, **_kwargs):
            return []

    class FakePatrolRepository:
        def get_run(self, run_id):
            return {
                "id": run_id,
                "mode": "parser_validation",
                "analysis_status": "completed",
                "started_at": (
                    "2026-07-01T10:00:00+08:00"
                    if run_id == "run-previous"
                    else "2026-06-01T10:00:00+08:00"
                ),
                "metadata_json": {
                    "offline_job_id": run_id,
                    "period_role": "current",
                },
            }

    monkeypatch.setattr("src.core.patrol.change_detector.StateRepository", FakeStateRepository)
    monkeypatch.setattr(
        "src.core.patrol.change_detector.PatrolRepository",
        FakePatrolRepository,
    )

    with pytest.raises(ExplicitBaselineNotReadyError, match="previous run time"):
        detect_observation_changes_against_history(
            current_run_id="run-current",
            previous_run_id="run-previous",
            persist=False,
            allow_longitudinal_offline_comparison=True,
        )


def test_observation_change_detector_uses_active_change_policy(monkeypatch):
    _install_analysis_runtime(monkeypatch)
    saved = {}

    class FakePolicyRepository:
        def get_active_policy(self, observation_type):
            assert observation_type == "device.cpu.utilization"
            return {
                "policy_id": "policy-1",
                "version_id": "policy-version-1",
                "policy": {
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "compare_mode": "threshold_and_deadband",
                    "deadband": 5,
                    "thresholds": {"high": 95},
                    "recovery_threshold": 90,
                },
            }

        def list_active_policies(self):
            return [
                {
                    **self.get_active_policy("device.cpu.utilization"),
                    "observation_type": "device.cpu.utilization",
                }
            ]

    class FakeRepository:
        def list_run_observations(self, run_id):
            return [
                {
                    "id": "obs-current",
                    "run_id": run_id,
                    "snapshot_id": "snapshot-current",
                    "mode": "production",
                    "device_id": "SW1",
                    "canonical_command": "display cpu",
                    "entity_type": "cpu",
                    "entity_key": "__singleton__",
                    "field_name": "cpu_5s",
                    "value_json": 91,
                    "value_number": 91.0,
                    "observed_at": "2026-06-20T10:00:00+00:00",
                    "confidence": 1.0,
                    "metadata_json": {"observation_type": "device.cpu.utilization"},
                }
            ]

        def list_prior_observations_for_run(self, run_id):
            return [
                {
                    "id": "obs-previous",
                    "run_id": "run-previous",
                    "snapshot_id": "snapshot-previous",
                    "mode": "production",
                    "device_id": "SW1",
                    "canonical_command": "display cpu",
                    "entity_type": "cpu",
                    "entity_key": "__singleton__",
                    "field_name": "cpu_5s",
                    "value_json": 89,
                    "value_number": 89.0,
                    "observed_at": "2026-06-20T09:00:00+00:00",
                    "confidence": 1.0,
                    "metadata_json": {"observation_type": "device.cpu.utilization"},
                }
            ]

    monkeypatch.setattr("src.core.patrol.change_detector.StateRepository", FakeRepository)
    monkeypatch.setattr(
        "src.core.patrol.change_detector.ChangePolicyRepository", FakePolicyRepository
    )
    monkeypatch.setattr(
        "src.core.patrol.change_detector.save_changes",
        lambda changes, **kwargs: saved.update(kwargs) or len(changes),
    )

    result = detect_observation_changes_against_history(
        current_run_id="run-current",
        persist=True,
    )

    assert result["change_count"] == 0
    assert saved["decisions"][0]["decision"] == "suppressed"
    assert saved["decisions"][0]["change_policy_id"] == "policy-1"


def test_observation_change_detector_emits_threshold_crossing_with_policy(monkeypatch):
    _install_analysis_runtime(monkeypatch)

    class FakePolicyRepository:
        def get_active_policy(self, observation_type):
            return {
                "policy_id": "policy-1",
                "version_id": "policy-version-1",
                "policy": {
                    "observation_type": observation_type,
                    "semantic_type": "gauge",
                    "compare_mode": "threshold_and_deadband",
                    "deadband": 5,
                    "thresholds": {"high": 95},
                    "recovery_threshold": 90,
                },
            }

        def list_active_policies(self):
            return [
                {
                    **self.get_active_policy("device.cpu.utilization"),
                    "observation_type": "device.cpu.utilization",
                }
            ]

    class FakeRepository:
        def list_run_observations(self, run_id):
            return [
                {
                    "id": "obs-current",
                    "run_id": run_id,
                    "snapshot_id": "snapshot-current",
                    "mode": "production",
                    "device_id": "SW1",
                    "canonical_command": "display cpu",
                    "entity_type": "cpu",
                    "entity_key": "__singleton__",
                    "field_name": "cpu_5s",
                    "value_json": 96,
                    "value_number": 96.0,
                    "observed_at": "2026-06-20T10:00:00+00:00",
                    "confidence": 1.0,
                    "metadata_json": {"observation_type": "device.cpu.utilization"},
                }
            ]

        def list_prior_observations_for_run(self, run_id):
            return [
                {
                    "id": "obs-previous",
                    "run_id": "run-previous",
                    "snapshot_id": "snapshot-previous",
                    "mode": "production",
                    "device_id": "SW1",
                    "canonical_command": "display cpu",
                    "entity_type": "cpu",
                    "entity_key": "__singleton__",
                    "field_name": "cpu_5s",
                    "value_json": 89,
                    "value_number": 89.0,
                    "observed_at": "2026-06-20T09:00:00+00:00",
                    "confidence": 1.0,
                    "metadata_json": {"observation_type": "device.cpu.utilization"},
                }
            ]

    monkeypatch.setattr("src.core.patrol.change_detector.StateRepository", FakeRepository)
    monkeypatch.setattr(
        "src.core.patrol.change_detector.ChangePolicyRepository", FakePolicyRepository
    )
    monkeypatch.setattr(
        "src.core.patrol.change_detector.save_changes",
        lambda changes, **_kwargs: len(changes),
    )

    result = detect_observation_changes_against_history(
        current_run_id="run-current",
        persist=True,
    )

    assert result["change_count"] == 1
    assert result["changes"][0]["old"] == 89
    assert result["changes"][0]["new"] == 96


def test_observation_change_detector_filters_selected_devices(monkeypatch):
    _install_analysis_runtime(monkeypatch)
    calls = {}

    class FakeRepository:
        def list_run_observations(self, run_id, *, device_ids=None):
            calls["current"] = (run_id, device_ids)
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

        def list_observations_for_runs(self, run_ids, *, device_ids=None, baseline_only=False):
            calls["previous"] = (run_ids, device_ids, baseline_only)
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
            raise AssertionError("explicit previous_run_id should be used")

    monkeypatch.setattr("src.core.patrol.change_detector.StateRepository", FakeRepository)
    monkeypatch.setattr(
        "src.core.patrol.change_detector.save_changes",
        lambda changes, **_kwargs: len(changes),
    )

    result = detect_observation_changes_against_history(
        current_run_id="run-current",
        previous_run_id="run-previous",
        device_ids=["SW1"],
        persist=True,
    )

    assert calls["current"] == ("run-current", ["SW1"])
    assert calls["previous"] == (["run-previous"], ["SW1"], False)
    assert result["device_ids"] == ["SW1"]


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


def test_change_detection_input_revision_includes_prior_observations(monkeypatch):
    scopes = []
    prior = [{"id": "prior-1", "snapshot_id": "snapshot-p1", "observed_at": "2026-01-01"}]

    class FakeStateRepository:
        def list_run_observations(self, _run_id):
            return [
                {
                    "id": "current-1",
                    "snapshot_id": "snapshot-c1",
                    "observed_at": "2026-01-02",
                }
            ]

        def list_observations_for_runs(self, _run_ids, **_kwargs):
            return list(prior)

    class FakePolicyRepository:
        def list_active_policies(self):
            return []

    class FakePatrolRepository:
        def begin_analysis_attempt(self, *, scope, **_kwargs):
            scopes.append(scope)
            return {
                "reused": False,
                "in_progress": True,
                "attempt_id": "attempt-1",
                "attempt_number": 1,
                "owner_token": None,
                "rule_bundle": {},
                "rule_bundle_digest": "digest",
            }

    monkeypatch.setattr("src.core.patrol.change_detector.StateRepository", FakeStateRepository)
    monkeypatch.setattr(
        "src.core.patrol.change_detector.ChangePolicyRepository", FakePolicyRepository
    )
    monkeypatch.setattr("src.core.patrol.change_detector.PatrolRepository", FakePatrolRepository)

    with pytest.raises(AnalysisAttemptInProgressError):
        detect_observation_changes_against_history(
            current_run_id="run-current", previous_run_id="run-previous"
        )
    prior.append({"id": "prior-2", "snapshot_id": "snapshot-p2", "observed_at": "2026-01-01"})
    with pytest.raises(AnalysisAttemptInProgressError):
        detect_observation_changes_against_history(
            current_run_id="run-current", previous_run_id="run-previous"
        )

    assert scopes[0]["input_revision_digest"] != scopes[1]["input_revision_digest"]
