# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 插件管理：读取/写入 YAML、校验、协同模板。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from src.core.logging import get_logger
from src.core.plugins.chat_intent import match_chat_workflow
from src.core.workflows.registry import (
    WORKFLOWS_ROOT,
    WorkflowTemplate,
    get_template,
    list_templates,
    load_workflows,
    resolve_active_steps,
    format_steps_flow,
)
from src.skills.skill_manager import get_skill_manager

log = get_logger(__name__)

PLUGIN_FILES = ("WORKFLOW.yaml", "CHAT.intent.yaml", "ITSM.webhook.yaml")


def _read_plugin_file(plugin_dir: Path, filename: str) -> str | None:
    path = plugin_dir / filename
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def template_to_summary(tpl: WorkflowTemplate) -> dict[str, Any]:
    return {
        "name": tpl.name,
        "description": tpl.description,
        "version": tpl.version,
        "step_count": len(tpl.steps),
        "steps": [
            {
                "name": s.name,
                "label": s.label or s.name,
                "skill": s.skill_name,
                "when": s.when,
            }
            for s in tpl.steps
        ],
        "plugin_dir": str(tpl.plugin_dir.relative_to(WORKFLOWS_ROOT)).replace("\\", "/")
        if tpl.plugin_dir.is_relative_to(WORKFLOWS_ROOT)
        else str(tpl.plugin_dir),
        "has_chat_intent": (tpl.plugin_dir / "CHAT.intent.yaml").is_file(),
        "has_webhook": (tpl.plugin_dir / "ITSM.webhook.yaml").is_file(),
        "test_context": tpl.test_context,
        "test_required_context": tpl.test_required_context,
    }


def get_plugin_detail(name: str) -> dict[str, Any] | None:
    tpl = get_template(name)
    if not tpl:
        return None
    files: dict[str, str | None] = {}
    for fn in PLUGIN_FILES:
        files[fn] = _read_plugin_file(tpl.plugin_dir, fn)
    return {
        **template_to_summary(tpl),
        "files": files,
        "on_complete": {
            "message": tpl.on_complete.message,
            "notify_each_step": tpl.on_complete.notify_each_step,
            "notify_on_failure": tpl.on_complete.notify_on_failure,
            "notification": {
                "title": tpl.on_complete.notification_title,
                "body": tpl.on_complete.notification_body,
                "level": tpl.on_complete.notification_level,
            },
        },
    }


def validate_workflow_yaml(content: str) -> dict[str, Any]:
    """校验 WORKFLOW.yaml 内容与 Skill 引用。"""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        raw = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        return {"valid": False, "errors": [f"YAML 解析失败: {exc}"], "warnings": []}

    if not raw.get("name"):
        errors.append("缺少 name 字段")
    steps = raw.get("steps") or []
    if not steps:
        errors.append("steps 不能为空")

    skill_names = {s["name"] for s in get_skill_manager().list_all_skills() if s.get("name")}
    enabled_skills = {
        s["name"] for s in get_skill_manager().list_all_skills() if s.get("name") and s.get("enabled", True)
    }
    for idx, step in enumerate(steps):
        step_name = step.get("name")
        skill = step.get("skill") or step.get("skill_name")
        if not step_name:
            errors.append(f"steps[{idx}] 缺少 name")
        if not skill:
            errors.append(f"steps[{idx}] 缺少 skill")
        elif skill not in skill_names:
            warnings.append(f"Skill '{skill}' 未注册或已禁用")
        elif skill not in enabled_skills:
            warnings.append(f"Skill '{skill}' 当前处于禁用状态")

    expr_pattern = re.compile(r"\$\{[^}]+\}")
    for step in steps:
        for key, val in (step.get("inputs") or {}).items():
            if isinstance(val, str) and expr_pattern.search(val):
                if not val.startswith("${"):
                    warnings.append(f"表达式 '{val}' 建议使用 ${{...}} 格式")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings, "parsed": raw}


def validate_plugin_files(files: dict[str, str | None]) -> dict[str, Any]:
    """校验完整插件包。"""
    wf = files.get("WORKFLOW.yaml") or ""
    result = validate_workflow_yaml(wf)
    chat = files.get("CHAT.intent.yaml")
    if chat:
        try:
            chat_raw = yaml.safe_load(chat) or {}
            if not chat_raw.get("workflow"):
                result.setdefault("errors", []).append("CHAT.intent.yaml 缺少 workflow")
                result["valid"] = False
        except yaml.YAMLError as exc:
            result.setdefault("errors", []).append(f"CHAT.intent.yaml 解析失败: {exc}")
            result["valid"] = False
    return result


def _normalize_plugin_files(name: str, files: dict[str, str]) -> dict[str, str]:
    """保存前对齐 WORKFLOW name 与 CHAT.intent workflow 字段。"""
    out = dict(files)
    wf_content = out.get("WORKFLOW.yaml", "")
    if wf_content.strip():
        raw = yaml.safe_load(wf_content) or {}
        raw["name"] = name
        out["WORKFLOW.yaml"] = yaml.dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False)

    chat_content = out.get("CHAT.intent.yaml")
    if chat_content and chat_content.strip():
        chat_raw = yaml.safe_load(chat_content) or {}
        chat_raw["workflow"] = name
        out["CHAT.intent.yaml"] = yaml.dump(chat_raw, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return out


def save_plugin(
    name: str,
    *,
    category: str = "itsm",
    files: dict[str, str],
) -> dict[str, Any]:
    """保存 Workflow 插件到 src/workflows/{category}/{name}/。"""
    if not name or not re.match(r"^[a-z0-9][a-z0-9-]*$", name):
        return {"success": False, "message": "插件名须为小写字母、数字与连字符"}

    files = _normalize_plugin_files(name, files)
    wf_content = files.get("WORKFLOW.yaml", "")
    validation = validate_workflow_yaml(wf_content)
    if not validation["valid"]:
        return {"success": False, "message": "校验失败", "validation": validation}

    plugin_dir = WORKFLOWS_ROOT / category / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    for fn in PLUGIN_FILES:
        content = files.get(fn)
        if content is not None and content.strip():
            (plugin_dir / fn).write_text(content, encoding="utf-8")

    from src.core.workflows.reload_bus import broadcast_workflow_reload

    broadcast_workflow_reload(source="save_plugin", plugin_name=name)

    log.info(
        "workflow_plugin_saved",
        plugin_name=name,
        category=category,
        plugin_dir=str(plugin_dir),
    )
    return {"success": True, "message": "插件已保存", "path": str(plugin_dir)}


def preview_chat_intent(
    query: str,
    *,
    workflow_name: str | None = None,
    chat_intent_yaml: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """预览聊天话术是否匹配 Workflow 触发规则。"""
    from src.common.ticket_utils import extract_ticket_id

    ctx = context or {}
    ticket_id = extract_ticket_id(query or "")

    if chat_intent_yaml:
        try:
            raw = yaml.safe_load(chat_intent_yaml) or {}
        except yaml.YAMLError as exc:
            return {"matched": False, "reason": f"YAML 解析失败: {exc}"}
        match_cfg = raw.get("match") or {}
        require_any = list(match_cfg.get("require_any") or [])
        require_all = list(match_cfg.get("require_all") or [])
        require_secondary = list(
            match_cfg.get("require_any_secondary") or match_cfg.get("require_any_after") or []
        )
        wf_name = raw.get("workflow") or workflow_name or ""

        def _matches(patterns: list[str]) -> bool:
            if not patterns:
                return True
            return any(re.search(re.escape(p), query, re.IGNORECASE) for p in patterns)

        if require_any and not _matches(require_any):
            return {"matched": False, "reason": f"未匹配 require_any: {require_any}", "workflow": wf_name}
        if require_all and not all(re.search(re.escape(p), query, re.IGNORECASE) for p in require_all):
            return {"matched": False, "reason": f"未匹配 require_all: {require_all}", "workflow": wf_name}
        if require_secondary and not _matches(require_secondary):
            return {
                "matched": False,
                "reason": f"未匹配 require_any_secondary: {require_secondary}",
                "workflow": wf_name,
            }
        if (require_any or require_all or require_secondary) and not ticket_id:
            return {
                "matched": False,
                "reason": "话术未包含可识别工单号（如 REQ2025）",
                "workflow": wf_name,
            }
        tpl = get_template(wf_name) if wf_name else None
        active_desc = format_steps_flow(resolve_active_steps(tpl, ctx)) if tpl else wf_name
        return {
            "matched": True,
            "workflow": wf_name,
            "ticket_id": ticket_id,
            "active_steps": active_desc,
        }

    intent = match_chat_workflow(query, "chat")
    if not intent:
        return {"matched": False, "reason": "未匹配任何已注册 Chat Intent"}
    if workflow_name and intent.workflow != workflow_name:
        return {"matched": False, "reason": f"匹配到 {intent.workflow}，非目标 {workflow_name}"}
    tpl = get_template(intent.workflow)
    active_desc = format_steps_flow(resolve_active_steps(tpl, ctx)) if tpl else intent.workflow
    from src.core.plugins.chat_intent import find_matching_intents, rank_intent_match

    alternates = [
        {"workflow": i.workflow, "score": rank_intent_match(query, i)}
        for i in find_matching_intents(query, "chat")[:5]
    ]
    return {
        "matched": True,
        "workflow": intent.workflow,
        "ticket_id": ticket_id,
        "active_steps": active_desc,
        "description": intent.description,
        "candidates": alternates,
    }


# ---------------------------------------------------------------------------
# 协同模板（模式 A 等）
# ---------------------------------------------------------------------------

MODE_A_WORKFLOW = """name: {name}
description: {description}
version: "1.0"

steps:
  - name: {step1_name}
    label: {step1_label}
    skill: {step1_skill}
    inputs:
      ticket_id: ${{context.ticket_id}}
      ticket_title: ${{context.ticket_title}}
      policy_file_url: ${{context.policy_file_url}}
      topology_file_url: ${{context.topology_file_url}}
      requester: ${{context.requester}}
      assignee: ${{context.assignee}}
      priority: ${{context.priority}}
      parameters: ${{context.parameters}}
      change_background: ${{context.change_background}}
      change_purpose: ${{context.change_purpose}}
      requester_dept: ${{context.requester_dept}}
      due_date: ${{context.due_date}}
      workflow_run_id: ${{run.id}}

  - name: {step2_name}
    label: {step2_label}
    skill: {step2_skill}
    inputs:
      ticket_id: ${{context.ticket_id}}
      ticket_title: ${{context.ticket_title}}
      change_background: ${{context.change_background}}
      change_purpose: ${{context.change_purpose}}
      requester: ${{context.requester}}
      requester_dept: ${{context.requester_dept}}
      priority: ${{context.priority}}
      due_date: ${{context.due_date}}
      assignee: ${{context.assignee}}
      manifest: ${{steps.{step1_name}.result.manifest}}
      config_file_key: ${{steps.{step1_name}.artifacts.config_zip.file_key}}
      config_files_url: ${{steps.{step1_name}.artifacts.config_zip.download_url}}
      workflow_run_id: ${{run.id}}

  - name: llm_analysis
    label: LLM 结果分析
    skill: llm-result-analyzer
    inputs:
      ticket_id: ${{context.ticket_id}}
      prev_result: ${{steps.{step2_name}.result}}
      analysis_prompt: ${{context.analysis_prompt}}
      analysis_focus: ${{context.analysis_focus}}
      source_step: {step2_name}
      workflow_run_id: ${{run.id}}

on_complete:
  message: 防火墙变更与 LLM 分析已完成
  notify_each_step: true
  notification:
    title: "LLM 分析已完成 (${{context.ticket_id}})"
    body: "策略、变更工单与 LLM 分析报告已生成。"
    level: success
"""

MODE_A_CHAT_INTENT = """workflow: {name}
priority: 110
description: 聊天触发防火墙变更 + LLM 结果分析（模式 A）

match:
  require_any:
    - 防火墙
    - 策略
  require_any_secondary:
    - LLM
    - llm
    - 结果分析
    - LLM分析

context_from_state:
  ticket_title: ticket_title
  policy_file_url: uploaded_file_path
  analysis_prompt: analysis_prompt

context_defaults:
  ticket_title: 防火墙策略变更
  change_purpose: 根据用户请求生成防火墙策略、编写变更工单并进行 LLM 分析
  analysis_focus: summary

response_template: |
  [OK] 已启动防火墙变更 + LLM 分析流程

  - **流程 ID**: `{{run_id}}`
  - **工单**: {{ticket_id}}
  - **步骤**: {{workflow_description}}

  流程在后台执行，完成后将收到站内通知。
"""


def list_collab_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": "mode-a-firewall-llm",
            "title": "模式 A：长时任务 → 依赖上步 → LLM 分析",
            "description": "Step1 生成策略 ZIP → Step2 编写变更工单 → Step3 llm-result-analyzer 生成分析报告",
            "steps": [
                {"name": "policy_generation", "label": "生成配置 ZIP", "skill": "firewall-policy-generator"},
                {"name": "change_ticket", "label": "编写变更工单", "skill": "itsm-change-ticket-writer"},
                {"name": "llm_analysis", "label": "LLM 结果分析", "skill": "llm-result-analyzer"},
            ],
            "default_plugin_name": "itsm-firewall-llm-analysis",
            "category": "itsm",
        },
        {
            "id": "mode-a-generic",
            "title": "模式 A：通用三步骤链",
            "description": "自定义 Step1/Step2 Skill，第三步固定为 llm-result-analyzer",
            "steps": [
                {"name": "step_one", "label": "第一步", "skill": "(自定义)"},
                {"name": "step_two", "label": "第二步", "skill": "(自定义)"},
                {"name": "llm_analysis", "label": "LLM 结果分析", "skill": "llm-result-analyzer"},
            ],
            "default_plugin_name": "custom-llm-analysis",
            "category": "custom",
        },
    ]


def generate_from_collab_template(
    template_id: str,
    *,
    plugin_name: str | None = None,
    step1_skill: str = "firewall-policy-generator",
    step2_skill: str = "itsm-change-ticket-writer",
    description: str | None = None,
) -> dict[str, str] | None:
    """根据协同模板生成插件 YAML 文件内容。"""
    if template_id == "mode-a-firewall-llm":
        name = plugin_name or "itsm-firewall-llm-analysis"
        desc = description or "ITSM 防火墙变更 + LLM 结果分析（模式 A）"
        workflow = MODE_A_WORKFLOW.format(
            name=name,
            description=desc,
            step1_name="policy_generation",
            step1_label="生成配置 ZIP",
            step1_skill=step1_skill,
            step2_name="change_ticket",
            step2_label="编写变更工单 Excel",
            step2_skill=step2_skill,
        )
        chat = MODE_A_CHAT_INTENT.format(name=name)
        return {"WORKFLOW.yaml": workflow, "CHAT.intent.yaml": chat}

    if template_id == "mode-a-generic":
        name = plugin_name or "custom-llm-analysis"
        desc = description or "自定义 Skill 链 + LLM 分析（模式 A）"
        workflow = f"""name: {name}
description: {desc}
version: "1.0"

steps:
  - name: step_one
    label: 第一步
    skill: {step1_skill}
    inputs:
      ticket_id: ${{context.ticket_id}}
      workflow_run_id: ${{run.id}}

  - name: step_two
    label: 第二步
    skill: {step2_skill}
    inputs:
      ticket_id: ${{context.ticket_id}}
      prev_result: ${{steps.step_one.result}}
      workflow_run_id: ${{run.id}}

  - name: llm_analysis
    label: LLM 结果分析
    skill: llm-result-analyzer
    inputs:
      ticket_id: ${{context.ticket_id}}
      prev_result: ${{steps.step_two.result}}
      analysis_prompt: ${{context.analysis_prompt}}
      analysis_focus: summary
      source_step: step_two
      workflow_run_id: ${{run.id}}

on_complete:
  message: Workflow 与 LLM 分析已完成
  notify_each_step: true
  notification:
    title: "分析完成 (${{context.ticket_id}})"
    body: "所有步骤已执行，LLM 分析报告已生成。"
    level: success
"""
        chat = MODE_A_CHAT_INTENT.format(name=name)
        return {"WORKFLOW.yaml": workflow, "CHAT.intent.yaml": chat}

    return None
