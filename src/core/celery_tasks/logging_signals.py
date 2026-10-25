# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Celery 信号：Worker 日志初始化与任务上下文。"""

from __future__ import annotations

import time
from typing import Any

from celery.signals import task_failure, task_postrun, task_prerun, worker_process_init

from src.common.config import get_settings
from src.core.logging import bind_context, configure_logging, get_logger, reset_context
from src.observability.trace_context import extract_observability_context, observability_from_workflow_run

logger = get_logger(__name__)

_task_start: dict[str, float] = {}
_task_tokens: dict[str, list] = {}


def _resolve_observability(task: Any, args: tuple[Any, ...], task_kwargs: dict[str, Any]) -> dict[str, str | None]:
    obs = extract_observability_context(task_kwargs)
    task_name = getattr(task, "name", "") or ""

    if not obs["run_id"] and args and "workflow" in task_name:
        obs["run_id"] = str(args[0])

    if obs["run_id"] and not obs["trace_id"]:
        from_run = observability_from_workflow_run(obs["run_id"])
        obs["trace_id"] = from_run.get("trace_id")

    if not obs["trace_id"] and not obs["run_id"] and args and "workflow" in task_name:
        obs = observability_from_workflow_run(str(args[0]))

    return obs


@worker_process_init.connect
def _init_worker_logging(**kwargs: Any) -> None:
    settings = get_settings()
    configure_logging(
        log_level=settings.LOG_LEVEL,
        log_format=settings.LOG_FORMAT,
        force=True,
    )
    logger.info("celery_worker_logging_ready")


@task_prerun.connect
def _task_prerun_handler(
    sender: Any = None,
    task_id: str | None = None,
    task: Any = None,
    args: tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
    **_: Any,
) -> None:
    task_id = task_id or ""
    _task_start[task_id] = time.perf_counter()
    obs = _resolve_observability(task, tuple(args or ()), dict(kwargs or {}))

    bind_pairs: dict[str, str | None] = {"celery_task_id": task_id}
    if obs.get("trace_id"):
        bind_pairs["trace_id"] = obs["trace_id"]
    if obs.get("run_id"):
        bind_pairs["run_id"] = obs["run_id"]

    _task_tokens[task_id] = bind_context(**bind_pairs)
    logger.info(
        "celery_task_started",
        task_name=getattr(task, "name", None),
        trace_id=obs.get("trace_id"),
        run_id=obs.get("run_id"),
    )


@task_postrun.connect
def _task_postrun_handler(
    sender: Any = None,
    task_id: str | None = None,
    task: Any = None,
    args: tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
    retval: Any = None,
    state: str | None = None,
    **_: Any,
) -> None:
    task_id = task_id or ""
    started = _task_start.pop(task_id, None)
    duration_ms = int((time.perf_counter() - started) * 1000) if started else None
    logger.info(
        "celery_task_finished",
        task_name=getattr(task, "name", None),
        state=state,
        duration_ms=duration_ms,
    )
    tokens = _task_tokens.pop(task_id, [])
    if tokens:
        reset_context(tokens)


@task_failure.connect
def _task_failure_handler(
    sender: Any = None,
    task_id: str | None = None,
    exception: BaseException | None = None,
    args: tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
    traceback: Any = None,
    einfo: Any = None,
    **_: Any,
) -> None:
    task_id = task_id or ""
    _task_start.pop(task_id, None)
    logger.error(
        "celery_task_failed",
        task_name=getattr(sender, "name", None),
        error=str(exception),
        exc_info=exception,
    )
    tokens = _task_tokens.pop(task_id, [])
    if tokens:
        reset_context(tokens)
