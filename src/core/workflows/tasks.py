# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow Celery 任务调度。"""

from __future__ import annotations

import logging
import re

from billiard.exceptions import SoftTimeLimitExceeded
from celery import chord

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

logger = logging.getLogger(__name__)


_PARSER_FAILURE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _parser_skill_failure_code(exc: Exception) -> str:
    """Preserve a stable generator reason instead of flattening every failure."""

    payload = getattr(exc, "payload", None)
    if not isinstance(payload, dict):
        return "skill_execution_failed"

    reasons: list[str] = []

    def add_reason(value) -> None:
        reason = str(value or "").strip()
        if reason:
            reasons.append(reason)

    add_reason(payload.get("failure_code"))
    add_reason(payload.get("error_code"))
    add_reason(payload.get("skipped_reason"))
    if isinstance(payload.get("agent_loop"), dict):
        add_reason(payload["agent_loop"].get("stop_reason"))
    for report in payload.get("reports") or []:
        if not isinstance(report, dict):
            continue
        add_reason(report.get("skipped_reason"))
        if isinstance(report.get("agent_loop"), dict):
            add_reason(report["agent_loop"].get("stop_reason"))

    error_text = str(payload.get("error") or "")
    terminal_match = re.search(
        r"terminal status:\s*([a-z][a-z0-9_]{2,63})",
        error_text,
        flags=re.IGNORECASE,
    )
    if terminal_match:
        reasons.append(terminal_match.group(1))

    for reason in reasons:
        segments = [segment.strip().lower() for segment in reason.split(":")]
        candidate = segments[0]
        if candidate == "model_requested_review" and len(segments) > 1:
            candidate = segments[1]
        if _PARSER_FAILURE_CODE_RE.fullmatch(candidate):
            return candidate
    return "skill_execution_failed"


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


def _refresh_rule_asset_projection(parser_job_id: str) -> None:
    """Keep the rule control-plane read model aligned with Parser job state."""

    try:
        from src.core.rule_control.command_asset_units import (
            RuleAssetCommandUnitService,
        )

        RuleAssetCommandUnitService().reconcile_for_parser_job(parser_job_id)
    except Exception:
        logger.exception(
            "Rule asset projection refresh failed parser_job_id=%s",
            parser_job_id,
        )


def _refresh_rule_asset_projection_from_skill_result(
    skill_name: str,
    result: dict | None,
) -> None:
    """Refresh command-unit state after a rule bundle becomes durable."""

    if skill_name != "rule-asset-persister" or not isinstance(result, dict):
        return
    bundle = result.get("bundle")
    if not isinstance(bundle, dict):
        return
    payload = bundle.get("payload_json")
    if not isinstance(payload, dict):
        payload = {}
    offline_job_id = str(payload.get("offline_job_id") or "").strip()
    if not offline_job_id:
        return
    try:
        from src.core.rule_control.command_asset_units import (
            RuleAssetCommandUnitService,
        )

        RuleAssetCommandUnitService().reconcile(offline_job_id)
    except Exception:
        logger.exception(
            "Rule asset projection refresh failed offline_job_id=%s bundle_id=%s",
            offline_job_id,
            bundle.get("id"),
        )


@celery.task(bind=True, name="execute_skill_task")
def execute_skill_task(self, skill_name: str, **params):
    """通用 Skill 执行 Celery 任务。"""
    from src.core.parser.repository import ParserJobRepository, ParserJobStateError
    from src.core.skills.executor import SkillExecutionError, execute_skill

    manages_parser_job_lifecycle = (
        skill_name == "textfsm-generator"
        and str(params.get("operation") or "generate") != "suggest_spec"
    )
    job_id = str(params.get("job_id") or "") if manages_parser_job_lifecycle else ""
    repository = ParserJobRepository() if job_id else None

    def fail_active_parser_job(error: str, failure_code: str) -> None:
        if repository is None:
            return
        current = repository.get_job_lifecycle(job_id)
        if not current or current["status"] not in {
            "collecting",
            "pending",
            "running",
            "validating",
        }:
            return
        repository.transition_job_status(
            job_id,
            "failed",
            error,
            failure_code=failure_code,
        )
        _refresh_rule_asset_projection(job_id)

    if repository:
        try:
            repository.transition_job_status(
                job_id,
                "running",
                expected_status="pending",
                expected_celery_task_id=str(self.request.id or ""),
            )
            _refresh_rule_asset_projection(job_id)
        except ParserJobStateError as exc:
            return {"success": False, "message": str(exc), "error": str(exc)}

    try:
        result = execute_skill(skill_name, params)
    except SkillExecutionError as exc:
        failure_code = _parser_skill_failure_code(exc)
        fail_active_parser_job(str(exc), failure_code)
        return {
            "success": False,
            "message": str(exc),
            "error": str(exc),
            "failure_code": failure_code,
        }
    except SoftTimeLimitExceeded:
        fail_active_parser_job(
            "textfsm-generator exceeded the Celery soft time limit",
            "worker_timeout",
        )
        raise
    except Exception as exc:
        fail_active_parser_job(str(exc), "worker_exception")
        logger.exception("Skill execution failed skill=%s job=%s", skill_name, job_id or None)
        raise

    if repository:
        current = repository.get_job_lifecycle(job_id)
        if current and current["status"] == "cancelled":
            return {"success": False, "message": "parser job cancelled", "error": "cancelled"}
        if current and current["status"] == "succeeded" and bool((result or {}).get("success")):
            return result
        if current and current["status"] not in {"running", "validating"}:
            return {
                "success": False,
                "message": "parser job inputs changed while generation was running",
                "error": "stale_generation_discarded",
            }
        success = bool((result or {}).get("success"))
        repository.transition_job_status(
            job_id,
            "succeeded" if success else "failed",
            None if success else str((result or {}).get("error") or (result or {}).get("message")),
            failure_code=None if success else "generator_failed",
        )
        _refresh_rule_asset_projection(job_id)
    _refresh_rule_asset_projection_from_skill_result(skill_name, result)
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
    if not template or step_index >= len(steps):
        return {"success": False, "error": "invalid step", "step_index": step_index}

    step_rec = steps[step_index]
    step_tpl = find_step_template(template, step_rec.step_name)
    if not step_tpl:
        return {
            "success": False,
            "error": f"unknown step: {step_rec.step_name}",
            "step_index": step_index,
        }

    if step_tpl.subworkflow:
        return _run_subworkflow_step(run_id, step_index, step_tpl)

    params = WorkflowEngine.build_step_params(run_id, step_index)
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
    if not template or step_index >= len(steps):
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
    if step_index >= len(steps):
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
    """调度 Workflow 单步 Skill（通过 execute_skill_task）。"""
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
    if step_index >= len(steps):
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
