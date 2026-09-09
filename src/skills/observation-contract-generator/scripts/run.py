#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.config import get_settings  # noqa: E402
from src.core.check_items.adapter_projection_migration import (  # noqa: E402
    generate_projection_candidate,
    migrate_legacy_adapter_to_v2,
)
from src.core.observation.record_set_relations import (  # noqa: E402
    apply_record_set_relations,
    required_threshold_reference_errors,
)
from src.core.observation.semantic_projection import (  # noqa: E402
    SemanticProjectionError,
    apply_value_mapping,
    dimension_field_references,
    dimension_schema_signature,
    normalize_dimensions,
    project_value,
    resolve_dimensions,
    transform_field_references,
    validate_transform,
    validate_unit_conversion,
    validate_value_mapping,
)
from src.core.parser.contract_inference import infer_contract  # noqa: E402
from src.core.parser.contract_validation import (  # noqa: E402
    infer_and_validate_contract,
    proposal_from_dict,
)
from src.core.parser.semantic_catalog import (  # noqa: E402
    canonical_field_for_name,
    rebind_applicability,
    rebound_field_applicability,
)
from src.core.parser.semantic_catalog import (
    field_semantics as catalog_field_semantics,
)
from src.core.parser.value_types import (  # noqa: E402
    is_unassigned_ip,
    is_valid_ip_value,
)
from src.core.rule_control.generation_agent import (  # noqa: E402
    DEFAULT_RULE_GENERATION_PROVIDERS,
    RuleGenerationAgent,
)
from src.core.rule_control.planning import (  # noqa: E402
    COMPARABLE_SEMANTIC_TYPES,
    NON_TARGET_ROLES,
    enrich_contract_reference_semantics,
    normalize_contract_semantics,
    profile_fields,
)
from src.core.state.observation_applicability import (  # noqa: E402
    validate_applicability,
)

OBSERVATION_TYPE_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
)
NUMERIC_SEMANTIC_TYPES = frozenset({"counter", "gauge", "percentage"})
_FIELD_UNIT_TOKENS = frozenset(
    {
        "amp",
        "amps",
        "byte",
        "bytes",
        "celsius",
        "dbm",
        "gb",
        "gib",
        "gigabytes",
        "gibibytes",
        "kb",
        "kib",
        "kilobytes",
        "kibibytes",
        "ma",
        "mb",
        "mib",
        "megabytes",
        "mibibytes",
        "percent",
        "percentage",
        "v",
        "volts",
        "watts",
    }
)


def _records(params: dict[str, Any]) -> list[dict[str, Any]]:
    records = params.get("records") or params.get("structured_records") or []
    if not isinstance(records, list):
        raise ValueError("records must be a list")
    normalized: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"records[{index}] must be an object")
        normalized.append(record)
    return normalized


def _reconcile_contract_seed_with_records(
    seed: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Broaden stale source validators only when structured evidence proves it."""

    output = json.loads(json.dumps(dict(seed or {})))
    validators = dict(output.get("validators") or {})
    revisions: list[dict[str, Any]] = []
    for field, raw_validator in tuple(validators.items()):
        validator = dict(raw_validator or {})
        if str(validator.get("type") or "").strip().casefold() != "ip":
            continue
        values = [
            value
            for record in records
            if field in record
            for value in (
                list(record[field])
                if isinstance(record[field], (list, tuple))
                else [record[field]]
            )
            if value is not None and str(value).strip()
        ]
        if (
            values
            and any(is_unassigned_ip(value) for value in values)
            and all(
                is_valid_ip_value(value, allow_unassigned=True)
                for value in values
            )
        ):
            validator["type"] = "ip_or_unassigned"
            validators[field] = validator
            revisions.append(
                {
                    "field": str(field),
                    "from": "ip",
                    "to": "ip_or_unassigned",
                    "reason": (
                        "structured records contain only valid IP values or "
                        "approved unassigned tokens"
                    ),
                    "observed_unassigned_tokens": sorted(
                        {
                            str(value).strip()
                            for value in values
                            if is_unassigned_ip(value)
                        }
                    ),
                }
            )
    output["validators"] = validators
    if revisions:
        evidence = dict(output.get("evidence") or {})
        evidence["source_validator_reconciliation"] = {
            "strategy": "evidence_proven_compatible_broadening",
            "revisions": revisions,
        }
        output["evidence"] = evidence
    return output


def _record_groups(params: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_groups = params.get("record_groups") or []
    if not raw_groups:
        return [{"group_id": "default", "records": records}]
    if not isinstance(raw_groups, list):
        raise ValueError("record_groups must be a list")
    groups: list[dict[str, Any]] = []
    for index, item in enumerate(raw_groups):
        if not isinstance(item, dict) or not isinstance(item.get("records"), list):
            raise ValueError(f"record_groups[{index}] must contain a records list")
        group_records = []
        for record_index, record in enumerate(item["records"]):
            if not isinstance(record, dict):
                raise ValueError(
                    f"record_groups[{index}].records[{record_index}] must be an object"
                )
            group_records.append(record)
        groups.append(
            {
                "group_id": str(item.get("group_id") or f"group-{index + 1}"),
                "records": group_records,
            }
        )
    return groups


def _validate_contract_by_group(
    *,
    command: str,
    groups: list[dict[str, Any]],
    candidate: dict[str, Any],
    field_inventory: list[str],
    contract_seed: dict[str, Any] | None = None,
    require_policy_targets: bool = False,
    observation_targets: list[dict[str, Any]] | None = None,
    strict_observation_targets: bool = False,
    canonical_identity_constraint: dict[str, Any] | None = None,
    field_profiles: dict[str, dict[str, Any]] | None = None,
    allow_semantic_overrides: bool = False,
) -> dict[str, Any]:
    declared = {
        str(item)
        for item in [
            *(candidate.get("required_fields") or []),
            *(candidate.get("optional_fields") or []),
        ]
    }
    semantic_fields = {str(item) for item in (candidate.get("state_semantics") or {})}
    seed_optional = {
        str(item) for item in (contract_seed or {}).get("optional_fields") or []
    }
    unknown_fields = sorted(
        (declared | semantic_fields) - set(field_inventory) - seed_optional
    )
    all_records = [
        record
        for group in groups
        for record in group.get("records") or []
        if isinstance(record, dict)
    ]
    governance_errors = _contract_governance_errors(
        candidate,
        field_inventory=field_inventory,
        contract_seed=contract_seed,
        require_policy_targets=require_policy_targets,
        observation_targets=observation_targets,
        strict_observation_targets=strict_observation_targets,
        canonical_identity_constraint=canonical_identity_constraint,
        field_profiles=(
            field_profiles
            or profile_fields(
                all_records,
                field_inventory=field_inventory,
            )
        ),
        sample_records=all_records,
        allow_semantic_overrides=allow_semantic_overrides,
    )
    governance_errors.extend(
        _canonical_identity_group_errors(candidate, groups=groups)
    )
    reports = [
        (
            str(group["group_id"]),
            infer_and_validate_contract(
                command=command,
                records=list(group["records"]),
                proposal=proposal_from_dict(candidate),
            ),
        )
        for group in groups
    ]
    validations = [dict(report.get("validation") or {}) for _, report in reports]
    success = bool(reports) and all(report.get("success") is True for _, report in reports)
    errors: list[str] = []
    if unknown_fields:
        errors.append(
            "contract references fields absent from sanitized records: " + ", ".join(unknown_fields)
        )
    errors.extend(governance_errors)
    for group_id, report in reports:
        if report.get("success") is not True:
            validation = dict(report.get("validation") or {})
            errors.append(
                f"snapshot group {group_id} failed contract validation: "
                f"missing={validation.get('missing_fields') or []}, "
                f"duplicate_keys={validation.get('duplicate_key_count') or 0}, "
                f"key_errors={validation.get('key_error_count') or 0}, "
                f"value_errors={validation.get('value_error_count') or 0}"
            )
    first_proposal = reports[0][1].get("proposal") if reports else candidate
    return {
        "success": success and not unknown_fields and not governance_errors,
        "errors": errors,
        "unknown_fields": unknown_fields,
        "record_count": sum(int(item.get("record_count") or 0) for item in validations),
        "snapshot_group_count": len(groups),
        "required_field_coverage": min(
            (float(item.get("required_field_coverage") or 0) for item in validations),
            default=0,
        ),
        "missing_fields": sorted(
            {str(field) for item in validations for field in item.get("missing_fields") or []}
        ),
        "primary_key_status": "valid"
        if validations and all(item.get("primary_key_status") == "valid" for item in validations)
        else "invalid",
        "duplicate_key_count": sum(
            int(item.get("duplicate_key_count") or 0) for item in validations
        ),
        "key_error_count": sum(int(item.get("key_error_count") or 0) for item in validations),
        "value_error_count": sum(int(item.get("value_error_count") or 0) for item in validations),
        "value_errors": [
            {"group_id": group_id, **error}
            for group_id, report in reports
            for error in (report.get("validation") or {}).get("value_errors") or []
        ][:30],
        "semantic_field_count": max(
            (int(item.get("semantic_field_count") or 0) for item in validations),
            default=0,
        ),
        "groups": [
            {
                "group_id": group_id,
                "success": report.get("success") is True,
                "validation": report.get("validation"),
            }
            for group_id, report in reports
        ],
        "proposal": first_proposal,
    }


def _canonical_identity_group_errors(
    candidate: dict[str, Any],
    *,
    groups: list[dict[str, Any]],
) -> list[str]:
    dimensions = candidate.get("entity_dimensions") or {}
    identity_fields = [
        str(item)
        for item in candidate.get("entity_identity_fields") or []
        if str(item).strip()
    ]
    if not dimensions or not identity_fields:
        return []
    try:
        normalized = normalize_dimensions(dimensions)
    except SemanticProjectionError:
        return []
    errors: list[str] = []
    for group in groups:
        group_id = str(group.get("group_id") or "default")
        seen: set[str] = set()
        duplicate_count = 0
        for record in group.get("records") or []:
            if not isinstance(record, dict):
                continue
            try:
                resolved = resolve_dimensions(
                    normalized,
                    record,
                    context={"device_id": f"sanitized-{group_id}"},
                )
            except SemanticProjectionError:
                continue
            key = json.dumps(
                [resolved.get(field) for field in identity_fields],
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            if key in seen:
                duplicate_count += 1
            seen.add(key)
        if duplicate_count:
            errors.append(
                f"canonical entity identity is not unique in snapshot group "
                f"{group_id}: {duplicate_count} duplicate records; identifier "
                "dimensions must preserve source primary-key uniqueness"
            )
    return errors


def _governed_semantic_hints(
    field_inventory: list[str], contract_seed: dict[str, Any] | None
) -> dict[str, dict[str, Any]]:
    seed_semantics = dict((contract_seed or {}).get("state_semantics") or {})
    hints: dict[str, dict[str, Any]] = {}
    primary_keys = {str(item) for item in (contract_seed or {}).get("primary_keys") or []}
    ignored = {str(item) for item in (contract_seed or {}).get("ignore_fields") or []}
    seed_fields = [
        str(item)
        for item in [
            *((contract_seed or {}).get("required_fields") or []),
            *((contract_seed or {}).get("optional_fields") or []),
        ]
    ]
    observed_fields = set(field_inventory)
    for field in dict.fromkeys([*field_inventory, *seed_fields]):
        expected: dict[str, Any] = {}
        catalog_hint = catalog_field_semantics(field)
        if isinstance(catalog_hint, dict):
            expected.update(catalog_hint)
        rebound_applicability = rebound_field_applicability(
            field,
            field_inventory=field_inventory,
        )
        if rebound_applicability:
            expected["applicability"] = rebound_applicability
        if isinstance(seed_semantics.get(field), dict):
            expected.update(seed_semantics[field])
        if "applicability" in expected:
            compiled_applicability = rebind_applicability(
                expected.get("applicability"),
                field_inventory=field_inventory,
            )
            if compiled_applicability:
                expected["applicability"] = compiled_applicability
            else:
                expected.pop("applicability", None)
        if field in primary_keys:
            expected["semantic_type"] = "identity"
        elif field in ignored:
            expected.setdefault("semantic_type", "text")
            expected["ignore"] = True
        elif field not in observed_fields:
            expected.setdefault("semantic_type", "text")
            expected.pop("observation_type", None)
            expected["comparison_role"] = "metadata"
            expected["monitor"] = False
        if expected:
            hints[field] = expected
    return hints


def _apply_governed_semantic_hints(
    candidate: dict[str, Any],
    hints: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compile safety-critical semantic hints into an untrusted LLM proposal."""

    output = dict(candidate)
    semantics = dict(output.get("state_semantics") or {})
    declared = {
        str(item)
        for item in [
            *(output.get("required_fields") or []),
            *(output.get("optional_fields") or []),
        ]
    }
    removed_fields = sorted(str(field) for field in semantics if str(field) not in declared)
    semantics = {
        str(field): raw
        for field, raw in semantics.items()
        if str(field) in declared
    }
    for field, raw in tuple(semantics.items()):
        actual = dict(raw or {}) if isinstance(raw, dict) else {}
        if "applicability" not in hints.get(str(field), {}):
            actual.pop("applicability", None)
        semantics[field] = actual
    for field, governed in hints.items():
        actual = dict(semantics.get(field) or {})
        merged = {**actual, **governed}
        if isinstance(actual.get("value_mapping"), dict) and isinstance(
            governed.get("value_mapping"), dict
        ):
            # Generic catalog mappings are hints. Once the model proposes an
            # evidence-complete mapping for a governed target, keep exactly
            # that reviewable table instead of importing unrelated state words.
            merged["value_mapping"] = dict(actual["value_mapping"])
        semantics[field] = merged
    primary_keys = {
        str(field) for field in output.get("primary_keys") or [] if str(field).strip()
    }
    ignored_fields = {
        str(field) for field in output.get("ignore_fields") or [] if str(field).strip()
    }
    defaulted_metadata_fields: list[str] = []
    for field in sorted(declared):
        metadata = dict(semantics.get(field) or {})
        if str(metadata.get("semantic_type") or "").strip():
            continue
        if field in primary_keys:
            metadata.update(
                semantic_type="identity",
                comparison_role="identity",
                monitor=False,
            )
        else:
            metadata.update(
                semantic_type="text",
                comparison_role="metadata",
                monitor=False,
            )
            if field in ignored_fields:
                metadata["ignore"] = True
            defaulted_metadata_fields.append(field)
        semantics[field] = metadata
    output["state_semantics"] = semantics
    if removed_fields or defaulted_metadata_fields:
        evidence = dict(output.get("evidence") or {})
        evidence["semantic_field_governance"] = {
            "strategy": "approved_parser_schema_only",
            "removed_fields": removed_fields,
            "defaulted_metadata_fields": defaulted_metadata_fields,
        }
        output["evidence"] = evidence
    return output


def _inherit_approved_parser_schema(
    candidate: dict[str, Any],
    contract_seed: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bind an untrusted semantic proposal to the approved Parser schema.

    Parser activation has already established record identity, field presence,
    validators and empty-output behavior. Regenerating those facts in an LLM loop
    creates drift without adding information, so the model is allowed to enrich
    semantics while the structural contract is inherited verbatim.
    """

    seed = dict(contract_seed or {})
    if not seed:
        return dict(candidate)
    output = json.loads(json.dumps(dict(candidate or {})))
    immutable_fields = (
        "category",
        "entity_type",
        "cardinality",
        "presence",
        "primary_keys",
        "required_fields",
        "optional_fields",
        "validators",
        "allow_empty",
        "empty_patterns",
        "ignore_fields",
        "source_field_hints",
    )
    inherited: list[str] = []
    for field in immutable_fields:
        if field not in seed:
            continue
        output[field] = json.loads(json.dumps(seed[field]))
        inherited.append(field)
    evidence = dict(output.get("evidence") or {})
    evidence["asset_generation_governance"] = {
        "schema_source": "approved_parser_contract",
        "immutable_fields": inherited,
        "model_scope": "state_semantics_and_policy_intent",
    }
    seed_reconciliation = dict(seed.get("evidence") or {}).get(
        "source_validator_reconciliation"
    )
    if isinstance(seed_reconciliation, dict):
        evidence["source_validator_reconciliation"] = json.loads(
            json.dumps(seed_reconciliation)
        )
    output["evidence"] = evidence
    return output


def _target_field_matches(
    field_inventory: list[str],
    target: dict[str, Any],
    semantics: dict[str, Any],
) -> list[str]:
    expected = str(target.get("canonical_field") or "").strip().casefold()
    observation_type = str(target.get("observation_type") or "").strip()
    observation_leaf = observation_type.rsplit(".", 1)[-1].casefold()
    canonical_expected = (
        expected
        if expected == observation_leaf
        else canonical_field_for_name(expected) or expected
    )
    matches: list[str] = []
    for field in field_inventory:
        normalized = str(field).strip().casefold()
        if re.search(r"(?:^|_)(?:alarm|warning|threshold|high|low)(?:_|$)", normalized):
            if not re.search(
                r"(?:^|_)(?:alarm|warning|threshold|high|low)(?:_|$)", expected
            ):
                continue
        canonical = canonical_field_for_name(field) or normalized
        if canonical == canonical_expected or normalized == expected:
            matches.append(field)
    preferred = [
        field
        for field in matches
        if str((semantics.get(field) or {}).get("observation_type") or "")
        == observation_type
    ]
    return preferred if len(preferred) == 1 else matches


def _semantic_field_tokens(value: Any) -> tuple[str, ...]:
    """Return unit-neutral tokens used by governed projection recipes."""

    tokens = tuple(
        token
        for token in re.split(r"[^a-z0-9]+", str(value or "").casefold())
        if token
    )
    if tokens and tokens[-1] in _FIELD_UNIT_TOKENS:
        return tokens[:-1]
    return tokens


def _recipe_field_match_score(field: str, canonical_name: str) -> int:
    actual = _semantic_field_tokens(field)
    expected = _semantic_field_tokens(canonical_name)
    if not actual or not expected:
        return 0
    if actual == expected:
        return 2
    if len(actual) >= len(expected) and actual[-len(expected) :] == expected:
        return 1
    if len(expected) > len(actual) and expected[-len(actual) :] == actual:
        return 1
    return 0


def _resolve_recipe_binding(
    *,
    field_inventory: list[str],
    binding: Any,
) -> str | None:
    config = dict(binding) if isinstance(binding, dict) else {}
    canonical_fields = [
        str(item).strip()
        for item in config.get("canonical_fields") or []
        if str(item).strip()
    ]
    if not canonical_fields:
        return None
    scores = {
        field: max(
            _recipe_field_match_score(field, canonical_name)
            for canonical_name in canonical_fields
        )
        for field in field_inventory
    }
    best_score = max(scores.values(), default=0)
    if best_score <= 0:
        return None
    matches = sorted(field for field, score in scores.items() if score == best_score)
    if len(matches) == 1:
        return matches[0]
    shortest_length = min(len(_semantic_field_tokens(field)) for field in matches)
    shortest = [
        field
        for field in matches
        if len(_semantic_field_tokens(field)) == shortest_length
    ]
    return shortest[0] if len(shortest) == 1 else None


def _compile_projection_expression(
    expression: Any,
    *,
    bindings: dict[str, str],
) -> Any:
    if isinstance(expression, list):
        return [
            _compile_projection_expression(item, bindings=bindings)
            for item in expression
        ]
    if not isinstance(expression, dict):
        return expression
    if set(expression) == {"binding"}:
        binding_name = str(expression.get("binding") or "").strip()
        field_name = bindings.get(binding_name)
        if not field_name:
            raise ValueError(f"projection recipe binding is unresolved: {binding_name}")
        return {"field": field_name}
    return {
        str(key): _compile_projection_expression(value, bindings=bindings)
        for key, value in expression.items()
    }


def _apply_governed_projection_recipes(
    candidate: dict[str, Any],
    *,
    field_inventory: list[str],
    observation_targets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compile check-item-owned derivations into the safe projection DSL.

    Recipes describe vendor-neutral source semantics, never a vendor, model or
    command. They are applied only when every binding resolves uniquely and the
    resulting expression passes the same local validator used at runtime.
    """

    if not observation_targets:
        return candidate
    output = json.loads(json.dumps(dict(candidate)))
    semantics = dict(output.get("state_semantics") or {})
    mapped_types = {
        str(metadata.get("observation_type") or "").strip()
        for metadata in semantics.values()
        if isinstance(metadata, dict)
        and str(metadata.get("observation_type") or "").strip()
    }
    target_by_type = {
        str(target.get("observation_type") or "").strip(): target
        for target in observation_targets
        if str(target.get("observation_type") or "").strip()
    }
    mapped_entity_types = {
        str((target_by_type.get(observation_type) or {}).get("canonical_entity_type") or "").strip()
        for observation_type in mapped_types
        if str(
            (target_by_type.get(observation_type) or {}).get(
                "canonical_entity_type"
            )
            or ""
        ).strip()
    }
    candidate_entity_type = str(output.get("canonical_entity_type") or "").strip()
    applied: list[dict[str, Any]] = []
    for target in observation_targets:
        observation_type = str(target.get("observation_type") or "").strip()
        target_entity_type = str(target.get("canonical_entity_type") or "").strip()
        if (
            not observation_type
            or observation_type in mapped_types
            or (
                not mapped_entity_types
                and candidate_entity_type
                and target_entity_type
                and candidate_entity_type != target_entity_type
            )
            or (
                mapped_entity_types
                and target_entity_type
                and target_entity_type not in mapped_entity_types
            )
        ):
            continue
        recipes = [
            dict(item)
            for item in target.get("projection_recipes") or []
            if isinstance(item, dict)
        ]
        for recipe in recipes:
            raw_bindings = dict(recipe.get("bindings") or {})
            bindings = {
                str(name): _resolve_recipe_binding(
                    field_inventory=field_inventory,
                    binding=config,
                )
                for name, config in raw_bindings.items()
            }
            if not bindings or any(not field for field in bindings.values()):
                continue
            anchor_name = str(recipe.get("anchor") or "").strip()
            anchor_field = bindings.get(anchor_name)
            if not anchor_field:
                continue
            metadata = dict(semantics.get(anchor_field) or {})
            existing_type = str(metadata.get("observation_type") or "").strip()
            if existing_type and existing_type != observation_type:
                continue
            try:
                transform = _compile_projection_expression(
                    recipe.get("transform"),
                    bindings={name: str(field) for name, field in bindings.items()},
                )
            except ValueError:
                continue
            if validate_transform(transform):
                continue
            metadata["observation_type"] = observation_type
            metadata["comparison_role"] = "observation"
            metadata["monitor"] = True
            metadata.pop("ignore", None)
            semantic_type = str(target.get("semantic_type") or "").strip()
            if semantic_type:
                metadata["semantic_type"] = semantic_type
            metadata["transform"] = transform
            canonical_unit = str(
                target.get("canonical_unit") or target.get("unit") or ""
            ).strip()
            if canonical_unit:
                # A recipe expression already yields the governed target unit.
                metadata["source_unit"] = canonical_unit
                metadata["canonical_unit"] = canonical_unit
            recipe_dimensions = recipe.get("dimensions")
            if recipe_dimensions is not None:
                try:
                    normalized_dimensions = normalize_dimensions(recipe_dimensions)
                except SemanticProjectionError:
                    continue
                if normalized_dimensions:
                    metadata["dimensions"] = normalized_dimensions
                else:
                    metadata.pop("dimensions", None)
            for constraint_key in ("policy_constraints", "event_constraints"):
                constraints = target.get(constraint_key)
                if isinstance(constraints, dict) and constraints:
                    metadata[constraint_key] = json.loads(json.dumps(constraints))
            semantics[anchor_field] = metadata
            mapped_types.add(observation_type)
            applied.append(
                {
                    "observation_type": observation_type,
                    "recipe_id": str(recipe.get("id") or "unnamed"),
                    "anchor_field": anchor_field,
                    "bindings": {
                        name: str(field) for name, field in sorted(bindings.items())
                    },
                }
            )
            break
    output["state_semantics"] = semantics
    if applied:
        evidence = dict(output.get("evidence") or {})
        evidence["governed_projection_recipes"] = applied
        output["evidence"] = evidence
    return output


def _apply_observation_target_constraints(
    candidate: dict[str, Any],
    *,
    field_inventory: list[str],
    observation_targets: list[dict[str, Any]],
    strict: bool,
) -> dict[str, Any]:
    """Assign policy-owned observation namespaces to model-selected semantics."""

    if not observation_targets:
        return candidate
    output = json.loads(json.dumps(dict(candidate)))
    semantics = dict(output.get("state_semantics") or {})
    resolved: dict[str, list[str]] = {}
    allowed_types = {
        str(item.get("observation_type") or "").strip()
        for item in observation_targets
        if str(item.get("observation_type") or "").strip()
        and (
            not str(item.get("semantic_type") or "").strip()
            or str(item.get("semantic_type") or "").strip().casefold()
            in COMPARABLE_SEMANTIC_TYPES
        )
    }
    for target in observation_targets:
        observation_type = str(target.get("observation_type") or "").strip()
        target_semantic_type = str(target.get("semantic_type") or "").strip().casefold()
        if target_semantic_type and target_semantic_type not in COMPARABLE_SEMANTIC_TYPES:
            continue
        if not str(target.get("canonical_field") or "").strip():
            continue
        matches = _target_field_matches(field_inventory, target, semantics)
        if not observation_type or len(matches) != 1:
            continue
        field = matches[0]
        metadata = dict(semantics.get(field) or {})
        metadata["observation_type"] = observation_type
        metadata["comparison_role"] = "observation"
        metadata["monitor"] = True
        semantics[field] = metadata
        resolved.setdefault(observation_type, []).append(field)
    targets_by_type = {
        str(target.get("observation_type") or "").strip(): target
        for target in observation_targets
        if str(target.get("observation_type") or "").strip()
        and (
            not str(target.get("semantic_type") or "").strip()
            or str(target.get("semantic_type") or "").strip().casefold()
            in COMPARABLE_SEMANTIC_TYPES
        )
    }
    model_resolved: dict[str, list[str]] = {}
    for field, raw in semantics.items():
        if not isinstance(raw, dict):
            continue
        observation_type = str(raw.get("observation_type") or "").strip()
        if observation_type in allowed_types:
            model_resolved.setdefault(observation_type, []).append(str(field))
    for observation_type, fields in model_resolved.items():
        # A unique canonical-field match is policy-owned and must not be widened
        # by a model that assigns the same observation namespace to adjacent
        # source columns. Only use the model as a fallback when governance could
        # not resolve the target and the model selected exactly one source field.
        if observation_type in resolved:
            continue
        unique_fields = list(dict.fromkeys(fields))
        if len(unique_fields) == 1:
            resolved[observation_type] = unique_fields
            continue
        target = targets_by_type.get(observation_type) or {}
        expected_dimensions = {
            str(item).strip()
            for item in target.get("dimensions") or []
            if str(item).strip()
        }
        if not expected_dimensions:
            continue
        signatures: set[str] = set()
        for field in unique_fields:
            dimensions = (semantics.get(field) or {}).get("dimensions")
            try:
                normalized = normalize_dimensions(dimensions)
                signature = dimension_schema_signature(dimensions)
            except SemanticProjectionError:
                break
            if set(normalized) != expected_dimensions or signature in signatures:
                break
            signatures.add(signature)
        else:
            resolved[observation_type] = unique_fields
    resolved = {
        observation_type: list(dict.fromkeys(fields))
        for observation_type, fields in resolved.items()
    }
    governed_semantics: dict[str, dict[str, Any]] = {}
    for observation_type, fields in resolved.items():
        target = targets_by_type.get(observation_type) or {}
        for field in fields:
            metadata = dict(semantics.get(field) or {})
            semantic_type = str(target.get("semantic_type") or "").strip()
            canonical_unit = str(
                target.get("canonical_unit") or target.get("unit") or ""
            ).strip()
            metadata["observation_type"] = observation_type
            metadata["comparison_role"] = "observation"
            metadata["monitor"] = True
            metadata.pop("ignore", None)
            if semantic_type:
                metadata["semantic_type"] = semantic_type
            if canonical_unit:
                metadata["canonical_unit"] = canonical_unit
            elif semantic_type.casefold() not in NUMERIC_SEMANTIC_TYPES:
                metadata.pop("source_unit", None)
                metadata.pop("canonical_unit", None)
                metadata.pop("unit", None)
            for constraint_key in ("policy_constraints", "event_constraints"):
                constraints = target.get(constraint_key)
                if isinstance(constraints, dict) and constraints:
                    metadata[constraint_key] = json.loads(
                        json.dumps(constraints)
                    )
            semantics[field] = metadata
            governed_semantics[field] = {
                "observation_type": observation_type,
                **(
                    {"semantic_type": semantic_type}
                    if semantic_type
                    else {}
                ),
                **(
                    {"canonical_unit": canonical_unit}
                    if canonical_unit
                    else {}
                ),
            }
    if strict:
        target_fields = {
            field
            for fields in resolved.values()
            for field in fields
        }
        for field, raw in tuple(semantics.items()):
            if field in target_fields or not isinstance(raw, dict):
                continue
            metadata = dict(raw)
            semantic_type = str(metadata.get("semantic_type") or "").strip().casefold()
            if semantic_type not in COMPARABLE_SEMANTIC_TYPES:
                metadata.pop("observation_type", None)
                metadata["monitor"] = False
                semantics[field] = metadata
                continue
            metadata.pop("observation_type", None)
            metadata["comparison_role"] = "metadata"
            metadata["monitor"] = False
            semantics[field] = metadata
    evidence = dict(output.get("evidence") or {})
    evidence["observation_target_governance"] = {
        "strategy": "policy_owned_namespace_binding",
        "strict": strict,
        "resolved": resolved,
        "governed_semantics": governed_semantics,
    }
    output["state_semantics"] = semantics
    output["evidence"] = evidence
    return output


def _partition_multi_record_observation_targets(
    *,
    context_sets: list[dict[str, Any]],
    observation_targets: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """Assign canonically evidenced targets to one Parser record set.

    A multi-record command can contain adjacent but unrelated state fields. When
    one record set contains the governed canonical field, giving the same target
    to every set invites the model to bind a convenient neighbouring field to
    that namespace. Targets without a unique canonical-field match remain
    available to every set so the model can still resolve vendor-specific names.
    """

    target_owner_by_type: dict[str, str] = {}
    for target in observation_targets:
        observation_type = str(target.get("observation_type") or "").strip()
        canonical_field = str(target.get("canonical_field") or "").strip()
        if not observation_type or not canonical_field:
            continue
        exact_matching_sets = [
            str(context_set.get("name") or "").strip()
            for context_set in context_sets
            if canonical_field.casefold()
            in {
                str(field).strip().casefold()
                for field in context_set.get("field_inventory") or []
                if str(field).strip()
            }
        ]
        if len(exact_matching_sets) == 1:
            target_owner_by_type[observation_type] = exact_matching_sets[0]
            continue

        semantic_scores: dict[str, int] = {}
        for context_set in context_sets:
            name = str(context_set.get("name") or "").strip()
            score = max(
                (
                    _recipe_field_match_score(str(field), canonical_field)
                    for field in context_set.get("field_inventory") or []
                    if str(field).strip()
                ),
                default=0,
            )
            if score:
                semantic_scores[name] = score
        best_semantic_score = max(semantic_scores.values(), default=0)
        best_semantic_sets = sorted(
            name
            for name, score in semantic_scores.items()
            if score == best_semantic_score
        )
        if best_semantic_score and len(best_semantic_sets) == 1:
            target_owner_by_type[observation_type] = best_semantic_sets[0]
            continue

        recipe_scores: dict[str, int] = {}
        for context_set in context_sets:
            name = str(context_set.get("name") or "").strip()
            field_inventory = [
                str(field)
                for field in context_set.get("field_inventory") or []
                if str(field).strip()
            ]
            for recipe in target.get("projection_recipes") or []:
                if not isinstance(recipe, dict):
                    continue
                raw_bindings = dict(recipe.get("bindings") or {})
                resolved = {
                    str(binding_name): _resolve_recipe_binding(
                        field_inventory=field_inventory,
                        binding=binding,
                    )
                    for binding_name, binding in raw_bindings.items()
                }
                if not resolved or any(not field for field in resolved.values()):
                    continue
                score = sum(
                    max(
                        (
                            _recipe_field_match_score(
                                str(resolved[binding_name]),
                                canonical,
                            )
                            for canonical in (
                                dict(raw_bindings[binding_name]).get(
                                    "canonical_fields"
                                )
                                if isinstance(raw_bindings[binding_name], dict)
                                else []
                            )
                        ),
                        default=0,
                    )
                    for binding_name in resolved
                )
                recipe_scores[name] = max(recipe_scores.get(name, 0), score)
        best_recipe_score = max(recipe_scores.values(), default=0)
        best_recipe_sets = sorted(
            name
            for name, score in recipe_scores.items()
            if score == best_recipe_score
        )
        if best_recipe_score and len(best_recipe_sets) == 1:
            target_owner_by_type[observation_type] = best_recipe_sets[0]
            continue

        matching_sets: list[str] = []
        for context_set in context_sets:
            name = str(context_set.get("name") or "").strip()
            field_inventory = [
                str(field)
                for field in context_set.get("field_inventory") or []
                if str(field).strip()
            ]
            if len(_target_field_matches(field_inventory, target, {})) == 1:
                matching_sets.append(name)
        if len(matching_sets) == 1:
            target_owner_by_type[observation_type] = matching_sets[0]

    targets_by_set: dict[str, list[dict[str, Any]]] = {}
    for context_set in context_sets:
        name = str(context_set.get("name") or "").strip()
        targets_by_set[name] = [
            target
            for target in observation_targets
            if not target_owner_by_type.get(
                str(target.get("observation_type") or "").strip()
            )
            or target_owner_by_type.get(
                str(target.get("observation_type") or "").strip()
            )
            == name
        ]
    return targets_by_set, target_owner_by_type


def _target_identity_constraint(target: dict[str, Any]) -> dict[str, Any]:
    entity_type = str(target.get("canonical_entity_type") or "").strip()
    identity_dimensions = [
        str(item).strip()
        for item in target.get("identity_dimensions") or []
        if str(item).strip()
    ]
    if not entity_type or not identity_dimensions:
        return {}
    return {
        "canonical_entity_type": entity_type,
        "identity_dimensions": identity_dimensions,
        "identity_dimension_rules": {
            str(name): dict(rule)
            for name, rule in dict(
                target.get("identity_dimension_rules") or {}
            ).items()
            if isinstance(rule, dict)
        },
    }


def _identity_constraints_for_targets(
    targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_signature: dict[str, dict[str, Any]] = {}
    for target in targets:
        constraint = _target_identity_constraint(target)
        if not constraint:
            continue
        signature = json.dumps(
            constraint,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        by_signature[signature] = constraint
    return [by_signature[key] for key in sorted(by_signature)]


def _default_identity_expression(
    *,
    dimension: str,
    rule: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    fields = {
        str(item).strip()
        for key in ("primary_keys", "required_fields", "optional_fields")
        for item in candidate.get(key) or []
        if str(item).strip()
    }
    primary_keys = [
        str(item).strip()
        for item in candidate.get("primary_keys") or []
        if str(item).strip()
    ]
    kind = str(rule.get("kind") or "").strip().casefold()
    if kind == "enum":
        allowed = [
            str(item).strip()
            for item in rule.get("allowed_values") or []
            if str(item).strip()
        ]
        evidence = " ".join(
            [
                str(candidate.get("name") or ""),
                str(candidate.get("entity_type") or ""),
                *primary_keys,
            ]
        ).casefold()
        aliases = {
            "processor": ("cpu", "core", "processor"),
            "slot": ("slot", "board", "member"),
            "system": ("system", "summary", "aggregate", "device"),
        }
        for value in allowed:
            tokens = aliases.get(value.casefold(), (value.casefold(),))
            if any(token and token in evidence for token in tokens):
                return {"literal": value}
        if not primary_keys and any(value.casefold() == "system" for value in allowed):
            return {
                "literal": next(value for value in allowed if value.casefold() == "system")
            }
        if len(allowed) == 1:
            return {"literal": allowed[0]}
        return None
    if dimension in fields:
        return {"field": dimension}
    if dimension.casefold() == "device_id":
        return {"context": "device_id"}
    if len(primary_keys) == 1:
        return {"field": primary_keys[0]}
    if len(primary_keys) > 1:
        args: list[dict[str, Any]] = []
        for index, field in enumerate(primary_keys):
            if index:
                args.append({"literal": "|"})
            args.append({"field": field})
        return {"op": "concat", "args": args}
    return {"context": "device_id"}


def _apply_canonical_identity_defaults(
    candidate: dict[str, Any],
    constraint: dict[str, Any],
    *,
    governed_field_dimensions: set[str] | None = None,
) -> dict[str, Any]:
    if not constraint:
        return candidate
    output = json.loads(json.dumps(dict(candidate)))
    output["canonical_entity_type"] = str(
        constraint.get("canonical_entity_type") or ""
    )
    identity_fields = [
        str(item).strip()
        for item in constraint.get("identity_dimensions") or []
        if str(item).strip()
    ]
    output["entity_identity_fields"] = identity_fields
    dimensions = dict(output.get("entity_dimensions") or {})
    rules = dict(constraint.get("identity_dimension_rules") or {})
    for dimension in identity_fields:
        dimension_rule = dict(rules.get(dimension) or {})
        expression = _default_identity_expression(
            dimension=dimension,
            rule=dimension_rule,
            candidate=output,
        )
        # Closed enum dimensions are catalog-owned vocabulary. A model may
        # suggest an identity, but it must not replace a provable governed
        # literal with an arbitrary field or context expression.
        if expression and (
            str(dimension_rule.get("kind") or "").strip().casefold() == "enum"
            or not dimensions.get(dimension)
        ):
            dimensions[dimension] = expression

    # A source record can legitimately be less granular than the canonical
    # entity. For example, a single-channel record has an interface key but no
    # explicit channel field. Do not let multiple canonical dimensions fall
    # back to that same source key: retain the first proven mapping and give
    # each missing trailing dimension a stable, dimension-specific sentinel.
    field_inventory = {
        str(item).strip()
        for item in [
            *(output.get("primary_keys") or []),
            *(output.get("required_fields") or []),
            *(output.get("optional_fields") or []),
            *dict(output.get("validators") or {}),
        ]
        if str(item).strip()
    }
    seen_identity_expressions: dict[str, str] = {}
    defaulted_identity_dimensions: list[str] = []
    for dimension in identity_fields:
        expression = dimensions.get(dimension)
        if not isinstance(expression, dict):
            continue
        signature = json.dumps(
            expression,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if signature in seen_identity_expressions and dimension not in field_inventory:
            dimensions[dimension] = {"literal": f"default:{dimension}"}
            defaulted_identity_dimensions.append(dimension)
            continue
        seen_identity_expressions[signature] = dimension

    # The catalog identity can be intentionally coarser than the approved
    # Parser record key. Preserve every remaining source key as an explicit
    # dimension so records cannot collapse while still keeping the canonical
    # identity field order owned by the catalog.
    referenced_source_fields = set(dimension_field_references(dimensions))
    preserved_source_keys: list[str] = []
    for source_key in (
        str(item).strip()
        for item in output.get("primary_keys") or []
        if str(item).strip()
    ):
        if source_key in referenced_source_fields:
            continue
        dimension_name = source_key
        if dimension_name in dimensions:
            dimension_name = f"source_{source_key}"
            suffix = 2
            while dimension_name in dimensions:
                dimension_name = f"source_{source_key}_{suffix}"
                suffix += 1
        dimensions[dimension_name] = {"field": source_key}
        referenced_source_fields.add(source_key)
        preserved_source_keys.append(source_key)
    output["entity_dimensions"] = dimensions
    output["entity_identity_fields"] = identity_fields + [
        dimension for dimension in dimensions if dimension not in identity_fields
    ]
    if preserved_source_keys or defaulted_identity_dimensions:
        evidence = dict(output.get("evidence") or {})
        if preserved_source_keys:
            evidence["source_primary_keys_preserved"] = preserved_source_keys
        if defaulted_identity_dimensions:
            evidence["defaulted_canonical_identity_dimensions"] = {
                dimension: f"default:{dimension}"
                for dimension in defaulted_identity_dimensions
            }
        output["evidence"] = evidence
    try:
        normalized_identity_dimensions = normalize_dimensions(dimensions)
    except SemanticProjectionError:
        return output

    # Entity identity is already part of entity_key. Weak models sometimes
    # repeat the same scope/device expressions on every observation field,
    # which turns identity into a metric slice and conflicts with governed
    # business dimensions such as window or direction. Remove only exact
    # duplicates; a conflicting expression remains visible to validation.
    semantics = dict(output.get("state_semantics") or {})
    removed_by_field: dict[str, list[str]] = {}
    for field, raw_metadata in tuple(semantics.items()):
        if not isinstance(raw_metadata, dict) or not raw_metadata.get("dimensions"):
            continue
        metadata = dict(raw_metadata)
        try:
            field_dimensions = normalize_dimensions(metadata.get("dimensions"))
        except SemanticProjectionError:
            continue
        protected_dimensions = governed_field_dimensions or set()
        removed = [
            name
            for name, expression in normalized_identity_dimensions.items()
            if name not in protected_dimensions
            and field_dimensions.get(name) == expression
        ]
        if not removed:
            continue
        for name in removed:
            field_dimensions.pop(name, None)
        if field_dimensions:
            metadata["dimensions"] = field_dimensions
        else:
            metadata.pop("dimensions", None)
        semantics[str(field)] = metadata
        removed_by_field[str(field)] = sorted(removed)
    output["state_semantics"] = semantics
    if removed_by_field:
        evidence = dict(output.get("evidence") or {})
        evidence["redundant_identity_dimensions_normalized"] = {
            "strategy": "entity_identity_owned_by_entity_key",
            "fields": removed_by_field,
        }
        output["evidence"] = evidence
    return output


def _prune_non_owned_observation_targets(
    candidate: dict[str, Any],
    *,
    allowed_observation_types: set[str],
) -> dict[str, Any]:
    """Remove namespaces not approved for this Parser record set."""

    output = json.loads(json.dumps(dict(candidate)))
    semantics = dict(output.get("state_semantics") or {})
    removed: dict[str, str] = {}
    for field, raw in tuple(semantics.items()):
        if not isinstance(raw, dict):
            continue
        metadata = dict(raw)
        observation_type = str(metadata.get("observation_type") or "").strip()
        if not observation_type or observation_type in allowed_observation_types:
            continue
        for key in (
            "observation_type",
            "value_mapping",
            "value_mapping_mode",
            "on_unmapped",
            "event_generation",
            "event_role",
            "policy_constraints",
            "event_constraints",
            "threshold_references",
        ):
            metadata.pop(key, None)
        metadata["comparison_role"] = "metadata"
        metadata["monitor"] = False
        semantics[field] = metadata
        removed[str(field)] = observation_type
    output["state_semantics"] = semantics
    if removed:
        evidence = dict(output.get("evidence") or {})
        evidence["non_owned_or_unapproved_observation_targets_pruned"] = removed
        output["evidence"] = evidence
    return output


def _contract_governance_errors(
    candidate: dict[str, Any],
    *,
    field_inventory: list[str],
    contract_seed: dict[str, Any] | None,
    require_policy_targets: bool = False,
    observation_targets: list[dict[str, Any]] | None = None,
    strict_observation_targets: bool = False,
    canonical_identity_constraint: dict[str, Any] | None = None,
    field_profiles: dict[str, dict[str, Any]] | None = None,
    sample_records: list[dict[str, Any]] | None = None,
    allow_semantic_overrides: bool = False,
) -> list[str]:
    errors: list[str] = []
    required = [str(item) for item in candidate.get("required_fields") or []]
    optional = [str(item) for item in candidate.get("optional_fields") or []]
    declared = required + optional
    duplicates = sorted({field for field in declared if declared.count(field) > 1})
    if duplicates:
        errors.append("fields must be declared exactly once: " + ", ".join(duplicates))
    missing = sorted(set(field_inventory) - set(declared))
    if missing:
        errors.append("contract omits fields emitted by the approved Parser: " + ", ".join(missing))

    seed = dict(contract_seed or {})
    for key in ("category", "entity_type"):
        expected = str(seed.get(key) or "").strip()
        if expected and str(candidate.get(key) or "").strip() != expected:
            errors.append(f"{key} must match the approved Parser contract seed: {expected}")
    seed_keys = [str(item) for item in seed.get("primary_keys") or []]
    if (
        "primary_keys" in seed
        and [str(item) for item in candidate.get("primary_keys") or []] != seed_keys
    ):
        errors.append("primary_keys must match the approved Parser contract seed")
    seed_required = {str(item) for item in seed.get("required_fields") or []}
    if "required_fields" in seed and set(required) != seed_required:
        errors.append(
            "required_fields must match the approved Parser contract seed: "
            + ", ".join(sorted(seed_required))
        )
    seed_validators = dict(seed.get("validators") or {})
    candidate_validators = dict(candidate.get("validators") or {})
    for field, expected in seed_validators.items():
        if candidate_validators.get(field) != expected:
            errors.append(f"validators.{field} must match the approved Parser contract seed")

    identity_constraint = dict(canonical_identity_constraint or {})
    identity_dimension_rules = {
        str(name): dict(rule)
        for name, rule in dict(
            identity_constraint.get("identity_dimension_rules") or {}
        ).items()
        if isinstance(rule, dict)
    }
    canonical_entity_type = str(
        candidate.get("canonical_entity_type") or ""
    ).strip()
    identity_fields = [
        str(item).strip()
        for item in candidate.get("entity_identity_fields") or []
        if str(item).strip()
    ]
    identity_dimensions = candidate.get("entity_dimensions") or {}
    has_canonical_identity = bool(
        canonical_entity_type or identity_fields or identity_dimensions
    )
    if has_canonical_identity:
        if not canonical_entity_type:
            errors.append("canonical_entity_type is required for canonical identity")
        if not identity_fields:
            errors.append("entity_identity_fields are required for canonical identity")
        if len(identity_fields) != len(set(identity_fields)):
            errors.append("entity_identity_fields must be unique")
        try:
            normalized_identity_dimensions = normalize_dimensions(
                identity_dimensions
            )
        except SemanticProjectionError as exc:
            errors.append(f"entity_dimensions is invalid: {exc}")
            normalized_identity_dimensions = {}
        if set(identity_fields) != set(normalized_identity_dimensions):
            errors.append(
                "entity_identity_fields must exactly match entity_dimensions"
            )
        identity_expression_owners: dict[str, list[str]] = {}
        for dimension, expression in normalized_identity_dimensions.items():
            signature = json.dumps(
                expression,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            identity_expression_owners.setdefault(signature, []).append(dimension)
        duplicated_identity_sources = [
            sorted(dimensions)
            for dimensions in identity_expression_owners.values()
            if len(dimensions) > 1
        ]
        for dimensions in sorted(duplicated_identity_sources):
            errors.append(
                "entity_dimensions cannot map distinct canonical identity "
                "dimensions to the same source expression: "
                + ", ".join(dimensions)
            )
        unknown_identity_fields = sorted(
            set(dimension_field_references(normalized_identity_dimensions))
            - set(field_inventory)
        )
        if unknown_identity_fields:
            errors.append(
                "entity_dimensions references fields absent from the approved Parser: "
                + ", ".join(unknown_identity_fields)
            )
        identity_source_fields = set(
            dimension_field_references(normalized_identity_dimensions)
        )
        missing_source_keys = sorted(
            {
                str(item)
                for item in candidate.get("primary_keys") or []
                if str(item).strip()
            }
            - identity_source_fields
        )
        if missing_source_keys:
            errors.append(
                "entity_dimensions must preserve every approved source primary "
                "key unless an explicit aggregation contract is approved; missing: "
                + ", ".join(missing_source_keys)
            )
        for dimension, rule in identity_dimension_rules.items():
            if (
                str(rule.get("kind") or "") == "enum"
                and set(normalized_identity_dimensions.get(dimension) or {})
                != {"literal"}
            ):
                errors.append(
                    f"entity_dimensions.{dimension} is an enum and must use "
                    "one approved literal value, not a source field or operation"
                )
        for index, record in enumerate(sample_records or []):
            try:
                resolved_identity = resolve_dimensions(
                    normalized_identity_dimensions,
                    record,
                    context={"device_id": "sanitized-device"},
                )
            except SemanticProjectionError as exc:
                errors.append(
                    f"entity_dimensions cannot resolve records[{index}]: {exc}"
                )
                break
            for dimension, rule in identity_dimension_rules.items():
                if dimension not in resolved_identity:
                    continue
                if str(rule.get("kind") or "") == "enum":
                    allowed = {
                        str(item).strip().casefold()
                        for item in rule.get("allowed_values") or []
                    }
                    actual = str(resolved_identity[dimension]).strip()
                    if actual.casefold() not in allowed:
                        errors.append(
                            f"entity_dimensions.{dimension} resolved to "
                            f"{actual!r}, outside approved values "
                            f"{sorted(allowed)}"
                        )
                        break
            else:
                continue
            break

    expected_entity_type = str(
        identity_constraint.get("canonical_entity_type") or ""
    ).strip()
    expected_identity_fields = [
        str(item).strip()
        for item in identity_constraint.get("identity_dimensions") or []
        if str(item).strip()
    ]
    if expected_entity_type and canonical_entity_type != expected_entity_type:
        errors.append(
            "canonical_entity_type must match the approved check item: "
            + expected_entity_type
        )
    if (
        expected_identity_fields
        and identity_fields[: len(expected_identity_fields)]
        != expected_identity_fields
    ):
        errors.append(
            "entity_identity_fields must begin with the approved check item order: "
            + ", ".join(expected_identity_fields)
        )

    semantics = dict(candidate.get("state_semantics") or {})
    policy_target_count = 0
    observation_fields: dict[str, list[str]] = {}
    projected_values_by_field: dict[str, list[Any]] = {}
    for field in declared:
        actual = semantics.get(field)
        if not isinstance(actual, dict) or not str(actual.get("semantic_type") or "").strip():
            errors.append(f"state_semantics.{field}.semantic_type is required")
            continue
        semantic_type = str(actual.get("semantic_type") or "").strip().casefold()
        comparison_role = str(actual.get("comparison_role") or "").strip().casefold()
        observation_type = str(actual.get("observation_type") or "").strip()
        excluded = (
            actual.get("ignore") is True
            or actual.get("monitor") is False
            or comparison_role in NON_TARGET_ROLES
        )
        if observation_type:
            observation_fields.setdefault(observation_type, []).append(field)
            if not OBSERVATION_TYPE_PATTERN.fullmatch(observation_type):
                errors.append(
                    f"state_semantics.{field}.observation_type must use a stable "
                    "lowercase dot namespace such as interface.runtime_statistics.line_status"
                )
            if semantic_type not in COMPARABLE_SEMANTIC_TYPES:
                errors.append(
                    f"state_semantics.{field} cannot be monitored because semantic_type "
                    f"{semantic_type!r} is not comparable"
                )
            if excluded:
                errors.append(
                    f"state_semantics.{field} cannot declare observation_type while marked "
                    "as a non-target field"
                )
        transform = actual.get("transform")
        if transform is not None:
            for message in validate_transform(transform):
                errors.append(f"state_semantics.{field}.{message}")
            unknown_transform_fields = sorted(
                set(transform_field_references(transform)) - set(field_inventory)
            )
            if unknown_transform_fields:
                errors.append(
                    f"state_semantics.{field}.transform references fields absent from "
                    "the approved Parser: "
                    + ", ".join(unknown_transform_fields)
                )
        value_mapping = actual.get("value_mapping")
        if value_mapping is not None:
            for message in validate_value_mapping(
                value_mapping,
                mode=actual.get("value_mapping_mode") or "casefold",
                on_unmapped=actual.get("on_unmapped") or "reject",
            ):
                errors.append(f"state_semantics.{field}.{message}")
            profile = dict((field_profiles or {}).get(field) or {})
            observed_values = profile.get("observed_values")
            if (
                str(actual.get("on_unmapped") or "reject").strip().casefold()
                == "reject"
                and isinstance(observed_values, list)
            ):
                for observed_value in observed_values:
                    try:
                        apply_value_mapping(
                            observed_value,
                            value_mapping,
                            mode=actual.get("value_mapping_mode") or "casefold",
                            on_unmapped="reject",
                        )
                    except SemanticProjectionError:
                        errors.append(
                            f"state_semantics.{field}.value_mapping does not cover "
                            "an observed source value from the full structured dataset: "
                            f"{observed_value!r}"
                        )
        if actual.get("emit_when_empty") is True and value_mapping is None:
            errors.append(
                f"state_semantics.{field}.emit_when_empty requires value_mapping"
            )
        projected_values: list[Any] = []
        for record_index, record in enumerate(sample_records or []):
            if field not in record:
                continue
            raw_value = record.get(field)
            is_empty = raw_value is None or (
                isinstance(raw_value, str) and not raw_value.strip()
            )
            if is_empty and actual.get("emit_when_empty") is not True:
                continue
            normalized_record = {
                **dict(record),
                **{
                    str(name).strip().lower(): value
                    for name, value in record.items()
                },
            }
            try:
                projected = project_value(
                    field_name=field,
                    source_value=raw_value,
                    normalized_record=normalized_record,
                    field_semantics=actual,
                )
            except SemanticProjectionError as exc:
                errors.append(
                    f"state_semantics.{field} cannot project records[{record_index}]: "
                    f"{exc}"
                )
                continue
            projected_values.append(projected)
        projected_values_by_field[field] = projected_values
        try:
            normalize_dimensions(actual.get("dimensions"))
            unknown_dimension_fields = sorted(
                set(dimension_field_references(actual.get("dimensions")))
                - set(field_inventory)
            )
            if unknown_dimension_fields:
                errors.append(
                    f"state_semantics.{field}.dimensions references fields absent from "
                    "the approved Parser: "
                    + ", ".join(unknown_dimension_fields)
                )
        except SemanticProjectionError as exc:
            errors.append(f"state_semantics.{field}.dimensions is invalid: {exc}")
        for message in validate_unit_conversion(
            source_unit=actual.get("source_unit") or actual.get("unit"),
            target_unit=actual.get("canonical_unit") or actual.get("unit"),
        ):
            errors.append(f"state_semantics.{field}.{message}")
        if (
            require_policy_targets
            and semantic_type in NUMERIC_SEMANTIC_TYPES
            and not excluded
            and int(((field_profiles or {}).get(field) or {}).get("numeric_count") or 0) == 0
        ):
            errors.append(
                f"state_semantics.{field} uses numeric semantic_type={semantic_type} but "
                "the structured records contain no numeric values; mark it non-target or "
                "fix the Parser normalization"
            )
        if require_policy_targets and semantic_type in COMPARABLE_SEMANTIC_TYPES and not excluded:
            if observation_type:
                policy_target_count += 1
            else:
                errors.append(
                    f"state_semantics.{field} must set observation_type for monitoring "
                    "or explicitly set monitor=false/comparison_role for a non-target field"
                )
    if require_policy_targets and not policy_target_count:
        errors.append(
            "contract must expose at least one evidenced comparable field as a policy target"
        )
    for observation_type, fields in observation_fields.items():
        if len(fields) <= 1:
            continue
        dimension_signatures: dict[str, str] = {}
        missing_dimensions: list[str] = []
        duplicate_dimensions: list[str] = []
        for field in fields:
            dimensions = (semantics.get(field) or {}).get("dimensions")
            if not dimensions:
                missing_dimensions.append(field)
                continue
            try:
                signature = dimension_schema_signature(dimensions)
            except SemanticProjectionError:
                continue
            if signature in dimension_signatures:
                duplicate_dimensions.extend([dimension_signatures[signature], field])
            else:
                dimension_signatures[signature] = field
        if missing_dimensions:
            errors.append(
                f"observation_type {observation_type} maps to multiple fields without "
                "explicit dimensions: "
                + ", ".join(sorted(missing_dimensions))
            )
        if duplicate_dimensions:
            errors.append(
                f"observation_type {observation_type} has duplicate dimension mappings: "
                + ", ".join(sorted(set(duplicate_dimensions)))
            )
    expected_observation_types: set[str] = set()
    for target in observation_targets or []:
        target_semantic_type = str(
            target.get("semantic_type") or ""
        ).strip().casefold()
        if target_semantic_type and target_semantic_type not in COMPARABLE_SEMANTIC_TYPES:
            continue
        observation_type = str(target.get("observation_type") or "").strip()
        if not observation_type:
            errors.append("observation target requires observation_type")
            continue
        expected_observation_types.add(observation_type)
        mapped_fields = observation_fields.get(observation_type, [])
        if not mapped_fields:
            if target.get("required", True) is not False:
                errors.append(
                    f"required observation target {observation_type} is not "
                    "produced by any field"
                )
            continue
        canonical_field = str(target.get("canonical_field") or "").strip()
        if canonical_field:
            matches = _target_field_matches(field_inventory, target, semantics)
            # canonical_field names the vendor-neutral report field, not a
            # mandatory source-column spelling. Use an objective alias match
            # when one exists, while allowing an explicitly mapped vendor
            # field to prove compatibility through sampled values, units and
            # the governed target below.
            if matches and not set(matches).intersection(mapped_fields):
                errors.append(
                    f"observation target {observation_type} does not map its governed "
                    f"canonical field {canonical_field!r}"
                )
        expected_dimensions = {
            str(item).strip()
            for item in target.get("dimensions") or []
            if str(item).strip()
        }
        expected_unit = str(
            target.get("unit") or target.get("canonical_unit") or ""
        ).strip().casefold()
        expected_semantic_type = str(
            target.get("semantic_type") or ""
        ).strip().casefold()
        allowed_values = list(target.get("allowed_values") or [])

        def value_token(value: Any) -> tuple[str, Any]:
            if isinstance(value, str):
                return ("string", value.strip().casefold())
            return (type(value).__name__, value)

        allowed_value_tokens = {value_token(value) for value in allowed_values}
        for field in mapped_fields:
            actual = dict(semantics.get(field) or {})
            try:
                actual_dimensions = set(
                    normalize_dimensions(actual.get("dimensions"))
                )
            except SemanticProjectionError:
                actual_dimensions = set()
            if actual_dimensions != expected_dimensions:
                errors.append(
                    f"state_semantics.{field}.dimensions must match governed "
                    f"dimensions {sorted(expected_dimensions)}"
                )
            actual_unit = str(
                actual.get("canonical_unit") or actual.get("unit") or ""
            ).strip().casefold()
            if expected_unit and actual_unit != expected_unit:
                errors.append(
                    f"state_semantics.{field} must use canonical unit "
                    f"{expected_unit!r}"
                )
            actual_semantic_type = str(
                actual.get("semantic_type") or ""
            ).strip().casefold()
            compatible_semantics = {
                ("gauge", "percentage"),
                ("percentage", "gauge"),
            }
            if (
                expected_semantic_type
                and actual_semantic_type != expected_semantic_type
                and (expected_semantic_type, actual_semantic_type)
                not in compatible_semantics
            ):
                errors.append(
                    f"state_semantics.{field}.semantic_type must match governed "
                    f"value {expected_semantic_type!r}"
                )
            if allowed_value_tokens:
                mapped_outputs = (
                    list(dict(actual.get("value_mapping") or {}).values())
                    if actual.get("value_mapping") is not None
                    else projected_values_by_field.get(field, [])
                )
                unapproved_values = [
                    value
                    for value in mapped_outputs
                    if value_token(value) not in allowed_value_tokens
                ]
                if unapproved_values:
                    errors.append(
                        f"state_semantics.{field} emits values outside governed "
                        f"{observation_type} values {allowed_values}: "
                        + ", ".join(
                            sorted({repr(value) for value in unapproved_values})
                        )
                    )
                if (
                    not projected_values_by_field.get(field)
                    and actual.get("emit_when_empty") is not True
                ):
                    errors.append(
                        f"state_semantics.{field} has no evidenced values for "
                        f"{observation_type}"
                    )
    if strict_observation_targets:
        extras = sorted(set(observation_fields) - expected_observation_types)
        if extras:
            errors.append("contract exposes unapproved observation types: " + ", ".join(extras))
    target_owned_fields = {
        field
        for observation_type, fields in observation_fields.items()
        if observation_type in expected_observation_types
        for field in fields
    }
    if not allow_semantic_overrides:
        for field, expected in _governed_semantic_hints(field_inventory, seed).items():
            actual = semantics.get(field)
            if not isinstance(actual, dict):
                continue
            governed_keys = [
                "semantic_type",
                "unit",
                "ignore",
                "applicability",
            ]
            if field in target_owned_fields:
                governed_keys = [key for key in governed_keys if key == "applicability"]
            if not strict_observation_targets:
                governed_keys.append("observation_type")
            for key in governed_keys:
                if key in expected and actual.get(key) != expected[key]:
                    errors.append(
                        f"state_semantics.{field}.{key} must match governed value "
                        f"{expected[key]!r}"
                    )
    for field, actual in semantics.items():
        if not isinstance(actual, dict):
            continue
        if actual.get("applicability"):
            errors.extend(
                f"state_semantics.{field}.{message}"
                for message in validate_applicability(
                    actual["applicability"],
                    declared_fields=set(field_inventory),
                )
            )
    return list(dict.fromkeys(errors))


def _agent_loop(
    *,
    mode: str,
    result: dict[str, Any],
    command: str,
) -> dict[str, Any]:
    proposal = result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    errors: list[str] = []
    for field in validation.get("missing_fields") or []:
        errors.append(f"required field missing: {field}")
    if validation.get("primary_key_status") == "invalid":
        errors.append("primary key validation failed")
    value_error_count = int(validation.get("value_error_count") or 0)
    if value_error_count:
        errors.append(f"value validation failed: {value_error_count} errors")
    success = result.get("success") is True
    revision_request = None
    if not success:
        revision_request = {
            "target_generator": "observation-contract-generator",
            "reason": errors or ["contract validation failed"],
            "required_fixes": [
                "choose stable non-empty primary keys",
                "keep required_fields aligned with parsed records",
                "remove dynamic fields from primary_keys",
                "fix validators for fields that failed deterministic checks",
            ],
            "next_params_patch": {
                "mode": "infer_and_validate",
                "command": command,
                "agent_loop": True,
            },
        }
    return {
        "ready_for_approval": success,
        "next_action": "submit_for_approval" if success else "revise_observation_contract",
        "revision_request": revision_request,
        "attempts": [
            {
                "attempt": 1,
                "stage": "observation_contract_generation",
                "generator": "observation-contract-generator",
                "mode": mode,
                "success": success,
                "input_summary": {
                    "command": command,
                    "record_count": validation.get("record_count", 0),
                },
                "output_summary": {
                    "category": proposal.get("category"),
                    "entity_type": proposal.get("entity_type"),
                    "primary_keys": proposal.get("primary_keys") or [],
                    "required_fields": proposal.get("required_fields") or [],
                    "semantic_field_count": validation.get("semantic_field_count", 0),
                },
                "validator_feedback": {
                    "errors": errors,
                    "missing_fields": validation.get("missing_fields") or [],
                    "duplicate_key_count": validation.get("duplicate_key_count", 0),
                    "key_error_count": validation.get("key_error_count", 0),
                    "value_error_count": value_error_count,
                },
                "revision_request": revision_request,
            }
        ],
    }


def _record_set_records(
    records: list[dict[str, Any]],
    name: str,
) -> list[dict[str, Any]]:
    return [
        {
            str(key): value
            for key, value in record.items()
            if str(key) != "__record_set__"
        }
        for record in records
        if str(record.get("__record_set__") or "") == name
    ]


def _record_set_groups(
    groups: list[dict[str, Any]],
    name: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for group in groups:
        records = _record_set_records(
            [
                dict(item)
                for item in group.get("records") or []
                if isinstance(item, dict)
            ],
            name,
        )
        if records:
            output.append(
                {
                    "group_id": str(group.get("group_id") or "default"),
                    "records": records,
                }
            )
    return output


def _prune_unapproved_relation_bindings(
    contract: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Remove relation targets outside the governed observation namespace."""

    output = json.loads(json.dumps(dict(contract or {})))
    record_sets = {
        str(item.get("name") or "").strip(): item
        for item in output.get("record_sets") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    retained_relations: list[dict[str, Any]] = []
    removed: list[dict[str, str]] = []
    for raw_relation in output.get("record_set_relations") or []:
        if not isinstance(raw_relation, dict):
            continue
        relation = dict(raw_relation)
        source_name = str(relation.get("source_record_set") or "").strip()
        source_set = record_sets.get(source_name) or {}
        semantics = dict(source_set.get("state_semantics") or {})
        bindings: dict[str, dict[str, Any]] = {}
        for source_field, roles in dict(
            relation.get("threshold_bindings") or {}
        ).items():
            metadata = semantics.get(str(source_field))
            if not isinstance(metadata, dict) or not str(
                metadata.get("observation_type") or ""
            ).strip():
                removed.append(
                    {
                        "relation_name": str(relation.get("name") or ""),
                        "record_set_name": source_name,
                        "field_name": str(source_field),
                        "reason": "field_is_not_an_approved_observation_target",
                    }
                )
                continue
            if isinstance(roles, dict) and roles:
                bindings[str(source_field)] = dict(roles)
        if not bindings:
            continue
        target_name = str(relation.get("target_record_set") or "").strip()
        if source_name == target_name:
            local_references_match = all(
                str(
                    (
                        dict(
                            (semantics.get(source_field) or {}).get(
                                "threshold_references"
                            )
                            or {}
                        ).get(role)
                        or {}
                    ).get("field_name")
                    or ""
                )
                == str(target_field)
                for source_field, roles in bindings.items()
                for role, target_field in roles.items()
            )
            if local_references_match:
                removed.extend(
                    {
                        "relation_name": str(relation.get("name") or ""),
                        "record_set_name": source_name,
                        "field_name": source_field,
                        "reason": "redundant_same_record_set_threshold_reference",
                    }
                    for source_field in sorted(bindings)
                )
                continue
        relation["threshold_bindings"] = bindings
        retained_relations.append(relation)
    output["record_set_relations"] = retained_relations
    return output, removed


def _run_multi_record_llm_generation(
    *,
    params: dict[str, Any],
    command: str,
    records: list[dict[str, Any]],
    record_groups: list[dict[str, Any]],
    proposal_payload: dict[str, Any],
    generation_provider: str,
) -> dict[str, Any]:
    seed_sets = [
        dict(item)
        for item in proposal_payload.get("record_sets") or []
        if isinstance(item, dict)
    ]
    seed_by_name = {
        str(item.get("name") or "").strip(): item
        for item in seed_sets
        if str(item.get("name") or "").strip()
    }
    if len(seed_by_name) != len(seed_sets):
        raise ValueError("approved Parser record_sets must have unique non-empty names")

    observation_targets = [
        dict(item)
        for item in params.get("observation_targets") or []
        if isinstance(item, dict)
    ]
    strict_observation_targets = bool(
        params.get("strict_observation_targets", bool(observation_targets))
    )
    canonical_identity_constraint = dict(
        params.get("canonical_identity_constraint") or {}
    )
    required_observation_types = {
        str(target.get("observation_type") or "").strip()
        for target in observation_targets
        if target.get("required", True) is not False
        and str(target.get("observation_type") or "").strip()
    }

    context_sets: list[dict[str, Any]] = []
    for name, seed in seed_by_name.items():
        set_records = _record_set_records(records, name)
        field_inventory = sorted(
            {
                str(field)
                for record in set_records
                for field in record
                if str(field).strip()
            }
        )
        governed_hints = _governed_semantic_hints(
            field_inventory,
            seed,
        )
        if strict_observation_targets:
            governed_hints = {
                field: {
                    key: value
                    for key, value in hint.items()
                    if key != "observation_type"
                }
                for field, hint in governed_hints.items()
            }
        context_sets.append(
            {
                "name": name,
                "field_inventory": field_inventory,
                "field_profiles": profile_fields(
                    set_records,
                    field_inventory=field_inventory,
                ),
                "records": set_records,
                "contract_constraints": {
                    key: seed.get(key)
                    for key in (
                        "entity_type",
                        "cardinality",
                        "presence",
                        "primary_keys",
                        "required_fields",
                        "optional_fields",
                        "validators",
                        "allow_empty",
                        "empty_patterns",
                        "ignore_fields",
                        "source_field_hints",
                    )
                    if seed.get(key) is not None
                },
                "governed_semantic_hints": governed_hints,
                "allowed_observation_targets": observation_targets,
                "canonical_identity_constraint": canonical_identity_constraint,
            }
        )

    targets_by_set, target_owner_by_type = _partition_multi_record_observation_targets(
        context_sets=context_sets,
        observation_targets=observation_targets,
    )
    for context_set in context_sets:
        name = str(context_set.get("name") or "").strip()
        set_targets = targets_by_set.get(name, [])
        context_set["allowed_observation_targets"] = set_targets
        set_identity_constraints = _identity_constraints_for_targets(set_targets)
        context_set["canonical_identity_constraint"] = (
            set_identity_constraints[0]
            if len(set_identity_constraints) == 1
            else {}
        )
        context_set["canonical_identity_candidates"] = set_identity_constraints
    governed_observation_types = {
        str(target.get("observation_type") or "").strip()
        for target in observation_targets
        if str(target.get("observation_type") or "").strip()
    }

    def validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        candidate_sets = [
            dict(item)
            for item in candidate.get("record_sets") or []
            if isinstance(item, dict)
        ]
        candidate_by_name = {
            str(item.get("name") or "").strip(): item
            for item in candidate_sets
            if str(item.get("name") or "").strip()
        }
        errors: list[str] = []
        if set(candidate_by_name) != set(seed_by_name):
            errors.append(
                "record_sets must match the approved Parser contract: "
                f"expected={sorted(seed_by_name)}, actual={sorted(candidate_by_name)}"
            )
        validated_sets: list[dict[str, Any]] = []
        group_reports: dict[str, Any] = {}
        mapped_observation_types: set[str] = set()
        for name, seed in seed_by_name.items():
            raw_candidate = candidate_by_name.get(name)
            if raw_candidate is None:
                continue
            set_records = _record_set_records(records, name)
            set_groups = _record_set_groups(record_groups, name)
            if not set_groups and set_records:
                set_groups = [{"group_id": "default", "records": set_records}]
            field_inventory = sorted(
                {
                    str(field)
                    for record in set_records
                    for field in record
                    if str(field).strip()
                }
            )
            schema_bound = _inherit_approved_parser_schema(raw_candidate, seed)
            schema_bound["name"] = name
            set_allowed_targets = targets_by_set.get(name, observation_targets)
            allowed_observation_types = {
                str(target.get("observation_type") or "").strip()
                for target in set_allowed_targets
                if str(target.get("observation_type") or "").strip()
            }
            governed_hints = _governed_semantic_hints(field_inventory, seed)
            if strict_observation_targets:
                governed_hints = {
                    field: {
                        key: value
                        for key, value in hint.items()
                        if key != "observation_type"
                    }
                    for field, hint in governed_hints.items()
                }
            governed = _apply_governed_semantic_hints(
                schema_bound,
                governed_hints,
            )
            if strict_observation_targets:
                governed = _prune_non_owned_observation_targets(
                    governed,
                    allowed_observation_types=allowed_observation_types,
                )
            governed = _apply_observation_target_constraints(
                governed,
                field_inventory=field_inventory,
                observation_targets=set_allowed_targets,
                strict=strict_observation_targets,
            )
            governed = _apply_governed_projection_recipes(
                governed,
                field_inventory=field_inventory,
                observation_targets=set_allowed_targets,
            )
            governed = _apply_observation_target_constraints(
                governed,
                field_inventory=field_inventory,
                observation_targets=set_allowed_targets,
                strict=strict_observation_targets,
            )
            governed = normalize_contract_semantics(governed)
            enriched = enrich_contract_reference_semantics(
                contract=governed,
                records=set_records,
            )
            set_observation_types = {
                str(metadata.get("observation_type") or "").strip()
                for metadata in dict(enriched.get("state_semantics") or {}).values()
                if isinstance(metadata, dict)
                and str(metadata.get("observation_type") or "").strip()
            }
            owned_types = {
                observation_type
                for observation_type, owner in target_owner_by_type.items()
                if owner == name
            }
            set_targets = [
                target
                for target in set_allowed_targets
                if str(target.get("observation_type") or "").strip()
                in set_observation_types | owned_types
            ]
            set_identity_constraints = _identity_constraints_for_targets(
                set_targets
            )
            if len(set_identity_constraints) > 1:
                errors.append(
                    f"{name}: one Parser record set cannot emit observations "
                    "with different canonical entity identities; move each "
                    "check-item target to the record set that represents its "
                    "entity grain"
                )
                identity_constraint = {}
            else:
                identity_constraint = (
                    set_identity_constraints[0]
                    if set_identity_constraints
                    else canonical_identity_constraint
                    if set_targets
                    else {}
                )
            enriched = _apply_canonical_identity_defaults(
                enriched,
                identity_constraint,
                governed_field_dimensions={
                    str(dimension).strip()
                    for target in set_targets
                    for dimension in target.get("dimensions") or []
                    if str(dimension).strip()
                },
            )
            report = _validate_contract_by_group(
                command=command,
                groups=set_groups,
                candidate=enriched,
                field_inventory=field_inventory,
                contract_seed=seed,
                require_policy_targets=bool(set_targets),
                observation_targets=set_targets,
                strict_observation_targets=strict_observation_targets,
                canonical_identity_constraint=identity_constraint,
                allow_semantic_overrides=bool(
                    params.get("allow_semantic_overrides")
                ),
            )
            group_reports[name] = report
            errors.extend(
                f"{name}: {message}"
                for message in report.get("errors") or []
            )
            validated = _inherit_approved_parser_schema(
                dict(report.get("proposal") or enriched),
                seed,
            )
            validated["name"] = name
            validated_sets.append(validated)
            mapped_observation_types.update(
                str(metadata.get("observation_type") or "").strip()
                for metadata in dict(
                    validated.get("state_semantics") or {}
                ).values()
                if isinstance(metadata, dict)
                and str(metadata.get("observation_type") or "").strip()
            )

        mapped_locations: dict[str, list[tuple[str, str, str]]] = {}
        targets_by_type = {
            str(target.get("observation_type") or "").strip(): target
            for target in observation_targets
            if str(target.get("observation_type") or "").strip()
        }
        for record_set in validated_sets:
            name = str(record_set.get("name") or "").strip()
            for field, raw in dict(record_set.get("state_semantics") or {}).items():
                if not isinstance(raw, dict):
                    continue
                observation_type = str(raw.get("observation_type") or "").strip()
                if observation_type not in governed_observation_types:
                    continue
                dimension_declaration = raw.get("dimensions") or record_set.get(
                    "entity_dimensions"
                )
                try:
                    dimension_signature = dimension_schema_signature(
                        dimension_declaration
                    )
                except SemanticProjectionError:
                    dimension_signature = "invalid"
                mapped_locations.setdefault(observation_type, []).append(
                    (name, str(field), dimension_signature)
                )
        duplicate_target_bindings: dict[str, list[str]] = {}
        for observation_type, locations in mapped_locations.items():
            if len(locations) <= 1:
                continue
            target = targets_by_type.get(observation_type) or {}
            expected_dimensions = {
                str(item).strip()
                for item in target.get("dimensions") or []
                if str(item).strip()
            }
            signatures = [item[2] for item in locations]
            if (
                "invalid" not in signatures
                and len(set(signatures)) == len(signatures)
                and (expected_dimensions or all(signatures))
            ):
                continue
            labels = [f"{name}.{field}" for name, field, _ in locations]
            duplicate_target_bindings[observation_type] = labels
            errors.append(
                f"governed observation target {observation_type} is bound by "
                f"multiple record sets without distinct dimensions: {labels}"
            )
        missing_required_targets = sorted(
            required_observation_types - mapped_observation_types
        )
        if missing_required_targets:
            errors.append(
                "multi-record contract does not cover required observation targets: "
                + ", ".join(missing_required_targets)
            )
        proposal = {
            "category": "multi_record",
            "entity_type": "multi_record",
            "primary_keys": [],
            "required_fields": [],
            "optional_fields": [],
            "validators": {},
            "allow_empty": False,
            "empty_patterns": [],
            "ignore_fields": [],
            "state_semantics": {},
            "record_sets": validated_sets,
            "record_set_relations": [
                dict(item)
                for item in candidate.get("record_set_relations") or []
                if isinstance(item, dict)
            ],
            "source": "llm_agent",
            "confidence": candidate.get("confidence", 0.8),
            "evidence": dict(candidate.get("evidence") or {}),
        }
        proposal, pruned_relation_bindings = _prune_unapproved_relation_bindings(
            proposal
        )
        relation_validation = apply_record_set_relations(
            contract=proposal,
            record_groups=record_groups,
        )
        proposal = dict(relation_validation.get("contract") or proposal)
        errors.extend(relation_validation.get("errors") or [])
        reference_errors = required_threshold_reference_errors(proposal)
        errors.extend(
            str(item.get("message") or item.get("code"))
            for item in reference_errors
        )
        proposal["evidence"]["multi_record_target_governance"] = {
            "strategy": "global_required_targets_with_per_record_set_evidence",
            "strict": strict_observation_targets,
            "required": sorted(required_observation_types),
            "mapped": sorted(mapped_observation_types),
            "deterministic_owners": target_owner_by_type,
            "duplicate_bindings": duplicate_target_bindings,
        }
        proposal["evidence"]["record_set_relation_validation"] = {
            "success": relation_validation.get("success") is True,
            "reports": relation_validation.get("reports") or [],
            "role_normalizations": relation_validation.get(
                "role_normalizations"
            )
            or [],
            "required_reference_errors": reference_errors,
            "pruned_unapproved_bindings": pruned_relation_bindings,
        }
        return {
            "success": not errors and len(validated_sets) == len(seed_by_name),
            "errors": errors,
            "record_set_count": len(validated_sets),
            "record_sets": group_reports,
            "record_set_relations": relation_validation.get("reports") or [],
            "required_reference_errors": reference_errors,
            "proposal": proposal,
        }

    context = {
        "command": command,
        "vendor_hint": params.get("vendor"),
        "model_hint": params.get("model"),
        "field_inventory": sorted(
            {
                field
                for item in context_sets
                for field in item["field_inventory"]
            }
        ),
        "records": records,
        "record_sets": context_sets,
        "contract_seed": proposal_payload,
        "observation_targets": observation_targets,
        "strict_observation_targets": strict_observation_targets,
        "canonical_identity_constraint": canonical_identity_constraint,
        "policy_intent": params.get("policy_intent"),
        "event_intent": params.get("event_intent"),
    }
    if str(params.get("mode") or "").strip().casefold() == "validate":
        validation = validate_candidate(proposal_payload)
        return {
            "success": validation.get("success") is True,
            "mode": "validate",
            "proposal": validation.pop("proposal", None) or proposal_payload,
            "validation": validation,
            "generation_provider": generation_provider or "manual",
        }
    generated = RuleGenerationAgent(
        DEFAULT_RULE_GENERATION_PROVIDERS.create(generation_provider),
        max_attempts=int(params.get("max_agent_attempts") or 3),
    ).run(
        stage="observation_contract",
        context=context,
        validator=validate_candidate,
    )
    validation = dict(generated.get("validation") or {})
    return {
        "success": generated.get("success") is True,
        "mode": "llm_agent_loop",
        "proposal": validation.pop("proposal", None) or generated.get("candidate"),
        "validation": validation,
        "agent_loop": generated.get("agent_loop"),
        "generation_provider": generation_provider,
    }


def run_contract_generation(params: dict[str, Any]) -> dict[str, Any]:
    mode = str(params.get("mode") or "infer_and_validate").strip().lower()
    if mode in {
        "projection_candidate",
        "projection_validate",
        "validate_projection",
    }:
        return generate_projection_candidate(params)
    if mode in {"migrate_adapter", "adapter_migration"}:
        return migrate_legacy_adapter_to_v2(
            params.get("adapter_spec") or params.get("proposal"),
            check_items=dict(params.get("check_items") or {}),
            observation_contracts_by_check_item=dict(
                params.get("observation_contracts_by_check_item") or {}
            ),
            parser_field_contract=dict(params.get("parser_field_contract") or {}),
            sample_replay=dict(params.get("sample_replay") or {}),
        )
    if get_settings().ADAPTER_PROJECTION_CUTOVER_ENFORCED:
        raise ValueError(
            "ObservationContract generation is frozen after the CommandAdapter "
            "V2 cutover; use projection_candidate or migrate_adapter"
        )
    command = str(params.get("command") or "").strip()
    if not command:
        raise ValueError("command is required")
    samples_text = str(params.get("samples_text") or params.get("samples_summary") or "")
    records = _records(params)
    record_groups = _record_groups(params, records)
    proposal_payload = params.get("proposal") or params.get("contract")
    generation_provider = str(params.get("generation_provider") or "").strip().casefold()
    if (
        generation_provider == "llm"
        and mode != "validate"
        and isinstance(proposal_payload, dict)
    ):
        proposal_payload = _reconcile_contract_seed_with_records(
            proposal_payload,
            records,
        )
    proposal = proposal_from_dict(proposal_payload) if isinstance(proposal_payload, dict) else None

    if (
        isinstance(proposal_payload, dict)
        and proposal_payload.get("record_sets")
        and (generation_provider == "llm" or mode == "validate")
    ):
        return _run_multi_record_llm_generation(
            params=params,
            command=command,
            records=records,
            record_groups=record_groups,
            proposal_payload=proposal_payload,
            generation_provider=generation_provider,
        )
    if generation_provider == "llm" and mode != "validate":
        field_inventory = sorted(
            {str(field) for record in records for field in record if str(field).strip()}
        )

        observation_targets = [
            dict(item)
            for item in params.get("observation_targets") or []
            if isinstance(item, dict)
        ]
        strict_observation_targets = bool(
            params.get("strict_observation_targets", bool(observation_targets))
        )
        governed_hints = _governed_semantic_hints(
            field_inventory,
            proposal_payload if isinstance(proposal_payload, dict) else None,
        )
        if strict_observation_targets:
            # Generic field aliases can suggest types and units, but only the
            # selected CheckItem owns the canonical observation namespace.
            # A source column named "status" may mean interface link state,
            # transceiver presence, fan health, or another vendor-specific
            # concept depending on the approved production unit.
            governed_hints = {
                field: {
                    key: value
                    for key, value in hint.items()
                    if key != "observation_type"
                }
                for field, hint in governed_hints.items()
            }
        canonical_identity_constraint = dict(
            params.get("canonical_identity_constraint") or {}
        )

        def validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
            schema_bound = _inherit_approved_parser_schema(
                candidate,
                proposal_payload if isinstance(proposal_payload, dict) else None,
            )
            governed = _apply_governed_semantic_hints(schema_bound, governed_hints)
            governed = _apply_observation_target_constraints(
                governed,
                field_inventory=field_inventory,
                observation_targets=observation_targets,
                strict=strict_observation_targets,
            )
            governed = _apply_governed_projection_recipes(
                governed,
                field_inventory=field_inventory,
                observation_targets=observation_targets,
            )
            target_bound = _apply_observation_target_constraints(
                governed,
                field_inventory=field_inventory,
                observation_targets=observation_targets,
                strict=strict_observation_targets,
            )
            target_bound = normalize_contract_semantics(target_bound)
            enriched = enrich_contract_reference_semantics(
                contract=target_bound,
                records=records,
            )
            enriched = _apply_canonical_identity_defaults(
                enriched,
                canonical_identity_constraint,
                governed_field_dimensions={
                    str(dimension).strip()
                    for target in observation_targets
                    for dimension in target.get("dimensions") or []
                    if str(dimension).strip()
                },
            )
            report = _validate_contract_by_group(
                command=command,
                groups=record_groups,
                candidate=enriched,
                field_inventory=field_inventory,
                contract_seed=(proposal_payload if isinstance(proposal_payload, dict) else None),
                require_policy_targets=bool(
                    (params.get("rule_asset_requirements") or {}).get(
                        "requires_change_policy",
                        bool(observation_targets),
                    )
                ),
                observation_targets=observation_targets,
                strict_observation_targets=strict_observation_targets,
                canonical_identity_constraint=canonical_identity_constraint,
                field_profiles=dict(params.get("field_profiles") or {}),
            )
            validated_proposal = dict(report.get("proposal") or enriched)
            reference_errors = required_threshold_reference_errors(
                validated_proposal
            )
            if reference_errors:
                report["success"] = False
                report["errors"] = list(
                    dict.fromkeys(
                        [
                            *(str(item) for item in report.get("errors") or []),
                            *(
                                str(item.get("message") or item.get("code"))
                                for item in reference_errors
                            ),
                        ]
                    )
                )
                report["required_reference_errors"] = reference_errors
            report["proposal"] = validated_proposal
            return report

        context = {
            "command": command,
            "vendor_hint": params.get("vendor"),
            "model_hint": params.get("model"),
            "field_inventory": field_inventory,
            "field_profiles": (
                dict(params.get("field_profiles") or {})
                or profile_fields(
                    records,
                    field_inventory=field_inventory,
                )
            ),
            "records": records,
            "snapshot_group_count": len(record_groups),
            "contract_seed": proposal_payload if isinstance(proposal_payload, dict) else None,
            "contract_constraints": {
                key: proposal_payload.get(key)
                for key in (
                    "category",
                    "entity_type",
                    "primary_keys",
                    "required_fields",
                    "optional_fields",
                    "validators",
                    "allow_empty",
                    "empty_patterns",
                    "ignore_fields",
                )
                if isinstance(proposal_payload, dict) and proposal_payload.get(key) is not None
            },
            "governed_semantic_hints": governed_hints,
            "observation_targets": observation_targets,
            "strict_observation_targets": strict_observation_targets,
            "canonical_identity_constraint": canonical_identity_constraint,
            "policy_intent": params.get("policy_intent"),
            "event_intent": params.get("event_intent"),
        }
        agent = RuleGenerationAgent(
            DEFAULT_RULE_GENERATION_PROVIDERS.create(generation_provider),
            max_attempts=int(params.get("max_agent_attempts") or 3),
        )
        generated = agent.run(
            stage="observation_contract",
            context=context,
            validator=validate_candidate,
        )
        validation = dict(generated.get("validation") or {})
        result = {
            "success": generated.get("success") is True,
            "mode": "llm_agent_loop",
            "proposal": validation.pop("proposal", None) or generated.get("candidate"),
            "validation": validation,
            "agent_loop": generated.get("agent_loop"),
            "generation_provider": generation_provider,
        }
        return result

    if mode == "infer":
        inferred = proposal or infer_contract(
            command=command,
            samples_text=samples_text,
            use_manual_profile=True,
            check_item=params.get("check_item_id") or params.get("check_item"),
            vendor=params.get("vendor"),
            model=params.get("model"),
        )
        result = {
            "success": True,
            "mode": mode,
            "proposal": inferred.to_dict(),
            "validation": None,
        }
        if params.get("agent_loop"):
            result["agent_loop"] = _agent_loop(mode=mode, result=result, command=command)
        return result
    if mode == "validate":
        if proposal is None:
            raise ValueError("proposal is required in validate mode")
        if params.get("record_groups"):
            validation = _validate_contract_by_group(
                command=command,
                groups=record_groups,
                candidate=proposal.to_dict(),
                field_inventory=sorted({str(field) for record in records for field in record}),
                require_policy_targets=bool(params.get("require_policy_targets")),
                allow_semantic_overrides=bool(params.get("allow_semantic_overrides")),
            )
            return {
                "success": validation.get("success") is True,
                "mode": mode,
                "proposal": validation.pop("proposal", None) or proposal.to_dict(),
                "validation": validation,
            }
        report = infer_and_validate_contract(
            command=command,
            records=records,
            samples_text=samples_text,
            proposal=proposal,
        )
        result = {"mode": mode, **report}
        if params.get("agent_loop"):
            result["agent_loop"] = _agent_loop(mode=mode, result=result, command=command)
        return result
    if mode == "infer_and_validate":
        if params.get("record_groups"):
            grouped_proposal = proposal or infer_contract(
                command=command,
                samples_text=samples_text,
                use_manual_profile=True,
                check_item=params.get("check_item_id") or params.get("check_item"),
                vendor=params.get("vendor"),
                model=params.get("model"),
            )
            validation = _validate_contract_by_group(
                command=command,
                groups=record_groups,
                candidate=grouped_proposal.to_dict(),
                field_inventory=sorted({str(field) for record in records for field in record}),
            )
            return {
                "success": validation.get("success") is True,
                "mode": mode,
                "proposal": validation.pop("proposal", None) or grouped_proposal.to_dict(),
                "validation": validation,
            }
        report = infer_and_validate_contract(
            command=command,
            records=records,
            samples_text=samples_text,
            proposal=proposal,
        )
        result = {"mode": mode, **report}
        if params.get("agent_loop"):
            result["agent_loop"] = _agent_loop(mode=mode, result=result, command=command)
        return result
    raise ValueError("mode must be infer, validate, or infer_and_validate")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and validate ObservationContract proposals"
    )
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    result = run_contract_generation(params)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
