from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.core.celery_tasks import beat_health
from src.gateway import diagnostics


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expirations[key] = ex

    def get(self, key: str) -> str | None:
        return self.values.get(key)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        CELERY_BEAT_HEARTBEAT_STALE_AFTER_SECONDS=45,
        CELERY_BEAT_HEARTBEAT_TTL_SECONDS=180,
    )


def test_heartbeat_records_and_classifies_fresh_and_stale(monkeypatch):
    redis = FakeRedis()
    now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(beat_health, "get_redis", lambda: redis)
    monkeypatch.setattr(beat_health, "get_settings", _settings)

    payload = beat_health.record_celery_beat_heartbeat(
        worker_id="control@node-1",
        now=now,
    )

    assert payload["worker_id"] == "control@node-1"
    assert redis.expirations[beat_health.CELERY_BEAT_HEARTBEAT_KEY] == 180

    fresh = beat_health.read_celery_beat_heartbeat(now=now + timedelta(seconds=10))
    stale = beat_health.read_celery_beat_heartbeat(now=now + timedelta(seconds=46))

    assert fresh.status == "ok"
    assert fresh.age_seconds == 10.0
    assert fresh.worker_id == "control@node-1"
    assert stale.status == "stale"
    assert stale.age_seconds == 46.0


def test_heartbeat_reports_missing_invalid_and_unavailable(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(beat_health, "get_redis", lambda: redis)
    monkeypatch.setattr(beat_health, "get_settings", _settings)

    assert beat_health.read_celery_beat_heartbeat().status == "missing"

    redis.values[beat_health.CELERY_BEAT_HEARTBEAT_KEY] = "not-json"
    assert beat_health.read_celery_beat_heartbeat().status == "invalid"

    monkeypatch.setattr(beat_health, "get_redis", lambda: None)
    with pytest.raises(beat_health.CeleryBeatHeartbeatUnavailableError):
        beat_health.record_celery_beat_heartbeat()
    with pytest.raises(beat_health.CeleryBeatHeartbeatUnavailableError):
        beat_health.read_celery_beat_heartbeat()


def test_diagnostics_marks_fresh_heartbeat_ok(monkeypatch):
    monkeypatch.setattr(
        beat_health,
        "read_celery_beat_heartbeat",
        lambda: beat_health.CeleryBeatHeartbeatStatus(
            status="ok",
            recorded_at="2026-07-19T12:00:00+00:00",
            age_seconds=2.0,
            worker_id="control@node-1",
            stale_after_seconds=45,
        ),
    )

    result = diagnostics._check_celery_beat()

    assert result.id == "celery_beat"
    assert result.status == "ok"
    assert result.detail["worker_id"] == "control@node-1"


def test_diagnostics_marks_missing_heartbeat_down(monkeypatch):
    monkeypatch.setattr(
        beat_health,
        "read_celery_beat_heartbeat",
        lambda: beat_health.CeleryBeatHeartbeatStatus(
            status="missing",
            recorded_at=None,
            age_seconds=None,
            worker_id=None,
            stale_after_seconds=45,
        ),
    )

    result = diagnostics._check_celery_beat()

    assert result.status == "down"
    assert "Beat" in result.message
