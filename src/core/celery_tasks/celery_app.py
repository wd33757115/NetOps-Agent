# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.common.config import get_settings
from src.core.logging import configure_logging, get_logger

_settings = get_settings()
configure_logging(log_level=_settings.LOG_LEVEL, log_format=_settings.LOG_FORMAT)
logger = get_logger(__name__)

from celery import Celery
from celery.signals import worker_ready

from src.core.celery_tasks import logging_signals as _logging_signals  # noqa: F401 — 注册信号

settings = _settings


def resolve_worker_pool() -> str:
    """Windows 上 prefork/spawn 会因 billiard 信号量触发 WinError 5，默认 solo。"""
    explicit = (settings.CELERY_WORKER_POOL or os.getenv("CELERY_WORKER_POOL", "")).strip().lower()
    if explicit:
        return explicit
    if sys.platform == "win32":
        return "solo"
    return "prefork"


_worker_pool = resolve_worker_pool()
if sys.platform == "win32" and _worker_pool not in ("solo", "threads"):
    logger.warning(
        "celery_windows_pool_unsafe",
        pool=_worker_pool,
        hint="Windows 请使用 solo（或 threads），否则易出现 PermissionError WinError 5",
    )

celery = Celery(
    "netops_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "src.core.celery_tasks.tasks",
        "src.core.workflows.tasks",
    ],
)

_celery_conf: dict = {
    "task_serializer": "json",
    "accept_content": ["json"],
    "result_serializer": "json",
    "timezone": "Asia/Shanghai",
    "enable_utc": True,
    "task_track_started": True,
    "task_time_limit": settings.CELERY_TASK_TIME_LIMIT,
    "task_soft_time_limit": settings.CELERY_TASK_SOFT_TIME_LIMIT,
    "worker_prefetch_multiplier": 1,
    "default_retry_delay": 60,
    "max_retries": settings.CELERY_MAX_RETRIES,
    "worker_pool": _worker_pool,
    "broker_connection_retry_on_startup": True,
    # Phase 2：按 Skill domain 路由到独立队列（Worker 需监听对应 queue）
    "task_routes": {
        "src.core.celery_tasks.tasks.execute_firewall_policy_task": {"queue": "netops.firewall"},
        "src.core.celery_tasks.tasks.execute_config_backup_task": {"queue": "netops.patrol"},
        "src.core.celery_tasks.tasks.execute_device_patrol_task": {"queue": "netops.patrol"},
        "dispatch_patrol_batch": {"queue": "netops.patrol"},
        "reconcile_patrol_batch_dispatches": {"queue": "netops.patrol"},
        "reconcile_current_authority_dispatches": {"queue": "netops.patrol"},
        "reconcile_patrol_batch_reports": {"queue": "netops.patrol"},
        "record_celery_beat_heartbeat": {"queue": "netops.health"},
        "cleanup_expired_patrol_outputs": {"queue": "netops.patrol"},
        "materialize_patrol_batch_report": {"queue": "netops.patrol"},
        "publish_network_event_outbox": {"queue": "netops.patrol"},
        "reconcile_patrol_runs": {"queue": "netops.patrol"},
        "preview_offline_patrol_job": {"queue": "netops.ai"},
        "netops.inspection.execute_offline": {"queue": "netops.patrol"},
        "netops.inspection.recover_offline": {"queue": "netops.patrol"},
        "netops.inspection.execute_online": {"queue": "netops.patrol"},
        "netops.inspection.recover_online": {"queue": "netops.patrol"},
    },
    "task_default_queue": "netops.default",
}
if _worker_pool == "prefork":
    _celery_conf["worker_max_tasks_per_child"] = 100

celery.conf.update(_celery_conf)
logger.info(
    "celery_app_configured",
    worker_pool=_worker_pool,
    worker_queues=settings.CELERY_WORKER_QUEUES,
)

_beat_heartbeat_interval = max(
    5,
    int(settings.CELERY_BEAT_HEARTBEAT_INTERVAL_SECONDS),
)
_beat_heartbeat_expires = max(
    _beat_heartbeat_interval * 2,
    int(settings.CELERY_BEAT_HEARTBEAT_STALE_AFTER_SECONDS),
)

celery.conf.beat_schedule = {
    "celery-beat-heartbeat": {
        "task": "record_celery_beat_heartbeat",
        "schedule": float(_beat_heartbeat_interval),
        "options": {
            "queue": "netops.health",
            "expires": _beat_heartbeat_expires,
        },
    },
    "archive-skill-executions-monthly": {
        "task": "src.core.celery_tasks.tasks.archive_skill_executions_task",
        "schedule": 30 * 24 * 3600.0,
        "options": {"queue": "netops.default"},
    },
    "publish-network-event-outbox": {
        "task": "publish_network_event_outbox",
        "schedule": 5.0,
        "options": {"queue": "netops.patrol"},
    },
    "reconcile-patrol-runs": {
        "task": "reconcile_patrol_runs",
        "schedule": 60.0,
        "options": {"queue": "netops.patrol"},
    },
    "recover-offline-patrol": {
        "task": "netops.inspection.recover_offline",
        "schedule": 60.0,
        "options": {"queue": "netops.patrol"},
    },
    "recover-online-patrol": {
        "task": "netops.inspection.recover_online",
        "schedule": 60.0,
        "options": {"queue": "netops.patrol"},
    },
    "reconcile-patrol-batch-dispatches": {
        "task": "reconcile_patrol_batch_dispatches",
        "schedule": 15.0,
        "options": {"queue": "netops.patrol"},
    },
    "reconcile-current-authority-dispatches": {
        "task": "reconcile_current_authority_dispatches",
        "schedule": 10.0,
        "options": {"queue": "netops.patrol"},
    },
    "cleanup-expired-patrol-outputs": {
        "task": "cleanup_expired_patrol_outputs",
        "schedule": 3600.0,
        "options": {"queue": "netops.patrol"},
    },
    "reconcile-patrol-batch-reports": {
        "task": "reconcile_patrol_batch_reports",
        "schedule": 30.0,
        "options": {"queue": "netops.patrol"},
    },
}


@worker_ready.connect
def _on_worker_ready(**kwargs):
    """Celery Worker 启动后订阅 Workflow 热重载广播与 EventBus Consumers。"""
    if settings.CELERY_WORKER_ROLE not in {"all", "control"}:
        logger.info(
            "celery_control_consumers_skipped",
            worker_role=settings.CELERY_WORKER_ROLE,
        )
        return
    from src.core.events.worker import start_event_consumers
    from src.core.workflows.reload_bus import start_reload_listener

    start_reload_listener()
    start_event_consumers()
    logger.info("celery_workflow_reload_listener_started")
    logger.info("celery_event_consumers_started")


celery.autodiscover_tasks(["src.core.celery_tasks"])

# Application packages own task modules, routes and periodic schedules.
from src.platform.kernel.registry import task_configuration
_app_modules, _app_routes, _app_schedules = task_configuration()
celery.conf.include = list(celery.conf.include or []) + _app_modules
celery.conf.task_routes = {**celery.conf.task_routes, **_app_routes}
celery.conf.beat_schedule = {
    **{name: entry for name, entry in celery.conf.beat_schedule.items() if entry["task"] not in _app_routes},
    **_app_schedules,
}
from src.platform.kernel.runtime import register_worker_inspection
register_worker_inspection()
celery.conf.beat_scheduler = 'src.platform.kernel.scheduler:AppScheduler'

if __name__ == '__main__':
    celery.start()
