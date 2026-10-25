# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""聊天意图 → Workflow 插件（扫描 **/CHAT.intent.yaml）。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.core.plugins.context_mapping import map_state_to_context
from src.core.workflows.registry import (
    WORKFLOWS_ROOT,
    format_steps_flow,
    get_template,
    resolve_active_steps,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatIntentPlugin:
    workflow: str
    priority: int
    description: str
    plugin_dir: Path
    require_any: list[str] = field(default_factory=list)
    require_all: list[str] = field(default_factory=list)
    require_any_secondary: list[str] = field(default_factory=list)
    required_context: list[str] = field(default_factory=list)
    auto_sources: list[str] = field(default_factory=list)
    context_from_state: dict[str, str] = field(default_factory=dict)
    context_from_query: dict[str, str] = field(default_factory=dict)
    context_defaults: dict[str, Any] = field(default_factory=dict)
    response_template: str = ""
    # Context fields a chat attachment fills, with the file endings each accepts.
    files: dict[str, list[str]] = field(default_factory=dict)


class ChatIntentRegistry:
    def __init__(self) -> None:
        self._intents: list[ChatIntentPlugin] = []

    def load(self, force: bool = False) -> None:
        if self._intents and not force:
            return
        self._intents.clear()
        if not WORKFLOWS_ROOT.is_dir():
            return
        for path in sorted(WORKFLOWS_ROOT.rglob("CHAT.intent.yaml")):
            intent = self._parse(path)
            if intent:
                self._intents.append(intent)
                logger.info("已加载 Chat Intent 插件: %s → %s", path.parent.name, intent.workflow)
        self._intents.sort(key=lambda x: -x.priority)

    def _parse(self, path: Path) -> ChatIntentPlugin | None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("解析 CHAT.intent.yaml 失败 %s: %s", path, exc)
            return None
        workflow = raw.get("workflow")
        if not workflow:
            return None
        match = raw.get("match") or {}
        return ChatIntentPlugin(
            workflow=str(workflow),
            priority=int(raw.get("priority") or 0),
            description=str(raw.get("description") or ""),
            plugin_dir=path.parent,
            require_any=list(match.get("require_any") or []),
            require_all=list(match.get("require_all") or []),
            require_any_secondary=list(match.get("require_any_secondary") or match.get("require_any_after") or []),
            required_context=list(raw.get("required_context") or []),
            auto_sources=list(raw.get("auto_if_source") or raw.get("auto_sources") or []),
            context_from_state=dict(raw.get("context_from_state") or {}),
            context_from_query=dict(raw.get("context_from_query") or {}),
            context_defaults=dict(raw.get("context_defaults") or {}),
            response_template=str(raw.get("response_template") or ""),
            files={
                str(key): [str(ending).lower() for ending in (endings or [])]
                for key, endings in (raw.get("files") or {}).items()
            },
        )

    def all_intents(self) -> list[ChatIntentPlugin]:
        self.load()
        return list(self._intents)

    def get_intent(self, workflow_name: str) -> ChatIntentPlugin | None:
        self.load()
        for intent in self._intents:
            if intent.workflow == workflow_name:
                return intent
        return None


class MissingTicketIdError(ValueError):
    """当前消息未包含可识别的工单号。"""


TICKET_REQUIRED_MSG = (
    "未在当前消息中识别到工单号。请在话术中明确工单编号，例如：\n"
    "「根据工单 REQ2025，用策略文件生成防火墙策略并编写变更工单」"
)


class MissingRequiredContextError(ValueError):
    """Chat Intent 声明的必需上下文无法从当前请求解析。"""

    def __init__(self, missing_fields: list[str]):
        self.missing_fields = missing_fields
        labels = "、".join(missing_fields)
        super().__init__(f"启动该 Workflow 还缺少必要信息：{labels}")


def _current_query(state: dict[str, Any]) -> str:
    messages = state.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    if isinstance(last, dict):
        return str(last.get("content") or "")
    return str(getattr(last, "content", "") or "")


def require_ticket_id_from_query(query: str) -> str:
    """从当前用户消息提取工单号（Workflow 启动必填，不使用会话历史）。"""
    from src.common.ticket_utils import extract_ticket_id

    ticket_id = extract_ticket_id(query or "")
    if not ticket_id:
        raise MissingTicketIdError(TICKET_REQUIRED_MSG)
    return ticket_id


def _extract_context_from_query(query: str) -> dict[str, Any]:
    """当前消息上下文提取器注册点；新增字段不应修改匹配框架。"""
    from src.common.ticket_utils import extract_ticket_id

    context: dict[str, Any] = {}
    if ticket_id := extract_ticket_id(query or ""):
        context["ticket_id"] = ticket_id
    paths = re.findall(r'(?:"([^"]+)"|\'([^\']+)\'|([A-Za-z]:\\[^\s，。；;]+))', query or "")
    normalized_paths = [next(part for part in match if part) for match in paths]
    for index, path in enumerate(normalized_paths):
        context[f"path_{index}"] = path
    if normalized_paths:
        context["file_path"] = normalized_paths[0]
    job_match = re.search(
        r"(?:job_id|任务编号|任务ID|任务|job\b)\s*[:：=#]?\s*"
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|(?=[A-Za-z0-9_.:-]*\d)[A-Za-z0-9][A-Za-z0-9_.:-]{5,})",
        query or "",
    )
    if job_match:
        context["job_id"] = job_match.group(1).rstrip("，。；;,")
    return context


QUERY_CONTEXT_FIELDS = frozenset({"ticket_id", "file_path", "path_0", "path_1", "job_id"})


def _missing_required_context(context: dict[str, Any], required: list[str]) -> list[str]:
    return [field for field in required if context.get(field) in (None, "", [], {})]


_registry = ChatIntentRegistry()


def _query_matches(query: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    return any(re.search(re.escape(p), query, re.IGNORECASE) for p in patterns)


def _count_query_matches(query: str, patterns: list[str]) -> int:
    if not patterns:
        return 0
    return sum(1 for p in patterns if re.search(re.escape(p), query, re.IGNORECASE))


def intent_matches_query(query: str, intent: ChatIntentPlugin) -> bool:
    """判断话术是否满足该 Chat Intent 的 match 规则（不含工单号校验）。"""
    if intent.require_any and not _query_matches(query, intent.require_any):
        return False
    if intent.require_all and not all(
        re.search(re.escape(p), query, re.IGNORECASE) for p in intent.require_all
    ):
        return False
    if intent.require_any_secondary and not _query_matches(query, intent.require_any_secondary):
        return False
    return bool(intent.require_any or intent.require_all or intent.require_any_secondary)


def rank_intent_match(query: str, intent: ChatIntentPlugin) -> tuple[int, int, int, str]:
    """匹配得分（越大越优先）：priority → secondary 命中数 → primary 命中数 → workflow 名。"""
    secondary_hits = _count_query_matches(query, intent.require_any_secondary)
    primary_hits = _count_query_matches(query, intent.require_any)
    all_hits = _count_query_matches(query, intent.require_all)
    return (intent.priority, secondary_hits + all_hits, primary_hits, intent.workflow)


def find_matching_intents(query: str, source: str | None) -> list[ChatIntentPlugin]:
    """返回所有匹配的 Chat Intent（已按 rank 降序）。"""
    from src.core.workflows.metadata_repo import is_plugin_chat_active

    _registry.load()
    if source:
        auto = [i for i in _registry._intents if source in i.auto_sources]
        if auto:
            auto.sort(key=lambda i: rank_intent_match(query or "", i), reverse=True)
            return auto

    matched = [
        i
        for i in _registry._intents
        if intent_matches_query(query, i) and is_plugin_chat_active(i.workflow)
    ]
    if not matched:
        return []
    matched.sort(key=lambda i: rank_intent_match(query, i), reverse=True)
    return matched


def match_chat_workflow(query: str, source: str | None) -> ChatIntentPlugin | None:
    matched = find_matching_intents(query, source)
    if not matched:
        return None
    # Webhook auto_sources 已在 find_matching_intents 内直接返回
    if source and source in (matched[0].auto_sources or []):
        return matched[0]
    extracted = _extract_context_from_query(query)
    for intent in matched:
        query_context = {
            target: extracted.get(source)
            for target, source in intent.context_from_query.items()
            if extracted.get(source) not in (None, "")
        }
        initial = {**intent.context_defaults, **extracted, **query_context}
        query_required = [field for field in intent.required_context if field in QUERY_CONTEXT_FIELDS]
        if not _missing_required_context(initial, query_required):
            return intent
    return None


def build_chat_workflow_context(state: dict[str, Any], intent: ChatIntentPlugin) -> dict[str, Any]:
    query = _current_query(state)

    ctx = dict(intent.context_defaults)
    # ticket_id 仅来自当前消息，禁止从 state / 历史会话映射。
    state_mapping = {
        k: v for k, v in intent.context_from_state.items() if k != "ticket_id" and v != "ticket_id"
    }
    ctx.update(map_state_to_context(state, state_mapping))
    extracted = _extract_context_from_query(query)
    ctx.update(extracted)
    ctx.update(
        {
            target: extracted.get(source)
            for target, source in intent.context_from_query.items()
            if extracted.get(source) not in (None, "")
        }
    )
    missing = _missing_required_context(ctx, intent.required_context)
    if missing:
        if missing == ["ticket_id"]:
            raise MissingTicketIdError(TICKET_REQUIRED_MSG)
        raise MissingRequiredContextError(missing)
    if query:
        ctx.setdefault("change_background", query[:500])
    if state.get("ticket_title"):
        ctx.setdefault("ticket_title", state.get("ticket_title"))
    return ctx


def build_default_workflow_context(state: dict[str, Any]) -> dict[str, Any]:
    """无 Chat Intent 时的通用上下文，不施加任何领域必填约束。"""
    query = _current_query(state)
    context: dict[str, Any] = {
        "query": query,
        "uploaded_file_path": state.get("uploaded_file_path"),
    }
    context.update(_extract_context_from_query(query))
    return {key: value for key, value in context.items() if value not in (None, "")}


def format_workflow_start_message(intent: ChatIntentPlugin, run_id: str, context: dict[str, Any]) -> str:
    tpl = get_template(intent.workflow)
    if tpl:
        active = resolve_active_steps(tpl, context, run_id=run_id)
        desc = format_steps_flow(active)
    else:
        desc = intent.workflow
    ticket = context.get("ticket_id") or "—"
    if intent.response_template:
        return intent.response_template.format(
            run_id=run_id,
            ticket_id=ticket,
            workflow_description=desc,
            workflow=intent.workflow,
        )
    return (
        f"[OK] 已启动 Workflow `{intent.workflow}`\n\n"
        f"- **流程 ID**: `{run_id}`\n"
        f"- **工单**: {ticket}\n"
        f"- **说明**: {desc}\n"
    )


def get_chat_intent_registry() -> ChatIntentRegistry:
    return _registry
