"""Clock helpers for legacy parser timestamps stored without time zones."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select


def database_clocks(db) -> tuple[datetime, datetime]:
    """Return UTC and database-local clocks for mixed timestamp columns."""

    now_utc = datetime.now(timezone.utc)
    now_local = db.execute(select(func.localtimestamp())).scalar_one()
    if now_local.tzinfo is not None:
        now_local = now_local.replace(tzinfo=None)
    return now_utc, now_local


def elapsed_seconds(
    value: datetime | None,
    *,
    now_utc: datetime,
    now_local: datetime,
) -> float:
    """Calculate elapsed time using the storage semantics of ``value``."""

    if value is None:
        return 0.0
    if value.tzinfo is None:
        return (now_local - value).total_seconds()
    return (now_utc - value.astimezone(timezone.utc)).total_seconds()
