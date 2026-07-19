# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Celery Beat scheduling-path heartbeat stored in Redis."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from src.auth.token_store import get_redis, invalidate_redis_client
from src.common.config import get_settings

CELERY_BEAT_HEARTBEAT_KEY = "runtime:celery:beat:heartbeat:v1"


class CeleryBeatHeartbeatUnavailableError(RuntimeError):
    """Raised when the scheduler heartbeat store cannot be reached."""


@dataclass(frozen=True)
class CeleryBeatHeartbeatStatus:
    status: Literal["ok", "missing", "stale", "invalid"]
    recorded_at: str | None
    age_seconds: float | None
    worker_id: str | None
    stale_after_seconds: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_celery_beat_heartbeat(
    *,
    worker_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Record successful delivery and execution of the Beat sentinel task."""

    client = get_redis()
    if client is None:
        raise CeleryBeatHeartbeatUnavailableError("Redis is unavailable")

    settings = get_settings()
    recorded_at = (now or _utc_now()).astimezone(timezone.utc)
    payload: dict[str, object] = {
        "schema_version": 1,
        "recorded_at": recorded_at.isoformat(),
        "worker_id": str(worker_id or "unknown"),
    }
    ttl_seconds = max(
        int(settings.CELERY_BEAT_HEARTBEAT_TTL_SECONDS),
        int(settings.CELERY_BEAT_HEARTBEAT_STALE_AFTER_SECONDS) * 2,
    )
    try:
        client.set(
            CELERY_BEAT_HEARTBEAT_KEY,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            ex=ttl_seconds,
        )
    except Exception as exc:
        invalidate_redis_client(exc)
        raise CeleryBeatHeartbeatUnavailableError(str(exc)) from exc
    return payload


def read_celery_beat_heartbeat(
    *,
    now: datetime | None = None,
) -> CeleryBeatHeartbeatStatus:
    """Read and classify the most recent successful scheduler-path heartbeat."""

    client = get_redis()
    if client is None:
        raise CeleryBeatHeartbeatUnavailableError("Redis is unavailable")

    settings = get_settings()
    stale_after = max(1, int(settings.CELERY_BEAT_HEARTBEAT_STALE_AFTER_SECONDS))
    try:
        raw = client.get(CELERY_BEAT_HEARTBEAT_KEY)
    except Exception as exc:
        invalidate_redis_client(exc)
        raise CeleryBeatHeartbeatUnavailableError(str(exc)) from exc

    if raw is None:
        return CeleryBeatHeartbeatStatus(
            status="missing",
            recorded_at=None,
            age_seconds=None,
            worker_id=None,
            stale_after_seconds=stale_after,
        )
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    try:
        payload = json.loads(str(raw))
        recorded_at_raw = str(payload["recorded_at"])
        recorded_at = datetime.fromisoformat(recorded_at_raw.replace("Z", "+00:00"))
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        age_seconds = max(
            0.0,
            ((now or _utc_now()).astimezone(timezone.utc) - recorded_at).total_seconds(),
        )
        worker_id = str(payload.get("worker_id") or "unknown")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return CeleryBeatHeartbeatStatus(
            status="invalid",
            recorded_at=None,
            age_seconds=None,
            worker_id=None,
            stale_after_seconds=stale_after,
        )

    return CeleryBeatHeartbeatStatus(
        status="ok" if age_seconds <= stale_after else "stale",
        recorded_at=recorded_at.astimezone(timezone.utc).isoformat(),
        age_seconds=round(age_seconds, 1),
        worker_id=worker_id,
        stale_after_seconds=stale_after,
    )
