"""The worker side of trying fields on an adapter: one model-backed trial per task."""

from src.core.celery_tasks.celery_app import celery

from .access import authorize, maintenance

TASK = "netops.rule_governance.adapter_fields_trial"


# A trial is at most three model calls; the limits only bound a call that never returns.
@celery.task(name=TASK, bind=True, soft_time_limit=840, time_limit=900)
@maintenance
def adapter_fields_trial(self, key, token):
    from .check_items.adapter_field_service import AdapterFieldService

    AdapterFieldService().run(key, token, guard=authorize)
    return {"success": True}


def dispatch(key, token):
    try:
        adapter_fields_trial.apply_async(args=[key, token], queue="app.rule-governance")
    except Exception:
        return False
    return True
