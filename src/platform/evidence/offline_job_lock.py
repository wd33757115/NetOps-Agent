"""Serialize offline execution and edits without changing the existing job schema."""

from contextlib import contextmanager
from functools import wraps
from hashlib import blake2b
from inspect import signature

from sqlalchemy import text

from src.infrastructure.db.postgres import get_db_session


class OfflineJobBusyError(ValueError):
    pass


@contextmanager
def job_execution_lock(job_id):
    # Held on a dedicated connection, so nested business transactions can commit.
    # PostgreSQL releases the transaction lock when a killed worker disconnects.
    key = int.from_bytes(
        blake2b(f"offline-patrol:{job_id}".encode(), digest_size=8).digest(),
        byteorder="big",
        signed=True,
    )
    with get_db_session() as db:
        if db.get_bind().dialect.name == "postgresql":
            acquired = db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key})
            if not acquired:
                raise OfflineJobBusyError("当前离线任务仍在执行，请等待执行退出后重试")
        yield


def idle_job(fn):
    """Do not edit/restart a cancelled job until its current worker has exited."""
    call_signature = signature(fn)

    @wraps(fn)
    def guarded(*args, **kwargs):
        job_id = call_signature.bind(*args, **kwargs).arguments["job_id"]
        with job_execution_lock(job_id):
            return fn(*args, **kwargs)

    return guarded
