# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill schema API 单元测试。"""

from pathlib import Path

from src.skills.skill_manager import get_skill_manager


SKILLS_DIR = Path(__file__).resolve().parents[2] / "src" / "skills"


def test_get_skill_schema_firewall():
    schema = get_skill_manager().get_skill_schema("firewall-policy-generator")
    assert schema is not None
    assert schema["name"] == "firewall-policy-generator"
    input_names = {i["name"] for i in schema["inputs"]}
    assert "ticket_id" in input_names
    assert "policy_file_url" in input_names
    assert schema.get("entry_output") == "dir"


def test_get_skill_schema_llm_analyzer():
    schema = get_skill_manager().get_skill_schema("llm-result-analyzer")
    assert schema is not None
    input_names = {i["name"] for i in schema["inputs"]}
    assert "prev_result" in input_names
    assert "analysis_prompt" in input_names


def test_get_skill_schema_rule_asset_submitter():
    schema = get_skill_manager().get_skill_schema("rule-asset-submitter")
    assert schema is not None
    assert schema["name"] == "rule-asset-submitter"
    input_names = {i["name"] for i in schema["inputs"]}
    assert {"job_id", "actor"}.issubset(input_names)
    assert schema.get("entry_output") == "none"


def test_get_skill_schema_rule_asset_approver():
    schema = get_skill_manager().get_skill_schema("rule-asset-approver")
    assert schema is not None
    assert schema["name"] == "rule-asset-approver"
    input_names = {i["name"] for i in schema["inputs"]}
    assert {"job_id", "actor"}.issubset(input_names)
    assert schema.get("entry_output") == "none"
    assert schema.get("category") == "parser"


def test_get_skill_schema_rule_asset_persister():
    schema = get_skill_manager().get_skill_schema("rule-asset-persister")
    assert schema is not None
    assert schema["name"] == "rule-asset-persister"
    input_names = {i["name"] for i in schema["inputs"]}
    assert {"contract_result", "change_policy_result", "event_rule_result"}.issubset(input_names)
    assert schema.get("entry_output") == "none"
    assert schema.get("category") == "parser"


def test_rule_asset_skill_entry_scripts_exist():
    for skill_name in [
        "rule-asset-persister",
        "rule-asset-submitter",
        "rule-asset-approver",
    ]:
        schema = get_skill_manager().get_skill_schema(skill_name)
        assert schema is not None
        entry_script = schema.get("entry_script")
        assert entry_script
        assert (SKILLS_DIR / skill_name / str(entry_script)).exists()


def test_get_skill_schema_missing():
    assert get_skill_manager().get_skill_schema("nonexistent-skill-xyz") is None
