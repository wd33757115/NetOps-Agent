"""Redis pub/sub helpers for patrol batch live events (SSE)."""

from __future__ import annotations

import json
from typing import Any, Iterator

from src.common.config import get_settings

CHANNEL = "netops.patrol.batch.events"


def publish_patrol_batch_event(event: dict[str, Any]) -> bool:
    try:
        import redis

        client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
        client.publish(CHANNEL, json.dumps(event, ensure_ascii=False, default=str))
        return True
    except Exception:
        return False


def iter_patrol_batch_events(*, batch_id: str | None = None) -> Iterator[dict[str, Any]]:
    import redis

    client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(CHANNEL)
    try:
        import time
        heartbeat_at = time.monotonic()
        while True:
            message = pubsub.get_message(timeout=1)
            if not message:
                if time.monotonic() - heartbeat_at >= 10:
                    heartbeat_at = time.monotonic()
                    yield {"type": "heartbeat", "patrol_batch_id": batch_id}
                continue
            if message.get("type") != "message":
                continue
            try:
                payload = json.loads(message.get("data") or "{}")
            except json.JSONDecodeError:
                continue
            if batch_id and str(payload.get("patrol_batch_id") or "") not in {
                "",
                str(batch_id),
            }:
                if str(payload.get("patrol_batch_id") or "") != str(batch_id):
                    continue
            yield payload
    finally:
        pubsub.close()
