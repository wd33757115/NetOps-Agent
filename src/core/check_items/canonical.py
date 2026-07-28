"""Versioned vendor-neutral observation catalog for check-item contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

DEFAULT_CANONICAL_CATALOG_PATH = (
    Path(__file__).parent / "assets" / "canonical_observations.v1.yaml"
)


@dataclass(frozen=True, slots=True)
class CanonicalObservationDefinition:
    id: str
    semantic_type: str
    value_types: tuple[str, ...]
    canonical_unit: str | None
    dimensions: tuple[str, ...]
    allowed_dimension_values: dict[str, tuple[str, ...]]
    assessment_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalObservationCatalog:
    catalog_id: str
    version: int
    status: str
    digest: str
    observations: tuple[CanonicalObservationDefinition, ...]

    def get(self, observation_type: str) -> CanonicalObservationDefinition | None:
        return next(
            (item for item in self.observations if item.id == observation_type),
            None,
        )


@lru_cache(maxsize=4)
def load_canonical_catalog(
    path: str | Path = DEFAULT_CANONICAL_CATALOG_PATH,
) -> CanonicalObservationCatalog:
    source = Path(path)
    content = source.read_bytes()
    payload = yaml.safe_load(content.decode("utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("canonical observation catalog must be an object")
    catalog_id = str(payload.get("catalog_id") or "").strip()
    version = int(payload.get("catalog_version") or 0)
    status = str(payload.get("status") or "").strip()
    if not catalog_id or version < 1 or status not in {"draft", "active", "deprecated"}:
        raise ValueError("canonical observation catalog metadata is invalid")
    rows = payload.get("observation_types")
    if not isinstance(rows, list) or not rows:
        raise ValueError("canonical observation catalog requires observation_types")
    observations: list[CanonicalObservationDefinition] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"observation_types[{index}] must be an object")
        observation_type = str(raw.get("id") or "").strip()
        if not observation_type or observation_type in seen:
            raise ValueError(
                f"canonical observation id is empty or duplicated: {observation_type}"
            )
        seen.add(observation_type)
        semantic_type = str(raw.get("semantic_type") or "").strip()
        value_types = tuple(str(item) for item in raw.get("value_types") or ())
        roles = tuple(str(item) for item in raw.get("assessment_roles") or ())
        dimensions = tuple(str(item) for item in raw.get("dimensions") or ())
        if not semantic_type or not value_types or not roles:
            raise ValueError(f"{observation_type} has incomplete semantic metadata")
        allowed = {
            str(name): tuple(str(value) for value in values or ())
            for name, values in dict(raw.get("allowed_dimension_values") or {}).items()
        }
        if set(allowed) - set(dimensions):
            raise ValueError(
                f"{observation_type} declares values for unknown dimensions"
            )
        observations.append(
            CanonicalObservationDefinition(
                id=observation_type,
                semantic_type=semantic_type,
                value_types=value_types,
                canonical_unit=(
                    str(raw.get("canonical_unit")).strip()
                    if raw.get("canonical_unit")
                    else None
                ),
                dimensions=dimensions,
                allowed_dimension_values=allowed,
                assessment_roles=roles,
            )
        )
    return CanonicalObservationCatalog(
        catalog_id=catalog_id,
        version=version,
        status=status,
        digest=hashlib.sha256(content).hexdigest(),
        observations=tuple(observations),
    )


def validate_check_item_contract(
    check_item: Any,
    *,
    catalog: CanonicalObservationCatalog | None = None,
) -> tuple[str, ...]:
    """Validate one CheckItem contract against approved canonical semantics."""

    catalog = catalog or load_canonical_catalog()
    check_item_id = str(
        getattr(check_item, "id", None)
        or (check_item.get("id") if isinstance(check_item, dict) else "")
        or "<unknown>"
    )
    contract = (
        getattr(check_item, "canonical_contract", None)
        if not isinstance(check_item, dict)
        else check_item.get("canonical_contract")
    )
    contract = dict(contract or {})
    rows = contract.get("observations") or []
    errors: list[str] = []
    identity_dimensions = tuple(
        str(item) for item in contract.get("identity_dimensions") or ()
    )
    identity_rules = contract.get("identity_dimension_rules") or {}
    if not isinstance(identity_rules, dict):
        errors.append(
            f"{check_item_id}.canonical_contract.identity_dimension_rules "
            "must be an object"
        )
        identity_rules = {}
    unknown_identity_rules = sorted(
        set(str(item) for item in identity_rules) - set(identity_dimensions)
    )
    if unknown_identity_rules:
        errors.append(
            f"{check_item_id} defines rules for unknown identity dimensions: "
            + ", ".join(unknown_identity_rules)
        )
    for dimension, raw_rule in identity_rules.items():
        if not isinstance(raw_rule, dict):
            errors.append(
                f"{check_item_id}.identity_dimension_rules.{dimension} "
                "must be an object"
            )
            continue
        kind = str(raw_rule.get("kind") or "").strip()
        if kind not in {"enum", "identifier"}:
            errors.append(
                f"{check_item_id}.identity_dimension_rules.{dimension}.kind "
                "must be enum or identifier"
            )
        allowed_values = raw_rule.get("allowed_values")
        if kind == "enum" and (
            not isinstance(allowed_values, list) or not allowed_values
        ):
            errors.append(
                f"{check_item_id}.identity_dimension_rules.{dimension} "
                "requires allowed_values"
            )
    if not isinstance(rows, list):
        return (f"{check_item_id}.canonical_contract.observations must be a list",)
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"{check_item_id}.observations[{index}] must be an object")
            continue
        observation_type = str(row.get("observation_type") or "").strip()
        if not observation_type:
            errors.append(f"{check_item_id}.observations[{index}] has no observation_type")
            continue
        if observation_type in seen:
            errors.append(f"{check_item_id} duplicates {observation_type}")
        seen.add(observation_type)
        definition = catalog.get(observation_type)
        if definition is None:
            errors.append(
                f"{check_item_id} references unapproved observation_type {observation_type}"
            )
            continue
        semantic_type = str(row.get("semantic_type") or "").strip()
        if semantic_type and semantic_type != definition.semantic_type:
            errors.append(
                f"{check_item_id}.{observation_type} semantic_type must be "
                f"{definition.semantic_type}"
            )
        unit = str(row.get("unit") or "").strip().casefold() or None
        if unit and unit != definition.canonical_unit:
            errors.append(
                f"{check_item_id}.{observation_type} unit must be "
                f"{definition.canonical_unit or '<none>'}"
            )
        dimensions = {str(item) for item in row.get("dimensions") or ()}
        unknown_dimensions = sorted(dimensions - set(definition.dimensions))
        if unknown_dimensions:
            errors.append(
                f"{check_item_id}.{observation_type} uses unapproved dimensions: "
                + ", ".join(unknown_dimensions)
            )
    return tuple(dict.fromkeys(errors))


def validate_check_item_catalog_contracts(
    check_items: Iterable[Any],
    *,
    catalog: CanonicalObservationCatalog | None = None,
) -> None:
    catalog = catalog or load_canonical_catalog()
    errors = [
        error
        for item in check_items
        for error in validate_check_item_contract(item, catalog=catalog)
    ]
    if errors:
        raise ValueError("invalid canonical check-item contracts: " + "; ".join(errors))
