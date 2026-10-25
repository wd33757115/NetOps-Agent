# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Langfuse 追踪封装（兼容自托管 Langfuse v2 + Python SDK v2）。"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from src.auth.models import CurrentUser
from src.common.config import get_settings

logger = logging.getLogger(__name__)

_langfuse_client: Any | None = None


def is_langfuse_enabled() -> bool:
    settings = get_settings()
    return bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)


def get_langfuse_client() -> Any | None:
    """获取 Langfuse v2 客户端单例。"""
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client
    if not is_langfuse_enabled():
        return None

    settings = get_settings()
    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST or "http://localhost:3001",
        )
        return _langfuse_client
    except Exception as exc:
        logger.warning("Langfuse client init failed: %s", exc)
        return None


def _build_metadata(
    *,
    user: CurrentUser | None,
    thread_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "conversation_id": conversation_id,
        "graph_thread_id": thread_id,
    }
    if user:
        metadata.update(
            {
                "username": user.username,
                "role": user.role,
                "thread_prefix": user.thread_prefix,
                "can_view_trace_detail": user.can_view_trace_detail(),
            }
        )
    return metadata


@dataclass
class LangfuseChatTrace:
    client: Any
    trace: Any
    node_spans: dict[str, Any] = field(default_factory=dict)

    @property
    def trace_id(self) -> str | None:
        trace_id = getattr(self.trace, "id", None)
        return str(trace_id) if trace_id else None


def start_chat_trace(
    *,
    user: CurrentUser | None,
    thread_id: str,
    conversation_id: str,
    trace_name: str = "supervisor_v2_chat",
    query: str | None = None,
) -> LangfuseChatTrace | None:
    """创建 Langfuse trace；未配置密钥时返回 None。"""
    client = get_langfuse_client()
    if not client:
        return None

    try:
        trace = client.trace(
            name=trace_name,
            user_id=user.user_id if user else None,
            session_id=thread_id,
            metadata=_build_metadata(
                user=user,
                thread_id=thread_id,
                conversation_id=conversation_id,
            ),
            input={"query": query} if query else None,
        )
        return LangfuseChatTrace(client=client, trace=trace)
    except Exception as exc:
        logger.warning("Langfuse trace start failed: %s", exc)
        return None


def record_graph_node(
    lf_trace: LangfuseChatTrace | None,
    node_name: str,
    summary: dict[str, Any],
) -> None:
    if not lf_trace:
        return
    try:
        span = lf_trace.trace.span(name=node_name, metadata=summary)
        span.end(output=summary)
        lf_trace.node_spans[node_name] = span
    except Exception as exc:
        logger.debug("Langfuse node span failed for %s: %s", node_name, exc)


def end_chat_trace(
    lf_trace: LangfuseChatTrace | None,
    *,
    output: Any | None = None,
    error: str | None = None,
) -> None:
    if not lf_trace:
        return
    try:
        if error:
            lf_trace.trace.update(output={"error": error}, level="ERROR")
        elif output is not None:
            lf_trace.trace.update(output=output)
    except Exception as exc:
        logger.warning("Langfuse trace update failed: %s", exc)
    finally:
        flush_langfuse(lf_trace.client)


def flush_langfuse(client: Any | None = None) -> None:
    target = client or get_langfuse_client()
    if not target:
        return
    try:
        target.flush()
    except Exception as exc:
        logger.warning("Langfuse flush failed: %s", exc)


def get_trace_url(trace_id: str | None) -> str | None:
    if not trace_id or not is_langfuse_enabled():
        return None
    host = (get_settings().LANGFUSE_HOST or "http://localhost:3001").rstrip("/")
    return f"{host}/trace/{trace_id}"


def _redact_span_input(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    redacted: dict[str, Any] = {}
    for key, value in params.items():
        if key in {"password", "secret", "token", "api_key"}:
            redacted[key] = "***"
        elif isinstance(value, str) and len(value) > 500:
            redacted[key] = value[:500] + "..."
        else:
            redacted[key] = value
    return redacted


@dataclass
class LangfuseWorkflowTrace:
    client: Any
    trace: Any | None
    langfuse_trace_id: str
    workflow_root_span_id: str | None = None
    step_spans: dict[str, Any] = field(default_factory=dict)

    @property
    def trace_id(self) -> str | None:
        return self.langfuse_trace_id or None

    @property
    def nested_under_chat(self) -> bool:
        return bool(self.workflow_root_span_id)


def resume_workflow_trace(
    trace_id: str,
    *,
    run_id: str,
    template_name: str | None = None,
    workflow_root_span_id: str | None = None,
) -> LangfuseWorkflowTrace | None:
    """在 Celery Worker 中恢复 Workflow 观测上下文（独立 trace 或聊天 trace 下的 span 树）。"""
    client = get_langfuse_client()
    if not client or not trace_id:
        return None
    try:
        if workflow_root_span_id:
            return LangfuseWorkflowTrace(
                client=client,
                trace=None,
                langfuse_trace_id=str(trace_id),
                workflow_root_span_id=str(workflow_root_span_id),
            )
        trace = client.trace(
            id=trace_id,
            name=f"workflow:{template_name or 'resumed'}",
            metadata={"run_id": run_id, "template_name": template_name, "resumed": True},
        )
        return LangfuseWorkflowTrace(
            client=client,
            trace=trace,
            langfuse_trace_id=str(trace_id),
        )
    except Exception as exc:
        logger.warning("Langfuse workflow trace resume failed: %s", exc)
        return None


def _create_workflow_step_span(
    wf_trace: LangfuseWorkflowTrace,
    *,
    step_name: str,
    skill_name: str | None,
    status: str,
    message: str,
    output: Any | None,
) -> Any | None:
    metadata = {"skill": skill_name, "status": status, "message": message}
    inputs = {"step": step_name, "skill": skill_name}
    if wf_trace.workflow_root_span_id:
        return wf_trace.client.span(
            trace_id=wf_trace.langfuse_trace_id,
            parent_observation_id=wf_trace.workflow_root_span_id,
            name=f"step:{step_name}",
            metadata=metadata,
            input=inputs,
            output=output,
        )
    if wf_trace.trace is not None:
        return wf_trace.trace.span(
            name=f"step:{step_name}",
            metadata=metadata,
            input=inputs,
            output=output,
        )
    return None


def record_skill_execution_span(
    *,
    trace_id: str | None,
    skill_name: str,
    run_id: str | None = None,
    step_name: str | None = None,
    parent_observation_id: str | None = None,
    status: str = "completed",
    message: str = "",
    input_params: dict[str, Any] | None = None,
    output: Any | None = None,
    error: str | None = None,
) -> None:
    """在既有 trace 下记录 Skill 执行 span（Celery / subprocess）。"""
    if not trace_id:
        return
    client = get_langfuse_client()
    if not client:
        return
    level = "ERROR" if error or status == "failed" else "DEFAULT"
    try:
        span_kwargs: dict[str, Any] = {
            "trace_id": trace_id,
            "name": f"skill:{skill_name}",
            "metadata": {
                "skill_name": skill_name,
                "run_id": run_id,
                "step_name": step_name,
                "status": status,
                "message": message,
            },
            "input": _redact_span_input(input_params),
            "output": output if not error else {"error": error, "output": output},
            "level": level,
            "status_message": error or message or None,
        }
        if parent_observation_id:
            span_kwargs["parent_observation_id"] = parent_observation_id
        span = client.span(**span_kwargs)
        span.end()
    except Exception as exc:
        logger.debug("Langfuse skill span failed skill=%s: %s", skill_name, exc)
    finally:
        flush_langfuse(client)


@contextmanager
def skill_execution_span(
    *,
    trace_id: str | None,
    skill_name: str,
    run_id: str | None = None,
    step_name: str | None = None,
    input_params: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Skill 执行上下文：成功/失败均写入 Langfuse span。"""
    try:
        yield
    except Exception as exc:
        record_skill_execution_span(
            trace_id=trace_id,
            skill_name=skill_name,
            run_id=run_id,
            step_name=step_name,
            status="failed",
            message=str(exc),
            input_params=input_params,
            error=str(exc),
        )
        raise
    else:
        record_skill_execution_span(
            trace_id=trace_id,
            skill_name=skill_name,
            run_id=run_id,
            step_name=step_name,
            status="completed",
            input_params=input_params,
            output={"success": True},
        )


def start_workflow_trace(
    *,
    run_id: str,
    template_name: str,
    ticket_id: str | None = None,
    source: str = "chat",
    user_id: str | None = None,
    parent_run_id: str | None = None,
    parent_trace_id: str | None = None,
) -> LangfuseWorkflowTrace | None:
    """为 Workflow Run 创建 Langfuse trace，或在聊天 trace 下创建嵌套 workflow span。"""
    client = get_langfuse_client()
    if not client:
        return None
    metadata: dict[str, Any] = {
        "run_id": run_id,
        "template_name": template_name,
        "source": source,
    }
    if parent_run_id:
        metadata["parent_run_id"] = parent_run_id
    if parent_trace_id:
        metadata["parent_chat_trace_id"] = parent_trace_id
    try:
        if parent_trace_id:
            root_span = client.span(
                trace_id=parent_trace_id,
                name=f"workflow:{template_name}",
                metadata=metadata,
                input={"ticket_id": ticket_id, "template": template_name, "run_id": run_id},
            )
            return LangfuseWorkflowTrace(
                client=client,
                trace=root_span,
                langfuse_trace_id=str(parent_trace_id),
                workflow_root_span_id=str(root_span.id),
            )

        trace = client.trace(
            name=f"workflow:{template_name}",
            user_id=user_id,
            session_id=run_id,
            metadata=metadata,
            input={"ticket_id": ticket_id, "template": template_name},
            tags=["workflow", source],
        )
        return LangfuseWorkflowTrace(
            client=client,
            trace=trace,
            langfuse_trace_id=str(getattr(trace, "id", "") or ""),
        )
    except Exception as exc:
        logger.warning("Langfuse workflow trace start failed: %s", exc)
        return None


def record_workflow_step(
    wf_trace: LangfuseWorkflowTrace | None,
    *,
    step_name: str,
    skill_name: str | None,
    status: str,
    message: str = "",
    output: Any | None = None,
) -> None:
    if not wf_trace:
        return
    try:
        span = _create_workflow_step_span(
            wf_trace,
            step_name=step_name,
            skill_name=skill_name,
            status=status,
            message=message,
            output=output,
        )
        if span is not None:
            span.end()
            wf_trace.step_spans[step_name] = span
    except Exception as exc:
        logger.debug("Langfuse workflow step span failed: %s", exc)


def end_workflow_trace(
    wf_trace: LangfuseWorkflowTrace | None,
    *,
    status: str,
    message: str = "",
    output: Any | None = None,
) -> None:
    if not wf_trace:
        return
    try:
        level = "ERROR" if status == "failed" else "DEFAULT"
        output_data = output or {"status": status, "message": message}
        target = wf_trace.trace
        if target is None and wf_trace.workflow_root_span_id:
            target = wf_trace.client.span(
                id=wf_trace.workflow_root_span_id,
                trace_id=wf_trace.langfuse_trace_id,
            )
        if target is not None:
            target.update(output=output_data, level=level)
            if hasattr(target, "end"):
                target.end()
        elif wf_trace.langfuse_trace_id:
            wf_trace.client.trace(id=wf_trace.langfuse_trace_id).update(
                output=output_data,
                level=level,
            )
    except Exception as exc:
        logger.warning("Langfuse workflow trace end failed: %s", exc)
    finally:
        flush_langfuse(wf_trace.client)
