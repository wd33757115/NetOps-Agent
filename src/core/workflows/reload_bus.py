# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 插件跨进程热重载（Redis Pub/Sub）。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Callable

from src.auth.token_store import get_redis
from src.core.logging import get_logger

log = get_logger(__name__)

RELOAD_CHANNEL = "workflow:reload"
_listener_thread: threading.Thread | None = None
_stop_event = threading.Event()


def reload_all_registries(*, source: str = "local") -> dict[str, int]:
    """在当前进程重载 Workflow / Chat Intent / Webhook 注册表。"""
    from src.core.plugins.chat_intent import get_chat_intent_registry
    from src.core.plugins.itsm_webhook import get_itsm_webhook_registry
    from src.core.workflows.registry import load_workflows

    wf_count = len(load_workflows(force=True))
    get_chat_intent_registry().load(force=True)
    intent_count = len(get_chat_intent_registry().all_intents())
    get_itsm_webhook_registry().load(force=True)
    log.info(
        "workflow_registry_reloaded",
        source=source,
        template_count=wf_count,
        chat_intent_count=intent_count,
    )
    return {"templates": wf_count, "intents": intent_count}


def publish_workflow_reload(*, source: str = "api", plugin_name: str | None = None) -> bool:
    """广播重载信号到所有订阅进程（Gateway / Celery Worker）。"""
    client = get_redis()
    if not client:
        log.debug("workflow_reload_redis_unavailable", action="local_reload_only", source=source)
        reload_all_registries(source=source)
        return False
    payload = {
        "source": source,
        "plugin_name": plugin_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        client.publish(RELOAD_CHANNEL, json.dumps(payload, ensure_ascii=False))
        log.info(
            "workflow_reload_published",
            source=source,
            plugin_name=plugin_name,
            channel=RELOAD_CHANNEL,
        )
        return True
    except Exception as exc:
        log.warning(
            "workflow_reload_publish_failed",
            source=source,
            plugin_name=plugin_name,
            error=str(exc),
        )
        reload_all_registries(source=source)
        return False


def broadcast_workflow_reload(*, source: str = "api", plugin_name: str | None = None) -> dict[str, int]:
    """本地重载 + 广播到其他 worker。"""
    stats = reload_all_registries(source=source)
    published = publish_workflow_reload(source=source, plugin_name=plugin_name)
    log.info(
        "workflow_reload_broadcast",
        source=source,
        plugin_name=plugin_name,
        published=published,
        **stats,
    )
    return stats


def _listen_loop(on_reload: Callable[[dict], None] | None = None) -> None:
    client = get_redis()
    if not client:
        log.warning("workflow_reload_listener_skipped", reason="redis_unavailable")
        return
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    try:
        pubsub.subscribe(RELOAD_CHANNEL)
        log.info("workflow_reload_listener_subscribed", channel=RELOAD_CHANNEL)
        while not _stop_event.is_set():
            message = pubsub.get_message(timeout=1.0)
            if not message or message.get("type") != "message":
                continue
            try:
                payload = json.loads(message["data"])
            except Exception:
                payload = {"source": "unknown"}
            log.info(
                "workflow_reload_pubsub_received",
                source=payload.get("source"),
                plugin_name=payload.get("plugin_name"),
            )
            reload_all_registries(source=str(payload.get("source") or "pubsub"))
            if on_reload:
                on_reload(payload)
    except Exception as exc:
        if not _stop_event.is_set():
            log.warning("workflow_reload_listener_failed", error=str(exc), exc_info=exc)
    finally:
        try:
            pubsub.close()
        except Exception:
            pass


def start_reload_listener(*, on_reload: Callable[[dict], None] | None = None) -> None:
    """启动后台线程监听 reload 广播（幂等）。"""
    global _listener_thread
    if _listener_thread and _listener_thread.is_alive():
        log.debug("workflow_reload_listener_already_running")
        return
    _stop_event.clear()

    def _runner() -> None:
        _listen_loop(on_reload)

    _listener_thread = threading.Thread(target=_runner, name="workflow-reload-listener", daemon=True)
    _listener_thread.start()
    log.info("workflow_reload_listener_started")


def stop_reload_listener() -> None:
    """停止 reload 监听线程。"""
    global _listener_thread
    _stop_event.set()
    if _listener_thread and _listener_thread.is_alive():
        _listener_thread.join(timeout=3)
    _listener_thread = None
    log.info("workflow_reload_listener_stopped")
