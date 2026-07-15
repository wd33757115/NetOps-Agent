# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Supervisor v2 图编排单元测试（无 LLM / 无真实设备）。"""

from src.agents.supervisor.graph_v2 import (
    INTERMEDIATE_RESULTS_RESET,
    _build_send_payload,
    _detect_dependency_cycle,
    _extract_ticket_id,
    _filter_skill_matches,
    _has_trigger_match,
    _is_knowledge_question,
    _merge_dicts,
    _merge_params_with_deps,
    _next_runnable_task,
    _parallel_runnable_tasks,
    _should_fallback_to_rag_after_skills,
    build_supervisor_graph_v2,
    orchestrator_dispatch,
    route_after_executor_v2,
)
from src.agents.supervisor.heuristic_plan import (
    build_heuristic_execution_plan as _build_heuristic_execution_plan,
)
from src.agents.supervisor.models_v2 import ExecutionPlan, SkillTaskSpec
from src.skill_system.router import SkillMatch


def test_extract_ticket_id_from_query():
    assert _extract_ticket_id("生成防火墙策略，工单号：rg001") == "rg001"
    assert _extract_ticket_id("ticket_id: ABC-99") == "ABC-99"
    assert _extract_ticket_id("帮我生成防火墙策略，工单号test001") == "test001"


def test_knowledge_question_not_rule_scheduled_to_skill():
    assert _is_knowledge_question("交换机接口down了怎么办") is True
    matches = [
        SkillMatch(
            skill_name="device-backup",
            confidence=0.49,
            match_type="semantic",
            reason="语义相似度: 0.49",
        )
    ]
    assert _filter_skill_matches("交换机接口down了怎么办", matches) == []
    plan = _build_heuristic_execution_plan(
        "交换机接口down了怎么办",
        ["device-backup"],
        matches,
        None,
    )
    assert plan is None


def test_heuristic_plan_for_firewall_trigger():
    matches = [
        SkillMatch(
            skill_name="firewall-policy-generator",
            confidence=0.95,
            match_type="trigger",
            reason="匹配触发词: 生成防火墙策略",
        )
    ]
    plan = _build_heuristic_execution_plan(
        "生成防火墙策略，工单号：rg001",
        ["firewall-policy-generator"],
        matches,
        None,
    )
    assert plan is not None
    assert not plan.fallback_to_rag
    assert plan.skills[0].skill_name == "firewall-policy-generator"
    assert plan.skills[0].parameters.get("ticket_id") == "rg001"
    assert _has_trigger_match(matches, ["firewall-policy-generator"])


def test_heuristic_plan_multi_skill_sequential_firewall_then_patrol():
    query = "帮我生成防火墙策略，工单号test001，生成之后帮我进行巡检网络设备"
    matches = [
        SkillMatch(
            skill_name="device-patrol",
            confidence=0.95,
            match_type="trigger",
            reason="匹配触发词: 巡检",
        ),
        SkillMatch(
            skill_name="firewall-policy-generator",
            confidence=0.95,
            match_type="trigger",
            reason="匹配触发词: 生成防火墙策略",
        ),
    ]
    loaded = ["device-patrol", "firewall-policy-generator"]
    plan = _build_heuristic_execution_plan(query, loaded, matches, None)
    assert plan is not None
    assert len(plan.skills) == 2
    assert plan.skills[0].skill_name == "firewall-policy-generator"
    assert plan.skills[0].parameters.get("ticket_id") == "test001"
    assert plan.skills[1].skill_name == "device-patrol"
    assert plan.skills[1].depends_on == ["firewall-policy-generator"]
    assert plan.skills[1].parameters.get("filter_params") == {}
    task = SkillTaskSpec(
        skill_name="config-backup",
        parameters={"device": "sw1"},
        depends_on=["device-inspection"],
    )
    intermediate = {
        "device-inspection": {
            "success": True,
            "data": {"devices": ["sw1", "sw2"]},
            "message": "巡检完成",
        }
    }
    params = _merge_params_with_deps(task, intermediate, None, "")
    assert params["device"] == "sw1"
    assert "device-inspection_output" in params
    assert params["previous_data"] == {"devices": ["sw1", "sw2"]}


def test_next_runnable_task_respects_dependencies():
    tasks = [
        SkillTaskSpec(skill_name="a", parameters={}),
        SkillTaskSpec(skill_name="b", parameters={}, depends_on=["a"]),
    ]
    assert _next_runnable_task(tasks, set()).skill_name == "a"
    assert _next_runnable_task(tasks, {"a"}, {"a": {"success": True}}).skill_name == "b"
    assert _next_runnable_task(tasks, {"a", "b"}) is None


def test_merge_dicts_reset_clears_stale_skill_results():
    stale = {"firewall-policy-generator": {"success": False, "error": "old"}}
    assert _merge_dicts(stale, INTERMEDIATE_RESULTS_RESET) == {}
    assert _merge_dicts(stale, {"firewall-policy-generator": {"success": True}})[
        "firewall-policy-generator"
    ]["success"]


def test_orchestrator_runs_after_intermediate_reset():
    """模拟 pre_process 清空后，不应因 checkpoint 残留而跳过 Skill 执行。"""
    plan = ExecutionPlan(
        reasoning="防火墙",
        skills=[
            SkillTaskSpec(skill_name="firewall-policy-generator", parameters={"ticket_id": "rg001"})
        ],
        execution_mode="parallel",
    )
    state = {
        "execution_plan": plan,
        "intermediate_results": _merge_dicts(
            {"firewall-policy-generator": {"success": False, "error": "stale"}},
            INTERMEDIATE_RESULTS_RESET,
        ),
        "uploaded_file_path": None,
        "ticket_id": "rg001",
        "messages": [],
    }
    result = orchestrator_dispatch(state)
    assert isinstance(result, list)
    assert len(result) == 1
    assert getattr(result[0], "node", None) == "skill_executor_v2"


def test_orchestrator_parallel_fan_out():
    plan = ExecutionPlan(
        reasoning="并行巡检与备份",
        skills=[
            SkillTaskSpec(skill_name="device-inspection", parameters={}),
            SkillTaskSpec(skill_name="config-backup", parameters={}),
        ],
        execution_mode="parallel",
    )
    state = {
        "execution_plan": plan,
        "intermediate_results": {},
        "uploaded_file_path": None,
        "ticket_id": "",
        "messages": [],
    }
    result = orchestrator_dispatch(state)
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(getattr(item, "node", None) == "skill_executor_v2" for item in result)


def test_orchestrator_sequential_single_send():
    plan = ExecutionPlan(
        reasoning="先巡检后备份",
        skills=[
            SkillTaskSpec(skill_name="device-inspection", parameters={}),
            SkillTaskSpec(
                skill_name="config-backup", parameters={}, depends_on=["device-inspection"]
            ),
        ],
        execution_mode="sequential",
    )
    state = {
        "execution_plan": plan,
        "intermediate_results": {},
        "uploaded_file_path": None,
        "ticket_id": "",
        "messages": [],
    }
    result = orchestrator_dispatch(state)
    assert getattr(result, "node", None) == "skill_executor_v2"
    assert result.arg["current_skill_task"].skill_name == "device-inspection"


def test_route_after_executor_loops_on_sequential():
    plan = ExecutionPlan(
        reasoning="顺序执行",
        skills=[
            SkillTaskSpec(skill_name="a", parameters={}),
            SkillTaskSpec(skill_name="b", parameters={}, depends_on=["a"]),
        ],
        execution_mode="sequential",
    )
    state = {"execution_plan": plan, "intermediate_results": {"a": {"success": True}}}
    assert route_after_executor_v2(state) == "orchestrator"


def test_route_after_executor_parallel_goes_to_aggregator():
    plan = ExecutionPlan(
        reasoning="并行",
        skills=[SkillTaskSpec(skill_name="a", parameters={})],
        execution_mode="parallel",
    )
    state = {"execution_plan": plan, "intermediate_results": {"a": {"success": True}}}
    assert route_after_executor_v2(state) == "final_aggregator"


def test_route_after_executor_parallel_failed_skills_fallback_rag():
    plan = ExecutionPlan(
        reasoning="并行失败",
        skills=[SkillTaskSpec(skill_name="a", parameters={})],
        execution_mode="parallel",
        fallback_to_rag=True,
    )
    state = {"execution_plan": plan, "intermediate_results": {"a": {"success": False}}}
    assert route_after_executor_v2(state) == "knowledge_qa"


def test_build_supervisor_graph_v2_compiles():
    graph = build_supervisor_graph_v2()
    assert graph is not None
    assert hasattr(graph, "invoke")


def test_build_send_payload_creates_skill_decision():
    plan = ExecutionPlan(reasoning="test", skills=[], execution_mode="parallel")
    state = {
        "execution_plan": plan,
        "intermediate_results": {},
        "uploaded_file_path": "/tmp/x",
        "ticket_id": "T001",
    }
    task = SkillTaskSpec(skill_name="config-backup", parameters={})
    payload = _build_send_payload(state, task)
    assert payload["skill_decision"].skill_name == "config-backup"
    assert payload["skill_decision"].parameters.get("uploaded_file_path") == "/tmp/x"


def test_next_runnable_task_skips_failed_dependency():
    tasks = [
        SkillTaskSpec(skill_name="a", parameters={}),
        SkillTaskSpec(skill_name="b", parameters={}, depends_on=["a"]),
    ]
    intermediate = {"a": {"success": False, "message": "failed"}}
    assert _next_runnable_task(tasks, {"a"}, intermediate) is None


def test_parallel_runnable_respects_dependencies():
    pending = [
        SkillTaskSpec(skill_name="a", parameters={}),
        SkillTaskSpec(skill_name="b", parameters={}, depends_on=["a"]),
    ]
    assert len(_parallel_runnable_tasks(pending, set(), {})) == 1
    assert _parallel_runnable_tasks(pending, set(), {})[0].skill_name == "a"
    runnable = _parallel_runnable_tasks(
        [SkillTaskSpec(skill_name="b", parameters={}, depends_on=["a"])],
        {"a"},
        {"a": {"success": True}},
    )
    assert len(runnable) == 1
    assert runnable[0].skill_name == "b"


def test_orchestrator_parallel_with_deps_runs_in_waves():
    plan = ExecutionPlan(
        reasoning="先 A 后 B",
        skills=[
            SkillTaskSpec(skill_name="a", parameters={}),
            SkillTaskSpec(skill_name="b", parameters={}, depends_on=["a"]),
        ],
        execution_mode="parallel",
    )
    state = {
        "execution_plan": plan,
        "intermediate_results": {},
        "uploaded_file_path": None,
        "ticket_id": "",
        "messages": [],
    }
    wave1 = orchestrator_dispatch(state)
    assert isinstance(wave1, list)
    assert len(wave1) == 1
    assert wave1[0].arg["current_skill_task"].skill_name == "a"

    state_after_a = {
        **state,
        "intermediate_results": {"a": {"success": True, "message": "ok"}},
    }
    assert route_after_executor_v2(state_after_a) == "orchestrator"
    wave2 = orchestrator_dispatch(state_after_a)
    assert isinstance(wave2, list)
    assert len(wave2) == 1
    assert wave2[0].arg["current_skill_task"].skill_name == "b"


def test_detect_dependency_cycle():
    tasks = [
        SkillTaskSpec(skill_name="a", parameters={}, depends_on=["b"]),
        SkillTaskSpec(skill_name="b", parameters={}, depends_on=["a"]),
    ]
    assert _detect_dependency_cycle(tasks) is True


def test_should_fallback_to_rag_when_all_skills_failed():
    plan = ExecutionPlan(
        reasoning="test",
        skills=[SkillTaskSpec(skill_name="firewall-policy-generator", parameters={})],
        execution_mode="parallel",
        fallback_to_rag=True,
    )
    state = {
        "execution_plan": plan,
        "intermediate_results": {
            "firewall-policy-generator": {"success": False, "message": "err"},
        },
    }
    assert _should_fallback_to_rag_after_skills(state) is True


def test_heuristic_plan_for_official_document_trigger():
    matches = [
        SkillMatch(
            skill_name="official-document-writing",
            confidence=0.95,
            match_type="trigger",
            reason="匹配触发词: 写请示",
        )
    ]
    query = "我写一份请示，向信息中心申请采购一台核心交换机"
    plan = _build_heuristic_execution_plan(
        query,
        ["official-document-writing"],
        matches,
        None,
    )
    assert plan is not None
    assert plan.skills[0].skill_name == "official-document-writing"
    assert plan.skills[0].parameters.get("user_query") == query
    assert plan.skills[0].parameters.get("document_type") == "请示"
    assert not plan.fallback_to_rag
