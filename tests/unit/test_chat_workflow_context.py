# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""聊天 Workflow 上下文：必填字段由插件声明。"""

from pathlib import Path

import pytest

from src.core.plugins.chat_intent import (
    ChatIntentPlugin,
    MissingRequiredContextError,
    MissingTicketIdError,
    build_chat_workflow_context,
    build_default_workflow_context,
    require_ticket_id_from_query,
)


def _firewall_intent() -> ChatIntentPlugin:
    return ChatIntentPlugin(
        workflow="itsm-firewall-change",
        priority=100,
        description="test",
        plugin_dir=Path("."),
        required_context=["ticket_id"],
        context_from_state={"ticket_id": "ticket_id", "policy_file_url": "uploaded_file_path"},
        context_defaults={"change_purpose": "默认目的"},
    )


def test_require_ticket_from_current_message():
    assert require_ticket_id_from_query("根据工单REQ2025，生成变更工单") == "REQ2025"


def test_require_ticket_raises_without_id():
    with pytest.raises(MissingTicketIdError):
        require_ticket_id_from_query("请生成防火墙策略")


def test_build_context_ignores_stale_state_ticket():
    state = {
        "ticket_id": "test001",
        "uploaded_file_path": "/tmp/policy.xlsx",
        "messages": [{"content": "根据工单REQ2025，编写变更工单"}],
    }
    ctx = build_chat_workflow_context(state, _firewall_intent())
    assert ctx["ticket_id"] == "REQ2025"
    assert ctx["policy_file_url"] == "/tmp/policy.xlsx"


def test_generic_workflow_context_does_not_require_ticket():
    intent = ChatIntentPlugin(
        workflow="patrol-change-event",
        priority=100,
        description="test",
        plugin_dir=Path("."),
        context_defaults={"filter_params": {}, "publish_events": True},
    )
    state = {"messages": [{"content": "巡检设备并分析变化"}]}

    ctx = build_chat_workflow_context(state, intent)

    assert ctx["filter_params"] == {}
    assert ctx["publish_events"] is True
    assert "ticket_id" not in ctx


def test_default_workflow_context_is_domain_neutral():
    ctx = build_default_workflow_context(
        {
            "messages": [{"content": "执行自定义流程"}],
            "uploaded_file_path": "/tmp/input.txt",
        }
    )

    assert ctx["query"] == "执行自定义流程"
    assert ctx["uploaded_file_path"] == "/tmp/input.txt"
    assert "ticket_id" not in ctx


def test_generic_required_context_is_not_ticket_specific():
    intent = ChatIntentPlugin(
        workflow="site-audit",
        priority=50,
        description="test",
        plugin_dir=Path("."),
        required_context=["site_id"],
        context_from_state={"site_id": "selected_site"},
    )

    ctx = build_chat_workflow_context(
        {
            "messages": [{"content": "执行站点审计"}],
            "selected_site": "XA-DC-01",
        },
        intent,
    )
    assert ctx["site_id"] == "XA-DC-01"

    with pytest.raises(MissingRequiredContextError) as exc_info:
        build_chat_workflow_context(
            {"messages": [{"content": "执行站点审计"}]},
            intent,
        )
    assert exc_info.value.missing_fields == ["site_id"]


def test_query_paths_can_be_mapped_by_intent():
    intent = ChatIntentPlugin(
        workflow="patrol-history-change-event",
        priority=100,
        description="test",
        plugin_dir=Path("."),
        required_context=["baseline_path", "current_path"],
        context_from_query={
            "baseline_path": "path_0",
            "current_path": "path_1",
        },
    )
    query = (
        '对比季度巡检，上一季度 "C:\\data\\previous"，'
        '本季度 "C:\\data\\current"'
    )

    ctx = build_chat_workflow_context(
        {"messages": [{"content": query}]},
        intent,
    )

    assert ctx["baseline_path"] == r"C:\data\previous"
    assert ctx["current_path"] == r"C:\data\current"


def test_query_job_id_can_be_mapped_by_intent():
    intent = ChatIntentPlugin(
        workflow="rule-asset-approve",
        priority=100,
        description="test",
        plugin_dir=Path("."),
        required_context=["job_id"],
        context_from_query={"job_id": "job_id"},
        context_defaults={"actor": "chat"},
    )
    query = "审批激活规则资产 job_id=689eb656-815d-49c6-9961-90b141bb43c2"

    ctx = build_chat_workflow_context(
        {"messages": [{"content": query}]},
        intent,
    )

    assert ctx["job_id"] == "689eb656-815d-49c6-9961-90b141bb43c2"
    assert ctx["actor"] == "chat"


@pytest.mark.parametrize(
    "query",
    [
        "审批激活规则资产 任务ID: 689eb656-815d-49c6-9961-90b141bb43c2",
        "审批激活规则资产 任务编号：689eb656-815d-49c6-9961-90b141bb43c2",
    ],
)
def test_chinese_job_id_labels_do_not_pollute_value(query: str):
    intent = ChatIntentPlugin(
        workflow="rule-asset-approve",
        priority=100,
        description="test",
        plugin_dir=Path("."),
        required_context=["job_id"],
        context_from_query={"job_id": "job_id"},
    )

    ctx = build_chat_workflow_context(
        {"messages": [{"content": query}]},
        intent,
    )

    assert ctx["job_id"] == "689eb656-815d-49c6-9961-90b141bb43c2"
