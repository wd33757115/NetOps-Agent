from src.core.celery_tasks.celery_app import celery


@celery.task(name="execute_document_operation")
def execute_document_operation(operation_id):
    from .operations import execute
    execute(operation_id)


@celery.task(name="reconcile_document_operations")
def reconcile_document_operations():
    from .operations import reconcile
    return reconcile()
