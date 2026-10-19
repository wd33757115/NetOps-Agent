# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 编排引擎。"""

from __future__ import annotations

import uuid
from typing import Any

from src.common.config import get_settings
from src.core.logging import get_logger
from src.core.workflows.artifacts import normalize_step_result
from src.core.workflows.events import publish_workflow_event
from src.core.workflows.expression import build_step_env, resolve_inputs, resolve_value
from src.core.workflows.registry import (
    get_template,
    load_workflows,
    resolve_active_steps,
)
from src.core.workflows.repository import (
    TERMINAL_WORKFLOW_STATUSES,
    cancel_workflow_run,
    create_notification,
    create_workflow_run,
    create_workflow_steps,
    get_workflow_run,
    list_child_runs,
    list_workflow_steps,
    mark_step_completed,
    mark_step_queued,
    update_run_context,
    update_run_status,
    update_run_step_index,
)
from src.observability.langfuse import (
    end_workflow_trace,
    record_workflow_step,
    resume_workflow_trace,
    start_workflow_trace,
)

log = get_logger(__name__)

# 进程内 Langfuse trace 缓存（run_id → trace）
_WF_TRACES: dict[str, Any] = {}


def _ensure_wf_trace(run_id: str):
    """Gateway 或 Celery Worker 内获取/恢复 Langfuse Workflow trace。"""
    existing = _WF_TRACES.get(run_id)
    if existing:
        return existing
    run = get_workflow_run(run_id)
    if not run:
        return None
    trace_id = (run.context or {}).get("langfuse_trace_id")
    if not trace_id:
        return None
    root_span_id = (run.context or {}).get("langfuse_workflow_root_span_id")
    wf_trace = resume_workflow_trace(
        str(trace_id),
        run_id=run_id,
        template_name=run.template_name,
        workflow_root_span_id=str(root_span_id) if root_span_id else None,
    )
    if wf_trace:
        _set_wf_trace(run_id, wf_trace)
    return wf_trace


def _set_wf_trace(run_id: str, trace) -> None:
    if trace:
        _WF_TRACES[run_id] = trace


def _pop_wf_trace(run_id: str):
    return _WF_TRACES.pop(run_id, None)


class WorkflowEngine:
    @classmethod
    def _publish_workflow_notify(
        cls, event_type: str, run_id: str, payload: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """发布 Workflow 通知领域事件。

        返回 (是否无需再直接写通知, 直接写通知时使用的幂等键)。双写时两条路径用同一个键，
        Consumer 写入的通知和直接写入的通知只保留一条。
        """
        settings = get_settings()
        if not settings.EVENT_BUS_ENABLED:
            return False, None
        run = get_workflow_run(run_id)
        enriched = dict(payload)
        if run:
            enriched.setdefault("user_id", run.user_id)
            enriched.setdefault("thread_id", run.thread_id)
            enriched.setdefault("ticket_id", run.ticket_id)
        trace_id = (run.context or {}).get("langfuse_trace_id") if run else None
        from src.core.events.publishers import publish_workflow_event, workflow_event_key

        msg_id = publish_workflow_event(
            event_type,
            run_id=run_id,
            trace_id=str(trace_id) if trace_id else None,
            payload=enriched,
        )
        key = workflow_event_key(event_type, run_id=run_id, payload=enriched)
        # 默认双写：EventBus 发布成功仍走 sync create_notification，避免 Consumer 未启动时无通知
        if msg_id and not settings.EVENT_BUS_DIRECT_NOTIFY_FALLBACK:
            return True, key
        return False, key

    @classmethod
    def ensure_wf_trace(cls, run_id: str):
        """Celery Worker 内恢复 Langfuse Workflow trace。"""
        return _ensure_wf_trace(run_id)

    @classmethod
    def start(
        cls,
        template_name: str,
        context: dict[str, Any],
        *,
        source: str = "chat",
        user_id: str | None = None,
        thread_id: str | None = None,
        parent_run_id: str | None = None,
        parent_trace_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        if template_name in {"patrol-change-event", "patrol-attested-offline-current", "patrol-offline-current", "patrol-history-change-event"}:
            raise ValueError("巡检已迁移为固定任务，请通过巡检应用提交")
        if template_name in {"rule-asset-generation", "rule-asset-submit", "rule-asset-approve", "parser-validation-ingest", "rollout-live-capture"}:
            raise ValueError("规则业务已迁入规则治理应用，请使用应用 API")
        load_workflows()
        template = get_template(template_name)
        if not template:
            raise ValueError(f"未知 Workflow 模板: {template_name}")

        requested_run_id = str(run_id) if run_id else None
        existing_run = get_workflow_run(requested_run_id) if requested_run_id else None
        if existing_run:
            if existing_run.template_name != template_name:
                raise ValueError(
                    f"Workflow run {requested_run_id} already belongs to "
                    f"{existing_run.template_name}"
                )
            existing_steps = list_workflow_steps(requested_run_id)
            if existing_run.status in TERMINAL_WORKFLOW_STATUSES:
                return requested_run_id
            if existing_steps and str(existing_steps[0].status or "pending") != "pending":
                return requested_run_id

        ticket_id = context.get("ticket_id")
        ctx = dict(existing_run.context or context or {}) if existing_run else dict(context or {})
        if parent_run_id:
            ctx["parent_run_id"] = parent_run_id
        chat_parent_trace = parent_trace_id or ctx.get("langfuse_parent_trace_id")

        run_id = create_workflow_run(
            template_name=template_name,
            context=ctx,
            ticket_id=ticket_id,
            source=source,
            user_id=user_id,
            thread_id=thread_id,
            run_id=requested_run_id,
        )

        wf_trace = None if existing_run else start_workflow_trace(
            run_id=run_id,
            template_name=template_name,
            ticket_id=ticket_id,
            source=source,
            user_id=user_id,
            parent_run_id=parent_run_id,
            parent_trace_id=str(chat_parent_trace) if chat_parent_trace else None,
        )
        if wf_trace and wf_trace.trace_id:
            ctx["langfuse_trace_id"] = wf_trace.trace_id
            if wf_trace.workflow_root_span_id:
                ctx["langfuse_workflow_root_span_id"] = wf_trace.workflow_root_span_id
            if chat_parent_trace:
                ctx["parent_chat_trace_id"] = str(chat_parent_trace)
            update_run_context(run_id, ctx)
            _set_wf_trace(run_id, wf_trace)

        active_tpl_steps = resolve_active_steps(template, ctx, run_id=run_id)
        if not active_tpl_steps:
            raise ValueError(f"Workflow {template_name} 无可用步骤")
        steps = []
        for s in active_tpl_steps:
            if s.subworkflow:
                steps.append(
                    {
                        "name": s.name,
                        "skill_name": f"subworkflow:{s.subworkflow}",
                        "celery_task": "execute_skill_task",
                    }
                )
            else:
                steps.append(
                    {
                        "name": s.name,
                        "skill_name": s.skill_name,
                        "celery_task": "execute_skill_task",
                    }
                )
        create_workflow_steps(run_id, steps)
        update_run_status(run_id, "running")
        publish_workflow_event(
            run_id,
            status="started",
            message=f"Workflow {template_name} 已启动",
            extra={
                "template_name": template_name,
                "langfuse_trace_id": ctx.get("langfuse_trace_id"),
                "parent_run_id": parent_run_id,
            },
        )
        cls._dispatch_from_index(run_id, 0)
        log.info(
            "workflow_run_started",
            run_id=run_id,
            template_name=template_name,
            ticket_id=ticket_id,
            source=source,
            step_count=len(steps),
            trace_id=ctx.get("langfuse_trace_id"),
        )
        return run_id

    @classmethod
    def _parallel_batch_indices(cls, template, db_steps, start_index: int) -> list[int]:
        """连续且 parallel_group 相同的步骤索引（>1 才视为并行批）。"""
        from src.core.workflows.registry import find_step_template

        if start_index >= len(db_steps):
            return []
        tpl_step = find_step_template(template, db_steps[start_index].step_name)
        if not tpl_step or not tpl_step.parallel_group:
            return [start_index]
        group = tpl_step.parallel_group
        indices = [start_index]
        for j in range(start_index + 1, len(db_steps)):
            ts = find_step_template(template, db_steps[j].step_name)
            if ts and ts.parallel_group == group:
                indices.append(j)
            else:
                break
        return indices if len(indices) > 1 else [start_index]

    @classmethod
    def _dispatch_from_index(cls, run_id: str, step_index: int) -> None:
        run = get_workflow_run(run_id)
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            return
        steps = list_workflow_steps(run_id)
        if step_index >= len(steps):
            cls._complete_workflow(run_id)
            return
        template = get_template(run.template_name)
        batch = (
            cls._parallel_batch_indices(template, steps, step_index) if template else [step_index]
        )
        if len(batch) > 1:
            cls._dispatch_parallel_batch(run_id, batch)
            return
        cls.dispatch_step(run_id, step_index)

    @classmethod
    def _dispatch_parallel_batch(cls, run_id: str, indices: list[int]) -> None:
        from src.core.skills.celery_routing import resolve_skill_celery_queue
        from src.core.workflows.tasks import dispatch_parallel_batch_task

        run = get_workflow_run(run_id)
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            return
        steps = list_workflow_steps(run_id)
        if not update_run_step_index(run_id, indices[0], status="queued"):
            return
        for idx in indices:
            step = steps[idx]
            publish_workflow_event(
                run_id,
                step_name=step.step_name,
                skill_name=step.skill_name,
                status="queued",
                message=f"已进入并行执行队列: {step.skill_name}",
            )
        queues = {resolve_skill_celery_queue(steps[index].skill_name) for index in indices}
        coordinator_queue = queues.pop() if len(queues) == 1 else "netops.default"
        dispatch_parallel_batch_task.apply_async(
            args=[run_id, indices],
            queue=coordinator_queue,
            task_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"workflow-parallel-dispatch|{run_id}|{','.join(map(str, indices))}",
                )
            ),
        )
        log.info(
            "workflow_parallel_batch_dispatched",
            run_id=run_id,
            step_indices=indices,
            step_names=[steps[i].step_name for i in indices],
        )

    @classmethod
    def dispatch_step(cls, run_id: str, step_index: int) -> None:
        from src.core.skills.celery_routing import resolve_skill_celery_queue
        from src.core.workflows.tasks import dispatch_workflow_step_task

        run = get_workflow_run(run_id)
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            return
        steps = list_workflow_steps(run_id)
        if step_index >= len(steps):
            cls._complete_workflow(run_id)
            return
        if not update_run_step_index(run_id, step_index, status="queued"):
            return
        step = steps[step_index]
        publish_workflow_event(
            run_id,
            step_name=step.step_name,
            skill_name=step.skill_name,
            status="queued",
            message=f"已进入执行队列: {step.skill_name}",
        )
        queue = resolve_skill_celery_queue(step.skill_name)
        async_result = dispatch_workflow_step_task.apply_async(
            args=[run_id, step_index],
            queue=queue,
            task_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"workflow-step-dispatch|{run_id}|{step_index}",
                )
            ),
        )
        if not mark_step_queued(step.id, async_result.id):
            try:
                async_result.revoke(terminate=False)
            except Exception:
                pass
            log.warning(
                "workflow_step_dispatch_cancelled_before_commit",
                run_id=run_id,
                step_index=step_index,
                celery_task_id=async_result.id,
            )
            return
        log.info(
            "workflow_step_dispatched",
            run_id=run_id,
            step_index=step_index,
            step_name=step.step_name,
            skill_name=step.skill_name,
            celery_queue=queue,
            celery_task_id=async_result.id,
        )

    @classmethod
    def handle_step_complete(
        cls, run_id: str, step_index: int, raw_result: dict[str, Any] | None
    ) -> None:
        run = get_workflow_run(run_id)
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            log.warning(
                "workflow_late_step_completion_ignored",
                run_id=run_id,
                step_index=step_index,
                workflow_status=run.status if run else "missing",
            )
            return
        steps = list_workflow_steps(run_id)
        if step_index >= len(steps):
            return
        step = steps[step_index]
        result = normalize_step_result(raw_result or {})
        if not mark_step_completed(step.id, result):
            current = get_workflow_run(run_id)
            log.warning(
                "workflow_late_step_result_fenced",
                run_id=run_id,
                step_index=step_index,
                workflow_status=current.status if current else "missing",
            )
            return

        if not result.get("success"):
            err = result.get("error") or result.get("message") or "步骤执行失败"
            log.error(
                "workflow_step_failed",
                run_id=run_id,
                step_index=step_index,
                step_name=step.step_name,
                skill_name=step.skill_name,
                error=str(err),
            )
            if not update_run_status(
                run_id,
                "failed",
                error=str(err),
                failure_code=str(
                    result.get("failure_code") or "workflow_step_failed"
                ),
            ):
                return
            cls._project_source_terminal_state(run_id, run)
            record_workflow_step(
                _ensure_wf_trace(run_id),
                step_name=step.step_name,
                skill_name=step.skill_name,
                status="failed",
                message=str(err),
            )
            publish_workflow_event(
                run_id,
                step_name=step.step_name,
                skill_name=step.skill_name,
                status="failed",
                message=str(err),
            )
            cls._finish_trace(run_id, "failed", str(err))
            cls._notify_failure(run_id, str(err))
            failed_run = get_workflow_run(run_id)
            cls._request_patrol_batch_report_if_terminal(failed_run)
            log.error(
                "workflow_run_failed",
                run_id=run_id,
                template_name=failed_run.template_name if failed_run else None,
                error=str(err),
            )
            return

        record_workflow_step(
            _ensure_wf_trace(run_id),
            step_name=step.step_name,
            skill_name=step.skill_name,
            status="completed",
            message=result.get("message") or "步骤完成",
            output=result,
        )
        publish_workflow_event(
            run_id,
            step_name=step.step_name,
            skill_name=step.skill_name,
            status="completed",
            message=result.get("message") or "步骤完成",
        )
        log.info(
            "workflow_step_completed",
            run_id=run_id,
            step_index=step_index,
            step_name=step.step_name,
            skill_name=step.skill_name,
        )
        run = get_workflow_run(run_id)
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            return
        template = get_template(run.template_name) if run else None
        if template and template.on_complete.notify_each_step:
            cls._notify_step_progress(run_id, step, result)

        cls._advance_after_step(run_id, step_index, steps, template)

    @classmethod
    def _advance_after_step(cls, run_id: str, step_index: int, steps, template) -> None:
        run = get_workflow_run(run_id)
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            return
        next_index = step_index + 1
        if next_index >= len(steps):
            cls._complete_workflow(run_id)
            return
        batch = (
            cls._parallel_batch_indices(template, steps, next_index) if template else [next_index]
        )
        if len(batch) > 1:
            cls._dispatch_parallel_batch(run_id, batch)
        else:
            cls.dispatch_step(run_id, next_index)

    @classmethod
    def handle_parallel_batch_complete(
        cls, run_id: str, results: list[dict[str, Any]], next_index: int
    ) -> None:
        """并行批完成后继续调度或标记失败。"""
        run = get_workflow_run(run_id)
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            log.warning(
                "workflow_late_parallel_completion_ignored",
                run_id=run_id,
                workflow_status=run.status if run else "missing",
            )
            return
        for item in results or []:
            if not item.get("success", True):
                err = item.get("error") or item.get("message") or "并行步骤失败"
                log.error(
                    "workflow_parallel_batch_failed",
                    run_id=run_id,
                    error=str(err),
                    step_index=item.get("step_index"),
                )
                if not update_run_status(
                    run_id,
                    "failed",
                    error=str(err),
                    failure_code=str(
                        item.get("failure_code")
                        or "workflow_parallel_step_failed"
                    ),
                ):
                    return
                cls._project_source_terminal_state(run_id)
                cls._finish_trace(run_id, "failed", str(err))
                cls._notify_failure(run_id, str(err))
                return
        steps = list_workflow_steps(run_id)
        run = get_workflow_run(run_id)
        template = get_template(run.template_name) if run else None
        if next_index >= len(steps):
            cls._complete_workflow(run_id)
            return
        batch = (
            cls._parallel_batch_indices(template, steps, next_index) if template else [next_index]
        )
        if len(batch) > 1:
            cls._dispatch_parallel_batch(run_id, batch)
        else:
            cls.dispatch_step(run_id, next_index)

    @classmethod
    def _complete_workflow(cls, run_id: str) -> None:
        run = get_workflow_run(run_id)
        if not run or run.status in TERMINAL_WORKFLOW_STATUSES:
            return
        if not update_run_status(run_id, "completed"):
            return
        cls._project_source_terminal_state(run_id, run)
        template = get_template(run.template_name)
        steps = list_workflow_steps(run_id)
        artifacts: dict[str, Any] = {}
        for s in steps:
            if s.output_artifacts:
                artifacts.update(s.output_artifacts)

        oc = template.on_complete if template else None
        message = oc.message if oc else "Workflow 已完成"
        env = build_step_env(
            context=run.context or {},
            run_id=run_id,
            ticket_id=run.ticket_id,
            step_records=steps,
            current_step_index=len(steps),
        )
        from src.core.workflows.artifacts import (
            collect_download_links,
        )

        publish_workflow_event(
            run_id,
            status="completed",
            message=message,
            extra={
                "downloads": collect_download_links(artifacts=artifacts),
                "langfuse_trace_id": (run.context or {}).get("langfuse_trace_id"),
            },
        )
        cls._finish_trace(
            run_id, "completed", message, output={"artifacts": list(artifacts.keys())}
        )
        cls._notify_success(run_id, template, env, artifacts)
        cls._request_patrol_batch_report_if_terminal(get_workflow_run(run_id) or run)
        log.info(
            "workflow_run_completed",
            run_id=run_id,
            template_name=run.template_name,
            ticket_id=run.ticket_id,
            artifact_keys=list(artifacts.keys()),
        )

    @classmethod
    def _request_patrol_batch_report_if_terminal(cls, run: Any | None) -> None:
        if run is None or run.template_name not in {
            "patrol-change-event",
            "patrol-attested-offline-current",
        }:
            return
        context = dict(run.context or {})
        patrol_batch_id = str(context.get("patrol_batch_id") or "").strip()
        if not patrol_batch_id:
            return
        cls.request_patrol_batch_report_if_terminal(
            patrol_batch_id,
            run_id=str(getattr(run, "id", "") or "") or None,
        )

    @classmethod
    def request_patrol_batch_report_if_terminal(
        cls, patrol_batch_id: str, *, run_id: str | None = None,
    ) -> bool:
        from src.apps.inspection.backend.reports import request_terminal_report

        return request_terminal_report(patrol_batch_id, run_id=run_id)

    @classmethod
    def cancel_run(
        cls,
        run_id: str,
        *,
        actor: str,
        reason: str,
        revoke: bool = True,
        _visited: set[str] | None = None,
    ) -> dict[str, Any]:
        """Cancel one workflow tree and fence late Celery callbacks."""

        visited = _visited if _visited is not None else set()
        if run_id in visited:
            return {
                "found": True,
                "changed": False,
                "idempotent": True,
                "run_id": run_id,
                "status": "cancelled",
                "child_results": [],
            }
        visited.add(run_id)

        result = cancel_workflow_run(run_id, actor=actor, reason=reason)
        child_results: list[dict[str, Any]] = []
        if result.get("found") and result.get("status") == "cancelled":
            for child in list_child_runs(run_id):
                if str(child.status or "") in TERMINAL_WORKFLOW_STATUSES:
                    continue
                child_results.append(
                    cls.cancel_run(
                        str(child.id),
                        actor=actor,
                        reason=f"parent {run_id} cancelled: {reason}",
                        revoke=revoke,
                        _visited=visited,
                    )
                )

        if revoke:
            try:
                from src.core.celery_tasks.celery_app import celery

                for task_id in result.get("task_ids") or []:
                    celery.control.revoke(str(task_id), terminate=False)
            except Exception as exc:
                log.warning(
                    "workflow_cancel_revoke_failed",
                    run_id=run_id,
                    error=str(exc),
                )

        if result.get("changed"):
            message = f"Workflow cancelled: {reason}"
            publish_workflow_event(
                run_id,
                status="cancelled",
                message=message,
                extra={"cancelled_by": actor, "reason": reason},
            )
            cls._finish_trace(run_id, "cancelled", message)
            cls._project_source_terminal_state(run_id)
            cls._notify_cancelled(run_id, actor=actor, reason=reason)

        for patrol_batch_id in result.get("patrol_batch_ids") or []:
            cls.request_patrol_batch_report_if_terminal(
                str(patrol_batch_id),
                run_id=run_id,
            )
        return {**result, "child_results": child_results}

    @classmethod
    def _project_source_terminal_state(cls, run_id: str, run=None) -> None:
        run = run or get_workflow_run(run_id)
        context = run.context if run and isinstance(run.context, dict) else {}
        if (
            run is not None
            and run.template_name == "patrol-attested-offline-current"
        ):
            try:
                from src.core.check_items.current_authority import (
                    PatrolCurrentAuthorityService,
                )

                PatrolCurrentAuthorityService().sync_workflow_terminal_state(
                    run_id
                )
            except Exception as exc:
                log.warning(
                    "current_authority_terminal_projection_failed",
                    run_id=run_id,
                    error=str(exc),
                )
            return
        if not run or (
            getattr(run, "source", None) != "offline-patrol"
            and not context.get("offline_job_id")
        ):
            return
        try:
            from src.core.patrol.offline_jobs import OfflinePatrolJobService

            OfflinePatrolJobService().sync_workflow_terminal_state(run_id)
        except Exception as exc:
            log.warning(
                "offline_patrol_terminal_projection_failed",
                run_id=run_id,
                error=str(exc),
            )

    @classmethod
    def _finish_trace(
        cls, run_id: str, status: str, message: str, *, output: Any | None = None
    ) -> None:
        wf_trace = _pop_wf_trace(run_id)
        if not wf_trace:
            wf_trace = _ensure_wf_trace(run_id)
            _WF_TRACES.pop(run_id, None)
        end_workflow_trace(wf_trace, status=status, message=message, output=output)

    @classmethod
    def _notify_success(
        cls,
        run_id: str,
        template,
        env: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> None:
        run = get_workflow_run(run_id)
        if not run or not run.user_id:
            return
        oc = template.on_complete if template else None
        title_tpl = oc.notification_title if oc else "Workflow 已完成 (${context.ticket_id})"
        body_tpl = oc.notification_body if oc else "流程已完成。"
        title = str(resolve_value(title_tpl, env) if title_tpl else "Workflow 已完成")
        body = str(resolve_value(body_tpl, env) if body_tpl else "流程已完成。")
        from src.core.workflows.artifacts import (
            collect_download_links,
            notification_download_payload,
        )

        links = collect_download_links(artifacts=artifacts)
        if links:
            body = (
                body.rstrip()
                + "\n\n"
                + "\n".join(f"下载 {lnk['label']}: {lnk['url']}" for lnk in links)
            )

        published, notify_key = cls._publish_workflow_notify(
            "workflow.completed",
            run_id,
            {
                "title": title,
                "body": body,
                "artifacts": artifacts,
                "level": (oc.notification_level if oc else "success"),
            },
        )
        if published:
            return
        create_notification(
            user_id=run.user_id,
            title=title,
            body=body,
            workflow_run_id=run_id,
            thread_id=run.thread_id,
            payload=notification_download_payload(artifacts=artifacts),
            level=(oc.notification_level if oc else "success"),
            idempotency_key=notify_key,
        )

    @classmethod
    def _notify_step_progress(cls, run_id: str, step, result: dict[str, Any]) -> None:
        """步骤完成时发送站内通知（便于 Bell 跟踪中间进度）。"""
        run = get_workflow_run(run_id)
        if not run or not run.user_id:
            return
        label = step.step_name.replace("_", " ")
        from src.core.workflows.artifacts import (
            collect_download_links,
            notification_download_payload,
        )

        body = str(result.get("message") or step.skill_name)

        links = collect_download_links(result=result if isinstance(result, dict) else None)
        if links:
            body = (
                body.rstrip()
                + "\n\n"
                + "\n".join(f"下载 {lnk['label']}: {lnk['url']}" for lnk in links)
            )

        published, notify_key = cls._publish_workflow_notify(
            "workflow.step.completed",
            run_id,
            {
                "step_name": step.step_name,
                "step_label": label,
                "skill_name": step.skill_name,
                "notify_user": True,
                "result": result if isinstance(result, dict) else None,
            },
        )
        if published:
            return
        create_notification(
            user_id=run.user_id,
            title=f"Workflow 步骤完成 — {label} ({run.ticket_id or run_id[:8]})",
            body=body,
            workflow_run_id=run_id,
            thread_id=run.thread_id,
            payload=notification_download_payload(
                result=result if isinstance(result, dict) else None
            ),
            level="info",
            idempotency_key=notify_key,
        )

    @classmethod
    def _notify_failure(cls, run_id: str, error: str) -> None:
        run = get_workflow_run(run_id)
        if not run or not run.user_id:
            return
        template = get_template(run.template_name)
        if template and not template.on_complete.notify_on_failure:
            return
        published, notify_key = cls._publish_workflow_notify(
            "workflow.failed",
            run_id,
            {
                "error": error,
                "notify_on_failure": template.on_complete.notify_on_failure if template else True,
            },
        )
        if published:
            return
        create_notification(
            user_id=run.user_id,
            title=f"Workflow 失败 ({run.ticket_id or run_id[:8]})",
            body=error,
            workflow_run_id=run_id,
            thread_id=run.thread_id,
            level="error",
            idempotency_key=notify_key,
        )

    @classmethod
    def _notify_cancelled(cls, run_id: str, *, actor: str, reason: str) -> None:
        run = get_workflow_run(run_id)
        if not run or not run.user_id:
            return
        title = f"Workflow cancelled ({run.ticket_id or run_id[:8]})"
        body = f"Cancelled by {actor}: {reason}"
        published, notify_key = cls._publish_workflow_notify(
            "workflow.cancelled",
            run_id,
            {
                "title": title,
                "body": body,
                "level": "warning",
                "cancelled_by": actor,
                "reason": reason,
            },
        )
        if published:
            return
        create_notification(
            user_id=run.user_id,
            title=title,
            body=body,
            workflow_run_id=run_id,
            thread_id=run.thread_id,
            level="warning",
            idempotency_key=notify_key or f"workflow-cancelled|{run_id}",
        )

    @classmethod
    def build_step_params(cls, run_id: str, step_index: int) -> dict[str, Any]:
        run = get_workflow_run(run_id)
        if not run:
            return {}

        steps = list_workflow_steps(run_id)
        if step_index >= len(steps):
            return dict(run.context or {})

        template = get_template(run.template_name)
        if not template:
            return dict(run.context or {})

        step_rec = steps[step_index]
        from src.core.workflows.registry import find_step_template

        step_tpl = find_step_template(template, step_rec.step_name)
        if not step_tpl:
            return dict(run.context or {})
        env = build_step_env(
            context=dict(run.context or {}),
            run_id=run_id,
            ticket_id=run.ticket_id,
            step_records=steps,
            current_step_index=step_index,
        )
        params = resolve_inputs(step_tpl.inputs, env)
        params["workflow_run_id"] = run_id
        trace_id = (run.context or {}).get("langfuse_trace_id")
        if trace_id:
            params["langfuse_trace_id"] = trace_id
        root_span_id = (run.context or {}).get("langfuse_workflow_root_span_id")
        if root_span_id:
            params["langfuse_workflow_root_span_id"] = root_span_id
        return params
