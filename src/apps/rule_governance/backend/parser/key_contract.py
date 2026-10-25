"""Stable entity-key validation shared by parser generation, runtime, and diff."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

SINGLETON_ENTITY_KEY = "__singleton__"


@dataclass(frozen=True)
class KeyContractResult:
    valid: bool
    keys: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    duplicate_keys: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()


class EntityKeyError(ValueError):
    """Raised when parsed records violate their entity identity contract."""


def _field_value(record: dict[str, Any], field_name: str) -> Any:
    if field_name in record:
        return record[field_name]
    lowered = {str(key).lower(): key for key in record}
    actual = lowered.get(field_name.lower())
    return record.get(actual) if actual is not None else None


def _normalize_key_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value).strip()


def build_entity_key(record: dict[str, Any], primary_keys: Iterable[str]) -> str:
    """Build an unambiguous composite key and require every component."""
    fields = tuple(str(field) for field in primary_keys)
    if not fields:
        return SINGLETON_ENTITY_KEY
    values: list[str] = []
    for field in fields:
        value = _field_value(record, field)
        normalized = _normalize_key_value(value) if value is not None else ""
        if not normalized:
            raise EntityKeyError(f"primary key field {field!r} is missing or empty")
        values.append(normalized)
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def validate_entity_keys(
    records: list[dict[str, Any]],
    primary_keys: Iterable[str],
) -> KeyContractResult:
    """Require singleton cardinality or complete, unique collection keys."""
    fields = tuple(str(field) for field in primary_keys)
    if not records:
        return KeyContractResult(valid=True)
    if not fields:
        if len(records) == 1:
            return KeyContractResult(valid=True, keys=(SINGLETON_ENTITY_KEY,))
        return KeyContractResult(
            valid=False,
            errors=(
                "multiple records require explicit primary_keys; "
                f"received {len(records)} records",
            ),
        )

    keys: list[str] = []
    missing: set[str] = set()
    errors: list[str] = []
    for index, record in enumerate(records):
        try:
            keys.append(build_entity_key(record, fields))
        except EntityKeyError as exc:
            errors.append(f"record[{index}]: {exc}")
            for field in fields:
                value = _field_value(record, field)
                if value is None or not _normalize_key_value(value):
                    missing.add(field)

    counts: dict[str, int] = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    duplicates = tuple(sorted(key for key, count in counts.items() if count > 1))
    if duplicates:
        errors.append(f"duplicate entity keys: {', '.join(duplicates)}")
    return KeyContractResult(
        valid=not errors,
        keys=tuple(keys),
        errors=tuple(errors),
        duplicate_keys=duplicates,
        missing_fields=tuple(sorted(missing)),
    )


def index_records(
    records: list[dict[str, Any]],
    primary_keys: Iterable[str],
) -> dict[str, dict[str, Any]]:
    result = validate_entity_keys(records, primary_keys)
    if not result.valid:
        raise EntityKeyError("; ".join(result.errors))
    return {
        build_entity_key(record, primary_keys): record
        for record in records
    }
