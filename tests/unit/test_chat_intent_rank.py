# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Chat Intent 同 priority tie-break 测试。"""

from dataclasses import replace
from pathlib import Path

from src.core.plugins.chat_intent import (
    ChatIntentPlugin,
    find_matching_intents,
    intent_matches_query,
    match_chat_workflow,
    rank_intent_match,
)


def _intent(
    workflow: str,
    *,
    priority: int = 100,
    require_any: list[str] | None = None,
    require_any_secondary: list[str] | None = None,
) -> ChatIntentPlugin:
    return ChatIntentPlugin(
        workflow=workflow,
        priority=priority,
        description="test",
        plugin_dir=Path("."),
        require_any=require_any or ["防火墙"],
        require_any_secondary=require_any_secondary or [],
    )


def test_rank_prefers_more_secondary_hits():
    a = _intent("workflow-a", priority=110, require_any_secondary=["变更工单"])
    b = _intent("workflow-b", priority=110, require_any_secondary=["LLM", "结果分析"])
    query = "根据工单 REQ2025001 防火墙策略 LLM 结果分析"
    assert rank_intent_match(query, b) > rank_intent_match(query, a)


def test_find_matching_intents_sorted():
    a = _intent("workflow-a", priority=110, require_any_secondary=["变更工单"])
    b = _intent("workflow-b", priority=110, require_any_secondary=["LLM", "结果分析"])
    query = "根据工单 REQ2025001 防火墙 LLM 结果分析"
    ranked = sorted([a, b], key=lambda i: rank_intent_match(query, i), reverse=True)
    assert ranked[0].workflow == "workflow-b"


def test_firewall_change_without_llm_keywords():
    intent = match_chat_workflow(
        "根据工单 REQ2025001 用策略文件生成防火墙策略并编写变更工单",
        "chat",
    )
    assert intent is not None
    assert intent.workflow == "itsm-firewall-change"
