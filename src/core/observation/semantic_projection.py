"""Governed projection from parser-native fields to canonical observations."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DIMENSION_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CONTEXT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ALLOWED_DIMENSION_CONTEXT_FIELDS = frozenset({"device_id"})
_ALLOWED_OPERATIONS = frozenset(
    {
        "abs",
        "add",
        "coalesce",
        "concat",
        "divide",
        "max",
        "min",
        "multiply",
        "negate",
        "round",
        "subtract",
    }
)
_VALUE_MAPPING_MODES = frozenset({"casefold", "exact"})
_UNMAPPED_VALUE_POLICIES = frozenset({"reject", "passthrough"})
_UNIT_ALIASES = {
    "%": "percent",
    "b": "bytes",
    "byte": "bytes",
    "bytes": "bytes",
    "c": "celsius",
    "celsius": "celsius",
    "dbm": "dbm",
    "g": "gibibytes",
    "gb": "gigabytes",
    "gib": "gibibytes",
    "gibibyte": "gibibytes",
    "gibibytes": "gibibytes",
    "k": "kibibytes",
    "kb": "kilobytes",
    "kib": "kibibytes",
    "kibibyte": "kibibytes",
    "kibibytes": "kibibytes",
    "m": "mibibytes",
    "mb": "megabytes",
    "mib": "mibibytes",
    "mibibyte": "mibibytes",
    "mibibytes": "mibibytes",
    "percent": "percent",
}
_BYTE_FACTORS = {
    "bytes": 1.0,
    "kilobytes": 1_000.0,
    "megabytes": 1_000_000.0,
    "gigabytes": 1_000_000_000.0,
    "kibibytes": 1_024.0,
    "mibibytes": 1_048_576.0,
    "gibibytes": 1_073_741_824.0,
}


class SemanticProjectionError(ValueError):
    """Raised when approved semantic DSL cannot be evaluated safely."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _field_value(
    record: dict[str, Any],
    field_name: str,
    *,
    value_index: int | None,
) -> Any:
    if not _FIELD_RE.fullmatch(field_name):
        raise SemanticProjectionError(f"invalid transform field: {field_name}")
    value = record.get(field_name)
    if value_index is not None and isinstance(value, list):
        return value[value_index] if value_index < len(value) else None
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise SemanticProjectionError("boolean values cannot be used in numeric transforms")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().rstrip("%"))
        except ValueError as exc:
            raise SemanticProjectionError(
                f"non-numeric transform operand: {value!r}"
            ) from exc
    else:
        raise SemanticProjectionError(f"non-numeric transform operand: {value!r}")
    if not math.isfinite(number):
        raise SemanticProjectionError("transform operand must be finite")
    return number


def _evaluate_expression(
    expression: Any,
    record: dict[str, Any],
    *,
    value_index: int | None,
    context: dict[str, Any] | None = None,
    depth: int = 0,
) -> Any:
    if depth > 8:
        raise SemanticProjectionError("transform expression exceeds maximum depth")
    if not isinstance(expression, dict):
        return expression
    keys = set(expression)
    if keys == {"literal"}:
        return expression["literal"]
    if keys == {"field"}:
        return _field_value(
            record,
            str(expression["field"]),
            value_index=value_index,
        )
    if keys == {"context"}:
        context_name = str(expression.get("context") or "").strip()
        if (
            not _CONTEXT_RE.fullmatch(context_name)
            or context_name not in ALLOWED_DIMENSION_CONTEXT_FIELDS
        ):
            raise SemanticProjectionError(
                f"unsupported transform context: {context_name or '<empty>'}"
            )
        if context is None or context_name not in context:
            raise SemanticProjectionError(
                f"transform context is unavailable: {context_name}"
            )
        return context[context_name]
    operation = str(expression.get("op") or "").strip().casefold()
    if operation not in _ALLOWED_OPERATIONS:
        raise SemanticProjectionError(f"unsupported transform operation: {operation or '<empty>'}")
    args = expression.get("args")
    if not isinstance(args, list) or not args or len(args) > 8:
        raise SemanticProjectionError("transform args must contain between 1 and 8 items")
    values = [
        _evaluate_expression(
            item,
            record,
            value_index=value_index,
            context=context,
            depth=depth + 1,
        )
        for item in args
    ]
    if operation == "coalesce":
        return next((value for value in values if value not in (None, "")), None)
    if operation == "concat":
        result = "".join("" if value is None else str(value) for value in values)
        if len(result) > 8192:
            raise SemanticProjectionError(
                "concatenated transform value exceeds 8192 characters"
            )
        return result
    if operation == "abs":
        if len(values) != 1:
            raise SemanticProjectionError("abs requires exactly one argument")
        return abs(_number(values[0]))
    if operation == "negate":
        if len(values) != 1:
            raise SemanticProjectionError("negate requires exactly one argument")
        return -_number(values[0])
    if operation == "round":
        if len(values) not in {1, 2}:
            raise SemanticProjectionError("round requires one or two arguments")
        digits = int(_number(values[1])) if len(values) == 2 else 0
        if digits < 0 or digits > 12:
            raise SemanticProjectionError("round precision must be between 0 and 12")
        return round(_number(values[0]), digits)
    numbers = [_number(value) for value in values]
    if operation == "add":
        return sum(numbers)
    if operation == "multiply":
        result = 1.0
        for number in numbers:
            result *= number
        return result
    if operation == "subtract":
        if len(numbers) != 2:
            raise SemanticProjectionError("subtract requires exactly two arguments")
        return numbers[0] - numbers[1]
    if operation == "divide":
        if len(numbers) != 2:
            raise SemanticProjectionError("divide requires exactly two arguments")
        if numbers[1] == 0:
            raise SemanticProjectionError("division by zero")
        return numbers[0] / numbers[1]
    if operation == "min":
        return min(numbers)
    if operation == "max":
        return max(numbers)
    raise SemanticProjectionError(f"unsupported transform operation: {operation}")


def validate_transform(expression: Any) -> tuple[str, ...]:
    """Statically validate a transform without executing sample data."""

    errors: list[str] = []

    def visit(value: Any, path: str, depth: int) -> None:
        if depth > 8:
            errors.append(f"{path} exceeds maximum depth")
            return
        if not isinstance(value, dict):
            return
        keys = set(value)
        if keys == {"literal"}:
            return
        if keys == {"field"}:
            field_name = str(value.get("field") or "")
            if not _FIELD_RE.fullmatch(field_name):
                errors.append(f"{path}.field is invalid")
            return
        if keys == {"context"}:
            context_name = str(value.get("context") or "").strip()
            if (
                not _CONTEXT_RE.fullmatch(context_name)
                or context_name not in ALLOWED_DIMENSION_CONTEXT_FIELDS
            ):
                errors.append(
                    f"{path}.context is unsupported: "
                    f"{context_name or '<empty>'}"
                )
            return
        operation = str(value.get("op") or "").strip().casefold()
        if operation not in _ALLOWED_OPERATIONS:
            errors.append(f"{path}.op is unsupported: {operation or '<empty>'}")
        args = value.get("args")
        if not isinstance(args, list) or not args or len(args) > 8:
            errors.append(f"{path}.args must contain between 1 and 8 items")
            return
        for index, item in enumerate(args):
            visit(item, f"{path}.args[{index}]", depth + 1)

    visit(expression, "transform", 0)
    return tuple(dict.fromkeys(errors))


def transform_field_references(expression: Any) -> tuple[str, ...]:
    """Return parser-native fields referenced by an approved transform."""

    fields: set[str] = set()

    def visit(value: Any) -> None:
        if not isinstance(value, dict):
            return
        if set(value) == {"field"}:
            field_name = str(value.get("field") or "").strip()
            if _FIELD_RE.fullmatch(field_name):
                fields.add(field_name)
            return
        args = value.get("args")
        if isinstance(args, list):
            for item in args:
                visit(item)

    visit(expression)
    return tuple(sorted(fields))


def transform_context_references(expression: Any) -> tuple[str, ...]:
    """Return approved collection-context values referenced by an expression."""

    contexts: set[str] = set()

    def visit(value: Any) -> None:
        if not isinstance(value, dict):
            return
        if set(value) == {"context"}:
            context_name = str(value.get("context") or "").strip()
            if context_name in ALLOWED_DIMENSION_CONTEXT_FIELDS:
                contexts.add(context_name)
            return
        args = value.get("args")
        if isinstance(args, list):
            for item in args:
                visit(item)

    visit(expression)
    return tuple(sorted(contexts))


def normalize_dimensions(dimensions: Any) -> dict[str, Any]:
    if dimensions in (None, {}):
        return {}
    if not isinstance(dimensions, dict):
        raise SemanticProjectionError("dimensions must be an object")
    output: dict[str, Any] = {}
    for raw_name, raw_source in dimensions.items():
        name = str(raw_name).strip()
        if not _DIMENSION_RE.fullmatch(name):
            raise SemanticProjectionError(f"invalid dimension name: {name}")
        if isinstance(raw_source, dict):
            errors = validate_transform(raw_source)
            if errors:
                raise SemanticProjectionError(
                    f"dimension {name} expression is invalid: {'; '.join(errors)}"
                )
            output[name] = json.loads(_canonical_json(raw_source))
        else:
            output[name] = {"literal": raw_source}
    return output


def dimension_field_references(dimensions: Any) -> tuple[str, ...]:
    """Return parser-native fields referenced by a dimension declaration."""

    declared = normalize_dimensions(dimensions)
    return tuple(
        sorted(
            {
                field
                for source in declared.values()
                for field in transform_field_references(source)
            }
        )
    )


def dimension_context_references(dimensions: Any) -> tuple[str, ...]:
    """Return approved collection-context values used by dimensions."""

    declared = normalize_dimensions(dimensions)
    return tuple(
        sorted(
            {
                context
                for source in declared.values()
                for context in transform_context_references(source)
            }
        )
    )


def dimension_schema_signature(dimensions: Any) -> str:
    """Return a stable signature for the declared dimension mapping."""

    return _digest(normalize_dimensions(dimensions))


def resolve_dimensions(
    dimensions: Any,
    record: dict[str, Any],
    *,
    value_index: int | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    declared = normalize_dimensions(dimensions)
    resolved: dict[str, Any] = {}
    for name, source in declared.items():
        value = _evaluate_expression(
            source,
            record,
            value_index=value_index,
            context=context,
        )
        if value in (None, ""):
            raise SemanticProjectionError(f"dimension {name} resolved to an empty value")
        resolved[name] = value
    if value_index is not None and "value_index" not in resolved:
        resolved["value_index"] = value_index + 1
    return resolved


def dimension_value_signature(dimensions: dict[str, Any]) -> str:
    return _digest(dict(sorted(dimensions.items())))


def normalize_unit(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    return _UNIT_ALIASES.get(text, text)


def convert_unit(value: Any, *, source_unit: Any, target_unit: Any) -> Any:
    source = normalize_unit(source_unit)
    target = normalize_unit(target_unit)
    if not source or not target or source == target:
        return value
    if source in _BYTE_FACTORS and target in _BYTE_FACTORS:
        return _number(value) * _BYTE_FACTORS[source] / _BYTE_FACTORS[target]
    raise SemanticProjectionError(f"unsupported unit conversion: {source} -> {target}")


def validate_unit_conversion(*, source_unit: Any, target_unit: Any) -> tuple[str, ...]:
    """Validate a declared conversion without requiring a sample value."""

    source = normalize_unit(source_unit)
    target = normalize_unit(target_unit)
    if not source or not target or source == target:
        return ()
    if source in _BYTE_FACTORS and target in _BYTE_FACTORS:
        return ()
    return (f"unsupported unit conversion: {source} -> {target}",)


def _value_mapping_key(value: Any, *, mode: str) -> str:
    text = "" if value is None else str(value).strip()
    return text if mode == "exact" else text.casefold()


def validate_value_mapping(
    mapping: Any,
    *,
    mode: Any = "casefold",
    on_unmapped: Any = "reject",
) -> tuple[str, ...]:
    """Validate an approved source-enum to canonical-value mapping."""

    errors: list[str] = []
    normalized_mode = str(mode or "casefold").strip().casefold()
    normalized_policy = str(on_unmapped or "reject").strip().casefold()
    if normalized_mode not in _VALUE_MAPPING_MODES:
        errors.append(
            "value_mapping_mode must be one of: "
            + ", ".join(sorted(_VALUE_MAPPING_MODES))
        )
    if normalized_policy not in _UNMAPPED_VALUE_POLICIES:
        errors.append(
            "on_unmapped must be one of: "
            + ", ".join(sorted(_UNMAPPED_VALUE_POLICIES))
        )
    if not isinstance(mapping, dict) or not mapping:
        errors.append("value_mapping must be a non-empty object")
        return tuple(dict.fromkeys(errors))
    if len(mapping) > 512:
        errors.append("value_mapping cannot contain more than 512 entries")
    seen: dict[str, str] = {}
    for raw_key, mapped_value in mapping.items():
        key_text = str(raw_key)
        if len(key_text) > 512:
            errors.append("value_mapping source keys cannot exceed 512 characters")
        normalized_key = _value_mapping_key(
            raw_key,
            mode=(
                normalized_mode
                if normalized_mode in _VALUE_MAPPING_MODES
                else "casefold"
            ),
        )
        previous = seen.get(normalized_key)
        if previous is not None and previous != key_text:
            errors.append(
                "value_mapping contains duplicate normalized source values: "
                f"{previous!r}, {key_text!r}"
            )
        seen[normalized_key] = key_text
        if mapped_value is None or isinstance(mapped_value, (dict, list)):
            errors.append(
                f"value_mapping[{key_text!r}] must map to a non-null scalar"
            )
        elif isinstance(mapped_value, str) and not mapped_value.strip():
            errors.append(
                f"value_mapping[{key_text!r}] cannot map to an empty string"
            )
    return tuple(dict.fromkeys(errors))


def apply_value_mapping(
    value: Any,
    mapping: Any,
    *,
    mode: Any = "casefold",
    on_unmapped: Any = "reject",
) -> Any:
    """Map one source value through an immutable, fail-closed Contract table."""

    errors = validate_value_mapping(
        mapping,
        mode=mode,
        on_unmapped=on_unmapped,
    )
    if errors:
        raise SemanticProjectionError("; ".join(errors))
    normalized_mode = str(mode or "casefold").strip().casefold()
    normalized_policy = str(on_unmapped or "reject").strip().casefold()
    indexed = {
        _value_mapping_key(key, mode=normalized_mode): mapped
        for key, mapped in mapping.items()
    }
    source_key = _value_mapping_key(value, mode=normalized_mode)
    if source_key in indexed:
        return indexed[source_key]
    if normalized_policy == "passthrough":
        return value
    raise SemanticProjectionError(
        f"source value {value!r} is not covered by the approved value_mapping"
    )


def project_value(
    *,
    field_name: str,
    source_value: Any,
    normalized_record: dict[str, Any],
    field_semantics: dict[str, Any],
    value_index: int | None = None,
) -> Any:
    """Apply only the approved expression and unit-conversion DSL."""

    transform = field_semantics.get("transform")
    if transform is None:
        value = source_value
    else:
        errors = validate_transform(transform)
        if errors:
            raise SemanticProjectionError("; ".join(errors))
        value = _evaluate_expression(
            transform,
            normalized_record,
            value_index=value_index,
        )
    mapping = field_semantics.get("value_mapping")
    if mapping is not None:
        value = apply_value_mapping(
            value,
            mapping,
            mode=field_semantics.get("value_mapping_mode") or "casefold",
            on_unmapped=field_semantics.get("on_unmapped") or "reject",
        )
    source_unit = field_semantics.get("source_unit") or field_semantics.get("unit")
    target_unit = field_semantics.get("canonical_unit") or field_semantics.get("unit")
    return convert_unit(value, source_unit=source_unit, target_unit=target_unit)
