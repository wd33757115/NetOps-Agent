# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""SkillRunner：Skill 执行单出口（标准化结果 + 持久化 + Workspace）。"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.core.skills.repository import save_skill_execution
from src.core.skills.resolver import get_skill_version
from src.core.skills.result import ErrorInfo, ExecutionContext, SkillExecutionResult, SkillStatus
from src.core.skills.workspace import ExecutionWorkspace
from src.observability.langfuse import record_skill_execution_span
from src.observability.trace_context import extract_observability_context, strip_observability_keys

logger = logging.getLogger(__name__)


def _context_from_params(params: dict[str, Any]) -> ExecutionContext:
    obs = extract_observability_context(params)
    return ExecutionContext(
        source=str(
            params.pop("_execution_source", None)
            or params.pop("execution_source", None)
            or "celery"
        ),
        message_id=params.pop("_message_id", None) or params.pop("message_id", None),
        thread_id=params.pop("_thread_id", None) or params.pop("thread_id", None),
        run_id=obs.get("run_id"),
        step_name=params.pop("_step_name", None),
        step_id=params.pop("_step_id", None),
        user_id=params.pop("_user_id", None) or params.pop("user_id", None),
        ticket_id=params.pop("ticket_id", None),
        trace_id=obs.get("trace_id"),
    )


def finalize_skill_execution(result: SkillExecutionResult) -> SkillExecutionResult:
    """持久化 + 写入 ExecutionWorkspace + 发布领域事件（best-effort）。"""
    save_skill_execution(result)
    ExecutionWorkspace.put(result.context.thread_id, result.context.message_id, result)
    try:
        from src.core.events.publishers import publish_skill_execution_event

        publish_skill_execution_event(result)
    except Exception as exc:
        logger.warning("publish_skill_execution_event failed: %s", exc)
    return result


def record_chat_skill_result(
    raw_result: Any,
    *,
    skill_name: str,
    context: ExecutionContext,
    input_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Chat registry 路径：SkillResult → 标准 envelope → 持久化。"""
    skill_version = get_skill_version(skill_name)
    if hasattr(raw_result, "success"):
        ser = SkillExecutionResult.from_skill_result(
            raw_result,
            skill_name=skill_name,
            skill_version=skill_version,
            context=context,
        )
    elif isinstance(raw_result, dict):
        ser = SkillExecutionResult.from_legacy_dict(
            raw_result,
            skill_name=skill_name,
            skill_version=skill_version,
            context=context,
        )
    else:
        ser = SkillExecutionResult(
            skill_name=skill_name,
            skill_version=skill_version,
            status=SkillStatus.ERROR,
            message="未知结果类型",
            context=context,
        )
    if input_params:
        ser.metadata.setdefault("input_params", input_params)
    finalize_skill_execution(ser)
    return ser.to_legacy_dict()


class SkillRunner:
    @classmethod
    def run(
        cls,
        skill_name: str,
        params: dict[str, Any] | None = None,
        *,
        context: ExecutionContext | None = None,
    ) -> SkillExecutionResult:
        from src.core.skills.executor import SkillExecutionError, _execute_skill_impl

        from src.platform.kernel.registry import migrated_skill_app
        migrated_app = migrated_skill_app(skill_name)
        if migrated_app:
            raise SkillExecutionError(f"该能力已迁入业务应用：/apps/{migrated_app}")

        params = dict(params or {})
        obs = extract_observability_context(params)
        if context is None:
            context = _context_from_params(params)
        else:
            params = strip_observability_keys(params)

        skill_params = strip_observability_keys(dict(params))
        trace_id = obs["trace_id"] or context.trace_id
        run_id = obs["run_id"] or context.run_id
        parent_observation_id = obs["workflow_root_span_id"]
        skill_version = get_skill_version(skill_name)
        user_id = context.user_id or skill_params.get("user_id")
        from src.skill_system.governance.rollout import is_skill_executable

        ok, gov_msg = is_skill_executable(skill_name, user_id=str(user_id) if user_id else None)
        if not ok:
            raise SkillExecutionError(gov_msg)
        t0 = time.time()

        try:
            raw = _execute_skill_impl(skill_name, skill_params)
            ser = SkillExecutionResult.from_legacy_dict(
                raw,
                skill_name=skill_name,
                skill_version=skill_version,
                context=context,
            )
            if not ser.success:
                record_skill_execution_span(
                    trace_id=trace_id,
                    skill_name=skill_name,
                    run_id=run_id,
                    parent_observation_id=parent_observation_id,
                    status="failed",
                    message=ser.message
                    or (ser.error_info.message if ser.error_info else "Skill 失败"),
                    input_params=skill_params,
                    output=raw,
                    error=ser.error_info.message if ser.error_info else ser.message,
                )
            else:
                record_skill_execution_span(
                    trace_id=trace_id,
                    skill_name=skill_name,
                    run_id=run_id,
                    parent_observation_id=parent_observation_id,
                    status="completed",
                    message=ser.message or "Skill 完成",
                    input_params=skill_params,
                    output=ser.to_summary(),
                )
        except SkillExecutionError as exc:
            duration_ms = int((time.time() - t0) * 1000)
            ser = SkillExecutionResult(
                skill_name=skill_name,
                skill_version=skill_version,
                status=SkillStatus.ERROR,
                message=str(exc),
                error_info=ErrorInfo(message=str(exc)),
                metadata={"duration_ms": duration_ms, "input_params": skill_params},
                context=context,
            )
            record_skill_execution_span(
                trace_id=trace_id,
                skill_name=skill_name,
                run_id=run_id,
                parent_observation_id=parent_observation_id,
                status="failed",
                message=str(exc),
                input_params=skill_params,
                error=str(exc),
            )
            finalize_skill_execution(ser)
            raise
        except Exception as exc:
            record_skill_execution_span(
                trace_id=trace_id,
                skill_name=skill_name,
                run_id=run_id,
                parent_observation_id=parent_observation_id,
                status="failed",
                message=str(exc),
                input_params=skill_params,
                error=str(exc),
            )
            raise

        duration_ms = int((time.time() - t0) * 1000)
        ser.metadata["duration_ms"] = duration_ms
        ser.metadata.setdefault("input_params", skill_params)
        finalize_skill_execution(ser)
        return ser
