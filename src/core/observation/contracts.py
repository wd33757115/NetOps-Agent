"""Source-agnostic ObservationContract domain helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from src.core.observation.record_set_relations import (
    normalize_record_set_relations,
)


CONTRACT_DIGEST_FIELDS = (
    "source_type",
    "schema_id",
    "entity_type",
    "primary_keys",
    "canonical_entity_type",
    "entity_identity_fields",
    "entity_dimensions",
    "required_fields",
    "optional_fields",
    "validators",
    "ignore_fields",
    "allow_empty",
    "empty_patterns",
    "state_semantics",
    "record_sets",
    "record_set_relations",
)


@dataclass(frozen=True)
class ObservationContract:
    contract_id: str
    source_type: str
    schema_id: str
    schema_ref: str | None
    entity_type: str
    primary_keys: tuple[str, ...]
    required_fields: tuple[str, ...]
    canonical_entity_type: str | None = None
    entity_identity_fields: tuple[str, ...] = ()
    entity_dimensions: dict[str, Any] | None = None
    optional_fields: tuple[str, ...] = ()
    validators: dict[str, dict[str, Any]] | None = None
    ignore_fields: tuple[str, ...] = ()
    allow_empty: bool = False
    empty_patterns: tuple[str, ...] = ()
    state_semantics: dict[str, Any] | None = None
    record_sets: tuple[dict[str, Any], ...] = ()
    record_set_relations: tuple[dict[str, Any], ...] = ()
    version: int = 1
    contract_sha256: str | None = None
    status: str = "active"
    source_template_id: str | None = None
    compatibility: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["id"] = data.pop("contract_id")
        return data

    def to_validation_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "source_type": self.source_type,
            "schema_id": self.schema_id,
            "schema_ref": self.schema_ref,
            "entity_type": self.entity_type,
            "primary_keys": list(self.primary_keys),
            "canonical_entity_type": self.canonical_entity_type,
            "entity_identity_fields": list(self.entity_identity_fields),
            "entity_dimensions": self.entity_dimensions or {},
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "validators": self.validators or {},
            "ignore_fields": list(self.ignore_fields),
            "allow_empty": bool(self.allow_empty),
            "empty_patterns": list(self.empty_patterns),
            "state_semantics": self.state_semantics or {},
            "record_sets": [dict(item) for item in self.record_sets],
            "record_set_relations": [
                dict(item) for item in self.record_set_relations
            ],
            "contract_version": self.version,
            "contract_sha256": self.contract_sha256,
            "compatibility": self.compatibility or {},
        }


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value or {}, dict) else {}


def _record_sets(value: Any) -> tuple[dict[str, Any], ...]:
    if value in (None, (), []):
        return ()
    if isinstance(value, dict):
        source = [
            {"name": str(name), **dict(config)}
            for name, config in value.items()
            if isinstance(config, dict)
        ]
    elif isinstance(value, (list, tuple)):
        source = [dict(item) for item in value if isinstance(item, dict)]
        if len(source) != len(value):
            raise ValueError("ObservationContract record_sets must contain only objects")
    else:
        raise ValueError("ObservationContract record_sets must be an array or object")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(source):
        name = str(item.get("name") or item.get("entity_type") or "").strip()
        if not name:
            raise ValueError(f"ObservationContract record_sets[{index}] requires name")
        if name in seen:
            raise ValueError(f"duplicate ObservationContract record-set name: {name}")
        seen.add(name)
        normalized.append(
            {
                "name": name,
                "entity_type": str(item.get("entity_type") or item.get("entity") or name),
                "primary_keys": list(_string_tuple(item.get("primary_keys"))),
                "canonical_entity_type": (
                    str(item.get("canonical_entity_type")).strip()
                    if item.get("canonical_entity_type")
                    else None
                ),
                "entity_identity_fields": list(
                    _string_tuple(item.get("entity_identity_fields"))
                ),
                "entity_dimensions": _mapping(item.get("entity_dimensions")),
                "required_fields": list(_string_tuple(item.get("required_fields"))),
                "optional_fields": list(_string_tuple(item.get("optional_fields"))),
                "validators": _mapping(item.get("validators")),
                "ignore_fields": list(_string_tuple(item.get("ignore_fields"))),
                "allow_empty": bool(item.get("allow_empty", False)),
                "empty_patterns": list(_string_tuple(item.get("empty_patterns"))),
                "state_semantics": _mapping(item.get("state_semantics")),
            }
        )
    return tuple(normalized)


def build_contract_payload(
    *,
    source_type: str,
    schema_id: str,
    schema_ref: str | None = None,
    category: str | None = None,
    validation: dict[str, Any] | None = None,
    version: int | None = None,
    source_template_id: str | None = None,
    status: str = "active",
) -> dict[str, Any]:
    validation = dict(validation or {})
    entity_type = str(validation.get("entity_type") or category or "record")
    payload = {
        "source_type": str(source_type),
        "schema_id": str(schema_id),
        "schema_ref": schema_ref,
        "source_template_id": source_template_id,
        "entity_type": entity_type,
        "primary_keys": _string_tuple(validation.get("primary_keys")),
        "canonical_entity_type": (
            str(validation.get("canonical_entity_type")).strip()
            if validation.get("canonical_entity_type")
            else None
        ),
        "entity_identity_fields": _string_tuple(
            validation.get("entity_identity_fields")
        ),
        "entity_dimensions": _mapping(validation.get("entity_dimensions")),
        "required_fields": _string_tuple(validation.get("required_fields")),
        "optional_fields": _string_tuple(validation.get("optional_fields")),
        "validators": _mapping(validation.get("validators")),
        "ignore_fields": _string_tuple(validation.get("ignore_fields")),
        "allow_empty": bool(validation.get("allow_empty", False)),
        "empty_patterns": _string_tuple(validation.get("empty_patterns")),
        "state_semantics": _mapping(validation.get("state_semantics")),
        "record_sets": _record_sets(validation.get("record_sets")),
        "record_set_relations": tuple(
            normalize_record_set_relations(
                validation.get("record_set_relations")
            )
        ),
        "version": int(version or validation.get("contract_version") or 1),
        "status": str(status),
    }
    payload["contract_sha256"] = observation_contract_digest(payload)
    return payload


def observation_contract_digest(payload: dict[str, Any]) -> str:
    normalized = {
        key: payload.get(key)
        for key in CONTRACT_DIGEST_FIELDS
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=list,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
