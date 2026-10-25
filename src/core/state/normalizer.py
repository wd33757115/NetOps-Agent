"""Normalize structured parser output into stable field-level observations."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import uuid
from dataclasses import replace
from typing import Any

from src.common.config import get_settings
from src.core.check_items.adapter_projection import (
    project_adapter_batches_to_observations,
)
from src.core.check_items.catalog import (
    CheckItemCatalogRepository,
    CheckItemDefinition,
)
from src.core.check_items.command_adapter import (
    CommandAdapterError,
    ObservationBatch,
    execute_adapter,
    is_direct_projection_adapter,
)
from src.core.observation.record_set_relations import (
    materialize_record_set_references,
)
from src.core.observation.semantic_projection import (
    SemanticProjectionError,
    dimension_value_signature,
    project_value,
    resolve_dimensions,
)
from src.core.parser.key_contract import EntityKeyError, build_entity_key
from src.core.parser.repository import ParserTemplateRepository
from src.core.parser.value_types import is_unassigned_ip, is_unavailable_value
from src.core.patrol.command_splitter import canonicalize_command
from src.core.state.domain import StateObservation
from src.core.state.observation_applicability import (
    APPLICABILITY_CONTEXT_KEY,
    applicability_context_fields,
)
from src.core.state.repository import StateRepository

DEFAULT_IGNORE_FIELDS = {"uptime"}
EMPTY_PLACEHOLDERS = {"", "-", "--", "n/a", "na", "none", "null"}
PLACEHOLDER_NORMALIZED_FIELDS = {"description"}


class ObservationContractVersionError(ValueError):
    """Raised when replay cannot resolve the immutable contract version."""


class LegacyObservationRuntimeRetiredError(ValueError):
    """Raised when production attempts to use the retired V1 semantic bridge."""




def _adapter_record_sets(structured: Any) -> dict[str, list[dict[str, Any]]]:
    if isinstance(structured, dict) and isinstance(
        structured.get("record_sets"), dict
    ):
        return {
            str(name): _records(rows)
            for name, rows in structured["record_sets"].items()
        }
    if isinstance(structured, dict):
        candidate_sets = {
            str(name): _records(rows)
            for name, rows in structured.items()
            if isinstance(rows, list)
        }
        if candidate_sets:
            return candidate_sets
    return {"default": _records(structured)}




def _adapter_projection(
    snapshot: dict[str, Any],
    *,
    mode: str,
    source: str,
    dataset_id: str | None,
) -> tuple[list[StateObservation], tuple[ObservationBatch, ...]] | None:
    version_id = str(snapshot.get("implementation_version_id") or "").strip()
    if not version_id:
        return None
    context = CheckItemCatalogRepository().get_implementation_version_context(
        version_id
    )
    if context is None:
        return None
    implementation, check_items = context
    if not implementation.adapter_spec:
        return None
    parser_id = str(
        dict(implementation.adapter_spec.get("parser") or {}).get("template_id")
        or ""
    ).strip()
    snapshot_parser_id = str(snapshot.get("parser_template_id") or "").strip()
    if parser_id != snapshot_parser_id:
        raise SemanticProjectionError(
            "command adapter Parser binding does not match the snapshot Parser version"
        )

    items_by_id = {item.id: item for item in check_items}
    direct_projection = is_direct_projection_adapter(implementation.adapter_spec)
    if not direct_projection:
        raise LegacyObservationRuntimeRetiredError("旧语义投影已退役，请使用检查项 V2 命令适配器")
    batches = execute_adapter(
        implementation.adapter_spec,
        record_sets=_adapter_record_sets(snapshot.get("structured_json")),
        check_items={
            item.id: {
                "version_id": item.version_id,
                "canonical_contract": item.canonical_contract,
            }
            for item in check_items
        },
        device_id=str(snapshot.get("inventory_device_id") or snapshot["device_id"]),
        adapter_version_id=implementation.version_id,
        evidence={
            "snapshot_id": snapshot.get("id"),
            "parser_template_id": snapshot.get("parser_template_id"),
            "raw_text_hash": snapshot.get("raw_text_hash"),
            "canonical_command": snapshot.get("canonical_command"),
        },
        observed_at=snapshot.get("observed_at"),
        require_pinned_check_item_versions=direct_projection,
    )
    if direct_projection:
        observations = project_adapter_batches_to_observations(
            batches,
            check_items=items_by_id,
            snapshot=snapshot,
            mode=mode,
            source=source,
            dataset_id=dataset_id,
            command_adapter_id=implementation.id,
            command_adapter_version_id=implementation.version_id,
        )
        return _reconcile_snapshot_observations(observations), batches

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
    validator_type = str((validator or {}).get("type") or "").lower()
    if isinstance(value, str):
        text = value.strip()
        if field in PLACEHOLDER_NORMALIZED_FIELDS and text.lower() in EMPTY_PLACEHOLDERS:
            return ""
        if is_unavailable_value(text, (validator or {}).get("unavailable_tokens")):
            return None
        if validator_type in {"number", "percent"}:
            numeric_text = text.rstrip("%").strip()
            try:
                return float(numeric_text)
            except ValueError:
                return text
        if validator_type == "ip_or_unassigned":
            if is_unassigned_ip(text):
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


def _reconcile_snapshot_observations(
    observations: list[StateObservation],
) -> list[StateObservation]:
    """Deduplicate one canonical fact and reject ambiguous same-snapshot values."""

    by_state_key: dict[str, StateObservation] = {}
    by_observation_id: dict[str, StateObservation] = {}
    for observation in observations:
        existing = by_state_key.get(observation.state_key)
        if (
            existing is not None
            and existing.observation_id != observation.observation_id
        ):
            raise SemanticProjectionError(
                "one snapshot produced conflicting canonical values for "
                f"{observation.observation_type or observation.field_name} "
                f"at entity {observation.entity_key}"
            )
        by_state_key[observation.state_key] = observation
        by_observation_id.setdefault(
            observation.observation_id,
            observation,
        )
    # Preserve the contract/record traversal order.  Reconciliation must not
    # silently reorder fields consumed by reports or downstream integrations.
    return list(by_observation_id.values())


def observations_from_snapshot(
    snapshot: dict[str, Any],
    *,
    mode: str,
    source: str,
    dataset_id: str | None,
    observation_contract_version: int | None = None,
    _contract_override: dict[str, Any] | None = None,
    _record_set_name: str | None = None,
) -> list[StateObservation]:
    metadata_json = dict(snapshot.get("metadata_json") or {})
    parse_status = str(snapshot.get("parse_status") or "")
    parser_failure_text_comparison = parse_status in {
        "template_missing",
        "parse_failed",
    }
    raw_text_comparison = (
        metadata_json.get("handling") == "raw_hash_diff"
        or parser_failure_text_comparison
    )
    structured = snapshot.get("structured_json")
    if (
        structured is None
        and raw_text_comparison
        and snapshot.get("raw_text_hash")
    ):
        structured = [{"raw_text_hash": snapshot.get("raw_text_hash")}]
    if structured is None:
        return []
    if raw_text_comparison:
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
    else:
        if _contract_override is None:
            adapter_projection = _adapter_projection(
                snapshot,
                mode=mode,
                source=source,
                dataset_id=dataset_id,
            )
            if adapter_projection is not None:
                return adapter_projection[0]
            raise LegacyObservationRuntimeRetiredError("缺少检查项 command-adapter/v2 命令适配器，无法生成标准数据")
        contract = dict(_contract_override)
        record_sets = [
            dict(item)
            for item in contract.get("record_sets") or ()
            if isinstance(item, dict)
        ]
        if record_sets:
            if isinstance(structured, dict) and isinstance(
                structured.get("record_sets"), dict
            ):
                structured_sets = dict(structured["record_sets"])
            elif isinstance(structured, dict) and all(
                str(item.get("name") or "") in structured for item in record_sets
            ):
                structured_sets = structured
            elif len(record_sets) == 1:
                structured_sets = {
                    str(record_sets[0].get("name") or "default"): structured
                }
            else:
                raise ValueError(
                    "multi-record ObservationContract requires structured_json.record_sets"
                )
            structured_sets, record_sets = materialize_record_set_references(
                contract=contract,
                structured_sets=structured_sets,
            )
            observations: list[StateObservation] = []
            for record_set in record_sets:
                name = str(record_set.get("name") or "").strip()
                if not name:
                    raise ValueError("ObservationContract record set requires name")
                child_snapshot = dict(snapshot)
                child_snapshot["structured_json"] = structured_sets.get(name, [])
                child_contract = {
                    **contract,
                    **record_set,
                    "schema_id": contract.get("schema_id"),
                    "contract_id": contract.get("contract_id"),
                    "contract_version": contract.get("contract_version"),
                    "contract_sha256": contract.get("contract_sha256"),
                    "record_sets": [],
                }
                observations.extend(
                    observations_from_snapshot(
                        child_snapshot,
                        mode=mode,
                        source=source,
                        dataset_id=dataset_id,

                        _contract_override=child_contract,
                        _record_set_name=name,
                    )
                )
            return _reconcile_snapshot_observations(observations)
    source_entity_type = str(contract.get("entity_type") or "record")
    entity_type = str(
        contract.get("canonical_entity_type") or source_entity_type
    )
    primary_keys = [str(key) for key in contract.get("primary_keys") or []]
    primary_key_names = {
        key.strip().casefold() for key in primary_keys if key.strip()
    }
    entity_identity_fields = [
        str(key)
        for key in contract.get("entity_identity_fields") or []
        if str(key).strip()
    ]
    entity_dimensions = dict(contract.get("entity_dimensions") or {})
    ignore_fields = {str(field).strip().lower() for field in contract.get("ignore_fields") or []}
    ignore_fields.update(DEFAULT_IGNORE_FIELDS)
    state_semantics = dict(contract.get("state_semantics") or {})
    validators = dict(contract.get("validators") or {})
    observations: list[StateObservation] = []
    external_device_id = str(snapshot["device_id"])
    state_device_id = str(snapshot.get("inventory_device_id") or external_device_id)

    def emits_empty_value(field_name: str) -> bool:
        normalized_name = str(field_name).strip().lower()
        semantics = state_semantics.get(field_name) or state_semantics.get(
            normalized_name
        )
        return bool(
            isinstance(semantics, dict)
            and semantics.get("emit_when_empty") is True
        )

    def emits_state_observation(field_name: str) -> bool:
        normalized_name = str(field_name).strip().lower()
        semantics = state_semantics.get(field_name) or state_semantics.get(
            normalized_name
        )
        if not isinstance(semantics, dict):
            return True
        if semantics.get("ignore") is True or semantics.get("monitor") is False:
            return False
        comparison_role = str(
            semantics.get("comparison_role") or ""
        ).strip()
        return not comparison_role or comparison_role == "observation"

    def append_observation(
        *,
        record: dict[str, Any],
        normalized_record: dict[str, Any],
        entity_key: str,
        source_entity_key: str,
        canonical_identity: dict[str, Any] | None,
        field_name: str,
        raw_value: Any,
        value_index: int | None,
    ) -> None:
        normalized_field_name = str(field_name).strip().lower()
        validator = validators.get(field_name) or validators.get(normalized_field_name)
        if not isinstance(validator, dict):
            validator = {}
        validator_type = str(validator.get("type") or "").lower()
        unavailable_token = (
            str(raw_value).strip()
            if is_unavailable_value(raw_value, validator.get("unavailable_tokens"))
            else None
        )
        source_value = _normalize_observation_value(
            str(field_name),
            raw_value,
            validator=validator,
        )
        field_semantics = state_semantics.get(field_name) or state_semantics.get(
            normalized_field_name
        )
        if not isinstance(field_semantics, dict):
            field_semantics = {}
        field_semantics = dict(field_semantics)
        if validator_type == "percent":
            field_semantics.setdefault("semantic_type", "percentage")
            field_semantics.setdefault("unit", "%")
        elif validator_type == "ip_or_unassigned":
            field_semantics.setdefault("semantic_type", "ip_address")
        value = project_value(
            field_name=normalized_field_name,
            source_value=source_value,
            normalized_record=normalized_record,
            field_semantics=field_semantics,
            value_index=value_index,
        )
        context_fields = set(
            applicability_context_fields(field_semantics.get("applicability"))
        )
        context_fields.update(
            str(item)
            for item in field_semantics.get("context_fields") or []
            if str(item).strip()
        )
        threshold_references = {
            str(role): dict(reference)
            for role, reference in dict(
                field_semantics.get("threshold_references") or {}
            ).items()
            if isinstance(reference, dict)
        }
        observation_context = None
        if context_fields or threshold_references:
            observation_context = {}
            for context_field in context_fields:
                context_validator = validators.get(context_field)
                if not isinstance(context_validator, dict):
                    context_validator = {}
                context_value = normalized_record.get(context_field)
                if value_index is not None and isinstance(context_value, list):
                    context_value = (
                        context_value[value_index]
                        if value_index < len(context_value)
                        else None
                    )
                observation_context[context_field] = _normalize_observation_value(
                    context_field,
                    context_value,
                    validator=context_validator,
                )
            threshold_fields = sorted(
                {
                    str(reference.get("context_key") or reference.get("field_name") or "")
                    for reference in threshold_references.values()
                    if str(reference.get("field_name") or "").strip()
                }
            )
            runtime_threshold_fields: list[str] = []
            threshold_reference_sources: dict[str, Any] = {}
            for role, raw_reference in threshold_references.items():
                source_field = str(
                    raw_reference.get("runtime_field_name")
                    or raw_reference.get("field_name")
                    or ""
                ).strip()
                context_key = str(
                    raw_reference.get("context_key") or source_field
                ).strip()
                if not source_field or not context_key:
                    continue
                source_value = normalized_record.get(source_field)
                if source_value in (None, ""):
                    continue
                reference_validator = raw_reference.get("validator")
                if not isinstance(reference_validator, dict):
                    reference_validator = validators.get(
                        str(raw_reference.get("field_name") or "")
                    )
                if not isinstance(reference_validator, dict):
                    reference_validator = {}
                source_value = _normalize_observation_value(
                    str(raw_reference.get("field_name") or source_field),
                    source_value,
                    validator=reference_validator,
                )
                if source_value in (None, ""):
                    continue
                observation_context[context_key] = source_value
                runtime_threshold_fields.append(context_key)
                threshold_reference_sources[str(role)] = {
                    key: raw_reference.get(key)
                    for key in (
                        "source",
                        "record_set_name",
                        "relation_name",
                        "field_name",
                    )
                    if raw_reference.get(key) is not None
                }
            populated_thresholds = [
                field
                for field in threshold_fields
                if observation_context.get(field) not in (None, "")
            ]
            populated_thresholds = sorted(
                set(runtime_threshold_fields or populated_thresholds)
            )
            if populated_thresholds:
                observation_context["threshold_source"] = "device_output"
                observation_context["threshold_confidence"] = "authoritative"
                observation_context["threshold_fields"] = populated_thresholds
                if threshold_reference_sources:
                    observation_context["threshold_reference_sources"] = (
                        threshold_reference_sources
                    )
        dimensions = resolve_dimensions(
            field_semantics.get("dimensions"),
            normalized_record,
            value_index=value_index,
        )
        dimension_signature = dimension_value_signature(dimensions)
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
                dimension_signature,
            ]
        )
        observation_key = _digest(
            [
                snapshot["id"],
                state_device_id,
                entity_type,
                entity_key,
                semantic_field,
                dimension_signature,
                value,
            ]
        )
        observation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, observation_key))
        execution_item_id = str(snapshot.get("execution_item_id") or "")
        command_ordinal = int(
            (snapshot.get("metadata_json") or {}).get("command_ordinal") or 0
        )
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
                schema_id=(
                    contract.get("schema_id")
                    or snapshot.get("schema_id")
                    or snapshot.get("parser_template_id")
                ),
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
                    str(
                        field_semantics.get("canonical_unit")
                        or field_semantics.get("unit")
                    )
                    if (
                        field_semantics.get("canonical_unit")
                        or field_semantics.get("unit")
                    )
                    else None
                ),
                dimensions=dimensions,
                dimension_signature=dimension_signature,
                value=value,
                value_text=value_text,
                value_number=value_number,
                value_type=value_type,
                observation_kind="state",
                observed_at=str(snapshot["observed_at"]),
                parser_template_id=snapshot.get("parser_template_id"),
                parser_version=snapshot.get("parser_version"),
                contract_version=contract.get("contract_version"),
                execution_item_id=execution_item_id,
                fact_order_key=fact_order_key,
                confidence=1.0,
                metadata={
                    "canonical_command": snapshot["canonical_command"],
                    "primary_keys": primary_keys,
                    "source_entity_type": source_entity_type,
                    "source_entity_key": source_entity_key,
                    "canonical_entity_type": entity_type,
                    "entity_identity_fields": entity_identity_fields,
                    "canonical_identity": canonical_identity,
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
                    "canonical_unit": field_semantics.get("canonical_unit"),
                    "source_value": source_value if value != source_value else None,
                    "availability": "unavailable" if unavailable_token else "available",
                    "unavailable_token": unavailable_token,
                    "parser_contract_version": snapshot.get("contract_version"),
                    "observation_contract_id": contract.get("contract_id"),
                    "observation_contract_version": contract.get("contract_version"),
                    "observation_contract_digest": contract.get("contract_sha256"),
                    "unstructured_comparison": {
                        "profile": (snapshot.get("metadata_json") or {}).get(
                            "comparison_profile"
                        ),
                        "profile_version": (snapshot.get("metadata_json") or {}).get(
                            "comparison_profile_version"
                        ),
                    }
                    if raw_text_comparison
                    else None,
                    "parser_failure_text_comparison": parser_failure_text_comparison,
                    "semantics": field_semantics,
                    "dimensions": dimensions,
                    "dimension_signature": dimension_signature,
                    "record_set_name": _record_set_name,
                    "value_index": value_index + 1 if value_index is not None else None,
                    APPLICABILITY_CONTEXT_KEY: observation_context,
                },
            )
        )

    for record in _records(structured):
        if _looks_like_header_artifact(
            record,
            entity_type=source_entity_type,
            primary_keys=primary_keys,
        ):
            continue
        normalized_record: dict[str, Any] = {}
        for record_field, record_value in record.items():
            normalized_name = str(record_field).strip().lower()
            record_validator = validators.get(record_field) or validators.get(
                normalized_name
            )
            if not isinstance(record_validator, dict):
                record_validator = {}
            if isinstance(record_value, list):
                normalized_value = [
                    _normalize_observation_value(
                        str(record_field),
                        item,
                        validator=record_validator,
                    )
                    for item in record_value
                ]
            else:
                normalized_value = _normalize_observation_value(
                    str(record_field),
                    record_value,
                    validator=record_validator,
                )
            normalized_record[record_field] = normalized_value
            normalized_record.setdefault(normalized_name, normalized_value)
        source_entity_key = build_entity_key(record, primary_keys)
        canonical_identity: dict[str, Any] | None = None
        if entity_dimensions:
            from src.core.check_items.identity import normalize_entity_identity

            resolved_identity = resolve_dimensions(
                entity_dimensions,
                normalized_record,
                context={"device_id": state_device_id},
            )
            identity_fields = entity_identity_fields or list(
                entity_dimensions
            )
            if (
                len(identity_fields) != len(set(identity_fields))
                or set(identity_fields) != set(resolved_identity)
            ):
                raise SemanticProjectionError(
                    "entity_identity_fields must uniquely match entity_dimensions"
                )
            identity_decision = normalize_entity_identity(
                entity_type=entity_type,
                identity_values=resolved_identity,
                primary_keys=identity_fields,
            )
            entity_key = identity_decision.entity_key
            canonical_identity = {
                "values": identity_decision.values,
                "policy_id": identity_decision.policy_id,
                "policy_version": identity_decision.policy_version,
                "policy_digest": identity_decision.policy_digest,
                "matched_policy_index": identity_decision.matched_policy_index,
            }
        else:
            entity_key = source_entity_key
        if raw_text_comparison:
            # Text is command-scoped: all empty-primary-key records must not
            # collapse into a single raw hash for the entire device.
            entity_key = json.dumps(
                [canonicalize_command(snapshot["canonical_command"])],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        for field_name in sorted(record):
            normalized_field_name = str(field_name).strip().lower()
            if (
                normalized_field_name in ignore_fields
                or normalized_field_name in primary_key_names
                or not emits_state_observation(str(field_name))
            ):
                continue
            raw_value = record.get(field_name)
            if isinstance(raw_value, list):
                for value_index, item in enumerate(raw_value):
                    if (
                        item is None
                        or (isinstance(item, str) and not item.strip())
                    ) and not emits_empty_value(str(field_name)):
                        continue
                    append_observation(
                        record=record,
                        normalized_record=normalized_record,
                        entity_key=entity_key,
                        source_entity_key=source_entity_key,
                        canonical_identity=canonical_identity,
                        field_name=str(field_name),
                        raw_value=item,
                        value_index=value_index,
                    )
                continue
            if (
                raw_value is None
                or (isinstance(raw_value, str) and not raw_value.strip())
            ) and not emits_empty_value(str(field_name)):
                continue
            append_observation(
                record=record,
                normalized_record=normalized_record,
                entity_key=entity_key,
                source_entity_key=source_entity_key,
                canonical_identity=canonical_identity,
                field_name=str(field_name),
                raw_value=raw_value,
                value_index=None,
            )
    return _reconcile_snapshot_observations(observations)


def _build_snapshot_normalization(
    repository: StateRepository,
    snapshot_id: str,
    *,
    update_current_state: bool | None,
    include_observations: bool = True,
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
    metadata_json = dict(snapshot.get("metadata_json") or {})
    parse_status = str(
        snapshot.get("parse_status")
        or ("parsed" if snapshot.get("structured_json") is not None else "parse_failed")
    )
    parser_failure_text_comparison = parse_status in {
        "template_missing",
        "parse_failed",
    }
    gated = {
        "identity_unknown": ("blocked", "blocked_by_identity", "blocked_by_identity"),
        "ignored": ("skipped", "ignored_by_policy", "ignored"),
        "not_applicable": ("skipped", "not_applicable", "not_applicable"),
        "empty_unverified": ("blocked", "empty_unverified", "blocked_by_output"),
        "command_error": ("blocked", "command_error", "blocked_by_output"),
        "truncated": ("blocked", "truncated", "blocked_by_output"),
        "paged": ("blocked", "paged", "blocked_by_output"),
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
    if parser_failure_text_comparison:
        observations = observations_from_snapshot(
            snapshot,
            mode=mode,
            source=str(context["source"]),
            dataset_id=context["dataset_id"],
        )
        return (
            {
                # The normalizer successfully classified this as a data-quality
                # result.  It is not an observation success and must not update
                # CurrentState, but callers must be able to distinguish that
                # controlled outcome from a normalization crash.
                "success": True,
                "snapshot_id": snapshot_id,
                "mode": mode,
                "parse_status": parse_status,
                "normalization_status": "blocked",
                "fact_status": "blocked_by_parser",
                "failure_code": parse_status,
                "error": (
                    "Parser is unavailable; raw CLI text is retained for "
                    "text comparison only"
                ),
                "no_observation_reason": "blocked_by_parser",
                "comparison_mode": "raw_text_only",
                "observation_count": len(observations),
                "created_count": 0,
                "current_state_updates": 0,
                "updated_current_state": False,
                "observations": [item.to_dict() for item in observations] if include_observations else [],
            },
            observations,
            False,
        )
    if parse_status == "parse_empty" and not snapshot.get(
        "implementation_version_id"
    ):
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
    # ``raw_hashed`` snapshots intentionally have no Parser payload.  Their
    # immutable CommandSnapshot digest is projected into the text-diff
    # observation below; requiring structured JSON here would discard the
    # configured non-structured path.
    if (
        snapshot.get("structured_json") is None
        and not (
            metadata_json.get("handling") == "raw_hash_diff"
            or parse_status == "raw_hashed"
        )
    ):
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
        adapter_projection = _adapter_projection(
            context["snapshot"],
            mode=mode,
            source=str(context["source"]),
            dataset_id=context["dataset_id"],
        )
        if adapter_projection is None:
            observations = observations_from_snapshot(
                context["snapshot"],
                mode=mode,
                source=str(context["source"]),
                dataset_id=context["dataset_id"],
            )
            batches: tuple[ObservationBatch, ...] = ()
        else:
            observations, batches = adapter_projection
    except (
        CommandAdapterError,
        EntityKeyError,
        LegacyObservationRuntimeRetiredError,
        ObservationContractVersionError,
        SemanticProjectionError,
    ) as exc:
        failure_code = (
            "legacy_runtime_retired"
            if isinstance(exc, LegacyObservationRuntimeRetiredError)
            else
            "contract_version_unavailable"
            if isinstance(exc, ObservationContractVersionError)
            else "adapter_mapping_invalid"
            if isinstance(exc, CommandAdapterError)
            else "semantic_projection_invalid"
            if isinstance(exc, SemanticProjectionError)
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
    batch_rows = [batch.to_dict() for batch in batches]
    quality_statuses = {batch.quality_status for batch in batches}
    if batches and not observations:
        if quality_statuses == {"not_applicable"}:
            normalization_status = "skipped"
            fact_status = "not_applicable"
            no_observation_reason = "not_applicable"
            success = True
        elif quality_statuses <= {"valid_empty"}:
            normalization_status = "empty"
            fact_status = "empty"
            no_observation_reason = "legitimate_empty"
            success = True
        else:
            normalization_status = "failed"
            fact_status = "blocked_by_contract"
            no_observation_reason = "adapter_output_unavailable"
            success = False
        return (
            {
                "success": success,
                "snapshot_id": snapshot_id,
                "mode": mode,
                "parse_status": parse_status,
                "normalization_status": normalization_status,
                "fact_status": fact_status,
                "failure_code": (
                    None if success else "adapter_output_unavailable"
                ),
                "no_observation_reason": no_observation_reason,
                "observation_count": 0,
                "created_count": 0,
                "current_state_updates": 0,
                "updated_current_state": should_update_current and success,
                "observations": [],
                "observation_batches": batch_rows,
                "authoritative_for_deletion": bool(
                    batches and quality_statuses <= {"complete", "valid_empty"}
                ),
            },
            [],
            should_update_current and success,
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
            "observations": [item.to_dict() for item in observations] if include_observations else [],
            "observation_batches": batch_rows,
            "partial": bool(
                batches
                and any(
                    batch.quality_status
                    not in {"complete", "valid_empty", "not_applicable"}
                    for batch in batches
                )
            ),
            "authoritative_for_deletion": bool(
                not batches
                or quality_statuses <= {"complete", "valid_empty"}
            ),
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
            authoritative_for_deletion=(
                False
                if result.get("authoritative_for_deletion") is False
                else None
            ),
            metadata={
                "observation_batches": result.get("observation_batches") or [],
            }
            if "observation_batches" in result
            else None,
        )
    if observations:
        created, current_updates = repository.save_observations(
            observations,
            update_current_state=should_update_current,
        )
        result["created_count"] = created
        result["current_state_updates"] = current_updates
    if not result["success"]:
        return result
    return result


def normalize_run(
    run_id: str,
    *,
    update_current_state: bool | None = None,
    device_ids: list[str] | None = None,
    include_observations: bool = True,
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
            include_observations=include_observations,
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
    include_observations: bool = True,
) -> dict[str, Any]:
    repository = StateRepository()
    from src.core.patrol.repository import PatrolRepository

    results: list[dict[str, Any]] = []
    evidence_only_observations: list[StateObservation] = []
    current_state_observations: list[StateObservation] = []
    created = 0
    current_updates = 0
    patrol_repository = PatrolRepository()
    for index, snapshot_id in enumerate(snapshot_ids):
        if heartbeat is not None:
            heartbeat()
        snapshot_result, observations, update_current = _build_snapshot_normalization(
            repository,
            snapshot_id,
            update_current_state=update_current_state,
            include_observations=include_observations,
        )
        if not include_observations:
            snapshot_result.pop("observations", None)
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
                authoritative_for_deletion=(
                    False
                    if snapshot_result.get("authoritative_for_deletion") is False
                    else None
                ),
                metadata={
                    "observation_batches": snapshot_result.get(
                        "observation_batches"
                    )
                    or [],
                }
                if "observation_batches" in snapshot_result
                else None,
            )
        if observations:
            if not include_observations:
                # Offline tasks consume only counts. Flush each command's facts
                # instead of keeping every device's observations in memory.
                count, updates = repository.save_observations(
                    observations,
                    update_current_state=bool(snapshot_result["success"] and update_current),
                )
                created += count
                current_updates += updates
                snapshot_result["created_count"] = count
                snapshot_result["current_state_updates"] = updates
            elif snapshot_result["success"] and update_current:
                current_state_observations.extend(observations)
            else:
                evidence_only_observations.extend(observations)
        else:
            patrol_repository.mark_collection_analysis_failed(
                run_id=run_id,
                snapshot_id=snapshot_id,
                failure_code=str(snapshot_result.get("failure_code") or "normalize_failed"),
                error=str(snapshot_result.get("error") or "normalization failed"),
            )
    if evidence_only_observations:
        created, _ = repository.save_observations(
            evidence_only_observations,
            update_current_state=False,
        )
    if current_state_observations:
        current_created, current_updates = repository.save_observations(
            current_state_observations,
            update_current_state=True,
        )
        created += current_created
        remaining_created = created
        remaining_updates = current_updates
        for snapshot_result in results:
            if (
                not snapshot_result["success"]
                and not snapshot_result.get("observation_count")
            ):
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
            include_observations=params.get("include_observations", True),
        )
    raise ValueError("provide snapshot_id or run_id")
