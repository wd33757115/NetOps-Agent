"""Celery tasks for durable offline patrol preview work."""

from src.core.celery_tasks.celery_app import celery
from src.core.patrol.offline_jobs import OfflinePatrolJobService


@celery.task(name="preview_offline_patrol_job", max_retries=0)
def preview_offline_patrol_job(job_id: str):
    return OfflinePatrolJobService().run_preview(job_id)
