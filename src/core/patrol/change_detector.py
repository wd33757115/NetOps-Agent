# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Detect field-level changes between structured patrol snapshots."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from src.core.parser.key_contract import EntityKeyError, index_records
from src.core.patrol.command_splitter import canonicalize_command
from src.core.patrol.change_event_repository import ChangeEventRepository
from src.core.patrol.domain import SnapshotRecord
from src.core.patrol.repository import PatrolRepository
from src.core.state.repository import StateRepository


@dataclass(frozen=True)
class NetworkChange:
    change_id: str
    run_id: str | None
    device_id: str | None
    command: str | None
    entity_type: str
    entity_key: str
    field: str
    old_value: Any
    new_value: Any
    change_type: str
    previous_snapshot_id: str | None = None
    current_snapshot_id: str | None = None
    previous_observation_id: str | None = None
    current_observation_id: str | None = None
    source: str = "patrol"
    old_number: float | None = None
    new_number: float | None = None
    detected_at: str = ""
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["old"] = data.pop("old_value")
        data["new"] = data.pop("new_value")
        return data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _equivalent_values(field: str, old: Any, new: Any) -> bool:
    old_text = _stringify(old).strip()
    new_text = _stringify(new).strip()
    if str(field).lower() in {"status", "protocol", "state"}:
        return old_text.casefold() == new_text.casefold()
    return old_text == new_text


def _normalize_records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return [{"value": value}]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%"))
        except ValueError:
            return None
    return None


def _state_key(observation: dict[str, Any]) -> str:
    return json.dumps(
        [
            observation.get("device_id"),
            observation.get("entity_type"),
            observation.get("entity_key"),
            observation.get("field_name"),
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _collection_key(observation: dict[str, Any]) -> str:
    return json.dumps(
        [
            observation.get("device_id"),
            observation.get("canonical_command"),
            observation.get("entity_type"),
            observation.get("field_name"),
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _observation_to_change(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    run_id: str,
    change_type: str,
    detected_at: str,
) -> NetworkChange:
    basis = current or previous or {}
    old = previous.get("value_json") if previous else None
    new = current.get("value_json") if current else None
    return NetworkChange(
        change_id=str(uuid.uuid4()),
        run_id=run_id,
        device_id=basis.get("device_id"),
        command=basis.get("canonical_command"),
        entity_type=str(basis.get("entity_type") or "record"),
        entity_key=str(basis.get("entity_key") or ""),
        field=str(basis.get("field_name") or ""),
        old_value=old,
        new_value=new,
        change_type=change_type,
        previous_snapshot_id=previous.get("snapshot_id") if previous else None,
        current_snapshot_id=current.get("snapshot_id") if current else None,
        previous_observation_id=previous.get("id") if previous else None,
        current_observation_id=current.get("id") if current else None,
        source="state_observation",
        old_number=(
            previous.get("value_number")
            if previous and previous.get("value_number") is not None
            else _number(old)
        ),
        new_number=(
            current.get("value_number")
            if current and current.get("value_number") is not None
            else _number(new)
        ),
        detected_at=detected_at,
        confidence=min(
            float(previous.get("confidence") or 1.0) if previous else 1.0,
            float(current.get("confidence") or 1.0) if current else 1.0,
        ),
    )


def detect_changes_between_values(
    previous: Any,
    current: Any,
    *,
    entity_type: str = "record",
    primary_keys: list[str] | None = None,
    run_id: str | None = None,
    device_id: str | None = None,
    command: str | None = None,
    previous_snapshot_id: str | None = None,
    current_snapshot_id: str | None = None,
) -> list[NetworkChange]:
    detected_at = _now()
    prev_rows = _normalize_records(previous)
    curr_rows = _normalize_records(current)
    primary_keys = primary_keys or []
    prev_map = index_records(prev_rows, primary_keys)
    curr_map = index_records(curr_rows, primary_keys)
    changes: list[NetworkChange] = []

    for key in sorted(set(prev_map) - set(curr_map)):
        changes.append(
            NetworkChange(
                change_id=str(uuid.uuid4()),
                run_id=run_id,
                device_id=device_id,
                command=command,
                entity_type=entity_type,
                entity_key=key,
                field="__record__",
                old_value=prev_map[key],
                new_value=None,
                change_type="deleted",
                previous_snapshot_id=previous_snapshot_id,
                current_snapshot_id=current_snapshot_id,
                detected_at=detected_at,
            )
        )

    for key in sorted(set(curr_map) - set(prev_map)):
        changes.append(
            NetworkChange(
                change_id=str(uuid.uuid4()),
                run_id=run_id,
                device_id=device_id,
                command=command,
                entity_type=entity_type,
                entity_key=key,
                field="__record__",
                old_value=None,
                new_value=curr_map[key],
                change_type="added",
                previous_snapshot_id=previous_snapshot_id,
                current_snapshot_id=current_snapshot_id,
                detected_at=detected_at,
            )
        )

    for key in sorted(set(prev_map) & set(curr_map)):
        prev = prev_map[key]
        curr = curr_map[key]
        for field in sorted(set(prev) | set(curr)):
            if field in {"状态"}:
                continue
            old = prev.get(field)
            new = curr.get(field)
            if _equivalent_values(str(field), old, new):
                continue
            changes.append(
                NetworkChange(
                    change_id=str(uuid.uuid4()),
                    run_id=run_id,
                    device_id=device_id,
                    command=command,
                    entity_type=entity_type,
                    entity_key=key,
                    field=str(field),
                    old_value=old,
                    new_value=new,
                    change_type="modified",
                    previous_snapshot_id=previous_snapshot_id,
                    current_snapshot_id=current_snapshot_id,
                    detected_at=detected_at,
                )
            )
    return changes


def detect_changes_between_snapshots(
    previous: SnapshotRecord,
    current: SnapshotRecord,
    *,
    entity_type: str = "record",
    primary_keys: list[str] | None = None,
) -> list[NetworkChange]:
    if previous.structured_json is None or current.structured_json is None:
        if previous.raw_text_hash == current.raw_text_hash:
            return []
        return [
            NetworkChange(
                change_id=str(uuid.uuid4()),
                run_id=current.run_id,
                device_id=current.device_id,
                command=current.command_canonical,
                entity_type="raw_command_output",
                entity_key=current.command_canonical,
                field="raw_text_hash",
                old_value=previous.raw_text_hash,
                new_value=current.raw_text_hash,
                change_type="modified",
                previous_snapshot_id=previous.snapshot_id,
                current_snapshot_id=current.snapshot_id,
                detected_at=_now(),
                confidence=0.5,
            )
        ]
    resolved_entity_type = entity_type
    resolved_primary_keys = primary_keys
    if primary_keys is None:
        resolved_entity_type, resolved_primary_keys = _snapshot_key_contract(
            previous, current
        )
    return detect_changes_between_values(
        previous.structured_json,
        current.structured_json,
        entity_type=resolved_entity_type,
        primary_keys=resolved_primary_keys,
        run_id=current.run_id,
        device_id=current.device_id,
        command=current.command_canonical,
        previous_snapshot_id=previous.snapshot_id,
        current_snapshot_id=current.snapshot_id,
    )


def _snapshot_key_contract(
    previous: SnapshotRecord,
    current: SnapshotRecord,
) -> tuple[str, list[str]]:
    """Resolve and compare the immutable entity identity contract."""
    if not previous.parser_name or not current.parser_name:
        raise EntityKeyError(
            "structured snapshots require parser template metadata or explicit primary_keys"
        )
    try:
        from src.core.parser.repository import ParserRepository

        repository = ParserRepository()
        previous_template = repository.get_template(previous.parser_name)
        current_template = repository.get_template(current.parser_name)
    except Exception as exc:
        raise EntityKeyError(f"unable to resolve parser key contract: {exc}") from exc
    if not previous_template or not current_template:
        raise EntityKeyError("parser template metadata was not found")

    previous_validation = dict(previous_template.validation_json or {})
    current_validation = dict(current_template.validation_json or {})
    previous_contract = (
        str(previous_validation.get("entity_type") or "record"),
        tuple(previous_validation.get("primary_keys") or ()),
    )
    current_contract = (
        str(current_validation.get("entity_type") or "record"),
        tuple(current_validation.get("primary_keys") or ()),
    )
    if previous_contract != current_contract:
        raise EntityKeyError(
            "snapshot parser key contracts are incompatible: "
            f"{previous_contract!r} != {current_contract!r}"
        )
    return current_contract[0], list(current_contract[1])


def detect_versioned_changes(
    previous: SnapshotRecord,
    current: SnapshotRecord,
) -> list[NetworkChange] | None:
    """Compare append-only parse revisions only when both use one parser version."""
    try:
        from src.core.parser.repository import ParserRepository

        repository = ParserRepository()
        previous_result = repository.get_latest_parse_result(previous.snapshot_id)
        current_result = repository.get_latest_parse_result(current.snapshot_id)
    except Exception:
        return None
    if not previous_result or not current_result:
        return []
    if (
        previous_result["template_id"] != current_result["template_id"]
        or previous_result["template_version"] != current_result["template_version"]
    ):
        return []
    return detect_changes_between_values(
        previous_result["structured_json"],
        current_result["structured_json"],
        entity_type=current_result["entity_type"],
        primary_keys=current_result["primary_keys"],
        run_id=current.run_id,
        device_id=current.device_id,
        command=current.command_canonical,
        previous_snapshot_id=previous.snapshot_id,
        current_snapshot_id=current.snapshot_id,
    )


def save_changes(changes: list[NetworkChange]) -> int:
    return ChangeEventRepository().save_changes(changes)


def detect_changes_from_params(params: dict[str, Any]) -> dict[str, Any]:
    entity_type = params.get("entity_type") or "record"
    primary_keys = (
        list(params.get("primary_keys") or [])
        if "primary_keys" in params
        else None
    )
    persist = bool(params.get("persist", True))
    use_versioned = bool(params.get("use_versioned_parser_results", False))
    use_state_observations = bool(params.get("use_state_observations", False))

    if params.get("current_run_id") and use_state_observations:
        return detect_observation_changes_against_history(
            current_run_id=str(params["current_run_id"]),
            previous_run_id=(
                str(params["previous_run_id"])
                if params.get("previous_run_id")
                else None
            ),
            persist=persist,
        )
    if "previous_snapshot" in params or "current_snapshot" in params:
        changes = detect_changes_between_values(
            params.get("previous_snapshot"),
            params.get("current_snapshot"),
            entity_type=entity_type,
            primary_keys=primary_keys,
            device_id=params.get("device_id"),
            command=params.get("command"),
        )
    elif params.get("previous_snapshot_id") and params.get("current_snapshot_id"):
        repository = PatrolRepository()
        previous = repository.get_snapshot(str(params["previous_snapshot_id"]))
        current = repository.get_snapshot(str(params["current_snapshot_id"]))
        if not previous or not current:
            raise ValueError("snapshot not found")
        changes = detect_changes_between_snapshots(
            previous,
            current,
            entity_type=entity_type,
            primary_keys=primary_keys,
        )
    elif params.get("previous_run_id") and params.get("current_run_id"):
        repository = PatrolRepository()
        current_snapshots = repository.list_run_snapshots(
            str(params["current_run_id"]),
            device_id=params.get("device_id"),
        )
        changes = []
        for current in current_snapshots:
            previous = repository.find_snapshot(
                run_id=str(params["previous_run_id"]),
                device_id=current.device_id,
                command_canonical=canonicalize_command(current.command_canonical),
            )
            if previous:
                versioned = (
                    detect_versioned_changes(previous, current)
                    if use_versioned
                    else None
                )
                changes.extend(
                    versioned
                    if versioned is not None
                    else detect_changes_between_snapshots(
                        previous,
                        current,
                        entity_type=entity_type,
                        primary_keys=primary_keys,
                    )
                )
    elif params.get("current_run_id"):
        return detect_changes_against_history(
            current_run_id=str(params["current_run_id"]),
            device_id=params.get("device_id"),
            persist=persist,
            use_versioned_parser_results=use_versioned,
        )
    else:
        raise ValueError(
            "provide previous_snapshot/current_snapshot, snapshot IDs, "
            "previous_run_id/current_run_id, or current_run_id"
        )

    persisted_change_count = save_changes(changes) if persist else 0
    return {
        "success": True,
        "change_count": len(changes),
        "persisted_change_count": persisted_change_count,
        "storage": "postgresql",
        "changes": [c.to_dict() for c in changes],
    }


def detect_observation_changes_against_history(
    *,
    current_run_id: str,
    previous_run_id: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    repository = StateRepository()
    changes: list[NetworkChange] = []
    compared_observations = 0
    baseline_observations = 0
    detected_at = _now()
    current_observations = repository.list_run_observations(current_run_id)
    current_by_key = {_state_key(item): item for item in current_observations}
    current_collections = {_collection_key(item) for item in current_observations}
    prior_by_key: dict[str, dict[str, Any]] = {}
    prior_observations = (
        repository.list_observations_for_runs([previous_run_id])
        if previous_run_id
        else repository.list_prior_observations_for_run(current_run_id)
    )
    prior_collections = {_collection_key(item) for item in prior_observations}
    for prior in prior_observations:
        prior_by_key.setdefault(_state_key(prior), prior)
    for current in current_observations:
        previous = prior_by_key.get(_state_key(current))
        if previous is None:
            if _collection_key(current) in prior_collections:
                changes.append(
                    _observation_to_change(
                        previous=None,
                        current=current,
                        run_id=current_run_id,
                        change_type="added",
                        detected_at=detected_at,
                    )
                )
            else:
                baseline_observations += 1
            continue
        compared_observations += 1
        old = previous.get("value_json")
        new = current.get("value_json")
        if _equivalent_values(str(current.get("field_name") or ""), old, new):
            continue
        changes.append(
            _observation_to_change(
                previous=previous,
                current=current,
                run_id=current_run_id,
                change_type="modified",
                detected_at=detected_at,
            )
        )
    for key, previous in prior_by_key.items():
        if key in current_by_key:
            continue
        if _collection_key(previous) not in current_collections:
            continue
        changes.append(
            _observation_to_change(
                previous=previous,
                current=None,
                run_id=current_run_id,
                change_type="deleted",
                detected_at=detected_at,
            )
        )

    persisted_change_count = save_changes(changes) if persist else 0
    return {
        "success": True,
        "current_run_id": current_run_id,
        "previous_run_id": previous_run_id,
        "compared_observations": compared_observations,
        "baseline_observations": baseline_observations,
        "added_observations": sum(1 for change in changes if change.change_type == "added"),
        "deleted_observations": sum(
            1 for change in changes if change.change_type == "deleted"
        ),
        "change_count": len(changes),
        "persisted_change_count": persisted_change_count,
        "storage": "postgresql",
        "source": "state_observation",
        "changes": [change.to_dict() for change in changes],
    }


def detect_changes_against_history(
    storage_reference: str | None = None,
    *,
    current_run_id: str,
    device_id: str | None = None,
    persist: bool = True,
    use_versioned_parser_results: bool = False,
) -> dict[str, Any]:
    """Compare every current snapshot with its latest prior snapshot."""
    del storage_reference
    repository = PatrolRepository()
    changes: list[NetworkChange] = []
    compared_snapshots = 0
    baseline_snapshots = 0
    for current in repository.list_run_snapshots(current_run_id, device_id=device_id):
        previous = repository.find_previous_snapshot(
            current_run_id=current_run_id,
            device_id=current.device_id,
            command_canonical=current.command_canonical,
        )
        if not previous:
            baseline_snapshots += 1
            continue
        compared_snapshots += 1
        versioned = (
            detect_versioned_changes(previous, current)
            if use_versioned_parser_results
            else None
        )
        changes.extend(
            versioned
            if versioned is not None
            else detect_changes_between_snapshots(previous, current)
        )

    persisted_change_count = save_changes(changes) if persist else 0
    return {
        "success": True,
        "current_run_id": current_run_id,
        "compared_snapshots": compared_snapshots,
        "baseline_snapshots": baseline_snapshots,
        "change_count": len(changes),
        "persisted_change_count": persisted_change_count,
        "storage": "postgresql",
        "changes": [change.to_dict() for change in changes],
    }
