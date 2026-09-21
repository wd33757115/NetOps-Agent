from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from .models import Run


class Submit(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=500)


@contextmanager
def sessions():
    from sqlalchemy.orm import Session

    from src.platform.kernel.migrations import business_engine

    with Session(business_engine()) as session, session.begin():
        yield session


def view(row):
    return {key: getattr(row, key) for key in ["id", "text", "status", "result"]}


def dispatch(run_id):
    from .tasks import execute

    try:
        execute.delay(run_id)
    except Exception:
        # The committed queued row is the durable dispatch record.
        pass


def submit(actor, payload):
    with sessions() as session:
        existing = session.scalar(
            select(Run).where(Run.owner_id == actor.user_id, Run.request_id == payload.request_id)
        )
        if not existing:
            try:
                with session.begin_nested():
                    existing = Run(
                        id=uuid4().hex,
                        owner_id=actor.user_id,
                        request_id=payload.request_id,
                        text=payload.text,
                        status="queued",
                    )
                    session.add(existing)
                    session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(Run).where(
                        Run.owner_id == actor.user_id, Run.request_id == payload.request_id
                    )
                )
        if existing.text != payload.text:
            raise HTTPException(409, "请求标识已用于其他内容")
        result = view(existing)
    if result["status"] == "queued":
        dispatch(result["id"])
    return result


def read(actor, run_id):
    with sessions() as session:
        row = session.get(Run, run_id)
        if not row or row.owner_id != actor.user_id:
            raise HTTPException(404, "记录不存在")
        return view(row)


def execute_run(run_id, authorize):
    now = datetime.now(timezone.utc)
    lease = uuid4().hex
    with sessions() as session:
        row = session.get(Run, run_id)
        if not row or row.status not in {"queued", "running"}:
            return
        owner = row.owner_id
        eligible = or_(Run.status == "queued", (Run.status == "running") & (Run.lease_until < now))
        try:
            authorize(owner, "delivery-probe")
        except HTTPException as exc:
            if exc.status_code != 503:
                session.execute(
                    update(Run).where(Run.id == run_id, eligible).values(status="cancelled")
                )
            return
        claimed = session.execute(
            update(Run)
            .where(Run.id == run_id, eligible)
            .values(status="running", lease_token=lease, lease_until=now + timedelta(seconds=60))
        )
        if claimed.rowcount != 1:
            return
        value = row.text.upper()
    try:
        authorize(owner, "delivery-probe")
    except HTTPException as exc:
        if exc.status_code == 503:
            return  # Lease expires and reconciliation retries.
        with sessions() as session:
            session.execute(
                update(Run)
                .where(Run.id == run_id, Run.lease_token == lease, Run.status == "running")
                .values(status="cancelled")
            )
        return
    with sessions() as session:
        session.execute(
            update(Run)
            .where(Run.id == run_id, Run.lease_token == lease, Run.status == "running")
            .values(status="completed", result=value)
        )


def reconcile():
    with sessions() as session:
        ids = list(
            session.scalars(
                select(Run.id)
                .where(
                    or_(
                        Run.status == "queued",
                        (Run.status == "running") & (Run.lease_until < datetime.now(timezone.utc)),
                    )
                )
                .limit(100)
            )
        )
    for run_id in ids:
        dispatch(run_id)
    return len(ids)


def drain():
    with sessions() as session:
        session.execute(
            update(Run)
            .where(
                or_(
                    Run.status == "queued",
                    (Run.status == "running") & (Run.lease_until < datetime.now(timezone.utc)),
                )
            )
            .values(status="cancelled")
        )


def pending():
    with sessions() as session:
        return session.scalar(
            select(func.count()).select_from(Run).where(Run.status.in_(["queued", "running"]))
        )
