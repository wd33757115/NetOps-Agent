from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from src.core.parser.contract_inference import ContractProposal
from src.core.parser.contract_validation import infer_and_validate_contract
from src.core.rule_control.generation_agent import ProviderResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPT = (
    PROJECT_ROOT
    / "src"
    / "skills"
    / "observation-contract-generator"
    / "scripts"
    / "run.py"
)


def _run_skill(tmp_path: Path, params: dict, *, expect_success_exit: bool = True) -> dict:
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SKILL_SCRIPT), "--params", str(params_path)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if expect_success_exit:
        assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


def _load_skill_module():
    spec = importlib.util.spec_from_file_location("observation_contract_skill", SKILL_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contract_validation_infers_interface_l3_primary_key():
    result = infer_and_validate_contract(
        command="display ip interface brief",
        records=[
            {
                "interface": "Interface1",
                "ip_address": "10.0.0.1",
                "oper_status": "up",
                "protocol_status": "up",
            },
            {
                "interface": "Interface2",
                "ip_address": "unassigned",
                "oper_status": "down",
                "protocol_status": "down",
            },
        ],
    )

    assert result["success"] is True
    assert result["proposal"]["entity_type"] == "interface_l3"
    assert result["proposal"]["primary_keys"] == ("interface",)
    assert result["proposal"]["state_semantics"]["interface"]["semantic_type"] == "identity"
    assert (
        result["proposal"]["state_semantics"]["protocol_status"]["semantic_type"]
        == "state"
    )
    assert result["validation"]["primary_key_status"] == "valid"
    assert result["validation"]["required_field_coverage"] == 100


def test_contract_validation_rejects_duplicate_primary_keys():
    result = infer_and_validate_contract(
        command="display ip interface brief",
        records=[
            {
                "interface": "Interface1",
                "ip_address": "10.0.0.1",
                "status": "up",
                "protocol": "up",
            },
            {
                "interface": "Interface1",
                "ip_address": "10.0.0.2",
                "status": "down",
                "protocol": "down",
            },
        ],
    )

    assert result["success"] is False
    assert result["validation"]["primary_key_status"] == "invalid"
    assert result["validation"]["duplicate_key_count"] == 1


def test_contract_validation_treats_numeric_zero_as_present():
    result = infer_and_validate_contract(
        command="display cpu",
        records=[{"slot": 0, "cpu_5s": 0, "cpu_1m": 0.0, "cpu_5m": 0}],
    )

    assert result["success"] is True
    assert result["validation"]["missing_fields"] == []


def test_contract_validation_treats_false_as_present_for_required_field():
    proposal = ContractProposal(
        category="feature_flag",
        entity_type="feature_flag",
        primary_keys=("name",),
        required_fields=("name", "enabled"),
    )

    result = infer_and_validate_contract(
        command="show feature",
        records=[{"name": "feature-a", "enabled": False}],
        proposal=proposal,
    )

    assert result["success"] is True
    assert result["validation"]["missing_fields"] == []


def test_contract_validation_checks_each_compiled_list_value():
    proposal = ContractProposal(
        category="measurement",
        entity_type="measurement",
        primary_keys=("name",),
        required_fields=("name",),
        optional_fields=("readings",),
        validators={"readings": {"type": "number"}},
    )

    accepted = infer_and_validate_contract(
        command="inspect measurements",
        records=[{"name": "a", "readings": ["1.5", "2.5"]}],
        proposal=proposal,
    )
    rejected = infer_and_validate_contract(
        command="inspect measurements",
        records=[{"name": "a", "readings": ["1.5", "invalid"]}],
        proposal=proposal,
    )

    assert accepted["success"] is True
    assert rejected["success"] is False
    assert rejected["validation"]["value_errors"] == [
        {
            "record_index": 0,
            "field": "readings",
            "validator": "number",
            "error": "value_not_numeric",
            "value_index": 1,
        }
    ]


def test_contract_validation_empty_output_requires_pattern_match():
    proposal = ContractProposal(
        category="vrrp",
        entity_type="vrrp",
        primary_keys=("interface", "group"),
        required_fields=("interface", "group", "state"),
        allow_empty=True,
        empty_patterns=(r"(?i)vrrp.*not configured",),
    )

    accepted = infer_and_validate_contract(
        command="display vrrp statistics",
        records=[],
        samples_text="VRRP is not configured on this device",
        proposal=proposal,
    )
    rejected = infer_and_validate_contract(
        command="display vrrp statistics",
        records=[],
        samples_text="Permission denied while executing command",
        proposal=proposal,
    )

    assert accepted["success"] is True
    assert accepted["validation"]["empty_accepted"] is True
    assert rejected["success"] is False
    assert rejected["validation"]["empty_accepted"] is False


def test_contract_validation_accepts_empty_schema_value_but_rejects_missing_column():
    proposal = ContractProposal(
        category="optical",
        entity_type="interface_optical",
        primary_keys=("interface_name",),
        required_fields=("interface_name", "status"),
        state_semantics={
            "interface_name": {"semantic_type": "identity"},
            "status": {
                "semantic_type": "state",
                "observation_type": "interface.optical.presence",
                "value_mapping": {
                    "": "present",
                    "The transceiver is absent.": "absent",
                },
                "emit_when_empty": True,
            },
        },
    )

    accepted = infer_and_validate_contract(
        command="display transceiver diagnosis interface",
        records=[
            {"interface_name": "XGE1/0/1", "status": ""},
            {
                "interface_name": "XGE1/0/2",
                "status": "The transceiver is absent.",
            },
        ],
        proposal=proposal,
    )
    missing_column = infer_and_validate_contract(
        command="display transceiver diagnosis interface",
        records=[{"interface_name": "XGE1/0/1"}],
        proposal=proposal,
    )

    assert accepted["success"] is True
    assert accepted["validation"]["missing_fields"] == []
    assert missing_column["success"] is False
    assert missing_column["validation"]["missing_fields"] == ["status"]


def test_contract_seed_broadens_ip_validator_only_with_complete_evidence():
    module = _load_skill_module()
    seed = {
        "validators": {
            "primary_ip": {"type": "ip"},
        }
    }

    reconciled = module._reconcile_contract_seed_with_records(
        seed,
        [
            {"primary_ip": "192.0.2.1"},
            {"primary_ip": "--"},
            {"primary_ip": "unassigned"},
        ],
    )
    unsafe = module._reconcile_contract_seed_with_records(
        seed,
        [
            {"primary_ip": "192.0.2.1"},
            {"primary_ip": "not-an-ip"},
        ],
    )

    assert reconciled["validators"]["primary_ip"]["type"] == "ip_or_unassigned"
    evidence = reconciled["evidence"]["source_validator_reconciliation"]
    assert evidence["strategy"] == "evidence_proven_compatible_broadening"
    assert evidence["revisions"][0]["observed_unassigned_tokens"] == [
        "--",
        "unassigned",
    ]
    assert unsafe["validators"]["primary_ip"]["type"] == "ip"
    assert "evidence" not in unsafe


def test_observation_contract_generator_skill_infer_and_validate(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "mode": "infer_and_validate",
            "command": "display cpu",
            "records": [
                {"slot": "1", "cpu_5s": "15", "cpu_1m": "16", "cpu_5m": "17"}
            ],
            "agent_loop": True,
        },
    )

    assert result["success"] is True
    assert result["mode"] == "infer_and_validate"
    assert result["proposal"]["entity_type"] == "cpu"
    assert result["proposal"]["state_semantics"]["cpu_5s"]["unit"] == "percent"
    assert result["validation"]["record_count"] == 1
    assert "records" not in result
    assert result["agent_loop"]["ready_for_approval"] is True
    attempt = result["agent_loop"]["attempts"][0]
    assert attempt["stage"] == "observation_contract_generation"
    assert attempt["input_summary"]["record_count"] == 1


def test_observation_contract_generator_skill_validates_provided_proposal(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "mode": "validate",
            "command": "show inventory",
            "proposal": {
                "category": "inventory",
                "entity_type": "inventory_component",
                "primary_keys": ["component"],
                "required_fields": ["component", "name"],
                "optional_fields": ["pid", "serial"],
            },
            "records": [{"component": "chassis", "name": "main chassis"}],
        },
    )

    assert result["success"] is True
    assert result["proposal"]["source"] == "provided"
    assert result["validation"]["primary_key_fields"] == ["component"]


def test_observation_contract_generator_skill_returns_revision_request(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "mode": "infer_and_validate",
            "command": "display ip interface brief",
            "records": [
                {
                    "interface": "Interface1",
                    "ip_address": "10.0.0.1",
                    "status": "up",
                    "protocol": "up",
                },
                {
                    "interface": "Interface1",
                    "ip_address": "10.0.0.2",
                    "status": "down",
                    "protocol": "down",
                },
            ],
            "agent_loop": True,
        },
        expect_success_exit=False,
    )

    assert result["success"] is False
    assert result["agent_loop"]["ready_for_approval"] is False
    assert (
        result["agent_loop"]["revision_request"]["target_generator"]
        == "observation-contract-generator"
    )
    assert "choose stable non-empty primary keys" in result["agent_loop"][
        "revision_request"
    ]["required_fixes"]


def test_llm_contract_business_semantics_are_compiled_before_validation(monkeypatch):
    module = _load_skill_module()

    class VersionSemanticProvider:
        name = "version-semantic-model"

        def propose(
            self,
            *,
            stage,
            context,
            attempt,
            previous_candidate,
            validator_feedback,
        ):
            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "category": "software_inventory",
                    "entity_type": "slot",
                    "primary_keys": ["slot"],
                    "required_fields": ["slot", "software_version"],
                    "optional_fields": [],
                    "state_semantics": {
                        "slot": {"semantic_type": "identity"},
                        "software_version": {
                            "semantic_type": "software_version",
                            "observation_type": "slot.software_version",
                            "comparison_role": "observation",
                            "monitor": True,
                        },
                    },
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        VersionSemanticProvider,
    )
    records = [{"slot": "0", "software_version": "7.1.064, Release 0821P18"}]
    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "display version",
            "generation_provider": "llm",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": {
                "category": "software_inventory",
                "entity_type": "slot",
                "primary_keys": ["slot"],
                "required_fields": ["slot", "software_version"],
                "optional_fields": [],
            },
        }
    )

    assert result["success"] is True
    semantics = result["proposal"]["state_semantics"]["software_version"]
    assert semantics["semantic_type"] == "text"
    assert semantics["domain_semantic_type"] == "software_version"


def test_llm_contract_generation_preserves_multiple_parser_record_sets(monkeypatch):
    module = _load_skill_module()

    class MultiRecordProvider:
        name = "multi-record-model"

        def propose(
            self,
            *,
            stage,
            context,
            attempt,
            previous_candidate,
            validator_feedback,
        ):
            assert stage == "observation_contract"
            assert {item["name"] for item in context["record_sets"]} == {
                "device_version",
                "slot_inventory",
            }
            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "category": "multi_record",
                    "entity_type": "multi_record",
                    "primary_keys": [],
                    "required_fields": [],
                    "optional_fields": [],
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
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "event_generation": False,
                                    "event_role": "change_only",
                                },
                                "software_release": {
                                    "semantic_type": "text",
                                    "comparison_role": "metadata",
                                    "monitor": False,
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
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "event_generation": False,
                                    "event_role": "change_only",
                                },
                            },
                        },
                    ],
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        MultiRecordProvider,
    )
    records = [
        {
            "__record_set__": "device_version",
            "software_version": "7.1.064",
            "software_release": "0821P18",
        },
        {
            "__record_set__": "slot_inventory",
            "slot": "0",
            "board_type": "MPU-60",
        },
    ]
    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "display version",
            "generation_provider": "llm",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": {
                "category": "multi_record",
                "entity_type": "multi_record",
                "primary_keys": [],
                "required_fields": [],
                "optional_fields": [],
                "record_sets": [
                    {
                        "name": "device_version",
                        "entity_type": "device",
                        "primary_keys": [],
                        "required_fields": ["software_version"],
                        "optional_fields": ["software_release"],
                    },
                    {
                        "name": "slot_inventory",
                        "entity_type": "device_slot",
                        "primary_keys": ["slot"],
                        "required_fields": ["slot", "board_type"],
                        "optional_fields": [],
                    },
                ],
            },
        }
    )

    assert result["success"] is True
    assert result["proposal"]["entity_type"] == "multi_record"
    by_name = {
        item["name"]: item
        for item in result["proposal"]["record_sets"]
    }
    assert by_name["device_version"]["state_semantics"]["software_version"][
        "observation_type"
    ] == "device.software.version"
    assert list(by_name["slot_inventory"]["primary_keys"]) == ["slot"]


def test_manual_validation_handles_named_multi_record_contracts():
    module = _load_skill_module()
    records = [
        {
            "__record_set__": "interface_status",
            "interface_name": "GE1/0/1",
            "status": "up",
        },
        {
            "__record_set__": "measurements",
            "interface_name": "GE1/0/1",
            "rx_power": -3.2,
        },
    ]
    proposal = {
        "category": "multi_record",
        "entity_type": "multi_record",
        "primary_keys": [],
        "required_fields": [],
        "optional_fields": [],
        "record_sets": [
            {
                "name": "interface_status",
                "entity_type": "interface_transceiver",
                "primary_keys": ["interface_name"],
                "required_fields": ["interface_name", "status"],
                "optional_fields": [],
                "validators": {
                    "interface_name": {"type": "interface_name"},
                    "status": {"type": "text"},
                },
                "state_semantics": {
                    "interface_name": {"semantic_type": "identity"},
                    "status": {"semantic_type": "state"},
                },
            },
            {
                "name": "measurements",
                "entity_type": "interface_measurement",
                "primary_keys": ["interface_name"],
                "required_fields": ["interface_name", "rx_power"],
                "optional_fields": [],
                "validators": {
                    "interface_name": {"type": "interface_name"},
                    "rx_power": {"type": "number"},
                },
                "state_semantics": {
                    "interface_name": {"semantic_type": "identity"},
                    "rx_power": {"semantic_type": "gauge"},
                },
            },
        ],
    }

    result = module.run_contract_generation(
        {
            "mode": "validate",
            "command": "display transceiver diagnosis interface",
            "generation_provider": "manual",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": proposal,
            "allow_semantic_overrides": True,
        }
    )

    assert result["success"] is True
    assert result["mode"] == "validate"
    assert {item["name"] for item in result["proposal"]["record_sets"]} == {
        "interface_status",
        "measurements",
    }


def test_multi_record_contract_applies_global_targets_to_evidenced_sets(monkeypatch):
    module = _load_skill_module()

    class OpticalBundleProvider:
        name = "optical-bundle-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "category": "multi_record",
                    "entity_type": "multi_record",
                    "primary_keys": [],
                    "required_fields": [],
                    "optional_fields": [],
                    "record_sets": [
                        {
                            "name": "interface_status",
                            "canonical_entity_type": "interface_optical",
                            "entity_identity_fields": ["interface", "channel"],
                            "entity_dimensions": {
                                "interface": {"field": "interface_name"},
                                "channel": {"literal": "module"},
                            },
                            "state_semantics": {
                                "interface_name": {
                                    "semantic_type": "identity",
                                    "comparison_role": "identity",
                                    "monitor": False,
                                },
                                "status": {
                                    "semantic_type": "state",
                                    "observation_type": "interface.optical.presence",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "dimensions": {
                                        "channel": {"literal": "module"},
                                    },
                                    "value_mapping": {
                                        "Current diagnostic parameters:": "present",
                                        "The transceiver is absent.": "absent",
                                    },
                                    "on_unmapped": "reject",
                                },
                            },
                        },
                        {
                            "name": "module_measurements",
                            "canonical_entity_type": "interface_optical",
                            "entity_identity_fields": ["interface", "channel"],
                            "entity_dimensions": {
                                "interface": {"field": "interface_name"},
                                "channel": {"literal": "module"},
                            },
                            "state_semantics": {
                                "interface_name": {
                                    "semantic_type": "identity",
                                    "comparison_role": "identity",
                                    "monitor": False,
                                },
                                "rx_power": {
                                    "semantic_type": "gauge",
                                    "unit": "dbm",
                                    "canonical_unit": "dbm",
                                    "observation_type": "interface.optical.rx_power",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "dimensions": {
                                        "channel": {"literal": "module"},
                                    },
                                },
                                "tx_power": {
                                    "semantic_type": "gauge",
                                    "unit": "dbm",
                                    "canonical_unit": "dbm",
                                    "observation_type": "interface.optical.tx_power",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "dimensions": {
                                        "channel": {"literal": "module"},
                                    },
                                },
                            },
                        },
                    ],
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        OpticalBundleProvider,
    )
    records = [
        {
            "__record_set__": "interface_status",
            "interface_name": "XGE1/0/1",
            "status": "Current diagnostic parameters:",
        },
        {
            "__record_set__": "interface_status",
            "interface_name": "XGE1/0/2",
            "status": "The transceiver is absent.",
        },
        {
            "__record_set__": "module_measurements",
            "interface_name": "XGE1/0/1",
            "rx_power": "-2.50",
            "tx_power": "-1.99",
        },
    ]
    proposal = {
        "category": "multi_record",
        "entity_type": "multi_record",
        "primary_keys": [],
        "required_fields": [],
        "optional_fields": [],
        "record_sets": [
            {
                "name": "interface_status",
                "entity_type": "interface_transceiver",
                "cardinality": "repeated",
                "presence": "required",
                "primary_keys": ["interface_name"],
                "required_fields": ["interface_name", "status"],
                "optional_fields": [],
            },
            {
                "name": "module_measurements",
                "entity_type": "module_diagnostic",
                "cardinality": "repeated",
                "presence": "optional",
                "primary_keys": ["interface_name"],
                "required_fields": ["interface_name", "rx_power", "tx_power"],
                "optional_fields": [],
            },
        ],
    }
    targets = [
        {
            "canonical_field": "transceiver_present",
            "observation_type": "interface.optical.presence",
            "semantic_type": "state",
            "dimensions": ["channel"],
            "allowed_values": ["present", "absent", "unknown"],
            "required": True,
        },
        {
            "canonical_field": "rx_power_dbm",
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "unit": "dbm",
            "dimensions": ["channel"],
            "required": True,
        },
        {
            "canonical_field": "tx_power_dbm",
            "observation_type": "interface.optical.tx_power",
            "semantic_type": "gauge",
            "unit": "dbm",
            "dimensions": ["channel"],
            "required": True,
        },
    ]

    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "display transceiver diagnosis interface",
            "generation_provider": "llm",
            "max_agent_attempts": 1,
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": proposal,
            "observation_targets": targets,
            "strict_observation_targets": True,
            "canonical_identity_constraint": {
                "canonical_entity_type": "interface_optical",
                "identity_dimensions": ["interface", "channel"],
            },
        }
    )

    assert result["success"] is True
    by_name = {
        item["name"]: item for item in result["proposal"]["record_sets"]
    }
    assert by_name["interface_status"]["presence"] == "required"
    assert by_name["module_measurements"]["presence"] == "optional"
    assert set(
        result["proposal"]["evidence"]["multi_record_target_governance"][
            "mapped"
        ]
    ) == {
        "interface.optical.presence",
        "interface.optical.rx_power",
        "interface.optical.tx_power",
    }
    assert "rx_power" not in by_name["interface_status"]["state_semantics"]


def test_multi_record_target_uses_record_set_identity_dimensions(monkeypatch):
    module = _load_skill_module()

    class ModuleAndLaneProvider:
        name = "module-and-lane-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "category": "multi_record",
                    "entity_type": "multi_record",
                    "record_sets": [
                        {
                            "name": "module_diagnostics",
                            "canonical_entity_type": "interface_optical",
                            "entity_identity_fields": ["interface", "channel"],
                            "entity_dimensions": {
                                "interface": {"field": "interface"},
                                "channel": {"literal": "0"},
                            },
                            "state_semantics": {
                                "rx_power_dbm": {
                                    "semantic_type": "gauge",
                                    "unit": "dbm",
                                    "observation_type": "interface.optical.rx_power",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                }
                            },
                        },
                        {
                            "name": "lane_diagnostics",
                            "canonical_entity_type": "interface_optical",
                            "entity_identity_fields": ["interface", "channel"],
                            "entity_dimensions": {
                                "interface": {"field": "interface"},
                                "channel": {"field": "channel"},
                            },
                            "state_semantics": {
                                "rx_power_dbm": {
                                    "semantic_type": "gauge",
                                    "unit": "dbm",
                                    "observation_type": "interface.optical.rx_power",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                }
                            },
                        },
                    ],
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        ModuleAndLaneProvider,
    )
    records = [
        {
            "__record_set__": "module_diagnostics",
            "interface": "Eth1/1",
            "rx_power_dbm": -4.2,
        },
        {
            "__record_set__": "lane_diagnostics",
            "interface": "Eth1/2",
            "channel": "1",
            "rx_power_dbm": -3.9,
        },
    ]
    seed = {
        "category": "multi_record",
        "entity_type": "multi_record",
        "record_sets": [
            {
                "name": "module_diagnostics",
                "entity_type": "interface_optical",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "rx_power_dbm"],
                "optional_fields": [],
                "validators": {
                    "interface": {"type": "interface_name"},
                    "rx_power_dbm": {"type": "number"},
                },
            },
            {
                "name": "lane_diagnostics",
                "entity_type": "interface_optical_channel",
                "primary_keys": ["interface", "channel"],
                "required_fields": ["interface", "channel", "rx_power_dbm"],
                "optional_fields": [],
                "validators": {
                    "interface": {"type": "interface_name"},
                    "channel": {"type": "text"},
                    "rx_power_dbm": {"type": "number"},
                },
            },
        ],
    }

    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "show interface transceiver detail",
            "generation_provider": "llm",
            "max_agent_attempts": 1,
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": seed,
            "observation_targets": [
                {
                    "canonical_field": "rx_power_dbm",
                    "observation_type": "interface.optical.rx_power",
                    "semantic_type": "gauge",
                    "unit": "dbm",
                    "required": True,
                }
            ],
            "strict_observation_targets": True,
            "canonical_identity_constraint": {
                "canonical_entity_type": "interface_optical",
                "identity_dimensions": ["interface", "channel"],
            },
        }
    )

    assert result["success"] is True, result.get("validation")
    governance = result["proposal"]["evidence"][
        "multi_record_target_governance"
    ]
    assert governance["duplicate_bindings"] == {}
    assert governance["mapped"] == ["interface.optical.rx_power"]


def test_multi_record_contract_assigns_canonical_target_to_one_record_set(monkeypatch):
    module = _load_skill_module()

    class FanBundleProvider:
        name = "fan-bundle-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "category": "multi_record",
                    "entity_type": "multi_record",
                    "primary_keys": [],
                    "required_fields": [],
                    "optional_fields": [],
                    "record_sets": [
                        {
                            "name": "fans",
                            "state_semantics": {
                                "fan": {
                                    "semantic_type": "identity",
                                    "comparison_role": "identity",
                                    "monitor": False,
                                },
                                "fan_status": {
                                    "semantic_type": "state",
                                    "observation_type": "environment.fan.state",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "value_mapping": {"Ok": "normal"},
                                    "on_unmapped": "reject",
                                },
                            },
                        },
                        {
                            "name": "fan_environment_summary",
                            "state_semantics": {
                                "air_filter_status": {
                                    "semantic_type": "state",
                                    "observation_type": "environment.air_filter.state",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "value_mapping": {
                                        "NotSupported": "not_supported"
                                    },
                                    "on_unmapped": "reject",
                                }
                            },
                        },
                    ],
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        FanBundleProvider,
    )
    records = [
        {"__record_set__": "fans", "fan": "Fan1", "fan_status": "Ok"},
        {
            "__record_set__": "fan_environment_summary",
            "air_filter_status": "NotSupported",
        },
    ]

    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "show environment fan",
            "generation_provider": "llm",
            "max_agent_attempts": 1,
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": {
                "category": "multi_record",
                "entity_type": "multi_record",
                "primary_keys": [],
                "required_fields": [],
                "optional_fields": [],
                "record_sets": [
                    {
                        "name": "fans",
                        "entity_type": "fan",
                        "cardinality": "repeated",
                        "presence": "required",
                        "primary_keys": ["fan"],
                        "required_fields": ["fan", "fan_status"],
                        "optional_fields": [],
                        "validators": {
                            "fan": {"type": "text"},
                            "fan_status": {"type": "text"},
                        },
                    },
                    {
                        "name": "fan_environment_summary",
                        "entity_type": "device",
                        "cardinality": "singleton",
                        "presence": "required",
                        "primary_keys": [],
                        "required_fields": ["air_filter_status"],
                        "optional_fields": [],
                        "validators": {
                            "air_filter_status": {"type": "text"}
                        },
                    },
                ],
            },
            "observation_targets": [
                {
                    "canonical_field": "fan_status",
                    "observation_type": "environment.fan.state",
                    "semantic_type": "state",
                    "required": True,
                }
            ],
            "strict_observation_targets": True,
        }
    )

    assert result["success"] is True
    by_name = {
        item["name"]: item for item in result["proposal"]["record_sets"]
    }
    assert by_name["fans"]["state_semantics"]["fan_status"][
        "observation_type"
    ] == "environment.fan.state"
    summary_state = by_name["fan_environment_summary"]["state_semantics"][
        "air_filter_status"
    ]
    assert "observation_type" not in summary_state
    assert summary_state["monitor"] is False
    governance = result["proposal"]["evidence"][
        "multi_record_target_governance"
    ]
    assert governance["deterministic_owners"] == {
        "environment.fan.state": "fans"
    }
    assert governance["duplicate_bindings"] == {}


def test_multi_record_contract_applies_distinct_check_item_identities(monkeypatch):
    module = _load_skill_module()

    class SystemResourcesProvider:
        name = "system-resources-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "category": "multi_record",
                    "entity_type": "multi_record",
                    "primary_keys": [],
                    "required_fields": [],
                    "optional_fields": [],
                    "record_sets": [
                        {
                            "name": "cpu_cores",
                            "state_semantics": {
                                "cpu_id": {
                                    "semantic_type": "identity",
                                    "comparison_role": "identity",
                                    "monitor": False,
                                },
                                "cpu_idle_percent": {
                                    "semantic_type": "gauge",
                                    "comparison_role": "metadata",
                                    "monitor": False,
                                    "transform": {
                                        "op": "subtract",
                                        "args": [
                                            {"literal": 100},
                                            {"field": "cpu_idle_percent"},
                                        ],
                                    },
                                    "dimensions": {
                                        "scope": {"literal": "processor"},
                                        "scope_id": {"field": "cpu_id"},
                                        "window": {"literal": "instant"},
                                    },
                                },
                            },
                        },
                        {
                            "name": "system_resources",
                            # Direct governed memory fields must override a weak
                            # model's classification of this mixed record set.
                            "canonical_entity_type": "cpu_scope",
                            "entity_dimensions": {
                                "scope": {"context": "device_id"},
                                "scope_id": {"context": "device_id"},
                            },
                            "state_semantics": {
                                "aggregate_cpu_idle_percent": {
                                    "semantic_type": "gauge",
                                    "comparison_role": "metadata",
                                    "monitor": False,
                                    "transform": {
                                        "op": "subtract",
                                        "args": [
                                            {"literal": 100},
                                            {"field": "aggregate_cpu_idle_percent"},
                                        ],
                                    },
                                },
                                "memory_total_kb": {
                                    "semantic_type": "gauge",
                                    "observation_type": "device.memory.total",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "transform": {
                                        "op": "multiply",
                                        "args": [
                                            {"field": "memory_total_kb"},
                                            {"literal": 1024},
                                        ],
                                    },
                                    "dimensions": {
                                        "scope": {"literal": "system"},
                                        "scope_id": {"context": "device_id"},
                                    },
                                },
                                "memory_used_kb": {
                                    "semantic_type": "gauge",
                                    "observation_type": "device.memory.used",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "transform": {
                                        "op": "multiply",
                                        "args": [
                                            {"field": "memory_used_kb"},
                                            {"literal": 1024},
                                        ],
                                    },
                                    "dimensions": {
                                        "scope": {"literal": "system"},
                                        "scope_id": {"context": "device_id"},
                                    },
                                },
                                "memory_free_kb": {
                                    "semantic_type": "gauge",
                                    "comparison_role": "metadata",
                                    "monitor": False,
                                    "transform": {
                                        "op": "multiply",
                                        "args": [
                                            {"field": "memory_free_kb"},
                                            {"literal": 1024},
                                        ],
                                    },
                                    "dimensions": {
                                        "scope": {"literal": "system"},
                                        "scope_id": {"context": "device_id"},
                                    },
                                },
                            },
                        },
                    ],
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        SystemResourcesProvider,
    )
    records = [
        {
            "__record_set__": "cpu_cores",
            "cpu_id": "CPU0",
            "cpu_idle_percent": "95.0",
        },
        {
            "__record_set__": "system_resources",
            "aggregate_cpu_idle_percent": "90.0",
            "memory_total_kb": "1000",
            "memory_used_kb": "250",
            "memory_free_kb": "750",
        },
    ]
    seed = {
        "category": "multi_record",
        "entity_type": "multi_record",
        "record_sets": [
            {
                "name": "cpu_cores",
                "entity_type": "cpu_core",
                "primary_keys": ["cpu_id"],
                "required_fields": ["cpu_id", "cpu_idle_percent"],
                "optional_fields": [],
                "validators": {
                    "cpu_id": {"type": "text"},
                    "cpu_idle_percent": {"type": "number"},
                },
            },
            {
                "name": "system_resources",
                "entity_type": "system_resources",
                "primary_keys": [],
                "required_fields": [
                    "aggregate_cpu_idle_percent",
                    "memory_total_kb",
                    "memory_used_kb",
                    "memory_free_kb",
                ],
                "optional_fields": [],
                "validators": {
                    "aggregate_cpu_idle_percent": {"type": "number"},
                    "memory_total_kb": {"type": "number"},
                    "memory_used_kb": {"type": "number"},
                    "memory_free_kb": {"type": "number"},
                },
            },
        ],
    }
    scope_rules = {
        "scope": {
            "kind": "enum",
            "allowed_values": ["system", "slot", "processor"],
        },
        "scope_id": {"kind": "identifier"},
    }
    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "show system resources",
            "generation_provider": "llm",
            "max_agent_attempts": 1,
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": seed,
            "observation_targets": [
                {
                    "canonical_field": "cpu_usage_percent",
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "dimensions": ["window"],
                    "required": True,
                    "projection_recipes": [
                        {
                            "id": "utilization_from_idle_percent",
                            "bindings": {
                                "idle": {
                                    "canonical_fields": ["cpu_idle_percent"]
                                }
                            },
                            "anchor": "idle",
                            "transform": {
                                "op": "subtract",
                                "args": [
                                    {"literal": 100},
                                    {"binding": "idle"},
                                ],
                            },
                            "dimensions": {"window": "instant"},
                        }
                    ],
                    "canonical_entity_type": "cpu_scope",
                    "identity_dimensions": ["scope", "scope_id"],
                    "identity_dimension_rules": scope_rules,
                },
                {
                    "canonical_field": "memory_usage_percent",
                    "observation_type": "device.memory.utilization",
                    "semantic_type": "gauge",
                    "dimensions": [],
                    "required": True,
                    "projection_recipes": [
                        {
                            "id": "utilization_from_used_and_total",
                            "bindings": {
                                "used": {
                                    "canonical_fields": ["memory_used_bytes"]
                                },
                                "total": {
                                    "canonical_fields": ["memory_total_bytes"]
                                },
                                "free": {
                                    "canonical_fields": ["memory_free_bytes"]
                                },
                            },
                            "anchor": "free",
                            "transform": {
                                "op": "multiply",
                                "args": [
                                    {"literal": 100},
                                    {
                                        "op": "divide",
                                        "args": [
                                            {"binding": "used"},
                                            {"binding": "total"},
                                        ],
                                    },
                                ],
                            },
                        }
                    ],
                    "canonical_entity_type": "memory_scope",
                    "identity_dimensions": ["scope", "scope_id"],
                    "identity_dimension_rules": scope_rules,
                },
                {
                    "canonical_field": "memory_total_bytes",
                    "observation_type": "device.memory.total",
                    "semantic_type": "gauge",
                    "required": True,
                    "canonical_entity_type": "memory_scope",
                    "identity_dimensions": ["scope", "scope_id"],
                    "identity_dimension_rules": scope_rules,
                },
                {
                    "canonical_field": "memory_used_bytes",
                    "observation_type": "device.memory.used",
                    "semantic_type": "gauge",
                    "required": True,
                    "canonical_entity_type": "memory_scope",
                    "identity_dimensions": ["scope", "scope_id"],
                    "identity_dimension_rules": scope_rules,
                },
            ],
            "strict_observation_targets": True,
        }
    )

    assert result["success"] is True, result.get("validation")
    by_name = {
        item["name"]: item for item in result["proposal"]["record_sets"]
    }
    assert by_name["cpu_cores"]["canonical_entity_type"] == "cpu_scope"
    assert by_name["cpu_cores"]["entity_dimensions"] == {
        "scope": {"literal": "processor"},
        "scope_id": {"field": "cpu_id"},
    }
    assert by_name["system_resources"]["canonical_entity_type"] == "memory_scope"
    assert by_name["system_resources"]["entity_dimensions"] == {
        "scope": {"literal": "system"},
        "scope_id": {"context": "device_id"},
    }
    assert by_name["cpu_cores"]["state_semantics"]["cpu_idle_percent"][
        "dimensions"
    ] == {"window": {"literal": "instant"}}
    assert by_name["cpu_cores"]["state_semantics"]["cpu_idle_percent"][
        "observation_type"
    ] == "device.cpu.utilization"
    assert by_name["system_resources"]["state_semantics"]["memory_free_kb"][
        "observation_type"
    ] == "device.memory.utilization"
    aggregate_cpu = by_name["system_resources"]["state_semantics"][
        "aggregate_cpu_idle_percent"
    ]
    assert "observation_type" not in aggregate_cpu
    assert aggregate_cpu["monitor"] is False
    assert by_name["system_resources"]["state_semantics"]["memory_free_kb"][
        "transform"
    ] == {
        "op": "multiply",
        "args": [
            {"literal": 100},
            {
                "op": "divide",
                "args": [
                    {"field": "memory_used_kb"},
                    {"field": "memory_total_kb"},
                ],
            },
        ],
    }
    assert by_name["cpu_cores"]["evidence"]["governed_projection_recipes"] == [
        {
            "observation_type": "device.cpu.utilization",
            "recipe_id": "utilization_from_idle_percent",
            "anchor_field": "cpu_idle_percent",
            "bindings": {"idle": "cpu_idle_percent"},
        }
    ]
    assert result["proposal"]["evidence"][
        "multi_record_target_governance"
    ]["deterministic_owners"] == {
        "device.cpu.utilization": "cpu_cores",
        "device.memory.total": "system_resources",
        "device.memory.used": "system_resources",
        "device.memory.utilization": "system_resources",
    }
    for field in ("memory_total_kb", "memory_used_kb", "memory_free_kb"):
        assert "dimensions" not in by_name["system_resources"][
            "state_semantics"
        ][field]
    assert by_name["system_resources"]["evidence"][
        "redundant_identity_dimensions_normalized"
    ]["fields"] == {
        "memory_free_kb": ["scope", "scope_id"],
        "memory_total_kb": ["scope", "scope_id"],
        "memory_used_kb": ["scope", "scope_id"],
    }


def test_multi_record_contract_fails_when_global_required_target_is_unmapped(
    monkeypatch,
):
    module = _load_skill_module()

    class MissingPresenceProvider:
        name = "missing-presence-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "category": "multi_record",
                    "entity_type": "multi_record",
                    "primary_keys": [],
                    "required_fields": [],
                    "optional_fields": [],
                    "record_sets": [
                        {
                            "name": "interface_status",
                            "state_semantics": {
                                "interface_name": {
                                    "semantic_type": "identity",
                                    "comparison_role": "identity",
                                    "monitor": False,
                                },
                                "status": {
                                    "semantic_type": "text",
                                    "comparison_role": "metadata",
                                    "monitor": False,
                                },
                            },
                        }
                    ],
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        MissingPresenceProvider,
    )
    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "display transceiver diagnosis interface",
            "generation_provider": "llm",
            "max_agent_attempts": 1,
            "records": [
                {
                    "__record_set__": "interface_status",
                    "interface_name": "XGE1/0/1",
                    "status": "The transceiver is absent.",
                }
            ],
            "record_groups": [
                {
                    "group_id": "snapshot-1",
                    "records": [
                        {
                            "__record_set__": "interface_status",
                            "interface_name": "XGE1/0/1",
                            "status": "The transceiver is absent.",
                        }
                    ],
                }
            ],
            "proposal": {
                "category": "multi_record",
                "entity_type": "multi_record",
                "primary_keys": [],
                "required_fields": [],
                "optional_fields": [],
                "record_sets": [
                    {
                        "name": "interface_status",
                        "entity_type": "interface_transceiver",
                        "primary_keys": ["interface_name"],
                        "required_fields": ["interface_name", "status"],
                        "optional_fields": [],
                    }
                ],
            },
            "observation_targets": [
                {
                    "canonical_field": "transceiver_present",
                    "observation_type": "interface.optical.presence",
                    "semantic_type": "state",
                    "required": True,
                }
            ],
            "strict_observation_targets": True,
        }
    )

    assert result["success"] is False
    assert any(
        "does not cover required observation targets: "
        "interface.optical.presence" in error
        for error in result["validation"]["errors"]
    )


def test_multi_record_identity_constraint_only_applies_to_target_record_set(
    monkeypatch,
):
    module = _load_skill_module()

    class VpcBundleProvider:
        name = "vpc-bundle-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                provider=self.name,
                model_name="mock",
                candidate={
                    "category": "multi_record",
                    "entity_type": "multi_record",
                    "primary_keys": [],
                    "required_fields": [],
                    "optional_fields": [],
                    "record_sets": [
                        {
                            "name": "vpc_global",
                            "canonical_entity_type": "mlag_domain",
                            "entity_identity_fields": ["domain_id"],
                            "entity_dimensions": {
                                "domain_id": {"field": "domain_id"},
                            },
                            "state_semantics": {
                                "domain_id": {
                                    "semantic_type": "identity",
                                    "comparison_role": "identity",
                                    "monitor": False,
                                },
                                "peer_status": {
                                    "semantic_type": "state",
                                    "observation_type": "redundancy.mlag.state",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "value_mapping": {
                                        "peer adjacency formed ok": "up",
                                    },
                                    "on_unmapped": "reject",
                                },
                            },
                        },
                        {
                            "name": "vpc_members",
                            "state_semantics": {
                                "id": {
                                    "semantic_type": "identity",
                                    "comparison_role": "identity",
                                    "monitor": False,
                                },
                                "status": {
                                    "semantic_type": "state",
                                    "comparison_role": "metadata",
                                    "monitor": False,
                                },
                            },
                        },
                    ],
                },
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        VpcBundleProvider,
    )
    records = [
        {
            "__record_set__": "vpc_global",
            "domain_id": "300",
            "peer_status": "peer adjacency formed ok",
        },
        {
            "__record_set__": "vpc_members",
            "id": "400",
            "status": "up",
        },
        {
            "__record_set__": "vpc_members",
            "id": "401",
            "status": "up",
        },
    ]
    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "show vpc",
            "generation_provider": "llm",
            "max_agent_attempts": 1,
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": {
                "category": "multi_record",
                "entity_type": "multi_record",
                "primary_keys": [],
                "required_fields": [],
                "optional_fields": [],
                "record_sets": [
                    {
                        "name": "vpc_global",
                        "entity_type": "vpc_domain",
                        "cardinality": "singleton",
                        "presence": "required",
                        "primary_keys": [],
                        "required_fields": ["domain_id", "peer_status"],
                        "optional_fields": [],
                    },
                    {
                        "name": "vpc_members",
                        "entity_type": "vpc_port_channel",
                        "cardinality": "repeated",
                        "presence": "required",
                        "primary_keys": ["id"],
                        "required_fields": ["id", "status"],
                        "optional_fields": [],
                    },
                ],
            },
            "observation_targets": [
                {
                    "observation_type": "redundancy.mlag.state",
                    "semantic_type": "state",
                    "canonical_description": (
                        "Current operating state of one multi-chassis link "
                        "aggregation domain."
                    ),
                    "required": True,
                }
            ],
            "strict_observation_targets": True,
            "canonical_identity_constraint": {
                "canonical_entity_type": "mlag_domain",
                "identity_dimensions": ["domain_id"],
            },
        }
    )

    assert result["success"] is True
    by_name = {
        item["name"]: item for item in result["proposal"]["record_sets"]
    }
    assert by_name["vpc_global"]["canonical_entity_type"] == "mlag_domain"
    assert not by_name["vpc_members"].get("canonical_entity_type")
    assert by_name["vpc_members"]["primary_keys"] == ["id"]


def test_contract_validation_scopes_primary_key_uniqueness_to_snapshot(tmp_path: Path):
    proposal = {
        "category": "interface",
        "entity_type": "interface_l3",
        "primary_keys": ["interface"],
        "required_fields": ["interface", "status", "protocol"],
        "optional_fields": [],
    }
    first = {"interface": "GE1/0/1", "status": "up", "protocol": "up"}
    second = {"interface": "GE1/0/1", "status": "down", "protocol": "down"}

    result = _run_skill(
        tmp_path,
        {
            "mode": "validate",
            "command": "display ip interface brief",
            "proposal": proposal,
            "records": [first, second],
            "record_groups": [
                {"group_id": "snapshot-a", "records": [first]},
                {"group_id": "snapshot-b", "records": [second]},
            ],
        },
    )

    assert result["success"] is True
    assert result["validation"]["snapshot_group_count"] == 2
    assert result["validation"]["duplicate_key_count"] == 0


def test_contract_validation_still_rejects_duplicates_inside_snapshot(tmp_path: Path):
    duplicate = {"interface": "GE1/0/1", "status": "up", "protocol": "up"}
    result = _run_skill(
        tmp_path,
        {
            "mode": "validate",
            "command": "display ip interface brief",
            "proposal": {
                "category": "interface",
                "entity_type": "interface_l3",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "status", "protocol"],
                "optional_fields": [],
            },
            "records": [duplicate, duplicate],
            "record_groups": [
                {"group_id": "snapshot-a", "records": [duplicate, duplicate]}
            ],
        },
        expect_success_exit=False,
    )

    assert result["success"] is False
    assert result["validation"]["duplicate_key_count"] == 1


def test_llm_loop_cannot_replace_approved_parser_contract(monkeypatch):
    module = _load_skill_module()

    class WeakThenCorrectProvider:
        name = "weak-test-model"

        def propose(
            self,
            *,
            stage,
            context,
            attempt,
            previous_candidate,
            validator_feedback,
        ):
            assert stage == "observation_contract"
            if attempt == 1:
                candidate = {
                    "category": "interface_status",
                    "entity_type": "interface_status",
                    "primary_keys": ["status"],
                    "required_fields": ["interface"],
                    "optional_fields": ["status"],
                    "state_semantics": {
                        "interface": {"semantic_type": "identity"},
                        "status": {
                            "semantic_type": "state",
                            "observation_type": "interface/status",
                        },
                    },
                }
            else:
                assert validator_feedback and validator_feedback.get("errors")
                candidate = {
                    "category": "interface_oper",
                    "entity_type": "interface",
                    "primary_keys": ["interface"],
                    "required_fields": ["interface", "status"],
                    "optional_fields": ["protocol"],
                    "state_semantics": {
                        "interface": {"semantic_type": "identity"},
                        "status": {
                            "semantic_type": "state",
                            "observation_type": "interface.oper_status",
                        },
                        "protocol": {
                            "semantic_type": "state",
                            "comparison_role": "metadata",
                            "monitor": False,
                        },
                    },
                }
            return ProviderResult(
                candidate=candidate,
                provider=self.name,
                model_name="mock",
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        WeakThenCorrectProvider,
    )
    records = [
        {"interface": "GE1/0/1", "status": "UP", "protocol": "UP"}
    ]
    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "display interface brief",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": {
                "category": "interface_oper",
                "entity_type": "interface",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "status"],
                "optional_fields": ["protocol"],
                "state_semantics": {
                    "interface": {"semantic_type": "identity"},
                    "status": {
                        "semantic_type": "state",
                        "observation_type": "interface.oper_status",
                    },
                    "protocol": {
                        "semantic_type": "state",
                        "comparison_role": "metadata",
                        "monitor": False,
                    },
                },
            },
            "generation_provider": "llm",
            "max_agent_attempts": 3,
        }
    )

    assert result["success"] is True
    assert result["proposal"]["entity_type"] == "interface"
    assert list(result["proposal"]["primary_keys"]) == ["interface"]
    assert result["proposal"]["state_semantics"]["status"]["observation_type"] == (
        "interface.oper_status"
    )
    assert result["agent_loop"]["attempt_count"] == 1


def test_llm_loop_requires_explicit_policy_target_decisions(monkeypatch):
    module = _load_skill_module()

    seed = {
        "category": "network",
        "entity_type": "interface_brief",
        "primary_keys": ["interface"],
        "required_fields": ["interface", "link"],
        "optional_fields": ["protocol", "speed"],
        "state_semantics": {
            "interface": {"semantic_type": "identity"},
            "link": {"semantic_type": "state"},
            "protocol": {"semantic_type": "state"},
            "speed": {"semantic_type": "gauge"},
        },
    }

    class CopyThenDecideProvider:
        name = "weak-test-model"

        def propose(
            self,
            *,
            stage,
            context,
            attempt,
            previous_candidate,
            validator_feedback,
        ):
            assert stage == "observation_contract"
            if attempt == 1:
                candidate = seed
            else:
                assert any(
                    "must set observation_type" in error
                    for error in validator_feedback["errors"]
                )
                candidate = {
                    **seed,
                    "state_semantics": {
                        "interface": {"semantic_type": "identity"},
                        "link": {
                            "semantic_type": "state",
                            "comparison_role": "observation",
                            "monitor": True,
                            "observation_type": "interface.oper_status",
                        },
                        "protocol": {
                            "semantic_type": "state",
                            "comparison_role": "metadata",
                            "monitor": False,
                        },
                        "speed": {
                            "semantic_type": "gauge",
                            "comparison_role": "metadata",
                            "monitor": False,
                        },
                    },
                }
            return ProviderResult(candidate=candidate, provider=self.name, model_name="mock")

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        CopyThenDecideProvider,
    )
    records = [
        {"interface": "GE1/0/1", "link": "UP", "protocol": "UP", "speed": "10G"},
        {"interface": "GE1/0/2", "link": "DOWN", "protocol": "DOWN", "speed": "10G"},
    ]

    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "display interface brief",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": seed,
            "generation_provider": "llm",
            "max_agent_attempts": 3,
        }
    )

    assert result["success"] is True
    assert result["agent_loop"]["attempt_count"] == 2
    assert result["proposal"]["state_semantics"]["link"]["observation_type"] == (
        "interface.oper_status"
    )
    assert result["proposal"]["state_semantics"]["protocol"]["monitor"] is False


def test_llm_loop_inherits_approved_parser_schema_when_model_keeps_rewriting_it(monkeypatch):
    module = _load_skill_module()

    class SemanticOnlyProvider:
        name = "weak-test-model"

        def propose(
            self,
            *,
            stage,
            context,
            attempt,
            previous_candidate,
            validator_feedback,
        ):
            assert stage == "observation_contract"
            return ProviderResult(
                candidate={
                    "category": "invented_category",
                    "entity_type": "invented_entity",
                    "primary_keys": ["status"],
                    "required_fields": ["interface"],
                    "optional_fields": ["status"],
                    "validators": {"ip_address": {"type": "text"}},
                    "state_semantics": {
                        "interface": {"semantic_type": "identity"},
                        "status": {
                            "semantic_type": "state",
                            "observation_type": "interface.oper_status",
                        },
                        "protocol": {
                            "semantic_type": "state",
                            "comparison_role": "metadata",
                            "monitor": False,
                        },
                        "ip_address": {
                            "semantic_type": "text",
                            "comparison_role": "metadata",
                            "monitor": False,
                        },
                    },
                },
                provider=self.name,
                model_name="mock",
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        SemanticOnlyProvider,
    )
    records = [
        {
            "interface": "GE1/0/1",
            "status": "UP",
            "protocol": "UP",
            "ip_address": "192.0.2.1",
        }
    ]
    seed = {
        "category": "interface_oper",
        "entity_type": "interface",
        "primary_keys": ["interface"],
        "required_fields": ["interface", "status"],
        "optional_fields": ["protocol", "ip_address"],
        "validators": {
            "ip_address": {"type": "regex", "pattern": "^\\d+(?:\\.\\d+){3}$"}
        },
        "allow_empty": False,
        "empty_patterns": [],
        "ignore_fields": [],
        "state_semantics": {
            "interface": {"semantic_type": "identity"},
            "status": {
                "semantic_type": "state",
                "observation_type": "interface.oper_status",
            },
        },
    }

    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "inspect arbitrary interfaces",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": seed,
            "generation_provider": "llm",
            "max_agent_attempts": 3,
        }
    )

    assert result["success"] is True
    assert result["agent_loop"]["attempt_count"] == 1
    assert result["proposal"]["category"] == seed["category"]
    assert result["proposal"]["entity_type"] == seed["entity_type"]
    assert list(result["proposal"]["primary_keys"]) == seed["primary_keys"]
    assert set(result["proposal"]["required_fields"]) == set(seed["required_fields"])
    assert result["proposal"]["validators"] == seed["validators"]
    governance = result["proposal"]["evidence"]["asset_generation_governance"]
    assert governance["schema_source"] == "approved_parser_contract"
    assert "validators" in governance["immutable_fields"]


def test_llm_loop_binds_policy_owned_observation_namespace(monkeypatch):
    module = _load_skill_module()

    class FreeNamingProvider:
        name = "weak-test-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                candidate={
                    "category": "network",
                    "entity_type": "interface",
                    "primary_keys": ["interface"],
                    "required_fields": ["interface", "link"],
                    "optional_fields": ["protocol"],
                    "state_semantics": {
                        "interface": {"semantic_type": "identity"},
                        "link": {
                            "semantic_type": "state",
                            "observation_type": "interface.link_state",
                            "monitor": True,
                        },
                        "protocol": {
                            "semantic_type": "state",
                            "observation_type": "interface.protocol_state",
                            "monitor": True,
                        },
                    },
                },
                provider=self.name,
                model_name="mock",
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        FreeNamingProvider,
    )
    records = [{"interface": "GE1/0/1", "link": "UP", "protocol": "UP"}]
    seed = {
        "category": "network",
        "entity_type": "interface",
        "primary_keys": ["interface"],
        "required_fields": ["interface", "link"],
        "optional_fields": ["protocol"],
        "validators": {},
        "state_semantics": {
            "interface": {"semantic_type": "identity"},
            "link": {"semantic_type": "state"},
            "protocol": {"semantic_type": "state"},
        },
    }

    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "inspect arbitrary interfaces",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": seed,
            "observation_targets": [
                {
                    "canonical_field": "status",
                    "observation_type": "interface.oper_status",
                }
            ],
            "strict_observation_targets": True,
            "generation_provider": "llm",
            "max_agent_attempts": 2,
        }
    )

    assert result["success"] is True
    semantics = result["proposal"]["state_semantics"]
    assert semantics["link"]["observation_type"] == "interface.oper_status"
    assert semantics["link"]["monitor"] is True
    assert "observation_type" not in semantics["protocol"]
    assert semantics["protocol"]["monitor"] is False
    governance = result["proposal"]["evidence"]["observation_target_governance"]
    assert governance["resolved"] == {"interface.oper_status": ["link"]}


def test_llm_contract_maps_vendor_states_and_allows_optional_target(monkeypatch):
    module = _load_skill_module()

    class CanonicalStateProvider:
        name = "canonical-state-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                candidate={
                    "category": "interface_oper",
                    "entity_type": "interface",
                    "primary_keys": ["interface"],
                    "required_fields": ["interface", "link_status"],
                    "optional_fields": ["protocol_status"],
                    "state_semantics": {
                        "interface": {
                            "semantic_type": "identity",
                            "comparison_role": "identity",
                            "monitor": False,
                        },
                        "link_status": {
                            "semantic_type": "state",
                            "observation_type": "interface.oper_status",
                            "comparison_role": "observation",
                            "monitor": True,
                            "event_generation": True,
                            "event_role": "health",
                            "value_mapping": {
                                "UP": "up",
                                "DOWN": "down",
                                "ADM": "admin_down",
                            },
                            "on_unmapped": "reject",
                        },
                        "protocol_status": {
                            "semantic_type": "state",
                            "observation_type": "interface.oper_status",
                            "comparison_role": "observation",
                            "monitor": True,
                        },
                    },
                },
                provider=self.name,
                model_name="mock",
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        CanonicalStateProvider,
    )
    records = [
        {
            "interface": "GE1/0/1",
            "link_status": "UP",
            "protocol_status": "UP",
        },
        {
            "interface": "GE1/0/2",
            "link_status": "ADM",
            "protocol_status": "DOWN",
        },
    ]
    seed = {
        "category": "interface_oper",
        "entity_type": "interface",
        "primary_keys": ["interface"],
        "required_fields": ["interface", "link_status"],
        "optional_fields": ["protocol_status"],
        "validators": {},
    }

    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "display interface brief",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": seed,
            "observation_targets": [
                {
                    "canonical_field": "status",
                    "observation_type": "interface.oper_status",
                    "semantic_type": "state",
                    "allowed_values": [
                        "up",
                        "down",
                        "admin_down",
                        "absent",
                        "error_disabled",
                        "unavailable",
                        "unknown",
                    ],
                    "required": True,
                    "policy_constraints": {
                        "allowed_compare_modes": [
                            "state_transition",
                            "enum_transition",
                        ]
                    },
                },
                {
                    "canonical_field": "admin_status",
                    "observation_type": "interface.admin_status",
                    "semantic_type": "state",
                    "allowed_values": ["up", "down", "unknown"],
                    "required": False,
                },
            ],
            "strict_observation_targets": True,
            "generation_provider": "llm",
            "max_agent_attempts": 1,
        }
    )

    assert result["success"] is True
    mapping = result["proposal"]["state_semantics"]["link_status"][
        "value_mapping"
    ]
    assert mapping == {"UP": "up", "DOWN": "down", "ADM": "admin_down"}
    assert result["proposal"]["state_semantics"]["link_status"][
        "policy_constraints"
    ] == {
        "allowed_compare_modes": ["state_transition", "enum_transition"],
    }
    assert "observation_type" not in result["proposal"]["state_semantics"][
        "protocol_status"
    ]
    assert result["proposal"]["state_semantics"]["protocol_status"][
        "comparison_role"
    ] == "metadata"
    assert result["proposal"]["evidence"]["observation_target_governance"][
        "resolved"
    ] == {"interface.oper_status": ["link_status"]}


def test_llm_contract_maps_vendor_named_optical_status_to_canonical_presence(
    monkeypatch,
):
    module = _load_skill_module()

    class OpticalProjectionProvider:
        name = "optical-projection-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                candidate={
                    "category": "transceiver_diagnosis",
                    "entity_type": "interface_optical_source",
                    "primary_keys": ["interface_name"],
                    "required_fields": ["interface_name", "status"],
                    "optional_fields": [
                        "current_rx_power",
                        "current_tx_power",
                    ],
                    "canonical_entity_type": "interface_optical",
                    "entity_identity_fields": ["interface", "channel"],
                    "entity_dimensions": {
                        "interface": {"field": "interface_name"},
                        "channel": {"literal": "module"},
                    },
                    "state_semantics": {
                        "interface_name": {
                            "semantic_type": "identity",
                            "comparison_role": "identity",
                            "monitor": False,
                        },
                        "status": {
                            "semantic_type": "state",
                            "observation_type": "interface.optical.presence",
                            "comparison_role": "observation",
                            "monitor": True,
                            "dimensions": {
                                "channel": {"literal": "module"},
                            },
                            "value_mapping": {
                                "": "present",
                                "The transceiver is absent.": "absent",
                            },
                            "emit_when_empty": True,
                            "on_unmapped": "reject",
                        },
                        "current_rx_power": {
                            "semantic_type": "gauge",
                            "unit": "dbm",
                            "canonical_unit": "dbm",
                            "observation_type": "interface.optical.rx_power",
                            "comparison_role": "observation",
                            "monitor": True,
                            "dimensions": {
                                "channel": {"literal": "module"},
                            },
                        },
                        "current_tx_power": {
                            "semantic_type": "gauge",
                            "unit": "dbm",
                            "canonical_unit": "dbm",
                            "observation_type": "interface.optical.tx_power",
                            "comparison_role": "observation",
                            "monitor": True,
                            "dimensions": {
                                "channel": {"literal": "module"},
                            },
                        },
                    },
                },
                provider=self.name,
                model_name="mock",
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        OpticalProjectionProvider,
    )
    records = [
        {
            "interface_name": "XGE1/0/1",
            "status": "",
            "current_rx_power": "-5.34",
            "current_tx_power": "-6.32",
        },
        {
            "interface_name": "XGE1/0/2",
            "status": "The transceiver is absent.",
            "current_rx_power": "",
            "current_tx_power": "",
        },
    ]
    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "display transceiver diagnosis interface",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": {
                "category": "transceiver_diagnosis",
                "entity_type": "interface_optical_source",
                "primary_keys": ["interface_name"],
                "required_fields": ["interface_name", "status"],
                "optional_fields": [
                    "current_rx_power",
                    "current_tx_power",
                ],
                "validators": {
                    "interface_name": {"type": "interface_name"},
                    "current_rx_power": {"type": "number"},
                    "current_tx_power": {"type": "number"},
                },
            },
            "canonical_identity_constraint": {
                "canonical_entity_type": "interface_optical",
                "identity_dimensions": ["interface", "channel"],
            },
            "observation_targets": [
                {
                    "canonical_field": "transceiver_present",
                    "observation_type": "interface.optical.presence",
                    "semantic_type": "state",
                    "dimensions": ["channel"],
                    "allowed_values": ["present", "absent", "unknown"],
                    "required": True,
                },
                {
                    "canonical_field": "rx_power_dbm",
                    "observation_type": "interface.optical.rx_power",
                    "semantic_type": "gauge",
                    "unit": "dbm",
                    "dimensions": ["channel"],
                    "required": True,
                },
                {
                    "canonical_field": "tx_power_dbm",
                    "observation_type": "interface.optical.tx_power",
                    "semantic_type": "gauge",
                    "unit": "dbm",
                    "dimensions": ["channel"],
                    "required": True,
                },
            ],
            "strict_observation_targets": True,
            "generation_provider": "llm",
            "max_agent_attempts": 1,
        }
    )

    assert result["success"] is True
    status = result["proposal"]["state_semantics"]["status"]
    assert status["observation_type"] == "interface.optical.presence"
    assert status["value_mapping"][""] == "present"
    assert status["emit_when_empty"] is True


def test_llm_loop_projects_source_cpu_keys_to_check_item_identity(monkeypatch):
    module = _load_skill_module()

    class CpuProjectionProvider:
        name = "cpu-projection-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                candidate={
                    "category": "cpu",
                    "entity_type": "cpu_record",
                    "primary_keys": ["slot", "cpu"],
                    "required_fields": ["slot", "cpu", "usage_5s", "usage_1m"],
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
                    "state_semantics": {
                        "slot": {"semantic_type": "identity"},
                        "cpu": {"semantic_type": "identity"},
                        "usage_5s": {
                            "semantic_type": "gauge",
                            "unit": "percent",
                            "observation_type": "device.cpu.utilization",
                            "dimensions": {"window": "5s"},
                            "comparison_role": "observation",
                            "monitor": True,
                        },
                        "usage_1m": {
                            "semantic_type": "gauge",
                            "unit": "percent",
                            "observation_type": "device.cpu.utilization",
                            "dimensions": {"window": "1m"},
                            "comparison_role": "observation",
                            "monitor": True,
                        },
                    },
                },
                provider=self.name,
                model_name="mock",
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        CpuProjectionProvider,
    )
    records = [
        {"slot": "0", "cpu": "0", "usage_5s": "12", "usage_1m": "14"},
        {"slot": "0", "cpu": "1", "usage_5s": "18", "usage_1m": "20"},
    ]
    seed = {
        "category": "cpu",
        "entity_type": "cpu_record",
        "primary_keys": ["slot", "cpu"],
        "required_fields": ["slot", "cpu", "usage_5s", "usage_1m"],
        "optional_fields": [],
        "validators": {
            "usage_5s": {"type": "percent"},
            "usage_1m": {"type": "percent"},
        },
    }

    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "display cpu",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": seed,
            "canonical_identity_constraint": {
                "canonical_entity_type": "cpu_scope",
                "identity_dimensions": ["scope", "scope_id"],
                "identity_dimension_rules": {
                    "scope": {
                        "kind": "enum",
                        "allowed_values": ["system", "slot", "processor"],
                    },
                    "scope_id": {"kind": "identifier"},
                },
            },
            "observation_targets": [
                {
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "unit": "percent",
                    "dimensions": ["window"],
                }
            ],
            "strict_observation_targets": True,
            "generation_provider": "llm",
            "max_agent_attempts": 1,
        }
    )

    assert result["success"] is True
    proposal = result["proposal"]
    assert proposal["entity_type"] == "cpu_record"
    assert proposal["canonical_entity_type"] == "cpu_scope"
    assert list(proposal["entity_identity_fields"]) == ["scope", "scope_id"]
    assert proposal["entity_dimensions"]["scope_id"]["op"] == "concat"
    assert {
        proposal["state_semantics"]["usage_5s"]["observation_type"],
        proposal["state_semantics"]["usage_1m"]["observation_type"],
    } == {"device.cpu.utilization"}


def test_llm_loop_repairs_collapsed_canonical_identity_from_validator_feedback(
    monkeypatch,
):
    module = _load_skill_module()
    feedback_history = []

    class RepairingCpuProjectionProvider:
        name = "repairing-cpu-projection-model"

        def propose(self, **kwargs):
            feedback_history.append(kwargs.get("validator_feedback"))
            scope_id = (
                {"field": "slot"}
                if kwargs["attempt"] == 1
                else {
                    "op": "concat",
                    "args": [
                        {"field": "slot"},
                        {"literal": "/"},
                        {"field": "cpu"},
                    ],
                }
            )
            return ProviderResult(
                candidate={
                    "category": "cpu",
                    "entity_type": "cpu_record",
                    "primary_keys": ["slot", "cpu"],
                    "required_fields": ["slot", "cpu", "usage_5s"],
                    "canonical_entity_type": "cpu_scope",
                    "entity_identity_fields": ["scope", "scope_id"],
                    "entity_dimensions": {
                        "scope": {"literal": "slot"},
                        "scope_id": scope_id,
                    },
                    "state_semantics": {
                        "slot": {"semantic_type": "identity"},
                        "cpu": {"semantic_type": "identity"},
                        "usage_5s": {
                            "semantic_type": "gauge",
                            "unit": "percent",
                            "observation_type": "device.cpu.utilization",
                            "dimensions": {"window": "5s"},
                            "comparison_role": "observation",
                            "monitor": True,
                        },
                    },
                },
                provider=self.name,
                model_name="mock",
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        RepairingCpuProjectionProvider,
    )
    records = [
        {"slot": "0", "cpu": "0", "usage_5s": "12"},
        {"slot": "0", "cpu": "1", "usage_5s": "18"},
    ]
    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "display cpu",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": {
                "category": "cpu",
                "entity_type": "cpu_record",
                "primary_keys": ["slot", "cpu"],
                "required_fields": ["slot", "cpu", "usage_5s"],
                "optional_fields": [],
                "validators": {"usage_5s": {"type": "percent"}},
            },
            "canonical_identity_constraint": {
                "canonical_entity_type": "cpu_scope",
                "identity_dimensions": ["scope", "scope_id"],
                "identity_dimension_rules": {
                    "scope": {
                        "kind": "enum",
                        "allowed_values": ["system", "slot", "processor"],
                    },
                    "scope_id": {"kind": "identifier"},
                },
            },
            "observation_targets": [
                {
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "unit": "percent",
                    "dimensions": ["window"],
                }
            ],
            "strict_observation_targets": True,
            "generation_provider": "llm",
            "max_agent_attempts": 2,
        }
    )

    assert result["success"] is True
    assert result["agent_loop"]["attempt_count"] == 2
    assert feedback_history[0] is None
    assert any(
        "missing: cpu" in error
        for error in feedback_history[1]["errors"]
    )
    assert result["proposal"]["entity_dimensions"]["scope_id"]["op"] == "concat"


def test_contract_governance_rejects_dynamic_value_in_scope_dimension():
    module = _load_skill_module()
    candidate = {
        "category": "cpu",
        "entity_type": "cpu_record",
        "primary_keys": ["slot", "cpu"],
        "required_fields": ["slot", "cpu", "usage_5s"],
        "canonical_entity_type": "cpu_scope",
        "entity_identity_fields": ["scope", "scope_id"],
        "entity_dimensions": {
            "scope": {
                "op": "concat",
                "args": [{"literal": "slot"}, {"field": "slot"}],
            },
            "scope_id": {"field": "cpu"},
        },
        "state_semantics": {
            "slot": {"semantic_type": "identity"},
            "cpu": {"semantic_type": "identity"},
            "usage_5s": {
                "semantic_type": "gauge",
                "unit": "percent",
                "observation_type": "device.cpu.utilization",
                "dimensions": {"window": "5s"},
                "comparison_role": "observation",
                "monitor": True,
            },
        },
    }

    errors = module._contract_governance_errors(
        candidate,
        field_inventory=["slot", "cpu", "usage_5s"],
        contract_seed={
            "category": "cpu",
            "entity_type": "cpu_record",
            "primary_keys": ["slot", "cpu"],
            "required_fields": ["slot", "cpu", "usage_5s"],
        },
        observation_targets=[
            {
                "observation_type": "device.cpu.utilization",
                "semantic_type": "gauge",
                "unit": "percent",
                "dimensions": ["window"],
            }
        ],
        strict_observation_targets=True,
        canonical_identity_constraint={
            "canonical_entity_type": "cpu_scope",
            "identity_dimensions": ["scope", "scope_id"],
            "identity_dimension_rules": {
                "scope": {
                    "kind": "enum",
                    "allowed_values": ["system", "slot", "processor"],
                }
            },
        },
        sample_records=[{"slot": "0", "cpu": "0", "usage_5s": "12"}],
    )

    assert any(
        "entity_dimensions.scope resolved to 'slot0'" in error
        for error in errors
    )

    candidate["entity_dimensions"] = {
        "scope": {"literal": "slot"},
        "scope_id": {"field": "slot"},
    }
    collapsed_errors = module._contract_governance_errors(
        candidate,
        field_inventory=["slot", "cpu", "usage_5s"],
        contract_seed={
            "category": "cpu",
            "entity_type": "cpu_record",
            "primary_keys": ["slot", "cpu"],
            "required_fields": ["slot", "cpu", "usage_5s"],
        },
        canonical_identity_constraint={
            "canonical_entity_type": "cpu_scope",
            "identity_dimensions": ["scope", "scope_id"],
            "identity_dimension_rules": {
                "scope": {
                    "kind": "enum",
                    "allowed_values": ["system", "slot", "processor"],
                }
            },
        },
        sample_records=[{"slot": "0", "cpu": "0", "usage_5s": "12"}],
    )
    assert any(
        "missing: cpu" in error
        for error in collapsed_errors
    )


def test_contract_governance_rejects_duplicate_canonical_identity_sources():
    module = _load_skill_module()
    candidate = {
        "category": "fan",
        "entity_type": "fan",
        "primary_keys": ["fan"],
        "required_fields": ["fan", "fan_status"],
        "optional_fields": [],
        "canonical_entity_type": "environment_fan",
        "entity_identity_fields": ["slot", "fan"],
        "entity_dimensions": {
            "slot": {"field": "fan"},
            "fan": {"field": "fan"},
        },
        "state_semantics": {
            "fan": {
                "semantic_type": "identity",
                "comparison_role": "identity",
                "monitor": False,
            },
            "fan_status": {
                "semantic_type": "state",
                "comparison_role": "observation",
                "monitor": True,
                "observation_type": "environment.fan.state",
            },
        },
    }

    errors = module._contract_governance_errors(
        candidate,
        field_inventory=["fan", "fan_status"],
        contract_seed={
            "category": "fan",
            "entity_type": "fan",
            "primary_keys": ["fan"],
            "required_fields": ["fan", "fan_status"],
        },
        canonical_identity_constraint={
            "canonical_entity_type": "environment_fan",
            "identity_dimensions": ["slot", "fan"],
        },
        sample_records=[{"fan": "Fan1", "fan_status": "Ok"}],
    )

    assert any(
        "cannot map distinct canonical identity dimensions to the same source "
        "expression: fan, slot" in error
        for error in errors
    )


def test_contract_governance_uses_full_field_profile_for_value_mapping_coverage():
    module = _load_skill_module()
    candidate = {
        "category": "interface",
        "entity_type": "interface",
        "primary_keys": ["interface"],
        "required_fields": ["interface", "status"],
        "optional_fields": [],
        "state_semantics": {
            "interface": {
                "semantic_type": "identity",
                "comparison_role": "identity",
                "monitor": False,
            },
            "status": {
                "semantic_type": "state",
                "comparison_role": "observation",
                "monitor": True,
                "observation_type": "interface.oper_status",
                "value_mapping": {"connected": "up"},
                "on_unmapped": "reject",
            },
        },
    }

    errors = module._contract_governance_errors(
        candidate,
        field_inventory=["interface", "status"],
        contract_seed={
            "category": "interface",
            "entity_type": "interface",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "status"],
        },
        field_profiles={
            "status": {
                "distinct_count": 3,
                "observed_values": ["connected", "notconnec", "noOperMem"],
            }
        },
        sample_records=[{"interface": "Eth1/1", "status": "connected"}],
    )

    assert any("'notconnec'" in error for error in errors)
    assert any("'noOperMem'" in error for error in errors)


def test_llm_loop_rebinds_portable_applicability_to_qualified_fields(monkeypatch):
    module = _load_skill_module()

    class WeakOpticalProvider:
        name = "weak-test-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                candidate={
                    "category": "record",
                    "entity_type": "diagnostic_record",
                    "primary_keys": ["interface"],
                    "required_fields": ["interface"],
                    "optional_fields": [
                        "channel_bias_ma",
                        "channel_rx_power_dbm",
                        "channel_tx_power_dbm",
                    ],
                    "state_semantics": {
                        "interface": {"semantic_type": "identity"},
                        "channel_bias_ma": {"semantic_type": "gauge"},
                        "channel_rx_power_dbm": {
                            "semantic_type": "gauge",
                            "monitor": True,
                        },
                        "channel_tx_power_dbm": {
                            "semantic_type": "gauge",
                            "monitor": True,
                        },
                    },
                },
                provider=self.name,
                model_name="mock",
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        WeakOpticalProvider,
    )
    records = [
        {
            "interface": "GE1/0/1",
            "channel_bias_ma": ["0.00", "6.20"],
            "channel_rx_power_dbm": ["-40.00", "-2.10"],
            "channel_tx_power_dbm": ["-40.00", "-1.90"],
        }
    ]
    seed = {
        "category": "record",
        "entity_type": "diagnostic_record",
        "primary_keys": ["interface"],
        "required_fields": ["interface"],
        "optional_fields": [
            "channel_bias_ma",
            "channel_rx_power_dbm",
            "channel_tx_power_dbm",
        ],
        "validators": {
            "channel_bias_ma": {"type": "number"},
            "channel_rx_power_dbm": {"type": "number"},
            "channel_tx_power_dbm": {"type": "number"},
        },
    }

    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "inspect portable optical diagnostics",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": seed,
            "observation_targets": [
                {
                    "canonical_field": "rx_power_dbm",
                    "observation_type": "interface.optical.rx_power",
                },
                {
                    "canonical_field": "tx_power_dbm",
                    "observation_type": "interface.optical.tx_power",
                },
            ],
            "strict_observation_targets": True,
            "generation_provider": "llm",
            "max_agent_attempts": 1,
        }
    )

    assert result["success"] is True
    rx = result["proposal"]["state_semantics"]["channel_rx_power_dbm"]
    applicability = rx["applicability"]
    assert applicability["exclude_when"] == [
        {
            "code": "measurement_not_applicable",
            "when": {
                "all": [
                    {"field": "channel_bias_ma", "op": "lte", "value": 0},
                    {
                        "field": "channel_rx_power_dbm",
                        "op": "equals_field",
                        "other_field": "channel_tx_power_dbm",
                    },
                    {
                        "field": "channel_rx_power_dbm",
                        "op": "lte",
                        "value": -40,
                    },
                ]
            },
        }
    ]


def test_governed_hints_drop_applicability_branches_missing_from_parser_schema():
    module = _load_skill_module()
    applicability = {
        "version": 1,
        "on_missing_context": "reject",
        "exclude_when": [
            {
                "code": "transceiver_absent",
                "when": {
                    "field": "transceiver_present",
                    "op": "in_ci",
                    "values": ["absent"],
                },
            },
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
            },
        ],
    }

    hints = module._governed_semantic_hints(
        ["interface", "rx_power_dbm", "tx_power_dbm"],
        {
            "primary_keys": ["interface"],
            "state_semantics": {
                "rx_power_dbm": {
                    "semantic_type": "gauge",
                    "unit": "dbm",
                    "applicability": applicability,
                }
            },
        },
    )

    assert "applicability" not in hints["rx_power_dbm"]


def test_schema_compiler_preserves_unobserved_optional_and_removes_phantom_field():
    module = _load_skill_module()
    seed = {
        "category": "record",
        "entity_type": "transceiver_diagnosis",
        "primary_keys": ["interface"],
        "required_fields": ["interface"],
        "optional_fields": ["absent"],
        "validators": {"absent": {"type": "enum", "values": ["true"]}},
        "state_semantics": {
            "interface": {"semantic_type": "identity"},
            "absent": {
                "semantic_type": "text",
                "description": "The transceiver is absent.",
            },
        },
    }
    hints = module._governed_semantic_hints(["interface"], seed)
    candidate = module._apply_governed_semantic_hints(
        {
            **seed,
            "state_semantics": {
                "interface": {"semantic_type": "identity"},
                "phantom_value": {"semantic_type": "state"},
            },
        },
        hints,
    )

    validation = module._validate_contract_by_group(
        command="inspect transceiver",
        groups=[{"group_id": "sample-1", "records": [{"interface": "GE1/0/1"}]}],
        candidate=candidate,
        field_inventory=["interface"],
        contract_seed=seed,
    )

    assert validation["success"] is True
    assert validation["unknown_fields"] == []
    assert candidate["state_semantics"]["absent"]["monitor"] is False
    assert "phantom_value" not in candidate["state_semantics"]
    assert candidate["evidence"]["semantic_field_governance"]["removed_fields"] == [
        "phantom_value"
    ]


def test_schema_compiler_defaults_model_omissions_to_non_monitored_metadata():
    module = _load_skill_module()
    candidate = module._apply_governed_semantic_hints(
        {
            "entity_type": "transceiver",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "present"],
            "optional_fields": ["part_number", "serial_number"],
            "state_semantics": {
                "present": {
                    "semantic_type": "state",
                    "observation_type": "interface.optical.presence",
                    "monitor": True,
                }
            },
        },
        {},
    )

    semantics = candidate["state_semantics"]
    assert semantics["interface"] == {
        "semantic_type": "identity",
        "comparison_role": "identity",
        "monitor": False,
    }
    assert semantics["part_number"] == {
        "semantic_type": "text",
        "comparison_role": "metadata",
        "monitor": False,
    }
    assert semantics["serial_number"] == {
        "semantic_type": "text",
        "comparison_role": "metadata",
        "monitor": False,
    }
    assert candidate["evidence"]["semantic_field_governance"][
        "defaulted_metadata_fields"
    ] == ["part_number", "serial_number"]


def test_same_record_set_threshold_relation_is_compiled_to_local_references():
    module = _load_skill_module()
    contract, removed = module._prune_unapproved_relation_bindings(
        {
            "record_sets": [
                {
                    "name": "diagnostics",
                    "state_semantics": {
                        "rx_power_dbm": {
                            "observation_type": "interface.optical.rx_power",
                            "threshold_references": {
                                "low": {"field_name": "rx_low_warning_dbm"},
                                "critical_low": {"field_name": "rx_low_alarm_dbm"},
                            },
                        }
                    },
                }
            ],
            "record_set_relations": [
                {
                    "name": "local_rx_thresholds",
                    "source_record_set": "diagnostics",
                    "target_record_set": "diagnostics",
                    "join_keys": {"interface": "interface"},
                    "threshold_bindings": {
                        "rx_power_dbm": {
                            "low": "rx_low_warning_dbm",
                            "critical_low": "rx_low_alarm_dbm",
                        }
                    },
                }
            ],
        }
    )

    assert contract["record_set_relations"] == []
    assert removed == [
        {
            "relation_name": "local_rx_thresholds",
            "record_set_name": "diagnostics",
            "field_name": "rx_power_dbm",
            "reason": "redundant_same_record_set_threshold_reference",
        }
    ]


def test_engineering_override_can_correct_catalog_semantic_type():
    module = _load_skill_module()
    candidate = {
        "category": "interface_status",
        "entity_type": "interface_status",
        "primary_keys": ["port"],
        "required_fields": ["port", "speed"],
        "optional_fields": [],
        "validators": {
            "port": {"type": "interface_name"},
            "speed": {"type": "text"},
        },
        "allow_empty": False,
        "state_semantics": {
            "port": {
                "semantic_type": "identity",
                "comparison_role": "identity",
                "monitor": False,
            },
            "speed": {
                "semantic_type": "state",
                "comparison_role": "observation",
                "monitor": True,
                "observation_type": "interface.speed",
            },
        },
    }
    records = [
        {"port": "Eth1/1", "speed": "40G"},
        {"port": "Eth1/2", "speed": "auto"},
    ]
    params = {
        "command": "show interface status",
        "groups": [{"group_id": "snapshot-1", "records": records}],
        "candidate": candidate,
        "field_inventory": ["port", "speed"],
        "require_policy_targets": True,
    }

    governed = module._validate_contract_by_group(**params)
    overridden = module._validate_contract_by_group(
        **params,
        allow_semantic_overrides=True,
    )

    assert governed["success"] is False
    assert any("must match governed value 'gauge'" in item for item in governed["errors"])
    assert overridden["success"] is True


def test_observation_target_owns_semantic_type_and_canonical_unit():
    module = _load_skill_module()
    candidate = {
        "primary_keys": ["interface"],
        "required_fields": ["interface"],
        "optional_fields": ["ip_address", "rx_power_dbm"],
        "validators": {},
        "state_semantics": {
            "interface": {"semantic_type": "identity"},
            "ip_address": {
                "semantic_type": "text",
                "unit": "ip",
                "observation_type": "interface.ip.address",
            },
            "rx_power_dbm": {
                "semantic_type": "text",
                "unit": "dbm",
                "observation_type": "interface.optical.rx_power",
            },
        }
    }

    governed = module._apply_observation_target_constraints(
        candidate,
        field_inventory=["interface", "ip_address", "rx_power_dbm"],
        observation_targets=[
            {
                "canonical_field": "ip_address",
                "observation_type": "interface.ip.address",
                "semantic_type": "state",
            },
            {
                "canonical_field": "rx_power_dbm",
                "observation_type": "interface.optical.rx_power",
                "semantic_type": "gauge",
                "unit": "dbm",
            },
        ],
        strict=True,
    )

    semantics = governed["state_semantics"]
    assert semantics["ip_address"]["semantic_type"] == "state"
    assert "unit" not in semantics["ip_address"]
    assert semantics["rx_power_dbm"]["semantic_type"] == "gauge"
    assert semantics["rx_power_dbm"]["canonical_unit"] == "dbm"
    assert governed["evidence"]["observation_target_governance"][
        "governed_semantics"
    ] == {
        "ip_address": {
            "observation_type": "interface.ip.address",
            "semantic_type": "state",
        },
        "rx_power_dbm": {
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "canonical_unit": "dbm",
        },
    }

    errors = module._contract_governance_errors(
        governed,
        field_inventory=["interface", "ip_address", "rx_power_dbm"],
        contract_seed={
            "primary_keys": ["interface"],
            "required_fields": ["interface"],
            "optional_fields": ["ip_address", "rx_power_dbm"],
            "validators": {},
        },
        require_policy_targets=True,
        observation_targets=[
            {
                "canonical_field": "ip_address",
                "observation_type": "interface.ip.address",
                "semantic_type": "state",
            },
            {
                "canonical_field": "rx_power_dbm",
                "observation_type": "interface.optical.rx_power",
                "semantic_type": "gauge",
                "unit": "dbm",
            },
        ],
        strict_observation_targets=True,
        field_profiles={
            "ip_address": {
                "observed_values": ["192.0.2.1"],
                "numeric_count": 0,
            },
            "rx_power_dbm": {
                "observed_values": [-12.5],
                "numeric_count": 1,
            },
        },
        sample_records=[
            {
                "interface": "Eth1/1",
                "ip_address": "192.0.2.1",
                "rx_power_dbm": -12.5,
            }
        ],
    )
    assert not any("ip_address.semantic_type" in error for error in errors)
    assert not any("ip_address.unit" in error for error in errors)


def test_multi_record_llm_can_propose_locally_proved_threshold_join(monkeypatch):
    module = _load_skill_module()

    class RelationProvider:
        name = "relation-model"

        def propose(self, **_kwargs):
            return ProviderResult(
                candidate={
                    "category": "multi_record",
                    "entity_type": "multi_record",
                    "primary_keys": [],
                    "required_fields": [],
                    "optional_fields": [],
                    "state_semantics": {},
                    "record_sets": [
                        {
                            "name": "measurements",
                            "entity_type": "interface_optical_measurement",
                            "primary_keys": ["interface"],
                            "required_fields": ["interface", "rx_power"],
                            "optional_fields": [],
                            "validators": {"rx_power": {"type": "number"}},
                            "state_semantics": {
                                "interface": {"semantic_type": "identity"},
                                "rx_power": {
                                    "semantic_type": "gauge",
                                    "unit": "dbm",
                                    "observation_type": "interface.optical.rx_power",
                                    "comparison_role": "observation",
                                    "monitor": True,
                                    "event_generation": True,
                                    "event_role": "health",
                                },
                            },
                        },
                        {
                            "name": "thresholds",
                            "entity_type": "interface_optical_threshold",
                            "primary_keys": ["interface"],
                            "required_fields": ["interface", "rx_alarm_low"],
                            "optional_fields": [],
                            "validators": {"rx_alarm_low": {"type": "number"}},
                            "state_semantics": {
                                "interface": {"semantic_type": "identity"},
                                "rx_alarm_low": {
                                    "semantic_type": "gauge",
                                    "unit": "dbm",
                                    "comparison_role": "threshold",
                                    "monitor": False,
                                    "event_generation": False,
                                    "event_role": "threshold",
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
                                "rx_power": {"critical_low": "rx_alarm_low"},
                                "interface": {"critical_low": "rx_alarm_low"},
                            },
                            "match_policy": "required",
                        }
                    ],
                },
                provider=self.name,
                model_name="mock",
            )

    monkeypatch.setitem(
        module.DEFAULT_RULE_GENERATION_PROVIDERS._factories,
        "llm",
        RelationProvider,
    )
    records = [
        {
            "__record_set__": "measurements",
            "interface": "Eth1/1",
            "rx_power": "-9.0",
        },
        {
            "__record_set__": "thresholds",
            "interface": "Eth1/1",
            "rx_alarm_low": "-12.0",
        },
    ]
    seed = {
        "category": "multi_record",
        "entity_type": "multi_record",
        "primary_keys": [],
        "required_fields": [],
        "optional_fields": [],
        "validators": {},
        "record_sets": [
            {
                "name": "measurements",
                "entity_type": "interface_optical_measurement",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "rx_power"],
                "optional_fields": [],
                "validators": {"rx_power": {"type": "number"}},
                "allow_empty": False,
                "empty_patterns": [],
                "ignore_fields": [],
            },
            {
                "name": "thresholds",
                "entity_type": "interface_optical_threshold",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "rx_alarm_low"],
                "optional_fields": [],
                "validators": {"rx_alarm_low": {"type": "number"}},
                "allow_empty": False,
                "empty_patterns": [],
                "ignore_fields": [],
            },
        ],
    }
    target = {
        "canonical_field": "rx_power_dbm",
        "observation_type": "interface.optical.rx_power",
        "semantic_type": "gauge",
        "unit": "dbm",
        "required": True,
        "policy_constraints": {
            "allowed_compare_modes": ["threshold_low_and_deadband"],
            "require_structured_threshold_references": True,
            "required_threshold_reference_any": ["low", "critical_low"],
        },
    }

    result = module.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": "inspect optics",
            "records": records,
            "record_groups": [{"group_id": "snapshot-1", "records": records}],
            "proposal": seed,
            "observation_targets": [target],
            "strict_observation_targets": True,
            "generation_provider": "llm",
            "max_agent_attempts": 1,
        }
    )

    assert result["success"] is True
    measurement = next(
        item
        for item in result["proposal"]["record_sets"]
        if item["name"] == "measurements"
    )
    reference = measurement["state_semantics"]["rx_power"][
        "threshold_references"
    ]["critical_low"]
    assert reference["record_set_name"] == "thresholds"
    relation_report = result["validation"]["record_set_relations"][0]
    assert relation_report["matched_record_count"] == 1
    assert result["validation"]["required_reference_errors"] == []
    pruned = result["proposal"]["evidence"][
        "record_set_relation_validation"
    ]["pruned_unapproved_bindings"]
    assert pruned == [
        {
            "relation_name": "measurement_thresholds",
            "record_set_name": "measurements",
            "field_name": "interface",
            "reason": "field_is_not_an_approved_observation_target",
        }
    ]
