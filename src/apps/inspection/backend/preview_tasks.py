"""Celery tasks for durable offline patrol preview work."""

from src.apps.inspection.backend.offline_tasks import (
    execute_offline as execute_offline,
)
from src.apps.inspection.backend.offline_tasks import (
    recover_offline as recover_offline,
)
from .access import execution_lock
from src.core.celery_tasks.celery_app import celery
from src.core.patrol.offline_job_lock import job_execution_lock
from src.core.patrol.offline_jobs import OfflinePatrolJobService


@celery.task(name="preview_offline_patrol_job", bind=True, max_retries=0)
def preview_offline_patrol_job(self, job_id: str):
    with execution_lock(), job_execution_lock(job_id):
        return OfflinePatrolJobService().run_preview(job_id, patrol_task_id=self.request.id)
