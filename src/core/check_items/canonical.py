"""Versioned vendor-neutral observation catalog for check-item contracts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CANONICAL_CATALOG_PATH = (
    Path(__file__).parent / "assets" / "canonical_observations.yaml"
)


@dataclass(frozen=True, slots=True)
class CanonicalObservationDefinition:
    id: str
    description: str
    semantic_type: str
    value_types: tuple[str, ...]
    canonical_unit: str | None
    dimensions: tuple[str, ...]
    allowed_dimension_values: dict[str, tuple[str, ...]]
    allowed_values: tuple[Any, ...]
    state_groups: dict[str, tuple[Any, ...]]
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


def freeze_rule_semantics(contract: dict[str, Any]) -> dict[str, Any]:
    """Validate local definitions and freeze the actual CheckItem-owned semantics."""
    from .local_semantics import validate_local_definition, validate_local_references

    contract = deepcopy(contract)
    catalog = load_canonical_catalog()
    rows = contract.get('observations', [])
    snapshot = contract.get('rule_semantics_snapshot') or {}
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('observations 必须为字段对象数组')
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('observations', []), list):
        raise ValueError('语义快照格式无效')
    prior = {row['id']: row for row in snapshot.get('observations', []) if isinstance(row, dict) and 'id' in row}
    definitions = []
    for row in rows:
        identifier = str(row.get('observation_type') or '')
        template = catalog.get(identifier)
        raw = row['semantic_definition'] if 'semantic_definition' in row else prior.get(identifier) or (asdict(template) if template else None)
        if raw is None:
            raise ValueError(f'unknown canonical observation types: {identifier}')
        definition = validate_local_definition(raw, identifier)
        if 'semantic_definition' in row:
            if len(definition['assessment_roles']) != 1:
                raise ValueError(f'{identifier}: 每个字段只能选择一个评估用途')
            if definition['assessment_roles'] == ['current_state'] and definition['semantic_type'] in {'counter', 'threshold'}:
                raise ValueError(f'{identifier}: 只有状态和数值测量可以用于健康判断')
            row['semantic_definition'] = definition
            row['semantic_type'] = definition['semantic_type']
            row['assessment_roles'] = definition['assessment_roles']
            if row.get('change_tracking', True) is False:
                row['assessment_roles'] = [role for role in row['assessment_roles'] if role != 'change'] or ['evidence']
            row['description'] = definition['description']
            if row.get('value_type') not in definition['value_types']:
                raise ValueError(f'{identifier}: 值类型与语义定义不一致')
            row['unit'] = definition['canonical_unit']
        definitions.append(definition)
    validate_local_references(contract.get('observations', []))
    from .local_semantics import compile_health_policy
    for row in rows:
        if 'semantic_definition' in row:
            compile_health_policy(row)
    digest = hashlib.sha256(json.dumps(definitions, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {
        **contract,
        "rule_semantics_snapshot": {
            "catalog_id": catalog.catalog_id,
            "version": catalog.version,
            "digest": digest,
            "observations": definitions,
        },
    }


def rule_semantics_catalog(contract: dict[str, Any]) -> CanonicalObservationCatalog:
    """Legacy contracts use the installed catalog; new revisions use their snapshot."""
    snapshot = contract.get("rule_semantics_snapshot")
    if snapshot is None:
        return load_canonical_catalog()
    if not isinstance(snapshot, dict) or not snapshot.get("digest"):
        raise ValueError("invalid frozen rule semantics")
    definitions = tuple(CanonicalObservationDefinition(**row) for row in snapshot.get("observations", []))
    required = {row["observation_type"] for row in contract.get("observations", [])}
    if required - {item.id for item in definitions}:
        raise ValueError("frozen rule semantics do not cover the CheckItem")
    return CanonicalObservationCatalog(
        catalog_id=snapshot["catalog_id"], version=int(snapshot["version"]),
        status="active", digest=snapshot["digest"], observations=definitions,
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
        allowed_values = tuple(raw.get("allowed_values") or ())
        state_groups = {
            str(name): tuple(values or ())
            for name, values in dict(raw.get("state_groups") or {}).items()
        }
        grouped_values = [value for values in state_groups.values() for value in values]
        if len(set(grouped_values)) != len(grouped_values):
            raise ValueError(
                f"{observation_type} state_groups must not overlap"
            )
        if allowed_values and set(grouped_values) - set(allowed_values):
            raise ValueError(
                f"{observation_type} state_groups contain values outside allowed_values"
            )
        if (
            semantic_type in {"state", "enum", "boolean"}
            and "current_state" in roles
            and (
                not state_groups.get("healthy")
                or not state_groups.get("unhealthy")
            )
        ):
            raise ValueError(
                f"{observation_type} current-state semantics require healthy and unhealthy groups"
            )
        observations.append(
            CanonicalObservationDefinition(
                id=observation_type,
                description=str(raw.get("description") or "").strip(),
                semantic_type=semantic_type,
                value_types=value_types,
                canonical_unit=(
                    str(raw.get("canonical_unit")).strip()
                    if raw.get("canonical_unit")
                    else None
                ),
                dimensions=dimensions,
                allowed_dimension_values=allowed,
                allowed_values=allowed_values,
                state_groups=state_groups,
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


def _validate_event_constraints(
    *,
    prefix: str,
    raw: Any,
) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return [f"{prefix}.event_constraints must be an object"]

    errors: list[str] = []
    role_event_types_raw = raw.get("role_event_types")
    if not isinstance(role_event_types_raw, dict) or not role_event_types_raw:
        return [
            f"{prefix}.event_constraints.role_event_types "
            "must be a non-empty object"
        ]
    role_event_types = {
        str(role).strip(): str(event_type).strip()
        for role, event_type in role_event_types_raw.items()
        if str(role).strip() and str(event_type).strip()
    }
    if len(role_event_types) != len(role_event_types_raw):
        errors.append(
            f"{prefix}.event_constraints.role_event_types "
            "must contain non-empty string pairs"
        )

    declared_event_types = raw.get("required_event_types")
    if declared_event_types is None:
        event_types = set(role_event_types.values())
    elif not isinstance(declared_event_types, list):
        errors.append(
            f"{prefix}.event_constraints.required_event_types "
            "must be a list"
        )
        event_types = set()
    else:
        event_types = {
            str(item).strip()
            for item in declared_event_types
            if str(item).strip()
        }
        if len(event_types) != len(declared_event_types):
            errors.append(
                f"{prefix}.event_constraints.required_event_types "
                "must contain unique non-empty strings"
            )
        missing_role_types = sorted(
            set(role_event_types.values()) - event_types
        )
        if missing_role_types:
            errors.append(
                f"{prefix}.event_constraints.required_event_types "
                "does not include role event types: "
                + ", ".join(missing_role_types)
            )

    event_classes_raw = raw.get("required_event_classes")
    event_classes = (
        {
            str(event_type).strip(): str(event_class).strip()
            for event_type, event_class in event_classes_raw.items()
            if str(event_type).strip() and str(event_class).strip()
        }
        if isinstance(event_classes_raw, dict)
        else {}
    )
    if not isinstance(event_classes_raw, dict):
        errors.append(
            f"{prefix}.event_constraints.required_event_classes "
            "must be an object"
        )
    elif set(event_classes) != event_types:
        errors.append(
            f"{prefix}.event_constraints.required_event_classes "
            "must cover exactly the required event types"
        )
    invalid_classes = sorted(
        {
            value
            for value in event_classes.values()
            if value not in {"fault", "recovery", "change"}
        }
    )
    if invalid_classes:
        errors.append(
            f"{prefix}.event_constraints has invalid event classes: "
            + ", ".join(invalid_classes)
        )

    severities_raw = raw.get("required_severities")
    severities = (
        {
            str(event_type).strip(): str(severity).strip().casefold()
            for event_type, severity in severities_raw.items()
            if str(event_type).strip() and str(severity).strip()
        }
        if isinstance(severities_raw, dict)
        else {}
    )
    if not isinstance(severities_raw, dict):
        errors.append(
            f"{prefix}.event_constraints.required_severities "
            "must be an object"
        )
    elif set(severities) != event_types:
        errors.append(
            f"{prefix}.event_constraints.required_severities "
            "must cover exactly the required event types"
        )
    invalid_severities = sorted(
        {
            value
            for value in severities.values()
            if value
            not in {"info", "warning", "minor", "major", "critical"}
        }
    )
    if invalid_severities:
        errors.append(
            f"{prefix}.event_constraints has invalid severities: "
            + ", ".join(invalid_severities)
        )

    recovery_pairs_raw = raw.get("required_recovery_pairs")
    if not isinstance(recovery_pairs_raw, list):
        errors.append(
            f"{prefix}.event_constraints.required_recovery_pairs "
            "must be a list"
        )
        recovery_pairs_raw = []
    recovery_pairs: set[tuple[str, str]] = set()
    for index, item in enumerate(recovery_pairs_raw):
        if not isinstance(item, dict):
            errors.append(
                f"{prefix}.event_constraints.required_recovery_pairs"
                f"[{index}] must be an object"
            )
            continue
        pair = (
            str(item.get("fault") or "").strip(),
            str(item.get("recovery") or "").strip(),
        )
        if not all(pair):
            errors.append(
                f"{prefix}.event_constraints.required_recovery_pairs"
                f"[{index}] requires fault and recovery"
            )
            continue
        recovery_pairs.add(pair)
        fault_type, recovery_type = pair
        if event_classes.get(fault_type) != "fault":
            errors.append(
                f"{prefix}.{fault_type} must be declared as fault"
            )
        if event_classes.get(recovery_type) != "recovery":
            errors.append(
                f"{prefix}.{recovery_type} must be declared as recovery"
            )
    paired_recovery_types = {
        recovery_type for _fault_type, recovery_type in recovery_pairs
    }
    unpaired_recovery_types = sorted(
        {
            event_type
            for event_type, event_class in event_classes.items()
            if event_class == "recovery"
        }
        - paired_recovery_types
    )
    if unpaired_recovery_types:
        errors.append(
            f"{prefix}.event_constraints has recovery events without "
            "fault pairs: "
            + ", ".join(unpaired_recovery_types)
        )

    max_rule_count = raw.get("max_rule_count")
    if max_rule_count is not None:
        try:
            maximum = int(max_rule_count)
        except (TypeError, ValueError):
            maximum = 0
        if maximum < len(event_types):
            errors.append(
                f"{prefix}.event_constraints.max_rule_count must be "
                f"at least {len(event_types)}"
            )
    return errors


def _validate_required_when(
    *,
    check_item_id: str,
    observation_type: str,
    row: dict[str, Any],
    canonical_fields: set[str],
) -> list[str]:
    """Validate the small, data-only conditional-requiredness contract.

    A field can remain mandatory for normal records while being legitimately
    absent for an explicitly reported state.  This is intentionally not a
    general expression language: the condition compares one already-mapped
    canonical field with one scalar canonical value.
    """

    raw = row.get("required_when")
    if raw is None:
        return []
    prefix = f"{check_item_id}.{observation_type}.required_when"
    if not isinstance(raw, dict):
        return [f"{prefix} must be an object"]

    errors: list[str] = []
    unknown = sorted(set(raw) - {"field", "equals"})
    if unknown:
        errors.append(f"{prefix} has unsupported keys: " + ", ".join(unknown))
    field = str(raw.get("field") or "").strip()
    if not field:
        errors.append(f"{prefix}.field is required")
    elif field not in canonical_fields:
        errors.append(f"{prefix}.field must reference a canonical field")
    if "equals" not in raw:
        errors.append(f"{prefix}.equals is required")
    else:
        expected = raw.get("equals")
        if expected is None or isinstance(expected, (dict, list, tuple, set)):
            errors.append(f"{prefix}.equals must be a non-null scalar")
        elif isinstance(expected, str) and not expected.strip():
            errors.append(f"{prefix}.equals cannot be an empty string")
    if row.get("required", True) is not True:
        errors.append(f"{prefix} requires required: true")
    return errors


def validate_check_item_contract(
    check_item: Any,
    *,
    catalog: CanonicalObservationCatalog | None = None,
) -> tuple[str, ...]:
    """Validate one CheckItem contract against approved canonical semantics."""

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
    catalog = catalog or rule_semantics_catalog(contract)
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
    canonical_fields = {
        str(row.get("canonical_field") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("canonical_field") or "").strip()
    }
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"{check_item_id}.observations[{index}] must be an object")
            continue
        observation_type = str(row.get("observation_type") or "").strip()
        if "health_policy" in row:
            from src.core.check_items.numeric_health import validate_health_policy

            errors.extend(
                f"{check_item_id}.{observation_type}: {error}"
                for error in validate_health_policy(row["health_policy"])
            )
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
        fixed = row.get("fixed_dimensions") or {}
        if not isinstance(fixed, dict):
            errors.append(f"{check_item_id}.{observation_type} fixed_dimensions must be an object")
            fixed = {}
        for key, value in fixed.items():
            allowed = definition.allowed_dimension_values.get(key, ())
            if (isinstance(value, (dict, list)) or value in (None, "")
                    or (allowed and value not in allowed)):
                errors.append(
                    f"{check_item_id}.{observation_type} invalid fixed dimension {key}"
                )
        if set(fixed) & dimensions:
            errors.append(
                f"{check_item_id}.{observation_type} fixed and dynamic dimensions overlap"
            )
        dimensions |= set(fixed)
        unknown_dimensions = sorted(dimensions - set(definition.dimensions))
        if unknown_dimensions:
            errors.append(
                f"{check_item_id}.{observation_type} uses unapproved dimensions: "
                + ", ".join(unknown_dimensions)
            )
        required = row.get("required", True)
        if not isinstance(required, bool):
            errors.append(
                f"{check_item_id}.{observation_type} required must be a boolean"
            )
        errors.extend(
            _validate_required_when(
                check_item_id=check_item_id,
                observation_type=observation_type,
                row=row,
                canonical_fields=canonical_fields,
            )
        )
        errors.extend(
            _validate_event_constraints(
                prefix=f"{check_item_id}.{observation_type}",
                raw=row.get("event_constraints"),
            )
        )
    return tuple(dict.fromkeys(errors))


def validate_check_item_catalog_contracts(
    check_items: Iterable[Any],
    *,
    catalog: CanonicalObservationCatalog | None = None,
) -> None:
    errors = [
        error
        for item in check_items
        for error in validate_check_item_contract(item, catalog=catalog)
    ]
    if errors:
        raise ValueError("invalid canonical check-item contracts: " + "; ".join(errors))
