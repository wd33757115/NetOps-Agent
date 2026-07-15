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
    assert result["agent_loop"]["attempt_count"] == 2


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
