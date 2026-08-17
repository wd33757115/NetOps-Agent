"""Runtime observations for governed legacy-path retirement."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any

try:
    from prometheus_client import Counter
except ImportError:  # Observability must not make the data plane unavailable.
    class Counter:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def labels(self, *_args: Any, **_kwargs: Any) -> Counter:
            return self

        def inc(self, _amount: float = 1.0) -> None:
            pass


logger = logging.getLogger(__name__)

LEGACY_PATH_CALL_COUNT = Counter(
    "netops_legacy_path_call_count",
    "Calls or result consumption through a governed legacy path",
    ("candidate_id", "operation", "outcome"),
)

_latest_lock = Lock()
_latest: dict[tuple[str, str, str], dict[str, Any]] = {}


class LegacyPathObservationRepository:
    """Persist and query payload-free legacy path observations."""

    def __init__(self, session_scope: Any | None = None) -> None:
        self._session_scope = session_scope

    def _sessions(self):
        if self._session_scope is not None:
            return self._session_scope()
        from src.infrastructure.db.postgres import get_db_session

        return get_db_session()

    def record(
        self,
        *,
        candidate_id: str,
        operation: str,
        outcome: str,
        observed_at: datetime,
    ) -> str:
        from src.infrastructure.db.models import LegacyPathObservationRecord

        observation_id = str(uuid.uuid4())
        with self._sessions() as db:
            db.add(
                LegacyPathObservationRecord(
                    id=observation_id,
                    candidate_id=candidate_id,
                    operation=operation,
                    outcome=outcome,
                    observed_at=observed_at,
                )
            )
        return observation_id

    def count_window(
        self,
        *,
        candidate_id: str,
        started_at: datetime,
        ended_at: datetime,
    ) -> int:
        from sqlalchemy import func

        from src.infrastructure.db.models import LegacyPathObservationRecord

        with self._sessions() as db:
            return int(
                db.query(func.count(LegacyPathObservationRecord.id))
                .filter(
                    LegacyPathObservationRecord.candidate_id == candidate_id,
                    LegacyPathObservationRecord.observed_at >= started_at,
                    LegacyPathObservationRecord.observed_at < ended_at,
                )
                .scalar()
                or 0
            )


def observe_legacy_path(
    candidate_id: str,
    operation: str,
    *,
    outcome: str = "used",
    persist: bool = True,
    repository: LegacyPathObservationRepository | None = None,
) -> None:
    """Record one legacy-path use without attaching tenant or payload data."""

    normalized_candidate = str(candidate_id).strip()
    normalized_operation = str(operation).strip()
    normalized_outcome = str(outcome).strip()
    if not normalized_candidate or not normalized_operation or not normalized_outcome:
        raise ValueError("legacy path observation labels must be non-empty")

    LEGACY_PATH_CALL_COUNT.labels(
        normalized_candidate,
        normalized_operation,
        normalized_outcome,
    ).inc()
    observed_at = datetime.now(timezone.utc)
    key = (normalized_candidate, normalized_operation, normalized_outcome)
    with _latest_lock:
        previous = _latest.get(key) or {}
        _latest[key] = {
            "candidate_id": normalized_candidate,
            "operation": normalized_operation,
            "outcome": normalized_outcome,
            "process_count": int(previous.get("process_count") or 0) + 1,
            "last_observed_at": observed_at.isoformat(),
        }
    if persist:
        try:
            (repository or LegacyPathObservationRepository()).record(
                candidate_id=normalized_candidate,
                operation=normalized_operation,
                outcome=normalized_outcome,
                observed_at=observed_at,
            )
        except Exception as exc:
            logger.warning(
                "legacy_path_observation_persistence_failed",
                extra={
                    "legacy_candidate_id": normalized_candidate,
                    "legacy_operation": normalized_operation,
                    "legacy_outcome": normalized_outcome,
                    "error_type": type(exc).__name__,
                },
            )
    logger.info(
        "legacy_path_observed",
        extra={
            "legacy_candidate_id": normalized_candidate,
            "legacy_operation": normalized_operation,
            "legacy_outcome": normalized_outcome,
        },
    )


def latest_legacy_path_observation(
    candidate_id: str,
    operation: str,
    outcome: str = "used",
) -> dict[str, Any] | None:
    """Expose process-local evidence for diagnostics and focused tests."""

    key = (str(candidate_id).strip(), str(operation).strip(), str(outcome).strip())
    with _latest_lock:
        value = _latest.get(key)
        return dict(value) if value else None


__all__ = [
    "LEGACY_PATH_CALL_COUNT",
    "LegacyPathObservationRepository",
    "latest_legacy_path_observation",
    "observe_legacy_path",
]
