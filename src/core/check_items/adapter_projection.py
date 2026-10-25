"""Direct V2 projection from canonical CommandAdapter records to state facts.

This module deliberately has no dependency on ``ObservationContract``.  A
published CommandAdapter v2 already pins the CheckItem version that defines
the target observation types, identity and units.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from src.core.check_items.catalog import CheckItemDefinition
from src.core.check_items.command_adapter import ObservationBatch
from src.core.observation.semantic_projection import (
    SemanticProjectionError,
    dimension_value_signature,
    resolve_dimensions,
)
from src.core.parser.key_contract import build_entity_key
from src.core.state.domain import StateObservation
from src.core.state.observation_applicability import (
    APPLICABILITY_CONTEXT_KEY,
    applicability_context_fields,
)


def project_adapter_batches_to_observations(
    batches: Sequence[ObservationBatch],
    *,
    check_items: Mapping[str, CheckItemDefinition],
    snapshot: Mapping[str, Any],
    mode: str,
    source: str,
    dataset_id: str | None,
    command_adapter_id: str,
    command_adapter_version_id: str,
) -> list[StateObservation]:
    """Turn V2 adapter batches into canonical observations directly.

    Empty, partial and unavailable batches deliberately produce no facts here;
    their quality envelope is handled by the patrol layer and must not be
    mistaken for entity removal or recovery.
    """

    observations: list[StateObservation] = []
    for batch in batches:
        # A partial batch proves neither coverage nor absence.  Retaining its
        # successful rows as state facts could still trigger a current-state
        # rule or contaminate a baseline, so V2 is deliberately fail-closed.
        if batch.quality_status != "complete" or not batch.records:
            continue
        check_item = check_items.get(batch.check_item_id)
        if check_item is None:
            raise SemanticProjectionError(
                f"adapter batch references an unavailable CheckItem: {batch.check_item_id}"
            )
        if (
            batch.check_item_version_id
            and str(check_item.version_id) != str(batch.check_item_version_id)
        ):
            raise SemanticProjectionError(
                "adapter batch CheckItem version does not match the frozen "
                f"implementation context: {batch.check_item_id}"
            )
        observations.extend(
            _project_batch(
                batch,
                check_item=check_item,
                snapshot=snapshot,
                mode=mode,
                source=source,
                dataset_id=dataset_id,
                command_adapter_id=command_adapter_id,
                command_adapter_version_id=command_adapter_version_id,
            )
        )
    return observations


def _project_batch(
    batch: ObservationBatch,
    *,
    check_item: CheckItemDefinition,
    snapshot: Mapping[str, Any],
    mode: str,
    source: str,
    dataset_id: str | None,
    command_adapter_id: str,
    command_adapter_version_id: str,
) -> list[StateObservation]:
    canonical = dict(check_item.canonical_contract or {})
    rows = [
        dict(item)
        for item in canonical.get("observations") or []
        if isinstance(item, Mapping)
        and str(item.get("canonical_field") or "").strip()
    ]
    primary_keys = [
        str(item)
        for item in (
            canonical.get("primary_keys")
            or canonical.get("identity_dimensions")
            or []
        )
        if str(item) and str(item) != "device_id"
    ]
    entity_type = str(
        canonical.get("canonical_entity_type")
        or canonical.get("entity_type")
        or check_item.id
    )
    entity_dimensions = dict(canonical.get("entity_dimensions") or {})
    identity_fields = [
        str(item)
        for item in (
            canonical.get("entity_identity_fields") or primary_keys
        )
        if str(item)
    ]
    external_device_id = str(snapshot.get("device_id") or batch.device_id)
    state_device_id = str(snapshot.get("inventory_device_id") or batch.device_id)
    output: list[StateObservation] = []
    if batch.projection_dimensions and len(batch.projection_dimensions) != len(
        batch.records
    ):
        raise SemanticProjectionError(
            "CommandAdapter V2 projection dimensions do not align with records"
        )
    dimension_rows = batch.projection_dimensions or tuple(
        {} for _ in batch.records
    )
    for record, projection_dimensions in zip(
        batch.records,
        dimension_rows,
        strict=True,
    ):
        normalized_record = dict(record)
        source_entity_key = build_entity_key(normalized_record, primary_keys)
        entity_key, canonical_identity = _resolve_identity(
            normalized_record,
            entity_type=entity_type,
            primary_keys=primary_keys,
            identity_fields=identity_fields,
            entity_dimensions=entity_dimensions,
            state_device_id=state_device_id,
            fallback_key=source_entity_key,
        )
        for declaration in rows:
            field_name = str(declaration["canonical_field"]).strip()
            if field_name in primary_keys:
                continue
            value = normalized_record.get(field_name)
            if value in (None, "") and declaration.get("emit_when_empty") is not True:
                continue
            declared_dimensions = declaration.get("dimensions") or []
            field_dimensions = projection_dimensions.get(field_name)
            if field_dimensions is None and declared_dimensions:
                raise SemanticProjectionError(
                    "CommandAdapter V2 projection is missing declared dimensions "
                    f"for canonical field: {field_name}"
                )
            # Fixed dimensions belong to the field's meaning (e.g. an explicit
            # five-minute CPU Value), not to an adapter-created CLI value.
            fixed_dimensions = dict(declaration.get("fixed_dimensions") or {})
            if any(key in fixed_dimensions and fixed_dimensions[key] != val
                   for key, val in (field_dimensions or {}).items()):
                raise SemanticProjectionError(f"conflicting fixed dimensions for {field_name}")
            dimensions = {**fixed_dimensions, **dict(field_dimensions or {})}
            dimension_signature = dimension_value_signature(dimensions)
            observation_type = str(declaration.get("observation_type") or "").strip() or None
            semantic_field = observation_type or field_name
            state_key = _digest(
                [state_device_id, entity_type, entity_key, semantic_field, dimension_signature]
            )
            observation_key = _digest(
                [
                    snapshot.get("id"),
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
            value_text, value_number, value_type = _value_parts(value)
            field_context = _field_context(
                declaration,
                normalized_record,
            )
            output.append(
                StateObservation(
                    observation_id=observation_id,
                    idempotency_key=observation_key,
                    state_key=state_key,
                    run_id=str(snapshot.get("run_id") or ""),
                    snapshot_id=str(snapshot.get("id") or ""),
                    dataset_id=dataset_id,
                    mode=mode,
                    source=source,
                    source_type=str(snapshot.get("source_type") or "cli"),
                    evidence_id=snapshot.get("evidence_id"),
                    schema_id=check_item.version_id,
                    device_id=state_device_id,
                    vendor=snapshot.get("vendor"),
                    model=snapshot.get("model"),
                    canonical_command=str(snapshot.get("canonical_command") or ""),
                    entity_type=entity_type,
                    entity_key=entity_key,
                    field_name=field_name,
                    observation_type=observation_type,
                    semantic_type=(
                        str(declaration.get("semantic_type"))
                        if declaration.get("semantic_type")
                        else None
                    ),
                    unit=(
                        str(
                            declaration.get("canonical_unit")
                            or declaration.get("unit")
                        )
                        if declaration.get("canonical_unit")
                        or declaration.get("unit")
                        else None
                    ),
                    dimensions=dimensions,
                    dimension_signature=dimension_signature,
                    value=value,
                    value_text=value_text,
                    value_number=value_number,
                    value_type=value_type,
                    observation_kind="state",
                    observed_at=str(snapshot.get("observed_at") or batch.observed_at),
                    parser_template_id=snapshot.get("parser_template_id"),
                    parser_version=snapshot.get("parser_version"),
                    contract_version=check_item.version,
                    execution_item_id=execution_item_id,
                    fact_order_key=(
                        f"{command_ordinal:020d}|{execution_item_id}|{observation_id}"
                    ),
                    confidence=1.0,
                    metadata={
                        "canonical_command": snapshot.get("canonical_command"),
                        "primary_keys": primary_keys,
                        "source_entity_type": entity_type,
                        "source_entity_key": source_entity_key,
                        "canonical_entity_type": entity_type,
                        "entity_identity_fields": identity_fields,
                        "canonical_identity": canonical_identity,
                        "evidence_id": snapshot.get("evidence_id"),
                        "source_type": str(snapshot.get("source_type") or "cli"),
                        "external_device_id": external_device_id,
                        "inventory_device_id": snapshot.get("inventory_device_id"),
                        "execution_item_id": snapshot.get("execution_item_id"),
                        "collection_item_id": (
                            (snapshot.get("metadata_json") or {}).get("collection_item_id")
                        ),
                        "command_ordinal": command_ordinal,
                        "identity_status": (
                            "inventory_bound"
                            if snapshot.get("inventory_device_id")
                            else "external_fallback"
                        ),
                        "observation_type": observation_type,
                        "semantic_type": declaration.get("semantic_type"),
                        "unit": declaration.get("unit"),
                        "canonical_unit": declaration.get("canonical_unit"),
                        "semantics": declaration,
                        "dimensions": dimensions,
                        "dimension_signature": dimension_signature,
                        APPLICABILITY_CONTEXT_KEY: field_context,
                        "check_item_id": check_item.id,
                        "check_item_version_id": check_item.version_id,
                        "command_adapter_id": command_adapter_id,
                        "command_adapter_version_id": command_adapter_version_id,
                        "adapter_schema_version": batch.evidence.get(
                            "adapter_schema_version"
                        ),
                        "adapter_projection_digest": batch.evidence.get(
                            "adapter_projection_digest"
                        ),
                        "projection_source_record_set": batch.evidence.get(
                            "source_record_set"
                        ),
                        "observation_batch_quality": batch.quality_status,
                    },
                )
            )
    return output


def _resolve_identity(
    record: Mapping[str, Any],
    *,
    entity_type: str,
    primary_keys: list[str],
    identity_fields: list[str],
    entity_dimensions: Mapping[str, Any],
    state_device_id: str,
    fallback_key: str,
) -> tuple[str, dict[str, Any] | None]:
    if not entity_dimensions:
        return fallback_key, None
    from src.core.check_items.identity import normalize_entity_identity

    resolved = resolve_dimensions(
        dict(entity_dimensions),
        dict(record),
        context={"device_id": state_device_id},
    )
    effective_identity_fields = identity_fields or list(resolved)
    if (
        len(effective_identity_fields) != len(set(effective_identity_fields))
        or set(effective_identity_fields) != set(resolved)
    ):
        raise SemanticProjectionError(
            "entity_identity_fields must uniquely match entity_dimensions"
        )
    decision = normalize_entity_identity(
        entity_type=entity_type,
        identity_values=resolved,
        primary_keys=effective_identity_fields,
    )
    return (
        decision.entity_key,
        {
            "values": decision.values,
            "policy_id": decision.policy_id,
            "policy_version": decision.policy_version,
            "policy_digest": decision.policy_digest,
            "matched_policy_index": decision.matched_policy_index,
        },
    )


def _field_context(
    declaration: Mapping[str, Any],
    record: Mapping[str, Any],
) -> dict[str, Any] | None:
    context_fields = set(
        applicability_context_fields(declaration.get("applicability"))
    )
    context_fields.update(
        str(item)
        for item in declaration.get("context_fields") or []
        if str(item).strip()
    )
    threshold_references = {
        str(role): dict(reference)
        for role, reference in dict(
            declaration.get("threshold_references") or {}
        ).items()
        if isinstance(reference, Mapping)
    }
    if not context_fields and not threshold_references:
        return None
    context = {
        field: record.get(field)
        for field in sorted(context_fields)
    }
    populated_thresholds: list[str] = []
    sources: dict[str, Any] = {}
    for role, reference in threshold_references.items():
        source_field = str(
            reference.get("runtime_field_name")
            or reference.get("field_name")
            or ""
        ).strip()
        context_key = str(reference.get("context_key") or source_field).strip()
        value = record.get(source_field)
        if not source_field or not context_key or value in (None, ""):
            continue
        context[context_key] = value
        populated_thresholds.append(context_key)
        sources[role] = {
            key: reference.get(key)
            for key in ("source", "record_set_name", "relation_name", "field_name")
            if reference.get(key) is not None
        }
    if populated_thresholds:
        context["threshold_source"] = "device_output"
        context["threshold_confidence"] = "authoritative"
        context["threshold_fields"] = sorted(set(populated_thresholds))
        if sources:
            context["threshold_reference_sources"] = sources
    return context


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


def _digest(parts: list[Any]) -> str:
    encoded = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
