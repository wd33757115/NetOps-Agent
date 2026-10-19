# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow Celery 任务调度。"""

from __future__ import annotations

import logging

from celery import chord

from src.apps.rule_governance.backend.parser.worker import (
    _PARSER_FAILURE_CODE_RE,
    _parser_skill_failure_code,
)
from src.apps.rule_governance.backend.parser.worker import (
    _refresh_rule_asset_projection as _refresh_rule_asset_projection,
)
from src.core.celery_tasks.celery_app import celery
from src.core.workflows.engine import WorkflowEngine
from src.core.workflows.registry import find_step_template, get_template
from src.core.workflows.repository import (
    TERMINAL_WORKFLOW_STATUSES,
    get_workflow_run,
    list_workflow_steps,
    mark_step_failed,
    mark_step_running,
)
from src.platform.tools.actions import is_action as is_app_action

logger = logging.getLogger(__name__)


def _workflow_exception_failure_code(exc: Exception) -> str:
    """Extract a stable code through Celery Retry and Skill wrappers."""

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending and len(seen) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        direct = str(getattr(current, "failure_code", "") or "").strip()
        if _PARSER_FAILURE_CODE_RE.fullmatch(direct):
            return direct
        payload = getattr(current, "payload", None)
        if isinstance(payload, dict):
            for key in ("failure_code", "error_code"):
                candidate = str(payload.get(key) or "").strip()
                if _PARSER_FAILURE_CODE_RE.fullmatch(candidate):
                    return candidate
        for nested in (
            getattr(current, "exc", None),
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return "workflow_step_exception"


@celery.task(bind=True, name="execute_skill_task")
def execute_skill_task(self, skill_name: str, **params):
    """Legacy Parser deliveries share the package worker; other Skills stay generic."""
    if skill_name == "textfsm-generator":
        from src.apps.rule_governance.backend.parser.worker import execute_parser_delivery

        return execute_parser_delivery(task_id=str(self.request.id or ""), params=params)

    from src.core.skills.executor import SkillExecutionError, execute_skill

    try:
        result = execute_skill(skill_name, params)
    except SkillExecutionError as exc:
        return {"success": False, "message": str(exc), "error": str(exc),
                "failure_code": _parser_skill_failure_code(exc)}
    return result


def _run_subworkflow_step(run_id: str, step_index: int, step_tpl) -> dict:
    """启动子 Workflow 并阻塞等待完成。"""
    import time

    run = get_workflow_run(run_id)
    if not run:
        return {"success": False, "error": "workflow run not found", "step_index": step_index}
    if run.status in TERMINAL_WORKFLOW_STATUSES:
        return {
            "success": False,
            "ignored": True,
            "error": f"workflow already {run.status}",
            "step_index": step_index,
        }

    child_template = step_tpl.subworkflow
    if not child_template:
        return {"success": False, "error": "subworkflow 未配置", "step_index": step_index}

    params = WorkflowEngine.build_step_params(run_id, step_index)
    child_context = {**(run.context or {}), **params}
    child_context.pop("langfuse_trace_id", None)

    try:
        child_run_id = WorkflowEngine.start(
            child_template,
            child_context,
            source="subworkflow",
            user_id=run.user_id,
            thread_id=run.thread_id,
            parent_run_id=run_id,
        )
    except ValueError as exc:
        return {
            "success": False,
            "error": str(exc),
            "failure_code": _workflow_exception_failure_code(exc),
            "step_index": step_index,
        }

    deadline = time.time() + 180
    child = get_workflow_run(child_run_id)
    while (
        child
        and child.status not in TERMINAL_WORKFLOW_STATUSES
        and time.time() < deadline
    ):
        time.sleep(0.5)
        child = get_workflow_run(child_run_id)

    if not child:
        return {
            "success": False,
            "error": "子 Workflow 不存在",
            "step_index": step_index,
            "child_run_id": child_run_id,
        }
    if child.status == "failed":
        return {
            "success": False,
            "error": child.error_message or "子 Workflow 失败",
            "step_index": step_index,
            "child_run_id": child_run_id,
        }
    if child.status == "cancelled":
        return {
            "success": False,
            "ignored": True,
            "error": child.error_message or "子 Workflow 已取消",
            "step_index": step_index,
            "child_run_id": child_run_id,
        }
    if child.status != "completed":
        return {
            "success": False,
            "error": "子 Workflow 超时",
            "step_index": step_index,
            "child_run_id": child_run_id,
        }

    child_steps = list_workflow_steps(child_run_id)
    artifacts: dict = {}
    for cs in child_steps:
        if cs.output_artifacts:
            artifacts.update(cs.output_artifacts)

    return {
        "success": True,
        "message": f"子 Workflow `{child_template}` 已完成",
        "child_run_id": child_run_id,
        "artifacts": artifacts,
        "step_index": step_index,
    }


def _run_workflow_step(run_id: str, step_index: int) -> dict:
    from src.core.workflows.engine import WorkflowEngine

    WorkflowEngine.ensure_wf_trace(run_id)

    run = get_workflow_run(run_id)
    if not run:
        return {"success": False, "error": "workflow run not found", "step_index": step_index}
    if run.status in TERMINAL_WORKFLOW_STATUSES:
        return {
            "success": False,
            "ignored": True,
            "error": f"workflow already {run.status}",
            "step_index": step_index,
        }

    template = get_template(run.template_name)
    steps = list_workflow_steps(run_id)
    if not template or not isinstance(step_index, int) or not 0 <= step_index < len(steps):
        return {"success": False, "error": "invalid step", "step_index": step_index}

    step_rec = steps[step_index]
    step_tpl = find_step_template(template, step_rec.step_name)
    if not step_tpl:
        return {
            "success": False,
            "error": f"unknown step: {step_rec.step_name}",
            "step_index": step_index,
        }

    if run.template_name in {"patrol-change-event", "patrol-attested-offline-current", "patrol-offline-current", "patrol-history-change-event"}:
        return {"success": False, "error": "巡检 Workflow 已停止，请通过巡检应用重新提交",
                "failure_code": "offline_patrol_workflow_retired", "step_index": step_index}
    if run.template_name in {"rule-asset-generation", "rule-asset-submit", "rule-asset-approve", "parser-validation-ingest", "rollout-live-capture"}:
        return {"success": False, "error": "旧规则 Workflow 已停止，请从规则治理应用重新提交",
                "failure_code": "rule_workflow_retired", "step_index": step_index}
    if step_tpl.subworkflow:
        return _run_subworkflow_step(run_id, step_index, step_tpl)

    params = WorkflowEngine.build_step_params(run_id, step_index)
    if is_app_action(step_tpl.skill_name):
        from src.core.workflows.app_steps import run_app_step

        return run_app_step(run, step_index, step_tpl.name, step_tpl.skill_name, params)
    try:
        result = execute_skill_task.run(skill_name=step_tpl.skill_name, **params)
        return {**(result or {}), "step_index": step_index}
    except Exception as exc:
        logger.exception(
            "Workflow 步骤执行异常 run=%s step=%s skill=%s", run_id, step_index, step_tpl.skill_name
        )
        return {"success": False, "error": str(exc), "step_index": step_index}


def _workflow_step_queue(run_id: str, step_index: int) -> str:
    from src.core.skills.celery_routing import resolve_skill_celery_queue

    run = get_workflow_run(run_id)
    template = get_template(run.template_name) if run else None
    steps = list_workflow_steps(run_id)
    if not template or not isinstance(step_index, int) or not 0 <= step_index < len(steps):
        return "netops.default"
    step_template = find_step_template(template, steps[step_index].step_name)
    return (
        resolve_skill_celery_queue(step_template.skill_name) if step_template else "netops.default"
    )


@celery.task(bind=True, name="execute_workflow_step_only")
def execute_workflow_step_only(self, run_id: str, step_index: int):
    """执行单步并写库，不推进下一步（供并行批使用）。"""
    from src.core.workflows.repository import mark_step_completed

    steps = list_workflow_steps(run_id)
    if not isinstance(step_index, int) or not 0 <= step_index < len(steps):
        return {"success": False, "error": "invalid step", "step_index": step_index}

    step_rec = steps[step_index]
    if not mark_step_running(step_rec.id, self.request.id):
        return {
            "success": False,
            "ignored": True,
            "error": "workflow reached a terminal state before task execution",
            "step_index": step_index,
        }

    result = _run_workflow_step(run_id, step_index)
    from src.core.workflows.artifacts import normalize_step_result

    normalized = normalize_step_result(result)
    if not mark_step_completed(step_rec.id, normalized):
        return {
            "success": False,
            "ignored": True,
            "error": "workflow reached a terminal state before result commit",
            "step_index": step_index,
        }

    if normalized.get("success"):
        publish = WorkflowEngine._notify_step_progress  # noqa: SLF001 — 复用通知
        run = get_workflow_run(run_id)
        template = get_template(run.template_name) if run else None
        if template and template.on_complete.notify_each_step:
            publish(run_id, step_rec, normalized)

    return {
        "success": bool(normalized.get("success")),
        "message": normalized.get("message"),
        "error": normalized.get("error"),
        "step_index": step_index,
    }


@celery.task(bind=True, name="parallel_batch_complete")
def parallel_batch_complete(self, results, run_id: str, next_index: int):
    WorkflowEngine.handle_parallel_batch_complete(run_id, results or [], next_index)
    return {"success": True, "run_id": run_id, "next_index": next_index}


@celery.task(bind=True, name="dispatch_parallel_batch_task")
def dispatch_parallel_batch_task(self, run_id: str, indices: list[int]):
    """并行调度同组步骤。"""
    if not indices:
        return {"success": False, "error": "empty batch"}
    run = get_workflow_run(run_id)
    if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
        return {
            "success": False,
            "ignored": True,
            "error": f"workflow already {run.status if run else 'missing'}",
        }
    queues = [_workflow_step_queue(run_id, idx) for idx in indices]
    header = [
        execute_workflow_step_only.s(run_id, idx).set(queue=queue)
        for idx, queue in zip(indices, queues, strict=True)
    ]
    next_index = max(indices) + 1
    callback_queue = queues[0] if len(set(queues)) == 1 else "netops.default"
    chord(header)(parallel_batch_complete.s(run_id, next_index).set(queue=callback_queue))
    return {"success": True, "batch": indices}


@celery.task(bind=True, name="dispatch_workflow_step_task")
def dispatch_workflow_step_task(self, run_id: str, step_index: int):
    """Dispatch a durable step to its package adapter or the generic Skill path."""
    run = get_workflow_run(run_id)
    if not run:
        return {"success": False, "error": "workflow run not found"}
    if run.status in TERMINAL_WORKFLOW_STATUSES:
        logger.warning(
            "Ignoring late workflow task run=%s step=%s status=%s",
            run_id,
            step_index,
            run.status,
        )
        return {
            "success": False,
            "ignored": True,
            "error": f"workflow already {run.status}",
        }

    steps = list_workflow_steps(run_id)
    if not isinstance(step_index, int) or not 0 <= step_index < len(steps):
        return {"success": False, "error": "invalid step"}

    step_rec = steps[step_index]
    if not mark_step_running(step_rec.id, self.request.id):
        return {
            "success": False,
            "ignored": True,
            "error": "workflow reached a terminal state before task execution",
        }

    result = _run_workflow_step(run_id, step_index)
    try:
        WorkflowEngine.handle_step_complete(run_id, step_index, result)
        return result
    except Exception as exc:
        logger.exception("Workflow 步骤完成处理异常 run=%s step=%s", run_id, step_index)
        mark_step_failed(step_rec.id, str(exc))
        WorkflowEngine.handle_step_complete(
            run_id, step_index, {"success": False, "error": str(exc)}
        )
        raise
