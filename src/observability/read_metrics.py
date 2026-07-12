# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Low-overhead read-path latency, query-count and result-size metrics."""

from __future__ import annotations

import inspect
import time
from contextvars import ContextVar
from functools import wraps
from threading import Lock
from typing import Any, Callable

from sqlalchemy import event

from src.infrastructure.db.postgres import engine

try:
    from prometheus_client import Histogram
except ImportError:  # Core data plane must run without optional metrics exporters.
    class Histogram:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def labels(self, *_args: Any, **_kwargs: Any) -> "Histogram":
            return self

        def observe(self, _value: float | int) -> None:
            pass


READ_DURATION_SECONDS = Histogram(
    "netops_read_duration_seconds",
    "Read operation latency",
    ("operation", "variant"),
)
READ_QUERY_COUNT = Histogram(
    "netops_read_query_count",
    "SQL statements executed by a read operation",
    ("operation", "variant"),
    buckets=(0, 1, 2, 5, 10, 20, 50, 100, 250, 500, 1000),
)
READ_RESULT_COUNT = Histogram(
    "netops_read_result_count",
    "Rows returned by a read operation",
    ("operation", "variant"),
    buckets=(0, 1, 5, 10, 20, 50, 100, 200, 500, 1000),
)

_query_count: ContextVar[int | None] = ContextVar("read_query_count", default=None)
_latest_lock = Lock()
_latest: dict[tuple[str, str], dict[str, float | int | str]] = {}


@event.listens_for(engine, "before_cursor_execute")
def _count_query(*_args: Any, **_kwargs: Any) -> None:
    current = _query_count.get()
    if current is not None:
        _query_count.set(current + 1)


def observe_read(
    operation: str,
    *,
    variant: Callable[[dict[str, Any]], str] | None = None,
    result_count: Callable[[Any], int] | None = None,
):
    """Decorate a synchronous read use case without changing its signature."""

    def decorate(func):
        signature = inspect.signature(func)

        @wraps(func)
        def wrapped(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            call_args = dict(bound.arguments)
            variant_name = variant(call_args) if variant else "default"
            token = _query_count.set(0)
            started = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - started
                queries = int(_query_count.get() or 0)
                _query_count.reset(token)
            rows = result_count(result) if result_count else 0
            READ_DURATION_SECONDS.labels(operation, variant_name).observe(elapsed)
            READ_QUERY_COUNT.labels(operation, variant_name).observe(queries)
            READ_RESULT_COUNT.labels(operation, variant_name).observe(rows)
            with _latest_lock:
                _latest[(operation, variant_name)] = {
                    "operation": operation,
                    "variant": variant_name,
                    "duration_seconds": elapsed,
                    "query_count": queries,
                    "result_count": rows,
                }
            return result

        return wrapped

    return decorate


def latest_read_observation(operation: str, variant: str) -> dict[str, Any] | None:
    with _latest_lock:
        value = _latest.get((operation, variant))
        return dict(value) if value else None
