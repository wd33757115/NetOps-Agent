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
                "status": "up",
                "protocol": "up",
            },
            {
                "interface": "Interface2",
                "ip_address": "unassigned",
                "status": "down",
                "protocol": "down",
            },
        ],
    )

    assert result["success"] is True
    assert result["proposal"]["entity_type"] == "interface_l3"
    assert result["proposal"]["primary_keys"] == ("interface",)
    assert result["proposal"]["state_semantics"]["interface"]["semantic_type"] == "identity"
    assert result["proposal"]["state_semantics"]["protocol"]["semantic_type"] == "state"
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
