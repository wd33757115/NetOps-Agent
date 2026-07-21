"""Historical replay for candidate event rules."""

from __future__ import annotations

from collections import Counter
from typing import Any

from src.core.patrol.event_rules import (
    EventRule,
    EventRuleDefinition,
    EventRuleRepository,
    event_rule_logical_key,
)
from src.core.patrol.rule_validation import matched_rules, sanitize_change
from src.infrastructure.db.models import NetworkChangeRecord
from src.infrastructure.db.postgres import get_db_session


def change_record_to_payload(row: NetworkChangeRecord) -> dict[str, Any]:
    return {
        "change_id": row.id,
        "run_id": row.run_id,
        "device_id": row.device_id,
        "command": row.canonical_command,
        "entity_type": row.entity_type,
        "entity_key": row.entity_key,
        "dimensions": row.dimensions_json or {},
        "field": row.field,
        "observation_type": row.observation_type,
        "old": row.old_value_json,
        "new": row.new_value_json,
        "change_type": row.change_type,
        "previous_snapshot_id": row.previous_snapshot_id,
        "current_snapshot_id": row.current_snapshot_id,
        "previous_observation_id": row.previous_observation_id,
        "current_observation_id": row.current_observation_id,
        "source": row.source,
        "old_number": row.old_number,
        "new_number": row.new_number,
        "event_status": row.event_status,
        "detected_at": row.detected_at.isoformat() if row.detected_at else None,
        "confidence": row.confidence,
    }


def _candidate_rules(definitions: list[EventRuleDefinition]) -> list[EventRule]:
    return [definition.to_rule() for definition in definitions]


def _logical_key(definition: EventRuleDefinition) -> str:
    return event_rule_logical_key(
        name=definition.rule_id,
        event_type=definition.event_type,
        condition=definition.condition,
        event_class=definition.event_class,
    )


def _active_definition(row: dict[str, Any]) -> EventRuleDefinition:
    return EventRuleDefinition(
        rule_id=str(row.get("rule_name") or row.get("name") or row.get("rule_id") or ""),
        version=int(row.get("version") or 1),
        event_type=str(row.get("event_type") or ""),
        severity=str(row.get("severity") or "warning"),
        condition=dict(row.get("condition") or {}),
        description=row.get("description"),
        priority=int(row.get("priority") or 100),
        exclusive=bool(row.get("exclusive", False)),
        event_class=str(row.get("event_class") or "fault"),
        recovery=dict(row.get("recovery") or {}),
        correlation_keys=tuple(
            row.get("correlation_keys") or ("device_id", "entity_type", "entity_key", "field")
        ),
    )


def replay_event_rule_definitions(
    definitions: list[EventRuleDefinition],
    *,
    limit: int = 1000,
    include_active_collisions: bool = True,
    run_ids: list[str] | None = None,
) -> dict[str, Any]:
    normalized_run_ids = sorted(
        {str(run_id).strip() for run_id in (run_ids or []) if str(run_id).strip()}
    )
    if run_ids is not None and not normalized_run_ids:
        raise ValueError("run_ids must contain at least one non-empty run id")
    candidate_rules = _candidate_rules(definitions)
    active_definitions = (
        [
            _active_definition(row)
            for row in EventRuleRepository().list_versions(status="active", limit=None)
        ]
        if include_active_collisions
        else []
    )
    active_rules = _candidate_rules(active_definitions)
    candidate_rule_ids = {rule.rule_id for rule in candidate_rules}
    candidate_logical_keys = {_logical_key(definition) for definition in definitions}
    active_logical_keys = {
        definition.rule_id: _logical_key(definition) for definition in active_definitions
    }
    replacement_rule_ids = sorted(
        {
            rule.rule_id
            for rule in active_rules
            if rule.rule_id in candidate_rule_ids
            or active_logical_keys.get(rule.rule_id) in candidate_logical_keys
        }
    )
    collision_ruleset = [rule for rule in active_rules if rule.rule_id not in replacement_rule_ids]
    matched_change_count = 0
    collision_count = 0
    event_types: Counter[str] = Counter()
    entity_types: Counter[str] = Counter()
    top_devices: Counter[str] = Counter()
    collision_rules: Counter[str] = Counter()
    change_shapes: Counter[str] = Counter()
    matched_change_shapes: Counter[str] = Counter()
    unexpected_matches: list[dict[str, Any]] = []
    sampled_run_id_values: set[str] = set()
    oldest_detected_at = None
    newest_detected_at = None

    with get_db_session() as db:
        query = db.query(NetworkChangeRecord)
        if run_ids is not None:
            query = query.filter(NetworkChangeRecord.run_id.in_(normalized_run_ids))
        total_change_count = int(query.count() or 0)
        rows = (
            query.order_by(NetworkChangeRecord.detected_at.desc())
            .limit(max(1, min(int(limit), 10000)))
            .all()
        )
        for row in rows:
            if str(row.run_id or "").strip():
                sampled_run_id_values.add(str(row.run_id).strip())
            if row.detected_at is not None:
                if oldest_detected_at is None or row.detected_at < oldest_detected_at:
                    oldest_detected_at = row.detected_at
                if newest_detected_at is None or row.detected_at > newest_detected_at:
                    newest_detected_at = row.detected_at
            shape = (
                f"{row.change_type or 'unknown'}"
                f"|{row.observation_type or row.entity_type or 'unknown'}"
            )
            change_shapes[shape] += 1
            change = change_record_to_payload(row)
            candidates = matched_rules(change, candidate_rules)
            if not candidates:
                continue
            matched_change_count += 1
            matched_change_shapes[shape] += 1
            for rule in candidates:
                event_types[rule.event_type] += 1
                collision_rules[rule.rule_id] += 1
            entity_types[str(row.entity_type or "unknown")] += 1
            if row.device_id:
                top_devices[str(row.device_id)] += 1
            active_matches = matched_rules(change, collision_ruleset) if collision_ruleset else []
            if len(candidates) > 1 or active_matches:
                collision_count += 1
                if len(unexpected_matches) < 20:
                    unexpected_matches.append(
                        {
                            "collision_type": (
                                "candidate_active" if active_matches else "candidate_candidate"
                            ),
                            "candidate_rule_ids": [rule.rule_id for rule in candidates],
                            "active_rule_ids": [rule.rule_id for rule in active_matches],
                            "change": sanitize_change(change),
                        }
                    )

    sampled_run_ids = sorted(sampled_run_id_values)
    scope = {
        "mode": "run_ids" if run_ids is not None else "global_recent",
        "run_ids": normalized_run_ids,
        "run_id_count": len(normalized_run_ids),
        "sampled_run_ids": sampled_run_ids,
        "sampled_run_id_count": len(sampled_run_ids),
    }
    if run_ids is not None:
        scope["missing_run_ids"] = sorted(set(normalized_run_ids) - set(sampled_run_ids))
    sampled_count = len(rows) if "rows" in locals() else 0
    window_days = 0
    if oldest_detected_at is not None and newest_detected_at is not None:
        window_days = max(0, (newest_detected_at - oldest_detected_at).days)
    # Representativeness: collision==0 over a sparse window is an empty gate, so
    # expose how much of history and how many change shapes replay covered.
    representativeness = {
        "total_change_count": total_change_count,
        "sampled_change_count": sampled_count,
        "sample_coverage_rate": (
            round(sampled_count / total_change_count, 4) if total_change_count else 0.0
        ),
        "matched_change_count": matched_change_count,
        "match_rate": (
            round(matched_change_count / sampled_count, 4) if sampled_count else 0.0
        ),
        "window_days": window_days,
        "window_start": oldest_detected_at.isoformat() if oldest_detected_at else None,
        "window_end": newest_detected_at.isoformat() if newest_detected_at else None,
        "change_shape_count": len(change_shapes),
        "matched_change_shape_count": len(matched_change_shapes),
        "change_shapes": dict(change_shapes.most_common(20)),
        "sparse_history": total_change_count < 10 or len(change_shapes) < 2,
    }
    return {
        "success": collision_count == 0,
        "rule_count": len(definitions),
        "sampled_change_count": sampled_count,
        "total_change_count": total_change_count,
        "matched_change_count": matched_change_count,
        "estimated_daily_event_count": matched_change_count,
        "event_count": sum(event_types.values()),
        "event_types": dict(event_types.most_common()),
        "matched_entity_types": dict(entity_types.most_common()),
        "top_devices": dict(top_devices.most_common(10)),
        "collision_count": collision_count,
        "collision_rules": dict(collision_rules.most_common()),
        "unexpected_matches": unexpected_matches,
        "replacement_rule_ids": replacement_rule_ids,
        "replacement_logical_keys": sorted(
            {
                active_logical_keys[rule_id]
                for rule_id in replacement_rule_ids
                if rule_id in active_logical_keys
            }
        ),
        "candidate_logical_keys": sorted(candidate_logical_keys),
        "recovery_pair_coverage": None,
        "representativeness": representativeness,
        "scope": scope,
    }
