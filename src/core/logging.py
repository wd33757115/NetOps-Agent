# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""结构化日志：structlog + stdlib 桥接，支持 JSON / Console 与上下文变量。"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar, Token
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# 请求 / 链路上下文（通过 bind_context / middleware / Celery signal 注入）
# ---------------------------------------------------------------------------
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
thread_id_var: ContextVar[str | None] = ContextVar("thread_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
ticket_id_var: ContextVar[str | None] = ContextVar("ticket_id", default=None)
celery_task_id_var: ContextVar[str | None] = ContextVar("celery_task_id", default=None)

_CONTEXT_VARS: dict[str, ContextVar[str | None]] = {
    "request_id": request_id_var,
    "trace_id": trace_id_var,
    "run_id": run_id_var,
    "thread_id": thread_id_var,
    "user_id": user_id_var,
    "ticket_id": ticket_id_var,
    "celery_task_id": celery_task_id_var,
}

_configured = False


def _merge_contextvars(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    for key, var in _CONTEXT_VARS.items():
        value = var.get()
        if value is not None:
            event_dict[key] = value
    return event_dict


def configure_logging(
    *,
    log_level: str = "INFO",
    log_format: str = "console",
    force: bool = False,
) -> None:
    """初始化 structlog 与 stdlib logging（进程内幂等，除非 force=True）。"""
    global _configured
    if _configured and not force:
        return

    level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = (log_format or "console").strip().lower()

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # 第三方库默认 WARNING，避免刷屏
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "celery", "kombu", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(max(level, logging.WARNING))

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取绑定了项目 processor 的 logger。"""
    return structlog.get_logger(name)


def bind_context(**kwargs: str | None) -> list[tuple[ContextVar[str | None], Token]]:
    """绑定上下文变量；返回 (var, token) 列表供 reset_context 使用。"""
    pairs: list[tuple[ContextVar[str | None], Token]] = []
    for key, value in kwargs.items():
        var = _CONTEXT_VARS.get(key)
        if var is None or value is None:
            continue
        pairs.append((var, var.set(str(value))))
    return pairs


def reset_context(pairs: list[tuple[ContextVar[str | None], Token]]) -> None:
    for var, token in reversed(pairs):
        var.reset(token)
