# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Redis Streams EventBus（发布 + 消费组工具）。"""

from __future__ import annotations

import logging
from typing import Any

from src.auth.token_store import get_redis
from src.common.config import get_settings
from src.core.events.domain_event import DomainEvent
from src.core.events.streams import STREAM_DLQ

logger = logging.getLogger(__name__)


class EventBus:
    @staticmethod
    def publish(stream: str, event: DomainEvent) -> str | None:
        """写入 Redis Stream；失败时记录日志，不抛异常。"""
        if not get_settings().EVENT_BUS_ENABLED:
            return None
        client = get_redis()
        if not client:
            logger.debug("EventBus.publish skipped: redis unavailable stream=%s type=%s", stream, event.event_type)
            return None
        try:
            msg_id = client.xadd(
                stream,
                event.to_stream_fields(),
                maxlen=get_settings().EVENT_BUS_STREAM_MAXLEN,
                approximate=True,
            )
            logger.debug(
                "event_published",
                extra={
                    "stream": stream,
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "message_id": msg_id,
                },
            )
            return str(msg_id)
        except Exception as exc:
            logger.warning(
                "event_publish_failed stream=%s event_type=%s error=%s",
                stream,
                event.event_type,
                exc,
            )
            return None

    @staticmethod
    def ensure_consumer_group(stream: str, group: str) -> None:
        client = get_redis()
        if not client:
            return
        try:
            client.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                logger.warning("xgroup_create failed stream=%s group=%s: %s", stream, group, exc)

    @staticmethod
    def publish_dlq(
        *,
        original_event: DomainEvent,
        consumer: str,
        error: str,
        stream: str,
        message_id: str,
    ) -> str | None:
        from datetime import datetime, timezone

        dlq = DomainEvent(
            event_type="event.dlq",
            source="system",
            correlation_id=original_event.event_id,
            payload={
                "original_event": original_event.model_dump(mode="json"),
                "consumer": consumer,
                "error": error,
                "source_stream": stream,
                "source_message_id": message_id,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return EventBus.publish(STREAM_DLQ, dlq)

    @staticmethod
    def read_group_pending_info(stream: str, group: str) -> dict[str, Any]:
        client = get_redis()
        if not client:
            return {}
        try:
            groups = client.xinfo_groups(stream)
            for g in groups or []:
                if g.get("name") == group:
                    return {
                        "stream": stream,
                        "group": group,
                        "pending": g.get("pending", 0),
                        "last_delivered_id": g.get("last-delivered-id"),
                        "consumers": g.get("consumers", 0),
                    }
        except Exception as exc:
            logger.debug("xinfo_groups failed: %s", exc)
        return {"stream": stream, "group": group, "pending": None}

    @staticmethod
    def list_dlq_entries(*, limit: int = 50) -> list[dict[str, Any]]:
        client = get_redis()
        if not client:
            return []
        try:
            items = client.xrevrange(STREAM_DLQ, count=limit)
            entries: list[dict[str, Any]] = []
            for msg_id, fields in items or []:
                try:
                    event = DomainEvent.from_stream_fields(fields)
                    entries.append(
                        {
                            "message_id": msg_id,
                            "event_id": event.event_id,
                            "occurred_at": event.occurred_at.isoformat(),
                            "payload": event.payload,
                        }
                    )
                except Exception:
                    entries.append({"message_id": msg_id, "raw": fields})
            return entries
        except Exception as exc:
            logger.warning("list_dlq failed: %s", exc)
            return []
