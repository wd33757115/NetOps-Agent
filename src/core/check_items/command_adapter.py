"""Small, deterministic runtime for command-adapter field mappings."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


ADAPTER_SCHEMA_VERSION = "command-adapter/v1"
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


class CommandAdapterError(ValueError):
    """Raised when an adapter specification or mapping is invalid."""


@dataclass(frozen=True)
class ObservationBatch:
    device_id: str
    check_item_id: str
    observed_at: str
    quality_status: str
    adapter_version_id: str
    check_item_version_id: str
    records: tuple[dict[str, Any], ...]
    evidence: dict[str, Any]
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_adapter_spec(
    raw: Mapping[str, Any] | None,
    *,
    check_item_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate the compact adapter schema without inferring business semantics."""

    spec = dict(raw or {})
    if not spec:
        return {}
    version = str(spec.get("schema_version") or ADAPTER_SCHEMA_VERSION).strip()
    if version != ADAPTER_SCHEMA_VERSION:
        raise CommandAdapterError(f"unsupported adapter schema: {version}")

    parser = spec.get("parser") or {}
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

    outputs = spec.get("outputs") or []
    if not isinstance(outputs, list) or not outputs:
        raise CommandAdapterError("adapter_spec.outputs must contain at least one output")
    known_check_items = {str(item) for item in check_item_ids if str(item)}
    normalized_outputs: list[dict[str, Any]] = []
    output_check_items: set[str] = set()
    for item in outputs:
        if not isinstance(item, Mapping):
            raise CommandAdapterError("each adapter output must be an object")
        check_item_id = str(
            item.get("check_item_id") or item.get("check_item") or ""
        ).strip()
        source_record_set = str(item.get("source_record_set") or "").strip()
        fields = item.get("fields") or {}
        if not check_item_id or not source_record_set:
            raise CommandAdapterError(
                "each adapter output requires check_item_id and source_record_set"
            )
        if known_check_items and check_item_id not in known_check_items:
            raise CommandAdapterError(
                f"adapter output references an unlinked check item: {check_item_id}"
            )
        if not isinstance(fields, Mapping) or not fields:
            raise CommandAdapterError(
                f"adapter output {check_item_id} requires field mappings"
            )
        normalized_fields = {
            str(target).strip(): _normalize_binding(binding, target=str(target))
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
        output_check_items.add(check_item_id)
        normalized_outputs.append(
            {
                "check_item_id": check_item_id,
                "source_record_set": source_record_set,
                "fields": normalized_fields,
                "empty_status": empty_status,
            }
        )
    if known_check_items and output_check_items != known_check_items:
        missing = sorted(known_check_items - output_check_items)
        raise CommandAdapterError(
            "adapter outputs do not cover linked check items: " + ", ".join(missing)
        )
    return {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "parser": {"template_id": template_id},
        "outputs": normalized_outputs,
    }


def execute_adapter(
    spec: Mapping[str, Any],
    *,
    record_sets: Mapping[str, Sequence[Mapping[str, Any]]],
    check_items: Mapping[str, Mapping[str, Any]],
    device_id: str,
    adapter_version_id: str,
    evidence: Mapping[str, Any] | None = None,
    observed_at: datetime | str | None = None,
) -> tuple[ObservationBatch, ...]:
    """Map named parser record sets to canonical check-item records."""

    normalized = validate_adapter_spec(spec, check_item_ids=tuple(check_items))
    if isinstance(observed_at, datetime):
        timestamp = observed_at.astimezone(timezone.utc).isoformat()
    elif observed_at:
        timestamp = str(observed_at)
    else:
        timestamp = datetime.now(timezone.utc).isoformat()
    batches: list[ObservationBatch] = []
    for output in normalized["outputs"]:
        check_item_id = output["check_item_id"]
        check_item = dict(check_items.get(check_item_id) or {})
        check_item_version_id = str(
            check_item.get("version_id") or check_item.get("check_item_version_id") or ""
        )
        source_rows = record_sets.get(output["source_record_set"])
        if source_rows is None:
            batches.append(
                _batch(
                    device_id=device_id,
                    check_item_id=check_item_id,
                    observed_at=timestamp,
                    quality_status="unavailable",
                    adapter_version_id=adapter_version_id,
                    check_item_version_id=check_item_version_id,
                    evidence=evidence,
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
                    evidence=evidence,
                )
            )
            continue

        records: list[dict[str, Any]] = []
        errors: list[str] = []
        field_specs = _field_specs(check_item)
        for index, source in enumerate(source_rows):
            try:
                mapped = {
                        target: evaluate_binding(binding, source)
                        for target, binding in output["fields"].items()
                    }
                records.append(_normalize_mapped_record(mapped, field_specs))
            except (CommandAdapterError, ArithmeticError, TypeError, ValueError) as exc:
                errors.append(f"record {index}: {exc}")
        primary_keys = _primary_keys(check_item)
        accepted_records, key_errors = _validate_primary_keys(records, primary_keys)
        errors.extend(key_errors)
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
                evidence=evidence,
                errors=tuple(errors),
            )
        )
    return tuple(batches)


def evaluate_binding(binding: Any, source: Mapping[str, Any]) -> Any:
    normalized = _normalize_binding(binding, target="field")
    if "literal" in normalized:
        return normalized["literal"]
    if "source" in normalized:
        field = normalized["source"]
        if field not in source:
            raise CommandAdapterError(f"source field is missing: {field}")
        return source[field]
    expression = str(normalized["expression"])
    tree = ast.parse(expression, mode="eval")
    return _evaluate_expression(tree.body, source)


def _normalize_binding(binding: Any, *, target: str) -> dict[str, Any]:
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
    if mode == "source":
        return {"source": str(normalized[mode]).strip()}
    if mode == "expression":
        expression = str(normalized[mode]).strip()
        tree = ast.parse(expression, mode="eval")
        _validate_expression(tree.body)
        return {"expression": expression}
    return {"literal": normalized[mode]}


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
    contract = dict(check_item.get("canonical_contract") or {})
    return {
        str(item.get("canonical_field") or "").strip(): dict(item)
        for item in contract.get("observations") or []
        if isinstance(item, Mapping)
        and str(item.get("canonical_field") or "").strip()
    }


def _normalize_mapped_record(
    record: Mapping[str, Any],
    field_specs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = dict(record)
    for field, spec in field_specs.items():
        value = normalized.get(field)
        if spec.get("required") is True and (
            value is None or (isinstance(value, str) and not value.strip())
        ):
            raise CommandAdapterError(f"required canonical field is empty: {field}")
        if value is None:
            continue
        value_type = str(spec.get("value_type") or "").strip().lower()
        if value_type in {"number", "percent"}:
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
    return normalized


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
        errors=errors,
    )
