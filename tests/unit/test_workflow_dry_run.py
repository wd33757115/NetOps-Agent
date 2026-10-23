# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow Dry-run 与 NL Chat Intent 单元测试。"""

from src.core.workflows.chat_intent_nl import suggest_chat_intent_from_nl
from src.core.workflows.dry_run import dry_run_workflow
from src.core.workflows.generator import dsl_from_collab_template, dsl_from_plugin_files, generate_plugin_files


def _sample_dsl(name: str = "dry-run-test"):
    return dsl_from_collab_template(
        plugin_name=name,
        description="Dry-run 测试",
        step1_skill="firewall-policy-generator",
        step2_skill="itsm-change-ticket-writer",
        category="custom",
        chat_match_any=["防火墙"],
    )


def test_dry_run_resolves_steps_and_inputs():
    dsl = _sample_dsl()
    result = dry_run_workflow(
        dsl,
        {"ticket_id": "REQ2025099"},
    )
    assert result["success"] is True
    assert result["active_step_count"] == 2
    assert len(result["steps"]) == 2
    assert result["steps"][0]["enabled"] is True
    assert result["steps"][0]["resolved_inputs"]


def test_dry_run_parallel_batch_detection():
    dsl = _sample_dsl("dry-run-parallel")
    dsl.steps[0].parallel_group = "batch1"
    dsl.steps[1].parallel_group = "batch1"

    result = dry_run_workflow(dsl, {"ticket_id": "REQ2025099"})
    assert len(result["parallel_batches"]) == 1
    assert result["parallel_batches"][0]["parallel_group"] == "batch1"
    assert len(result["parallel_batches"][0]["step_names"]) == 2


def test_dry_run_skips_when_when_false():
    dsl = _sample_dsl("dry-run-when")
    dsl.steps[0].when = "${context.priority} == 'high'"

    result = dry_run_workflow(dsl, {"ticket_id": "REQ2025099", "priority": "low"})
    assert result["active_step_count"] == 1
    assert result["steps"][0]["enabled"] is False
    assert "policy_generation" in result["skipped_steps"] or dsl.steps[0].name in result["skipped_steps"]


def test_suggest_chat_intent_heuristic():
    result = suggest_chat_intent_from_nl(
        "用户请求生成防火墙策略并编写变更工单",
        "test-wf",
        use_llm=False,
    )
    assert result["success"] is True
    assert result["source"] == "heuristic"
    assert "防火墙" in result["chat_intent_yaml"] or "策略" in result["chat_intent_yaml"]
    assert "require_any" in result["chat_intent_yaml"]


def test_dsl_from_plugin_files_roundtrip():
    dsl = _sample_dsl("roundtrip-wf")
    files = generate_plugin_files(dsl, auto_map_inputs=False)
    restored = dsl_from_plugin_files(files, category="itsm")
    assert restored.meta.name == dsl.meta.name
    assert len(restored.steps) == len(dsl.steps)
    assert restored.steps[0].skill == dsl.steps[0].skill
    assert restored.triggers.chat is not None
    assert restored.triggers.chat.match.require_any
