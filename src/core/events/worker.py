# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""EventBus 后台 Consumer 轮询线程。"""

from __future__ import annotations

import threading
import time

from src.common.config import get_settings
from src.core.events.consumers import ALL_CONSUMERS
from src.core.logging import get_logger

logger = get_logger(__name__)

_listener_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _poll_loop() -> None:
    settings = get_settings()
    for consumer in ALL_CONSUMERS:
        try:
            consumer.setup()
        except Exception as exc:
            logger.warning("event_consumer_setup_failed consumer=%s error=%s", consumer.name, exc)

    logger.info("event_consumers_started", consumer_count=len(ALL_CONSUMERS))
    while not _stop_event.is_set():
        total = 0
        for consumer in ALL_CONSUMERS:
            try:
                total += consumer.poll_once()
            except Exception as exc:
                logger.warning("event_consumer_poll_failed consumer=%s error=%s", consumer.name, exc)
        if total == 0:
            time.sleep(settings.EVENT_BUS_POLL_IDLE_SEC)


def start_event_consumers() -> None:
    """启动后台 Consumer 线程（Gateway / Celery Worker 均可调用）。"""
    global _listener_thread
    if not get_settings().EVENT_BUS_ENABLED:
        logger.info("event_consumers_disabled")
        return
    if _listener_thread and _listener_thread.is_alive():
        return
    _stop_event.clear()

    def _runner() -> None:
        _poll_loop()

    _listener_thread = threading.Thread(target=_runner, name="event-bus-consumers", daemon=True)
    _listener_thread.start()


def stop_event_consumers() -> None:
    global _listener_thread
    _stop_event.set()
    if _listener_thread and _listener_thread.is_alive():
        _listener_thread.join(timeout=5)
    _listener_thread = None
    logger.info("event_consumers_stopped")


def poll_event_consumers_once() -> int:
    """同步拉取一轮（供测试 / 管理命令）。"""
    total = 0
    for consumer in ALL_CONSUMERS:
        consumer.setup()
        total += consumer.poll_once()
    return total
