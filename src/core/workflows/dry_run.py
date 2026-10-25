# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow Dry-run：不启动 Celery，解析 when/inputs 并模拟步骤链。"""

from __future__ import annotations

from typing import Any

from src.core.workflows.dsl import WorkflowDSL, WorkflowStepDSL
from src.core.workflows.expression import build_step_env, resolve_inputs, step_is_enabled
from src.core.workflows.generator import generate_plugin_files
from src.core.workflows.manager import validate_plugin_files
from src.core.workflows.generator import render_expression
from src.core.workflows.mapping import apply_auto_mapping


class _MockStepRecord:
    """dry-run 用轻量 step 记录，兼容 build_step_env。"""

    def __init__(self, step_name: str, result: dict[str, Any], artifacts: dict[str, Any] | None = None):
        self.step_name = step_name
        self.result = result
        self.output_artifacts = artifacts or {}


def _default_mock_result(step: WorkflowStepDSL) -> dict[str, Any]:
    result = {
        "success": True,
        "message": f"[模拟] {step.skill} 执行成功",
        "skill": step.skill,
    }
    if step.skill == "app:firewall-policy/generate_policy":
        result["policy_run_id"] = "0" * 32
    if step.skill == "app:firewall-policy/draft_request":
        result["policy_file_url"] = "netdisk:mock"
    return result


def _default_mock_artifacts(step: WorkflowStepDSL) -> dict[str, Any]:
    if step.skill in {"firewall-policy-generator", "app:firewall-policy/generate_policy"}:
        return {
            "config_zip": {
                "file_key": f"mock/{step.name}/config.zip",
                "download_url": f"https://mock.local/{step.name}/config.zip",
            }
        }
    if step.skill in {"itsm-change-ticket-writer", "app:firewall-policy/write_change_ticket"}:
        return {
            "change_excel": {
                "file_key": f"mock/{step.name}/change.xlsx",
                "download_url": f"https://mock.local/{step.name}/change.xlsx",
            }
        }
    return {}


def dry_run_workflow(
    dsl: WorkflowDSL,
    context: dict[str, Any],
    *,
    run_id: str = "dry-run",
    auto_map_inputs: bool = True,
) -> dict[str, Any]:
    """
    模拟 Workflow 执行：解析 when、推断/解析 inputs，返回步骤链与 mock 结果。
    不调用 Celery / Skill 真实执行。
    """
    steps = apply_auto_mapping(dsl.steps, enabled=auto_map_inputs)
    mock_records: list[_MockStepRecord] = []
    simulated: list[dict[str, Any]] = []
    skipped: list[str] = []

    for idx, step in enumerate(steps):
        env = build_step_env(
            context=context,
            run_id=run_id,
            ticket_id=context.get("ticket_id"),
            step_records=mock_records,
            current_step_index=len(mock_records),
        )
        enabled = step_is_enabled(step.when, env)
        if not enabled:
            skipped.append(step.name)
            simulated.append(
                {
                    "index": idx,
                    "name": step.name,
                    "label": step.label,
                    "skill": step.skill,
                    "enabled": False,
                    "when": step.when,
                    "parallel_group": step.parallel_group,
                    "depends_on": step.depends_on or [],
                    "resolved_inputs": {},
                    "mock_result": None,
                }
            )
            continue

        resolved = resolve_inputs(
            {k: render_expression(v) for k, v in (step.inputs or {}).items()},
            env,
        )
        mock_result = _default_mock_result(step)
        mock_artifacts = _default_mock_artifacts(step)
        mock_records.append(_MockStepRecord(step.name, mock_result, mock_artifacts))

        simulated.append(
            {
                "index": idx,
                "name": step.name,
                "label": step.label,
                "skill": step.skill,
                "enabled": True,
                "when": step.when,
                "parallel_group": step.parallel_group,
                "depends_on": step.depends_on or [],
                "resolved_inputs": resolved,
                "mock_result": mock_result,
                "mock_artifacts": mock_artifacts,
            }
        )

    active = [s for s in simulated if s["enabled"]]
    parallel_batches: list[dict[str, Any]] = []
    i = 0
    while i < len(active):
        step = active[i]
        group = step.get("parallel_group")
        if group:
            batch = [step]
            j = i + 1
            while j < len(active) and active[j].get("parallel_group") == group:
                batch.append(active[j])
                j += 1
            if len(batch) > 1:
                parallel_batches.append(
                    {
                        "parallel_group": group,
                        "step_names": [s["name"] for s in batch],
                    }
                )
                i = j
                continue
        i += 1

    files = generate_plugin_files(dsl.model_copy(update={"steps": steps}), auto_map_inputs=False)
    validation = validate_plugin_files(files)

    flow_labels = [s["label"] or s["name"] for s in active]
    flow = " → ".join(flow_labels) if flow_labels else "（无步骤）"

    return {
        "success": True,
        "run_id": run_id,
        "template_name": dsl.meta.name,
        "context": context,
        "steps": simulated,
        "active_step_count": len(active),
        "skipped_steps": skipped,
        "parallel_batches": parallel_batches,
        "flow_description": flow,
        "validation": validation,
    }
