"""What every part of the ledger shares: the session, who may write, and the change log.

Reading needs the App grant; writing also needs an admin or operator account, because the
ledger is the source other features trust. Every write records who changed what.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException

from .models import Change


@contextmanager
def sessions():
    from sqlalchemy.orm import Session

    from src.platform.kernel.migrations import business_engine

    with Session(business_engine()) as session, session.begin():
        yield session


def now():
    return datetime.now(timezone.utc)


def new_id():
    return uuid4().hex


def can_edit(actor):
    return actor.role in {"admin", "operator"}


def require_editor(actor):
    if not can_edit(actor):
        raise HTTPException(403, "只读账号不能修改台账")


def bad(message):
    return HTTPException(422, message)


def missing(what):
    return HTTPException(404, f"{what}不存在或已被删除")


def percent(used, total):
    return round(used * 100 / total) if total else 0


def record(session, actor, entity, entity_id, label, action, detail=None):
    session.add(
        Change(
            id=new_id(),
            at=now(),
            actor_id=actor.user_id,
            actor_name=actor.username,
            entity=entity,
            entity_id=entity_id,
            label=label,
            action=action,
            detail=detail or None,
        )
    )


def diff(row, values, names):
    """Field changes as {field, before, after}; also applies them to the row."""
    changes = []
    for key, value in values.items():
        before = getattr(row, key)
        if before != value:
            changes.append({"field": names.get(key, key), "before": before, "after": value})
            setattr(row, key, value)
    return changes
