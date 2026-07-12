# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Detect field-level changes between structured patrol snapshots."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.parser.key_contract import EntityKeyError, index_records
from src.core.patrol.change_event_repository import (
    ChangeEventRepository,
    change_idempotency_key,
    deterministic_change_id,
)
from src.core.patrol.change_policy import compare_with_policy, policy_from_dict
from src.core.patrol.change_policy_repository import ChangePolicyRepository
from src.core.patrol.command_splitter import canonicalize_command
from src.core.patrol.domain import SnapshotRecord
from src.core.patrol.repository import (
    AnalysisAttemptFencedError,
    AnalysisAttemptInProgressError,
    AnalysisLeaseHeartbeat,
    PatrolRepository,
)
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
    dimensions: dict[str, Any] | None = None
    previous_snapshot_id: str | None = None
    current_snapshot_id: str | None = None
    previous_observation_id: str | None = None
    current_observation_id: str | None = None
    source: str = "patrol"
    analysis_revision: str = "original"
    rule_bundle_digest: str | None = None
    old_number: float | None = None
    new_number: float | None = None
    detected_at: str = ""
    confidence: float = 1.0
    observation_type: str | None = None
    change_policy_id: str | None = None
    change_policy_version_id: str | None = None
    comparison_type: str | None = None
    decision_reason: str | None = None
    policy_snapshot: dict[str, Any] | None = None
    observed_at: str | None = None
    processed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["old"] = data.pop("old_value")
        data["new"] = data.pop("new_value")
        return data


def _new_change(**values: Any) -> NetworkChange:
    change = NetworkChange(change_id="", **values)
    return replace(change, change_id=deterministic_change_id(change))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _observed_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _equivalent_values(field: str, old: Any, new: Any) -> bool:
    old_text = _stringify(old).strip()
    new_text = _stringify(new).strip()
    normalized_field = str(field).lower()
    if normalized_field in {"status", "protocol", "state"}:
        return old_text.casefold() == new_text.casefold()
    if normalized_field in {"ip", "ip_address", "address"}:
        try:
            old_interface = ipaddress.ip_interface(old_text)
            new_interface = ipaddress.ip_interface(new_text)
        except ValueError:
            return old_text == new_text
        if old_interface.ip != new_interface.ip:
            return False
        old_has_prefix = "/" in old_text
        new_has_prefix = "/" in new_text
        return (
            not old_has_prefix
            or not new_has_prefix
            or old_interface.network.prefixlen == new_interface.network.prefixlen
        )
    return old_text == new_text


def _observation_type(observation: dict[str, Any]) -> str:
    if observation.get("observation_type"):
        return str(observation["observation_type"])
    metadata = observation.get("metadata_json")
    if isinstance(metadata, dict):
        for key in ("observation_type", "semantic_observation_type"):
            if metadata.get(key):
                return str(metadata[key])
        semantics = metadata.get("semantics")
        if isinstance(semantics, dict) and semantics.get("observation_type"):
            return str(semantics["observation_type"])
    return str(observation.get("field_name") or "")


class ChangePolicyResolver:
    def __init__(self, policies: list[dict[str, Any]] | None = None) -> None:
        self._repo = ChangePolicyRepository()
        self._frozen = policies is not None
        self._cache: dict[str, dict[str, Any] | None] = {
            str(policy.get("observation_type") or ""): policy
            for policy in policies or []
            if policy.get("observation_type")
        }

    def resolve(self, observation_type: str) -> dict[str, Any] | None:
        if not observation_type:
            return None
        if observation_type not in self._cache and not self._frozen:
            self._cache[observation_type] = self._repo.get_active_policy(observation_type)
        return self._cache.get(observation_type)


def _policy_compare_result(
    *,
    resolver: ChangePolicyResolver,
    current: dict[str, Any],
    old: Any,
    new: Any,
) -> dict[str, Any] | None:
    observation_type = _observation_type(current)
    active = resolver.resolve(observation_type)
    if not active:
        return None
    policy_payload = active.get("policy")
    if not isinstance(policy_payload, dict):
        return None
    result = compare_with_policy(
        policy_from_dict(policy_payload),
        old=old,
        new=new,
        field=str(current.get("field_name") or ""),
    )
    result["policy_id"] = active.get("policy_id")
    result["policy_version_id"] = active.get("version_id")
    result["observation_type"] = observation_type
    result["comparison_type"] = policy_payload.get("compare_mode")
    result["decision_reason"] = (
        "change_policy_matched" if result.get("changed") else "change_policy_suppressed"
    )
    result["policy_snapshot"] = policy_payload
    return result


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
            observation.get("observation_type")
            or str(observation.get("field_name") or "").strip().lower(),
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _deduplicate_current_observations(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest_by_key: dict[str, dict[str, Any]] = {}
    for item in observations:
        key = _state_key(item)
        existing = latest_by_key.get(key)
        item_order = (
            _observed_datetime(item.get("observed_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            str(item.get("fact_order_key") or ""),
        )
        existing_order = (
            (
                _observed_datetime(existing.get("observed_at"))
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
            str(existing.get("fact_order_key") or ""),
        ) if existing else None
        if existing_order is None or item_order > existing_order:
            latest_by_key[key] = item
    return list(latest_by_key.values())


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


def _collection_identity(observation: dict[str, Any]) -> tuple[str, str]:
    return (
        str(observation.get("device_id") or ""),
        canonicalize_command(str(observation.get("canonical_command") or "")),
    )


def _aggregate_collection_results(
    results: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in results:
        key = (
            str(item.get("device_id") or ""),
            canonicalize_command(str(item.get("canonical_command") or "")),
        )
        grouped.setdefault(key, []).append(item)
    aggregated: dict[tuple[str, str], dict[str, Any]] = {}
    for key, items in grouped.items():
        ordered = sorted(
            items,
            key=lambda item: (
                _observed_datetime(item.get("observed_at"))
                or datetime.min.replace(tzinfo=timezone.utc),
                str(item.get("execution_item_id") or ""),
            ),
        )
        representative = dict(ordered[-1])
        representative["authoritative_for_deletion"] = bool(ordered) and all(
            item.get("authoritative_for_deletion") is True for item in ordered
        )
        representative["execution_item_ids"] = [
            str(item.get("execution_item_id") or "") for item in ordered
        ]
        representative["sample_count"] = len(ordered)
        aggregated[key] = representative
    return aggregated


def _entity_dimensions(
    entity_key: str,
    primary_keys: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    if not primary_keys or entity_key == "__singleton__":
        return {}
    try:
        values = json.loads(entity_key)
    except (TypeError, ValueError):
        values = []
    if not isinstance(values, list):
        values = [values]
    return {
        str(key): values[index] if index < len(values) else None
        for index, key in enumerate(primary_keys)
    }


def _observation_dimensions(observation: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(observation.get("metadata_json") or {})
    explicit = metadata.get("dimensions")
    if isinstance(explicit, dict):
        return dict(explicit)
    return _entity_dimensions(
        str(observation.get("entity_key") or ""),
        tuple(str(key) for key in metadata.get("primary_keys") or ()),
    )


def _observation_to_change(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    run_id: str,
    change_type: str,
    detected_at: str,
    policy_result: dict[str, Any] | None = None,
    decision_reason: str | None = None,
) -> NetworkChange:
    basis = current or previous or {}
    old = previous.get("value_json") if previous else None
    new = current.get("value_json") if current else None
    return _new_change(
        run_id=run_id,
        device_id=basis.get("device_id"),
        command=basis.get("canonical_command"),
        entity_type=str(basis.get("entity_type") or "record"),
        entity_key=str(basis.get("entity_key") or ""),
        dimensions=_observation_dimensions(basis),
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
        observation_type=_observation_type(basis),
        change_policy_id=(policy_result or {}).get("policy_id"),
        change_policy_version_id=(policy_result or {}).get("policy_version_id"),
        comparison_type=(policy_result or {}).get("comparison_type") or "exact_diff",
        decision_reason=decision_reason
        or (policy_result or {}).get("decision_reason")
        or change_type,
        policy_snapshot=(policy_result or {}).get("policy_snapshot"),
        observed_at=str(basis.get("observed_at") or detected_at),
        processed_at=detected_at,
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
            _new_change(
                run_id=run_id,
                device_id=device_id,
                command=command,
                entity_type=entity_type,
                entity_key=key,
                dimensions=_entity_dimensions(key, primary_keys),
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
            _new_change(
                run_id=run_id,
                device_id=device_id,
                command=command,
                entity_type=entity_type,
                entity_key=key,
                dimensions=_entity_dimensions(key, primary_keys),
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
                _new_change(
                    run_id=run_id,
                    device_id=device_id,
                    command=command,
                    entity_type=entity_type,
                    entity_key=key,
                    dimensions=_entity_dimensions(key, primary_keys),
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
    if (
        previous.parse_status == "raw_hashed"
        or current.parse_status == "raw_hashed"
        or previous.structured_json is None
        or current.structured_json is None
    ):
        if previous.raw_text_hash == current.raw_text_hash:
            return []
        return [
            _new_change(
                run_id=current.run_id,
                device_id=current.device_id,
                command=current.command_canonical,
                entity_type="raw_command_output",
                entity_key=current.command_canonical,
                dimensions={"command": current.command_canonical},
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
        resolved_entity_type, resolved_primary_keys = _snapshot_key_contract(previous, current)
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


def save_changes(
    changes: list[NetworkChange],
    *,
    derived_observations: list[Any] | None = None,
    decisions: list[dict[str, Any]] | None = None,
) -> int:
    return ChangeEventRepository().save_changes(
        changes,
        derived_observations=derived_observations,
        decisions=decisions,
    )


def _change_decision(
    *,
    run_id: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    decision: str,
    reason: str,
    policy_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    basis = current or previous or {}
    return {
        "run_id": run_id,
        "device_id": basis.get("device_id"),
        "canonical_command": basis.get("canonical_command"),
        "entity_type": basis.get("entity_type"),
        "entity_key": basis.get("entity_key"),
        "field": basis.get("field_name"),
        "observation_type": _observation_type(basis),
        "previous_observation_id": previous.get("id") if previous else None,
        "current_observation_id": current.get("id") if current else None,
        "decision": decision,
        "reason": reason,
        "change_policy_id": (policy_result or {}).get("policy_id"),
        "change_policy_version_id": (policy_result or {}).get("policy_version_id"),
        "comparison_type": (policy_result or {}).get("comparison_type") or "presence",
        "old": previous.get("value_json") if previous else None,
        "new": current.get("value_json") if current else None,
        "observed_at": basis.get("observed_at"),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        if decision == "suppressed"
        else None,
        "metadata": {"policy_snapshot": (policy_result or {}).get("policy_snapshot")},
    }


def detect_changes_from_params(params: dict[str, Any]) -> dict[str, Any]:
    entity_type = params.get("entity_type") or "record"
    primary_keys = list(params.get("primary_keys") or []) if "primary_keys" in params else None
    persist = bool(params.get("persist", True))
    use_versioned = bool(params.get("use_versioned_parser_results", False))
    use_state_observations = bool(params.get("use_state_observations", False))

    if params.get("current_run_id") and use_state_observations:
        device_ids = params.get("device_ids")
        if not device_ids and params.get("device_id"):
            device_ids = [str(params["device_id"])]
        kwargs: dict[str, Any] = {}
        if device_ids:
            kwargs["device_ids"] = device_ids
        if "missing_confirmations" in params:
            kwargs["missing_confirmations"] = int(params["missing_confirmations"])
        if "analysis_revision" in params:
            kwargs["analysis_revision"] = str(params.get("analysis_revision") or "original")
        return detect_observation_changes_against_history(
            current_run_id=str(params["current_run_id"]),
            previous_run_id=(
                str(params["previous_run_id"]) if params.get("previous_run_id") else None
            ),
            persist=persist,
            **kwargs,
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
                versioned = detect_versioned_changes(previous, current) if use_versioned else None
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

    changes = [replace(change, change_id=deterministic_change_id(change)) for change in changes]
    persisted_change_count = save_changes(changes) if persist else 0
    if persist:
        canonical_ids = ChangeEventRepository().canonical_change_ids(changes)
        changes = [
            replace(
                change,
                change_id=canonical_ids.get(change_idempotency_key(change), change.change_id),
            )
            for change in changes
        ]
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
    device_ids: list[str] | None = None,
    persist: bool = True,
    missing_confirmations: int = 2,
    analysis_revision: str = "original",
) -> dict[str, Any]:
    patrol_repository = PatrolRepository()
    state_repository = StateRepository()
    if device_ids:
        input_observations = state_repository.list_run_observations(
            current_run_id, device_ids=device_ids
        )
    else:
        input_observations = state_repository.list_run_observations(current_run_id)
    if previous_run_id:
        try:
            prior_observations = (
                state_repository.list_observations_for_runs(
                    [previous_run_id], device_ids=device_ids, baseline_only=True
                )
                if device_ids
                else state_repository.list_observations_for_runs(
                    [previous_run_id], baseline_only=True
                )
            )
        except TypeError:
            prior_observations = (
                state_repository.list_observations_for_runs(
                    [previous_run_id], device_ids=device_ids
                )
                if device_ids
                else state_repository.list_observations_for_runs([previous_run_id])
            )
    else:
        prior_observations = state_repository.list_prior_observations_for_run(current_run_id)
    input_revision_digest = hashlib.sha256(
        json.dumps(
            {
                "current": [
                    [item.get("id"), item.get("snapshot_id"), item.get("observed_at")]
                    for item in sorted(input_observations, key=lambda row: str(row.get("id") or ""))
                ],
                "prior": [
                    [item.get("id"), item.get("snapshot_id"), item.get("observed_at")]
                    for item in sorted(prior_observations, key=lambda row: str(row.get("id") or ""))
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    if not persist:
        return _detect_observation_changes_against_history_impl(
            current_run_id=current_run_id,
            previous_run_id=previous_run_id,
            device_ids=device_ids,
            persist=False,
            missing_confirmations=missing_confirmations,
            current_observations=input_observations,
            prior_observations=prior_observations,
            analysis_revision=analysis_revision,
        )
    frozen_policies = ChangePolicyRepository().list_active_policies()
    frozen_policies = json.loads(json.dumps(frozen_policies, default=str))
    scope = {
        "device_ids": sorted({str(item) for item in device_ids or []}),
        "full_run": not bool(device_ids),
        "previous_run_id": previous_run_id,
        "missing_confirmations": max(1, int(missing_confirmations)),
        "persist": bool(persist),
        "input_revision_digest": input_revision_digest,
        "analysis_revision": analysis_revision,
    }
    attempt = patrol_repository.begin_analysis_attempt(
        run_id=current_run_id,
        stage="change_detection",
        scope=scope,
        rule_bundle={"change_policies": frozen_policies},
    )
    if attempt["reused"]:
        persisted = ChangeEventRepository().list_changes(run_id=current_run_id)
        persisted = [
            item
            for item in persisted
            if str(item.get("analysis_revision") or "original") == analysis_revision
            and str(item.get("rule_bundle_digest") or "")
            == str(attempt["rule_bundle_digest"] or "")
        ]
        if device_ids:
            allowed = {str(item) for item in device_ids}
            persisted = [item for item in persisted if str(item.get("device_id")) in allowed]
        return {
            **dict(attempt["result"] or {}),
            "changes": persisted,
            "change_count": len(persisted),
            "reused_analysis_attempt": True,
        }
    if attempt["in_progress"]:
        raise AnalysisAttemptInProgressError(
            f"change detection already running for {current_run_id}"
        )
    heartbeat = AnalysisLeaseHeartbeat(patrol_repository, attempt)
    updates_run_status = not bool(device_ids)
    if updates_run_status:
        try:
            patrol_repository.update_analysis_status(
                current_run_id,
                analysis_status="detecting",
                baseline_eligible=False,
                metadata={"change_policy_bundle_digest": attempt["rule_bundle_digest"]},
            )
        except ValueError:
            pass
    heartbeat.start()
    try:
        result = _detect_observation_changes_against_history_impl(
            current_run_id=current_run_id,
            previous_run_id=previous_run_id,
            device_ids=device_ids,
            persist=persist,
            missing_confirmations=missing_confirmations,
            policy_bundle=list(attempt["rule_bundle"].get("change_policies") or []),
            current_observations=input_observations,
            prior_observations=prior_observations,
            heartbeat=heartbeat,
            analysis_revision=analysis_revision,
            rule_bundle_digest=str(attempt["rule_bundle_digest"]),
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
                    current_run_id,
                    analysis_status="failed",
                    baseline_eligible=False,
                    metadata={
                        "analysis_failure_stage": "change_detection",
                        "analysis_failure": str(exc),
                    },
                )
            except ValueError:
                pass
        raise


def detect_historical_backfill_changes(
    *,
    run_id: str,
    backfill_job_id: str,
) -> dict[str, Any]:
    """Derive only facts introduced by one approved historical backfill."""
    state_repository = StateRepository()
    current = [
        item
        for item in state_repository.list_run_observations(run_id)
        if (item.get("metadata_json") or {}).get("backfill_job_id") == backfill_job_id
    ]
    state_keys = {str(item.get("state_key") or "") for item in current}
    prior = [
        item
        for item in state_repository.list_prior_observations_for_run(run_id)
        if str(item.get("state_key") or "") in state_keys
    ]
    policies = ChangePolicyRepository().list_active_policies()
    bundle_digest = hashlib.sha256(
        json.dumps(policies, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return _detect_observation_changes_against_history_impl(
        current_run_id=run_id,
        persist=True,
        missing_confirmations=2,
        policy_bundle=policies,
        current_observations=current,
        prior_observations=prior,
        analysis_revision=f"historical_backfill:{backfill_job_id}",
        rule_bundle_digest=bundle_digest,
    )


def _detect_observation_changes_against_history_impl(
    *,
    current_run_id: str,
    previous_run_id: str | None = None,
    device_ids: list[str] | None = None,
    persist: bool = True,
    missing_confirmations: int = 2,
    policy_bundle: list[dict[str, Any]] | None = None,
    current_observations: list[dict[str, Any]] | None = None,
    prior_observations: list[dict[str, Any]] | None = None,
    heartbeat: Any | None = None,
    analysis_revision: str = "original",
    rule_bundle_digest: str | None = None,
) -> dict[str, Any]:
    repository = StateRepository()
    patrol_repository = PatrolRepository()
    run_context = patrol_repository.get_run(current_run_id)
    if run_context and run_context.get("run_purpose") == "baseline":
        return {
            "success": True,
            "current_run_id": current_run_id,
            "previous_run_id": previous_run_id,
            "device_ids": device_ids or [],
            "compared_observations": 0,
            "baseline_observations": 0,
            "initial_observations": 0,
            "added_observations": 0,
            "deleted_observations": 0,
            "missing_observations": 0,
            "reappeared_observations": 0,
            "authoritative_collection_count": len(
                patrol_repository.list_authoritative_collection_keys(current_run_id)
            ),
            "change_count": 0,
            "persisted_change_count": 0,
            "storage": "postgresql",
            "source": "state_observation",
            "run_purpose": "baseline",
            "changes": [],
        }
    changes: list[NetworkChange] = []
    derived_observations: list[Any] = []
    decisions: list[dict[str, Any]] = []
    compared_observations = 0
    baseline_observations = 0
    detected_at = _now()
    if current_observations is None:
        if device_ids:
            current_observations = repository.list_run_observations(
                current_run_id,
                device_ids=device_ids,
            )
        else:
            current_observations = repository.list_run_observations(current_run_id)
    current_observations = _deduplicate_current_observations(current_observations)
    current_by_key = {_state_key(item): item for item in current_observations}
    collection_results = patrol_repository.list_collection_results(current_run_id)
    current_collection_results = _aggregate_collection_results(collection_results)
    authoritative_collections = {
        key
        for key, item in current_collection_results.items()
        if item.get("authoritative_for_deletion") is True
    }
    prior_by_key: dict[str, dict[str, Any]] = {}
    policy_resolver = ChangePolicyResolver(policy_bundle)
    if prior_observations is not None:
        pass
    elif previous_run_id:
        try:
            prior_observations = (
                repository.list_observations_for_runs(
                    [previous_run_id],
                    device_ids=device_ids,
                    baseline_only=True,
                )
                if device_ids
                else repository.list_observations_for_runs([previous_run_id], baseline_only=True)
            )
        except TypeError:
            prior_observations = (
                repository.list_observations_for_runs([previous_run_id], device_ids=device_ids)
                if device_ids
                else repository.list_observations_for_runs([previous_run_id])
            )
    else:
        prior_observations = repository.list_prior_observations_for_run(current_run_id)
    prior_collections = {_collection_key(item) for item in prior_observations}
    for prior in prior_observations:
        prior_by_key.setdefault(_state_key(prior), prior)
    for index, current in enumerate(current_observations):
        if heartbeat is not None:
            heartbeat()
        previous = prior_by_key.get(_state_key(current))
        if previous:
            previous_at = _observed_datetime(previous.get("observed_at"))
            current_at = _observed_datetime(current.get("observed_at"))
            if previous_at and current_at and previous_at > current_at:
                previous = None
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
                changes.append(
                    _observation_to_change(
                        previous=None,
                        current=current,
                        run_id=current_run_id,
                        change_type="initial",
                        detected_at=detected_at,
                    )
                )
            continue
        if str(previous.get("observation_kind") or "state") == "tombstone":
            changes.append(
                _observation_to_change(
                    previous=previous,
                    current=current,
                    run_id=current_run_id,
                    change_type="reappeared",
                    detected_at=detected_at,
                    decision_reason="entity_reappeared_after_absence",
                )
            )
            continue
        compared_observations += 1
        old = previous.get("value_json")
        new = current.get("value_json")
        policy_result = _policy_compare_result(
            resolver=policy_resolver,
            current=current,
            old=old,
            new=new,
        )
        if policy_result is not None:
            if not policy_result.get("changed"):
                decisions.append(
                    _change_decision(
                        run_id=current_run_id,
                        previous=previous,
                        current=current,
                        decision="suppressed",
                        reason=str(
                            policy_result.get("decision_reason") or "change_policy_suppressed"
                        ),
                        policy_result=policy_result,
                    )
                )
                continue
        elif _equivalent_values(str(current.get("field_name") or ""), old, new):
            continue
        changes.append(
            _observation_to_change(
                previous=previous,
                current=current,
                run_id=current_run_id,
                change_type="modified",
                detected_at=detected_at,
                policy_result=policy_result,
            )
        )
        if policy_result is not None:
            decisions.append(
                _change_decision(
                    run_id=current_run_id,
                    previous=previous,
                    current=current,
                    decision="emitted",
                    reason=str(policy_result.get("decision_reason") or "change_emitted"),
                    policy_result=policy_result,
                )
            )
    for index, (key, previous) in enumerate(prior_by_key.items()):
        if heartbeat is not None:
            heartbeat()
        if key in current_by_key:
            continue
        collection_identity = _collection_identity(previous)
        if collection_identity not in authoritative_collections:
            continue
        metadata = dict(previous.get("metadata_json") or {})
        prior_missing_count = int(metadata.get("missing_count") or 0)
        prior_presence = str(metadata.get("presence_state") or "present")
        if prior_presence == "absent":
            continue
        missing_count = prior_missing_count + 1
        collection = current_collection_results[collection_identity]
        snapshot_id = str((collection.get("metadata_json") or {}).get("snapshot_id") or "")
        if not snapshot_id:
            continue
        tombstone = repository.build_absence_observation(
            previous,
            run_id=current_run_id,
            snapshot_id=snapshot_id,
            observed_at=collection.get("observed_at") or detected_at,
            missing_count=missing_count,
            missing_confirmations=max(1, int(missing_confirmations)),
        )
        derived_observations.append(tombstone)
        change_type = (
            "deleted" if missing_count >= max(1, int(missing_confirmations)) else "missing"
        )
        changes.append(
            _observation_to_change(
                previous=previous,
                current=tombstone.to_dict(),
                run_id=current_run_id,
                change_type=change_type,
                detected_at=detected_at,
                decision_reason=(
                    "confirmed_absent_after_complete_collections"
                    if change_type == "deleted"
                    else "missing_candidate_pending_confirmation"
                ),
            )
        )
        decisions.append(
            _change_decision(
                run_id=current_run_id,
                previous=previous,
                current=tombstone.to_dict(),
                decision="emitted",
                reason=(
                    "confirmed_absent_after_complete_collections"
                    if change_type == "deleted"
                    else "missing_candidate_pending_confirmation"
                ),
            )
        )

    changes = [
        replace(
            change,
            analysis_revision=analysis_revision,
            rule_bundle_digest=rule_bundle_digest,
        )
        for change in changes
    ]
    changes = [replace(change, change_id=deterministic_change_id(change)) for change in changes]
    for decision in decisions:
        decision["analysis_revision"] = analysis_revision
        decision["rule_bundle_digest"] = rule_bundle_digest
    persisted_change_count = (
        save_changes(
            changes,
            derived_observations=derived_observations,
            decisions=decisions,
        )
        if persist
        else 0
    )
    if persist:
        canonical_ids = ChangeEventRepository().canonical_change_ids(changes)
        changes = [
            replace(
                change,
                change_id=canonical_ids.get(change_idempotency_key(change), change.change_id),
            )
            for change in changes
        ]
    return {
        "success": True,
        "current_run_id": current_run_id,
        "previous_run_id": previous_run_id,
        "device_ids": device_ids or [],
        "compared_observations": compared_observations,
        "baseline_observations": baseline_observations,
        "initial_observations": sum(1 for change in changes if change.change_type == "initial"),
        "added_observations": sum(1 for change in changes if change.change_type == "added"),
        "deleted_observations": sum(1 for change in changes if change.change_type == "deleted"),
        "missing_observations": sum(1 for change in changes if change.change_type == "missing"),
        "reappeared_observations": sum(
            1 for change in changes if change.change_type == "reappeared"
        ),
        "authoritative_collection_count": len(authoritative_collections),
        "change_count": len(changes),
        "persisted_change_count": persisted_change_count,
        "change_decision_count": len(decisions),
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
            detect_versioned_changes(previous, current) if use_versioned_parser_results else None
        )
        changes.extend(
            versioned
            if versioned is not None
            else detect_changes_between_snapshots(previous, current)
        )

    changes = [replace(change, change_id=deterministic_change_id(change)) for change in changes]
    persisted_change_count = save_changes(changes) if persist else 0
    if persist:
        canonical_ids = ChangeEventRepository().canonical_change_ids(changes)
        changes = [
            replace(
                change,
                change_id=canonical_ids.get(change_idempotency_key(change), change.change_id),
            )
            for change in changes
        ]
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
