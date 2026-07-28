# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Supervisor v2 短链规则调度：ExecutionPlan 启发式（不含 Workflow 长流程定义）。"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from src.agents.supervisor.models_v2 import ExecutionPlan, SkillTaskSpec
from src.common.ticket_utils import extract_ticket_id
from src.skill_system.router import SkillMatch

# 仅允许在 WorkflowEngine / CHAT.intent 中编排，禁止进入同步 ExecutionPlan 链
WORKFLOW_ONLY_SKILLS = frozenset(
    {
        "itsm-change-ticket-writer",
        "itsm-callback",
    }
)

# 短链 heuristic 上限；更长组合走 LLM ExecutionPlan 或 Workflow
MAX_HEURISTIC_CHAIN_LEN = 3

_SEQUENTIAL_INTENT = re.compile(
    r"之后|然后|接着|完成后|先.+?后|再.{0,12}(?:巡检|备份|生成|检查)"
)

_DOCUMENT_TYPES = ("请示", "通知", "函", "报告", "总结", "纪要", "决定", "入党申请书", "申请书")


def extract_device_filter_params(query: str) -> dict[str, Any]:
    """从用户话术中提取设备备份/巡检过滤条件。"""
    params: dict[str, Any] = {}
    ip_match = re.search(r"\d{1,3}(?:\.\d{1,3}){3}", query)
    if ip_match:
        params["ip"] = ip_match.group(0)
    for group in ("生产环境", "测试环境", "DMZ区域"):
        if group in query:
            params["group"] = group
            break
    group_match = re.search(r"(?:分组|group)[为是:\s]+([^\s，,。.]+)", query, re.IGNORECASE)
    if group_match:
        params["group"] = group_match.group(1)
    return params


def _params_firewall_policy(
    query: str,
    uploaded_file_path: str | None,
    ticket_id: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"ticket_title": "防火墙策略生成"}
    if uploaded_file_path:
        params["policy_file_url"] = uploaded_file_path
    return params


def _params_official_document(
    query: str,
    uploaded_file_path: str | None,
    ticket_id: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"user_query": query, "action": "write"}
    for doc_type in _DOCUMENT_TYPES:
        if doc_type in query:
            params["document_type"] = doc_type
            break
    return params


def _params_device_ops(
    query: str,
    uploaded_file_path: str | None,
    ticket_id: str | None,
) -> dict[str, Any]:
    return {"filter_params": extract_device_filter_params(query)}


_SKILL_PARAM_BUILDERS: dict[str, Callable[..., dict[str, Any]]] = {
    "firewall-policy-generator": _params_firewall_policy,
    "official-document-writing": _params_official_document,
    "device-backup": _params_device_ops,
    "device-patrol": _params_device_ops,
}


def build_heuristic_skill_params(
    skill_name: str,
    query: str,
    uploaded_file_path: str | None,
    ticket_id: str | None,
) -> dict[str, Any]:
    """按 Skill 插件注册表构建 heuristic 入参（graph 内不写 if-elif）。"""
    params: dict[str, Any] = {}
    if ticket_id:
        params["ticket_id"] = ticket_id
    builder = _SKILL_PARAM_BUILDERS.get(skill_name)
    if builder:
        params.update(builder(query, uploaded_file_path, ticket_id))
    return params


def _trigger_position_in_query(query: str, match: SkillMatch) -> int:
    reason = match.reason or ""
    trigger = ""
    for sep in ("匹配触发词:", "匹配触发词："):
        if sep in reason:
            trigger = reason.split(sep, 1)[1].strip()
            break
    if trigger:
        pos = query.lower().find(trigger.lower())
        if pos >= 0:
            return pos
    return 10**9


def ordered_trigger_skills(
    query: str,
    loaded_skills: list[str],
    skill_matches: list[SkillMatch] | None,
) -> list[str]:
    """按话术中触发词出现顺序排列触发词命中的 Skill（排除 Workflow 专用 Skill）。"""
    matches_by_name = {m.skill_name: m for m in (skill_matches or [])}
    triggered = [
        name
        for name in loaded_skills
        if (match := matches_by_name.get(name))
        and match.match_type == "trigger"
        and name not in WORKFLOW_ONLY_SKILLS
    ]
    triggered.sort(key=lambda name: _trigger_position_in_query(query, matches_by_name[name]))
    return triggered


def build_heuristic_execution_plan(
    query: str,
    loaded_skills: list[str],
    skill_matches: list[SkillMatch] | None,
    uploaded_file_path: str | None,
) -> ExecutionPlan | None:
    """
    不调用 LLM 的短链 ExecutionPlan（触发词命中、同轮 SSE 聚合）。
    固定长流程 / ITSM 多步请走 CHAT.intent → WorkflowEngine。
    """
    ordered = ordered_trigger_skills(query, loaded_skills, skill_matches)
    if not ordered or len(ordered) > MAX_HEURISTIC_CHAIN_LEN:
        return None

    ticket_id = extract_ticket_id(query)
    matches_by_name = {m.skill_name: m for m in (skill_matches or [])}
    sequential = len(ordered) > 1 and bool(_SEQUENTIAL_INTENT.search(query))

    tasks: list[SkillTaskSpec] = []
    prev: str | None = None
    for name in ordered:
        params = build_heuristic_skill_params(name, query, uploaded_file_path, ticket_id)
        depends_on = [prev] if sequential and prev else []
        tasks.append(SkillTaskSpec(skill_name=name, parameters=params, depends_on=depends_on))
        prev = name

    reasons = [matches_by_name[n].reason for n in ordered if matches_by_name.get(n)]
    if len(ordered) > 1:
        mode_hint = "顺序" if sequential else "并行"
        reasoning = f"规则调度（免 LLM，{mode_hint}）: " + " → ".join(reasons)
    else:
        reasoning = f"规则调度（免 LLM）: {reasons[0] if reasons else '已加载 Skill 指令'}"

    return ExecutionPlan(
        reasoning=reasoning,
        skills=tasks,
        execution_mode="parallel",
        fallback_to_rag=False,
    )
