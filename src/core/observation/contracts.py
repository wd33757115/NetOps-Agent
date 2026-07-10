"""Source-agnostic ObservationContract domain helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


CONTRACT_DIGEST_FIELDS = (
    "source_type",
    "schema_id",
    "entity_type",
    "primary_keys",
    "required_fields",
    "optional_fields",
    "validators",
    "ignore_fields",
    "allow_empty",
    "empty_patterns",
    "state_semantics",
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
    optional_fields: tuple[str, ...] = ()
    validators: dict[str, dict[str, Any]] | None = None
    ignore_fields: tuple[str, ...] = ()
    allow_empty: bool = False
    empty_patterns: tuple[str, ...] = ()
    state_semantics: dict[str, Any] | None = None
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
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "validators": self.validators or {},
            "ignore_fields": list(self.ignore_fields),
            "allow_empty": bool(self.allow_empty),
            "empty_patterns": list(self.empty_patterns),
            "state_semantics": self.state_semantics or {},
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
        "required_fields": _string_tuple(validation.get("required_fields")),
        "optional_fields": _string_tuple(validation.get("optional_fields")),
        "validators": _mapping(validation.get("validators")),
        "ignore_fields": _string_tuple(validation.get("ignore_fields")),
        "allow_empty": bool(validation.get("allow_empty", False)),
        "empty_patterns": _string_tuple(validation.get("empty_patterns")),
        "state_semantics": _mapping(validation.get("state_semantics")),
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
