# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Langfuse / Workflow trace 上下文在 Celery 与 Skill 间的传递。"""

from __future__ import annotations

from typing import Any

# 仅观测用，不应写入 Skill CLI params.json
INTERNAL_OBSERVABILITY_KEYS = frozenset(
    {"langfuse_trace_id", "langfuse_workflow_root_span_id", "parent_trace_id"}
)


def extract_observability_context(params: dict[str, Any] | None) -> dict[str, str | None]:
    """从 Skill/Celery 参数字典提取 trace_id / run_id / workflow root span。"""
    data = params or {}
    trace_id = data.get("langfuse_trace_id") or data.get("parent_trace_id")
    run_id = data.get("workflow_run_id") or data.get("run_id")
    root_span_id = data.get("langfuse_workflow_root_span_id")
    return {
        "trace_id": str(trace_id) if trace_id else None,
        "run_id": str(run_id) if run_id else None,
        "workflow_root_span_id": str(root_span_id) if root_span_id else None,
    }


def strip_observability_keys(params: dict[str, Any]) -> dict[str, Any]:
    """移除内部观测字段，避免污染 Skill 脚本入参。"""
    return {k: v for k, v in params.items() if k not in INTERNAL_OBSERVABILITY_KEYS}


def observability_from_workflow_run(run_id: str | None) -> dict[str, str | None]:
    """从 Workflow run 记录读取 langfuse_trace_id。"""
    if not run_id:
        return {"trace_id": None, "run_id": None, "workflow_root_span_id": None}
    try:
        from src.core.workflows.repository import get_workflow_run

        run = get_workflow_run(run_id)
        if not run:
            return {"trace_id": None, "run_id": run_id, "workflow_root_span_id": None}
        ctx = run.context or {}
        trace_id = ctx.get("langfuse_trace_id")
        root_span_id = ctx.get("langfuse_workflow_root_span_id")
        return {
            "trace_id": str(trace_id) if trace_id else None,
            "run_id": run_id,
            "workflow_root_span_id": str(root_span_id) if root_span_id else None,
        }
    except Exception:
        return {"trace_id": None, "run_id": run_id, "workflow_root_span_id": None}
