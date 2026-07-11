# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Consumer 幂等（Redis SET）。"""

from __future__ import annotations

from src.auth.token_store import get_redis
from src.core.events.streams import IDEMPOTENCY_PREFIX, IDEMPOTENCY_TTL_SEC


class IdempotencyStoreUnavailableError(RuntimeError):
    """Raised when consumption safety cannot be established."""


def _key(group: str, event_id: str) -> str:
    return f"{IDEMPOTENCY_PREFIX}{group}:{event_id}"


def is_processed(group: str, event_id: str) -> bool:
    client = get_redis()
    if not client:
        raise IdempotencyStoreUnavailableError("event idempotency store is unavailable")
    try:
        return bool(client.exists(_key(group, event_id)))
    except Exception as exc:
        raise IdempotencyStoreUnavailableError("event idempotency lookup failed") from exc


def mark_processed(group: str, event_id: str) -> None:
    client = get_redis()
    if not client:
        raise IdempotencyStoreUnavailableError("event idempotency store is unavailable")
    try:
        client.set(_key(group, event_id), "1", ex=IDEMPOTENCY_TTL_SEC)
    except Exception as exc:
        raise IdempotencyStoreUnavailableError("event idempotency write failed") from exc
