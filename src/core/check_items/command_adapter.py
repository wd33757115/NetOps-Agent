"""Deterministic CommandAdapter projection contracts and execution.

``command-adapter/v2`` is the production A -> B boundary:

* TextFSM/Parser produces vendor-native record sets (A);
* an immutable adapter output projects one record set into one CheckItem contract
  (B); and
* the state runtime consumes the resulting canonical records directly.

The v1 reader is intentionally retained for historical replay only. New
adapters must use the explicit ``projection`` object from v2; no parser field
name is treated as a business-semantic inference.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from src.core.observation.semantic_projection import (
    SemanticProjectionError,
    dimension_field_references,
    normalize_dimensions,
    project_value,
    resolve_dimensions,
    transform_field_references,
    validate_transform,
    validate_unit_conversion,
    validate_value_mapping,
)

ADAPTER_SCHEMA_VERSION = "command-adapter/v2"
LEGACY_ADAPTER_SCHEMA_VERSION = "command-adapter/v1"
SUPPORTED_ADAPTER_SCHEMA_VERSIONS = frozenset(
    {LEGACY_ADAPTER_SCHEMA_VERSION, ADAPTER_SCHEMA_VERSION}
)
QUALITY_STATUSES = {
    "complete",
    "valid_empty",
    "partial",
    "unavailable",
    "not_applicable",
}
_EMPTY_STATUSES = {"valid_empty", "unavailable", "not_applicable"}
_ALLOWED_BINARY_OPERATORS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
}
_ALLOWED_UNARY_OPERATORS = {
    ast.UAdd: lambda value: value,
    ast.USub: lambda value: -value,
}
_PROJECTION_DIMENSIONS_FIELD = "__adapter_projection_dimensions__"


class CommandAdapterError(ValueError):
    """Raised when an adapter specification or mapping is invalid."""


@dataclass(frozen=True)
class ObservationBatch:
    """A quality-qualified canonical record batch for one CheckItem."""

    device_id: str
    check_item_id: str
    observed_at: str
    quality_status: str
    adapter_version_id: str
    check_item_version_id: str
    records: tuple[dict[str, Any], ...]
    evidence: dict[str, Any]
    projection_dimensions: tuple[dict[str, dict[str, Any]], ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def adapter_schema_version(raw: Mapping[str, Any] | None) -> str:
    """Return the declared adapter schema without treating legacy drafts as v2."""

    spec = dict(raw or {})
    declared = str(spec.get("schema_version") or "").strip()
    if declared:
        return declared
    outputs = spec.get("outputs") or []
    if any(isinstance(item, Mapping) and "fields" in item for item in outputs):
        return LEGACY_ADAPTER_SCHEMA_VERSION
    return ADAPTER_SCHEMA_VERSION


def is_direct_projection_adapter(raw: Mapping[str, Any] | None) -> bool:
    """Whether this adapter is eligible for the V2 direct runtime path."""

    # An implementation without an adapter is a legitimate legacy structured
    # route: its historical ObservationContract remains authoritative.  Do not
    # let the schema default used by ``validate_adapter_spec`` accidentally
    # turn that route into a V2 projection route.
    return bool(dict(raw or {})) and (
        adapter_schema_version(raw) == ADAPTER_SCHEMA_VERSION
    )


def adapter_projection_digest(raw: Mapping[str, Any] | None) -> str:
    """Create a stable evidence digest for a normalized projection contract."""

    normalized = validate_adapter_spec(raw)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_adapter_spec(
    raw: Mapping[str, Any] | None,
    *,
    check_item_ids: Sequence[str] = (),
    check_items: Mapping[str, Mapping[str, Any]] | None = None,
    require_pinned_check_item_versions: bool = False,
) -> dict[str, Any]:
    """Validate a deterministic adapter without inferring business semantics.

    ``check_item_ids`` is sufficient for catalog loading. Runtime and preview
    callers should also pass ``check_items`` so targets, required fields and
    pinned CheckItem versions are verified against actual canonical contracts.
    """

    spec = dict(raw or {})
    if not spec:
        return {}
    version = adapter_schema_version(spec)
    if version not in SUPPORTED_ADAPTER_SCHEMA_VERSIONS:
        raise CommandAdapterError(f"unsupported adapter schema: {version}")

    parser = _normalize_parser(spec.get("parser"))
    outputs = spec.get("outputs") or []
    if not isinstance(outputs, list) or not outputs:
        raise CommandAdapterError("adapter_spec.outputs must contain at least one output")

    check_item_details = {
        str(key): dict(value)
        for key, value in dict(check_items or {}).items()
        if str(key) and isinstance(value, Mapping)
    }
    known_check_items = {
        str(item) for item in check_item_ids if str(item)
    } | set(check_item_details)
    normalized_outputs: list[dict[str, Any]] = []
    output_check_items: set[str] = set()
    seen_v2_output_keys: set[tuple[str, str]] = set()
    for item in outputs:
        if not isinstance(item, Mapping):
            raise CommandAdapterError("each adapter output must be an object")
        check_item_id = str(
            item.get("check_item_id") or item.get("check_item") or ""
        ).strip()
        source_record_set = str(item.get("source_record_set") or "").strip()
        if not check_item_id or not source_record_set:
            raise CommandAdapterError(
                "each adapter output requires check_item_id and source_record_set"
            )
        if known_check_items and check_item_id not in known_check_items:
            raise CommandAdapterError(
                f"adapter output references an unlinked check item: {check_item_id}"
            )
        output_key = (check_item_id, source_record_set)
        if version == ADAPTER_SCHEMA_VERSION and output_key in seen_v2_output_keys:
            raise CommandAdapterError(
                "command-adapter/v2 cannot project the same CheckItem from the "
                "same Parser record set more than once"
            )
        seen_v2_output_keys.add(output_key)

        fields = _output_fields(item, version=version)
        if not isinstance(fields, Mapping) or not fields:
            raise CommandAdapterError(
                f"adapter output {check_item_id} requires field mappings"
            )
        normalized_fields = {
            str(target).strip(): _normalize_binding(
                binding,
                target=str(target).strip(),
                projection=(version == ADAPTER_SCHEMA_VERSION),
            )
            for target, binding in fields.items()
            if str(target).strip()
        }
        if not normalized_fields:
            raise CommandAdapterError(
                f"adapter output {check_item_id} requires field mappings"
            )
        empty_status = str(item.get("empty_status") or "unavailable").strip()
        if empty_status not in _EMPTY_STATUSES:
            raise CommandAdapterError(
                f"adapter output {check_item_id} has invalid empty_status: {empty_status}"
            )
        pinned_version_id = str(item.get("check_item_version_id") or "").strip()
        # V1 is replay-only and historically delegated its semantic coverage to
        # ObservationContract.  Applying V2's complete canonical-field and
        # dimension requirements to it would make old evidence impossible to
        # replay.  All new publication paths use V2 and are validated here.
        if version == ADAPTER_SCHEMA_VERSION:
            _validate_target_contract(
                check_item_id=check_item_id,
                pinned_version_id=pinned_version_id,
                fields=normalized_fields,
                check_item=check_item_details.get(check_item_id),
                require_pinned=require_pinned_check_item_versions,
            )
        output_check_items.add(check_item_id)
        normalized_output = {
            "check_item_id": check_item_id,
            "source_record_set": source_record_set,
            "empty_status": empty_status,
        }
        if pinned_version_id:
            normalized_output["check_item_version_id"] = pinned_version_id
        if version == ADAPTER_SCHEMA_VERSION:
            normalized_output["projection"] = {"fields": normalized_fields}
            joins = _normalize_record_set_joins(
                item.get("record_set_joins"),
                primary_record_set=source_record_set,
            )
            if joins:
                normalized_output["record_set_joins"] = joins
        else:
            normalized_output["fields"] = normalized_fields
        normalized_outputs.append(normalized_output)

    if known_check_items and output_check_items != known_check_items:
        missing = sorted(known_check_items - output_check_items)
        raise CommandAdapterError(
            "adapter outputs do not cover linked check items: " + ", ".join(missing)
        )
    return {
        "schema_version": version,
        "parser": parser,
        "outputs": normalized_outputs,
    }


def direct_projection_manifest(
    raw: Mapping[str, Any],
    *,
    check_item_id: str,
    check_item: Mapping[str, Any],
    require_pinned_check_item_version: bool = True,
) -> dict[str, Any]:
    """Return the governed V2 projection manifest for one CheckItem target."""

    # Validate the entire adapter's structure, then the requested target's
    # contract. Sibling CheckItems are legitimate outputs, not unlinked targets;
    # their contracts are validated by their own plans and by catalog approval.
    normalized = validate_adapter_spec(raw)
    if normalized.get("schema_version") != ADAPTER_SCHEMA_VERSION:
        raise CommandAdapterError("direct projection requires command-adapter/v2")
    outputs = [
        item
        for item in normalized["outputs"]
        if item["check_item_id"] == check_item_id
    ]
    if not outputs:
        raise CommandAdapterError(
            f"command-adapter/v2 has no output for {check_item_id}"
        )
    validate_adapter_spec(
        {**normalized, "outputs": outputs},
        check_item_ids=(check_item_id,),
        check_items={check_item_id: check_item},
        require_pinned_check_item_versions=require_pinned_check_item_version,
    )
    observation_by_field = {
        str(item.get("canonical_field") or "").strip(): str(
            item.get("observation_type") or ""
        ).strip()
        for item in _canonical_rows(check_item)
        if str(item.get("canonical_field") or "").strip()
        and str(item.get("observation_type") or "").strip()
    }
    output_manifests: list[dict[str, Any]] = []
    for output in outputs:
        fields = _output_fields(output, version=ADAPTER_SCHEMA_VERSION)
        binding_sources = {
            field: projection_binding_field_references(binding)
            for field, binding in fields.items()
        }
        value_references = {
            field: _projection_value_field_references(binding)
            for field, binding in fields.items()
        }
        dimension_references = {
            field: dimension_field_references(binding.get("dimensions"))
            for field, binding in fields.items()
            if binding.get("dimensions")
        }
        joins = [dict(item) for item in output.get("record_set_joins") or []]
        output_manifests.append(
            {
                "source_record_set": output["source_record_set"],
                "target_fields": sorted(fields),
                "source_fields": sorted(
                    {
                        field
                        for values in binding_sources.values()
                        for field in values
                    }
                ),
                "record_set_joins": joins,
                "joined_source_fields": sorted(
                    {
                        str(field)
                        for join in joins
                        for field in join.get("fields") or []
                    }
                ),
                "source_fields_by_target": {
                    field: list(values)
                    for field, values in sorted(binding_sources.items())
                },
                "value_source_fields_by_target": {
                    field: list(values)
                    for field, values in sorted(value_references.items())
                },
                "dimension_field_references_by_target": {
                    field: list(values)
                    for field, values in sorted(dimension_references.items())
                },
                "dimensions_by_target": {
                    field: dict(binding.get("dimensions") or {})
                    for field, binding in sorted(fields.items())
                    if binding.get("dimensions")
                },
                "observation_types": sorted(
                    observation_by_field[field]
                    for field in fields
                    if observation_by_field.get(field)
                ),
            }
        )
    primary_manifest = output_manifests[0]
    return {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "parser_template_id": normalized["parser"]["template_id"],
        "check_item_id": check_item_id,
        "check_item_version_id": str(
            outputs[0].get("check_item_version_id") or ""
        ),
        # Keep the first-output fields for callers that operate on a single
        # record set, while exposing every record-set projection below.
        **primary_manifest,
        "outputs": output_manifests,
        "source_record_sets": [
            item["source_record_set"] for item in output_manifests
        ],
        "observation_types": sorted(
            {
                observation_type
                for item in output_manifests
                for observation_type in item["observation_types"]
            }
        ),
        "digest": adapter_projection_digest(normalized),
    }


def direct_projection_source_field_errors(
    manifest: Mapping[str, Any],
    *,
    parser_field_contract: Mapping[str, Any],
) -> tuple[str, ...]:
    """Verify every direct mapping reference exists in the approved ParserSpec."""

    declared_sets = dict(parser_field_contract.get("parser_spec_record_sets") or {})
    if not declared_sets:
        declared_sets = dict(parser_field_contract.get("template_record_sets") or {})
    outputs = manifest.get("outputs")
    output_manifests = (
        [dict(item) for item in outputs if isinstance(item, Mapping)]
        if isinstance(outputs, list)
        else [dict(manifest)]
    )
    errors: list[str] = []
    for output in output_manifests:
        record_set = str(output.get("source_record_set") or "").strip()
        fields = declared_sets.get(record_set)
        if fields is None:
            errors.append(
                f"projection source_record_set is absent from ParserSpec: {record_set}"
            )
            continue
        available = {str(item) for item in fields}
        joined_source_fields = {
            str(item) for item in output.get("joined_source_fields") or []
        }
        target_fields = {str(item) for item in output.get("target_fields") or []}
        canonical_dimension_references = {
            str(field)
            for values in dict(
                output.get("dimension_field_references_by_target") or {}
            ).values()
            for field in values or []
            if str(field) in target_fields
        }
        value_references = {
            str(field)
            for values in dict(
                output.get("value_source_fields_by_target") or {}
            ).values()
            for field in values or []
        }
        required_sources = value_references | (
            set(output.get("source_fields") or [])
            - value_references
            - canonical_dimension_references
        )
        missing = sorted(required_sources - available - joined_source_fields)
        errors.extend(
            f"projection references a Parser field that is not declared in {record_set}: {field}"
            for field in missing
        )
        for join in output.get("record_set_joins") or []:
            joined_set = str(join.get("source_record_set") or "")
            joined_available = {
                str(item) for item in declared_sets.get(joined_set) or []
            }
            if not joined_available:
                errors.append(
                    f"projection joined record_set is absent from ParserSpec: {joined_set}"
                )
                continue
            right_keys = {
                str(item) for item in dict(join.get("on") or {}).values()
            }
            missing_joined = sorted(
                (set(join.get("fields") or []) | right_keys) - joined_available
            )
            errors.extend(
                f"projection join references a Parser field that is not declared in {joined_set}: {field}"
                for field in missing_joined
            )
            missing_left = sorted(set(dict(join.get("on") or {})) - available)
            errors.extend(
                f"projection join key is not declared in {record_set}: {field}"
                for field in missing_left
            )
    return tuple(errors)


def projection_binding_field_references(binding: Any) -> tuple[str, ...]:
    """Return all Parser-native fields consumed by one projection binding."""

    normalized = _normalize_binding(binding, target="field", projection=True)
    references: set[str] = set()
    if "source" in normalized:
        references.add(str(normalized["source"]))
    if "expression" in normalized:
        tree = ast.parse(str(normalized["expression"]), mode="eval")
        references.update(
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        )
    transform = normalized.get("transform")
    if transform is not None:
        references.update(transform_field_references(transform))
    dimensions = normalized.get("dimensions")
    if dimensions is not None:
        references.update(dimension_field_references(dimensions))
    return tuple(sorted(references))


def _projection_value_field_references(binding: Any) -> tuple[str, ...]:
    """Return source fields required before a dimension expression executes."""

    normalized = _normalize_binding(binding, target="field", projection=True)
    references: set[str] = set()
    if "source" in normalized:
        references.add(str(normalized["source"]))
    if "expression" in normalized:
        tree = ast.parse(str(normalized["expression"]), mode="eval")
        references.update(
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        )
    transform = normalized.get("transform")
    if transform is not None:
        references.update(transform_field_references(transform))
    return tuple(sorted(references))


def execute_adapter(
    spec: Mapping[str, Any],
    *,
    record_sets: Mapping[str, Sequence[Mapping[str, Any]]],
    check_items: Mapping[str, Mapping[str, Any]],
    device_id: str,
    adapter_version_id: str,
    evidence: Mapping[str, Any] | None = None,
    observed_at: datetime | str | None = None,
    require_pinned_check_item_versions: bool = False,
) -> tuple[ObservationBatch, ...]:
    """Map named Parser record sets to quality-qualified canonical records."""

    normalized = validate_adapter_spec(
        spec,
        check_item_ids=tuple(check_items),
        check_items=check_items,
        require_pinned_check_item_versions=require_pinned_check_item_versions,
    )
    if isinstance(observed_at, datetime):
        timestamp = observed_at.astimezone(timezone.utc).isoformat()
    elif observed_at:
        timestamp = str(observed_at)
    else:
        timestamp = datetime.now(timezone.utc).isoformat()
    base_evidence = {
        **dict(evidence or {}),
        "adapter_schema_version": normalized["schema_version"],
        "adapter_projection_digest": adapter_projection_digest(normalized),
    }
    batches: list[ObservationBatch] = []
    for output in normalized["outputs"]:
        check_item_id = output["check_item_id"]
        check_item = dict(check_items.get(check_item_id) or {})
        check_item_version_id = str(
            output.get("check_item_version_id")
            or check_item.get("version_id")
            or check_item.get("check_item_version_id")
            or ""
        )
        source_rows = record_sets.get(output["source_record_set"])
        output_evidence = {
            **base_evidence,
            "check_item_id": check_item_id,
            "check_item_version_id": check_item_version_id,
            "source_record_set": output["source_record_set"],
        }
        if normalized["schema_version"] == ADAPTER_SCHEMA_VERSION:
            output_evidence["projection_dimensions_by_field"] = {
                field: dict(binding.get("dimensions") or {})
                for field, binding in _output_fields(
                    output,
                    version=ADAPTER_SCHEMA_VERSION,
                ).items()
                if isinstance(binding, Mapping) and binding.get("dimensions")
            }
        if source_rows is None:
            batches.append(
                _batch(
                    device_id=device_id,
                    check_item_id=check_item_id,
                    observed_at=timestamp,
                    quality_status="unavailable",
                    adapter_version_id=adapter_version_id,
                    check_item_version_id=check_item_version_id,
                    evidence=output_evidence,
                    errors=(f"missing record_set: {output['source_record_set']}",),
                )
            )
            continue
        if not source_rows:
            batches.append(
                _batch(
                    device_id=device_id,
                    check_item_id=check_item_id,
                    observed_at=timestamp,
                    quality_status=output["empty_status"],
                    adapter_version_id=adapter_version_id,
                    check_item_version_id=check_item_version_id,
                    evidence=output_evidence,
                )
            )
            continue

        source_rows, join_errors = _join_record_sets(
            source_rows,
            joins=output.get("record_set_joins") or [],
            record_sets=record_sets,
        )

        records: list[dict[str, Any]] = []
        errors: list[str] = list(join_errors)
        field_specs = _field_specs(check_item)
        fields = _output_fields(output, version=normalized["schema_version"])
        aggregate_dimensions = _aggregate_dimensions(check_item)
        for index, source in enumerate(source_rows):
            blank = [name for name in aggregate_dimensions if str(source.get(name) or "").strip() == ""]
            if blank:
                source = {**source, **dict.fromkeys(blank, AGGREGATE_DIMENSION)}
            try:
                mapped = {
                    target: (
                        project_binding(binding, source, target=target)
                        if normalized["schema_version"] == ADAPTER_SCHEMA_VERSION
                        else evaluate_binding(binding, source)
                    )
                    for target, binding in fields.items()
                }
                if normalized["schema_version"] == ADAPTER_SCHEMA_VERSION:
                    mapped[_PROJECTION_DIMENSIONS_FIELD] = (
                        _resolve_projection_dimensions(
                            fields,
                            source=source,
                            mapped=mapped,
                        )
                    )
                records.append(_normalize_mapped_record(mapped, field_specs))
            except (
                CommandAdapterError,
                SemanticProjectionError,
                ArithmeticError,
                TypeError,
                ValueError,
            ) as exc:
                errors.append(f"record {index}: {exc}")
        primary_keys = _primary_keys(check_item)
        accepted_records, key_errors = _validate_primary_keys(records, primary_keys)
        errors.extend(key_errors)
        projection_dimensions = (
            tuple(
                dict(record.pop(_PROJECTION_DIMENSIONS_FIELD, {}) or {})
                for record in accepted_records
            )
            if normalized["schema_version"] == ADAPTER_SCHEMA_VERSION
            else ()
        )
        quality_status = "complete" if not errors else (
            "partial" if accepted_records else "unavailable"
        )
        batches.append(
            _batch(
                device_id=device_id,
                check_item_id=check_item_id,
                observed_at=timestamp,
                quality_status=quality_status,
                adapter_version_id=adapter_version_id,
                check_item_version_id=check_item_version_id,
                records=tuple(accepted_records),
                evidence=output_evidence,
                projection_dimensions=projection_dimensions,
                errors=tuple(errors),
            )
        )
    return tuple(batches)


def evaluate_binding(binding: Any, source: Mapping[str, Any]) -> Any:
    """Evaluate the source/literal/arithmetic portion of a field binding."""

    normalized = _normalize_binding(binding, target="field", projection=False)
    return _evaluate_binding(normalized, source)


def project_binding(
    binding: Any,
    source: Mapping[str, Any],
    *,
    target: str,
) -> Any:
    """Evaluate a V2 binding and its approved semantic conversion DSL."""

    normalized = _normalize_binding(binding, target=target, projection=True)
    source_value = _evaluate_binding(normalized, source)
    semantics = {
        key: normalized[key]
        for key in (
            "transform",
            "value_mapping",
            "value_mapping_mode",
            "on_unmapped",
            "source_unit",
            "canonical_unit",
        )
        if key in normalized
    }
    if not semantics:
        return source_value
    return project_value(
        field_name=target,
        source_value=source_value,
        normalized_record=dict(source),
        field_semantics=semantics,
    )


def _resolve_projection_dimensions(
    fields: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    mapped: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Resolve V2 dimensions while both Parser and canonical fields are visible."""

    context = {**dict(source), **dict(mapped)}
    return {
        target: resolve_dimensions(binding["dimensions"], context)
        for target, binding in fields.items()
        if isinstance(binding, Mapping) and binding.get("dimensions")
    }


def _normalize_parser(raw: Any) -> dict[str, str]:
    parser = raw or {}
    if not isinstance(parser, Mapping):
        raise CommandAdapterError("adapter_spec.parser must be an object")
    template_id = str(parser.get("template_id") or "").strip()
    legacy_templates = parser.get("templates") or []
    if not template_id and legacy_templates:
        if not isinstance(legacy_templates, list):
            raise CommandAdapterError("adapter_spec.parser.templates must be an array")
        legacy_ids = {
            str(item.get("template_id") or "").strip()
            for item in legacy_templates
            if isinstance(item, Mapping)
            and str(item.get("template_id") or "").strip()
        }
        if len(legacy_ids) == 1:
            template_id = next(iter(legacy_ids))
        elif len(legacy_ids) > 1:
            raise CommandAdapterError(
                "one command adapter must reference one Parser asset; "
                "use a multi-record ParserBundle"
            )
    if not template_id:
        raise CommandAdapterError("adapter_spec.parser.template_id is required")
    return {"template_id": template_id}


def _normalize_record_set_joins(
    raw: Any,
    *,
    primary_record_set: str,
) -> list[dict[str, Any]]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise CommandAdapterError("record_set_joins must be an array")
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise CommandAdapterError("record_set_joins entries must be objects")
        source_record_set = str(item.get("source_record_set") or "").strip()
        on = {
            str(left).strip(): str(right).strip()
            for left, right in dict(item.get("on") or {}).items()
            if str(left).strip() and str(right).strip()
        }
        fields = sorted(
            {str(field).strip() for field in item.get("fields") or [] if str(field).strip()}
        )
        if not source_record_set or source_record_set == primary_record_set:
            raise CommandAdapterError(
                "record_set_joins.source_record_set must name another Parser record set"
            )
        if not on or not fields:
            raise CommandAdapterError("record_set_joins requires on and fields")
        normalized.append(
            {
                "source_record_set": source_record_set,
                "on": on,
                "fields": fields,
                "required": item.get("required", True) is not False,
            }
        )
    return normalized


def _join_record_sets(
    primary_rows: Sequence[Mapping[str, Any]],
    *,
    joins: Sequence[Mapping[str, Any]],
    record_sets: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = [dict(item) for item in primary_rows]
    errors: list[str] = []
    for join in joins:
        record_set = str(join.get("source_record_set") or "")
        joined_rows = record_sets.get(record_set)
        required = join.get("required", True) is not False
        if joined_rows is None:
            if required:
                errors.append(f"missing joined record_set: {record_set}")
            continue
        on = dict(join.get("on") or {})
        fields = [str(item) for item in join.get("fields") or []]
        index: dict[tuple[str, ...], dict[str, Any]] = {}
        duplicate_keys: set[tuple[str, ...]] = set()
        right_names = list(on.values())
        for joined in joined_rows:
            key = tuple(str(joined.get(name) or "").strip() for name in right_names)
            if not all(key):
                errors.append(f"joined record_set {record_set} has an empty join key")
                continue
            if key in index:
                duplicate_keys.add(key)
                continue
            index[key] = dict(joined)
        for key in sorted(duplicate_keys):
            errors.append(f"joined record_set {record_set} has duplicate key: {key}")
            index.pop(key, None)
        enriched: list[dict[str, Any]] = []
        left_names = list(on)
        for row_number, row in enumerate(rows):
            key = tuple(str(row.get(name) or "").strip() for name in left_names)
            matched = index.get(key)
            if matched is None:
                if required:
                    errors.append(
                        f"record {row_number}: no {record_set} row matches join key {key}"
                    )
                enriched.append({**row, **{field: None for field in fields}})
                continue
            merged = dict(row)
            for field in fields:
                if field in merged and merged[field] not in (None, ""):
                    errors.append(
                        f"record {row_number}: joined field would overwrite source field: {field}"
                    )
                    continue
                merged[field] = matched.get(field)
            enriched.append(merged)
        rows = enriched
    return rows, errors


AGGREGATE_DIMENSION = "aggregate"


def _aggregate_dimensions(check_item: Mapping[str, Any]) -> tuple[str, ...]:
    """Identity dimensions that subdivide one object, such as an optical channel.

    Under the same-name contract no projection can rename or default a Value, so
    a record that prints no token for such a dimension (an unchannelized optic)
    describes the object as a whole. It is recorded as "aggregate" rather than
    rejected, which used to drop every record of a single-lane transceiver.
    """

    contract = check_item.get("canonical_contract") or {}
    if contract.get("value_contract") != "same-name/v1":
        return ()
    subdivisions = {
        str(name)
        for row in contract.get("observations") or []
        for name in row.get("dimensions") or []
    }
    return tuple(
        name for name in contract.get("identity_dimensions") or [] if name in subdivisions
    )


def _output_fields(output: Mapping[str, Any], *, version: str) -> Mapping[str, Any]:
    if version == ADAPTER_SCHEMA_VERSION:
        projection = output.get("projection") or {}
        if not isinstance(projection, Mapping):
            raise CommandAdapterError("adapter_spec.outputs[].projection must be an object")
        return projection.get("fields") or {}
    return output.get("fields") or {}


def _normalize_binding(
    binding: Any,
    *,
    target: str,
    projection: bool,
) -> dict[str, Any]:
    if isinstance(binding, str):
        binding = {"source": binding}
    if not isinstance(binding, Mapping):
        raise CommandAdapterError(f"field mapping {target} must be an object or source name")
    normalized = dict(binding)
    modes = [key for key in ("source", "literal", "expression") if key in normalized]
    if len(modes) != 1:
        raise CommandAdapterError(
            f"field mapping {target} must define exactly one of source, literal, expression"
        )
    mode = modes[0]
    if mode in {"source", "expression"} and not str(normalized[mode]).strip():
        raise CommandAdapterError(f"field mapping {target} has an empty {mode}")
    result: dict[str, Any]
    if mode == "source":
        result = {"source": str(normalized[mode]).strip()}
    elif mode == "expression":
        expression = str(normalized[mode]).strip()
        tree = ast.parse(expression, mode="eval")
        _validate_expression(tree.body)
        result = {"expression": expression}
    else:
        result = {"literal": normalized[mode]}
    if not projection:
        return result

    transform = normalized.get("transform")
    if transform is not None:
        errors = validate_transform(transform)
        if errors:
            raise CommandAdapterError(
                f"field mapping {target} has an invalid transform: {'; '.join(errors)}"
            )
        result["transform"] = json.loads(json.dumps(transform, ensure_ascii=False))
    value_mapping = normalized.get("value_mapping")
    mapping_mode = normalized.get("value_mapping_mode", "casefold")
    on_unmapped = normalized.get("on_unmapped", "reject")
    if value_mapping is not None:
        errors = validate_value_mapping(
            value_mapping,
            mode=mapping_mode,
            on_unmapped=on_unmapped,
        )
        if errors:
            raise CommandAdapterError(
                f"field mapping {target} has an invalid value_mapping: {'; '.join(errors)}"
            )
        result["value_mapping"] = json.loads(
            json.dumps(value_mapping, ensure_ascii=False)
        )
        result["value_mapping_mode"] = str(mapping_mode or "casefold").strip()
        result["on_unmapped"] = str(on_unmapped or "reject").strip()
    elif "value_mapping_mode" in normalized or "on_unmapped" in normalized:
        raise CommandAdapterError(
            f"field mapping {target} declares value-mapping options without value_mapping"
        )
    source_unit = normalized.get("source_unit")
    canonical_unit = normalized.get("canonical_unit")
    if source_unit is not None or canonical_unit is not None:
        errors = validate_unit_conversion(
            source_unit=source_unit,
            target_unit=canonical_unit,
        )
        if errors:
            raise CommandAdapterError(
                f"field mapping {target} has an invalid unit conversion: {'; '.join(errors)}"
            )
        if source_unit is not None:
            result["source_unit"] = str(source_unit).strip()
        if canonical_unit is not None:
            result["canonical_unit"] = str(canonical_unit).strip()
    dimensions = normalized.get("dimensions")
    if dimensions is not None:
        try:
            result["dimensions"] = normalize_dimensions(dimensions)
        except SemanticProjectionError as exc:
            raise CommandAdapterError(
                f"field mapping {target} has invalid dimensions: {exc}"
            ) from exc
    return result


def _evaluate_binding(binding: Mapping[str, Any], source: Mapping[str, Any]) -> Any:
    if "literal" in binding:
        return binding["literal"]
    if "source" in binding:
        field = str(binding["source"])
        if field not in source:
            raise CommandAdapterError(f"source field is missing: {field}")
        return source[field]
    expression = str(binding["expression"])
    tree = ast.parse(expression, mode="eval")
    return _evaluate_expression(tree.body, source)


def _validate_expression(node: ast.AST) -> None:
    if isinstance(node, ast.Name):
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY_OPERATORS:
        _validate_expression(node.left)
        _validate_expression(node.right)
        return
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY_OPERATORS:
        _validate_expression(node.operand)
        return
    raise CommandAdapterError("mapping expressions only support fields and basic arithmetic")


def _evaluate_expression(node: ast.AST, source: Mapping[str, Any]) -> float:
    if isinstance(node, ast.Name):
        if node.id not in source:
            raise CommandAdapterError(f"source field is missing: {node.id}")
        value = source[node.id]
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise CommandAdapterError(
                f"source field is not numeric: {node.id}={value!r}"
            ) from exc
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        operator = _ALLOWED_BINARY_OPERATORS[type(node.op)]
        return operator(
            _evaluate_expression(node.left, source),
            _evaluate_expression(node.right, source),
        )
    if isinstance(node, ast.UnaryOp):
        operator = _ALLOWED_UNARY_OPERATORS[type(node.op)]
        return operator(_evaluate_expression(node.operand, source))
    raise CommandAdapterError("unsupported mapping expression")


def _canonical_rows(check_item: Mapping[str, Any]) -> list[dict[str, Any]]:
    contract = dict(check_item.get("canonical_contract") or {})
    return [
        dict(item)
        for item in contract.get("observations") or []
        if isinstance(item, Mapping)
    ]


def _primary_keys(check_item: Mapping[str, Any]) -> tuple[str, ...]:
    explicit = check_item.get("primary_keys")
    contract = check_item.get("canonical_contract") or {}
    values = explicit or (
        contract.get("primary_keys")
        or contract.get("identity_dimensions")
        or []
    )
    return tuple(
        str(item) for item in values if str(item) and str(item) != "device_id"
    )


def _field_specs(check_item: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    specs = {
        str(item.get("canonical_field") or "").strip(): dict(item)
        for item in _canonical_rows(check_item)
        if str(item.get("canonical_field") or "").strip()
    }
    contract = check_item.get("canonical_contract") or {}
    # Identity declarations carry semantics too. Previously scope=1 passed even
    # though the CheckItem only permits system/slot/processor.
    for name, rule in (contract.get("identity_dimension_rules") or {}).items():
        if name in _primary_keys(check_item):
            specs[name] = {**dict(rule), "required": True}
    return specs


def _validate_target_contract(
    *,
    check_item_id: str,
    pinned_version_id: str,
    fields: Mapping[str, Any],
    check_item: Mapping[str, Any] | None,
    require_pinned: bool,
) -> None:
    if require_pinned and not pinned_version_id:
        raise CommandAdapterError(
            f"command-adapter/v2 output {check_item_id} requires check_item_version_id"
        )
    if check_item is None:
        return
    expected_version = str(
        check_item.get("version_id") or check_item.get("check_item_version_id") or ""
    ).strip()
    if pinned_version_id and expected_version and pinned_version_id != expected_version:
        raise CommandAdapterError(
            f"adapter output {check_item_id} is pinned to {pinned_version_id}, "
            f"but the selected CheckItem version is {expected_version}"
        )
    field_specs = _field_specs(check_item)
    if (check_item.get("canonical_contract") or {}).get("value_contract") == "same-name/v1":
        for name, binding in fields.items():
            expected = {"source": name}
            dimensions = field_specs.get(name, {}).get("dimensions") or []
            if dimensions:
                expected["dimensions"] = {dimension: {"field": dimension} for dimension in dimensions}
            if dict(binding) != expected:
                bound = sorted(dict(binding.get("dimensions") or {}))
                if {key: value for key, value in dict(binding).items() if key != "dimensions"} == {"source": name}:
                    # Nothing is renamed or converted: the two sides only disagree on the dimensions.
                    raise CommandAdapterError(
                        f"同名 Value {name} 的维度不一致：检查项声明 {'、'.join(sorted(dimensions)) or '无'}，"
                        f"适配器输出 {'、'.join(bound) or '无'}"
                    )
                raise CommandAdapterError(f"同名 Value {name} 不允许改名或转换；请修正 TextFSM")
    primary_keys = set(_primary_keys(check_item))
    allowed = set(field_specs) | primary_keys
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise CommandAdapterError(
            f"adapter output {check_item_id} targets fields outside the CheckItem contract: "
            + ", ".join(unknown)
        )
    required = primary_keys | {
        field
        for field, details in field_specs.items()
        if details.get("required", True) is not False
    }
    missing = sorted(required - set(fields))
    if missing:
        raise CommandAdapterError(
            f"adapter output {check_item_id} does not cover required canonical fields: "
            + ", ".join(missing)
        )
    for target, binding in fields.items():
        details = field_specs.get(target)
        if not details:
            continue
        declared_unit = str(
            details.get("canonical_unit") or details.get("unit") or ""
        ).strip()
        mapping_unit = str(binding.get("canonical_unit") or "").strip()
        if declared_unit and mapping_unit and declared_unit.casefold() != mapping_unit.casefold():
            raise CommandAdapterError(
                f"adapter output {check_item_id} maps {target} to unit {mapping_unit}, "
                f"but the CheckItem declares {declared_unit}"
            )
        raw_dimensions = details.get("dimensions") or []
        if isinstance(raw_dimensions, Mapping):
            declared_dimensions = {
                str(name).strip()
                for name in raw_dimensions
                if str(name).strip()
            }
        elif isinstance(raw_dimensions, (list, tuple, set)):
            declared_dimensions = {
                str(name).strip()
                for name in raw_dimensions
                if str(name).strip()
            }
        else:
            raise CommandAdapterError(
                f"CheckItem declaration {target} has invalid dimensions"
            )
        mapped_dimensions = set(dict(binding.get("dimensions") or {}))
        missing_dimensions = sorted(declared_dimensions - mapped_dimensions)
        unknown_dimensions = sorted(mapped_dimensions - declared_dimensions)
        if missing_dimensions:
            raise CommandAdapterError(
                f"adapter output {check_item_id} does not map required dimensions "
                f"for {target}: {', '.join(missing_dimensions)}"
            )
        if unknown_dimensions:
            raise CommandAdapterError(
                f"adapter output {check_item_id} maps unknown dimensions for {target}: "
                + ", ".join(unknown_dimensions)
            )
    _validate_required_when_contract(
        check_item_id=check_item_id,
        field_specs=field_specs,
        fields=fields,
    )


def _validate_required_when_contract(
    *,
    check_item_id: str,
    field_specs: Mapping[str, Mapping[str, Any]],
    fields: Mapping[str, Any],
) -> None:
    """Make conditional requiredness explicit and projection-bound.

    The CheckItem owns the condition; the Adapter must still map both the
    guarded metric and its canonical state field.  That preserves fail-closed
    behavior for a present entity whose required measurement is missing.
    """

    for target, details in field_specs.items():
        raw = details.get("required_when")
        if raw is None:
            continue
        prefix = f"CheckItem {check_item_id} declaration {target}.required_when"
        if not isinstance(raw, Mapping):
            raise CommandAdapterError(f"{prefix} must be an object")
        unknown = sorted(set(raw) - {"field", "equals"})
        if unknown:
            raise CommandAdapterError(
                f"{prefix} has unsupported keys: " + ", ".join(unknown)
            )
        controller = str(raw.get("field") or "").strip()
        if not controller or controller not in field_specs:
            raise CommandAdapterError(
                f"{prefix}.field must reference a canonical field"
            )
        if controller not in fields:
            raise CommandAdapterError(
                f"adapter output {check_item_id} must map conditional field: {controller}"
            )
        if "equals" not in raw:
            raise CommandAdapterError(f"{prefix}.equals is required")
        expected = raw.get("equals")
        if expected is None or isinstance(expected, (dict, list, tuple, set)):
            raise CommandAdapterError(f"{prefix}.equals must be a non-null scalar")
        if isinstance(expected, str) and not expected.strip():
            raise CommandAdapterError(f"{prefix}.equals cannot be an empty string")
        if details.get("required", True) is not True:
            raise CommandAdapterError(f"{prefix} requires required: true")


def _normalize_mapped_record(
    record: Mapping[str, Any],
    field_specs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = dict(record)
    for field, spec in field_specs.items():
        value = normalized.get(field)
        if spec.get("collection") == "list":
            if value is None or value == "":
                value = []
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                raise CommandAdapterError(f"canonical field requires a list of non-empty text: {field}")
            normalized[field] = [item.strip() for item in value]
            if spec.get("unique_items") and len(set(normalized[field])) != len(value):
                raise CommandAdapterError(f"duplicate child identifiers: {field}")
            if _required_for_record(spec, normalized) and not value:
                raise CommandAdapterError(f"required canonical field is empty: {field}")
            continue
        if _required_for_record(spec, normalized) and (
            value is None or (isinstance(value, str) and not value.strip())
            or value == []
            or (isinstance(value, str) and value.strip().casefold() in {
                str(item).casefold() for item in spec.get("unavailable_values", [])
            })
        ):
            raise CommandAdapterError(f"required canonical field is empty: {field}")
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        value_type = str(spec.get("value_type") or "").strip().lower()
        if value_type == "integer":
            try:
                normalized[field] = int(str(value).strip())
            except (TypeError, ValueError) as exc:
                raise CommandAdapterError(f"canonical field is not integer: {field}={value!r}") from exc
        elif value_type in {"number", "percent"}:
            try:
                normalized[field] = float(str(value).strip().rstrip("%"))
            except (TypeError, ValueError) as exc:
                raise CommandAdapterError(
                    f"canonical field is not {value_type}: {field}={value!r}"
                ) from exc
        elif value_type == "boolean":
            token = str(value).strip().casefold()
            if token in {"true", "yes", "1"}:
                normalized[field] = True
            elif token in {"false", "no", "0"}:
                normalized[field] = False
            elif not isinstance(value, bool):
                raise CommandAdapterError(
                    f"canonical field is not boolean: {field}={value!r}"
                )
        elif isinstance(value, str):
            normalized[field] = value.strip()
        allowed_values = tuple(spec.get("allowed_values") or spec.get("values") or ())
        if allowed_values and normalized[field] not in allowed_values:
            raise CommandAdapterError(
                f"canonical field is outside allowed values: {field}={normalized[field]!r}"
            )
    for field, spec in field_specs.items():
        paired = spec.get("paired_with")
        if spec.get("collection") == "list" and paired:
            if paired not in field_specs or field_specs[paired].get("collection") != "list":
                raise CommandAdapterError(f"invalid paired list declaration: {field}")
            if len(normalized[field]) != len(normalized[paired]):
                raise CommandAdapterError(f"child lists must have equal lengths: {field}, {paired}")
    return normalized


def _required_for_record(
    spec: Mapping[str, Any],
    record: Mapping[str, Any],
) -> bool:
    """Evaluate a CheckItem's restricted per-record requiredness rule."""

    if spec.get("required", True) is not True:
        return False
    raw = spec.get("required_when")
    if raw is None:
        return True
    if not isinstance(raw, Mapping):
        raise CommandAdapterError("required_when must be an object")
    field = str(raw.get("field") or "").strip()
    if not field or "equals" not in raw:
        raise CommandAdapterError("required_when must define field and equals")
    return record.get(field) == raw.get("equals")


def _validate_primary_keys(
    records: Sequence[dict[str, Any]],
    primary_keys: Sequence[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    if not primary_keys:
        return list(records), []
    accepted: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for index, record in enumerate(records):
        values = tuple(str(record.get(key) or "").strip() for key in primary_keys)
        if not all(values):
            errors.append(f"record {index}: primary key is empty: {', '.join(primary_keys)}")
            continue
        if values in seen:
            errors.append(f"record {index}: duplicate primary key: {values}")
            continue
        seen.add(values)
        accepted.append(record)
    return accepted, errors


def _batch(
    *,
    device_id: str,
    check_item_id: str,
    observed_at: str,
    quality_status: str,
    adapter_version_id: str,
    check_item_version_id: str,
    records: tuple[dict[str, Any], ...] = (),
    evidence: Mapping[str, Any] | None = None,
    projection_dimensions: tuple[dict[str, dict[str, Any]], ...] = (),
    errors: tuple[str, ...] = (),
) -> ObservationBatch:
    if quality_status not in QUALITY_STATUSES:
        raise CommandAdapterError(f"invalid quality status: {quality_status}")
    return ObservationBatch(
        device_id=str(device_id),
        check_item_id=check_item_id,
        observed_at=observed_at,
        quality_status=quality_status,
        adapter_version_id=str(adapter_version_id),
        check_item_version_id=str(check_item_version_id),
        records=records,
        evidence=dict(evidence or {}),
        projection_dimensions=projection_dimensions,
        errors=errors,
    )
