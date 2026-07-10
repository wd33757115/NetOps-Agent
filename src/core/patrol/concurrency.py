"""Redis-backed lease semaphore for cross-worker patrol concurrency."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Thread

from src.auth.token_store import get_redis

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


@dataclass(frozen=True)
class ConcurrencyLease:
    key: str
    token: str | None
    mode: str


class DistributedPatrolSemaphore:
    def __init__(
        self,
        *,
        limit: int,
        lease_seconds: int,
        wait_seconds: int,
        key: str = "netops:patrol:ssh:global",
        enabled: bool = True,
    ) -> None:
        self.limit = max(1, int(limit))
        self.lease_seconds = max(5, int(lease_seconds))
        self.wait_seconds = max(1, int(wait_seconds))
        self.key = key
        self.enabled = enabled

    def acquire(self) -> ConcurrencyLease:
        if not self.enabled:
            return ConcurrencyLease(self.key, None, "local_only")
        client = get_redis()
        if client is None:
            return ConcurrencyLease(self.key, None, "degraded_local_fallback")
        token = str(uuid.uuid4())
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            now_ms = int(time.time() * 1000)
            expires_ms = now_ms + self.lease_seconds * 1000
            acquired = client.eval(
                _ACQUIRE_SCRIPT,
                1,
                self.key,
                now_ms,
                expires_ms,
                self.limit,
                token,
            )
            if int(acquired or 0) == 1:
                return ConcurrencyLease(self.key, token, "distributed")
            time.sleep(0.1)
        raise TimeoutError("timed out waiting for distributed patrol concurrency lease")

    def renew(self, lease: ConcurrencyLease) -> bool:
        if not lease.token:
            return True
        client = get_redis()
        if client is None:
            return False
        expires_ms = int(time.time() * 1000) + self.lease_seconds * 1000
        renewed = client.eval(
            _RENEW_SCRIPT,
            1,
            lease.key,
            lease.token,
            expires_ms,
            self.lease_seconds,
        )
        return int(renewed or 0) == 1

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
                        return
                except Exception:
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
