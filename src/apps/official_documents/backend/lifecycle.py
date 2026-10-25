"""Close durable queued work; retain running leases and historical results."""

from sqlalchemy import func, select, update, or_
from .models import OfficialDocumentOperation as Operation, now
from contextlib import contextmanager


@contextmanager
def db_session():
    from sqlalchemy.orm import Session
    from src.platform.kernel.migrations import business_engine

    with Session(business_engine()) as session, session.begin():
        yield session


def drain():
    with db_session() as session:
        session.execute(
            update(Operation)
            .where(
                or_(
                    Operation.status == "queued",
                    (Operation.status == "running") & (Operation.lease_expires_at < now()),
                )
            )
            .values(status="cancelled", error_code="应用已停用")
        )


def pending():
    with db_session() as session:
        return session.scalar(
            select(func.count())
            .select_from(Operation)
            .where(Operation.status.in_(["queued", "running"]))
        )
