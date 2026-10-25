from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from src.core.check_items.catalog import CheckItemCatalog, load_catalog
from src.core.check_items.resolver import CheckItemImplementationResolver
from src.core.parser.policy import DEFAULT_COMMAND_ALIASES, ParserPolicy
from src.core.parser.semantic_catalog import (
    field_aliases,
    field_semantics,
    resolve_semantic_contract,
    schema_field_aliases,
)

_SEMANTIC_ROLE_BY_TYPE = {
    "counter": "metric",
    "gauge": "metric",
    "state": "state",
    "threshold": "threshold",
}


def _snake_case(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "")).strip("_")
    return text.casefold()


def _preferred_observation_field(
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Describe a stable Parser field name without claiming a mapping."""

    observation_type = str(observation.get("observation_type") or "").strip()
    configured_name = _snake_case(observation.get("canonical_field"))
    semantic_parts = [
        _snake_case(item)
        for item in observation_type.split(".")[1:]
        if _snake_case(item)
    ]
    base_name = configured_name or "_".join(semantic_parts) or "value"
    dimensions = [
        _snake_case(item)
        for item in observation.get("dimensions") or []
        if _snake_case(item)
    ]
    unit = _snake_case(observation.get("unit"))
    include_unit = bool(
        unit
        and base_name != unit
        and not base_name.endswith(f"_{unit}")
    )
    suffixes = [
        *(f"{{{item}}}" for item in dimensions),
        *([unit] if include_unit else []),
    ]
    pattern = "_".join([base_name, *suffixes]) if suffixes else base_name
    return {
        "preferred_name": base_name if not dimensions else None,
        "preferred_pattern": pattern,
        "observation_type": observation_type,
        "semantic_type": str(observation.get("semantic_type") or "").strip(),
        "unit": str(observation.get("unit") or "").strip() or None,
        "dimensions": dimensions,
        "required": observation.get("required", True) is not False,
        "source": "check_item.canonical_field" if configured_name else "observation_type",
    }


def _semantic_profile_vocabulary(
    *,
    command: str,
    sample_text: str,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Project the vendor-neutral semantic profile into Parser naming hints."""

    match = resolve_semantic_contract(
        command=command,
        samples_text=sample_text,
    )
    if match is None:
        return None, []
    contract = dict(match.contract or {})
    primary_keys = {
        _snake_case(item) for item in contract.get("primary_keys") or []
    }
    ordered_names = dict.fromkeys(
        _snake_case(item)
        for item in (
            *(contract.get("primary_keys") or []),
            *(contract.get("required_fields") or []),
            *(contract.get("optional_fields") or []),
        )
        if _snake_case(item)
    )
    vocabulary: list[dict[str, Any]] = []
    for name in ordered_names:
        semantics = dict(field_semantics(name) or {})
        semantic_type = str(semantics.get("semantic_type") or "").strip()
        vocabulary.append(
            {
                "preferred_name": name,
                "role": (
                    "identity"
                    if name in primary_keys
                    else _SEMANTIC_ROLE_BY_TYPE.get(semantic_type, "context")
                ),
                "observation_type": (
                    str(semantics.get("observation_type") or "").strip()
                    or None
                ),
                "aliases": sorted(
                    {
                        _snake_case(alias)
                        for alias in (
                            *field_aliases(name),
                            *schema_field_aliases(name),
                        )
                        if _snake_case(alias) and _snake_case(alias) != name
                    }
                ),
                "source": "parser_semantic_catalog",
            }
        )
    return match.profile_id, vocabulary


def build_parser_field_naming_context(
    *,
    vendor: str,
    model: str | None,
    family: str | None,
    profile: str | None,
    command: str,
    sample_text: str = "",
    catalog: CheckItemCatalog | None = None,
    check_item_id: str | None = None,
) -> dict[str, Any]:
    """Build check-item-owned naming guidance for ParserSpec generation.

    The guidance is advisory. A source-faithful Parser may use another field
    name, while the approved CommandAdapter Projection remains the mapping
    layer.
    """

    catalog = catalog or load_catalog()
    selected_check_item_id = str(check_item_id or "").strip()
    check_item_ids = (
        (selected_check_item_id,)
        if selected_check_item_id
        else resolve_parser_check_item_ids(
            vendor=vendor,
            model=model,
            family=family,
            profile=profile,
            command=command,
            catalog=catalog,
        )
    )
    semantic_profile_id, semantic_vocabulary = _semantic_profile_vocabulary(
        command=command,
        sample_text=sample_text,
    )
    items: list[dict[str, Any]] = []
    for check_item_id in check_item_ids:
        check_item = catalog.check_item(check_item_id)
        if check_item is None or check_item.result_mode != "structured":
            continue
        contract = dict(check_item.canonical_contract or {})
        identity_fields = [
            _snake_case(item)
            for item in contract.get("identity_dimensions") or []
            if _snake_case(item) and _snake_case(item) != "device_id"
        ]
        vocabulary_by_name = {
            str(item["preferred_name"]): dict(item)
            for item in semantic_vocabulary
        }
        for identity_field in identity_fields:
            existing = dict(vocabulary_by_name.get(identity_field) or {})
            vocabulary_by_name[identity_field] = {
                "preferred_name": identity_field,
                "role": "identity",
                "observation_type": existing.get("observation_type"),
                "aliases": sorted(
                    {
                        *(
                            str(alias)
                            for alias in existing.get("aliases") or []
                        ),
                        *(
                            _snake_case(alias)
                            for alias in schema_field_aliases(identity_field)
                            if _snake_case(alias)
                        ),
                    }
                ),
                "source": existing.get("source") or "check_item.identity_dimension",
            }
        raw_vocabulary = contract.get("parser_field_vocabulary")
        if isinstance(raw_vocabulary, Mapping):
            for raw_name, raw_config in raw_vocabulary.items():
                name = _snake_case(raw_name)
                if not name:
                    continue
                config = (
                    dict(raw_config)
                    if isinstance(raw_config, Mapping)
                    else {}
                )
                inherited = dict(vocabulary_by_name.get(name) or {})
                vocabulary_by_name[name] = {
                    "preferred_name": name,
                    "role": (
                        str(config.get("role") or "").strip()
                        or inherited.get("role")
                    ),
                    "observation_type": (
                        str(config.get("observation_type") or "").strip()
                        or inherited.get("observation_type")
                    ),
                    "canonical_candidates": sorted(
                        {
                            str(candidate).strip().casefold()
                            for candidate in config.get("canonical_candidates") or []
                            if str(candidate).strip()
                        }
                    ),
                    "value_type": (
                        str(config.get("value_type") or "").strip()
                        or inherited.get("value_type")
                    ),
                    "aliases": sorted(
                        {
                            *(
                                str(alias)
                                for alias in inherited.get("aliases") or []
                            ),
                            *(
                                _snake_case(alias)
                                for alias in config.get("aliases") or []
                                if _snake_case(alias)
                            ),
                        }
                    ),
                    "source": "check_item.parser_field_vocabulary",
                }
        configured_vocabulary = [
            vocabulary_by_name[name]
            for name in sorted(vocabulary_by_name)
        ]
        observations = [
            _preferred_observation_field(item)
            for item in contract.get("observations") or []
            if isinstance(item, Mapping)
            and str(item.get("observation_type") or "").strip()
        ]
        if contract.get("value_contract") == "same-name/v1":
            identity_fields = [str(name) for name in contract.get("identity_dimensions", []) if name != "device_id"]
            observations = [
                {**_preferred_observation_field(row), "preferred_name": row["canonical_field"],
                 "preferred_pattern": row["canonical_field"]}
                for row in contract.get("observations", [])
            ]
            # The selected contract owns exact names; legacy aliases are not hints
            # for a strict schema, including when its Values use uppercase.
            configured_vocabulary = [
                {"preferred_name": name, "role": "identity", "aliases": []}
                for name in identity_fields
            ] + [
                {"preferred_name": row["canonical_field"], "observation_type": row["observation_type"],
                 "role": "state", "aliases": []}
                for row in contract.get("observations", [])
            ]
        items.append(
            {
                "check_item_id": check_item.id,
                "check_item_version_id": check_item.version_id,
                "check_item_name": check_item.name,
                "entity_type": str(contract.get("entity_type") or "").strip(),
                "canonical_contract": contract,
                "preferred_identity_fields": identity_fields,
                "preferred_observation_fields": observations,
                "configured_vocabulary": configured_vocabulary,
            }
        )
    preferred_value_names = sorted(
        {
            str(field).strip()
            for item in items
            for field in (
                *(item.get("preferred_identity_fields") or []),
                *(
                    observation.get("preferred_name")
                    for observation in item.get("preferred_observation_fields") or []
                    if observation.get("preferred_name")
                ),
                *(
                    vocabulary.get("preferred_name")
                    for vocabulary in item.get("configured_vocabulary") or []
                    if vocabulary.get("preferred_name")
                ),
            )
            if str(field).strip()
        }
    )
    strict_value_names = any(
        item["canonical_contract"].get("value_contract") == "same-name/v1" for item in items
    )
    return {
        "schema_version": "parser-field-naming-guidance/v1",
        "strict_value_names": strict_value_names,
        "resolved": bool(items),
        "semantic_profile_id": semantic_profile_id,
        "preferred_value_names": preferred_value_names,
        "check_items": items,
        "policy": {
            "preference": (
                "Use exactly the canonical field and identity names declared by the CheckItem."
                if strict_value_names else
                "Use the check-item preferred name when it faithfully represents "
                "the visible source value, unit and record grain."
            ),
            "fallback": (
                "Do not invent aliases, auxiliary Values or conversions. Missing source values "
                "remain absent; report unsupported required fields for human intervention."
                if strict_value_names else
                "When semantics differ, keep a clear source-faithful snake_case name "
                "and leave canonical mapping to the approved ObservationContract."
            ),
            "validation": (
                "Undeclared Value names or missing required fields must fail validation."
                if strict_value_names else
                "A naming difference alone must never invalidate an executable Parser."
            ),
        },
    }


def _canonical_command(value: Any) -> str:
    basic = ParserPolicy._basic_normalize(str(value or ""))
    return ParserPolicy._basic_normalize(DEFAULT_COMMAND_ALIASES.get(basic, basic))


def _record_sets(parser_spec: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    payload = dict(parser_spec or {})
    raw_sets = payload.get("record_sets")
    if isinstance(raw_sets, Mapping):
        return [
            {**dict(config), "name": str(name)}
            for name, config in raw_sets.items()
            if isinstance(config, Mapping)
        ]
    if isinstance(raw_sets, list):
        return [dict(item) for item in raw_sets if isinstance(item, Mapping)]
    if isinstance(payload.get("fields"), Mapping):
        return [
            {
                "name": str(payload.get("name") or payload.get("entity") or "records"),
                **payload,
            }
        ]
    return []


def parser_output_profile(
    parser_spec: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Describe what a ParserSpec emits without assigning canonical semantics."""

    sets: list[dict[str, Any]] = []
    fields_by_role: dict[str, list[dict[str, str]]] = defaultdict(list)
    canonical_hints: list[dict[str, str]] = []
    for record_set in _record_sets(parser_spec):
        name = str(
            record_set.get("name")
            or record_set.get("entity")
            or "records"
        )
        fields = dict(record_set.get("fields") or {})
        projected_fields: list[dict[str, Any]] = []
        for field_name, raw_config in fields.items():
            config = dict(raw_config or {}) if isinstance(raw_config, Mapping) else {}
            role = str(config.get("role") or "unspecified").strip().casefold()
            if role not in {
                "identity",
                "state",
                "metric",
                "threshold",
                "context",
            }:
                role = "unspecified"
            field = {
                "name": str(field_name),
                "role": role,
                "type": str(config.get("type") or "text"),
                "required": bool(config.get("required", False)),
                "monitor": config.get("monitor"),
                "canonical_candidate": (
                    str(config.get("canonical_candidate") or "").strip() or None
                ),
            }
            projected_fields.append(field)
            fields_by_role[role].append(
                {"record_set": name, "field": str(field_name)}
            )
            if field["canonical_candidate"]:
                canonical_hints.append(
                    {
                        "record_set": name,
                        "field": str(field_name),
                        "candidate": str(field["canonical_candidate"]),
                        "status": "unverified_hint",
                    }
                )
        sets.append(
            {
                "name": name,
                "entity": str(record_set.get("entity") or name),
                "cardinality": str(record_set.get("cardinality") or "repeated"),
                "presence": str(record_set.get("presence") or "required"),
                "primary_keys": [
                    str(item) for item in record_set.get("primary_keys") or []
                ],
                "fields": projected_fields,
            }
        )
    return {
        "record_set_count": len(sets),
        "field_count": sum(len(item["fields"]) for item in sets),
        "record_sets": sets,
        "roles": {
            role: fields_by_role.get(role, [])
            for role in ("identity", "state", "metric", "threshold", "context", "unspecified")
        },
        "role_counts": {
            role: len(fields_by_role.get(role, []))
            for role in ("identity", "state", "metric", "threshold", "context", "unspecified")
        },
        "canonical_candidate_hints": canonical_hints,
        "hint_claim": (
            "canonical_candidate values are LLM hints only; they are not an "
            "approved ObservationContract mapping"
        ),
    }


def _contract_mappings(
    observation_contract: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    if not observation_contract:
        return []
    payload = dict(observation_contract)
    sources = [
        (
            str(payload.get("entity_type") or "default"),
            dict(payload.get("state_semantics") or {}),
        )
    ]
    for record_set in payload.get("record_sets") or []:
        if not isinstance(record_set, Mapping):
            continue
        sources.append(
            (
                str(record_set.get("name") or record_set.get("entity_type") or "records"),
                dict(record_set.get("state_semantics") or {}),
            )
        )
    mappings: list[dict[str, str]] = []
    for record_set, semantics in sources:
        for source_field, raw_metadata in semantics.items():
            if not isinstance(raw_metadata, Mapping):
                continue
            observation_type = str(
                raw_metadata.get("observation_type") or ""
            ).strip()
            if observation_type:
                mappings.append(
                    {
                        "record_set": record_set,
                        "source_field": str(source_field),
                        "observation_type": observation_type,
                    }
                )
    return mappings


def _required_parser_roles(
    *,
    observations: list[dict[str, Any]],
    identity_dimensions: list[str],
) -> tuple[list[str], list[dict[str, str]]]:
    """Derive Parser shape expectations from catalog semantics and constraints."""

    requirements: list[dict[str, str]] = []
    parser_owned_identity = [
        item
        for item in identity_dimensions
        if item.strip().casefold() not in {"device_id"}
    ]
    if parser_owned_identity:
        requirements.append(
            {
                "role": "identity",
                "reason": "non_device_identity_dimensions",
                "source": ",".join(parser_owned_identity),
            }
        )

    for item in observations:
        if item.get("required", True) is False:
            continue
        observation_type = str(item.get("observation_type") or "").strip()
        semantic_type = str(item.get("semantic_type") or "").casefold()
        role = _SEMANTIC_ROLE_BY_TYPE.get(semantic_type)
        if role:
            requirements.append(
                {
                    "role": role,
                    "reason": "required_canonical_observation",
                    "source": observation_type,
                }
            )
        policy_constraints = dict(item.get("policy_constraints") or {})
        if policy_constraints.get("require_structured_threshold_references") is True:
            requirements.append(
                {
                    "role": "threshold",
                    "reason": "structured_threshold_reference_required",
                    "source": observation_type,
                }
            )

    deduplicated = {
        (item["role"], item["reason"], item["source"]): item
        for item in requirements
    }
    ordered = [deduplicated[key] for key in sorted(deduplicated)]
    return sorted({item["role"] for item in ordered}), ordered


def resolve_parser_check_item_ids(
    *,
    vendor: str,
    model: str | None,
    family: str | None,
    profile: str | None,
    command: str,
    catalog: CheckItemCatalog | None = None,
) -> list[str]:
    """Resolve only catalog-declared implementations for a Parser scope."""

    catalog = catalog or load_catalog()
    resolver = CheckItemImplementationResolver(catalog)
    implementations = {item.version_id: item for item in catalog.implementations}
    device = {
        "device_id": f"parser-capability:{vendor}:{model or profile or 'unknown'}",
        "vendor": vendor,
        "model": model,
        "family": family,
        "platform": profile,
    }
    command_key = _canonical_command(command)
    resolved: list[str] = []
    for check_item in catalog.check_items:
        decision = resolver.resolve(device=device, check_item_id=check_item.id)
        implementation = implementations.get(
            str(decision.implementation_version_id or "")
        )
        if decision.status != "resolved" or implementation is None:
            continue
        if command_key not in {
            _canonical_command(item.command) for item in implementation.commands
        }:
            continue
        resolved.append(check_item.id)
    return sorted(set(resolved))


def build_parser_check_item_capability(
    *,
    check_item_id: str,
    parser_spec: Mapping[str, Any] | None,
    observation_contract: Mapping[str, Any] | None = None,
    vendor: str | None = None,
    model: str | None = None,
    family: str | None = None,
    profile: str | None = None,
    command: str | None = None,
    catalog: CheckItemCatalog | None = None,
) -> dict[str, Any]:
    """Project Parser evidence against a check item without overstating readiness."""

    catalog = catalog or load_catalog()
    check_item = catalog.check_item(check_item_id)
    output = parser_output_profile(parser_spec)
    if check_item is None:
        return {
            "schema_version": "parser-check-item-capability/v1",
            "check_item_id": check_item_id,
            "status": "unknown_check_item",
            "parser_output": output,
            "target": None,
            "canonical_mapping": {
                "status": "mapping_unverified",
                "mappings": [],
                "missing_required_observation_types": [],
            },
            "blockers": [
                {
                    "code": "check_item_unknown",
                    "message": f"check item {check_item_id} is not present in the active catalog",
                }
            ],
            "claim_boundary": {
                "parser_structure_proven": False,
                "canonical_mapping_proven": False,
                "check_item_runtime_ready": False,
            },
        }

    observations = [
        dict(item)
        for item in check_item.canonical_contract.get("observations") or []
        if isinstance(item, Mapping)
    ]
    required = [
        item
        for item in observations
        if item.get("required", True) is not False
    ]
    required_types = [
        str(item.get("observation_type") or "").strip()
        for item in required
        if str(item.get("observation_type") or "").strip()
    ]
    identity_dimensions = [
        str(item)
        for item in check_item.canonical_contract.get("identity_dimensions") or []
    ]
    required_roles, role_requirements = _required_parser_roles(
        observations=observations,
        identity_dimensions=identity_dimensions,
    )
    observed_roles = {
        role for role, count in output["role_counts"].items() if count > 0
    }
    missing_roles = sorted(set(required_roles) - observed_roles)
    mappings = _contract_mappings(observation_contract)
    mapped_types = sorted(
        {
            item["observation_type"]
            for item in mappings
            if item["observation_type"]
        }
    )
    missing_types = sorted(set(required_types) - set(mapped_types))
    if observation_contract is None:
        mapping_status = "mapping_unverified"
    elif missing_types and mapped_types:
        mapping_status = "partial_mapping"
    elif missing_types:
        mapping_status = "mapping_missing"
    else:
        mapping_status = "mapping_declared"

    scope_evaluated = bool(vendor and command)
    implementation_scope_ids = (
        resolve_parser_check_item_ids(
            vendor=str(vendor or ""),
            model=model,
            family=family,
            profile=profile,
            command=str(command or ""),
            catalog=catalog,
        )
        if scope_evaluated
        else []
    )
    scope_match = (
        check_item_id in implementation_scope_ids
        if scope_evaluated
        else None
    )

    blockers: list[dict[str, Any]] = []
    if scope_match is False:
        blockers.append(
            {
                "code": "check_item_implementation_scope_mismatch",
                "message": (
                    "the Parser vendor/model/command scope is not mapped to the "
                    f"declared check item {check_item_id} by the active catalog"
                ),
                "context": {"resolved_check_item_ids": implementation_scope_ids},
            }
        )
    if missing_roles:
        blockers.append(
            {
                "code": "parser_output_role_missing",
                "message": (
                    "Parser output does not expose fields for required semantic roles: "
                    + ", ".join(missing_roles)
                ),
                "context": {"missing_roles": missing_roles},
            }
        )
    if observation_contract is None:
        blockers.append(
            {
                "code": "observation_contract_mapping_required",
                "message": (
                    "Parser approval proves structured extraction only; an approved "
                    "ObservationContract must map source fields to canonical observations"
                ),
                "context": {"required_observation_types": required_types},
            }
        )
    elif missing_types:
        blockers.append(
            {
                "code": "canonical_observation_mapping_incomplete",
                "message": (
                    "ObservationContract does not cover required canonical observations: "
                    + ", ".join(missing_types)
                ),
                "context": {"missing_observation_types": missing_types},
            }
        )

    return {
        "schema_version": "parser-check-item-capability/v1",
        "check_item_id": check_item.id,
        "check_item_version": check_item.version,
        "check_item_version_id": check_item.version_id,
        "check_item_digest": check_item.digest,
        "status": (
            "parser_shape_incomplete"
            if missing_roles
            else mapping_status
        ),
        "implementation_scope": {
            "evaluated": scope_evaluated,
            "matched": scope_match,
            "resolved_check_item_ids": implementation_scope_ids,
        },
        "parser_output": output,
        "target": {
            "name": check_item.name,
            "result_mode": check_item.result_mode,
            "assessment_mode": check_item.assessment_mode,
            "entity_type": check_item.canonical_contract.get("entity_type"),
            "identity_dimensions": identity_dimensions,
            "required_semantic_roles": required_roles,
            "role_requirements": role_requirements,
            "required_observation_types": required_types,
            "observations": [
                {
                    "observation_type": str(item.get("observation_type") or ""),
                    "semantic_type": str(item.get("semantic_type") or ""),
                    "canonical_field": item.get("canonical_field"),
                    "unit": item.get("unit"),
                    "required": item.get("required", True) is not False,
                }
                for item in observations
            ],
        },
        "shape_assessment": {
            "status": "incomplete" if missing_roles else "compatible",
            "observed_roles": sorted(observed_roles),
            "missing_required_roles": missing_roles,
        },
        "canonical_mapping": {
            "status": mapping_status,
            "contract_supplied": observation_contract is not None,
            "mappings": mappings,
            "mapped_observation_types": mapped_types,
            "missing_required_observation_types": missing_types,
        },
        "blockers": blockers,
        "claim_boundary": {
            "parser_structure_proven": False,
            "canonical_mapping_proven": mapping_status == "mapping_declared",
            "check_item_runtime_ready": False,
            "statement": (
                "A Parser candidate can be approved for its observed output shape, "
                "but that approval alone never proves complete check-item or "
                "RuntimeBundle capability."
            ),
        },
    }
