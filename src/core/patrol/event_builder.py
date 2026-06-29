# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Build operational network events from low-level changes."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from src.core.patrol.change_event_repository import ChangeEventRepository
from src.core.patrol.event_rules import match_event_rule


@dataclass(frozen=True)
class NetworkEvent:
    event_id: str
    event_type: str
    severity: str
    device_id: str | None
    entity_type: str
    entity_key: str
    source_change_id: str | None
    run_id: str | None
    occurred_at: str
    status: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_events_from_changes(changes: list[dict[str, Any]]) -> list[NetworkEvent]:
    events: list[NetworkEvent] = []
    occurred_at = _now()
    for change in changes:
        rule = match_event_rule(change)
        if not rule:
            continue

        events.append(
            NetworkEvent(
                event_id=str(uuid.uuid4()),
                event_type=rule.event_type,
                severity=rule.severity,
                device_id=change.get("device_id"),
                entity_type=str(change.get("entity_type") or "unknown"),
                entity_key=str(change.get("entity_key") or ""),
                source_change_id=change.get("change_id"),
                run_id=change.get("run_id"),
                occurred_at=change.get("detected_at") or occurred_at,
                status="open",
                payload={
                    "change": change,
                    "rule": {"rule_id": rule.rule_id, "version": rule.version},
                },
            )
        )
    return events


def summarize_no_event_changes(
    changes: list[dict[str, Any]],
    event_change_ids: set[str],
    *,
    limit: int = 10,
) -> dict[str, Any]:
    no_event_changes = [
        change
        for change in changes
        if change.get("change_id") and str(change.get("change_id")) not in event_change_ids
    ]
    by_field = Counter(str(change.get("field") or "unknown") for change in no_event_changes)
    by_entity_type = Counter(
        str(change.get("entity_type") or "unknown") for change in no_event_changes
    )
    by_command = Counter(
        str(change.get("command") or "unknown") for change in no_event_changes
    )
    by_change_type = Counter(
        str(change.get("change_type") or "unknown") for change in no_event_changes
    )
    examples = [
        {
            "change_id": change.get("change_id"),
            "device_id": change.get("device_id"),
            "command": change.get("command"),
            "entity_type": change.get("entity_type"),
            "entity_key": change.get("entity_key"),
            "field": change.get("field"),
            "change_type": change.get("change_type"),
            "old": change.get("old"),
            "new": change.get("new"),
        }
        for change in no_event_changes[:limit]
    ]
    return {
        "total": len(no_event_changes),
        "by_field": dict(by_field.most_common(limit)),
        "by_entity_type": dict(by_entity_type.most_common(limit)),
        "by_command": dict(by_command.most_common(limit)),
        "by_change_type": dict(by_change_type.most_common(limit)),
        "examples": examples,
    }


def save_events(events: list[NetworkEvent]) -> int:
    return ChangeEventRepository().save_events(events)


def publish_events(events: list[NetworkEvent]) -> list[str]:
    from src.core.events.bus import EventBus
    from src.core.events.domain_event import DomainEvent
    from src.core.events.streams import STREAM_NETWORK_EVENT

    message_ids: list[str] = []
    for event in events:
        msg_id = EventBus.publish(
            STREAM_NETWORK_EVENT,
            DomainEvent(
                event_type=f"network.{event.event_type}",
                source="patrol",
                correlation_id=event.run_id or event.event_id,
                payload=event.to_dict(),
            ),
        )
        if msg_id:
            message_ids.append(msg_id)
    return message_ids


def load_changes(
    *,
    run_id: str | None = None,
    device_id: str | None = None,
) -> list[dict[str, Any]]:
    return ChangeEventRepository().list_changes(
        run_id=run_id,
        device_id=device_id,
    )


def build_events_from_params(params: dict[str, Any]) -> dict[str, Any]:
    changes = params.get("changes")
    if changes is None:
        if not params.get("run_id") and not params.get("device_id"):
            raise ValueError("changes, run_id, or device_id is required")
        changes = load_changes(
            run_id=params.get("run_id"),
            device_id=params.get("device_id"),
        )
    events = build_events_from_changes(list(changes or []))
    persist = params.get("persist", True)
    persisted_event_count = save_events(events) if persist else 0
    event_change_ids = {
        event.source_change_id for event in events if event.source_change_id
    }
    event_change_ids = {str(change_id) for change_id in event_change_ids}
    no_event_count = 0
    if persist:
        no_event_change_ids = [
            str(change["change_id"])
            for change in changes or []
            if change.get("change_id") and change.get("change_id") not in event_change_ids
        ]
        no_event_count = ChangeEventRepository().mark_changes_event_status(
            no_event_change_ids,
            status="no_event",
        )
    message_ids: list[str] = []
    if params.get("publish", False):
        message_ids = publish_events(events)
    return {
        "success": True,
        "event_count": len(events),
        "persisted_event_count": persisted_event_count,
        "no_event_change_count": no_event_count,
        "no_event_summary": summarize_no_event_changes(
            list(changes or []),
            event_change_ids,
        ),
        "storage": "postgresql",
        "events": [event.to_dict() for event in events],
        "published_message_ids": message_ids,
    }
