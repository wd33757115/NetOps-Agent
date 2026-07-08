# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""聊天 Workflow 意图匹配测试。"""

from src.core.plugins.chat_intent import match_chat_workflow


def test_greeting_does_not_match_workflow():
    assert match_chat_workflow("你好", "chat") is None
    assert match_chat_workflow("打个招呼", "chat") is None


def test_patrol_does_not_match_firewall_workflow():
    assert match_chat_workflow("巡检设备", "chat") is None


def test_patrol_analysis_workflow_does_not_require_ticket():
    intent = match_chat_workflow("巡检设备并分析变化", "chat")

    assert intent is not None
    assert intent.workflow == "patrol-change-event"
    assert intent.required_context == []


def test_firewall_workflow_requires_ticket_in_query():
    assert match_chat_workflow("生成防火墙策略并编写变更工单", "chat") is None
    intent = match_chat_workflow(
        "根据工单REQ2025，用策略文件生成防火墙策略并编写变更工单",
        "chat",
    )
    assert intent is not None
    assert intent.workflow == "itsm-firewall-change"


def test_webhook_source_skips_ticket_check():
    intent = match_chat_workflow("", "itsm_webhook")
    assert intent is not None
    assert intent.workflow == "itsm-firewall-change"


def test_rule_asset_submit_requires_job_id_in_query():
    assert match_chat_workflow("提交规则资产审批", "chat") is None
    assert match_chat_workflow("提交规则资产审批 job status", "chat") is None

    intent = match_chat_workflow(
        "提交规则资产审批 job_id=689eb656-815d-49c6-9961-90b141bb43c2",
        "chat",
    )

    assert intent is not None
    assert intent.workflow == "rule-asset-submit"


def test_rule_asset_approve_requires_job_id_in_query():
    assert match_chat_workflow("审批激活规则资产", "chat") is None
    assert match_chat_workflow("审批激活规则资产 job pending", "chat") is None

    intent = match_chat_workflow(
        "审批激活规则资产 任务 689eb656-815d-49c6-9961-90b141bb43c2",
        "chat",
    )

    assert intent is not None
    assert intent.workflow == "rule-asset-approve"
