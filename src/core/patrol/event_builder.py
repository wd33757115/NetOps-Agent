# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Build operational network events from low-level changes."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from src.core.patrol.change_event_repository import ChangeEventRepository
from src.core.patrol.event_rules import (
    EventRule,
    EventRuleRepository,
    event_rules_from_snapshot,
    load_event_rules,
    match_event_rules,
)


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


def _event_correlation_identity(
    *,
    device_id: str | None,
    entity_type: str,
    entity_key: str,
    change: dict[str, Any],
    correlation_keys: tuple[str, ...],
) -> tuple[str, dict[str, Any], str]:
    dimensions = dict(change.get("dimensions") or {})
    values = {
        "device_id": device_id,
        "entity_type": entity_type,
        "entity_key": entity_key,
        **change,
        **dimensions,
    }
    resolved: dict[str, Any] = {}
    for key in correlation_keys:
        value = values.get(key)
        if value is None or value == "":
            value = {"missing": True, "entity_key": entity_key}
        resolved[str(key)] = value
    canonical = json.dumps(
        [[key, resolved[str(key)]] for key in correlation_keys],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    legacy = "|".join(str(values.get(key) or "") for key in correlation_keys)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), resolved, legacy


def build_events_from_changes(
    changes: list[dict[str, Any]],
    *,
    rules: list[EventRule] | None = None,
    heartbeat: Any | None = None,
) -> list[NetworkEvent]:
    events: list[NetworkEvent] = []
    occurred_at = _now()
    rules = load_event_rules() if rules is None else rules
    for index, change in enumerate(changes):
        if heartbeat is not None:
            heartbeat()
        matched_rules = match_event_rules(change, rules=rules, preordered=True)
        if not matched_rules:
            continue
        entity_type = str(change.get("entity_type") or "unknown")
        entity_key = str(change.get("entity_key") or "")
        device_id = change.get("device_id")

        for rule in matched_rules:
            correlation_key, correlation_dimensions, legacy_correlation_key = (
                _event_correlation_identity(
                    device_id=device_id,
                    entity_type=entity_type,
                    entity_key=entity_key,
                    change=change,
                    correlation_keys=rule.correlation_keys,
                )
            )
            analysis_revision = str(change.get("analysis_revision") or "original")
            historical_backfill = analysis_revision.startswith("historical_backfill:")
            if historical_backfill:
                correlation_key = f"{correlation_key}|{analysis_revision}"
                correlation_dimensions = {
                    **correlation_dimensions,
                    "historical_revision": analysis_revision,
                }
                legacy_correlation_key = ""
            events.append(
                NetworkEvent(
                    event_id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            "|".join(
                                [
                                    "network-event-candidate-v2",
                                    str(change.get("change_id") or ""),
                                    rule.event_type,
                                    correlation_key,
                                    rule.rule_id,
                                    str(rule.version),
                                ]
                            ),
                        )
                    ),
                    event_type=rule.event_type,
                    severity=rule.severity,
                    device_id=device_id,
                    entity_type=entity_type,
                    entity_key=entity_key,
                    source_change_id=change.get("change_id"),
                    run_id=change.get("run_id"),
                    occurred_at=(
                        change.get("observed_at") or change.get("detected_at") or occurred_at
                    ),
                    status="historical" if historical_backfill else "open",
                    payload={
                        "change": change,
                        "rule": {
                            "rule_id": rule.rule_id,
                            "version": rule.version,
                            "priority": rule.priority,
                            "exclusive": rule.exclusive,
                            "event_class": rule.event_class,
                            "recovery": dict(rule.recovery or {}),
                            "correlation_keys": list(rule.correlation_keys),
                        },
                        "correlation_key": correlation_key,
                        "correlation_dimensions": correlation_dimensions,
                        "legacy_correlation_key": legacy_correlation_key,
                        "dedupe_key": f"{rule.event_type}|{correlation_key}",
                        "historical_backfill": historical_backfill,
                        "analysis_revision": analysis_revision,
                    },
                )
            )
    return events


def no_event_reason(
    change: dict[str, Any],
    *,
    active_rule_count: int | None = None,
) -> str:
    if change.get("change_type") == "missing":
        return "presence_confirmation_pending"
    if change.get("change_type") == "initial" and change.get("new") is None:
        return "initial_empty_state"
    if active_rule_count == 0:
        return "no_active_event_rules"
    if active_rule_count is not None:
        return "active_rules_evaluated_no_match"
    return "event_rule_context_unavailable"


def summarize_no_event_changes(
    changes: list[dict[str, Any]],
    event_change_ids: set[str],
    *,
    active_rule_count: int | None = None,
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
    by_command = Counter(str(change.get("command") or "unknown") for change in no_event_changes)
    by_change_type = Counter(
        str(change.get("change_type") or "unknown") for change in no_event_changes
    )

    by_reason = Counter(
        no_event_reason(change, active_rule_count=active_rule_count) for change in no_event_changes
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
            "reason": no_event_reason(change, active_rule_count=active_rule_count),
        }
        for change in no_event_changes[:limit]
    ]
    return {
        "total": len(no_event_changes),
        "by_field": dict(by_field.most_common(limit)),
        "by_entity_type": dict(by_entity_type.most_common(limit)),
        "by_command": dict(by_command.most_common(limit)),
        "by_change_type": dict(by_change_type.most_common(limit)),
        "by_reason": dict(by_reason.most_common(limit)),
        "active_rule_count": active_rule_count,
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
                event_id=event.event_id,
                event_type=f"network.{event.event_type}",
                occurred_at=event.occurred_at,
                source="patrol",
                correlation_id=event.run_id or event.event_id,
                payload=event.to_dict(),
            ),
        )
        if msg_id:
            message_ids.append(msg_id)
    return message_ids


def publish_event_outbox(*, limit: int = 10) -> list[str]:
    from src.core.events.bus import EventBus
    from src.core.events.domain_event import DomainEvent
    from src.core.events.streams import STREAM_NETWORK_EVENT

    repository = ChangeEventRepository()
    message_ids: list[str] = []
    claim_token, rows = repository.claim_event_outbox(limit=limit)
    for row in rows:
        try:
            if not repository.renew_event_outbox_claim(str(row["id"]), claim_token=claim_token):
                continue
            payload = dict(row.get("payload_json") or {})
            message_id = EventBus.publish(
                STREAM_NETWORK_EVENT,
                DomainEvent(
                    event_id=str(payload.get("event_id") or row["event_id"]),
                    event_type=str(row.get("event_type") or "network.event"),
                    occurred_at=payload.get("occurred_at") or _now(),
                    source="patrol",
                    correlation_id=str(
                        payload.get("run_id") or payload.get("event_id") or row["id"]
                    ),
                    payload=payload,
                ),
            )
            if message_id:
                repository.mark_event_outbox_published(
                    str(row["id"]),
                    str(message_id),
                    claim_token=claim_token,
                )
                message_ids.append(str(message_id))
            else:
                repository.mark_event_outbox_failed(
                    str(row["id"]),
                    "event_bus_publish_returned_none",
                    claim_token=claim_token,
                )
        except Exception as exc:
            repository.mark_event_outbox_failed(str(row["id"]), str(exc), claim_token=claim_token)
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
    from src.core.patrol.repository import (
        AnalysisAttemptFencedError,
        AnalysisAttemptInProgressError,
        AnalysisLeaseHeartbeat,
        PatrolRepository,
    )

    run_id = params.get("run_id")
    if not run_id:
        return _build_events_from_params_impl(params, rules=None)
    patrol_repository = PatrolRepository()
    supplied_changes = (
        list(params.get("changes") or [])
        if params.get("changes") is not None
        else load_changes(run_id=str(run_id), device_id=params.get("device_id"))
    )
    requested_revision = str(params.get("analysis_revision") or "original")
    if params.get("changes") is None:
        supplied_changes = [
            change
            for change in supplied_changes
            if str(change.get("analysis_revision") or "original") == requested_revision
        ]
    if not params.get("persist", True):
        return _build_events_from_params_impl({**params, "changes": supplied_changes}, rules=None)
    frozen_rules = EventRuleRepository().snapshot_active_rules()
    frozen_rules = json.loads(json.dumps(frozen_rules, default=str))
    input_revision_digest = hashlib.sha256(
        json.dumps(
            [
                [change.get("change_id"), change.get("processed_at")]
                for change in sorted(
                    supplied_changes,
                    key=lambda row: str(row.get("change_id") or ""),
                )
            ],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    scope = {
        "device_id": params.get("device_id"),
        "full_run": params.get("changes") is None and not params.get("device_id"),
        "persist": bool(params.get("persist", True)),
        "publish": bool(params.get("publish", False)),
        "change_ids": sorted(
            str(change.get("change_id")) for change in supplied_changes if change.get("change_id")
        ),
        "input_revision_digest": input_revision_digest,
        "analysis_revision": str(params.get("analysis_revision") or "original"),
    }
    updates_run_status = bool(scope["full_run"])
    attempt = patrol_repository.begin_analysis_attempt(
        run_id=str(run_id),
        stage="event_building",
        scope=scope,
        rule_bundle={"event_rules": frozen_rules},
    )
    if attempt["reused"]:
        occurrences = ChangeEventRepository().list_event_occurrences(run_id=str(run_id))
        change_ids = {
            str(change.get("change_id")) for change in supplied_changes if change.get("change_id")
        }
        occurrences = [
            item for item in occurrences if str(item.get("source_change_id") or "") in change_ids
        ]
        return {
            **dict(attempt["result"] or {}),
            "events": [dict(item.get("payload_json") or {}) for item in occurrences],
            "reused_analysis_attempt": True,
        }
    if attempt["in_progress"]:
        raise AnalysisAttemptInProgressError(f"event building already running for {run_id}")
    heartbeat = AnalysisLeaseHeartbeat(patrol_repository, attempt)
    if updates_run_status:
        try:
            patrol_repository.update_analysis_status(
                str(run_id),
                analysis_status="building_events",
                baseline_eligible=False,
                metadata={"event_rule_bundle_digest": attempt["rule_bundle_digest"]},
            )
        except ValueError:
            pass
    heartbeat.start()
    try:
        rules = event_rules_from_snapshot(list(attempt["rule_bundle"].get("event_rules") or []))
        heartbeat(force=True)
        result = _build_events_from_params_impl(
            {
                **params,
                "changes": supplied_changes,
                "_analysis_updates_run_status": updates_run_status,
            },
            rules=rules,
            heartbeat=heartbeat,
        )
        heartbeat.stop()
        patrol_repository.complete_analysis_attempt(
            str(attempt["attempt_id"]),
            attempt_number=int(attempt["attempt_number"]),
            owner_token=str(attempt["owner_token"]),
            result=result,
        )
        return result
    except Exception as exc:
        heartbeat.stop()
        try:
            patrol_repository.fail_analysis_attempt(
                str(attempt["attempt_id"]),
                attempt_number=int(attempt["attempt_number"]),
                owner_token=str(attempt["owner_token"]),
                error=str(exc),
            )
        except AnalysisAttemptFencedError:
            raise
        if updates_run_status:
            try:
                patrol_repository.update_analysis_status(
                    str(run_id),
                    analysis_status="failed",
                    baseline_eligible=False,
                    metadata={
                        "analysis_failure_stage": "building_events",
                        "analysis_failure": str(exc),
                    },
                )
            except ValueError:
                pass
        raise


def _build_events_from_params_impl(
    params: dict[str, Any],
    *,
    rules: list[EventRule] | None,
    heartbeat: Any | None = None,
) -> dict[str, Any]:
    from src.core.patrol.repository import PatrolRepository

    run_id = params.get("run_id")
    patrol_repository = PatrolRepository()
    changes = params.get("changes")
    if changes is None:
        if not params.get("run_id") and not params.get("device_id"):
            raise ValueError("changes, run_id, or device_id is required")
        changes = load_changes(
            run_id=params.get("run_id"),
            device_id=params.get("device_id"),
        )
    effective_rules = load_event_rules() if rules is None else rules
    events = build_events_from_changes(
        list(changes or []),
        rules=effective_rules,
        heartbeat=heartbeat,
    )
    persist = params.get("persist", True)
    created_event_count = save_events(events) if persist else 0
    event_persistence = {
        "persisted_occurrence_count": created_event_count,
        "created_event_count": created_event_count,
        "updated_event_count": 0,
        "recovered_event_count": 0,
        "unique_event_count": created_event_count,
        "open_after_run_count": created_event_count,
        "resolved_after_run_count": 0,
        "current_open_event_count": created_event_count,
        "current_resolved_event_count": 0,
        "open_event_count": created_event_count,
        "resolved_event_count": 0,
    }
    if persist and run_id:
        event_repository = ChangeEventRepository()
        summarize_run_events = getattr(event_repository, "summarize_run_events", None)
        if callable(summarize_run_events):
            event_persistence = summarize_run_events(
                run_id=str(run_id),
                source_change_ids={
                    str(change.get("change_id"))
                    for change in changes or []
                    if change.get("change_id")
                },
            )
    persisted_event_count = int(event_persistence["persisted_occurrence_count"])
    event_change_ids = {event.source_change_id for event in events if event.source_change_id}
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
            decision_reasons={
                str(change["change_id"]): no_event_reason(
                    change,
                    active_rule_count=len(effective_rules),
                )
                for change in changes or []
                if change.get("change_id") and str(change["change_id"]) in no_event_change_ids
            },
        )
    message_ids: list[str] = []
    if params.get("publish", False):
        message_ids = publish_event_outbox()
    result = {
        "success": True,
        "event_count": len(events),
        "matched_event_count": len(events),
        "persisted_event_count": persisted_event_count,
        "created_event_count": int(event_persistence["created_event_count"]),
        "updated_event_count": int(event_persistence["updated_event_count"]),
        "recovered_event_count": int(event_persistence["recovered_event_count"]),
        "deduplicated_event_count": max(0, len(events) - persisted_event_count),
        "unique_event_count": int(event_persistence["unique_event_count"]),
        "open_after_run_count": int(event_persistence["open_after_run_count"]),
        "resolved_after_run_count": int(event_persistence["resolved_after_run_count"]),
        "current_open_event_count": int(event_persistence["current_open_event_count"]),
        "current_resolved_event_count": int(event_persistence["current_resolved_event_count"]),
        "open_event_count": int(event_persistence["open_event_count"]),
        "resolved_event_count": int(event_persistence["resolved_event_count"]),
        "no_event_change_count": no_event_count,
        "no_event_summary": summarize_no_event_changes(
            list(changes or []),
            event_change_ids,
            active_rule_count=len(effective_rules),
        ),
        "storage": "postgresql",
        "events": [event.to_dict() for event in events],
        "published_message_ids": message_ids,
    }
    if run_id and params.get("_analysis_updates_run_status", True):
        run = patrol_repository.get_run(str(run_id)) or {}
        if run:
            collection_status = str(run.get("collection_status") or "failed")
            patrol_repository.update_analysis_status(
                str(run_id),
                analysis_status="completed",
                baseline_eligible=collection_status in {"completed", "partial"},
                metadata={
                    "event_count": len(events),
                    "no_event_change_count": no_event_count,
                },
            )
    return result
