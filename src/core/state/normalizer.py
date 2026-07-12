"""Normalize structured parser output into stable field-level observations."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import uuid
from typing import Any

from src.core.observation import ObservationContractRepository
from src.core.parser.key_contract import EntityKeyError, build_entity_key
from src.core.parser.repository import ParserTemplateRepository
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


def _normalize_observation_value(
    field_name: str,
    value: Any,
    *,
    validator: dict[str, Any] | None = None,
) -> Any:
    field = str(field_name).strip().lower()
    if isinstance(value, str):
        text = value.strip()
        if field in PLACEHOLDER_NORMALIZED_FIELDS and text.lower() in EMPTY_PLACEHOLDERS:
            return ""
        if str((validator or {}).get("type") or "").lower() == "ip_or_unassigned":
            if text.lower() in {"unassigned", "unset", "none", "n/a", "--", "-"}:
                return text.lower()
            try:
                if "/" in text:
                    return ipaddress.ip_interface(text).with_prefixlen
                return ipaddress.ip_address(text).compressed
            except ValueError:
                return text
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
    template = ParserTemplateRepository().get_template(template_id)
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
    if (snapshot.get("metadata_json") or {}).get("handling") == "raw_hash_diff":
        contract = {
            "entity_type": "raw_command_output",
            "primary_keys": [],
            "state_semantics": {
                "raw_text_hash": {
                    "observation_type": "raw.command.hash",
                    "semantic_type": "sha256",
                }
            },
        }
    entity_type = str(contract.get("entity_type") or "record")
    primary_keys = [str(key) for key in contract.get("primary_keys") or []]
    ignore_fields = {str(field).strip().lower() for field in contract.get("ignore_fields") or []}
    ignore_fields.update(DEFAULT_IGNORE_FIELDS)
    state_semantics = dict(contract.get("state_semantics") or {})
    validators = dict(contract.get("validators") or {})
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
            validator = validators.get(field_name) or validators.get(normalized_field_name)
            if not isinstance(validator, dict):
                validator = {}
            value = _normalize_observation_value(
                str(field_name),
                record.get(field_name),
                validator=validator,
            )
            field_semantics = state_semantics.get(field_name) or state_semantics.get(
                normalized_field_name
            )
            if not isinstance(field_semantics, dict):
                field_semantics = {}
            field_semantics = dict(field_semantics)
            validator_type = str(validator.get("type") or "").lower()
            if validator_type == "percent":
                field_semantics.setdefault("semantic_type", "percentage")
                field_semantics.setdefault("unit", "%")
            elif validator_type == "ip_or_unassigned":
                field_semantics.setdefault("semantic_type", "ip_address")
            value_text, value_number, value_type = _value_parts(value)
            observation_type = (
                str(field_semantics.get("observation_type"))
                if field_semantics.get("observation_type")
                else None
            )
            semantic_field = observation_type or normalized_field_name
            state_key = _digest(
                [
                    state_device_id,
                    entity_type,
                    entity_key,
                    semantic_field,
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
            observation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, observation_key))
            execution_item_id = str(snapshot.get("execution_item_id") or "")
            command_ordinal = int((snapshot.get("metadata_json") or {}).get("command_ordinal") or 0)
            fact_order_key = f"{command_ordinal:020d}|{execution_item_id}|{observation_id}"
            observations.append(
                StateObservation(
                    observation_id=observation_id,
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
                    observation_type=observation_type,
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
                    execution_item_id=execution_item_id,
                    fact_order_key=fact_order_key,
                    confidence=1.0,
                    metadata={
                        "canonical_command": snapshot["canonical_command"],
                        "primary_keys": primary_keys,
                        "evidence_id": snapshot.get("evidence_id"),
                        "source_type": str(snapshot.get("source_type") or "cli"),
                        "external_device_id": external_device_id,
                        "inventory_device_id": snapshot.get("inventory_device_id"),
                        "execution_item_id": snapshot.get("execution_item_id"),
                        "collection_item_id": (
                            (snapshot.get("metadata_json") or {}).get("collection_item_id")
                        ),
                        "command_ordinal": (
                            (snapshot.get("metadata_json") or {}).get("command_ordinal")
                        ),
                        "historical_backfill": bool(
                            (snapshot.get("metadata_json") or {}).get("historical_backfill")
                        ),
                        "backfill_job_id": (
                            (snapshot.get("metadata_json") or {}).get("backfill_job_id")
                        ),
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
    snapshot = context["snapshot"]
    parse_status = str(
        snapshot.get("parse_status")
        or ("parsed" if snapshot.get("structured_json") is not None else "parse_failed")
    )
    gated = {
        "template_missing": ("blocked", "blocked_by_parser", "blocked_by_parser"),
        "parse_failed": ("blocked", "blocked_by_parser", "blocked_by_parser"),
        "identity_unknown": ("blocked", "blocked_by_identity", "blocked_by_identity"),
        "ignored": ("skipped", "ignored_by_policy", "ignored"),
        "not_applicable": ("skipped", "not_applicable", "not_applicable"),
        "empty_unverified": ("blocked", "empty_unverified", "blocked_by_output"),
        "command_error": ("blocked", "command_error", "blocked_by_output"),
    }.get(parse_status)
    if gated is not None:
        normalization_status, reason, fact_status = gated
        return (
            {
                "success": True,
                "snapshot_id": snapshot_id,
                "mode": mode,
                "parse_status": parse_status,
                "normalization_status": normalization_status,
                "fact_status": fact_status,
                "failure_code": reason,
                "no_observation_reason": reason,
                "observation_count": 0,
                "created_count": 0,
                "current_state_updates": 0,
                "updated_current_state": False,
                "observations": [],
            },
            [],
            False,
        )
    if parse_status == "parse_empty":
        return (
            {
                "success": True,
                "snapshot_id": snapshot_id,
                "mode": mode,
                "parse_status": parse_status,
                "normalization_status": "empty",
                "fact_status": "empty",
                "no_observation_reason": "legitimate_empty",
                "observation_count": 0,
                "created_count": 0,
                "current_state_updates": 0,
                "updated_current_state": should_update_current,
                "observations": [],
            },
            [],
            should_update_current,
        )
    if snapshot.get("structured_json") is None:
        return (
            {
                "success": False,
                "snapshot_id": snapshot_id,
                "mode": mode,
                "parse_status": parse_status,
                "normalization_status": "failed",
                "fact_status": "normalization_failed",
                "failure_code": "structured_payload_missing",
                "error": "parsed snapshot has no structured payload",
                "no_observation_reason": "normalization_failed",
                "observation_count": 0,
                "created_count": 0,
                "current_state_updates": 0,
            },
            [],
            should_update_current,
        )
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
                "normalization_status": "failed",
                "fact_status": "blocked_by_contract",
                "no_observation_reason": "blocked_by_contract",
                "observation_count": 0,
                "created_count": 0,
                "current_state_updates": 0,
            },
            [],
            should_update_current,
        )
    normalization_status = "normalized" if observations else "empty"
    return (
        {
            "success": True,
            "snapshot_id": snapshot_id,
            "mode": mode,
            "parse_status": parse_status,
            "normalization_status": normalization_status,
            "fact_status": "ready" if observations else "empty",
            **({"no_observation_reason": "legitimate_empty"} if not observations else {}),
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
    context = repository.get_snapshot_context(snapshot_id)
    if context is not None:
        from src.core.patrol.repository import PatrolRepository

        PatrolRepository().update_collection_normalization_status(
            run_id=str(context["snapshot"]["run_id"]),
            snapshot_id=snapshot_id,
            normalization_status=str(result["normalization_status"]),
            fact_status=str(result["fact_status"]),
            failure_code=result.get("failure_code"),
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
    from src.core.patrol.repository import (
        AnalysisAttemptFencedError,
        AnalysisAttemptInProgressError,
        AnalysisLeaseHeartbeat,
        PatrolRepository,
    )

    snapshot_ids = (
        repository.list_run_snapshot_ids(run_id, device_ids=device_ids)
        if device_ids
        else repository.list_run_snapshot_ids(run_id)
    )
    input_revision_digest = _digest(["normalization-input-v1", run_id, sorted(snapshot_ids)])
    updates_run_status = not bool(device_ids)
    patrol_repository = PatrolRepository()
    attempt = patrol_repository.begin_analysis_attempt(
        run_id=run_id,
        stage="normalizing",
        scope={
            "device_ids": sorted({str(item) for item in device_ids or []}),
            "full_run": not bool(device_ids),
            "update_current_state": update_current_state,
            "input_revision_digest": input_revision_digest,
        },
        rule_bundle={"observation_contracts": "snapshot_bound"},
    )
    if attempt["reused"]:
        return {
            **dict(attempt["result"] or {}),
            "results": [],
            "reused_analysis_attempt": True,
        }
    if attempt["in_progress"]:
        raise AnalysisAttemptInProgressError(f"normalization already running for {run_id}")
    heartbeat = AnalysisLeaseHeartbeat(patrol_repository, attempt)

    if updates_run_status:
        try:
            patrol_repository.update_analysis_status(
                run_id,
                analysis_status="normalizing",
                baseline_eligible=False,
            )
        except ValueError:
            pass
    heartbeat.start()
    try:
        result = _normalize_run_impl(
            run_id,
            snapshot_ids=snapshot_ids,
            update_current_state=update_current_state,
            device_ids=device_ids,
            update_run_status=updates_run_status,
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
                    run_id,
                    analysis_status="failed",
                    baseline_eligible=False,
                    metadata={
                        "analysis_failure_stage": "normalizing",
                        "analysis_failure": str(exc),
                    },
                )
            except ValueError:
                pass
        raise


def _normalize_run_impl(
    run_id: str,
    *,
    snapshot_ids: list[str],
    update_current_state: bool | None,
    device_ids: list[str] | None,
    update_run_status: bool = True,
    heartbeat: Any | None = None,
) -> dict[str, Any]:
    repository = StateRepository()
    from src.core.patrol.repository import PatrolRepository

    results: list[dict[str, Any]] = []
    valid_observations: list[StateObservation] = []
    should_update_current = False
    patrol_repository = PatrolRepository()
    for index, snapshot_id in enumerate(snapshot_ids):
        if heartbeat is not None:
            heartbeat()
        snapshot_result, observations, update_current = _build_snapshot_normalization(
            repository,
            snapshot_id,
            update_current_state=update_current_state,
        )
        results.append(snapshot_result)
        update_status = getattr(
            patrol_repository,
            "update_collection_normalization_status",
            None,
        )
        if callable(update_status):
            update_status(
                run_id=run_id,
                snapshot_id=snapshot_id,
                normalization_status=str(snapshot_result["normalization_status"]),
                fact_status=str(snapshot_result["fact_status"]),
                failure_code=snapshot_result.get("failure_code"),
            )
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
        "normalized_snapshot_count": sum(
            item.get("normalization_status") == "normalized" for item in results
        ),
        "empty_snapshot_count": sum(
            item.get("normalization_status") == "empty" for item in results
        ),
        "ignored_snapshot_count": sum(
            item.get("normalization_status") == "skipped" for item in results
        ),
        "blocked_snapshot_count": sum(
            item.get("normalization_status") == "blocked" for item in results
        ),
        "blocked_by_parser_count": sum(
            item.get("no_observation_reason") == "blocked_by_parser" for item in results
        ),
        "blocked_by_contract_count": sum(
            item.get("no_observation_reason") == "blocked_by_contract" for item in results
        ),
        "results": results,
    }
    if update_run_status and not result["success"]:
        try:
            PatrolRepository().update_analysis_status(
                run_id,
                analysis_status="failed",
                baseline_eligible=False,
                metadata={"analysis_failure_stage": "normalizing"},
            )
        except ValueError:
            pass
    elif update_run_status and result["partial"]:
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
    elif update_run_status:
        try:
            PatrolRepository().update_analysis_status(
                run_id,
                analysis_status="normalized",
                baseline_eligible=False,
                metadata={
                    "normalization_failed_snapshot_count": 0,
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
