# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Supervisor v2 — 高级协同模式

架构：pre_process → supervisor(ExecutionPlan) → orchestrator(Send fan-out)
      → skill_executor_v2(Map) → final_aggregator(Reduce) / knowledge_qa → END
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from src.agents.knowledge_qa.agent import knowledge_qa_node
from src.agents.supervisor.heuristic_plan import (
    build_heuristic_execution_plan as _build_heuristic_execution_plan,
)
from src.agents.supervisor.models_v2 import (
    ExecutionPlan,
    ExecutionPlanModel,
    SkillTaskSpec,
)
from src.common.config import get_settings
from src.common.metrics import increment_counter, observe_histogram
from src.common.ticket_utils import extract_ticket_id as _extract_ticket_id
from src.core.logging import get_logger
from src.skill_system import get_skill_system
from src.skill_system.router import SkillMatch
from src.skills.registry import skill_registry
from src.skills.skill_base import SkillDecision, SkillResult

settings = get_settings()
log = get_logger(__name__)

AgentType = Literal["supervisor", "skill_executor", "knowledge_qa", "workflow_starter", "end"]
PRE_PROCESS_TOP_K = settings.PRE_PROCESS_TOP_K
# 语义路由低于此阈值不加载 Skill（避免知识问句误匹配 device-backup 等）
SEMANTIC_SKILL_MIN_CONFIDENCE = float(os.getenv("SEMANTIC_SKILL_MIN_CONFIDENCE", "0.72"))

llm = ChatDeepSeek(
    model=settings.LLM_MODEL,
    temperature=0.05,
    api_key=settings.DEEPSEEK_API_KEY,
    request_timeout=30,
)
llm_with_execution_plan = llm.with_structured_output(
    ExecutionPlanModel, method="function_calling"
)


# 新一轮用户消息时由 pre_process_node 写入，用于清空 checkpoint 中的 intermediate_results
INTERMEDIATE_RESULTS_RESET: dict[str, Any] = {"__reset_intermediate_results__": True}


def _merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(right, dict) and right.get("__reset_intermediate_results__"):
        return {}
    merged = dict(left or {})
    if right:
        merged.update(right)
    return merged


class SupervisorStateV2(TypedDict):
    messages: Annotated[list, "add_messages"]
    next_agent: AgentType | None
    context: str | None
    source: str | None
    task_id: str | None
    celery_task_id: str | None
    knowledge_references: list | None
    uploaded_file_path: str | None
    ticket_id: str | None
    thread_id: str | None
    metadata_filters: dict | None
    skill_decision: SkillDecision | None
    skill_result: SkillResult | None
    fallback_to_rag: bool | None
    async_mode: bool | None
    agent_type: str | None
    # v2 协同字段
    skill_matches: list[SkillMatch] | None
    skill_instructions: dict[str, str] | None
    execution_plan: ExecutionPlan | None
    intermediate_results: Annotated[dict[str, Any] | None, _merge_dicts]
    loaded_skills: list[str] | None
    current_skill_task: SkillTaskSpec | None
    user_id: str | None
    user_role: str | None
    workflow_type: str | None
    workflow_run_id: str | None
    langfuse_parent_trace_id: str | None
    message_id: str | None


def _get_query(state: SupervisorStateV2) -> str:
    return state["messages"][-1].content


def _route_skills(
    query: str,
    top_k: int = PRE_PROCESS_TOP_K,
    user_role: str | None = None,
    user_id: str | None = None,
) -> list[SkillMatch]:
    skill_system = get_skill_system()
    if skill_system.router:
        return skill_system.router.route(query, top_k=top_k, user_role=user_role, user_id=user_id)
    return skill_system.route(query, top_k=top_k)


def _has_trigger_match(skill_matches: list[SkillMatch], loaded_skills: list[str]) -> bool:
    loaded = set(loaded_skills)
    return any(
        m.skill_name in loaded
        and m.match_type == "trigger"
        and m.confidence >= 0.85
        for m in skill_matches
    )


def _is_knowledge_question(query: str) -> bool:
    """
    判断是否为知识库/方法论类问句（应走 RAG，而非低置信 Skill）。
    含明确运维动作触发词时返回 False。
    """
    q = query.strip()
    operational_patterns = [
        r"生成防火墙",
        r"防火墙策略",
        r"备份.{0,8}配置",
        r"配置备份",
        r"设备备份",
        r"执行.{0,6}巡检",
        r"设备巡检",
        r"工单号",
        r"ticket[_\s-]?id",
        r"下发",
        r"生成.{0,6}策略",
        r"公文",
        r"写.{0,4}请示",
        r"写.{0,4}通知",
        r"写.{0,4}函",
        r"写.{0,4}报告",
        r"写.{0,4}总结",
        r"写.{0,4}纪要",
        r"一份请示",
        r"一份通知",
        r"公文写作",
        r"公文审核",
        r"入党申请",
        r"申请书",
        r"给我一份",
        r"写一份",
        r"撰写",
        r"textfsm",
        r"解析模板",
        r"生成.{0,8}模板",
    ]
    if any(re.search(p, q, re.IGNORECASE) for p in operational_patterns):
        return False

    knowledge_patterns = [
        r"怎么办",
        r"如何做",
        r"怎么做",
        r"如何",
        r"什么是",
        r"为什么",
        r"为啥",
        r"原理",
        r"步骤",
        r"排查",
        r"故障",
        r"标准操作",
        r"SOP",
        r"是什么意思",
        r"介绍",
        r"讲解",
        r"处理方法",
        r"处理流程",
    ]
    return any(re.search(p, q, re.IGNORECASE) for p in knowledge_patterns)


def _is_actionable_skill_match(match: SkillMatch) -> bool:
    """是否值得加载/执行该 Skill 匹配。"""
    if match.match_type == "trigger":
        return match.confidence >= 0.85
    if match.match_type == "tag":
        return match.confidence >= 0.75
    if match.match_type == "semantic":
        return match.confidence >= SEMANTIC_SKILL_MIN_CONFIDENCE
    return match.confidence >= 0.85


def _filter_skill_matches(query: str, matches: list[SkillMatch]) -> list[SkillMatch]:
    """过滤低置信语义匹配；知识类问句在无触发词时不保留 Skill。"""
    filtered = [m for m in matches if _is_actionable_skill_match(m)]
    if _is_knowledge_question(query) and not any(
        m.match_type == "trigger" and m.confidence >= 0.85 for m in filtered
    ):
        return []
    return filtered


def _resolve_workflow_intent(query: str, source: str | None):
    """匹配 CHAT.intent 插件；话术命中即走 Workflow（工单号由 workflow_starter 校验）。"""
    from src.core.plugins.chat_intent import find_matching_intents

    matched = find_matching_intents(query, source)
    return matched[0] if matched else None


def _merge_params_with_deps(
    task: SkillTaskSpec,
    intermediate_results: dict[str, Any] | None,
    uploaded_file_path: str | None,
    ticket_id: str | None,
) -> dict[str, Any]:
    from src.core.skills.chain_resolve import merge_upstream_params

    params = merge_upstream_params(
        task.skill_name,
        dict(task.parameters),
        list(task.depends_on),
        intermediate_results,
    )
    if uploaded_file_path:
        params.setdefault("uploaded_file_path", uploaded_file_path)
    if ticket_id:
        params.setdefault("ticket_id", ticket_id)
    return params


def _successful_deps(task: SkillTaskSpec, intermediate: dict[str, Any] | None) -> bool:
    """前置依赖必须已成功执行。"""
    intermediate = intermediate or {}
    for dep in task.depends_on:
        dep_result = intermediate.get(dep)
        if not isinstance(dep_result, dict) or not dep_result.get("success"):
            return False
    return True


def _deps_satisfied(
    task: SkillTaskSpec,
    completed: set[str],
    intermediate: dict[str, Any] | None = None,
) -> bool:
    if not all(dep in completed for dep in task.depends_on):
        return False
    return _successful_deps(task, intermediate)


def _detect_dependency_cycle(tasks: list[SkillTaskSpec]) -> bool:
    graph: dict[str, list[str]] = {t.skill_name: list(t.depends_on) for t in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if dfs(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(dfs(name) for name in graph)


def _next_runnable_task(
    tasks: list[SkillTaskSpec],
    completed: set[str],
    intermediate: dict[str, Any] | None = None,
) -> SkillTaskSpec | None:
    for task in tasks:
        if task.skill_name in completed:
            continue
        if _deps_satisfied(task, completed, intermediate):
            return task
    return None


def _parallel_runnable_tasks(
    pending: list[SkillTaskSpec],
    completed: set[str],
    intermediate: dict[str, Any] | None,
) -> list[SkillTaskSpec]:
    return [t for t in pending if _deps_satisfied(t, completed, intermediate)]


def _should_fallback_to_rag_after_skills(state: SupervisorStateV2) -> bool:
    """全部 Skill 失败且计划或 Skill 元数据允许 RAG 兜底。"""
    plan = state.get("execution_plan")
    intermediate = state.get("intermediate_results") or {}
    if not plan or not intermediate:
        return False

    tasks = _filter_conditional_tasks(plan, state)
    executed = [t for t in tasks if t.skill_name in intermediate]
    if not executed:
        return False

    if any(intermediate.get(t.skill_name, {}).get("success") for t in executed):
        return False

    if plan.fallback_to_rag:
        return True

    for task in executed:
        skill = skill_registry.get_skill(task.skill_name)
        if skill and skill.fallback_to_rag_if_fail:
            return True
    return False


def _filter_conditional_tasks(plan: ExecutionPlan, state: SupervisorStateV2) -> list[SkillTaskSpec]:
    if plan.execution_mode != "conditional":
        return list(plan.skills)
    intermediate = state.get("intermediate_results") or {}
    filtered: list[SkillTaskSpec] = []
    for task in plan.skills:
        condition_key = plan.conditions.get(task.skill_name)
        if not condition_key:
            filtered.append(task)
            continue
        for dep in task.depends_on:
            dep_result = intermediate.get(dep)
            if isinstance(dep_result, dict) and dep_result.get("success"):
                filtered.append(task)
                break
    return filtered


def _build_send_payload(state: SupervisorStateV2, task: SkillTaskSpec) -> dict[str, Any]:
    plan = state.get("execution_plan")
    reasoning = plan.reasoning if plan else "orchestrator dispatch"
    params = _merge_params_with_deps(
        task,
        state.get("intermediate_results"),
        state.get("uploaded_file_path"),
        state.get("ticket_id") or "",
    )
    messages = state.get("messages") or []
    if messages:
        params.setdefault("user_query", messages[-1].content)
    if state.get("message_id"):
        params.setdefault("_message_id", state["message_id"])
    if state.get("thread_id"):
        params.setdefault("_thread_id", state["thread_id"])
    if state.get("user_id"):
        params.setdefault("_user_id", state["user_id"])
    params.setdefault("_execution_source", "chat")
    return {
        "current_skill_task": task,
        "skill_decision": SkillDecision(
            reasoning=reasoning,
            skill_name=task.skill_name,
            parameters=params,
            fallback_to_rag=False,
        ),
    }


def pre_process_node(state: SupervisorStateV2) -> SupervisorStateV2:
    """SemanticRouter 多匹配 + SkillLoader 批量加载（Progressive Disclosure）。"""
    t_start = time.time()
    query = _get_query(state)
    log.info("supervisor_v2_pre_process", query_preview=query[:80])

    raw_matches = _route_skills(
        query,
        top_k=PRE_PROCESS_TOP_K,
        user_role=state.get("user_role"),
        user_id=state.get("user_id"),
    )
    matches = _filter_skill_matches(query, raw_matches)
    if len(matches) < len(raw_matches):
        log.info(
            "supervisor_v2_pre_process_filtered",
            raw_count=len(raw_matches),
            kept_count=len(matches),
            min_confidence=SEMANTIC_SKILL_MIN_CONFIDENCE,
        )
    skill_system = get_skill_system()
    skill_instructions: dict[str, str] = {}
    loaded_skills: list[str] = []

    for match in matches:
        try:
            content = (skill_system.get_skill_instructions(match.skill_name) or "").strip()
            if content:
                skill_instructions[match.skill_name] = content
                loaded_skills.append(match.skill_name)
            else:
                log.warning(
                    "supervisor_v2_pre_process_skill_empty",
                    skill_name=match.skill_name,
                    match_type=match.match_type,
                )
        except Exception as exc:
            log.exception(
                "supervisor_v2_pre_process_skill_load_failed",
                skill_name=match.skill_name,
                error=str(exc),
            )

    duration_ms = (time.time() - t_start) * 1000
    observe_histogram("supervisor_v2_pre_process_duration_ms", duration_ms)
    increment_counter("supervisor_v2_pre_process_total", tags={"loaded_count": str(len(loaded_skills))})
    log.info(
        "supervisor_v2_pre_process_complete",
        match_count=len(matches),
        loaded_count=len(loaded_skills),
        duration_ms=int(duration_ms),
    )

    ticket_id = _extract_ticket_id(query)
    ticket_update = {"ticket_id": ticket_id, "message_id": str(uuid.uuid4())}

    return {
        **state,
        **ticket_update,
        "skill_matches": matches,
        "skill_instructions": skill_instructions,
        "loaded_skills": loaded_skills,
        # 每轮对话清空 Skill / Workflow 路由状态，避免 checkpoint 污染后续普通聊天
        "intermediate_results": INTERMEDIATE_RESULTS_RESET,
        "workflow_type": None,
        "workflow_run_id": None,
        "next_agent": None,
        "execution_plan": None,
    }


def supervisor_node_v2(state: SupervisorStateV2) -> SupervisorStateV2:
    """Supervisor 决策节点：仅输出 ExecutionPlan，不直接执行 Skill。"""
    t_start = time.time()
    query = _get_query(state)
    uploaded_file_path = state.get("uploaded_file_path")
    skill_instructions = state.get("skill_instructions") or {}
    loaded_skills = state.get("loaded_skills") or []

    log.info("supervisor_v2_supervisor_begin", loaded_skills=loaded_skills)

    workflow_intent = _resolve_workflow_intent(query, state.get("source"))
    if workflow_intent:
        log.info("supervisor_v2_workflow_intent_matched", workflow=workflow_intent.workflow)
        increment_counter("supervisor_v2_plan_total", tags={"result": "workflow"})
        observe_histogram("supervisor_v2_supervisor_duration_ms", (time.time() - t_start) * 1000)
        return {
            **state,
            "workflow_type": workflow_intent.workflow,
            "next_agent": "workflow_starter",
            "fallback_to_rag": False,
        }

    if not loaded_skills:
        plan = ExecutionPlan(
            reasoning="无匹配 Skill，走 RAG 兜底",
            skills=[],
            execution_mode="parallel",
            fallback_to_rag=True,
        )
        increment_counter("supervisor_v2_plan_total", tags={"result": "rag_fallback"})
        observe_histogram("supervisor_v2_supervisor_duration_ms", (time.time() - t_start) * 1000)
        return {**state, "execution_plan": plan, "fallback_to_rag": True, "next_agent": "knowledge_qa"}

    skill_matches = state.get("skill_matches") or []
    heuristic_plan = _build_heuristic_execution_plan(
        query, loaded_skills, skill_matches, uploaded_file_path
    )
    # 仅触发词命中时用规则调度；低置信语义匹配改走 RAG / LLM 规划
    use_rule_plan = heuristic_plan is not None and _has_trigger_match(
        skill_matches, loaded_skills
    )

    if use_rule_plan:
        skill_names = " → ".join(
            f"{t.skill_name}{'←' + ','.join(t.depends_on) if t.depends_on else ''}"
            for t in heuristic_plan.skills
        )
        log.info("supervisor_v2_execution_plan_rule", skill_chain=skill_names)
        increment_counter("supervisor_v2_plan_total", tags={"result": "rule_plan"})
        observe_histogram("supervisor_v2_supervisor_duration_ms", (time.time() - t_start) * 1000)
        return {
            **state,
            "execution_plan": heuristic_plan,
            "fallback_to_rag": False,
            "next_agent": "skill_executor",
        }

    skills_block = []
    for name in loaded_skills:
        instructions = skill_instructions.get(name, "")
        skills_block.append(f"### Skill: {name}\n{instructions[:2000]}")

    prompt = f"""你是 NetOps 多 Skill 协同调度器。根据用户请求，从已加载的 Skill 中制定 ExecutionPlan。

【用户请求】
{query}

【上下文】
- uploaded_file_path: {uploaded_file_path or '无'}

【已加载 Skills 指令（Progressive Disclosure）】
{chr(10).join(skills_block)}

【任务】
1. 知识性问题（概念/原理/方法论）→ skills=[] 且 fallback_to_rag=true
2. 单 Skill 任务 → skills 仅含 1 项，execution_mode=parallel
3. 多 Skill 可并行 → execution_mode=parallel，depends_on=[]
4. 有先后顺序（A 结果供 B 使用）→ execution_mode=sequential，B.depends_on 含 A 的 skill_name
5. 条件执行 → execution_mode=conditional，在 conditions 中描述触发条件
6. 从用户话术中提取 parameters；不要编造文件路径

【输出】
reasoning、skills（skill_name/parameters/depends_on）、execution_mode、conditions、fallback_to_rag
"""

    try:
        plan_model = llm_with_execution_plan.invoke(prompt)
        plan = ExecutionPlan.from_model(plan_model)
        log.info(
            "supervisor_v2_execution_plan_llm",
            execution_mode=plan.execution_mode,
            skills=[s.skill_name for s in plan.skills],
        )
    except Exception as exc:
        log.warning("supervisor_v2_execution_plan_llm_failed", error=str(exc))
        if heuristic_plan and heuristic_plan.skills:
            plan = heuristic_plan
            plan.reasoning = f"{plan.reasoning}（LLM 不可用: {str(exc)[:80]}）"
            log.info(
                "supervisor_v2_execution_plan_fallback",
                skill_name=plan.skills[0].skill_name,
            )
        else:
            plan = ExecutionPlan(
                reasoning=f"LLM 决策失败: {str(exc)[:80]}",
                skills=[],
                fallback_to_rag=True,
            )

    tag = "skill_plan" if plan.skills and not plan.fallback_to_rag else "rag_fallback"
    increment_counter("supervisor_v2_plan_total", tags={"result": tag})
    observe_histogram("supervisor_v2_supervisor_duration_ms", (time.time() - t_start) * 1000)

    next_agent: AgentType = "knowledge_qa" if plan.fallback_to_rag or not plan.skills else "skill_executor"
    return {
        **state,
        "execution_plan": plan,
        "fallback_to_rag": plan.fallback_to_rag,
        "next_agent": next_agent,
    }


def orchestrator_dispatch(state: SupervisorStateV2):
    """
    动态编排：parallel 多 Send fan-out；sequential/conditional 单 Send 或结束。
    返回 Send 列表 / 节点名。
    """
    plan = state.get("execution_plan")
    if not plan or plan.fallback_to_rag or not plan.skills:
        return "knowledge_qa"

    tasks = _filter_conditional_tasks(plan, state)
    if not tasks:
        log.info("supervisor_v2_orchestrator_no_tasks")
        return "final_aggregator"

    if _detect_dependency_cycle(tasks):
        log.error("supervisor_v2_orchestrator_dependency_cycle")
        return "final_aggregator"

    completed = set((state.get("intermediate_results") or {}).keys())
    intermediate = state.get("intermediate_results") or {}

    if plan.execution_mode == "parallel":
        pending = [t for t in tasks if t.skill_name not in completed]
        if not pending:
            log.debug(
                "supervisor_v2_orchestrator_skills_cached",
                completed=list(completed),
            )
            return "final_aggregator"

        runnable = _parallel_runnable_tasks(pending, completed, intermediate)
        if not runnable:
            log.info("supervisor_v2_orchestrator_parallel_blocked")
            return "final_aggregator"

        log.info("supervisor_v2_orchestrator_parallel_fanout", skill_count=len(runnable))
        increment_counter(
            "supervisor_v2_orchestrator_fanout_total",
            tags={"mode": "parallel", "count": str(len(runnable))},
        )
        return [Send("skill_executor_v2", _build_send_payload(state, task)) for task in runnable]

    next_task = _next_runnable_task(
        [t for t in tasks if t.skill_name not in completed],
        completed,
        intermediate,
    )
    if not next_task:
        return "final_aggregator"

    log.info("supervisor_v2_orchestrator_sequential_fanout", skill_name=next_task.skill_name)
    increment_counter("supervisor_v2_orchestrator_fanout_total", tags={"mode": plan.execution_mode, "count": "1"})
    return Send("skill_executor_v2", _build_send_payload(state, next_task))


async def _execute_skill_decision(decision: SkillDecision, async_mode: bool) -> SkillResult:
    if async_mode:
        return skill_registry.submit_skill_task(decision)
    try:
        asyncio.get_running_loop()
        import nest_asyncio

        nest_asyncio.apply()
        return await skill_registry.async_execute_skill(decision)
    except RuntimeError:
        return await skill_registry.async_execute_skill(decision)


def skill_executor_v2_node(state: SupervisorStateV2) -> SupervisorStateV2:
    """单 Skill 执行实例，读写 intermediate_results 共享上下文。"""
    t_start = time.time()
    decision = state.get("skill_decision")
    task = state.get("current_skill_task")
    skill_name = (task.skill_name if task else None) or (decision.skill_name if decision else None)

    if not decision or not skill_name:
        log.info("supervisor_v2_skill_executor_skip")
        return state

    log.info("supervisor_v2_skill_executor_start", skill_name=skill_name)
    log.debug("supervisor_v2_skill_executor_params", skill_name=skill_name, parameters=decision.parameters)

    try:
        from src.skill_system.governance.rollout import is_skill_executable

        ok, gov_msg = is_skill_executable(skill_name, user_id=state.get("user_id"))
        if not ok:
            log.warning("supervisor_v2_skill_executor_governance_blocked", skill_name=skill_name, reason=gov_msg)
            return {
                "intermediate_results": {
                    skill_name: {"success": False, "message": gov_msg, "error": gov_msg},
                },
            }
    except Exception:
        pass

    try:
        from src.core.skills.rate_limit import check_skill_rate_limit

        allowed, rate_msg = check_skill_rate_limit(state.get("user_id"), skill_name)
        if not allowed:
            log.warning("supervisor_v2_skill_executor_rate_limited", skill_name=skill_name, user_id=state.get("user_id"))
            return {
                "intermediate_results": {
                    skill_name: {"success": False, "message": rate_msg, "error": rate_msg},
                },
            }
    except Exception:
        pass

    try:
        from src.auth.rbac import role_to_permission_level
        from src.skill_system.security import PermissionLevel, get_security_manager

        role = (state.get("user_role") or "operator").lower()
        perm_name = role_to_permission_level(role)
        user_level = PermissionLevel(perm_name) if perm_name in PermissionLevel._value2member_map_ else PermissionLevel.USER
        if not get_security_manager().check_permission(skill_name, user_level):
            msg = f"角色 {role} 无权执行 Skill: {skill_name}"
            log.warning("supervisor_v2_skill_executor_denied", skill_name=skill_name, role=role)
            return {
                "intermediate_results": {
                    skill_name: {"success": False, "message": msg, "error": msg},
                },
            }
    except Exception:
        pass

    try:
        from src.core.skills.result import ExecutionContext
        from src.core.skills.runner import record_chat_skill_result

        result = asyncio.run(_execute_skill_decision(decision, bool(state.get("async_mode"))))
        exec_context = ExecutionContext(
            source="chat",
            message_id=state.get("message_id"),
            thread_id=state.get("thread_id"),
            user_id=state.get("user_id"),
            ticket_id=state.get("ticket_id"),
        )
        payload = record_chat_skill_result(
            result,
            skill_name=skill_name,
            context=exec_context,
            input_params=decision.parameters,
        )
        tag = "success" if payload.get("success") else "error"
        increment_counter("supervisor_v2_skill_execution_total", tags={"result": tag, "skill": skill_name})
        observe_histogram("supervisor_v2_skill_execution_duration_ms", (time.time() - t_start) * 1000)
        log.info(
            "supervisor_v2_skill_executor_complete",
            skill_name=skill_name,
            success=payload.get("success"),
            execution_id=payload.get("execution_id"),
            duration_ms=int((time.time() - t_start) * 1000),
        )

        return {
            "intermediate_results": {skill_name: payload},
        }
    except Exception as exc:
        error_msg = f"技能执行异常: {exc}"
        log.error(
            "supervisor_v2_skill_executor_failed",
            skill_name=skill_name,
            error=error_msg,
            exc_info=exc,
        )
        increment_counter("supervisor_v2_skill_execution_total", tags={"result": "exception", "skill": skill_name or "unknown"})
        return {
            "intermediate_results": {skill_name: {"success": False, "message": error_msg, "error": error_msg}},
        }


def route_after_executor_v2(state: SupervisorStateV2) -> str:
    if _should_fallback_to_rag_after_skills(state):
        log.info("supervisor_v2_route_fallback_rag")
        return "knowledge_qa"

    plan = state.get("execution_plan")
    if not plan:
        return "final_aggregator"

    completed = set((state.get("intermediate_results") or {}).keys())
    intermediate = state.get("intermediate_results") or {}
    tasks = _filter_conditional_tasks(plan, state)
    pending = [t for t in tasks if t.skill_name not in completed]

    if plan.execution_mode == "parallel":
        if pending and _parallel_runnable_tasks(pending, completed, intermediate):
            return "orchestrator"
        return "final_aggregator"

    if pending and _next_runnable_task(pending, completed, intermediate):
        return "orchestrator"
    return "final_aggregator"


def workflow_starter_node(state: SupervisorStateV2) -> SupervisorStateV2:
    """启动 Workflow 插件（长时任务在 Celery 执行）。"""
    from src.core.plugins.chat_intent import (
        MissingRequiredContextError,
        MissingTicketIdError,
        build_chat_workflow_context,
        build_default_workflow_context,
        format_workflow_start_message,
        get_chat_intent_registry,
    )
    from src.core.workflows.engine import WorkflowEngine

    workflow_name = state.get("workflow_type") or "itsm-firewall-change"
    intent = get_chat_intent_registry().get_intent(workflow_name)
    try:
        if intent:
            context = build_chat_workflow_context(state, intent)
        else:
            context = build_default_workflow_context(state)
    except (MissingTicketIdError, MissingRequiredContextError) as exc:
        return {
            **state,
            "messages": state["messages"] + [AIMessage(content=str(exc))],
            "next_agent": "end",
            "agent_type": "workflow_starter",
        }

    run_id = WorkflowEngine.start(
        workflow_name,
        context,
        source=state.get("source") or "chat",
        user_id=state.get("user_id"),
        thread_id=state.get("thread_id"),
        parent_trace_id=state.get("langfuse_parent_trace_id"),
    )
    if intent:
        content = format_workflow_start_message(intent, run_id, context)
    else:
        content = f"[OK] 已启动 Workflow `{workflow_name}`\n\n- **流程 ID**: `{run_id}`"
    log.info(
        "supervisor_v2_workflow_started",
        workflow=workflow_name,
        run_id=run_id,
        ticket_id=context.get("ticket_id"),
    )
    return {
        **state,
        "workflow_run_id": run_id,
        "messages": state["messages"] + [AIMessage(content=content)],
        "context": f"Workflow 已启动: {run_id}",
        "next_agent": "end",
        "agent_type": "workflow_starter",
    }


def final_aggregator_node(state: SupervisorStateV2) -> SupervisorStateV2:
    """Map-Reduce 聚合：合并所有 Skill 执行结果为用户可见回复。"""
    from src.core.workflows.artifacts import collect_download_links

    results = state.get("intermediate_results") or {}
    plan = state.get("execution_plan")
    lines = ["[OK] 多 Skill 协同执行完成\n"]

    if plan and plan.reasoning:
        lines.append(f"**调度说明**: {plan.reasoning}\n")

    for skill_name, result in results.items():
        if not isinstance(result, dict):
            continue
        status = "成功" if result.get("success") else "失败"
        lines.append(f"### {skill_name}")
        lines.append(f"- 状态: {status}")
        lines.append(f"- 结果: {result.get('message', '')}")
        links = collect_download_links(result=result)
        if links:
            lines.append("- 下载:")
            for link in links:
                lines.append(f"  - [{link['label']}]({link['url']})")
        elif result.get("download_url"):
            url = result["download_url"]
            lines.append(f"- 下载: [{url}]({url})")
        if result.get("error"):
            lines.append(f"- 错误: {result['error']}")
        lines.append("")

    if not results:
        lines.append("未产生 Skill 执行结果。")

    content = "\n".join(lines)
    log.info("supervisor_v2_final_aggregator", result_count=len(results))

    return {
        **state,
        "messages": state["messages"] + [AIMessage(content=content)],
        "context": f"协同执行完成，共 {len(results)} 个 Skill",
        "next_agent": "end",
        "agent_type": "skill_executor",
    }


def _format_rag_failure_message(exc: Exception) -> str:
    msg = str(exc)
    if "402" in msg or "Insufficient Balance" in msg:
        return (
            "**无法完成回答**：大模型 API 账户余额不足（HTTP 402）。\n\n"
            "本次请求已尝试走知识库（RAG）兜底，但生成回答同样需要调用 LLM。\n\n"
            "**建议**：\n"
            "1. 为 DeepSeek（或当前配置的 LLM）充值后重试；\n"
            "2. 运维类指令（如「生成防火墙策略」）在余额恢复后应自动匹配 Skill 执行，无需 RAG；\n"
            "3. 若未匹配到预期 Skill，请检查该技能是否启用及当前用户是否有执行权限。\n"
        )
    return f"[FAIL] RAG 查询失败: {msg}"


def knowledge_qa_node_wrapper_v2(state: SupervisorStateV2) -> SupervisorStateV2:
    log.info("supervisor_v2_knowledge_qa_begin")
    try:
        result = knowledge_qa_node(state)
        return {**state, **result, "next_agent": "end", "agent_type": "knowledge_qa"}
    except Exception as exc:
        log.exception("supervisor_v2_knowledge_qa_failed", error=str(exc))
        return {
            **state,
            "messages": state["messages"] + [AIMessage(content=_format_rag_failure_message(exc))],
            "next_agent": "end",
        }


def route_after_supervisor_v2(state: SupervisorStateV2) -> str:
    next_agent = state.get("next_agent")
    if next_agent == "workflow_starter":
        return "workflow_starter"
    plan = state.get("execution_plan")
    if not plan or plan.fallback_to_rag or not plan.skills:
        return "knowledge_qa"
    return "orchestrator"


def build_supervisor_graph_v2(checkpointer=None):
    """构建 Supervisor v2 高级协同 StateGraph。"""
    workflow = StateGraph(SupervisorStateV2)

    workflow.add_node("pre_process", pre_process_node)
    workflow.add_node("supervisor", supervisor_node_v2)
    workflow.add_node("orchestrator", lambda state: state)  # 占位，实际路由在 conditional_edges
    workflow.add_node("skill_executor_v2", skill_executor_v2_node)
    workflow.add_node("workflow_starter", workflow_starter_node)
    workflow.add_node("final_aggregator", final_aggregator_node)
    workflow.add_node("knowledge_qa", knowledge_qa_node_wrapper_v2)

    workflow.set_entry_point("pre_process")
    workflow.add_edge("pre_process", "supervisor")
    workflow.add_conditional_edges(
        "supervisor",
        route_after_supervisor_v2,
        {"orchestrator": "orchestrator", "knowledge_qa": "knowledge_qa", "workflow_starter": "workflow_starter"},
    )
    workflow.add_conditional_edges("orchestrator", orchestrator_dispatch)
    workflow.add_conditional_edges(
        "skill_executor_v2",
        route_after_executor_v2,
        {
            "orchestrator": "orchestrator",
            "final_aggregator": "final_aggregator",
            "knowledge_qa": "knowledge_qa",
        },
    )
    workflow.add_edge("workflow_starter", END)
    workflow.add_edge("final_aggregator", END)
    workflow.add_edge("knowledge_qa", END)

    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()
        log.info("supervisor_v2_memory_checkpointer")

    compiled = workflow.compile(checkpointer=checkpointer)
    log.info("supervisor_v2_graph_compiled")
    return compiled


def get_supervisor_graph_v2():
    # SSE 聊天走 agent_graph.astream()，需要 checkpointer.aget_tuple。
    # 同步 PostgresSaver 不支持 async API，会触发 NotImplementedError。
    return build_supervisor_graph_v2()


_compiled_graph_v2 = None


def compiled_graph_v2():
    global _compiled_graph_v2
    if _compiled_graph_v2 is None:
        _compiled_graph_v2 = get_supervisor_graph_v2()
    return _compiled_graph_v2
