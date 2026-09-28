from celery import shared_task


@shared_task(name="app.topology.execute", acks_late=True, reject_on_worker_lost=True)
def execute(run_id):
    from src.platform.authorization.client import authorize_worker

    from .service import execute_run

    execute_run(run_id, authorize_worker)


@shared_task(name="app.topology.reconcile")
def reconcile():
    from .service import reconcile as recover

    return recover()
