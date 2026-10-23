# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 表达式提示 — 供画布与参数面板使用。"""

from __future__ import annotations

from typing import Any

from src.core.workflows.dsl import WorkflowDSL, WorkflowStepDSL
from src.core.workflows.mapping import (
    _available_expressions_for_step,
    build_mapping_suggestions,
    infer_step_inputs,
)
from src.skills.skill_manager import get_skill_manager

# 常见 Webhook / 聊天 context 字段
_DEFAULT_CONTEXT_FIELDS = [
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
    "callback_url",
]


def _skill_output_hints(skill_name: str) -> list[dict[str, str]]:
    """从 Skill schema outputs 推断 result 字段提示。"""
    schema = get_skill_manager().get_skill_schema(skill_name)
    if not schema:
        return []

    hints: list[dict[str, str]] = []
    for out in schema.get("outputs") or []:
        name = out.get("name")
        if not name:
            continue
        if out.get("type") == "object":
            hints.append({"label": f"result.{name}", "expr": f"${{steps.<step>.result.{name}}}"})
        else:
            hints.append({"label": f"result.{name}", "expr": f"${{steps.<step>.result.{name}}}"})

    hints.append({"label": "result (完整 JSON)", "expr": "${steps.<step>.result}"})

    entry_output = schema.get("entry_output")
    if entry_output == "dir":
        hints.append(
            {
                "label": "artifacts.config_zip.file_key",
                "expr": "${steps.<step>.artifacts.config_zip.file_key}",
            }
        )
        hints.append(
            {
                "label": "artifacts.config_zip.download_url",
                "expr": "${steps.<step>.artifacts.config_zip.download_url}",
            }
        )
    elif entry_output == "file":
        hints.append(
            {
                "label": "artifacts (首 artifact)",
                "expr": "${steps.<step>.artifacts}",
            }
        )

    return hints


def build_expression_hints(
    dsl: WorkflowDSL,
    *,
    step_name: str | None = None,
    skill: str | None = None,
) -> dict[str, Any]:
    """
    返回指定步骤可用的表达式提示。

    若未指定 step_name，使用 DSL 最后一步。
    """
    steps = dsl.steps
    target: WorkflowStepDSL | None = None

    if step_name:
        target = next((s for s in steps if s.name == step_name), None)
    elif steps:
        target = steps[-1]

    if not target and skill:
        target = next((s for s in steps if s.skill == skill), None)

    if not target:
        return {
            "step_name": step_name,
            "skill": skill,
            "context": [{"label": f, "expr": f"${{context.{f}}}"} for f in _DEFAULT_CONTEXT_FIELDS],
            "steps": {},
            "suggestions": [],
            "available_expressions": _available_expressions_for_step(None),
        }

    idx = steps.index(target)
    prev = steps[idx - 1] if idx > 0 else None

    upstream_steps: dict[str, Any] = {}
    for s in steps[:idx]:
        output_hints = _skill_output_hints(s.skill)
        for h in output_hints:
            h["expr"] = h["expr"].replace("<step>", s.name)

        upstream_steps[s.name] = {
            "skill": s.skill,
            "label": s.label,
            "outputs": output_hints,
        }

    suggestions = infer_step_inputs(target, prev_step=prev, step_index=idx)
    mapping = build_mapping_suggestions(steps)
    step_mapping = next((m for m in mapping if m["step_name"] == target.name), {})

    available = step_mapping.get("available_expressions") or _available_expressions_for_step(prev)

    output_hints_current = []
    for s in steps[:idx]:
        for h in _skill_output_hints(s.skill):
            output_hints_current.append(
                {"label": f"{s.name}.{h['label']}", "expr": h["expr"].replace("<step>", s.name)}
            )

    return {
        "step_name": target.name,
        "skill": target.skill,
        "context": [{"label": f, "expr": f"${{context.{f}}}"} for f in _DEFAULT_CONTEXT_FIELDS],
        "run": [{"label": "run.id", "expr": "${run.id}"}],
        "upstream_step": prev.name if prev else None,
        "steps": upstream_steps,
        "suggestions": [{"key": k, "expr": v} for k, v in suggestions.items()],
        "available_expressions": available,
        "upstream_output_hints": output_hints_current,
    }
