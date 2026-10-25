# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Celery 任务提交与结果等待（快速失败，避免聊天线程长时间阻塞）。"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

CELERY_SKILL_WAIT_TIMEOUT = int(os.getenv("CELERY_SKILL_WAIT_TIMEOUT", "120"))
CELERY_INSPECT_TIMEOUT = float(os.getenv("CELERY_INSPECT_TIMEOUT", "2.0"))


class CeleryWorkerUnavailableError(RuntimeError):
    """无可用 Celery Worker。"""


def celery_workers_available() -> bool:
    """检测是否有 Worker 响应 ping（约 2 秒内返回）。"""
    try:
        from src.core.celery_tasks.celery_app import celery

        inspect = celery.control.inspect(timeout=CELERY_INSPECT_TIMEOUT)
        ping = inspect.ping()
        return bool(ping)
    except Exception as exc:
        logger.warning("Celery inspect 失败: %s", exc)
        return False


def wait_celery_task_result(async_result, timeout: int | None = None):
    """
    等待 Celery 任务结果。Worker 未启动时快速失败，避免 result.get(300) 长时间挂起。
    """
    from celery.exceptions import TimeoutError as CeleryTimeoutError

    if not celery_workers_available():
        raise CeleryWorkerUnavailableError(
            "Celery Worker 未运行，无法执行后台任务。请启动 Celery Worker（例如 scripts\\test\\start.ps1）。"
        )

    wait_seconds = timeout if timeout is not None else CELERY_SKILL_WAIT_TIMEOUT
    try:
        return async_result.get(timeout=wait_seconds)
    except CeleryTimeoutError as exc:
        raise TimeoutError(
            f"Celery 任务在 {wait_seconds} 秒内未完成，请查看 Worker 日志或增大 CELERY_SKILL_WAIT_TIMEOUT"
        ) from exc
