# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""扫描加载 Workflow 插件包（src/workflows/**/WORKFLOW.yaml）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.core.logging import get_logger

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS_ROOT = PROJECT_ROOT / "src" / "workflows"


@dataclass(frozen=True)
class WorkflowStepTemplate:
    name: str
    skill_name: str
    inputs: dict[str, Any] = field(default_factory=dict)
    when: str | None = None
    label: str | None = None
    parallel_group: str | None = None
    depends_on: list[str] = field(default_factory=list)
    subworkflow: str | None = None

    @property
    def is_subworkflow(self) -> bool:
        return bool(self.subworkflow)


@dataclass(frozen=True)
class WorkflowCompletionConfig:
    message: str = "Workflow 已完成"
    notification_title: str | None = None
    notification_body: str | None = None
    notification_level: str = "success"
    notify_each_step: bool = False
    notify_on_failure: bool = True


@dataclass(frozen=True)
class WorkflowTemplate:
    name: str
    description: str
    version: str
    steps: list[WorkflowStepTemplate]
    plugin_dir: Path
    on_complete: WorkflowCompletionConfig = field(default_factory=WorkflowCompletionConfig)
    test_context: dict[str, Any] = field(default_factory=dict)
    test_required_context: list[str] = field(default_factory=list)


_TEMPLATES: dict[str, WorkflowTemplate] = {}


def _parse_workflow_file(path: Path) -> WorkflowTemplate | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("workflow_plugin_parse_failed", path=str(path), error=str(exc))
        return None

    name = raw.get("name")
    if not name:
        log.warning("workflow_plugin_missing_name", path=str(path))
        return None

    steps: list[WorkflowStepTemplate] = []
    for item in raw.get("steps") or []:
        subworkflow = item.get("subworkflow")
        skill = item.get("skill") or item.get("skill_name")
        if not item.get("name") or (not skill and not subworkflow):
            continue
        skill_name = str(skill) if skill else f"subworkflow:{subworkflow}"
        steps.append(
            WorkflowStepTemplate(
                name=str(item["name"]),
                skill_name=skill_name,
                inputs=dict(item.get("inputs") or {}),
                when=item.get("when"),
                label=item.get("label"),
                parallel_group=item.get("parallel_group"),
                depends_on=list(item.get("depends_on") or []),
                subworkflow=str(subworkflow) if subworkflow else None,
            )
        )

    oc = raw.get("on_complete") or {}
    test_config = raw.get("test") or {}
    notif = oc.get("notification") or {}
    on_complete = WorkflowCompletionConfig(
        message=str(oc.get("message") or "Workflow 已完成"),
        notification_title=notif.get("title"),
        notification_body=notif.get("body"),
        notification_level=str(notif.get("level") or "success"),
        notify_each_step=bool(oc.get("notify_each_step", notif.get("notify_each_step", False))),
        notify_on_failure=bool(oc.get("notify_on_failure", notif.get("notify_on_failure", True))),
    )

    return WorkflowTemplate(
        name=str(name),
        description=str(raw.get("description") or ""),
        version=str(raw.get("version") or "1.0"),
        steps=steps,
        plugin_dir=path.parent,
        on_complete=on_complete,
        test_context=dict(test_config.get("context") or {}),
        test_required_context=[
            str(item) for item in (test_config.get("required_context") or [])
        ],
    )


def load_workflows(force: bool = False) -> dict[str, WorkflowTemplate]:
    global _TEMPLATES
    if _TEMPLATES and not force:
        return _TEMPLATES

    found: dict[str, WorkflowTemplate] = {}
    if WORKFLOWS_ROOT.is_dir():
        for path in sorted(WORKFLOWS_ROOT.rglob("WORKFLOW.yaml")):
            tpl = _parse_workflow_file(path)
            if tpl:
                found[tpl.name] = tpl
                log.info(
                    "workflow_plugin_loaded",
                    plugin_name=tpl.name,
                    plugin_dir=str(path.parent),
                    step_count=len(tpl.steps),
                )

    _TEMPLATES = found
    log.info(
        "workflow_plugins_load_complete",
        plugin_count=len(found),
        plugin_names=sorted(found.keys()),
        force_reload=force,
    )
    return _TEMPLATES


def get_template(name: str) -> WorkflowTemplate | None:
    load_workflows()
    return _TEMPLATES.get(name)


def list_templates() -> list[WorkflowTemplate]:
    load_workflows()
    # Retain definitions for historical reads, but do not offer migrated business
    # workflows as executable templates.
    retired = {"patrol-change-event", "patrol-attested-offline-current", "patrol-offline-current", "patrol-history-change-event", "rule-asset-generation", "rule-asset-submit", "rule-asset-approve", "parser-validation-ingest", "rollout-live-capture"}
    return [template for name, template in _TEMPLATES.items() if name not in retired]


def find_step_template(template: WorkflowTemplate, step_name: str) -> WorkflowStepTemplate | None:
    for step in template.steps:
        if step.name == step_name:
            return step
    return None


def missing_test_context(
    template: WorkflowTemplate,
    context: dict[str, Any],
) -> list[str]:
    """Return workflow-owned required test fields that have no usable value."""
    missing: list[str] = []
    for field_name in template.test_required_context:
        value = context.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field_name)
    return missing


def resolve_active_steps(
    template: WorkflowTemplate,
    context: dict[str, Any],
    *,
    run_id: str = "pending",
) -> list[WorkflowStepTemplate]:
    """按 context 解析 when 条件，返回本次实际执行的步骤。"""
    from src.core.workflows.expression import build_step_env, step_is_enabled

    env = build_step_env(
        context=context,
        run_id=run_id,
        ticket_id=context.get("ticket_id"),
        step_records=[],
        current_step_index=0,
    )
    return [s for s in template.steps if step_is_enabled(s.when, env)]


def format_steps_flow(steps: list[WorkflowStepTemplate]) -> str:
    labels = [s.label or s.name for s in steps]
    return " → ".join(labels) if labels else "（无步骤）"


# 模块导入时加载
TEMPLATES: dict[str, WorkflowTemplate] = load_workflows()
