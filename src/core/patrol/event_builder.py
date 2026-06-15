# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Build operational network events from low-level changes."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from src.core.patrol.change_event_repository import ChangeEventRepository


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


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    try:
        return float(text)
    except ValueError:
        return None


def _is_down(value: Any) -> bool:
    text = _norm(value)
    return text in {"down", "administratively down", "admin-down", "notconnect", "inactive"}


def _is_up(value: Any) -> bool:
    text = _norm(value)
    return text in {"up", "connected", "active", "established", "normal"}


def _field_in(change: dict[str, Any], names: set[str]) -> bool:
    return _norm(change.get("field")).replace("-", "_") in names


def build_events_from_changes(changes: list[dict[str, Any]]) -> list[NetworkEvent]:
    events: list[NetworkEvent] = []
    occurred_at = _now()
    for change in changes:
        field = _norm(change.get("field")).replace("-", "_")
        old = change.get("old")
        new = change.get("new")
        event_type = None
        severity = None

        if field in {"cpu", "cpu_5s", "cpu_1m", "cpu_5m", "cpu_usage"}:
            value = _to_float(new)
            if value is not None and value >= 90:
                event_type = "CPUHigh"
                severity = "warning"
        elif _field_in(
            change,
            {"status", "protocol", "physical", "physical_status", "oper_status", "line_protocol"},
        ):
            if _is_up(old) and _is_down(new):
                event_type = "InterfaceDown"
                severity = "major"
            elif _is_down(old) and _is_up(new):
                event_type = "InterfaceUp"
                severity = "info"
        elif field in {"state", "bgp_state", "peer_state", "neighbor_state"}:
            if _norm(old) == "established" and _norm(new) != "established":
                event_type = "BGPNeighborLost"
                severity = "critical"
            elif _norm(old) != "established" and _norm(new) == "established":
                event_type = "BGPNeighborEstablished"
                severity = "info"
        elif (
            change.get("entity_type") == "raw_command_output"
            and change.get("field") == "raw_text_hash"
        ):
            command = _norm(change.get("command"))
            if any(
                marker in command
                for marker in ("configuration", "running-config", "current-configuration")
            ):
                event_type = "ConfigChanged"
                severity = "minor"

        if not event_type:
            continue

        events.append(
            NetworkEvent(
                event_id=str(uuid.uuid4()),
                event_type=event_type,
                severity=severity or "info",
                device_id=change.get("device_id"),
                entity_type=str(change.get("entity_type") or "unknown"),
                entity_key=str(change.get("entity_key") or ""),
                source_change_id=change.get("change_id"),
                run_id=change.get("run_id"),
                occurred_at=change.get("detected_at") or occurred_at,
                status="open",
                payload={"change": change},
            )
        )
    return events


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
    persisted_event_count = (
        save_events(events) if params.get("persist", True) else 0
    )
    message_ids: list[str] = []
    if params.get("publish", False):
        message_ids = publish_events(events)
    return {
        "success": True,
        "event_count": len(events),
        "persisted_event_count": persisted_event_count,
        "storage": "postgresql",
        "events": [event.to_dict() for event in events],
        "published_message_ids": message_ids,
    }
