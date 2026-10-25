"""Package-only process profile, also useful for isolated delivery verification."""

from celery import Celery

from src.common.config import get_settings

from .registry import task_configuration
from .runtime import register_worker_inspection

settings = get_settings()
modules, routes, schedules = task_configuration()
app = Celery(
    "netops-packages",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=modules,
)
app.conf.update(
    task_routes=routes,
    beat_schedule=schedules,
    beat_scheduler="src.platform.kernel.scheduler:AppScheduler",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
register_worker_inspection()
