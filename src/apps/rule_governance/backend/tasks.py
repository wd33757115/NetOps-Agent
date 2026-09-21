from src.core.celery_tasks.celery_app import celery

from .access import maintenance
from .rule_control.generation_jobs import TASK, GenerationJobs


@celery.task(name=TASK, bind=True, soft_time_limit=7140, time_limit=7200)
def generate_rules(self, run_id):
    if self.request.id != run_id:
        return {"success": False, "ignored": True}
    return GenerationJobs().run(run_id)


@celery.task(name="netops.rule_governance.recover_generation")
@maintenance
def recover_generation():
    return GenerationJobs().reconcile()
