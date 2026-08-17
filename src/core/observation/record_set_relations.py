"""Validated relations between independent structured-record sets.

The relation contract is vendor-neutral.  An LLM may propose which fields are
related, but this module proves the join against sanitized records before the
relationship can become runtime state semantics.
"""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any

from src.core.parser.value_types import is_unavailable_value


THRESHOLD_REFERENCE_ROLES = frozenset(
    {"low", "high", "critical_low", "critical_high"}
)
MATCH_POLICIES = frozenset({"partial", "required"})
_RUNTIME_REFERENCE_PREFIX = "__record_set_reference__"


def _threshold_role_from_source_hint(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    explicit_level = str(value.get("threshold_level") or "").strip().casefold()
    explicit_direction = str(
        value.get("threshold_direction") or ""
    ).strip().casefold()
    labels = [
        str(item).strip()
        for item in value.get("source_labels") or []
        if str(item).strip()
    ]
    hint_role = str(value.get("role") or "").strip()
    words = set(
        re.findall(
            r"[a-z0-9]+",
            " ".join([explicit_level, explicit_direction, hint_role, *labels]).casefold(),
        )
    )
    critical = explicit_level in {"alarm", "critical"} or bool(
        {"alarm", "critical"}.intersection(words)
    )
    warning = explicit_level in {"warning", "warn"} or bool(
        {"warning", "warn"}.intersection(words)
    )
    low = explicit_direction == "low" or bool(
        {"low", "lower", "minimum", "min"}.intersection(words)
    )
    high = explicit_direction == "high" or bool(
        {"high", "upper", "maximum", "max"}.intersection(words)
    )
    if critical == warning or low == high:
        return None
    direction = "low" if low else "high"
    return f"critical_{direction}" if critical else direction


def reconcile_record_set_relation_roles(contract: dict[str, Any]) -> dict[str, Any]:
    """Reconcile model-proposed roles with approved ParserSpec source labels."""

    output = copy.deepcopy(dict(contract or {}))
    record_sets = {
        str(item.get("name") or "").strip(): item
        for item in output.get("record_sets") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    normalizations: list[dict[str, Any]] = []
    errors: list[str] = []
    relations: list[dict[str, Any]] = []
    for raw_relation in output.get("record_set_relations") or []:
        if not isinstance(raw_relation, dict):
            relations.append(raw_relation)
            continue
        relation = copy.deepcopy(raw_relation)
        relation_name = str(relation.get("name") or "").strip()
        if not relation_name:
            relation_name = (
                f"{str(relation.get('source_record_set') or '').strip()}_to_"
                f"{str(relation.get('target_record_set') or '').strip()}"
            ).strip("_")
        target_name = str(relation.get("target_record_set") or "").strip()
        target_set = record_sets.get(target_name) or {}
        source_hints = dict(target_set.get("source_field_hints") or {})
        bindings: dict[str, dict[str, str]] = {}
        for source_field, raw_roles in dict(
            relation.get("threshold_bindings") or {}
        ).items():
            if not isinstance(raw_roles, dict):
                continue
            effective_roles: dict[str, str] = {}
            for proposed_role, target_field in raw_roles.items():
                proposed = str(proposed_role).strip().casefold()
                target = str(target_field).strip()
                evidence_role = _threshold_role_from_source_hint(
                    source_hints.get(target)
                )
                effective = evidence_role or proposed
                previous = effective_roles.get(effective)
                if previous and previous != target:
                    errors.append(
                        f"relation {relation.get('name')!r} maps both {previous!r} "
                        f"and {target!r} to evidenced role {effective!r}"
                    )
                    continue
                effective_roles[effective] = target
                if evidence_role and evidence_role != proposed:
                    normalizations.append(
                        {
                            "kind": "threshold_role_from_approved_source_label",
                            "relation_name": relation_name,
                            "target_record_set": target_name,
                            "target_field": target,
                            "proposed_role": proposed,
                            "effective_role": evidence_role,
                            "source_labels": list(
                                dict(source_hints.get(target) or {}).get(
                                    "source_labels"
                                )
                                or []
                            ),
                        }
                    )
            if effective_roles:
                bindings[str(source_field)] = effective_roles
        relation["threshold_bindings"] = bindings
        relations.append(relation)
    output["record_set_relations"] = relations
    return {
        "success": not errors,
        "contract": output,
        "normalizations": normalizations,
        "errors": errors,
    }


def _record_set_fields(record_set: dict[str, Any]) -> set[str]:
    return {
        str(field)
        for key in (
            "primary_keys",
            "required_fields",
            "optional_fields",
        )
        for field in record_set.get(key) or []
        if str(field).strip()
    } | {
        str(field)
        for field in dict(record_set.get("validators") or {})
        if str(field).strip()
    } | {
        str(field)
        for field in dict(record_set.get("state_semantics") or {})
        if str(field).strip()
    }


def _join_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return str(value)


def _join_key(
    record: dict[str, Any],
    fields: list[str],
) -> tuple[str, ...] | None:
    values = tuple(_join_value(record.get(field)) for field in fields)
    if not values or any(value is None for value in values):
        return None
    return tuple(str(value) for value in values)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_record_set_relations(value: Any) -> list[dict[str, Any]]:
    """Return the canonical, JSON-safe relation representation."""

    if value in (None, (), []):
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("record_set_relations must be an array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError(
                f"record_set_relations[{index}] must be an object"
            )
        source = str(raw.get("source_record_set") or "").strip()
        target = str(raw.get("target_record_set") or "").strip()
        name = str(raw.get("name") or f"{source}_to_{target}").strip()
        if not source or not target:
            raise ValueError(
                f"record_set_relations[{index}] requires source_record_set "
                "and target_record_set"
            )
        if not name or name in seen:
            raise ValueError(f"duplicate record-set relation name: {name}")
        seen.add(name)
        join_keys = {
            str(source_field).strip(): str(target_field).strip()
            for source_field, target_field in dict(
                raw.get("join_keys") or {}
            ).items()
            if str(source_field).strip() and str(target_field).strip()
        }
        bindings: dict[str, dict[str, str]] = {}
        for source_field, raw_roles in dict(
            raw.get("threshold_bindings") or {}
        ).items():
            source_field = str(source_field).strip()
            if not source_field or not isinstance(raw_roles, dict):
                continue
            roles = {
                str(role).strip().casefold(): str(target_field).strip()
                for role, target_field in raw_roles.items()
                if str(role).strip() and str(target_field).strip()
            }
            if roles:
                bindings[source_field] = roles
        match_policy = str(raw.get("match_policy") or "partial").strip().casefold()
        normalized.append(
            {
                "name": name,
                "source_record_set": source,
                "target_record_set": target,
                "join_keys": join_keys,
                "threshold_bindings": bindings,
                "match_policy": match_policy,
            }
        )
    return normalized


def _relation_groups(
    record_groups: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    groups = [
        {
            "group_id": str(group.get("group_id") or f"group-{index + 1}"),
            "records": [
                dict(record)
                for record in group.get("records") or []
                if isinstance(record, dict)
            ],
        }
        for index, group in enumerate(record_groups or [])
        if isinstance(group, dict)
    ]
    return groups


def apply_record_set_relations(
    *,
    contract: dict[str, Any],
    record_groups: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Validate relation evidence and compile it into field semantics."""

    reconciliation = reconcile_record_set_relation_roles(contract)
    output = dict(reconciliation.get("contract") or contract or {})
    role_normalizations = list(reconciliation.get("normalizations") or [])
    reconciliation_errors = list(reconciliation.get("errors") or [])
    try:
        relations = normalize_record_set_relations(
            output.get("record_set_relations")
        )
    except ValueError as exc:
        return {
            "success": False,
            "contract": output,
            "relations": [],
            "reports": [],
            "role_normalizations": role_normalizations,
            "errors": [*reconciliation_errors, str(exc)],
        }
    output["record_set_relations"] = relations
    if not relations:
        return {
            "success": not reconciliation_errors,
            "contract": output,
            "relations": [],
            "reports": [],
            "role_normalizations": role_normalizations,
            "errors": reconciliation_errors,
        }

    record_sets = [
        dict(item)
        for item in output.get("record_sets") or []
        if isinstance(item, dict)
    ]
    by_name = {
        str(item.get("name") or "").strip(): item
        for item in record_sets
        if str(item.get("name") or "").strip()
    }
    groups = _relation_groups(record_groups)
    errors: list[str] = list(reconciliation_errors)
    reports: list[dict[str, Any]] = []
    claimed_roles: dict[tuple[str, str, str], str] = {}

    for relation in relations:
        name = relation["name"]
        source_name = relation["source_record_set"]
        target_name = relation["target_record_set"]
        source_set = by_name.get(source_name)
        target_set = by_name.get(target_name)
        relation_errors: list[str] = []
        if source_name == target_name:
            relation_errors.append("source and target record sets must differ")
        if source_set is None:
            relation_errors.append(f"unknown source record set {source_name!r}")
        if target_set is None:
            relation_errors.append(f"unknown target record set {target_name!r}")
        if not relation["join_keys"]:
            relation_errors.append("join_keys must not be empty")
        if not relation["threshold_bindings"]:
            relation_errors.append("threshold_bindings must not be empty")
        if relation["match_policy"] not in MATCH_POLICIES:
            relation_errors.append(
                "match_policy must be one of " + ", ".join(sorted(MATCH_POLICIES))
            )

        source_fields = _record_set_fields(source_set or {})
        target_fields = _record_set_fields(target_set or {})
        for source_field, target_field in relation["join_keys"].items():
            if source_field not in source_fields:
                relation_errors.append(
                    f"source join field {source_field!r} is not declared by {source_name}"
                )
            if target_field not in target_fields:
                relation_errors.append(
                    f"target join field {target_field!r} is not declared by {target_name}"
                )

        for source_field, role_bindings in relation["threshold_bindings"].items():
            if source_field not in source_fields:
                relation_errors.append(
                    f"monitored field {source_field!r} is not declared by {source_name}"
                )
            source_semantics = dict(
                (source_set or {}).get("state_semantics") or {}
            ).get(source_field)
            if not isinstance(source_semantics, dict) or not str(
                source_semantics.get("observation_type") or ""
            ).strip():
                relation_errors.append(
                    f"monitored field {source_name}.{source_field} has no observation_type"
                )
            for role, target_field in role_bindings.items():
                if role not in THRESHOLD_REFERENCE_ROLES:
                    relation_errors.append(
                        f"unsupported threshold role {role!r} for {source_field}"
                    )
                if target_field not in target_fields:
                    relation_errors.append(
                        f"threshold field {target_field!r} is not declared by {target_name}"
                    )
                claim_key = (source_name, source_field, role)
                previous = claimed_roles.get(claim_key)
                if previous and previous != name:
                    relation_errors.append(
                        f"{source_name}.{source_field}.{role} is claimed by both "
                        f"{previous!r} and {name!r}"
                    )
                claimed_roles[claim_key] = name

        source_key_fields = list(relation["join_keys"])
        target_key_fields = list(relation["join_keys"].values())
        source_count = 0
        matched_count = 0
        unmatched_count = 0
        matched_values: dict[str, int] = {
            f"{source_field}.{role}": 0
            for source_field, roles in relation["threshold_bindings"].items()
            for role in roles
        }
        target_validators = dict((target_set or {}).get("validators") or {})
        for group in groups:
            source_rows = [
                record
                for record in group["records"]
                if str(record.get("__record_set__") or "") == source_name
            ]
            target_rows = [
                record
                for record in group["records"]
                if str(record.get("__record_set__") or "") == target_name
            ]
            target_index: dict[tuple[str, ...], dict[str, Any]] = {}
            for target_row in target_rows:
                key = _join_key(target_row, target_key_fields)
                if key is None:
                    continue
                if key in target_index:
                    relation_errors.append(
                        f"group {group['group_id']} has duplicate target join key {key!r}"
                    )
                    continue
                target_index[key] = target_row
            for source_row in source_rows:
                source_count += 1
                key = _join_key(source_row, source_key_fields)
                target_row = target_index.get(key) if key is not None else None
                if target_row is None:
                    unmatched_count += 1
                    continue
                matched_count += 1
                for source_field, roles in relation["threshold_bindings"].items():
                    for role, target_field in roles.items():
                        value = target_row.get(target_field)
                        if value in (None, ""):
                            continue
                        validator = target_validators.get(target_field)
                        if not isinstance(validator, dict):
                            validator = {}
                        if is_unavailable_value(
                            value,
                            validator.get("unavailable_tokens"),
                        ):
                            continue
                        if _number(value) is None:
                            relation_errors.append(
                                f"group {group['group_id']} threshold field "
                                f"{target_name}.{target_field} is not numeric"
                            )
                            continue
                        matched_values[f"{source_field}.{role}"] += 1
        if source_count == 0:
            relation_errors.append("no source records prove this relation")
        elif matched_count == 0:
            relation_errors.append("no source record matched a target record")
        if relation["match_policy"] == "required" and unmatched_count:
            relation_errors.append(
                f"required relation left {unmatched_count} source records unmatched"
            )
        for binding, count in matched_values.items():
            if not count:
                relation_errors.append(
                    f"threshold binding {binding} has no matched numeric evidence"
                )

        if source_set is not None and target_set is not None:
            source_semantics = {
                str(field): dict(metadata) if isinstance(metadata, dict) else {}
                for field, metadata in dict(
                    source_set.get("state_semantics") or {}
                ).items()
            }
            target_semantics = {
                str(field): dict(metadata) if isinstance(metadata, dict) else {}
                for field, metadata in dict(
                    target_set.get("state_semantics") or {}
                ).items()
            }
            for source_field, roles in relation["threshold_bindings"].items():
                metadata = dict(source_semantics.get(source_field) or {})
                references = dict(metadata.get("threshold_references") or {})
                for role, target_field in roles.items():
                    references[role] = {
                        "field_name": target_field,
                        "context_key": f"threshold.{role}",
                        "level": "alarm" if role.startswith("critical_") else "warning",
                        "source": "cross_record_set_reference",
                        "record_set_name": target_name,
                        "join_keys": dict(relation["join_keys"]),
                        "relation_name": name,
                        "match_policy": relation["match_policy"],
                    }
                    target_metadata = dict(target_semantics.get(target_field) or {})
                    target_metadata.update(
                        comparison_role="threshold",
                        monitor=False,
                        target_field=source_field,
                        threshold_role=role,
                        threshold_direction=(
                            "low" if "low" in role else "high"
                        ),
                        threshold_level=(
                            "alarm" if role.startswith("critical_") else "warning"
                        ),
                    )
                    target_semantics[target_field] = target_metadata
                metadata["threshold_references"] = references
                source_semantics[source_field] = metadata
            source_set["state_semantics"] = source_semantics
            target_set["state_semantics"] = target_semantics

        reports.append(
            {
                "name": name,
                "source_record_set": source_name,
                "target_record_set": target_name,
                "source_record_count": source_count,
                "matched_record_count": matched_count,
                "unmatched_record_count": unmatched_count,
                "match_rate": (
                    round(matched_count / source_count, 6) if source_count else 0.0
                ),
                "matched_threshold_values": matched_values,
                "success": not relation_errors,
                "errors": list(dict.fromkeys(relation_errors)),
            }
        )
        errors.extend(f"{name}: {message}" for message in relation_errors)

    output["record_sets"] = list(by_name.values())
    return {
        "success": not errors,
        "contract": output,
        "relations": relations,
        "reports": reports,
        "role_normalizations": role_normalizations,
        "errors": list(dict.fromkeys(errors)),
    }


def required_threshold_reference_errors(
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """Enforce catalog-owned structured-threshold requirements."""

    record_sets = [
        dict(item)
        for item in contract.get("record_sets") or []
        if isinstance(item, dict)
    ]
    if not record_sets:
        record_sets = [{"name": "default", **dict(contract)}]
    errors: list[dict[str, Any]] = []
    for record_set in record_sets:
        record_set_name = str(record_set.get("name") or "default")
        for field, raw_semantics in dict(
            record_set.get("state_semantics") or {}
        ).items():
            if not isinstance(raw_semantics, dict):
                continue
            constraints = raw_semantics.get("policy_constraints")
            if not isinstance(constraints, dict) or constraints.get(
                "require_structured_threshold_references"
            ) is not True:
                continue
            references = {
                str(role): value
                for role, value in dict(
                    raw_semantics.get("threshold_references") or {}
                ).items()
                if isinstance(value, dict)
            }
            required_any = {
                str(role)
                for role in constraints.get("required_threshold_reference_any")
                or []
                if str(role).strip()
            }
            if not references or (
                required_any and not required_any.intersection(references)
            ):
                errors.append(
                    {
                        "code": "structured_threshold_reference_missing",
                        "record_set_name": record_set_name,
                        "field_name": str(field),
                        "observation_type": raw_semantics.get("observation_type"),
                        "required_any": sorted(required_any),
                        "message": (
                            f"{record_set_name}.{field} requires a validated structured "
                            "threshold reference"
                        ),
                    }
                )
    return errors


def materialize_record_set_references(
    *,
    contract: dict[str, Any],
    structured_sets: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Join one parsed snapshot and expose approved references to the normalizer."""

    materialized = {
        str(name): [dict(record) for record in value if isinstance(record, dict)]
        if isinstance(value, list)
        else dict(value)
        if isinstance(value, dict)
        else value
        for name, value in structured_sets.items()
    }
    record_sets = [
        copy.deepcopy(item)
        for item in contract.get("record_sets") or []
        if isinstance(item, dict)
    ]
    by_name = {
        str(item.get("name") or "").strip(): item
        for item in record_sets
        if str(item.get("name") or "").strip()
    }
    for source_name, source_set in by_name.items():
        source_rows = materialized.get(source_name)
        if not isinstance(source_rows, list):
            continue
        semantics = {
            str(field): dict(metadata) if isinstance(metadata, dict) else {}
            for field, metadata in dict(
                source_set.get("state_semantics") or {}
            ).items()
        }
        ignored = {
            str(field) for field in source_set.get("ignore_fields") or []
        }
        for source_field, metadata in semantics.items():
            references = dict(metadata.get("threshold_references") or {})
            for role, raw_reference in tuple(references.items()):
                if not isinstance(raw_reference, dict):
                    continue
                target_name = str(
                    raw_reference.get("record_set_name") or ""
                ).strip()
                if not target_name:
                    continue
                target_rows = materialized.get(target_name)
                target_set = by_name.get(target_name)
                if not isinstance(target_rows, list) or target_set is None:
                    continue
                join_keys = {
                    str(source_key): str(target_key)
                    for source_key, target_key in dict(
                        raw_reference.get("join_keys") or {}
                    ).items()
                    if str(source_key).strip() and str(target_key).strip()
                }
                if not join_keys:
                    raise ValueError(
                        f"cross-record reference {source_name}.{source_field}.{role} "
                        "has no join_keys"
                    )
                target_key_fields = list(join_keys.values())
                target_index: dict[tuple[str, ...], dict[str, Any]] = {}
                for target_row in target_rows:
                    key = _join_key(target_row, target_key_fields)
                    if key is None:
                        continue
                    if key in target_index:
                        raise ValueError(
                            f"ambiguous cross-record reference {source_name} -> "
                            f"{target_name}: duplicate key {key!r}"
                        )
                    target_index[key] = target_row
                target_field = str(raw_reference.get("field_name") or "").strip()
                runtime_field = (
                    f"{_RUNTIME_REFERENCE_PREFIX}:{source_field}:{role}:"
                    f"{target_name}:{target_field}"
                )
                for source_row in source_rows:
                    key = _join_key(source_row, list(join_keys))
                    target_row = target_index.get(key) if key is not None else None
                    if target_row is not None and target_row.get(target_field) not in (
                        None,
                        "",
                    ):
                        source_row[runtime_field] = target_row[target_field]
                target_validator = dict(
                    dict(target_set.get("validators") or {}).get(target_field) or {}
                )
                reference = dict(raw_reference)
                reference["runtime_field_name"] = runtime_field
                if target_validator:
                    reference["validator"] = target_validator
                references[str(role)] = reference
                ignored.add(runtime_field)
            metadata["threshold_references"] = references
            semantics[source_field] = metadata
        source_set["state_semantics"] = semantics
        source_set["ignore_fields"] = sorted(ignored)
    return materialized, record_sets
