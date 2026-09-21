from celery import shared_task


@shared_task(name="app.delivery_probe.execute")
def execute(run_id):
    from src.platform.authorization.client import authorize_worker

    from . import service

    return service.execute_run(run_id, authorize_worker)


@shared_task(name="app.delivery_probe.reconcile")
def reconcile():
    from . import service

    return service.reconcile()
