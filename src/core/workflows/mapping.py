# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 步骤输入智能映射。"""

from __future__ import annotations

from typing import Any

from src.core.workflows.dsl import ExpressionRef, WorkflowStepDSL
from src.skills.skill_manager import get_skill_manager

# 常见 context 字段，首步 Skill 通常需要
_COMMON_CONTEXT_FIELDS = (
    "ticket_id",
    "ticket_title",
    "policy_file_url",
    "topology_file_url",
    "requester",
    "assignee",
    "priority",
    "parameters",
    "change_background",
    "change_purpose",
    "requester_dept",
    "due_date",
)

FIREWALL_DRAFT = "app:firewall-policy/draft_request"
FIREWALL_POLICY = "app:firewall-policy/generate_policy"
FIREWALL_TICKET = "app:firewall-policy/write_change_ticket"

# Skill 对之间的已知映射规则：(prev_skill, skill) -> {input: expression}
_KNOWN_PAIR_MAPPINGS: dict[tuple[str, str], dict[str, str]] = {
    (FIREWALL_DRAFT, FIREWALL_POLICY): {
        "policy_file_url": "${steps.{prev}.result.policy_file_url}",
    },
    (FIREWALL_POLICY, FIREWALL_TICKET): {
        "policy_run_id": "${steps.{prev}.result.policy_run_id}",
    },
    (
        "firewall-policy-generator",
        "itsm-change-ticket-writer",
    ): {
        "manifest": "${steps.{prev}.result.manifest}",
        "config_file_key": "${steps.{prev}.artifacts.config_zip.file_key}",
        "config_files_url": "${steps.{prev}.artifacts.config_zip.download_url}",
    },
}

# 按 Skill 名称的固定映射补充
_SKILL_SPECIFIC: dict[str, dict[str, str]] = {
    "itsm-change-ticket-writer": {
        "change_background": "${context.change_background}",
        "change_purpose": "${context.change_purpose}",
        "requester": "${context.requester}",
        "requester_dept": "${context.requester_dept}",
        "priority": "${context.priority}",
        "due_date": "${context.due_date}",
        "assignee": "${context.assignee}",
    },
}


def _context_expr(field: str) -> str:
    return f"${{context.{field}}}"


def _run_expr(field: str = "id") -> str:
    return f"${{run.{field}}}"


def _step_result_expr(step_name: str, path: str = "result") -> str:
    return f"${{steps.{step_name}.{path}}}"


def get_skill_input_names(skill_name: str) -> set[str]:
    """读取 Skill SKILL.md 中声明的 input 参数名；应用动作读它的输入模型。"""
    from src.platform.tools import actions

    if actions.is_action(skill_name):
        found = actions.catalog().get(skill_name)
        return set(found[1].input_model.model_fields) if found else set()
    manager = get_skill_manager()
    skill_md = manager._find_skill_md_path(skill_name)  # noqa: SLF001 — 管理器内部路径解析
    if not skill_md:
        return set()
    meta = manager._parse_skill_md_content(skill_md.read_text(encoding="utf-8"))  # noqa: SLF001
    inputs = meta.get("inputs") or []
    return {item["name"] for item in inputs if isinstance(item, dict) and item.get("name")}


def _apply_template(template: str, prev_step_name: str) -> str:
    """将模板中的 {prev} 替换为实际步骤名（避免 str.format 与 ${} 冲突）。"""
    return template.replace("{prev}", prev_step_name)


def infer_step_inputs(
    step: WorkflowStepDSL,
    *,
    prev_step: WorkflowStepDSL | None,
    step_index: int,
) -> dict[str, str]:
    """
    为单个步骤推断 inputs 表达式。

    用户显式配置的 inputs 不会被覆盖；本函数仅返回建议/缺失字段。
    """
    skill = step.skill
    skill_inputs = get_skill_input_names(skill)
    inferred: dict[str, str] = {}

    # 几乎所有步骤都需要
    for key in ("ticket_id", "workflow_run_id"):
        if key in skill_inputs:
            inferred[key] = _context_expr("ticket_id") if key == "ticket_id" else _run_expr("id")

    if step_index == 0:
        for field in _COMMON_CONTEXT_FIELDS:
            if field in skill_inputs and field not in inferred:
                inferred[field] = _context_expr(field)
    elif prev_step:
        pair_key = (prev_step.skill, skill)
        pair_rules = _KNOWN_PAIR_MAPPINGS.get(pair_key, {})
        for input_key, template in pair_rules.items():
            if input_key in skill_inputs:
                inferred[input_key] = _apply_template(template, prev_step.name)

        skill_rules = _SKILL_SPECIFIC.get(skill, {})
        for input_key, template in skill_rules.items():
            if input_key not in inferred and input_key in skill_inputs:
                inferred[input_key] = _apply_template(template, prev_step.name)

        # 通用 fallback：上一步 result 传给下游
        if "prev_result" in skill_inputs and "prev_result" not in inferred:
            inferred["prev_result"] = _step_result_expr(prev_step.name, "result")

        # 按 Skill schema 名称为 context 字段补全
        for field in _COMMON_CONTEXT_FIELDS:
            if field in skill_inputs and field not in inferred:
                inferred[field] = _context_expr(field)

    return inferred


def apply_auto_mapping(
    steps: list[WorkflowStepDSL],
    *,
    enabled: bool = True,
) -> list[WorkflowStepDSL]:
    """对 DSL 步骤应用自动映射，显式 inputs 优先。"""
    if not enabled:
        return steps

    merged_steps: list[WorkflowStepDSL] = []
    for idx, step in enumerate(steps):
        prev = steps[idx - 1] if idx > 0 else None
        inferred = infer_step_inputs(step, prev_step=prev, step_index=idx)
        combined = {**inferred, **step.inputs}
        merged_steps.append(step.model_copy(update={"inputs": combined}))
    return merged_steps


def _available_expressions_for_step(prev: WorkflowStepDSL | None) -> list[dict[str, str]]:
    """返回 UI 可用的表达式快捷项。"""
    exprs = [
        {"label": "context.ticket_id", "expr": "${context.ticket_id}"},
        {"label": "context.ticket_title", "expr": "${context.ticket_title}"},
        {"label": "context.policy_file_url", "expr": "${context.policy_file_url}"},
        {"label": "run.id", "expr": "${run.id}"},
    ]
    if prev:
        p = prev.name
        exprs.extend(
            [
                {"label": f"steps.{p}.result", "expr": f"${{steps.{p}.result}}"},
                {"label": f"steps.{p}.result.manifest", "expr": f"${{steps.{p}.result.manifest}}"},
                {
                    "label": f"steps.{p}.artifacts.config_zip.file_key",
                    "expr": f"${{steps.{p}.artifacts.config_zip.file_key}}",
                },
                {
                    "label": f"steps.{p}.artifacts.config_zip.download_url",
                    "expr": f"${{steps.{p}.artifacts.config_zip.download_url}}",
                },
            ]
        )
    return exprs


def build_mapping_suggestions(
    steps: list[WorkflowStepDSL],
) -> list[dict[str, Any]]:
    """返回每步的映射建议，供 UI 展示。"""
    suggestions: list[dict[str, Any]] = []
    for idx, step in enumerate(steps):
        prev = steps[idx - 1] if idx > 0 else None
        inferred = infer_step_inputs(step, prev_step=prev, step_index=idx)
        suggestions.append(
            {
                "step_name": step.name,
                "skill": step.skill,
                "suggested_inputs": inferred,
                "upstream_step": prev.name if prev else None,
                "available_expressions": _available_expressions_for_step(prev),
            }
        )
    return suggestions
