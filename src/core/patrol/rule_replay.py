"""Historical replay for candidate event rules."""

from __future__ import annotations

from collections import Counter
from typing import Any

from src.core.patrol.event_rules import EventRule, EventRuleDefinition, EventRuleRepository
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
        "field": row.field,
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


def replay_event_rule_definitions(
    definitions: list[EventRuleDefinition],
    *,
    limit: int = 1000,
    include_active_collisions: bool = True,
) -> dict[str, Any]:
    candidate_rules = _candidate_rules(definitions)
    active_rules = EventRuleRepository().list_active_rules() if include_active_collisions else []
    matched_change_count = 0
    collision_count = 0
    event_types: Counter[str] = Counter()
    entity_types: Counter[str] = Counter()
    top_devices: Counter[str] = Counter()
    collision_rules: Counter[str] = Counter()
    unexpected_matches: list[dict[str, Any]] = []

    with get_db_session() as db:
        rows = (
            db.query(NetworkChangeRecord)
            .order_by(NetworkChangeRecord.detected_at.desc())
            .limit(max(1, min(int(limit), 10000)))
            .all()
        )
        for row in rows:
            change = change_record_to_payload(row)
            candidates = matched_rules(change, candidate_rules)
            if not candidates:
                continue
            matched_change_count += 1
            for rule in candidates:
                event_types[rule.event_type] += 1
                collision_rules[rule.rule_id] += 1
            entity_types[str(row.entity_type or "unknown")] += 1
            if row.device_id:
                top_devices[str(row.device_id)] += 1
            active_matches = matched_rules(change, active_rules) if active_rules else []
            if len(candidates) > 1 or active_matches:
                collision_count += 1
                if len(unexpected_matches) < 20:
                    unexpected_matches.append(
                        {
                            "collision_type": (
                                "candidate_active"
                                if active_matches
                                else "candidate_candidate"
                            ),
                            "candidate_rule_ids": [rule.rule_id for rule in candidates],
                            "active_rule_ids": [rule.rule_id for rule in active_matches],
                            "change": sanitize_change(change),
                        }
                    )

    return {
        "success": collision_count == 0,
        "rule_count": len(definitions),
        "sampled_change_count": len(rows) if "rows" in locals() else 0,
        "matched_change_count": matched_change_count,
        "estimated_daily_event_count": matched_change_count,
        "event_count": sum(event_types.values()),
        "event_types": dict(event_types.most_common()),
        "matched_entity_types": dict(entity_types.most_common()),
        "top_devices": dict(top_devices.most_common(10)),
        "collision_count": collision_count,
        "collision_rules": dict(collision_rules.most_common()),
        "unexpected_matches": unexpected_matches,
        "recovery_pair_coverage": None,
    }
