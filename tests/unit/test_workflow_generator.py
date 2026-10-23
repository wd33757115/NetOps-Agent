# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow DSL 生成器单元测试。"""

import yaml

from src.core.workflows.dsl import WorkflowDSL
from src.core.workflows.generator import (
    dsl_from_collab_template,
    generate_and_persist,
    generate_plugin_files,
    preview_workflow,
)
from src.core.workflows.mapping import apply_auto_mapping, infer_step_inputs


def _firewall_dsl() -> WorkflowDSL:
    return dsl_from_collab_template(
        plugin_name="test-firewall",
        description="测试防火墙变更链",
        step1_skill="firewall-policy-generator",
        step2_skill="itsm-change-ticket-writer",
        category="itsm",
        chat_match_any=["防火墙", "策略"],
        chat_match_secondary=["变更工单"],
    )


def test_dsl_from_collab_template_structure():
    dsl = _firewall_dsl()
    assert dsl.meta.name == "test-firewall"
    assert [s.name for s in dsl.steps] == ["policy_generation", "change_ticket"]


def test_infer_firewall_to_change_ticket_mapping():
    # The retired firewall Skills now name the firewall-policy App's actions.
    dsl = _firewall_dsl()
    mapped = apply_auto_mapping(dsl.steps)
    policy_step, change_step = mapped[0], mapped[1]
    assert policy_step.skill == "app:firewall-policy/generate_policy"
    assert policy_step.inputs["policy_file_url"] == "${context.policy_file_url}"
    assert change_step.skill == "app:firewall-policy/write_change_ticket"
    assert change_step.inputs["policy_run_id"] == "${steps.policy_generation.result.policy_run_id}"
    assert change_step.inputs["ticket_title"] == "${context.ticket_title}"


def test_generate_plugin_files_contains_all_artifacts():
    dsl = _firewall_dsl()
    files = generate_plugin_files(dsl)
    assert "WORKFLOW.yaml" in files
    assert "CHAT.intent.yaml" in files
    wf = yaml.safe_load(files["WORKFLOW.yaml"])
    assert wf["name"] == "test-firewall"
    assert len(wf["steps"]) == 2
    chat = yaml.safe_load(files["CHAT.intent.yaml"])
    assert chat["workflow"] == "test-firewall"
    assert "防火墙" in chat["match"]["require_any"]
    assert chat["required_context"] == ["ticket_id", "policy_file_url"]


def test_preview_workflow_validates():
    dsl = _firewall_dsl()
    result = preview_workflow(dsl)
    assert result["success"] is True
    assert result["persisted"] is False
    assert "WORKFLOW.yaml" in result["files"]


def test_generate_and_persist_without_save():
    dsl = _firewall_dsl()
    from src.core.workflows.dsl import GenerateOptions

    result = generate_and_persist(dsl, options=GenerateOptions(persist=False))
    assert result["success"] is True
    assert result["persisted"] is False


def test_infer_step_inputs_generic_prev_result(monkeypatch):
    from src.core.workflows.dsl import WorkflowStepDSL

    # A Skill that declares prev_result gets the previous step's whole result.
    monkeypatch.setattr("src.core.workflows.mapping.get_skill_input_names",
                        lambda name: {"prev_result"} if name == "some-skill-b" else set())
    prev = WorkflowStepDSL(id="a", name="step_one", label="一", skill="some-skill-a")
    curr = WorkflowStepDSL(id="b", name="step_two", label="二", skill="some-skill-b")
    inferred = infer_step_inputs(curr, prev_step=prev, step_index=1)
    assert inferred["prev_result"] == "${steps.step_one.result}"


def test_save_generated_plugin():
    import shutil
    from unittest.mock import patch

    from src.core.plugins.chat_intent import get_chat_intent_registry
    from src.core.workflows.dsl import GenerateOptions
    from src.core.workflows.registry import WORKFLOWS_ROOT

    plugin_name = "gen-test-wf"
    plugin_dir = WORKFLOWS_ROOT / "custom" / plugin_name

    dsl = dsl_from_collab_template(
        plugin_name=plugin_name,
        description="生成器落盘测试",
        step1_skill="firewall-policy-generator",
        step2_skill=None,
        category="custom",
    )

    try:
        with patch("src.core.workflows.metadata_repo.upsert_plugin_metadata"):
            result = generate_and_persist(
                dsl,
                options=GenerateOptions(persist=True, overwrite=True, reload=True),
            )
        assert result["success"] is True
        assert result["persisted"] is True
        assert (plugin_dir / "WORKFLOW.yaml").is_file()
        wf = yaml.safe_load((plugin_dir / "WORKFLOW.yaml").read_text(encoding="utf-8"))
        assert wf["name"] == plugin_name
        assert len(wf["steps"]) == 1
    finally:
        shutil.rmtree(plugin_dir, ignore_errors=True)
        get_chat_intent_registry().load(force=True)
