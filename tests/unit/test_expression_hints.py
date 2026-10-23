# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""表达式提示 API 单元测试。"""

from src.core.workflows.dsl import WorkflowDSL, WorkflowMetaDSL, WorkflowStepDSL
from src.core.workflows.expression_hints import build_expression_hints
from src.core.workflows.generator import dsl_from_collab_template


def _firewall_dsl() -> WorkflowDSL:
    return dsl_from_collab_template(
        plugin_name="hint-test-wf",
        description="test",
        step1_skill="firewall-policy-generator",
        step2_skill="itsm-change-ticket-writer",
    )


def test_expression_hints_includes_context():
    hints = build_expression_hints(_firewall_dsl(), step_name="change_ticket")
    assert hints["step_name"] == "change_ticket"
    assert hints["skill"] == "itsm-change-ticket-writer"
    assert any(c["label"] == "ticket_id" for c in hints["context"])
    assert hints["upstream_step"] == "policy_generation"


def test_expression_hints_suggestions_for_change_ticket():
    hints = build_expression_hints(_firewall_dsl(), step_name="change_ticket")
    keys = {s["key"] for s in hints["suggestions"]}
    assert "manifest" in keys
    assert "config_file_key" in keys


def test_expression_hints_available_expressions():
    hints = build_expression_hints(_firewall_dsl(), step_name="change_ticket")
    exprs = hints["available_expressions"]
    assert any("policy_generation" in e["expr"] for e in exprs)


def test_expression_hints_minimal_dsl():
    dsl = WorkflowDSL(
        meta=WorkflowMetaDSL(name="minimal", description=""),
        steps=[WorkflowStepDSL(id="s1", name="only", label="Only", skill="firewall-policy-generator")],
    )
    hints = build_expression_hints(dsl)
    assert hints["step_name"] == "only"
    assert hints["context"]
