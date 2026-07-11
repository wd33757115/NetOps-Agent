# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Stream Consumer 基类。"""

from __future__ import annotations

import logging
import socket
from abc import ABC, abstractmethod
from typing import Any

from src.auth.token_store import get_redis
from src.common.config import get_settings
from src.core.events.bus import EventBus
from src.core.events.domain_event import DomainEvent
from src.core.events.idempotency import (
    IdempotencyStoreUnavailableError,
    is_processed,
    mark_processed,
)

logger = logging.getLogger(__name__)


class StreamConsumer(ABC):
    name: str
    group: str
    streams: list[str]

    def __init__(self) -> None:
        self._consumer_name = f"{self.name}-{socket.gethostname()}"

    def setup(self) -> None:
        for stream in self.streams:
            EventBus.ensure_consumer_group(stream, self.group)

    @abstractmethod
    def handle(self, event: DomainEvent) -> None:
        ...

    def poll_once(self) -> int:
        """拉取一批消息并处理，返回处理条数。"""
        client = get_redis()
        if not client:
            return 0
        settings = get_settings()
        processed = 0
        for stream in self.streams:
            reclaimed = self._claim_pending(client, stream, settings)
            processed += self._process_messages(client, stream, reclaimed)
            try:
                batches = client.xreadgroup(
                    groupname=self.group,
                    consumername=self._consumer_name,
                    streams={stream: ">"},
                    count=settings.EVENT_BUS_CONSUMER_BATCH_SIZE,
                    block=settings.EVENT_BUS_CONSUMER_BLOCK_MS,
                )
            except Exception as exc:
                logger.debug("xreadgroup failed stream=%s group=%s: %s", stream, self.group, exc)
                continue
            if not batches:
                continue
            for _stream_name, messages in batches:
                processed += self._process_messages(client, stream, messages)
        return processed

    def _claim_pending(self, client, stream: str, settings) -> list[tuple[Any, Any]]:
        try:
            result = client.xautoclaim(
                stream,
                self.group,
                self._consumer_name,
                min_idle_time=max(
                    1,
                    int(getattr(settings, "EVENT_BUS_PENDING_MIN_IDLE_MS", 60_000)),
                ),
                start_id="0-0",
                count=max(
                    1,
                    int(getattr(settings, "EVENT_BUS_RECLAIM_BATCH_SIZE", 20)),
                ),
            )
        except Exception as exc:
            logger.debug(
                "xautoclaim failed stream=%s group=%s: %s",
                stream,
                self.group,
                exc,
            )
            return []
        if not result or len(result) < 2:
            return []
        return list(result[1] or [])

    def _process_messages(self, client, stream: str, messages: list[tuple[Any, Any]]) -> int:
        processed = 0
        for msg_id, fields in messages:
            event: DomainEvent | None = None
            try:
                event = DomainEvent.from_stream_fields(fields)
                if is_processed(self.group, event.idempotency_key):
                    client.xack(stream, self.group, msg_id)
                    continue
                self.handle(event)
                mark_processed(self.group, event.idempotency_key)
                client.xack(stream, self.group, msg_id)
                processed += 1
            except IdempotencyStoreUnavailableError as exc:
                logger.warning(
                    "event_consumer_idempotency_unavailable consumer=%s stream=%s msg=%s error=%s",
                    self.name,
                    stream,
                    msg_id,
                    exc,
                )
            except Exception as exc:
                logger.warning(
                    "event_consumer_handle_failed consumer=%s stream=%s msg=%s error=%s",
                    self.name,
                    stream,
                    msg_id,
                    exc,
                )
                dlq_message_id = None
                dlq_event = event or DomainEvent(
                    event_type="event.invalid_envelope",
                    source="system",
                    payload={
                        "source_stream": stream,
                        "source_message_id": str(msg_id),
                    },
                )
                dlq_message_id = EventBus.publish_dlq(
                        original_event=dlq_event,
                        consumer=self.name,
                        error=str(exc),
                        stream=stream,
                        message_id=str(msg_id),
                )
                if dlq_message_id:
                    try:
                        client.xack(stream, self.group, msg_id)
                    except Exception:
                        pass
        return processed
