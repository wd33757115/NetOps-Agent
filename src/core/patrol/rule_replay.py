"""Historical replay for candidate event rules."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from typing import Any

from src.core.patrol.event_rules import (
    EventRule,
    EventRuleDefinition,
    EventRuleRepository,
    event_rule_logical_key,
)
from src.core.patrol.rule_validation import matched_rules, sanitize_change
from src.infrastructure.db.models import NetworkChangeRecord, StateObservationRecord
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


def _contract_record_sets(contract: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(contract, dict) or not contract:
        return []
    raw_sets = contract.get("record_sets")
    if isinstance(raw_sets, list) and raw_sets:
        return [dict(item) for item in raw_sets if isinstance(item, dict)]
    return [dict(contract)]


def _entity_identity(record_set: dict[str, Any], entity_key: Any) -> dict[str, Any]:
    try:
        values = json.loads(str(entity_key or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        values = []
    if not isinstance(values, list):
        values = [values]
    return {
        str(field): values[index]
        for index, field in enumerate(record_set.get("primary_keys") or [])
        if index < len(values)
    }


def _project_candidate_change(
    change: dict[str, Any],
    contract: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    """Project pre-contract Change evidence into the candidate namespace."""

    source_type = str(change.get("entity_type") or "")
    record_set = next(
        (
            item
            for item in _contract_record_sets(contract)
            if str(item.get("entity_type") or "") == source_type
        ),
        None,
    )
    if record_set is None:
        return dict(change), None

    projected = dict(change)
    field = str(projected.get("field") or "")
    semantics = dict(record_set.get("state_semantics") or {}).get(field)
    if not isinstance(semantics, dict):
        semantics = {}
    canonical_type = str(record_set.get("canonical_entity_type") or "").strip()
    if canonical_type:
        projected["entity_type"] = canonical_type
    observation_type = str(semantics.get("observation_type") or "").strip()
    if observation_type:
        projected["observation_type"] = observation_type

    value_mapping = {
        str(key).strip().casefold(): value
        for key, value in dict(semantics.get("value_mapping") or {}).items()
    }
    if value_mapping:
        for key in ("old", "new"):
            value = projected.get(key)
            mapped = value_mapping.get(str(value or "").strip().casefold())
            if mapped is not None:
                projected[key] = mapped

    identity = _entity_identity(record_set, projected.get("entity_key"))
    dimensions = dict(projected.get("dimensions") or {})
    for name, binding in dict(record_set.get("entity_dimensions") or {}).items():
        if not isinstance(binding, dict):
            continue
        if "literal" in binding:
            dimensions[str(name)] = binding["literal"]
        elif str(binding.get("field") or "") in identity:
            dimensions[str(name)] = identity[str(binding["field"])]
    projected["dimensions"] = dimensions
    return projected, str(record_set.get("name") or source_type)


def _candidate_threshold_contexts(
    *,
    changes: list[dict[str, Any]],
    observations: list[StateObservationRecord],
    contract: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Resolve candidate cross-record threshold references without persistence."""

    record_sets = _contract_record_sets(contract)
    by_name = {
        str(item.get("name") or item.get("entity_type") or ""): item
        for item in record_sets
    }
    observation_rows: dict[tuple[str, str, tuple[tuple[str, Any], ...]], dict[str, Any]] = {}
    for row in observations:
        target_set = next(
            (
                item
                for item in record_sets
                if str(item.get("entity_type") or "") == str(row.entity_type or "")
            ),
            None,
        )
        if target_set is None:
            continue
        identity = _entity_identity(target_set, row.entity_key)
        identity_key = tuple(sorted(identity.items()))
        key = (
            str(row.snapshot_id),
            str(target_set.get("name") or target_set.get("entity_type") or ""),
            identity_key,
        )
        observation_rows.setdefault(key, {})[str(row.field_name)] = row.value_json

    output: dict[str, dict[str, Any]] = {}
    for relation in (contract or {}).get("record_set_relations") or []:
        if not isinstance(relation, dict):
            continue
        source_set = by_name.get(str(relation.get("source_record_set") or ""))
        target_set = by_name.get(str(relation.get("target_record_set") or ""))
        if source_set is None or target_set is None:
            continue
        join_keys = dict(relation.get("join_keys") or {})
        bindings = dict(relation.get("threshold_bindings") or {})
        for change in changes:
            if str(change.get("entity_type") or "") != str(source_set.get("entity_type") or ""):
                continue
            roles = bindings.get(str(change.get("field") or ""))
            if not isinstance(roles, dict):
                continue
            source_identity = _entity_identity(source_set, change.get("entity_key"))
            target_identity = {
                str(target_key): source_identity.get(str(source_key))
                for source_key, target_key in join_keys.items()
            }
            if any(value is None for value in target_identity.values()):
                continue
            lookup_key = (
                str(change.get("current_snapshot_id") or ""),
                str(target_set.get("name") or target_set.get("entity_type") or ""),
                tuple(sorted(target_identity.items())),
            )
            target_values = observation_rows.get(lookup_key) or {}
            context = output.setdefault(str(change.get("change_id") or ""), {})
            for role, target_field in roles.items():
                if str(target_field) in target_values:
                    context[f"threshold.{role}"] = target_values[str(target_field)]
    return output


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


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def replay_event_rule_definitions_against_changes(
    definitions: list[EventRuleDefinition],
    changes: list[dict[str, Any]],
    *,
    include_active_collisions: bool = True,
    active_definitions: list[EventRuleDefinition] | None = None,
    candidate_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay candidate rules against immutable in-memory Change evidence."""

    candidate_rules = _candidate_rules(definitions)
    if active_definitions is None and include_active_collisions:
        active_definitions = [
            _active_definition(row)
            for row in EventRuleRepository().list_versions(
                status="active",
                limit=None,
            )
        ]
    active_definitions = list(active_definitions or [])
    active_rules = _candidate_rules(active_definitions)
    candidate_rule_ids = {rule.rule_id for rule in candidate_rules}
    candidate_logical_keys = {
        _logical_key(definition) for definition in definitions
    }
    active_logical_keys = {
        definition.rule_id: _logical_key(definition)
        for definition in active_definitions
    }
    replacement_rule_ids = sorted(
        {
            rule.rule_id
            for rule in active_rules
            if rule.rule_id in candidate_rule_ids
            or active_logical_keys.get(rule.rule_id)
            in candidate_logical_keys
        }
    )
    collision_ruleset = [
        rule
        for rule in active_rules
        if rule.rule_id not in replacement_rule_ids
    ]

    matched_change_count = 0
    collision_count = 0
    event_types: Counter[str] = Counter()
    collision_rules: Counter[str] = Counter()
    change_shapes: Counter[str] = Counter()
    matched_change_shapes: Counter[str] = Counter()
    entity_types: Counter[str] = Counter()
    top_devices: Counter[str] = Counter()
    unexpected_matches: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    timestamps: list[datetime] = []
    projected_change_count = 0
    for change in changes:
        payload, projected_set = _project_candidate_change(change, candidate_contract)
        if projected_set:
            projected_change_count += 1
        shape = (
            f"{payload.get('change_type') or 'unknown'}"
            f"|{payload.get('observation_type') or payload.get('entity_type') or 'unknown'}"
        )
        change_shapes[shape] += 1
        run_id = str(payload.get("run_id") or "").strip()
        if run_id:
            run_ids.add(run_id)
        # A transactional or historical replay runs "now", so detected_at is
        # often the replay clock rather than the source evidence clock.
        observed_at = _timestamp(
            payload.get("observed_at") or payload.get("detected_at")
        )
        if observed_at is not None:
            timestamps.append(observed_at)
        candidates = matched_rules(payload, candidate_rules)
        if not candidates:
            continue
        matched_change_count += 1
        matched_change_shapes[shape] += 1
        for rule in candidates:
            event_types[rule.event_type] += 1
            collision_rules[rule.rule_id] += 1
        entity_types[str(payload.get("entity_type") or "unknown")] += 1
        if payload.get("device_id"):
            top_devices[str(payload["device_id"])] += 1
        active_matches = (
            matched_rules(payload, collision_ruleset)
            if collision_ruleset
            else []
        )
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
                        "candidate_rule_ids": [
                            rule.rule_id for rule in candidates
                        ],
                        "active_rule_ids": [
                            rule.rule_id for rule in active_matches
                        ],
                        "change": sanitize_change(payload),
                    }
                )

    oldest = min(timestamps) if timestamps else None
    newest = max(timestamps) if timestamps else None
    observed_window_days = (
        max((newest - oldest).total_seconds() / 86400.0, 0.0)
        if oldest is not None and newest is not None
        else 0.0
    )
    effective_window_days = max(observed_window_days, 1.0)
    event_count = sum(event_types.values())
    sampled_count = len(changes)
    estimate_reliable = observed_window_days >= 1.0 and len(run_ids) >= 2
    return {
        "success": collision_count == 0,
        "rule_count": len(definitions),
        "sampled_change_count": sampled_count,
        "total_change_count": sampled_count,
        "matched_change_count": matched_change_count,
        "estimated_daily_event_count": round(
            event_count / effective_window_days,
            4,
        ),
        "estimated_daily_event_count_basis": (
            "observed_window"
            if observed_window_days >= 1.0
            else "one_day_floor"
        ),
        "estimated_daily_event_count_reliable": estimate_reliable,
        "event_count": event_count,
        "event_types": dict(event_types.most_common()),
        "matched_entity_types": dict(entity_types.most_common()),
        "top_devices": dict(top_devices.most_common(10)),
        "collision_count": collision_count,
        "collision_rules": dict(collision_rules.most_common()),
        "unexpected_matches": unexpected_matches,
        "replacement_rule_ids": replacement_rule_ids,
        "candidate_logical_keys": sorted(candidate_logical_keys),
        "representativeness": {
            "total_change_count": sampled_count,
            "sampled_change_count": sampled_count,
            "sample_coverage_rate": 1.0 if sampled_count else 0.0,
            "matched_change_count": matched_change_count,
            "match_rate": (
                round(matched_change_count / sampled_count, 4)
                if sampled_count
                else 0.0
            ),
            "window_days": round(observed_window_days, 4),
            "effective_estimation_window_days": round(
                effective_window_days,
                4,
            ),
            "window_start": oldest.isoformat() if oldest else None,
            "window_end": newest.isoformat() if newest else None,
            "change_shape_count": len(change_shapes),
            "matched_change_shape_count": len(matched_change_shapes),
            "change_shapes": dict(change_shapes.most_common(20)),
            "sparse_history": sampled_count < 10
            or len(change_shapes) < 2
            or len(run_ids) < 2
            or observed_window_days < 1.0,
        },
        "scope": {
            "mode": "provided_changes",
            "run_ids": sorted(run_ids),
            "run_id_count": len(run_ids),
            "sampled_run_ids": sorted(run_ids),
            "sampled_run_id_count": len(run_ids),
        },
        "candidate_projection": {
            "enabled": bool(candidate_contract),
            "projected_change_count": projected_change_count,
        },
    }


def replay_event_rule_definitions(
    definitions: list[EventRuleDefinition],
    *,
    limit: int = 1000,
    include_active_collisions: bool = True,
    run_ids: list[str] | None = None,
    candidate_contract: dict[str, Any] | None = None,
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
        snapshot_ids = {
            str(row.current_snapshot_id)
            for row in rows
            if str(row.current_snapshot_id or "").strip()
        }
        observations = (
            db.query(StateObservationRecord)
            .filter(StateObservationRecord.snapshot_id.in_(snapshot_ids))
            .all()
            if candidate_contract and snapshot_ids
            else []
        )
        raw_changes = [change_record_to_payload(row) for row in rows]
        threshold_contexts = _candidate_threshold_contexts(
            changes=raw_changes,
            observations=observations,
            contract=candidate_contract,
        )
        projected_change_count = 0
        threshold_context_count = 0
        for row, raw_change in zip(rows, raw_changes, strict=True):
            if str(row.run_id or "").strip():
                sampled_run_id_values.add(str(row.run_id).strip())
            if row.detected_at is not None:
                if oldest_detected_at is None or row.detected_at < oldest_detected_at:
                    oldest_detected_at = row.detected_at
                if newest_detected_at is None or row.detected_at > newest_detected_at:
                    newest_detected_at = row.detected_at
            change, projected_set = _project_candidate_change(
                raw_change,
                candidate_contract,
            )
            if projected_set:
                projected_change_count += 1
            threshold_context = threshold_contexts.get(str(row.id)) or {}
            if threshold_context:
                change["context"] = {
                    **dict(change.get("context") or {}),
                    **threshold_context,
                }
                threshold_context_count += 1
            shape = (
                f"{row.change_type or 'unknown'}"
                f"|{change.get('observation_type') or change.get('entity_type') or 'unknown'}"
            )
            change_shapes[shape] += 1
            candidates = matched_rules(change, candidate_rules)
            if not candidates:
                continue
            matched_change_count += 1
            matched_change_shapes[shape] += 1
            for rule in candidates:
                event_types[rule.event_type] += 1
                collision_rules[rule.rule_id] += 1
            entity_types[str(change.get("entity_type") or "unknown")] += 1
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
    window_days = 0.0
    if oldest_detected_at is not None and newest_detected_at is not None:
        window_days = max(
            0.0,
            (newest_detected_at - oldest_detected_at).total_seconds()
            / 86400.0,
        )
    effective_window_days = max(window_days, 1.0)
    event_count = sum(event_types.values())
    estimate_reliable = window_days >= 1.0 and len(sampled_run_ids) >= 2
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
        "window_days": round(window_days, 4),
        "effective_estimation_window_days": round(
            effective_window_days,
            4,
        ),
        "window_start": oldest_detected_at.isoformat() if oldest_detected_at else None,
        "window_end": newest_detected_at.isoformat() if newest_detected_at else None,
        "change_shape_count": len(change_shapes),
        "matched_change_shape_count": len(matched_change_shapes),
        "change_shapes": dict(change_shapes.most_common(20)),
        "sparse_history": total_change_count < 10
        or len(change_shapes) < 2
        or len(sampled_run_ids) < 2
        or window_days < 1.0,
    }
    return {
        "success": collision_count == 0,
        "rule_count": len(definitions),
        "sampled_change_count": sampled_count,
        "total_change_count": total_change_count,
        "matched_change_count": matched_change_count,
        "estimated_daily_event_count": round(
            event_count / effective_window_days,
            4,
        ),
        "estimated_daily_event_count_basis": (
            "observed_window" if window_days >= 1.0 else "one_day_floor"
        ),
        "estimated_daily_event_count_reliable": estimate_reliable,
        "event_count": event_count,
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
        "candidate_projection": {
            "enabled": bool(candidate_contract),
            "projected_change_count": projected_change_count,
            "threshold_context_count": threshold_context_count,
        },
    }
