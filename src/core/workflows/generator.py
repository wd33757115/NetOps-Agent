# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow DSL → 标准插件 YAML 文件生成器。"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from src.core.plugins.chat_intent import get_chat_intent_registry
from src.core.plugins.itsm_webhook import get_itsm_webhook_registry
from src.core.workflows.dsl import (
    ExpressionRef,
    GenerateOptions,
    WorkflowDSL,
    WorkflowStepDSL,
)
from src.core.workflows.manager import save_plugin, validate_plugin_files
from src.core.workflows.mapping import apply_auto_mapping
from src.core.workflows.registry import WORKFLOWS_ROOT, list_templates, load_workflows

logger = logging.getLogger(__name__)

_PLUGIN_FILES = ("WORKFLOW.yaml", "CHAT.intent.yaml", "ITSM.webhook.yaml")


def render_expression(value: str | ExpressionRef) -> str:
    """将 DSL 表达式渲染为 WORKFLOW.yaml 中的 ${...} 字符串。"""
    if isinstance(value, str):
        return value
    if value.type == "literal":
        return value.value if value.value is not None else value.path
    if value.type == "context":
        return f"${{context.{value.path}}}"
    if value.type == "run":
        return f"${{run.{value.path or 'id'}}}"
    if value.type == "step_result":
        return f"${{steps.{value.path}}}"
    if value.type == "step_artifact":
        return f"${{steps.{value.path}}}"
    return str(value)


def _render_inputs(inputs: dict[str, str | ExpressionRef]) -> dict[str, str]:
    return {key: render_expression(val) for key, val in inputs.items()}


def _step_to_yaml_dict(step: WorkflowStepDSL) -> dict[str, Any]:
    item: dict[str, Any] = {"name": step.name}
    if step.subworkflow:
        item["subworkflow"] = step.subworkflow
    elif step.skill:
        item["skill"] = step.skill
    if step.label:
        item["label"] = step.label
    if step.when:
        item["when"] = step.when
    if step.parallel_group:
        item["parallel_group"] = step.parallel_group
    if step.depends_on:
        item["depends_on"] = step.depends_on
    rendered = _render_inputs(step.inputs)
    if rendered:
        item["inputs"] = rendered
    return item


def build_workflow_dict(dsl: WorkflowDSL) -> dict[str, Any]:
    """构建 WORKFLOW.yaml 对应的 Python dict。"""
    on_complete: dict[str, Any] = {
        "message": dsl.on_complete.message,
        "notify_each_step": dsl.on_complete.notify_each_step,
    }
    if dsl.on_complete.notify_on_failure is not True:
        on_complete["notify_on_failure"] = dsl.on_complete.notify_on_failure

    notification = dsl.on_complete.notification
    on_complete["notification"] = {
        "title": notification.title,
        "body": notification.body,
        "level": notification.level,
    }

    return {
        "name": dsl.meta.name,
        "description": dsl.meta.description,
        "version": dsl.meta.version,
        "steps": [_step_to_yaml_dict(s) for s in dsl.steps],
        "on_complete": on_complete,
    }


def build_chat_intent_dict(dsl: WorkflowDSL) -> dict[str, Any] | None:
    """构建 CHAT.intent.yaml dict；未启用 chat 触发时返回 None。"""
    chat = dsl.triggers.chat
    if not chat or not chat.enabled:
        return None

    match_block: dict[str, list[str]] = {}
    if chat.match.require_any:
        match_block["require_any"] = chat.match.require_any
    if chat.match.require_all:
        match_block["require_all"] = chat.match.require_all
    if chat.match.require_any_secondary:
        match_block["require_any_secondary"] = chat.match.require_any_secondary

    data: dict[str, Any] = {
        "workflow": dsl.meta.name,
        "priority": chat.priority,
        "description": chat.description or dsl.meta.description,
    }
    if match_block:
        data["match"] = match_block
    if chat.required_context:
        data["required_context"] = chat.required_context
    if chat.context_from_state:
        data["context_from_state"] = chat.context_from_state
    if chat.context_from_query:
        data["context_from_query"] = chat.context_from_query
    if chat.context_defaults:
        data["context_defaults"] = chat.context_defaults
    if chat.files:
        data["files"] = chat.files
    if chat.response_template:
        data["response_template"] = chat.response_template
    return data


def build_webhook_dict(dsl: WorkflowDSL) -> dict[str, Any] | None:
    """构建 ITSM.webhook.yaml dict。"""
    webhook = dsl.triggers.webhook
    if not webhook or not webhook.enabled or not webhook.route_key:
        return None

    data: dict[str, Any] = {
        "route_key": webhook.route_key,
        "workflow": dsl.meta.name,
        "accepted_message": webhook.accepted_message,
    }
    if webhook.legacy_paths:
        data["legacy_paths"] = webhook.legacy_paths
    if webhook.context_mapping:
        data["context_mapping"] = webhook.context_mapping
    return data


def _dump_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def generate_plugin_files(
    dsl: WorkflowDSL,
    *,
    auto_map_inputs: bool = True,
) -> dict[str, str]:
    """
    从 DSL 生成插件文件内容。

    Returns:
        dict: 文件名 → YAML 文本
    """
    steps = apply_auto_mapping(dsl.steps, enabled=auto_map_inputs)
    normalized = dsl.model_copy(update={"steps": steps})

    files: dict[str, str] = {}
    files["WORKFLOW.yaml"] = _dump_yaml(build_workflow_dict(normalized))

    chat_dict = build_chat_intent_dict(normalized)
    if chat_dict:
        files["CHAT.intent.yaml"] = _dump_yaml(chat_dict)

    webhook_dict = build_webhook_dict(normalized)
    if webhook_dict:
        files["ITSM.webhook.yaml"] = _dump_yaml(webhook_dict)

    return files


def preview_workflow(dsl: WorkflowDSL, *, options: GenerateOptions | None = None) -> dict[str, Any]:
    """预览生成结果，不落盘。"""
    opts = options or GenerateOptions()
    files = generate_plugin_files(dsl, auto_map_inputs=opts.auto_map_inputs)
    validation = validate_plugin_files(files)
    return {
        "success": validation.get("valid", False),
        "plugin_path": str(WORKFLOWS_ROOT / dsl.meta.category / dsl.meta.name).replace("\\", "/"),
        "files": files,
        "validation": validation,
        "persisted": False,
    }


def generate_and_persist(
    dsl: WorkflowDSL,
    *,
    options: GenerateOptions | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """生成插件文件并按需落盘、热加载。"""
    opts = options or GenerateOptions()
    files = generate_plugin_files(dsl, auto_map_inputs=opts.auto_map_inputs)
    validation = validate_plugin_files(files)

    result: dict[str, Any] = {
        "success": validation.get("valid", False),
        "plugin_path": str(WORKFLOWS_ROOT / dsl.meta.category / dsl.meta.name).replace("\\", "/"),
        "files": files,
        "validation": validation,
        "persisted": False,
        "reload": None,
    }

    if not validation.get("valid"):
        result["message"] = "校验失败"
        return result

    if not opts.persist:
        result["message"] = "预览生成成功（未落盘）"
        return result

    plugin_dir = WORKFLOWS_ROOT / dsl.meta.category / dsl.meta.name
    if plugin_dir.exists() and not opts.overwrite:
        result["success"] = False
        result["message"] = f"插件已存在: {plugin_dir}，请设置 overwrite=true"
        return result

    save_result = save_plugin(
        dsl.meta.name,
        category=dsl.meta.category,
        files=files,
    )
    result["persisted"] = save_result.get("success", False)
    result["path"] = save_result.get("path")
    result["message"] = save_result.get("message", "")

    if opts.reload and result["persisted"]:
        from src.core.workflows.reload_bus import broadcast_workflow_reload

        reload_stats = broadcast_workflow_reload(source="generate", plugin_name=dsl.meta.name)
        result["reload"] = {
            "templates": reload_stats.get("templates", 0),
            "message": "Registry 已热加载（含多 Worker 广播）",
        }

    if result["persisted"]:
        from src.core.workflows.metadata_repo import (
            transition_plugin_status,
            upsert_plugin_metadata,
        )
        from src.core.workflows.versioning import publish_plugin

        if opts.publish:
            pub = publish_plugin(
                dsl.meta.name,
                user_id=user_id,
                change_summary=opts.change_summary,
            )
            result["publish"] = pub
            result["status"] = pub.get("status", "published")
            result["message"] = pub.get("message", result.get("message"))
        elif opts.submit_review:
            upsert_plugin_metadata(
                dsl.meta.name,
                category=dsl.meta.category,
                description=dsl.meta.description,
                plugin_path=result.get("path"),
                status="review",
                user_id=user_id,
            )
            result["status"] = "review"
        else:
            upsert_plugin_metadata(
                dsl.meta.name,
                category=dsl.meta.category,
                description=dsl.meta.description,
                plugin_path=result.get("path"),
                status="draft",
                user_id=user_id,
            )
            result["status"] = "draft"

    result["success"] = result["persisted"] and validation.get("valid", False)
    return result


def dsl_from_collab_template(
    *,
    plugin_name: str,
    description: str,
    step1_skill: str,
    step2_skill: str | None,
    category: str = "itsm",
    chat_match_any: list[str] | None = None,
    chat_match_secondary: list[str] | None = None,
) -> WorkflowDSL:
    """从向导表单参数构建标准 WorkflowDSL（供前后端共用逻辑参考）。"""
    from src.core.workflows.dsl import (
        ChatIntentDSL,
        ChatIntentMatchDSL,
        WorkflowMetaDSL,
        WorkflowStepDSL,
        WorkflowTriggersDSL,
    )

    from src.core.workflows.mapping import FIREWALL_POLICY, FIREWALL_TICKET

    # The firewall Skills moved into the firewall-policy App; the chain calls its actions.
    legacy = {"firewall-policy-generator": FIREWALL_POLICY, "itsm-change-ticket-writer": FIREWALL_TICKET}
    step1_skill = legacy.get(step1_skill, step1_skill)
    step2_skill = legacy.get(step2_skill, step2_skill) if step2_skill else step2_skill
    is_firewall_chain = step1_skill == FIREWALL_POLICY and step2_skill in (FIREWALL_TICKET, None, "")
    step1_name = "policy_generation" if is_firewall_chain else "step_one"
    step1_label = "生成防火墙策略" if is_firewall_chain else "第一步"
    step2_name = "change_ticket" if is_firewall_chain else "step_two"
    step2_label = "编写变更工单" if is_firewall_chain else "第二步"

    steps: list[WorkflowStepDSL] = [
        WorkflowStepDSL(
            id="s1",
            name=step1_name,
            label=step1_label,
            skill=step1_skill,
        ),
    ]
    if step2_skill:
        steps.append(
            WorkflowStepDSL(
                id="s2",
                name=step2_name,
                label=step2_label,
                skill=step2_skill,
            )
        )

    chat = ChatIntentDSL(
        enabled=True,
        priority=50,
        description=description,
        match=ChatIntentMatchDSL(
            require_any=chat_match_any or ["关键词"],
            require_any_secondary=chat_match_secondary or [],
        ),
        required_context=["ticket_id", "policy_file_url"] if is_firewall_chain else [],
        response_template=(
            "[OK] 已启动 Workflow\n\n"
            "- **流程 ID**: `{run_id}`\n"
            "- **工单**: {ticket_id}\n"
            "- **步骤**: {workflow_description}\n"
            if is_firewall_chain
            else "[OK] 已启动 Workflow\n\n"
            "- **流程 ID**: `{run_id}`\n"
            "- **步骤**: {workflow_description}\n"
        ),
    )

    from src.core.workflows.dsl import NotificationDSL, OnCompleteDSL

    return WorkflowDSL(
        meta=WorkflowMetaDSL(
            name=plugin_name,
            description=description,
            category=category,
        ),
        steps=steps,
        triggers=WorkflowTriggersDSL(chat=chat),
        on_complete=OnCompleteDSL(
            message="Workflow 已完成",
            notification=NotificationDSL(
                title='流程已完成 (${context.ticket_id})',
                body="所有步骤已执行。",
            ),
        ),
    )


def dsl_from_plugin_files(
    files: dict[str, str | None],
    *,
    category: str = "itsm",
) -> WorkflowDSL:
    """从插件 YAML 文件反解析为 WorkflowDSL（供 UI 编辑已有 Workflow）。"""
    from src.core.workflows.dsl import (
        ChatIntentDSL,
        ChatIntentMatchDSL,
        ItsmWebhookDSL,
        NotificationDSL,
        OnCompleteDSL,
        WorkflowMetaDSL,
        WorkflowStepDSL,
        WorkflowTriggersDSL,
    )

    wf_raw = yaml.safe_load(files.get("WORKFLOW.yaml") or "") or {}
    name = str(wf_raw.get("name") or "unnamed-workflow")

    steps: list[WorkflowStepDSL] = []
    for idx, item in enumerate(wf_raw.get("steps") or []):
        subworkflow = item.get("subworkflow")
        skill = item.get("skill") or item.get("skill_name") or ""
        if not item.get("name") or (not skill and not subworkflow):
            continue
        raw_inputs = item.get("inputs") or {}
        steps.append(
            WorkflowStepDSL(
                id=f"s{idx + 1}",
                name=str(item["name"]),
                label=str(item.get("label") or item["name"]),
                skill=str(skill),
                subworkflow=str(subworkflow) if subworkflow else None,
                when=item.get("when"),
                parallel_group=item.get("parallel_group"),
                depends_on=list(item.get("depends_on") or []),
                inputs={k: str(v) for k, v in raw_inputs.items()},
            )
        )

    triggers = WorkflowTriggersDSL()
    chat_text = files.get("CHAT.intent.yaml")
    if chat_text:
        chat_raw = yaml.safe_load(chat_text) or {}
        match_raw = chat_raw.get("match") or {}
        triggers.chat = ChatIntentDSL(
            enabled=True,
            priority=int(chat_raw.get("priority") or 50),
            description=str(chat_raw.get("description") or ""),
            match=ChatIntentMatchDSL(
                require_any=list(match_raw.get("require_any") or []),
                require_all=list(match_raw.get("require_all") or []),
                require_any_secondary=list(match_raw.get("require_any_secondary") or []),
            ),
            required_context=list(chat_raw.get("required_context") or []),
            context_from_state=dict(chat_raw.get("context_from_state") or {}),
            context_from_query=dict(chat_raw.get("context_from_query") or {}),
            context_defaults=dict(chat_raw.get("context_defaults") or {}),
            files={str(k): list(v or []) for k, v in (chat_raw.get("files") or {}).items()},
            response_template=str(
                chat_raw.get("response_template")
                or "[OK] 已启动 Workflow\n\n"
                "- **流程 ID**: `{run_id}`\n"
                "- **步骤**: {workflow_description}\n"
            ),
        )

    webhook_text = files.get("ITSM.webhook.yaml")
    if webhook_text:
        wh_raw = yaml.safe_load(webhook_text) or {}
        triggers.webhook = ItsmWebhookDSL(
            enabled=True,
            route_key=str(wh_raw.get("route_key") or ""),
            accepted_message=str(wh_raw.get("accepted_message") or "已受理，正在处理"),
            legacy_paths=list(wh_raw.get("legacy_paths") or []),
            context_mapping=dict(wh_raw.get("context_mapping") or {}),
        )

    oc = wf_raw.get("on_complete") or {}
    notif = oc.get("notification") or {}
    on_complete = OnCompleteDSL(
        message=str(oc.get("message") or "Workflow 已完成"),
        notify_each_step=bool(oc.get("notify_each_step", False)),
        notify_on_failure=bool(oc.get("notify_on_failure", True)),
        notification=NotificationDSL(
            title=str(notif.get("title") or '流程已完成 (${context.ticket_id})'),
            body=str(notif.get("body") or "所有步骤已执行。"),
            level=str(notif.get("level") or "success"),  # type: ignore[arg-type]
        ),
    )

    return WorkflowDSL(
        meta=WorkflowMetaDSL(
            name=name,
            description=str(wf_raw.get("description") or ""),
            category=category,
            version=str(wf_raw.get("version") or "1.0"),
        ),
        steps=steps or [
            WorkflowStepDSL(id="s1", name="step_one", label="步骤", skill="demo-text-stats")
        ],
        triggers=triggers,
        on_complete=on_complete,
    )
