from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy.orm import Session


def now():
    return datetime.now(timezone.utc)


@contextmanager
def sessions():
    from src.platform.kernel.migrations import business_engine

    with Session(business_engine()) as session, session.begin():
        yield session
