"""Redis-backed lease semaphore for cross-worker patrol concurrency."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Thread

from src.auth.token_store import get_redis, invalidate_redis_client
from src.common.metrics import increment_counter, observe_histogram

logger = logging.getLogger(__name__)


def _record_lease_acquire(
    *,
    scope: str,
    outcome: str,
    started_at: float,
) -> None:
    tags = {"scope": scope, "outcome": outcome}
    try:
        increment_counter("patrol_concurrency_lease_acquire_total", tags=tags)
        observe_histogram(
            "patrol_concurrency_lease_acquire_duration_ms",
            (time.perf_counter() - started_at) * 1000,
            tags=tags,
        )
    except Exception:
        logger.debug("failed to record patrol lease metrics", exc_info=True)


def _record_lease_lost(scope: str) -> None:
    try:
        increment_counter(
            "patrol_concurrency_lease_lost_total",
            tags={"scope": scope},
        )
    except Exception:
        logger.debug("failed to record patrol lease-loss metric", exc_info=True)


class PatrolConcurrencyUnavailableError(RuntimeError):
    """Raised when the global SSH safety limit cannot be enforced."""


class PatrolConcurrencyLeaseLostError(RuntimeError):
    """Raised before more commands run after a distributed lease is lost."""

_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expires = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local token = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZCARD', key) >= limit then
  return 0
end
redis.call('ZADD', key, expires, token)
redis.call('EXPIRE', key, math.max(1, math.ceil((expires - now) / 1000)))
return 1
"""

_RENEW_SCRIPT = """
local key = KEYS[1]
local token = ARGV[1]
local expires = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
if redis.call('ZSCORE', key, token) == false then
  return 0
end
redis.call('ZADD', key, expires, token)
redis.call('EXPIRE', key, math.max(1, ttl))
return 1
"""

_EXCLUSIVE_ACQUIRE_SCRIPT = """
local lease_key = KEYS[1]
local fence_key = KEYS[2]
local token = ARGV[1]
local ttl_ms = tonumber(ARGV[2])
if redis.call('EXISTS', lease_key) == 1 then
  return 0
end
local fencing_token = redis.call('INCR', fence_key)
local acquired = redis.call('SET', lease_key, token, 'PX', ttl_ms, 'NX')
if not acquired then
  return 0
end
return fencing_token
"""

_EXCLUSIVE_RENEW_SCRIPT = """
local lease_key = KEYS[1]
local token = ARGV[1]
local ttl_ms = tonumber(ARGV[2])
if redis.call('GET', lease_key) ~= token then
  return 0
end
return redis.call('PEXPIRE', lease_key, ttl_ms)
"""

_EXCLUSIVE_VERIFY_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return 1
end
return 0
"""

_EXCLUSIVE_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass
class ConcurrencyLease:
    key: str
    token: str | None
    mode: str
    lost: bool = False
    last_error: str | None = None

    @property
    def effective_mode(self) -> str:
        return "degraded_lease_lost" if self.lost else self.mode


class DistributedPatrolSemaphore:
    def __init__(
        self,
        *,
        limit: int,
        lease_seconds: int,
        wait_seconds: int,
        key: str = "netops:patrol:ssh:global",
        enabled: bool = True,
        fail_closed: bool = True,
    ) -> None:
        self.limit = max(1, int(limit))
        self.lease_seconds = max(5, int(lease_seconds))
        self.wait_seconds = max(1, int(wait_seconds))
        self.key = key
        self.enabled = enabled
        self.fail_closed = bool(fail_closed)

    def acquire(self, *, key: str | None = None) -> ConcurrencyLease:
        started_at = time.perf_counter()
        effective_key = str(key or self.key)
        scope = "site" if ":site:" in effective_key else "global"
        if not self.enabled:
            _record_lease_acquire(
                scope=scope,
                outcome="local_only",
                started_at=started_at,
            )
            return ConcurrencyLease(
                effective_key,
                None,
                "local_only",
            )
        client = get_redis()
        if client is None:
            if self.fail_closed:
                _record_lease_acquire(
                    scope=scope,
                    outcome="unavailable",
                    started_at=started_at,
                )
                raise PatrolConcurrencyUnavailableError(
                    "global patrol concurrency control is unavailable"
                )
            _record_lease_acquire(
                scope=scope,
                outcome="degraded",
                started_at=started_at,
            )
            return ConcurrencyLease(
                effective_key,
                None,
                "degraded_local_fallback",
            )
        token = str(uuid.uuid4())
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            now_ms = int(time.time() * 1000)
            expires_ms = now_ms + self.lease_seconds * 1000
            try:
                acquired = client.eval(
                    _ACQUIRE_SCRIPT,
                    1,
                    effective_key,
                    now_ms,
                    expires_ms,
                    self.limit,
                    token,
                )
            except Exception as exc:
                invalidate_redis_client(exc)
                if self.fail_closed:
                    _record_lease_acquire(
                        scope=scope,
                        outcome="error",
                        started_at=started_at,
                    )
                    raise PatrolConcurrencyUnavailableError(
                        "global patrol concurrency control failed"
                    ) from exc
                _record_lease_acquire(
                    scope=scope,
                    outcome="degraded",
                    started_at=started_at,
                )
                return ConcurrencyLease(
                    effective_key,
                    None,
                    "degraded_local_fallback",
                    last_error=str(exc),
                )
            if int(acquired or 0) == 1:
                _record_lease_acquire(
                    scope=scope,
                    outcome="acquired",
                    started_at=started_at,
                )
                return ConcurrencyLease(
                    effective_key,
                    token,
                    "distributed",
                )
            time.sleep(0.1)
        _record_lease_acquire(
            scope=scope,
            outcome="timeout",
            started_at=started_at,
        )
        raise TimeoutError("timed out waiting for distributed patrol concurrency lease")

    def renew(self, lease: ConcurrencyLease) -> bool:
        if not lease.token:
            return True
        client = get_redis()
        if client is None:
            return False
        expires_ms = int(time.time() * 1000) + self.lease_seconds * 1000
        try:
            renewed = client.eval(
                _RENEW_SCRIPT,
                1,
                lease.key,
                lease.token,
                expires_ms,
                self.lease_seconds,
            )
        except Exception as exc:
            invalidate_redis_client(exc)
            lease.last_error = str(exc)
            return False
        return int(renewed or 0) == 1

    @staticmethod
    def assert_lease(lease: ConcurrencyLease) -> None:
        if lease.lost:
            raise PatrolConcurrencyLeaseLostError(
                "global patrol concurrency lease was lost; remaining commands were stopped"
            )

    @contextmanager
    def maintain(self, lease: ConcurrencyLease) -> Iterator[ConcurrencyLease]:
        """Renew a distributed lease until the protected operation completes."""
        if not lease.token:
            try:
                yield lease
            finally:
                self.release(lease)
            return

        stopped = Event()

        def heartbeat() -> None:
            interval = max(1.0, self.lease_seconds / 3)
            while not stopped.wait(interval):
                try:
                    if not self.renew(lease):
                        lease.lost = True
                        _record_lease_lost(
                            "site" if ":site:" in lease.key else "global"
                        )
                        logger.error(
                            "patrol distributed concurrency lease lost key=%s token=%s",
                            lease.key,
                            lease.token,
                        )
                        return
                except Exception:
                    lease.lost = True
                    return

        thread = Thread(target=heartbeat, name="patrol-concurrency-lease", daemon=True)
        thread.start()
        try:
            yield lease
        finally:
            stopped.set()
            thread.join(timeout=1.0)
            self.release(lease)

    @staticmethod
    def release(lease: ConcurrencyLease) -> None:
        if not lease.token:
            return
        client = get_redis()
        if client is not None:
            client.zrem(lease.key, lease.token)


@dataclass
class ExclusiveFencedLease:
    key: str
    fence_key: str
    token: str | None
    fencing_token: int | None
    mode: str
    lost: bool = False
    last_error: str | None = None

    @property
    def effective_mode(self) -> str:
        return "degraded_lease_lost" if self.lost else self.mode


class RedisExclusiveFencedLease:
    """One Redis owner at a time with a monotonic fencing token."""

    def __init__(
        self,
        *,
        lease_seconds: int,
        wait_seconds: int,
        key_prefix: str = "netops:patrol:ssh:device",
        enabled: bool = True,
        fail_closed: bool = True,
    ) -> None:
        self.lease_seconds = max(5, int(lease_seconds))
        self.wait_seconds = max(1, int(wait_seconds))
        self.key_prefix = str(key_prefix).rstrip(":")
        self.enabled = bool(enabled)
        self.fail_closed = bool(fail_closed)

    def _keys(self, scope_id: str) -> tuple[str, str]:
        scope_digest = hashlib.sha256(
            str(scope_id).encode("utf-8")
        ).hexdigest()
        key = f"{self.key_prefix}:{scope_digest}"
        return key, f"{key}:fence"

    def acquire(self, scope_id: str) -> ExclusiveFencedLease:
        started_at = time.perf_counter()
        key, fence_key = self._keys(scope_id)
        if not self.enabled:
            _record_lease_acquire(
                scope="device",
                outcome="local_only",
                started_at=started_at,
            )
            return ExclusiveFencedLease(
                key,
                fence_key,
                None,
                None,
                "local_only",
            )
        client = get_redis()
        if client is None:
            if self.fail_closed:
                _record_lease_acquire(
                    scope="device",
                    outcome="unavailable",
                    started_at=started_at,
                )
                raise PatrolConcurrencyUnavailableError(
                    "device patrol exclusive lease is unavailable"
                )
            _record_lease_acquire(
                scope="device",
                outcome="degraded",
                started_at=started_at,
            )
            return ExclusiveFencedLease(
                key,
                fence_key,
                None,
                None,
                "degraded_local_fallback",
            )
        token = uuid.uuid4().hex
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            try:
                fencing_token = client.eval(
                    _EXCLUSIVE_ACQUIRE_SCRIPT,
                    2,
                    key,
                    fence_key,
                    token,
                    self.lease_seconds * 1000,
                )
            except Exception as exc:
                invalidate_redis_client(exc)
                if self.fail_closed:
                    _record_lease_acquire(
                        scope="device",
                        outcome="error",
                        started_at=started_at,
                    )
                    raise PatrolConcurrencyUnavailableError(
                        "device patrol exclusive lease failed"
                    ) from exc
                _record_lease_acquire(
                    scope="device",
                    outcome="degraded",
                    started_at=started_at,
                )
                return ExclusiveFencedLease(
                    key,
                    fence_key,
                    None,
                    None,
                    "degraded_local_fallback",
                    last_error=str(exc),
                )
            if int(fencing_token or 0) > 0:
                _record_lease_acquire(
                    scope="device",
                    outcome="acquired",
                    started_at=started_at,
                )
                return ExclusiveFencedLease(
                    key,
                    fence_key,
                    token,
                    int(fencing_token),
                    "distributed",
                )
            time.sleep(0.1)
        _record_lease_acquire(
            scope="device",
            outcome="timeout",
            started_at=started_at,
        )
        raise TimeoutError(
            "timed out waiting for device patrol exclusive lease"
        )

    def renew(self, lease: ExclusiveFencedLease) -> bool:
        if not lease.token:
            return True
        client = get_redis()
        if client is None:
            return False
        try:
            renewed = client.eval(
                _EXCLUSIVE_RENEW_SCRIPT,
                1,
                lease.key,
                lease.token,
                self.lease_seconds * 1000,
            )
        except Exception as exc:
            invalidate_redis_client(exc)
            lease.last_error = str(exc)
            return False
        return int(renewed or 0) == 1

    def assert_lease(self, lease: ExclusiveFencedLease) -> None:
        if lease.lost:
            raise PatrolConcurrencyLeaseLostError(
                "device patrol exclusive lease was lost"
            )
        if not lease.token:
            return
        client = get_redis()
        if client is None:
            lease.lost = True
            _record_lease_lost("device")
            raise PatrolConcurrencyLeaseLostError(
                "device patrol exclusive lease cannot be verified"
            )
        try:
            owned = client.eval(
                _EXCLUSIVE_VERIFY_SCRIPT,
                1,
                lease.key,
                lease.token,
            )
        except Exception as exc:
            invalidate_redis_client(exc)
            lease.lost = True
            lease.last_error = str(exc)
            _record_lease_lost("device")
            raise PatrolConcurrencyLeaseLostError(
                "device patrol exclusive lease verification failed"
            ) from exc
        if int(owned or 0) != 1:
            lease.lost = True
            _record_lease_lost("device")
            raise PatrolConcurrencyLeaseLostError(
                "device patrol worker was fenced by a newer owner"
            )

    @contextmanager
    def maintain(
        self,
        lease: ExclusiveFencedLease,
    ) -> Iterator[ExclusiveFencedLease]:
        if not lease.token:
            try:
                yield lease
            finally:
                self.release(lease)
            return

        stopped = Event()

        def heartbeat() -> None:
            interval = max(1.0, self.lease_seconds / 3)
            while not stopped.wait(interval):
                if not self.renew(lease):
                    lease.lost = True
                    _record_lease_lost("device")
                    logger.error(
                        "patrol device lease lost key=%s fence=%s",
                        lease.key,
                        lease.fencing_token,
                    )
                    return

        thread = Thread(
            target=heartbeat,
            name="patrol-device-lease",
            daemon=True,
        )
        thread.start()
        try:
            yield lease
        finally:
            stopped.set()
            thread.join(timeout=1.0)
            self.release(lease)

    @staticmethod
    def release(lease: ExclusiveFencedLease) -> None:
        if not lease.token:
            return
        client = get_redis()
        if client is None:
            return
        try:
            client.eval(
                _EXCLUSIVE_RELEASE_SCRIPT,
                1,
                lease.key,
                lease.token,
            )
        except Exception as exc:
            invalidate_redis_client(exc)
            lease.last_error = str(exc)


@dataclass
class HierarchicalConcurrencyLease:
    global_lease: ConcurrencyLease
    site_lease: ConcurrencyLease
    device_lease: ExclusiveFencedLease

    @property
    def fencing_token(self) -> int | None:
        return self.device_lease.fencing_token

    @property
    def lost(self) -> bool:
        return bool(
            self.global_lease.lost
            or self.site_lease.lost
            or self.device_lease.lost
        )

    @property
    def effective_mode(self) -> str:
        modes = {
            self.global_lease.effective_mode,
            self.site_lease.effective_mode,
            self.device_lease.effective_mode,
        }
        if modes == {"distributed"}:
            return "distributed_hierarchical"
        if "degraded_lease_lost" in modes:
            return "degraded_lease_lost"
        if "degraded_local_fallback" in modes:
            return "degraded_local_fallback"
        return "local_only"


class HierarchicalPatrolLeaseManager:
    """Global capacity, site capacity, then device-exclusive fencing."""

    def __init__(
        self,
        *,
        global_semaphore: DistributedPatrolSemaphore,
        site_limit: int,
        lease_seconds: int,
        wait_seconds: int,
        enabled: bool = True,
        fail_closed: bool = True,
    ) -> None:
        self.global_semaphore = global_semaphore
        self.site_semaphore = DistributedPatrolSemaphore(
            limit=site_limit,
            lease_seconds=lease_seconds,
            wait_seconds=wait_seconds,
            key="netops:patrol:ssh:site",
            enabled=enabled,
            fail_closed=fail_closed,
        )
        self.device_leases = RedisExclusiveFencedLease(
            lease_seconds=lease_seconds,
            wait_seconds=wait_seconds,
            enabled=enabled,
            fail_closed=fail_closed,
        )

    @staticmethod
    def _scope_key(prefix: str, value: str) -> str:
        digest = hashlib.sha256(
            value.encode("utf-8")
        ).hexdigest()
        return f"{prefix}:{digest}"

    def acquire(
        self,
        *,
        device_id: str,
        site: str | None,
    ) -> HierarchicalConcurrencyLease:
        global_lease = self.global_semaphore.acquire()
        try:
            site_key = self._scope_key(
                "netops:patrol:ssh:site",
                str(site or "__unassigned__"),
            )
            site_lease = self.site_semaphore.acquire(
                key=site_key
            )
        except Exception:
            self.global_semaphore.release(global_lease)
            raise
        try:
            device_lease = self.device_leases.acquire(device_id)
        except Exception:
            self.site_semaphore.release(site_lease)
            self.global_semaphore.release(global_lease)
            raise
        return HierarchicalConcurrencyLease(
            global_lease=global_lease,
            site_lease=site_lease,
            device_lease=device_lease,
        )

    def assert_lease(
        self,
        lease: HierarchicalConcurrencyLease,
    ) -> None:
        self.global_semaphore.assert_lease(lease.global_lease)
        self.site_semaphore.assert_lease(lease.site_lease)
        self.device_leases.assert_lease(lease.device_lease)

    @contextmanager
    def maintain(
        self,
        lease: HierarchicalConcurrencyLease,
    ) -> Iterator[HierarchicalConcurrencyLease]:
        with self.global_semaphore.maintain(
            lease.global_lease
        ):
            with self.site_semaphore.maintain(
                lease.site_lease
            ):
                with self.device_leases.maintain(
                    lease.device_lease
                ):
                    yield lease
