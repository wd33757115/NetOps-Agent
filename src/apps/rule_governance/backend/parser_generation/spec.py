"""Small, stable Parser specification used by the v2 generation pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VALUE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ALLOWED_TYPES = {
    "text",
    "number",
    "percent",
    "enum",
    "interface_name",
    "ip",
    "ip_or_unassigned",
    "regex",
}
_ALLOWED_FIELD_ROLES = {
    "context",
    "identity",
    "metric",
    "state",
    "threshold",
}
_ALLOWED_CHANGE_BEHAVIORS = {"counter", "gauge", "stable", "volatile"}
_ALLOWED_CARDINALITIES = {"repeated", "singleton"}
_ALLOWED_PRESENCE = {"required", "optional"}


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    items = (value,) if isinstance(value, str) else value
    if not isinstance(items, (list, tuple, set)):
        raise ValueError("ParserSpec string collections must be arrays or strings")
    return tuple(
        dict.fromkeys(str(item).strip() for item in items if str(item).strip())
    )


@dataclass(frozen=True)
class FieldSpec:
    name: str
    required: bool
    value_type: str = "text"
    values: tuple[str, ...] = ()
    pattern: str | None = None
    source_labels: tuple[str, ...] = ()
    role: str | None = None
    operational_reason: str | None = None
    change_behavior: str | None = None
    monitor: bool | None = None
    canonical_candidate: str | None = None
    evidence: tuple[str, ...] = ()
    unavailable_tokens: tuple[str, ...] = ()
    primary_key_default: str | None = None

    def __post_init__(self) -> None:
        if not _VALUE_NAME_RE.fullmatch(self.name):
            raise ValueError(f"invalid ParserSpec field name: {self.name}")
        if self.value_type not in _ALLOWED_TYPES:
            raise ValueError(f"unsupported ParserSpec field type: {self.value_type}")
        if self.value_type == "enum" and not self.values:
            raise ValueError(f"enum field {self.name} requires values")
        if self.value_type == "regex" and not self.pattern:
            raise ValueError(f"regex field {self.name} requires pattern")
        if self.role is not None and self.role not in _ALLOWED_FIELD_ROLES:
            raise ValueError(f"unsupported ParserSpec field role: {self.role}")
        if (
            self.change_behavior is not None
            and self.change_behavior not in _ALLOWED_CHANGE_BEHAVIORS
        ):
            raise ValueError(f"unsupported ParserSpec change behavior: {self.change_behavior}")
        if self.canonical_candidate and not re.fullmatch(
            r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
            self.canonical_candidate,
        ):
            raise ValueError(f"invalid ParserSpec canonical candidate: {self.canonical_candidate}")
        if self.unavailable_tokens and self.value_type not in {"number", "percent"}:
            raise ValueError(
                "ParserSpec unavailable_tokens are supported only for number or percent fields"
            )
        if any(not str(item).strip() for item in self.unavailable_tokens):
            raise ValueError("ParserSpec unavailable_tokens cannot contain blank values")
        if self.primary_key_default is not None:
            default = str(self.primary_key_default)
            if not default.strip() or default != default.strip():
                raise ValueError(
                    "ParserSpec primary_key_default must be a non-blank trimmed value"
                )

    def validator(self) -> dict[str, Any]:
        if self.value_type == "enum":
            return {"type": "enum", "values": list(self.values)}
        if self.value_type == "regex":
            return {"type": "regex", "pattern": self.pattern}
        return {
            "type": self.value_type,
            **(
                {"unavailable_tokens": list(self.unavailable_tokens)}
                if self.unavailable_tokens
                else {}
            ),
        }


@dataclass(frozen=True)
class ParserSpec:
    entity: str
    primary_keys: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    allow_empty: bool = False
    empty_patterns: tuple[str, ...] = ()
    name: str | None = None
    cardinality: str = "repeated"
    expected_records: tuple[dict[str, Any], ...] = ()
    presence: str = "required"

    def __post_init__(self) -> None:
        if not _FIELD_NAME_RE.fullmatch(self.entity):
            raise ValueError(f"invalid ParserSpec entity: {self.entity}")
        if self.name is not None and not _FIELD_NAME_RE.fullmatch(self.name):
            raise ValueError(f"invalid ParserSpec record-set name: {self.name}")
        if self.cardinality not in _ALLOWED_CARDINALITIES:
            raise ValueError(f"unsupported ParserSpec record-set cardinality: {self.cardinality}")
        if self.presence not in _ALLOWED_PRESENCE:
            raise ValueError(f"unsupported ParserSpec record-set presence: {self.presence}")
        names = [field.name for field in self.fields]
        if not names or len(names) != len(set(names)):
            raise ValueError("ParserSpec fields must be non-empty and unique")
        if self.cardinality == "repeated" and not self.primary_keys:
            raise ValueError("ParserSpec requires at least one primary key")
        if self.cardinality == "singleton" and self.primary_keys:
            raise ValueError("singleton ParserSpec record sets cannot declare primary keys")
        missing_keys = sorted(set(self.primary_keys) - set(names))
        if missing_keys:
            raise ValueError(f"ParserSpec primary keys are not fields: {', '.join(missing_keys)}")
        optional_keys = [
            key
            for key in self.primary_keys
            if not self.field_map[key].required
            and self.field_map[key].primary_key_default is None
        ]
        if optional_keys:
            raise ValueError(
                "ParserSpec primary keys must be required or declare a "
                f"primary_key_default: {', '.join(optional_keys)}"
            )
        defaulted_non_keys = [
            field.name
            for field in self.fields
            if field.primary_key_default is not None and field.name not in self.primary_keys
        ]
        if defaulted_non_keys:
            raise ValueError(
                "ParserSpec primary_key_default is allowed only for primary keys: "
                + ", ".join(defaulted_non_keys)
            )
        if self.allow_empty and not self.empty_patterns:
            raise ValueError("allow_empty requires at least one explicit empty pattern")

    @property
    def field_map(self) -> dict[str, FieldSpec]:
        return {field.name: field for field in self.fields}

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if field.required)

    @property
    def optional_fields(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if not field.required)

    @property
    def primary_key_defaults(self) -> dict[str, str]:
        """Return validation-only defaults declared for primary-key fields."""

        keys = set(self.primary_keys)
        return {
            field.name: str(field.primary_key_default)
            for field in self.fields
            if field.name in keys and field.primary_key_default is not None
        }

    @property
    def validators(self) -> dict[str, dict[str, Any]]:
        return {field.name: field.validator() for field in self.fields}

    @property
    def record_set_name(self) -> str:
        return self.name or self.entity

    def to_dict(self, *, include_record_set_metadata: bool = False) -> dict[str, Any]:
        return {
            **({"name": self.name} if include_record_set_metadata and self.name else {}),
            "entity": self.entity,
            **({"cardinality": self.cardinality} if include_record_set_metadata else {}),
            **(
                {"presence": self.presence}
                if include_record_set_metadata and self.presence != "required"
                else {}
            ),
            "primary_keys": list(self.primary_keys),
            "fields": {
                field.name: {
                    "required": field.required,
                    "type": field.value_type,
                    **({"values": list(field.values)} if field.values else {}),
                    **({"pattern": field.pattern} if field.pattern else {}),
                    **({"source_labels": list(field.source_labels)} if field.source_labels else {}),
                    **({"role": field.role} if field.role else {}),
                    **(
                        {"operational_reason": field.operational_reason}
                        if field.operational_reason
                        else {}
                    ),
                    **({"change_behavior": field.change_behavior} if field.change_behavior else {}),
                    **({"monitor": field.monitor} if field.monitor is not None else {}),
                    **(
                        {"canonical_candidate": field.canonical_candidate}
                        if field.canonical_candidate
                        else {}
                    ),
                    **({"evidence": list(field.evidence)} if field.evidence else {}),
                    **(
                        {"unavailable_tokens": list(field.unavailable_tokens)}
                        if field.unavailable_tokens
                        else {}
                    ),
                    **(
                        {"primary_key_default": field.primary_key_default}
                        if field.primary_key_default is not None
                        else {}
                    ),
                }
                for field in self.fields
            },
            "allow_empty": self.allow_empty,
            "empty_patterns": list(self.empty_patterns),
            **(
                {"expected_records": [dict(item) for item in self.expected_records]}
                if include_record_set_metadata
                else {}
            ),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ParserSpec:
        raw_fields = payload.get("fields") or {}
        if not isinstance(raw_fields, dict):
            raise ValueError("ParserSpec fields must be an object")
        fields: list[FieldSpec] = []
        for name, raw_config in raw_fields.items():
            if not isinstance(raw_config, dict):
                raise ValueError(f"ParserSpec field {name} must be an object")
            fields.append(
                FieldSpec(
                    name=str(name),
                    required=bool(raw_config.get("required", False)),
                    value_type=str(raw_config.get("type") or "text"),
                    values=tuple(str(item) for item in raw_config.get("values") or ()),
                    pattern=str(raw_config.get("pattern") or "").strip() or None,
                    source_labels=tuple(
                        str(item) for item in raw_config.get("source_labels") or ()
                    ),
                    role=str(raw_config.get("role") or "").strip().casefold() or None,
                    operational_reason=(
                        str(raw_config.get("operational_reason") or "").strip() or None
                    ),
                    change_behavior=(
                        str(raw_config.get("change_behavior") or "").strip().casefold() or None
                    ),
                    monitor=(
                        bool(raw_config.get("monitor"))
                        if raw_config.get("monitor") is not None
                        else None
                    ),
                    canonical_candidate=(
                        str(raw_config.get("canonical_candidate") or "").strip() or None
                    ),
                    evidence=tuple(str(item) for item in raw_config.get("evidence") or ()),
                    unavailable_tokens=_string_tuple(
                        raw_config.get("unavailable_tokens")
                    ),
                    primary_key_default=(
                        str(raw_config.get("primary_key_default"))
                        if raw_config.get("primary_key_default") is not None
                        else None
                    ),
                )
            )
        primary_keys = tuple(str(item) for item in payload.get("primary_keys") or ())
        cardinality = str(payload.get("cardinality") or "").strip().casefold()
        if not cardinality:
            cardinality = "repeated" if primary_keys else "singleton"
        raw_expected = payload.get("expected_records") or ()
        if not isinstance(raw_expected, (list, tuple)):
            raise ValueError("ParserSpec expected_records must be an array")
        expected_records = tuple(dict(item) for item in raw_expected if isinstance(item, dict))
        if len(expected_records) != len(raw_expected):
            raise ValueError("ParserSpec expected_records must contain only objects")
        return cls(
            entity=str(payload.get("entity") or ""),
            primary_keys=primary_keys,
            fields=tuple(fields),
            allow_empty=bool(payload.get("allow_empty", False)),
            empty_patterns=tuple(str(item) for item in payload.get("empty_patterns") or ()),
            name=str(payload.get("name") or "").strip() or None,
            cardinality=cardinality,
            expected_records=expected_records,
            presence=str(payload.get("presence") or "required").strip().casefold(),
        )

    @classmethod
    def from_contract(cls, contract: dict[str, Any]) -> ParserSpec:
        required = tuple(str(item) for item in contract.get("required_fields") or ())
        optional = tuple(str(item) for item in contract.get("optional_fields") or ())
        validators = dict(contract.get("validators") or {})
        bindings = dict((contract.get("evidence") or {}).get("field_bindings") or {})
        fields: list[FieldSpec] = []
        for name in (*required, *optional):
            validator = dict(validators.get(name) or {})
            raw_bindings = bindings.get(name) or []
            binding_items = raw_bindings if isinstance(raw_bindings, list) else [raw_bindings]
            labels: list[str] = []
            for item in binding_items:
                if not isinstance(item, dict):
                    continue
                label = str(
                    item.get("source_label") or item.get("label") or item.get("header") or ""
                ).strip()
                if label and label not in labels:
                    labels.append(label)
            fields.append(
                FieldSpec(
                    name=name,
                    required=name in required,
                    value_type=str(validator.get("type") or "text"),
                    values=tuple(str(item) for item in validator.get("values") or ()),
                    pattern=str(validator.get("pattern") or "").strip() or None,
                    source_labels=tuple(labels),
                )
            )
        primary_keys = tuple(str(item) for item in contract.get("primary_keys") or ())
        return cls(
            entity=str(contract.get("entity_type") or contract.get("entity") or "record"),
            primary_keys=primary_keys,
            fields=tuple(fields),
            allow_empty=bool(contract.get("allow_empty", False)),
            empty_patterns=tuple(str(item) for item in contract.get("empty_patterns") or ()),
            cardinality="repeated" if primary_keys else "singleton",
        )


@dataclass(frozen=True)
class ParserSpecDocument:
    """Versioned semantic design for one command and one or more record sets."""

    business_purpose: str
    record_sets: tuple[ParserSpec, ...]
    version: int = 2

    def __post_init__(self) -> None:
        if self.version != 2:
            raise ValueError(f"unsupported ParserSpec document version: {self.version}")
        if not self.business_purpose.strip():
            raise ValueError("ParserSpec v2 requires business_purpose")
        if not self.record_sets:
            raise ValueError("ParserSpec v2 requires at least one record set")
        names = [item.record_set_name for item in self.record_sets]
        if len(names) != len(set(names)):
            raise ValueError("ParserSpec v2 record-set names must be unique")

    @property
    def is_multi_record(self) -> bool:
        return len(self.record_sets) > 1

    @property
    def single_record_set(self) -> ParserSpec:
        if len(self.record_sets) != 1:
            raise ValueError("ParserSpec contains multiple record sets")
        return self.record_sets[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "business_purpose": self.business_purpose,
            "record_sets": [
                item.to_dict(include_record_set_metadata=True) for item in self.record_sets
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ParserSpecDocument:
        raw_record_sets = payload.get("record_sets")
        if raw_record_sets is None:
            spec = ParserSpec.from_dict(payload)
            if spec.name is None:
                spec = ParserSpec(
                    entity=spec.entity,
                    primary_keys=spec.primary_keys,
                    fields=spec.fields,
                    allow_empty=spec.allow_empty,
                    empty_patterns=spec.empty_patterns,
                    name=spec.entity,
                    cardinality=spec.cardinality,
                    expected_records=spec.expected_records,
                    presence=spec.presence,
                )
            return cls(
                business_purpose=str(
                    payload.get("business_purpose")
                    or f"Parse operational records for {spec.entity}"
                ),
                record_sets=(spec,),
            )
        if isinstance(raw_record_sets, dict):
            normalized_sets = [
                {"name": name, **dict(config or {})}
                for name, config in raw_record_sets.items()
                if isinstance(config, dict)
            ]
        elif isinstance(raw_record_sets, list):
            normalized_sets = raw_record_sets
        else:
            raise ValueError("ParserSpec v2 record_sets must be an array or object")
        record_sets = tuple(
            ParserSpec.from_dict(dict(item)) for item in normalized_sets if isinstance(item, dict)
        )
        if len(record_sets) != len(normalized_sets):
            raise ValueError("ParserSpec v2 record_sets must contain only objects")
        return cls(
            version=int(payload.get("version") or 2),
            business_purpose=str(payload.get("business_purpose") or "").strip(),
            record_sets=record_sets,
        )


def normalize_parser_spec_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical persisted representation of a ParserSpec payload."""

    if payload.get("record_sets") is not None or str(payload.get("version") or "") == "2":
        return ParserSpecDocument.from_dict(payload).to_dict()
    return ParserSpec.from_dict(payload).to_dict()


def parser_spec_digest(payload: dict[str, Any]) -> str:
    """Return a stable digest for an already normalized ParserSpec payload."""

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
