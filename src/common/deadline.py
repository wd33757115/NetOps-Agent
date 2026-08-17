"""Small wall-clock deadline helper for blocking provider SDK calls."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class WallClockTimeoutError(TimeoutError):
    """Raised when a blocking call exceeds the caller's total wall-clock budget."""


def call_with_wall_clock_timeout(
    function: Callable[[], T],
    *,
    timeout_seconds: int | float,
    operation: str,
) -> T:
    """Run a blocking SDK call with a process-safe wall-clock boundary.

    Provider clients usually expose connect/read/write timeouts, not a total
    request deadline. The worker executes Skills in a subprocess, so a timed-out
    daemon thread cannot keep that subprocess alive while the durable Job is
    returned to a retryable state.
    """

    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            result_queue.put((True, function()), block=False)
        except BaseException as exc:  # preserve provider exception types
            result_queue.put((False, exc), block=False)

    threading.Thread(
        target=invoke,
        name=f"deadline-{operation}",
        daemon=True,
    ).start()
    try:
        succeeded, value = result_queue.get(timeout=timeout)
    except queue.Empty as exc:
        raise WallClockTimeoutError(
            f"{operation} exceeded {timeout:g} seconds"
        ) from exc
    if succeeded:
        return value  # type: ignore[return-value]
    if isinstance(value, BaseException):
        raise value
    raise RuntimeError(f"{operation} failed without an exception")
