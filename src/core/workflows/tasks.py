# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow Celery 任务调度。"""

from __future__ import annotations

import logging

from billiard.exceptions import SoftTimeLimitExceeded
from celery import chord

from src.core.celery_tasks.celery_app import celery
from src.core.workflows.engine import WorkflowEngine
from src.core.workflows.registry import find_step_template, get_template
from src.core.workflows.repository import get_workflow_run, list_workflow_steps, mark_step_failed, mark_step_running

logger = logging.getLogger(__name__)


@celery.task(bind=True, name="execute_skill_task")
def execute_skill_task(self, skill_name: str, **params):
    """通用 Skill 执行 Celery 任务。"""
    from src.core.parser.repository import ParserJobStateError, ParserRepository
    from src.core.skills.executor import SkillExecutionError, execute_skill

    job_id = str(params.get("job_id") or "") if skill_name == "textfsm-generator" else ""
    repository = ParserRepository() if job_id else None
    if repository:
        try:
            repository.transition_job_status(job_id, "running")
        except ParserJobStateError as exc:
            return {"success": False, "message": str(exc), "error": str(exc)}

    try:
        result = execute_skill(skill_name, params)
    except SkillExecutionError as exc:
        if repository:
            repository.transition_job_status(
                job_id, "failed", str(exc), failure_code="skill_execution_failed"
            )
        return {"success": False, "message": str(exc), "error": str(exc)}
    except SoftTimeLimitExceeded:
        if repository:
            repository.transition_job_status(
                job_id,
                "failed",
                "textfsm-generator exceeded the Celery soft time limit",
                failure_code="worker_timeout",
            )
        raise
    except Exception as exc:
        if repository:
            repository.transition_job_status(
                job_id, "failed", str(exc), failure_code="worker_exception"
            )
        logger.exception("Skill execution failed skill=%s job=%s", skill_name, job_id or None)
        raise

    if repository:
        current = repository.get_job(job_id)
        if current and current["status"] == "cancelled":
            return {"success": False, "message": "parser job cancelled", "error": "cancelled"}
        success = bool((result or {}).get("success"))
        repository.transition_job_status(
            job_id,
            "succeeded" if success else "failed",
            None
            if success
            else str((result or {}).get("error") or (result or {}).get("message")),
            failure_code=None if success else "generator_failed",
        )
    return result


def _run_subworkflow_step(run_id: str, step_index: int, step_tpl) -> dict:
    """启动子 Workflow 并阻塞等待完成。"""
    import time

    run = get_workflow_run(run_id)
    if not run:
        return {"success": False, "error": "workflow run not found", "step_index": step_index}

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
        return {"success": False, "error": str(exc), "step_index": step_index}

    deadline = time.time() + 180
    child = get_workflow_run(child_run_id)
    while child and child.status not in ("completed", "failed") and time.time() < deadline:
        time.sleep(0.5)
        child = get_workflow_run(child_run_id)

    if not child:
        return {"success": False, "error": "子 Workflow 不存在", "step_index": step_index, "child_run_id": child_run_id}
    if child.status == "failed":
        return {
            "success": False,
            "error": child.error_message or "子 Workflow 失败",
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

    template = get_template(run.template_name)
    steps = list_workflow_steps(run_id)
    if not template or step_index >= len(steps):
        return {"success": False, "error": "invalid step", "step_index": step_index}

    step_rec = steps[step_index]
    step_tpl = find_step_template(template, step_rec.step_name)
    if not step_tpl:
        return {"success": False, "error": f"unknown step: {step_rec.step_name}", "step_index": step_index}

    if step_tpl.subworkflow:
        return _run_subworkflow_step(run_id, step_index, step_tpl)

    params = WorkflowEngine.build_step_params(run_id, step_index)
    try:
        result = execute_skill_task.run(skill_name=step_tpl.skill_name, **params)
        return {**(result or {}), "step_index": step_index}
    except Exception as exc:
        logger.exception("Workflow 步骤执行异常 run=%s step=%s skill=%s", run_id, step_index, step_tpl.skill_name)
        return {"success": False, "error": str(exc), "step_index": step_index}


@celery.task(bind=True, name="execute_workflow_step_only")
def execute_workflow_step_only(self, run_id: str, step_index: int):
    """执行单步并写库，不推进下一步（供并行批使用）。"""
    from src.core.workflows.repository import mark_step_completed

    steps = list_workflow_steps(run_id)
    if step_index >= len(steps):
        return {"success": False, "error": "invalid step", "step_index": step_index}

    step_rec = steps[step_index]
    mark_step_running(step_rec.id, self.request.id)

    result = _run_workflow_step(run_id, step_index)
    from src.core.workflows.artifacts import normalize_step_result

    normalized = normalize_step_result(result)
    mark_step_completed(step_rec.id, normalized)

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
    header = [execute_workflow_step_only.s(run_id, idx) for idx in indices]
    next_index = max(indices) + 1
    chord(header)(parallel_batch_complete.s(run_id, next_index))
    return {"success": True, "batch": indices}


@celery.task(bind=True, name="dispatch_workflow_step_task")
def dispatch_workflow_step_task(self, run_id: str, step_index: int):
    """调度 Workflow 单步 Skill（通过 execute_skill_task）。"""
    run = get_workflow_run(run_id)
    if not run:
        return {"success": False, "error": "workflow run not found"}

    steps = list_workflow_steps(run_id)
    if step_index >= len(steps):
        return {"success": False, "error": "invalid step"}

    step_rec = steps[step_index]
    mark_step_running(step_rec.id, self.request.id)

    result = _run_workflow_step(run_id, step_index)
    try:
        WorkflowEngine.handle_step_complete(run_id, step_index, result)
        return result
    except Exception as exc:
        logger.exception("Workflow 步骤完成处理异常 run=%s step=%s", run_id, step_index)
        mark_step_failed(step_rec.id, str(exc))
        WorkflowEngine.handle_step_complete(run_id, step_index, {"success": False, "error": str(exc)})
        raise
