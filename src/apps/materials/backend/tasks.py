from celery import shared_task


@shared_task(name="app.materials.import_docset", acks_late=True, reject_on_worker_lost=True)
def import_docset(job_id):
    from src.platform.authorization.client import authorize_worker

    from .docsets import run_import

    run_import(job_id, authorize_worker)


@shared_task(name="app.materials.publish_docset", acks_late=True, reject_on_worker_lost=True)
def publish_docset(job_id):
    from src.platform.authorization.client import authorize_worker

    from .docsets import run_publish

    run_publish(job_id, authorize_worker)


@shared_task(name="app.materials.reconcile_imports")
def reconcile_imports():
    from .docsets import reconcile

    return reconcile()


@shared_task(name="app.materials.materialize_docset", acks_late=True, reject_on_worker_lost=True)
def materialize_docset(document_id, revision):
    from .docset_cache import materialize

    materialize(document_id, revision)
