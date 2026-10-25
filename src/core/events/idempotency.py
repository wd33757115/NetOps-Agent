# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Consumer 幂等（Redis SET）。"""

from __future__ import annotations

from src.auth.token_store import get_redis
from src.core.events.streams import (
    IDEMPOTENCY_PREFIX,
    IDEMPOTENCY_PROCESSING_PREFIX,
    IDEMPOTENCY_TTL_SEC,
)


class IdempotencyStoreUnavailableError(RuntimeError):
    """Raised when consumption safety cannot be established."""


def _key(group: str, event_id: str) -> str:
    return f"{IDEMPOTENCY_PREFIX}{group}:{event_id}"


def _processing_key(group: str, event_id: str) -> str:
    return f"{IDEMPOTENCY_PROCESSING_PREFIX}{group}:{event_id}"


def is_processed(group: str, event_id: str) -> bool:
    client = get_redis()
    if not client:
        raise IdempotencyStoreUnavailableError("event idempotency store is unavailable")
    try:
        return bool(client.exists(_key(group, event_id)))
    except Exception as exc:
        raise IdempotencyStoreUnavailableError("event idempotency lookup failed") from exc


def claim_processing(
    group: str,
    event_id: str,
    *,
    owner_token: str,
    lease_ms: int,
) -> str:
    client = get_redis()
    if not client:
        raise IdempotencyStoreUnavailableError("event idempotency store is unavailable")
    try:
        state = client.eval(
            """
            if redis.call('EXISTS', KEYS[2]) == 1 then
                return 2
            end
            if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'PX', ARGV[2]) then
                return 1
            end
            return 0
            """,
            2,
            _processing_key(group, event_id),
            _key(group, event_id),
            owner_token,
            str(max(1_000, int(lease_ms))),
        )
        return {0: "busy", 1: "claimed", 2: "completed"}.get(
            int(state), "busy"
        )
    except Exception as exc:
        raise IdempotencyStoreUnavailableError("event processing claim failed") from exc


def mark_processed(group: str, event_id: str, *, owner_token: str) -> None:
    client = get_redis()
    if not client:
        raise IdempotencyStoreUnavailableError("event idempotency store is unavailable")
    script = """
    if redis.call('GET', KEYS[1]) ~= ARGV[1] then
        return 0
    end
    redis.call('SET', KEYS[2], '1', 'EX', ARGV[2])
    redis.call('DEL', KEYS[1])
    return 1
    """
    try:
        completed = client.eval(
            script,
            2,
            _processing_key(group, event_id),
            _key(group, event_id),
            owner_token,
            str(IDEMPOTENCY_TTL_SEC),
        )
        if int(completed or 0) != 1:
            raise IdempotencyStoreUnavailableError(
                "event processing ownership was lost before completion"
            )
    except IdempotencyStoreUnavailableError:
        raise
    except Exception as exc:
        raise IdempotencyStoreUnavailableError("event idempotency write failed") from exc


def release_processing(group: str, event_id: str, *, owner_token: str) -> None:
    client = get_redis()
    if not client:
        return
    script = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    end
    return 0
    """
    try:
        client.eval(
            script,
            1,
            _processing_key(group, event_id),
            owner_token,
        )
    except Exception:
        return


def renew_processing(
    group: str, event_id: str, *, owner_token: str, lease_ms: int
) -> bool:
    client = get_redis()
    if not client:
        raise IdempotencyStoreUnavailableError("event idempotency store is unavailable")
    try:
        renewed = client.eval(
            """
            if redis.call('GET', KEYS[1]) ~= ARGV[1] then
                return 0
            end
            redis.call('PEXPIRE', KEYS[1], ARGV[2])
            return 1
            """,
            1,
            _processing_key(group, event_id),
            owner_token,
            str(max(1_000, int(lease_ms))),
        )
        return int(renewed or 0) == 1
    except Exception as exc:
        raise IdempotencyStoreUnavailableError("event processing lease renewal failed") from exc


def record_processing_failure(group: str, event_id: str) -> int:
    client = get_redis()
    if not client:
        raise IdempotencyStoreUnavailableError("event idempotency store is unavailable")
    key = f"{IDEMPOTENCY_PROCESSING_PREFIX}failures:{group}:{event_id}"
    try:
        value = client.incr(key)
        client.expire(key, IDEMPOTENCY_TTL_SEC)
        return int(value)
    except Exception as exc:
        raise IdempotencyStoreUnavailableError("event failure counter update failed") from exc


def clear_processing_failures(group: str, event_id: str) -> None:
    client = get_redis()
    if not client:
        return
    try:
        client.delete(f"{IDEMPOTENCY_PROCESSING_PREFIX}failures:{group}:{event_id}")
    except Exception:
        return
