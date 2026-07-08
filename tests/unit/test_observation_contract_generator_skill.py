from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.core.parser.contract_inference import ContractProposal
from src.core.parser.contract_validation import infer_and_validate_contract


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
