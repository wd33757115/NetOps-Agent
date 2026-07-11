"""Normalize structured parser output into stable field-level observations."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from src.core.observation import ObservationContractRepository
from src.core.parser.key_contract import EntityKeyError, build_entity_key
from src.core.parser.repository import ParserRepository
from src.core.state.domain import StateObservation
from src.core.state.repository import StateRepository

DEFAULT_IGNORE_FIELDS = {"uptime"}
EMPTY_PLACEHOLDERS = {"", "-", "--", "n/a", "na", "none", "null"}
PLACEHOLDER_NORMALIZED_FIELDS = {"description"}


class ObservationContractVersionError(ValueError):
    """Raised when replay cannot resolve the immutable contract version."""


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


def _template_contract(
    template_id: str | None,
    *,
    contract_version: int | None = None,
) -> dict[str, Any]:
    if not template_id:
        return {}
    contract_repository = ObservationContractRepository()
    contract = (
        contract_repository.get_validation_dict(
            source_type="cli",
            schema_id=template_id,
            version=int(contract_version),
        )
        if contract_version is not None
        else contract_repository.get_active_validation_dict(
            source_type="cli",
            schema_id=template_id,
        )
    )
    if contract:
        return contract
    template = ParserRepository().get_template(template_id)
    if not template:
        if contract_version is not None:
            raise ObservationContractVersionError(
                f"ObservationContract not found: cli:{template_id} v{contract_version}"
            )
        return {}
    if template.status == "active":
        synced = ObservationContractRepository().upsert_from_parser_template(
            template_id,
            status="active",
        )
        if synced:
            synced_validation = synced.to_validation_dict()
            if contract_version is None or int(
                synced_validation.get("contract_version") or 0
            ) == int(contract_version):
                return synced_validation
    if contract_version is not None:
        raise ObservationContractVersionError(
            f"ObservationContract not found: cli:{template_id} v{contract_version}"
        )
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
    snapshot_contract_version = snapshot.get("contract_version")
    contract = (
        _template_contract(
            snapshot.get("parser_template_id"),
            contract_version=int(snapshot_contract_version),
        )
        if snapshot_contract_version is not None
        else _template_contract(snapshot.get("parser_template_id"))
    )
    entity_type = str(contract.get("entity_type") or "record")
    primary_keys = [str(key) for key in contract.get("primary_keys") or []]
    ignore_fields = {str(field).strip().lower() for field in contract.get("ignore_fields") or []}
    ignore_fields.update(DEFAULT_IGNORE_FIELDS)
    state_semantics = dict(contract.get("state_semantics") or {})
    observations: list[StateObservation] = []
    external_device_id = str(snapshot["device_id"])
    state_device_id = str(snapshot.get("inventory_device_id") or external_device_id)
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
            field_semantics = state_semantics.get(field_name) or state_semantics.get(
                normalized_field_name
            )
            if not isinstance(field_semantics, dict):
                field_semantics = {}
            value_text, value_number, value_type = _value_parts(value)
            state_key = _digest(
                [
                    state_device_id,
                    entity_type,
                    entity_key,
                    field_name,
                ]
            )
            observation_key = _digest(
                [
                    snapshot["id"],
                    state_device_id,
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
                    schema_id=snapshot.get("schema_id") or snapshot.get("parser_template_id"),
                    device_id=state_device_id,
                    vendor=snapshot.get("vendor"),
                    model=snapshot.get("model"),
                    canonical_command=snapshot["canonical_command"],
                    entity_type=entity_type,
                    entity_key=entity_key,
                    field_name=str(field_name),
                    observation_type=(
                        str(field_semantics.get("observation_type"))
                        if field_semantics.get("observation_type")
                        else None
                    ),
                    semantic_type=(
                        str(field_semantics.get("semantic_type"))
                        if field_semantics.get("semantic_type")
                        else None
                    ),
                    unit=(
                        str(field_semantics.get("unit")) if field_semantics.get("unit") else None
                    ),
                    value=value,
                    value_text=value_text,
                    value_number=value_number,
                    value_type=value_type,
                    observation_kind="state",
                    observed_at=str(snapshot["observed_at"]),
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
                        "external_device_id": external_device_id,
                        "inventory_device_id": snapshot.get("inventory_device_id"),
                        "identity_status": (
                            "inventory_bound"
                            if snapshot.get("inventory_device_id")
                            else "external_fallback"
                        ),
                        "observation_type": field_semantics.get("observation_type"),
                        "semantic_type": field_semantics.get("semantic_type"),
                        "unit": field_semantics.get("unit"),
                        "semantics": field_semantics,
                    },
                )
            )
    return observations


def _build_snapshot_normalization(
    repository: StateRepository,
    snapshot_id: str,
    *,
    update_current_state: bool | None,
) -> tuple[dict[str, Any], list[StateObservation], bool]:
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
    except (EntityKeyError, ObservationContractVersionError) as exc:
        failure_code = (
            "contract_version_unavailable"
            if isinstance(exc, ObservationContractVersionError)
            else "entity_key_invalid"
        )
        return (
            {
                "success": False,
                "snapshot_id": snapshot_id,
                "error": str(exc),
                "failure_code": failure_code,
                "observation_count": 0,
                "created_count": 0,
                "current_state_updates": 0,
            },
            [],
            should_update_current,
        )
    return (
        {
            "success": True,
            "snapshot_id": snapshot_id,
            "mode": mode,
            "observation_count": len(observations),
            "created_count": 0,
            "current_state_updates": 0,
            "updated_current_state": should_update_current,
            "observations": [item.to_dict() for item in observations],
        },
        observations,
        should_update_current,
    )


def normalize_snapshot(
    snapshot_id: str,
    *,
    update_current_state: bool | None = None,
) -> dict[str, Any]:
    repository = StateRepository()
    result, observations, should_update_current = _build_snapshot_normalization(
        repository,
        snapshot_id,
        update_current_state=update_current_state,
    )
    if not result["success"]:
        return result
    created, current_updates = repository.save_observations(
        observations,
        update_current_state=should_update_current,
    )
    result["created_count"] = created
    result["current_state_updates"] = current_updates
    return result


def normalize_run(
    run_id: str,
    *,
    update_current_state: bool | None = None,
    device_ids: list[str] | None = None,
) -> dict[str, Any]:
    repository = StateRepository()
    from src.core.patrol.repository import PatrolRepository

    try:
        PatrolRepository().update_analysis_status(
            run_id,
            analysis_status="normalizing",
            baseline_eligible=False,
        )
    except ValueError:
        pass
    snapshot_ids = (
        repository.list_run_snapshot_ids(run_id, device_ids=device_ids)
        if device_ids
        else repository.list_run_snapshot_ids(run_id)
    )
    results: list[dict[str, Any]] = []
    valid_observations: list[StateObservation] = []
    should_update_current = False
    patrol_repository = PatrolRepository()
    for snapshot_id in snapshot_ids:
        snapshot_result, observations, update_current = _build_snapshot_normalization(
            repository,
            snapshot_id,
            update_current_state=update_current_state,
        )
        results.append(snapshot_result)
        if snapshot_result["success"]:
            valid_observations.extend(observations)
            should_update_current = should_update_current or update_current
        else:
            patrol_repository.mark_collection_analysis_failed(
                run_id=run_id,
                snapshot_id=snapshot_id,
                failure_code=str(snapshot_result.get("failure_code") or "normalize_failed"),
                error=str(snapshot_result.get("error") or "normalization failed"),
            )
    created = 0
    current_updates = 0
    if valid_observations:
        created, current_updates = repository.save_observations(
            valid_observations,
            update_current_state=should_update_current,
        )
        remaining_created = created
        remaining_updates = current_updates
        for snapshot_result in results:
            if not snapshot_result["success"]:
                continue
            count = int(snapshot_result["observation_count"])
            snapshot_result["created_count"] = min(count, remaining_created)
            remaining_created -= snapshot_result["created_count"]
            snapshot_result["current_state_updates"] = min(count, remaining_updates)
            remaining_updates -= snapshot_result["current_state_updates"]
    failed_count = sum(not item.get("success") for item in results)
    successful_count = len(results) - failed_count
    result = {
        "success": successful_count > 0 or not results,
        "partial": failed_count > 0 and successful_count > 0,
        "run_id": run_id,
        "device_ids": device_ids or [],
        "snapshot_count": len(results),
        "observation_count": sum(int(r.get("observation_count") or 0) for r in results),
        "created_count": created,
        "current_state_updates": current_updates,
        "successful_snapshot_count": successful_count,
        "failed_snapshot_count": failed_count,
        "results": results,
    }
    if not result["success"]:
        try:
            PatrolRepository().update_analysis_status(
                run_id,
                analysis_status="failed",
                baseline_eligible=False,
                metadata={"analysis_failure_stage": "normalizing"},
            )
        except ValueError:
            pass
    elif result["partial"]:
        try:
            PatrolRepository().update_analysis_status(
                run_id,
                analysis_status="normalized_partial",
                baseline_eligible=False,
                metadata={
                    "normalization_failed_snapshot_count": failed_count,
                    "normalization_successful_snapshot_count": successful_count,
                },
            )
        except ValueError:
            pass
    return result


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
            device_ids=params.get("device_ids"),
        )
    raise ValueError("provide snapshot_id or run_id")
