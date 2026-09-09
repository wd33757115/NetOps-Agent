# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""heuristic_plan 与 supervisor Workflow 路由单元测试。"""

from unittest.mock import MagicMock, patch

from src.agents.supervisor.graph_v2 import supervisor_node_v2
from src.agents.supervisor.heuristic_plan import (
    WORKFLOW_ONLY_SKILLS,
    build_heuristic_execution_plan,
    build_heuristic_skill_params,
    ordered_trigger_skills,
)
from src.skill_system.router import SkillMatch


def test_firewall_user_can_disable_merging():
    params = build_heuristic_skill_params('firewall-policy-generator', '工单 T1，生成防火墙策略，不要合并', 'request.xlsx', 'T1')
    assert params['merge_policies'] is False
    assert params['policy_file_url'] == 'request.xlsx'


def test_workflow_only_skills_excluded_from_heuristic_chain():
    query = "编写变更工单，工单号 REQ001，回调 ITSM"
    matches = [
        SkillMatch(
            skill_name="itsm-change-ticket-writer",
            confidence=0.95,
            match_type="trigger",
            reason="匹配触发词: 变更工单",
        ),
        SkillMatch(
            skill_name="device-patrol",
            confidence=0.95,
            match_type="trigger",
            reason="匹配触发词: 巡检",
        ),
    ]
    ordered = ordered_trigger_skills(query, ["itsm-change-ticket-writer", "device-patrol"], matches)
    assert "itsm-change-ticket-writer" not in ordered
    assert ordered == ["device-patrol"]

    plan = build_heuristic_execution_plan(
        query,
        ["itsm-change-ticket-writer", "device-patrol"],
        matches,
        None,
    )
    assert plan is not None
    assert len(plan.skills) == 1
    assert plan.skills[0].skill_name == "device-patrol"


def test_heuristic_chain_capped_at_max_length():
    names = [f"skill-{i}" for i in range(4)]
    matches = [
        SkillMatch(skill_name=n, confidence=0.95, match_type="trigger", reason=f"匹配触发词: {n}")
        for n in names
    ]
    plan = build_heuristic_execution_plan("skill-0 skill-1 skill-2 skill-3", names, matches, None)
    assert plan is None


def test_supervisor_routes_chat_intent_without_ticket_to_workflow_starter():
    """CHAT.intent 话术命中时优先 Workflow，缺工单由 workflow_starter 提示。"""
    fake_intent = MagicMock()
    fake_intent.workflow = "itsm-firewall-change"

    state = {
        "messages": [MagicMock(content="防火墙策略 编写变更工单")],
        "loaded_skills": ["firewall-policy-generator"],
        "skill_instructions": {},
        "skill_matches": [],
        "source": "chat",
        "uploaded_file_path": None,
    }

    with patch(
        "src.core.plugins.chat_intent.find_matching_intents",
        return_value=[fake_intent],
    ):
        result = supervisor_node_v2(state)

    assert result["next_agent"] == "workflow_starter"
    assert result["workflow_type"] == "itsm-firewall-change"
    assert result.get("execution_plan") is None


def test_build_heuristic_skill_params_firewall():
    params = build_heuristic_skill_params(
        "firewall-policy-generator",
        "生成防火墙策略，工单 rg001",
        "/uploads/policy.xlsx",
        "rg001",
    )
    assert params["ticket_id"] == "rg001"
    assert params["policy_file_url"] == "/uploads/policy.xlsx"
    assert params["ticket_title"] == "防火墙策略生成"


def test_workflow_only_skills_constant():
    assert "itsm-change-ticket-writer" in WORKFLOW_ONLY_SKILLS
    assert "itsm-callback" in WORKFLOW_ONLY_SKILLS


def test_firewall_business_paths_opt_in():
    for query, expected in [('生成防火墙策略，展示业务系统和拓扑路径', True),
                            ('生成防火墙策略，不需要业务系统说明', False)]:
        params = build_heuristic_skill_params('firewall-policy-generator', query, '/uploads/policy.xlsx', 'TEST')
        assert params['include_business_paths'] is expected
    params = build_heuristic_skill_params('firewall-policy-generator', '生成防火墙策略', '/uploads/policy.xlsx', 'TEST')
    assert 'include_business_paths' not in params
