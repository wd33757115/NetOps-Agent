"""Package revision tasks; old seven-argument messages are adopted into the ledger."""

from __future__ import annotations

from src.apps.rule_governance.backend.access import maintenance

from src.apps.rule_governance.backend.rule_control.engineering_revision_jobs import (
    HARD_LIMIT,
    SOFT_LIMIT,
    TASK_NAME,
    EngineeringRevisionJobs,
)
from src.core.celery_tasks.celery_app import celery


@celery.task(
    name=TASK_NAME, bind=True, queue="app.rule-governance", soft_time_limit=SOFT_LIMIT, time_limit=HARD_LIMIT
)
def execute_engineering_revision(self, task_id: str):
    if str(self.request.id or "") != task_id:
        return {"success": False, "error": "revision delivery identity mismatch"}
    return EngineeringRevisionJobs().run(task_id)


@celery.task(
    name="validate_rule_asset_engineering_revision",
    bind=True,
    queue="app.rule-governance",
    soft_time_limit=SOFT_LIMIT,
    time_limit=HARD_LIMIT,
)
def validate_rule_asset_engineering_revision_task(
    self,
    bundle_id,
    contract,
    change_policies,
    event_rules,
    actor,
    reason,
    idempotency_key,
    actor_user_id=None,
):
    delivery_id = str(self.request.id or "")
    if not delivery_id:
        return {"success": False, "error": "revision delivery identity missing"}
    jobs = EngineeringRevisionJobs()
    jobs.submit(
        bundle_id,
        contract=contract,
        change_policies=change_policies,
        event_rules=event_rules,
        actor=actor,
        actor_user_id=actor_user_id,
        reason=reason,
        idempotency_key=idempotency_key,
        legacy_task_id=delivery_id,
        dispatch=False,
    )
    return jobs.run(delivery_id)


@celery.task(name="recover_engineering_revisions", queue="app.rule-governance")
@maintenance
def recover_engineering_revisions():
    return EngineeringRevisionJobs().reconcile()


def enqueue_engineering_revision(bundle_id: str, **request):
    return EngineeringRevisionJobs().submit(bundle_id, **request)
