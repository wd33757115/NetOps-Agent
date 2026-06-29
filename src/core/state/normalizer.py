"""Normalize structured parser output into stable field-level observations."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from src.core.observation import ObservationContractRepository
from src.core.parser.key_contract import EntityKeyError, build_entity_key
from src.core.parser.repository import ParserRepository
from src.core.state.domain import StateObservation
from src.core.state.repository import StateRepository


DEFAULT_IGNORE_FIELDS = {"uptime"}
EMPTY_PLACEHOLDERS = {"", "-", "--", "n/a", "na", "none", "null"}
PLACEHOLDER_NORMALIZED_FIELDS = {"description"}


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return [{"value": value}]


def _value_parts(value: Any) -> tuple[str | None, float | None, str]:
    if value is None:
        return None, None, "null"
    if isinstance(value, bool):
        return str(value).lower(), float(int(value)), "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value), float(value), "number"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True), None, "json"
    text = str(value)
    try:
        number = float(text.strip().rstrip("%"))
    except (TypeError, ValueError):
        number = None
    return text, number, "string"


def _normalize_observation_value(field_name: str, value: Any) -> Any:
    field = str(field_name).strip().lower()
    if isinstance(value, str):
        text = value.strip()
        if field in PLACEHOLDER_NORMALIZED_FIELDS and text.lower() in EMPTY_PLACEHOLDERS:
            return ""
        return text
    return value


def _digest(parts: list[Any]) -> str:
    encoded = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _template_contract(template_id: str | None) -> dict[str, Any]:
    if not template_id:
        return {}
    contract = ObservationContractRepository().get_active_validation_dict(
        source_type="cli",
        schema_id=template_id,
    )
    if contract:
        return contract
    template = ParserRepository().get_template(template_id)
    if not template:
        return {}
    if template.status == "active":
        synced = ObservationContractRepository().upsert_from_parser_template(
            template_id,
            status="active",
        )
        if synced:
            return synced.to_validation_dict()
    validation = dict(template.validation_json or {})
    validation.setdefault("entity_type", template.category)
    validation.setdefault("contract_version", template.contract_version)
    validation.setdefault("schema_id", template.id)
    validation.setdefault("source_type", "cli")
    return validation


def _looks_like_header_artifact(
    record: dict[str, Any],
    *,
    entity_type: str,
    primary_keys: list[str],
) -> bool:
    if entity_type not in {"interface_l2", "interface_l3"}:
        return False
    if primary_keys != ["interface"]:
        return False
    interface = str(record.get("interface") or "").strip().lower()
    if interface in {"interface", "port", "name", "interface name", "port name"}:
        return True
    status = str(record.get("status") or "").strip().lower()
    protocol = str(record.get("protocol") or "").strip().lower()
    return interface in {"ifname", "intf"} and status in {"status", "link"}


def observations_from_snapshot(
    snapshot: dict[str, Any],
    *,
    mode: str,
    source: str,
    dataset_id: str | None,
) -> list[StateObservation]:
    structured = snapshot.get("structured_json")
    if structured is None:
        return []
    contract = _template_contract(snapshot.get("parser_template_id"))
    entity_type = str(contract.get("entity_type") or "record")
    primary_keys = [str(key) for key in contract.get("primary_keys") or []]
    ignore_fields = {
        str(field).strip().lower() for field in contract.get("ignore_fields") or []
    }
    ignore_fields.update(DEFAULT_IGNORE_FIELDS)
    observations: list[StateObservation] = []
    for record in _records(structured):
        if _looks_like_header_artifact(
            record,
            entity_type=entity_type,
            primary_keys=primary_keys,
        ):
            continue
        entity_key = build_entity_key(record, primary_keys)
        for field_name in sorted(record):
            normalized_field_name = str(field_name).strip().lower()
            if normalized_field_name in ignore_fields or field_name in primary_keys:
                continue
            value = _normalize_observation_value(str(field_name), record.get(field_name))
            value_text, value_number, value_type = _value_parts(value)
            state_key = _digest(
                [
                    snapshot["device_id"],
                    entity_type,
                    entity_key,
                    field_name,
                ]
            )
            observation_key = _digest(
                [
                    snapshot["id"],
                    snapshot["device_id"],
                    entity_type,
                    entity_key,
                    field_name,
                    value,
                ]
            )
            observations.append(
                StateObservation(
                    observation_id=str(uuid.uuid4()),
                    idempotency_key=observation_key,
                    state_key=state_key,
                    run_id=snapshot["run_id"],
                    snapshot_id=snapshot["id"],
                    dataset_id=dataset_id,
                    mode=mode,
                    source=source,
                    source_type=str(snapshot.get("source_type") or "cli"),
                    evidence_id=snapshot.get("evidence_id"),
                    schema_id=snapshot.get("schema_id")
                    or snapshot.get("parser_template_id"),
                    device_id=snapshot["device_id"],
                    vendor=snapshot.get("vendor"),
                    model=snapshot.get("model"),
                    canonical_command=snapshot["canonical_command"],
                    entity_type=entity_type,
                    entity_key=entity_key,
                    field_name=str(field_name),
                    value=value,
                    value_text=value_text,
                    value_number=value_number,
                    value_type=value_type,
                    observation_kind="state",
                    observed_at=str(snapshot.get("observed_at") or datetime.utcnow()),
                    parser_template_id=snapshot.get("parser_template_id"),
                    parser_version=snapshot.get("parser_version"),
                    contract_version=contract.get("contract_version")
                    or snapshot.get("contract_version"),
                    confidence=1.0,
                    metadata={
                        "canonical_command": snapshot["canonical_command"],
                        "primary_keys": primary_keys,
                        "evidence_id": snapshot.get("evidence_id"),
                        "source_type": str(snapshot.get("source_type") or "cli"),
                    },
                )
            )
    return observations


def normalize_snapshot(
    snapshot_id: str,
    *,
    update_current_state: bool | None = None,
) -> dict[str, Any]:
    repository = StateRepository()
    context = repository.get_snapshot_context(snapshot_id)
    if context is None:
        raise ValueError(f"snapshot not found: {snapshot_id}")
    mode = str(context["mode"])
    should_update_current = (
        mode == "production" if update_current_state is None else update_current_state
    )
    if mode == "parser_validation" and should_update_current:
        raise ValueError("parser_validation snapshots cannot update CurrentState")
    try:
        observations = observations_from_snapshot(
            context["snapshot"],
            mode=mode,
            source=str(context["source"]),
            dataset_id=context["dataset_id"],
        )
    except EntityKeyError as exc:
        return {
            "success": False,
            "snapshot_id": snapshot_id,
            "error": str(exc),
            "failure_code": "entity_key_invalid",
            "observation_count": 0,
            "created_count": 0,
            "current_state_updates": 0,
        }
    created, current_updates = repository.save_observations(
        observations,
        update_current_state=should_update_current,
    )
    return {
        "success": True,
        "snapshot_id": snapshot_id,
        "mode": mode,
        "observation_count": len(observations),
        "created_count": created,
        "current_state_updates": current_updates,
        "updated_current_state": should_update_current,
        "observations": [item.to_dict() for item in observations],
    }


def normalize_run(
    run_id: str,
    *,
    update_current_state: bool | None = None,
) -> dict[str, Any]:
    repository = StateRepository()
    results = [
        normalize_snapshot(snapshot_id, update_current_state=update_current_state)
        for snapshot_id in repository.list_run_snapshot_ids(run_id)
    ]
    return {
        "success": all(result.get("success") for result in results),
        "run_id": run_id,
        "snapshot_count": len(results),
        "observation_count": sum(int(r.get("observation_count") or 0) for r in results),
        "created_count": sum(int(r.get("created_count") or 0) for r in results),
        "current_state_updates": sum(
            int(r.get("current_state_updates") or 0) for r in results
        ),
        "results": results,
    }


def normalize_state_from_params(params: dict[str, Any]) -> dict[str, Any]:
    if params.get("snapshot_id"):
        return normalize_snapshot(
            str(params["snapshot_id"]),
            update_current_state=params.get("update_current_state"),
        )
    if params.get("run_id"):
        return normalize_run(
            str(params["run_id"]),
            update_current_state=params.get("update_current_state"),
        )
    raise ValueError("provide snapshot_id or run_id")
