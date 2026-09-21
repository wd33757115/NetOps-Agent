from celery import shared_task


def authorize_worker(*args, **kwargs):
    from src.platform.authorization.client import authorize_worker as check

    return check(*args, **kwargs)


@shared_task(name="execute_document_operation")
def execute_document_operation(operation_id):
    from fastapi import HTTPException
    from .operations import execute, db_session
    from .models import OfficialDocumentOperation, now
    from sqlalchemy import update, or_

    with db_session() as session:
        op = session.get(OfficialDocumentOperation, operation_id)
        if not op or op.status not in ("queued", "running"):
            return
        owner = op.owner_id
    try:
        authorize_worker(owner, "official-documents")
    except HTTPException as exc:
        if exc.status_code == 503:
            # Keep the durable queued/leased row for later reconciliation.
            return
        with db_session() as session:
            op = OfficialDocumentOperation
            session.execute(
                update(op)
                .where(
                    op.id == operation_id,
                    or_(
                        op.status == "queued",
                        (op.status == "running") & (op.lease_expires_at < now()),
                    ),
                )
                .values(status="failed", error_code="应用已停用或使用权限已撤销")
            )
        return
    execute(operation_id, authorize=lambda: authorize_worker(owner, "official-documents"))


@shared_task(name="reconcile_document_operations")
def reconcile_document_operations():
    from .operations import reconcile

    return reconcile()
