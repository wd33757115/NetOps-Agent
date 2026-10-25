from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy.orm import Session


@contextmanager
def sessions():
    from src.platform.kernel.migrations import business_engine

    with Session(business_engine()) as session, session.begin():
        yield session


def owned(session, model, identity, owner):
    row = session.get(model, identity)
    if not row or row.owner_id != str(owner):
        raise HTTPException(404, "记录不存在")
    return row
