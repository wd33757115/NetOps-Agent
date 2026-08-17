"""Deterministic, read-only planning for CheckItemRuntimeBundle assembly."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlencode

from src.core.check_items.assessment_policy import (
    normalize_assessment_policy_contract,
)
from src.core.check_items.canonical import load_canonical_catalog
from src.core.check_items.catalog import (
    CollectionCommand,
    primary_collection_commands,
)
from src.core.check_items.presentation import check_item_display_name
from src.core.observation.semantic_projection import (
    SemanticProjectionError,
    dimension_field_references,
    normalize_dimensions,
    transform_field_references,
)
from src.core.parser.field_contract import build_parser_field_contract
from src.core.patrol.event_constraint_governance import (
    scope_event_constraints_to_policy,
)
from src.infrastructure.db.models import (
    ChangePolicyVersionRecord,
    CheckItemCatalogChangeRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    EntityIdentityPolicyVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    ObservationContractRecord,
    ParserTemplate,
    TextComparisonProfileRecord,
    TextComparisonProfileVersionRecord,
)


def canonical_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def normalize_command(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def govern_evaluation_scope(value: Any, *, event_class: str = "fault") -> str:
    """Resolve event governance lazily to avoid the event-rules import cycle."""

    from src.core.patrol.event_rules import (
        govern_evaluation_scope as _govern_evaluation_scope,
    )

    return _govern_evaluation_scope(value, event_class=event_class)


def contract_observation_types(contract: ObservationContractRecord) -> set[str]:
    values: set[str] = set()

    def collect(semantics: Any) -> None:
        for item in dict(semantics or {}).values():
            if not isinstance(item, dict):
                continue
            observation_type = str(item.get("observation_type") or "").strip()
            if observation_type:
                values.add(observation_type)

    collect(contract.state_semantics_json)
    for record_set in contract.record_sets_json or []:
        if isinstance(record_set, dict):
            collect(record_set.get("state_semantics"))
    return values


def adapter_observation_bindings(
    implementation: CollectionImplementationVersionRecord,
    *,
    check_item_id: str,
    canonical_rows: Iterable[Mapping[str, Any]],
    parser_template_id: str,
) -> tuple[dict[str, str], ...]:
    """Return explicitly governed direct field mappings for one Parser route."""

    adapter_spec = dict(implementation.adapter_spec_json or {})
    bound_parser_id = str(
        dict(adapter_spec.get("parser") or {}).get("template_id") or ""
    ).strip()
    if not bound_parser_id or bound_parser_id != parser_template_id:
        return ()

    observation_by_field = {
        str(item.get("canonical_field") or "").strip(): str(
            item.get("observation_type") or ""
        ).strip()
        for item in canonical_rows
        if str(item.get("canonical_field") or "").strip()
        and str(item.get("observation_type") or "").strip()
    }
    bindings: list[dict[str, str]] = []
    for output in adapter_spec.get("outputs") or []:
        if not isinstance(output, Mapping):
            continue
        if str(output.get("check_item_id") or "").strip() != check_item_id:
            continue
        source_record_set = str(output.get("source_record_set") or "").strip()
        if not source_record_set:
            continue
        for target_field, raw_binding in dict(output.get("fields") or {}).items():
            observation_type = observation_by_field.get(str(target_field).strip())
            binding = dict(raw_binding) if isinstance(raw_binding, Mapping) else {}
            source_field = str(binding.get("source") or "").strip()
            if observation_type and source_field:
                bindings.append(
                    {
                        "observation_type": observation_type,
                        "source_record_set": source_record_set,
                        "source_field": source_field,
                    }
                )
    return tuple(bindings)


def contract_identity_bindings(
    contract: ObservationContractRecord,
) -> tuple[dict[str, Any], ...]:
    """Return canonical identity declarations at each independent record grain."""

    def observation_types(semantics: Any) -> set[str]:
        return {
            str(item.get("observation_type") or "").strip()
            for item in dict(semantics or {}).values()
            if isinstance(item, dict)
            and str(item.get("observation_type") or "").strip()
        }

    record_sets = [
        dict(item)
        for item in contract.record_sets_json or []
        if isinstance(item, dict)
    ]
    if record_sets:
        return tuple(
            {
                "name": str(item.get("name") or ""),
                "source_entity_type": str(
                    item.get("entity_type") or contract.entity_type or "record"
                ),
                "source_primary_keys": [
                    str(key) for key in item.get("primary_keys") or []
                ],
                "canonical_entity_type": str(
                    item.get("canonical_entity_type")
                    or item.get("entity_type")
                    or contract.canonical_entity_type
                    or contract.entity_type
                    or "record"
                ),
                "identity_fields": [
                    str(key)
                    for key in (
                        item.get("entity_identity_fields")
                        or item.get("primary_keys")
                        or []
                    )
                ],
                "entity_dimensions": dict(
                    item.get("entity_dimensions") or {}
                ),
                "observation_types": observation_types(
                    item.get("state_semantics")
                ),
                "declared_fields": {
                    str(field)
                    for key in (
                        "primary_keys",
                        "required_fields",
                        "optional_fields",
                    )
                    for field in item.get(key) or []
                },
            }
            for item in record_sets
        )
    return (
        {
            "name": "default",
            "source_entity_type": str(contract.entity_type or "record"),
            "source_primary_keys": [
                str(key) for key in contract.primary_keys_json or []
            ],
            "canonical_entity_type": str(
                contract.canonical_entity_type
                or contract.entity_type
                or "record"
            ),
            "identity_fields": [
                str(key)
                for key in (
                    contract.entity_identity_fields_json
                    or contract.primary_keys_json
                    or []
                )
            ],
            "entity_dimensions": dict(contract.entity_dimensions_json or {}),
            "observation_types": observation_types(
                contract.state_semantics_json
            ),
            "declared_fields": {
                str(field)
                for values in (
                    contract.primary_keys_json,
                    contract.required_fields_json,
                    contract.optional_fields_json,
                )
                for field in values or []
            },
        },
    )


def contract_identity_blockers(
    contract: ObservationContractRecord,
    *,
    expected_entity_type: str,
    expected_identity_fields: tuple[str, ...],
    expected_observation_types: set[str],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    relevant = [
        item
        for item in contract_identity_bindings(contract)
        if item["observation_types"] & expected_observation_types
    ]
    for item in relevant:
        if item["canonical_entity_type"] != expected_entity_type:
            blockers.append(
                {
                    "code": "canonical_entity_type_mismatch",
                    "message": (
                        "Contract 的规范实体类型与检查项目录不一致。"
                    ),
                    "record_set": item["name"],
                    "expected": expected_entity_type,
                    "actual": item["canonical_entity_type"],
                }
            )
        actual_identity_fields = tuple(item["identity_fields"])
        canonical_prefix = actual_identity_fields[: len(expected_identity_fields)]
        source_identity_extensions = actual_identity_fields[
            len(expected_identity_fields) :
        ]
        identity_matches = (
            canonical_prefix == expected_identity_fields
            and set(source_identity_extensions).issubset(
                set(item["source_primary_keys"])
            )
        )
        if not identity_matches:
            blockers.append(
                {
                    "code": "canonical_identity_dimensions_mismatch",
                    "message": (
                        "Contract 的规范实体身份维度与检查项目录不一致。"
                    ),
                    "record_set": item["name"],
                    "expected": list(expected_identity_fields),
                    "actual": list(actual_identity_fields),
                }
            )
        dimensions = item["entity_dimensions"]
        if dimensions:
            try:
                normalized = normalize_dimensions(dimensions)
            except SemanticProjectionError as exc:
                blockers.append(
                    {
                        "code": "canonical_identity_mapping_invalid",
                        "message": f"Contract 的规范实体映射无效：{exc}",
                        "record_set": item["name"],
                    }
                )
                continue
            if set(normalized) != set(item["identity_fields"]):
                blockers.append(
                    {
                        "code": "canonical_identity_mapping_incomplete",
                        "message": (
                            "Contract 的规范实体映射未完整覆盖身份维度。"
                        ),
                        "record_set": item["name"],
                    }
                )
            unknown_fields = sorted(
                set(dimension_field_references(normalized))
                - set(item["declared_fields"])
            )
            if unknown_fields:
                blockers.append(
                    {
                        "code": "canonical_identity_source_field_missing",
                        "message": (
                            "Contract 的规范实体映射引用了 Parser 未声明字段。"
                        ),
                        "record_set": item["name"],
                        "fields": unknown_fields,
                    }
                )
        elif (
            item["canonical_entity_type"] != item["source_entity_type"]
            or tuple(item["source_primary_keys"]) != expected_identity_fields
        ):
            blockers.append(
                {
                    "code": "canonical_identity_mapping_missing",
                    "message": (
                        "源记录身份不同于检查项规范身份，但 Contract 未声明映射。"
                    ),
                    "record_set": item["name"],
                }
            )
    return blockers


def _contract_source_record_sets(
    contract: ObservationContractRecord,
    *,
    fallback_name: str = "default",
) -> tuple[dict[str, Any], ...]:
    record_sets = tuple(
        dict(item)
        for item in contract.record_sets_json or []
        if isinstance(item, dict)
    )
    if record_sets:
        return record_sets
    return (
        {
            "name": fallback_name,
            "primary_keys": list(contract.primary_keys_json or []),
            "required_fields": list(contract.required_fields_json or []),
            "optional_fields": list(contract.optional_fields_json or []),
            "validators": dict(contract.validators_json or {}),
            "ignore_fields": list(contract.ignore_fields_json or []),
            "entity_dimensions": dict(contract.entity_dimensions_json or {}),
            "state_semantics": dict(contract.state_semantics_json or {}),
        },
    )


def adapter_contract_observation_types(
    contract: ObservationContractRecord,
    *,
    parser_field_contract: Mapping[str, Any],
    bindings: Iterable[Mapping[str, str]],
) -> tuple[set[str], set[str]]:
    """Prove mapped semantics from explicit adapter, Parser, and Contract fields."""

    parser_sets = {
        str(name): {str(field) for field in fields or []}
        for name, fields in dict(
            parser_field_contract.get("template_record_sets") or {}
        ).items()
    }
    produced_types: set[str] = set()
    source_record_sets: set[str] = set()
    for binding in bindings:
        source_record_set = str(binding.get("source_record_set") or "").strip()
        source_field = str(binding.get("source_field") or "").strip()
        observation_type = str(binding.get("observation_type") or "").strip()
        if source_field not in parser_sets.get(source_record_set, set()):
            continue
        contract_sets = {
            str(item.get("name") or ""): item
            for item in _contract_source_record_sets(
                contract,
                fallback_name=source_record_set,
            )
        }
        contract_set = contract_sets.get(source_record_set)
        if contract_set is None:
            continue
        contract_fields = {
            str(field)
            for key in ("primary_keys", "required_fields", "optional_fields")
            for field in contract_set.get(key) or []
        }
        contract_fields.update(
            str(field) for field in dict(contract_set.get("validators") or {})
        )
        if source_field not in contract_fields:
            continue
        produced_types.add(observation_type)
        source_record_sets.add(source_record_set)
    return produced_types, source_record_sets


def contract_source_field_blockers(
    contract: ObservationContractRecord,
    *,
    parser_field_contract: Mapping[str, Any],
    fallback_record_set: str = "default",
) -> list[dict[str, Any]]:
    """Prove every Contract source reference exists in the frozen Parser schema."""

    parser_sets = {
        str(name): {str(field) for field in fields or []}
        for name, fields in dict(
            parser_field_contract.get("template_record_sets") or {}
        ).items()
    }
    blockers: list[dict[str, Any]] = []
    contract_sets = {
        str(item.get("name") or "default"): item
        for item in _contract_source_record_sets(
            contract,
            fallback_name=fallback_record_set,
        )
    }

    def missing_fields(
        *,
        record_set: str,
        fields: Iterable[Any],
        reference_kind: str,
    ) -> None:
        if record_set not in parser_sets:
            blockers.append(
                {
                    "code": "observation_contract_record_set_missing",
                    "message": "Contract 引用了 Parser 不存在的 record set。",
                    "record_set": record_set,
                    "reference_kind": reference_kind,
                }
            )
            return
        missing = sorted(
            {
                str(field)
                for field in fields
                if str(field).strip()
            }
            - parser_sets[record_set]
        )
        if missing:
            blockers.append(
                {
                    "code": "observation_contract_source_field_missing",
                    "message": "Contract 引用了 Parser 未输出的源字段。",
                    "record_set": record_set,
                    "reference_kind": reference_kind,
                    "fields": missing,
                }
            )

    for name, item in contract_sets.items():
        semantics = dict(item.get("state_semantics") or {})
        direct_fields = {
            str(field)
            for key in (
                "primary_keys",
                "required_fields",
                "optional_fields",
                "ignore_fields",
            )
            for field in item.get(key) or []
            if str(field).strip()
        }
        direct_fields.update(
            str(field) for field in dict(item.get("validators") or {})
        )
        direct_fields.update(str(field) for field in semantics)
        direct_fields.update(
            dimension_field_references(item.get("entity_dimensions") or {})
        )
        for raw_semantics in semantics.values():
            if not isinstance(raw_semantics, Mapping):
                continue
            direct_fields.update(
                transform_field_references(raw_semantics.get("transform"))
            )
            direct_fields.update(
                str(field)
                for field in raw_semantics.get("context_fields") or []
                if str(field).strip()
            )
            for reference in dict(
                raw_semantics.get("threshold_references") or {}
            ).values():
                if not isinstance(reference, Mapping):
                    continue
                target_set = str(
                    reference.get("record_set_name") or name
                ).strip()
                field_name = str(
                    reference.get("field_name") or ""
                ).strip()
                missing_fields(
                    record_set=target_set,
                    fields=[field_name],
                    reference_kind="threshold_reference",
                )
                join_keys = dict(reference.get("join_keys") or {})
                missing_fields(
                    record_set=name,
                    fields=join_keys.keys(),
                    reference_kind="threshold_join_source",
                )
                missing_fields(
                    record_set=target_set,
                    fields=join_keys.values(),
                    reference_kind="threshold_join_target",
                )
        missing_fields(
            record_set=name,
            fields=direct_fields,
            reference_kind="contract_schema",
        )

    for relation in contract.record_set_relations_json or []:
        if not isinstance(relation, Mapping):
            continue
        source_set = str(
            relation.get("source_record_set") or ""
        ).strip()
        target_set = str(
            relation.get("target_record_set") or ""
        ).strip()
        join_keys = dict(relation.get("join_keys") or {})
        missing_fields(
            record_set=source_set,
            fields=join_keys.keys(),
            reference_kind="relation_join_source",
        )
        missing_fields(
            record_set=target_set,
            fields=join_keys.values(),
            reference_kind="relation_join_target",
        )
        threshold_bindings = dict(
            relation.get("threshold_bindings") or {}
        )
        missing_fields(
            record_set=source_set,
            fields=threshold_bindings.keys(),
            reference_kind="relation_measurement_source",
        )
        missing_fields(
            record_set=target_set,
            fields=[
                field
                for bindings in threshold_bindings.values()
                if isinstance(bindings, Mapping)
                for field in bindings.values()
            ],
            reference_kind="relation_threshold_target",
        )

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in blockers:
        signature = canonical_digest(item)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(item)
    return unique


def event_rule_observation_types(rule: EventRuleVersionRecord) -> tuple[str, ...]:
    raw = dict(rule.condition_json or {}).get("observation_types")
    if not isinstance(raw, list):
        return ()
    return tuple(
        sorted(
            {
                str(item).strip()
                for item in raw
                if isinstance(item, str) and str(item).strip()
            }
        )
    )


_ASSET_REQUIREMENT_ORDER = (
    "parser_template",
    "observation_contract",
    "current_state_rule",
    "change_policy",
    "event_rule",
    "comparison_profile",
    "identity_policy",
)


def _event_constraints(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    constraints = row.get("event_constraints")
    if not isinstance(constraints, Mapping):
        return {
            "event_types": (),
            "event_classes": {},
            "event_severities": {},
            "recovery_pairs": (),
            "role_event_types": {},
            "evidence_bound_roles": False,
            "raw": {},
        }
    role_event_types = {
        str(role).strip(): str(event_type).strip()
        for role, event_type in dict(
            constraints.get("role_event_types") or {}
        ).items()
        if str(role).strip() and str(event_type).strip()
    }
    declared_required = constraints.get("required_event_types")
    if isinstance(declared_required, (list, tuple, set, frozenset)):
        required_event_types = {
            str(item).strip()
            for item in declared_required
            if str(item).strip()
        }
    else:
        required_event_types = set(role_event_types.values())
    event_classes = {
        str(event_type).strip(): str(event_class).strip()
        for event_type, event_class in dict(
            constraints.get("required_event_classes") or {}
        ).items()
        if str(event_type).strip() and str(event_class).strip()
    }
    event_severities = {
        str(event_type).strip(): str(severity).strip().casefold()
        for event_type, severity in dict(
            constraints.get("required_severities") or {}
        ).items()
        if str(event_type).strip() and str(severity).strip()
    }
    recovery_pairs = tuple(
        sorted(
            (
                {
                    "fault": str(item.get("fault") or "").strip(),
                    "recovery": str(item.get("recovery") or "").strip(),
                }
                for item in constraints.get("required_recovery_pairs") or []
                if isinstance(item, Mapping)
                and str(item.get("fault") or "").strip()
                and str(item.get("recovery") or "").strip()
            ),
            key=lambda item: (item["fault"], item["recovery"]),
        )
    )
    return {
        "event_types": tuple(sorted(required_event_types)),
        "event_classes": event_classes,
        "event_severities": event_severities,
        "recovery_pairs": recovery_pairs,
        "role_event_types": role_event_types,
        "evidence_bound_roles": constraints.get("evidence_bound_roles")
        is True,
        "raw": dict(constraints),
    }


def derive_asset_requirement_profile(
    *,
    result_mode: str,
    assessment_mode: str,
    canonical_rows: Iterable[Mapping[str, Any]],
    canonical_catalog: Any,
) -> dict[str, Any]:
    """Derive the immutable asset contract for one check-item version."""

    rows = [
        dict(item)
        for item in canonical_rows
        if isinstance(item, Mapping)
        and str(item.get("observation_type") or "").strip()
    ]
    required_types = {
        str(item["observation_type"]).strip()
        for item in rows
        if item.get("required", True) is not False
    }
    optional_types = {
        str(item["observation_type"]).strip()
        for item in rows
        if item.get("required", True) is False
    }

    def types_with_role(values: set[str], role: str) -> set[str]:
        return {
            observation_type
            for observation_type in values
            if (
                (
                    definition := canonical_catalog.get(
                        observation_type
                    )
                )
                is not None
                and role in definition.assessment_roles
            )
        }

    structured = result_mode == "structured"
    comparison = result_mode in {"normalized_diff", "hash_only"}
    evaluates_state = assessment_mode == "health"
    evaluates_change = assessment_mode in {"health", "change"}

    current_required = (
        types_with_role(required_types, "current_state")
        if structured and evaluates_state
        else set()
    )
    current_conditional = (
        types_with_role(optional_types, "current_state")
        if structured and evaluates_state
        else set()
    )
    change_required = (
        types_with_role(required_types, "change")
        if structured and evaluates_change
        else set()
    )
    change_conditional = (
        types_with_role(optional_types, "change")
        if structured and evaluates_change
        else set()
    )

    required_events: dict[str, list[str]] = {}
    conditional_events: dict[str, list[str]] = {}
    required_event_classes: dict[str, str] = {}
    required_event_severities: dict[str, str] = {}
    required_recovery_pairs: dict[str, list[dict[str, str]]] = {}
    evidence_bound_event_constraints: dict[str, dict[str, Any]] = {}
    if structured and evaluates_change:
        for row in rows:
            observation_type = str(row["observation_type"]).strip()
            event_constraints = _event_constraints(row)
            event_types = tuple(event_constraints["event_types"])
            if not event_types:
                continue
            if event_constraints["evidence_bound_roles"]:
                evidence_bound_event_constraints[observation_type] = {
                    "constraints": dict(event_constraints["raw"]),
                    "required_observation": row.get("required", True)
                    is not False,
                }
            else:
                target = (
                    conditional_events
                    if row.get("required", True) is False
                    else required_events
                )
                target[observation_type] = list(event_types)
            required_event_classes.update(
                event_constraints["event_classes"]
            )
            required_event_severities.update(
                event_constraints["event_severities"]
            )
            recovery_pairs = list(
                event_constraints["recovery_pairs"]
            )
            if recovery_pairs:
                required_recovery_pairs[observation_type] = (
                    recovery_pairs
                )

    requirements: dict[str, dict[str, Any]] = {
        "parser_template": {
            "required": structured,
            "reason_code": (
                "structured_output_requires_parser"
                if structured
                else "result_mode_does_not_use_parser"
            ),
        },
        "observation_contract": {
            "required": structured,
            "reason_code": (
                "structured_output_requires_canonical_projection"
                if structured
                else "result_mode_does_not_emit_observations"
            ),
            "required_observation_types": sorted(required_types),
            "conditional_observation_types": sorted(optional_types),
        },
        "current_state_rule": {
            "required": bool(current_required),
            "reason_code": (
                "health_assessment_requires_current_state_evaluation"
                if current_required
                else "check_item_does_not_require_current_state_evaluation"
            ),
            "required_observation_types": sorted(current_required),
            "conditional_observation_types": sorted(
                current_conditional
            ),
        },
        "change_policy": {
            "required": bool(change_required),
            "reason_code": (
                "canonical_change_semantics_require_policy"
                if change_required
                else (
                    "text_comparison_profile_owns_change_policy"
                    if comparison
                    else "check_item_does_not_emit_canonical_changes"
                )
            ),
            "required_observation_types": sorted(change_required),
            "conditional_observation_types": sorted(
                change_conditional
            ),
        },
        "event_rule": {
            "required": bool(
                required_events or evidence_bound_event_constraints
            ),
            "reason_code": (
                "catalog_declares_required_event_outcomes"
                if required_events or evidence_bound_event_constraints
                else "catalog_declares_no_required_event_outcomes"
            ),
            "required_event_types_by_observation": dict(
                sorted(required_events.items())
            ),
            "conditional_event_types_by_observation": dict(
                sorted(conditional_events.items())
            ),
            "required_event_classes": dict(
                sorted(required_event_classes.items())
            ),
            "required_event_severities": dict(
                sorted(required_event_severities.items())
            ),
            "required_recovery_pairs_by_observation": dict(
                sorted(required_recovery_pairs.items())
            ),
            "evidence_bound_event_constraints_by_observation": dict(
                sorted(evidence_bound_event_constraints.items())
            ),
        },
        "comparison_profile": {
            "required": comparison,
            "reason_code": (
                "text_result_requires_frozen_comparison_policy"
                if comparison
                else "result_mode_does_not_use_text_comparison"
            ),
        },
        "identity_policy": {
            "required": True,
            "reason_code": "all_evidence_requires_stable_entity_identity",
        },
    }
    required_asset_types = [
        asset_type
        for asset_type in _ASSET_REQUIREMENT_ORDER
        if requirements[asset_type]["required"]
    ]
    not_required = {
        asset_type: str(requirements[asset_type]["reason_code"])
        for asset_type in _ASSET_REQUIREMENT_ORDER
        if not requirements[asset_type]["required"]
    }
    runtime_facts = [
        "evidence_qualification",
        "coverage_decision",
        "check_assessment",
    ]
    if structured:
        runtime_facts.append("state_observation")
    elif comparison:
        runtime_facts.extend(
            ["comparison_context", "network_change"]
        )
    else:
        runtime_facts.append("qualified_evidence")

    return {
        "schema_version": "asset-requirement-profile/v1",
        "result_mode": result_mode,
        "assessment_mode": assessment_mode,
        "requirements": requirements,
        "required_asset_types": required_asset_types,
        "not_required": not_required,
        "required_runtime_facts": runtime_facts,
    }


def normalize_rule_bindings(
    values: Mapping[str, str | Iterable[str]],
) -> dict[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {}
    for version_id, raw_types in values.items():
        if isinstance(raw_types, str):
            candidates = (raw_types,)
        else:
            candidates = tuple(raw_types)
        observation_types = tuple(
            sorted({str(item).strip() for item in candidates if str(item).strip()})
        )
        if str(version_id).strip() and observation_types:
            normalized[str(version_id)] = observation_types
    return normalized


def _string_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = tuple(value)
    else:
        values = (value,)
    return tuple(
        sorted(
            {
                str(item).strip().casefold()
                for item in values
                if str(item).strip()
            }
        )
    )


def _selector_values(selector: Mapping[str, Any], singular: str, plural: str) -> tuple[str, ...]:
    values = list(_string_values(selector.get(plural)))
    values.extend(_string_values(selector.get(singular)))
    return tuple(sorted(set(values)))


def _exact_models_from_patterns(patterns: Iterable[str]) -> set[str]:
    exact_models: set[str] = set()
    for raw_pattern in patterns:
        pattern = str(raw_pattern or "").strip()
        if not (pattern.startswith("^") and pattern.endswith("$")):
            continue
        body = pattern[1:-1]
        literal: list[str] = []
        index = 0
        is_exact = True
        while index < len(body):
            character = body[index]
            if character == "\\":
                index += 1
                if index >= len(body) or body[index].isalnum():
                    # Character classes such as \d and dangling escapes describe
                    # a set of models, not one exact model identity.
                    is_exact = False
                    break
                literal.append(body[index])
            elif character in ".^$*+?{}[]()|":
                is_exact = False
                break
            else:
                literal.append(character)
            index += 1
        value = "".join(literal)
        if (
            is_exact
            and value
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", value)
        ):
            exact_models.add(value.casefold())
    return exact_models


def parser_compatibility(
    implementation: CollectionImplementationVersionRecord,
    parser: ParserTemplate,
) -> dict[str, Any]:
    """Prove that the implementation target set is contained by Parser evidence."""

    validation = dict(parser.validation_json or {})
    raw_manifest = validation.get("compatibility")
    manifest = dict(raw_manifest) if isinstance(raw_manifest, dict) else {}
    selector = dict(implementation.match_json or {})
    blockers: list[dict[str, Any]] = []

    def block(code: str, message: str) -> None:
        blockers.append({"code": code, "message": message})

    if not manifest:
        block(
            "parser_compatibility_manifest_missing",
            "Parser 缺少已审批的兼容范围证据。",
        )

    scope = str(manifest.get("scope") or "").strip().casefold()
    if scope not in {"model", "family", "vendor"}:
        block(
            "parser_compatibility_scope_invalid",
            "Parser 兼容范围必须明确为 model、family 或 vendor。",
        )

    implementation_vendors = {
        value.casefold()
        for value in _selector_values(selector, "vendor", "vendors")
    }
    parser_vendor = str(manifest.get("vendor") or parser.vendor or "").strip().casefold()
    if not implementation_vendors:
        block(
            "implementation_vendor_missing",
            "采集实现未声明厂商，无法证明 Parser 兼容范围。",
        )
    elif implementation_vendors != {parser_vendor}:
        block(
            "parser_vendor_mismatch",
            f"采集实现厂商 {sorted(implementation_vendors)} 与 Parser 厂商 "
            f"{manifest.get('vendor') or parser.vendor} 不一致。",
        )

    primary_commands = primary_collection_commands(
        (
            CollectionCommand(
                command=str(item.get("command") or ""),
                priority=int(item.get("priority") or 0),
            )
            for item in implementation.commands_json or []
            if isinstance(item, dict) and normalize_command(item.get("command"))
        ),
        implementation_id=str(implementation.implementation_id),
        version_id=str(implementation.id),
    )
    implementation_commands = {
        normalize_command(item.command) for item in primary_commands
    }
    parser_command = normalize_command(
        manifest.get("canonical_command") or parser.canonical_command
    )
    if parser_command not in implementation_commands:
        block(
            "parser_command_not_declared",
            "Parser 命令不在采集实现声明的命令集合中。",
        )

    implementation_families = set(
        _selector_values(selector, "family", "families")
    )
    implementation_models = set(_selector_values(selector, "model", "models"))
    implementation_model_patterns = set(
        _string_values(selector.get("model_patterns"))
    )
    implementation_exact_models = {
        value.casefold() for value in implementation_models
    } | _exact_models_from_patterns(implementation_model_patterns)
    parser_families = set(
        _selector_values(
            manifest,
            "family",
            "families",
        )
    ) | set(_string_values(parser.family))
    parser_models = set(
        _selector_values(
            manifest,
            "model",
            "models",
        )
    ) | set(_string_values(parser.model))
    parser_model_identities = {value.casefold() for value in parser_models}
    parser_model_patterns = set(_string_values(manifest.get("model_patterns")))

    if scope == "family":
        if implementation_families:
            if not implementation_families.issubset(parser_families):
                block(
                    "implementation_scope_broader_than_parser",
                    "采集实现覆盖的设备 family 超出 Parser 已验证 family。",
                )
        elif implementation_models and implementation_models.issubset(parser_models):
            pass
        else:
            block(
                "implementation_scope_broader_than_parser",
                "采集实现范围宽于 family 级 Parser 证据，请拆分实现或补充兼容样本。",
            )
    elif scope == "model":
        if implementation_exact_models:
            if not implementation_exact_models.issubset(parser_model_identities):
                block(
                    "implementation_scope_broader_than_parser",
                    "采集实现覆盖的型号超出 Parser 已验证型号。",
                )
        elif (
            implementation_model_patterns
            and implementation_model_patterns == parser_model_patterns
        ):
            pass
        else:
            block(
                "implementation_scope_broader_than_parser",
                "采集实现范围宽于型号级 Parser 证据，请拆分实现或补充兼容样本。",
            )

    implementation_os_patterns = set(
        _string_values(selector.get("os_version_patterns"))
    )
    parser_os_patterns = set(_string_values(manifest.get("os_version_patterns")))
    if (
        implementation_os_patterns
        and not bool(manifest.get("all_os_versions"))
        and implementation_os_patterns != parser_os_patterns
    ):
        block(
            "parser_os_scope_unproven",
            "Parser 证据未覆盖采集实现声明的系统版本范围。",
        )

    implementation_capabilities = set(
        _string_values(selector.get("capabilities"))
    )
    parser_capabilities = set(_string_values(manifest.get("capabilities")))
    if (
        implementation_capabilities
        and not implementation_capabilities.issubset(parser_capabilities)
    ):
        block(
            "parser_capability_scope_unproven",
            "Parser 证据未覆盖采集实现声明的设备能力。",
        )

    normalized_manifest = {
        "manifest_version": manifest.get("manifest_version"),
        "scope": scope or None,
        "vendor": manifest.get("vendor") or parser.vendor,
        "families": sorted(parser_families),
        "models": sorted(parser_models),
        "model_patterns": sorted(parser_model_patterns),
        "canonical_command": parser_command,
        "sample_count": int(
            validation.get("unique_sample_count")
            or validation.get("sample_count")
            or 0
        ),
        "sample_manifest_sha256": validation.get("sample_manifest_sha256"),
        "structure_signatures": sorted(
            str(item)
            for item in manifest.get("structure_signatures") or []
            if str(item)
        ),
    }
    return {
        "compatible": not blockers,
        "blockers": blockers,
        "implementation_selector": selector,
        "parser_manifest": normalized_manifest,
    }


def _issue(
    code: str,
    message: str,
    *,
    remediation: str | None = None,
    **context: Any,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "remediation": remediation,
        "context": {key: value for key, value in context.items() if value is not None},
    }


def _remediation_profile(code: str) -> tuple[str, str, str, str | None]:
    """Map stable assembly diagnostics to one product action."""

    normalized = str(code or "runtime_bundle_invalid").strip().casefold()
    source_required_assets = {
        "observation_contract_missing": "observation_contract",
        "change_policy_missing": "change_policy",
        "current_state_event_rule_missing": "event_rule",
        "required_event_rule_missing": "event_rule",
        "comparison_profile_missing": "comparison_profile",
    }
    if normalized in source_required_assets:
        return (
            "先导入包含该检查项命令的离线原始输出；平台将建立生产单元并生成、验证和审批缺失资产。",
            "import_rule_samples",
            "导入规则样本",
            source_required_assets[normalized],
        )
    if normalized.endswith("_ambiguous"):
        return (
            "在运行包装配页显式选择唯一兼容版本，然后重新执行预检。",
            "review_runtime_selection",
            "选择资产版本",
            None,
        )
    if (
        "parser" in normalized
        or normalized in {
            "command_route_unavailable",
            "no_executable_structured_command",
        }
    ):
        return (
            "在规则资产生产工作台补齐或修复兼容 Parser，并完成验证与发布。",
            "repair_parser_asset",
            "处理 Parser",
            "parser_template",
        )
    if (
        "contract" in normalized
        or "canonical_identity" in normalized
        or "canonical_observation" in normalized
        or "observation_projection" in normalized
        or normalized == "unapproved_observation_type"
    ):
        return (
            "在规则资产生产工作台修订 ObservationContract 的标准语义和实体映射，验证后发布。",
            "repair_observation_contract",
            "处理 Contract",
            "observation_contract",
        )
    if "change_policy" in normalized:
        return (
            "在规则资产生产工作台生成或修订 ChangePolicy，完成回放、复核与发布。",
            "repair_change_policy",
            "处理变化策略",
            "change_policy",
        )
    if "event_rule" in normalized or "current_state" in normalized:
        return (
            "在规则资产生产工作台生成或修订 CurrentStateRule / EventRule，完成回放、复核与发布。",
            "repair_event_rule",
            "处理事件规则",
            "event_rule",
        )
    if "comparison_profile" in normalized:
        return (
            "在规则资产生产工作台补齐或选择 TextComparisonProfile，验证后发布。",
            "repair_comparison_profile",
            "处理比较规则",
            "comparison_profile",
        )
    if "entity_identity_policy" in normalized:
        return (
            "在规则资产生产工作台检查并激活实体身份策略，然后重新装配运行包。",
            "repair_identity_policy",
            "处理身份策略",
            "identity_policy",
        )
    return (
        "在规则资产生产工作台修正检查项或采集实现的版本化配置，然后重新装配运行包。",
        "repair_check_item_configuration",
        "处理检查项配置",
        "check_item",
    )


def _actionable_issues(
    issues: Iterable[Mapping[str, Any]],
    *,
    check_item_id: str | None,
    check_item_version_id: str,
    implementation_version_id: str,
) -> list[dict[str, Any]]:
    """Give every blocker a deterministic explanation and navigation target."""

    result: list[dict[str, Any]] = []
    for raw in issues:
        issue = dict(raw)
        code = str(issue.get("code") or "runtime_bundle_invalid")
        default_remediation, action_code, action_label, asset_type = (
            _remediation_profile(code)
        )
        remediation = str(issue.get("remediation") or default_remediation)
        if action_code == "review_runtime_selection":
            params = {
                "workspace": "runtime",
                "subtab": "coverage-backfill",
                "check_item_version_id": check_item_version_id,
                "implementation_version_id": implementation_version_id,
            }
            if check_item_id:
                params["search"] = check_item_id
        elif action_code == "import_rule_samples":
            params = {
                "workspace": "production",
                "subtab": "source-jobs",
            }
            if check_item_id:
                params["check_item_id"] = check_item_id
        else:
            params = {
                "workspace": "production",
                "subtab": "asset-production-units",
            }
            if check_item_id:
                params["command"] = check_item_id
            if action_code == "repair_parser_asset":
                params["action"] = "parser"
            elif action_code in {
                "repair_observation_contract",
                "repair_change_policy",
                "repair_event_rule",
                "repair_comparison_profile",
            }:
                params["action"] = "rule-generation"
        issue["remediation"] = remediation
        issue["remediation_action"] = {
            "code": action_code,
            "label": action_label,
            "description": remediation,
            "target_url": f"/skills/parser?{urlencode(params)}",
            "asset_type": asset_type,
        }
        result.append(issue)
    return result


class RuntimeBundleAssemblyPlanner:
    """Resolve an immutable asset set without writing control-plane state."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def build(
        self,
        *,
        check_item_version_id: str,
        implementation_version_id: str,
        parser_template_ids: Iterable[str] = (),
        observation_contract_ids: Iterable[str] = (),
        change_policy_version_ids: Iterable[str] = (),
        event_rule_version_ids: Iterable[str] = (),
        comparison_profile_version_id: str | None = None,
    ) -> dict[str, Any]:
        db = self.db
        canonical_catalog = load_canonical_catalog()
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        check_version = db.get(CheckItemVersionRecord, check_item_version_id)
        implementation_version = db.get(
            CollectionImplementationVersionRecord,
            implementation_version_id,
        )
        if check_version is None:
            blockers.append(
                _issue(
                    "check_item_version_missing",
                    "检查项版本不存在。",
                    check_item_version_id=check_item_version_id,
                )
            )
        if implementation_version is None:
            blockers.append(
                _issue(
                    "implementation_version_missing",
                    "采集实现版本不存在。",
                    implementation_version_id=implementation_version_id,
                )
            )
        if check_version is None or implementation_version is None:
            return self._terminal_plan(
                check_item_version_id=check_item_version_id,
                implementation_version_id=implementation_version_id,
                blockers=blockers,
                warnings=warnings,
            )

        check_item = db.get(CheckItemRecord, check_version.check_item_id)
        implementation = db.get(
            CollectionImplementationRecord,
            implementation_version.implementation_id,
        )
        link = (
            db.query(CheckItemImplementationLinkRecord)
            .filter(
                CheckItemImplementationLinkRecord.check_item_version_id
                == check_version.id,
                CheckItemImplementationLinkRecord.implementation_version_id
                == implementation_version.id,
            )
            .first()
        )
        if check_item is None or implementation is None:
            blockers.append(
                _issue(
                    "catalog_root_missing",
                    "检查项或采集实现根记录不存在。",
                )
            )
        if link is None:
            blockers.append(
                _issue(
                    "implementation_not_linked",
                    "采集实现未关联到当前检查项版本。",
                )
            )
        if check_version.status != "active":
            blockers.append(
                _issue("check_item_not_active", "检查项版本不是 Active。")
            )
        governed_candidate_change = None
        if implementation_version.status == "draft":
            governed_candidate_change = (
                db.query(CheckItemCatalogChangeRecord)
                .filter(
                    CheckItemCatalogChangeRecord.version_id
                    == implementation_version.id,
                    CheckItemCatalogChangeRecord.asset_kind
                    == "collection_implementation",
                    CheckItemCatalogChangeRecord.status
                    == "pending_approval",
                )
                .one_or_none()
            )
        if (
            implementation_version.status != "active"
            and governed_candidate_change is None
        ):
            blockers.append(
                _issue("implementation_not_active", "采集实现版本不是 Active。")
            )

        canonical_rows = [
            dict(item)
            for item in dict(check_version.canonical_contract_json or {}).get(
                "observations"
            )
            or []
            if isinstance(item, dict)
        ]
        expected_types = {
            str(item.get("observation_type") or "").strip()
            for item in canonical_rows
            if str(item.get("observation_type") or "").strip()
        }
        required_types = {
            str(item.get("observation_type") or "").strip()
            for item in canonical_rows
            if str(item.get("observation_type") or "").strip()
            and item.get("required", True) is not False
        }
        canonical_contract = dict(
            check_version.canonical_contract_json or {}
        )
        expected_entity_type = str(
            canonical_contract.get("entity_type") or ""
        ).strip()
        expected_identity_fields = tuple(
            str(item)
            for item in canonical_contract.get("identity_dimensions") or []
        )
        approved_types = {item.id for item in canonical_catalog.observations}
        result_mode = str(check_version.result_mode)
        assessment_mode = str(check_version.assessment_mode)
        freshness_policy = dict(check_version.freshness_policy_json or {})
        empty_result_policy = dict(
            check_version.empty_result_policy_json or {}
        )
        coverage_policy = dict(check_version.coverage_policy_json or {})
        output_quality_policy = dict(
            implementation_version.output_quality_policy_json or {}
        )
        asset_requirement_profile = derive_asset_requirement_profile(
            result_mode=result_mode,
            assessment_mode=assessment_mode,
            canonical_rows=canonical_rows,
            canonical_catalog=canonical_catalog,
        )
        asset_requirement_profile_digest = canonical_digest(
            asset_requirement_profile
        )
        asset_requirements = dict(
            asset_requirement_profile["requirements"]
        )
        if assessment_mode == "health":
            for code, policy, message in (
                (
                    "freshness_policy_missing",
                    freshness_policy,
                    "健康检查项缺少版本化 freshness policy。",
                ),
                (
                    "empty_result_policy_missing",
                    empty_result_policy,
                    "健康检查项缺少版本化 empty-result policy。",
                ),
                (
                    "coverage_policy_missing",
                    coverage_policy,
                    "健康检查项缺少版本化 entity coverage policy。",
                ),
                (
                    "output_quality_policy_missing",
                    output_quality_policy,
                    "采集实现缺少版本化 OutputQualityProfile。",
                ),
            ):
                if not policy:
                    blockers.append(_issue(code, message))

        primary_commands = primary_collection_commands(
            (
                CollectionCommand(
                    command=str(item.get("command") or "").strip(),
                    priority=int(item.get("priority") or 0),
                )
                for item in implementation_version.commands_json or []
                if isinstance(item, dict) and normalize_command(item.get("command"))
            ),
            implementation_id=str(implementation_version.implementation_id),
            version_id=str(implementation_version.id),
        )
        commands = [
            {
                "command": item.command,
                "canonical_command": normalize_command(item.command),
                "priority": item.priority,
            }
            for item in primary_commands
        ]
        command_names = {item["canonical_command"] for item in commands}

        requested_parser_ids = {
            str(item) for item in parser_template_ids if str(item).strip()
        }
        requested_contract_ids = {
            str(item) for item in observation_contract_ids if str(item).strip()
        }
        requested_policy_ids = {
            str(item) for item in change_policy_version_ids if str(item).strip()
        }
        requested_rule_ids = {
            str(item) for item in event_rule_version_ids if str(item).strip()
        }

        parser_rows = (
            db.query(ParserTemplate)
            .filter(ParserTemplate.status == "active")
            .order_by(
                ParserTemplate.canonical_command,
                ParserTemplate.vendor,
                ParserTemplate.profile,
                ParserTemplate.id,
            )
            .all()
            if result_mode == "structured"
            else []
        )
        parser_rows = [
            item
            for item in parser_rows
            if normalize_command(item.canonical_command) in command_names
        ]
        parser_by_id = {str(item.id): item for item in parser_rows}
        unknown_requested_parsers = sorted(requested_parser_ids - set(parser_by_id))
        if unknown_requested_parsers:
            blockers.append(
                _issue(
                    "selected_parser_unavailable",
                    "所选 Parser 不存在、不是 Active，或命令不属于该采集实现。",
                    parser_template_ids=unknown_requested_parsers,
                )
            )

        contract_rows = (
            db.query(ObservationContractRecord)
            .filter(ObservationContractRecord.status == "active")
            .order_by(
                ObservationContractRecord.source_template_id,
                ObservationContractRecord.schema_id,
                ObservationContractRecord.version,
                ObservationContractRecord.id,
            )
            .all()
            if result_mode == "structured"
            else []
        )
        contract_by_id = {str(item.id): item for item in contract_rows}
        unknown_requested_contracts = sorted(
            requested_contract_ids - set(contract_by_id)
        )
        if unknown_requested_contracts:
            blockers.append(
                _issue(
                    "selected_contract_unavailable",
                    "所选 ObservationContract 不存在或不是 Active。",
                    observation_contract_ids=unknown_requested_contracts,
                )
            )

        contracts_by_parser: dict[str, list[ObservationContractRecord]] = defaultdict(list)
        for contract in contract_rows:
            contracts_by_parser[str(contract.source_template_id or "")].append(contract)

        parser_candidates: list[dict[str, Any]] = []
        route_candidates_by_command: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for parser in parser_rows:
            parser_id = str(parser.id)
            compatibility = parser_compatibility(implementation_version, parser)
            parser_field_contract = build_parser_field_contract(
                validation=dict(parser.validation_json or {}),
                template_text=parser.template_text,
            )
            adapter_bindings = adapter_observation_bindings(
                implementation_version,
                check_item_id=str(check_item.id),
                canonical_rows=canonical_rows,
                parser_template_id=parser_id,
            )
            available_contracts = [
                item
                for item in contracts_by_parser.get(parser_id, [])
                if not requested_contract_ids or str(item.id) in requested_contract_ids
            ]
            contract_types: dict[str, set[str]] = {}
            contract_adapter_sets: dict[str, set[str]] = {}
            for item in available_contracts:
                mapped_types, mapped_sets = adapter_contract_observation_types(
                    item,
                    parser_field_contract=parser_field_contract,
                    bindings=adapter_bindings,
                )
                contract_types[str(item.id)] = (
                    contract_observation_types(item) | mapped_types
                )
                contract_adapter_sets[str(item.id)] = mapped_sets
            relevant_contracts = [
                item
                for item in available_contracts
                if contract_types[str(item.id)] & expected_types
            ]
            produced_counts = Counter(
                observation_type
                for contract in relevant_contracts
                for observation_type in contract_types[str(contract.id)]
                if observation_type in expected_types
            )
            duplicate_types = sorted(
                key for key, count in produced_counts.items() if count > 1
            )
            produced_types = {
                observation_type
                for contract in relevant_contracts
                for observation_type in contract_types[str(contract.id)]
            }
            missing_types = sorted(required_types - produced_types)
            unapproved_types = sorted(produced_types - approved_types)
            route_blockers = list(compatibility["blockers"])
            if parser_field_contract["status"] != "aligned":
                route_blockers.append(
                    {
                        "code": (
                            "parser_field_contract_drift"
                            if parser_field_contract["status"] == "schema_drift"
                            else "parser_field_contract_missing"
                        ),
                        "message": (
                            "ParserSpec 与实际 TextFSM 输出字段不一致。"
                            if parser_field_contract["status"] == "schema_drift"
                            else "Parser 缺少可验证的字段合同。"
                        ),
                        "field_contract_status": parser_field_contract["status"],
                        "differences": parser_field_contract.get("differences") or [],
                        "errors": parser_field_contract.get("errors") or [],
                    }
                )
            for contract in relevant_contracts:
                route_blockers.extend(
                    contract_identity_blockers(
                        contract,
                        expected_entity_type=expected_entity_type,
                        expected_identity_fields=expected_identity_fields,
                        expected_observation_types=expected_types,
                    )
                )
                if parser_field_contract["status"] == "aligned":
                    mapped_sets = contract_adapter_sets[str(contract.id)]
                    route_blockers.extend(
                        contract_source_field_blockers(
                            contract,
                            parser_field_contract=parser_field_contract,
                            fallback_record_set=(
                                next(iter(mapped_sets))
                                if not contract.record_sets_json
                                and len(mapped_sets) == 1
                                else "default"
                            ),
                        )
                    )
            if not relevant_contracts:
                route_blockers.append(
                    {
                        "code": "observation_contract_missing",
                        "message": "Parser 没有产生本检查项标准语义的 Active Contract。",
                    }
                )
            if missing_types:
                route_blockers.append(
                    {
                        "code": "canonical_observation_missing",
                        "message": "Contract 未覆盖检查项全部必需标准语义。",
                        "observation_types": missing_types,
                    }
                )
            if unapproved_types:
                route_blockers.append(
                    {
                        "code": "unapproved_observation_type",
                        "message": "Contract 产生了 Canonical Catalog 之外的临时语义。",
                        "observation_types": unapproved_types,
                    }
                )
            if duplicate_types:
                route_blockers.append(
                    {
                        "code": "observation_projection_ambiguous",
                        "message": "多个 Contract 重复产生同一检查项标准语义。",
                        "observation_types": duplicate_types,
                    }
                )
            candidate = {
                "parser_template_id": parser_id,
                "vendor": parser.vendor,
                "model": parser.model,
                "family": parser.family,
                "profile": parser.profile,
                "canonical_command": normalize_command(parser.canonical_command),
                "version": parser.version,
                "sha256": parser.sha256,
                "compatibility": compatibility,
                "field_contract": parser_field_contract,
                "contract_ids": [str(item.id) for item in relevant_contracts],
                "produced_observation_types": sorted(produced_types),
                "missing_observation_types": missing_types,
                "route_blockers": route_blockers,
                "ready": not route_blockers,
            }
            parser_candidates.append(candidate)
            route_candidates_by_command[candidate["canonical_command"]].append(candidate)

        command_routes: list[dict[str, Any]] = []
        selected_routes: list[dict[str, Any]] = []
        for command in commands:
            canonical_command = command["canonical_command"]
            if result_mode != "structured":
                command_routes.append(
                    {
                        **command,
                        "status": "executable",
                        "execution_mode": result_mode,
                        "selected_parser_template_id": None,
                        "selected_contract_ids": [],
                        "candidate_count": 0,
                        "reasons": [],
                    }
                )
                continue
            candidates = route_candidates_by_command.get(canonical_command, [])
            eligible = [
                item
                for item in candidates
                if item["ready"]
                and (
                    not requested_parser_ids
                    or item["parser_template_id"] in requested_parser_ids
                )
            ]
            selected: dict[str, Any] | None = None
            route_status = "unavailable"
            reasons: list[dict[str, Any]] = []
            if len(eligible) == 1:
                selected = eligible[0]
                route_status = "executable"
                selected_routes.append(selected)
            elif len(eligible) > 1:
                route_status = "ambiguous"
                issue = _issue(
                    "parser_route_ambiguous",
                    "同一命令存在多个完整 Active Parser 路径，请显式选择。",
                    remediation="选择与目标实现兼容的 Parser 版本。",
                    canonical_command=canonical_command,
                    parser_template_ids=[
                        item["parser_template_id"] for item in eligible
                    ],
                )
                blockers.append(issue)
                reasons.append(issue)
            else:
                reasons = [
                    {
                        "code": blocker.get("code"),
                        "message": blocker.get("message"),
                        "parser_template_id": candidate["parser_template_id"],
                    }
                    for candidate in candidates
                    for blocker in candidate["route_blockers"]
                ]
                warning = _issue(
                    "command_route_unavailable",
                    f"命令 {command['command']} 尚无完整可执行资产路径。",
                    remediation="补齐兼容 Parser 与 Canonical Contract，或修订实现范围。",
                    canonical_command=canonical_command,
                )
                warnings.append(warning)
                reasons.append(warning)
            command_routes.append(
                {
                    **command,
                    "status": route_status,
                    "execution_mode": result_mode,
                    "selected_parser_template_id": (
                        selected["parser_template_id"] if selected else None
                    ),
                    "selected_contract_ids": (
                        list(selected["contract_ids"]) if selected else []
                    ),
                    "candidate_count": len(candidates),
                    "reasons": reasons,
                }
            )

        selected_parser_ids = {
            item["parser_template_id"] for item in selected_routes
        }
        selected_contract_ids = {
            contract_id
            for item in selected_routes
            for contract_id in item["contract_ids"]
        }
        if requested_parser_ids - selected_parser_ids:
            blockers.append(
                _issue(
                    "selected_parser_not_executable",
                    "一个或多个所选 Parser 未形成完整可执行命令路径。",
                    parser_template_ids=sorted(
                        requested_parser_ids - selected_parser_ids
                    ),
                )
            )
        if requested_contract_ids - selected_contract_ids:
            blockers.append(
                _issue(
                    "selected_contract_not_executable",
                    "一个或多个所选 Contract 未进入完整可执行命令路径。",
                    observation_contract_ids=sorted(
                        requested_contract_ids - selected_contract_ids
                    ),
                )
            )

        if result_mode == "structured" and not selected_routes:
            promoted: set[tuple[str, str]] = set()
            for candidate in parser_candidates:
                for item in candidate["route_blockers"]:
                    code = str(item.get("code") or "parser_route_invalid")
                    message = str(
                        item.get("message")
                        or "Parser 路径不满足运行包装配要求。"
                    )
                    key = (code, message)
                    if key in promoted:
                        continue
                    promoted.add(key)
                    blockers.append(
                        _issue(
                            code,
                            message,
                            **{
                                "parser_template_id": candidate[
                                    "parser_template_id"
                                ],
                                **{
                                    key: value
                                    for key, value in item.items()
                                    if key
                                    not in {
                                        "code",
                                        "message",
                                        "remediation",
                                        "context",
                                    }
                                },
                            },
                        )
                    )
            blockers.append(
                _issue(
                    "no_executable_structured_command",
                    "当前采集实现没有任何完整的结构化命令路径。",
                    remediation="修复至少一条 Parser + Contract 路径后重新装配。",
                )
            )

        produced_types = {
            observation_type
            for item in selected_routes
            for observation_type in item["produced_observation_types"]
        }
        selected_projection = sorted(expected_types & produced_types)

        change_requirement = dict(
            asset_requirements["change_policy"]
        )
        change_required = set(
            change_requirement.get("required_observation_types") or []
        )
        change_required.update(
            set(
                change_requirement.get(
                    "conditional_observation_types"
                )
                or []
            )
            & produced_types
        )
        if requested_policy_ids and not (
            change_required
            or change_requirement.get(
                "conditional_observation_types"
            )
        ):
            blockers.append(
                _issue(
                    "change_policy_not_required",
                    "当前检查项模式不使用 Canonical ChangePolicy。",
                    remediation=(
                        "非结构化变化由 TextComparisonProfile 管理；"
                        "只有标准 Observation 变化才绑定 ChangePolicy。"
                    ),
                    change_policy_version_ids=sorted(
                        requested_policy_ids
                    ),
                )
            )
        policy_rows = (
            db.query(ChangePolicyVersionRecord)
            .filter(
                ChangePolicyVersionRecord.status == "active",
                ChangePolicyVersionRecord.observation_type.in_(
                    sorted(change_required) or [""]
                ),
            )
            .order_by(
                ChangePolicyVersionRecord.observation_type,
                ChangePolicyVersionRecord.policy_id,
                ChangePolicyVersionRecord.version,
                ChangePolicyVersionRecord.id,
            )
            .all()
        )
        if requested_policy_ids:
            selected_policy_pool = [
                item for item in policy_rows if str(item.id) in requested_policy_ids
            ]
            unknown = sorted(requested_policy_ids - {str(item.id) for item in policy_rows})
            if unknown:
                blockers.append(
                    _issue(
                        "selected_change_policy_unavailable",
                        "所选 ChangePolicy 不存在、不是 Active 或不属于检查项标准语义。",
                        change_policy_version_ids=unknown,
                    )
                )
        else:
            selected_policy_pool = policy_rows
        policies_by_type: dict[str, list[ChangePolicyVersionRecord]] = defaultdict(list)
        for item in selected_policy_pool:
            policies_by_type[str(item.observation_type)].append(item)
        selected_policy_ids: set[str] = set()
        selected_policy_by_type: dict[
            str, ChangePolicyVersionRecord
        ] = {}
        for observation_type in sorted(change_required):
            candidates = policies_by_type.get(observation_type, [])
            if len(candidates) == 1:
                selected_policy_ids.add(str(candidates[0].id))
                selected_policy_by_type[observation_type] = candidates[0]
            elif not candidates:
                blockers.append(
                    _issue(
                        "change_policy_missing",
                        f"{observation_type} 缺少 Active ChangePolicy。",
                        observation_type=observation_type,
                    )
                )
            else:
                blockers.append(
                    _issue(
                        "change_policy_ambiguous",
                        f"{observation_type} 存在多个 Active ChangePolicy，请显式选择。",
                        observation_type=observation_type,
                        change_policy_version_ids=[
                            str(item.id) for item in candidates
                        ],
                    )
                )

        current_requirement = dict(
            asset_requirements["current_state_rule"]
        )
        event_requirement = dict(
            asset_requirements["event_rule"]
        )
        current_rule_types_allowed = set(
            current_requirement.get("required_observation_types") or []
        )
        current_rule_types_allowed.update(
            set(
                current_requirement.get(
                    "conditional_observation_types"
                )
                or []
            )
            & produced_types
        )
        required_event_types_by_observation = {
            str(observation_type): {
                str(event_type)
                for event_type in event_types or []
                if str(event_type)
            }
            for observation_type, event_types in dict(
                event_requirement.get(
                    "required_event_types_by_observation"
                )
                or {}
            ).items()
        }
        conditional_event_types_by_observation = {
            str(observation_type): {
                str(event_type)
                for event_type in event_types or []
                if str(event_type)
            }
            for observation_type, event_types in dict(
                event_requirement.get(
                    "conditional_event_types_by_observation"
                )
                or {}
            ).items()
            if str(observation_type) in produced_types
        }
        required_event_types_by_observation.update(
            conditional_event_types_by_observation
        )
        required_recovery_pairs_by_observation = {
            str(observation_type): tuple(
                {
                    "fault": str(item.get("fault") or ""),
                    "recovery": str(item.get("recovery") or ""),
                }
                for item in pairs or []
                if isinstance(item, Mapping)
                and str(item.get("fault") or "")
                and str(item.get("recovery") or "")
            )
            for observation_type, pairs in dict(
                event_requirement.get(
                    "required_recovery_pairs_by_observation"
                )
                or {}
            ).items()
            if str(observation_type)
            in required_event_types_by_observation
        }
        effective_event_role_bindings: dict[str, dict[str, Any]] = {}
        evidence_bound_constraints = dict(
            event_requirement.get(
                "evidence_bound_event_constraints_by_observation"
            )
            or {}
        )
        for observation_type, raw_profile in sorted(
            evidence_bound_constraints.items()
        ):
            if not isinstance(raw_profile, Mapping):
                continue
            if (
                raw_profile.get("required_observation") is False
                and str(observation_type) not in produced_types
            ):
                continue
            policy_record = selected_policy_by_type.get(
                str(observation_type)
            )
            if policy_record is None:
                continue
            scoped = scope_event_constraints_to_policy(
                dict(raw_profile.get("constraints") or {}),
                dict(policy_record.policy_json or {}),
            )
            if scoped.get("success") is not True:
                blockers.append(
                    _issue(
                        "evidence_bound_event_roles_unresolved",
                        (
                            f"{observation_type} 的事件角色无法从已选 "
                            "ChangePolicy 阈值证据中确定。"
                        ),
                        observation_type=str(observation_type),
                        change_policy_version_id=str(policy_record.id),
                        errors=list(scoped.get("errors") or []),
                    )
                )
                continue
            event_types = {
                str(item)
                for item in scoped.get("active_event_types") or []
                if str(item)
            }
            required_event_types_by_observation[
                str(observation_type)
            ] = event_types
            effective_constraints = dict(
                scoped.get("constraints") or {}
            )
            effective_event_role_bindings[str(observation_type)] = {
                "change_policy_version_id": str(policy_record.id),
                "active_roles": list(scoped.get("active_roles") or []),
                "event_types": sorted(event_types),
            }
            recovery_pairs = tuple(
                {
                    "fault": str(item.get("fault") or ""),
                    "recovery": str(item.get("recovery") or ""),
                }
                for item in effective_constraints.get(
                    "required_recovery_pairs"
                )
                or []
                if isinstance(item, Mapping)
                and str(item.get("fault") or "")
                and str(item.get("recovery") or "")
            )
            if recovery_pairs:
                required_recovery_pairs_by_observation[
                    str(observation_type)
                ] = recovery_pairs
        required_event_classes = {
            str(event_type): str(event_class)
            for event_type, event_class in dict(
                event_requirement.get("required_event_classes")
                or {}
            ).items()
            if str(event_type) and str(event_class)
        }
        required_event_severities = {
            str(event_type): str(severity).casefold()
            for event_type, severity in dict(
                event_requirement.get("required_event_severities")
                or {}
            ).items()
            if str(event_type) and str(severity)
        }
        allowed_rule_observation_types = (
            current_rule_types_allowed
            | set(required_event_types_by_observation)
        )
        if requested_rule_ids and not allowed_rule_observation_types:
            blockers.append(
                _issue(
                    "event_rule_not_required",
                    "当前检查项模式未声明 CurrentStateRule 或 EventRule 需求。",
                    event_rule_version_ids=sorted(
                        requested_rule_ids
                    ),
                )
            )
        rule_rows = (
            db.query(EventRuleVersionRecord, EventRuleRecord)
            .join(EventRuleRecord, EventRuleRecord.id == EventRuleVersionRecord.rule_id)
            .filter(EventRuleVersionRecord.status == "active")
            .order_by(
                EventRuleVersionRecord.priority.desc(),
                EventRuleRecord.name,
                EventRuleVersionRecord.version,
                EventRuleVersionRecord.id,
            )
            .all()
            if allowed_rule_observation_types or requested_rule_ids
            else []
        )
        rule_candidates: list[dict[str, Any]] = []
        selected_rule_bindings: dict[str, tuple[str, ...]] = {}
        selected_rule_rows: list[tuple[EventRuleVersionRecord, EventRuleRecord]] = []
        all_rule_ids = {str(version.id) for version, _root in rule_rows}
        unknown_requested_rules = sorted(requested_rule_ids - all_rule_ids)
        if unknown_requested_rules:
            blockers.append(
                _issue(
                    "selected_event_rule_unavailable",
                    "所选 EventRule 不存在或不是 Active。",
                    event_rule_version_ids=unknown_requested_rules,
                )
            )
        for version, root in rule_rows:
            version_id = str(version.id)
            observation_types = event_rule_observation_types(version)
            matched_observation_types = (
                set(observation_types)
                & allowed_rule_observation_types
            )
            intersects = bool(matched_observation_types)
            explicitly_selected = version_id in requested_rule_ids
            if not intersects and not explicitly_selected:
                continue
            rule_blockers: list[dict[str, Any]] = []
            if not observation_types:
                rule_blockers.append(
                    {
                        "code": "event_rule_observation_types_missing",
                        "message": "EventRule 未在不可变条件中显式声明 observation_types。",
                    }
                )
            unknown_types = sorted(set(observation_types) - approved_types)
            outside_check = sorted(set(observation_types) - expected_types)
            outside_profile = sorted(
                set(observation_types)
                - allowed_rule_observation_types
            )
            if unknown_types:
                rule_blockers.append(
                    {
                        "code": "event_rule_observation_type_unapproved",
                        "message": "EventRule 引用了 Canonical Catalog 之外的语义。",
                        "observation_types": unknown_types,
                    }
                )
            if outside_check:
                rule_blockers.append(
                    {
                        "code": "event_rule_cross_check_item_binding",
                        "message": "EventRule 同时引用了当前检查项之外的语义，不能装入本运行包。",
                        "observation_types": outside_check,
                    }
                )
            if outside_profile:
                rule_blockers.append(
                    {
                        "code": "event_rule_outside_asset_profile",
                        "message": (
                            "EventRule 引用了当前资产需求画像未声明的语义。"
                        ),
                        "observation_types": outside_profile,
                    }
                )
            scope = govern_evaluation_scope(
                version.evaluation_scope,
                event_class=version.event_class,
            )
            event_type = str(version.event_type or "")
            if scope in {"change", "both"}:
                undeclared_event_observations = sorted(
                    observation_type
                    for observation_type in matched_observation_types
                    if event_type
                    not in required_event_types_by_observation.get(
                        observation_type,
                        set(),
                    )
                )
                if undeclared_event_observations:
                    rule_blockers.append(
                        {
                            "code": (
                                "event_rule_type_outside_asset_profile"
                            ),
                            "message": (
                                "EventRule emits an event type that the "
                                "check-item profile does not declare."
                            ),
                            "event_type": event_type,
                            "observation_types": (
                                undeclared_event_observations
                            ),
                        }
                    )
            expected_event_class = required_event_classes.get(event_type)
            actual_event_class = str(version.event_class or "fault")
            if (
                expected_event_class
                and actual_event_class != expected_event_class
            ):
                rule_blockers.append(
                    {
                        "code": "event_rule_class_mismatch",
                        "message": (
                            "EventRule class does not match the "
                            "check-item catalog."
                        ),
                        "event_type": event_type,
                        "expected_event_class": expected_event_class,
                        "actual_event_class": actual_event_class,
                    }
                )
            expected_severity = required_event_severities.get(event_type)
            actual_severity = str(version.severity or "").casefold()
            if (
                expected_severity
                and actual_severity != expected_severity
            ):
                rule_blockers.append(
                    {
                        "code": "event_rule_severity_mismatch",
                        "message": (
                            "EventRule severity does not match the "
                            "check-item catalog."
                        ),
                        "event_type": event_type,
                        "expected_severity": expected_severity,
                        "actual_severity": actual_severity,
                    }
                )
            expected_recovery_targets = {
                pair["fault"]
                for observation_type in matched_observation_types
                for pair in (
                    required_recovery_pairs_by_observation.get(
                        observation_type,
                        (),
                    )
                )
                if pair["recovery"] == event_type
            }
            if expected_recovery_targets:
                actual_recovery_targets_raw = dict(
                    version.recovery_json or {}
                ).get("resolves") or []
                if isinstance(actual_recovery_targets_raw, str):
                    actual_recovery_targets_raw = [
                        actual_recovery_targets_raw
                    ]
                actual_recovery_targets = {
                    str(item)
                    for item in actual_recovery_targets_raw
                    if str(item)
                }
                if actual_recovery_targets != (
                    expected_recovery_targets
                ):
                    rule_blockers.append(
                        {
                            "code": "event_rule_recovery_pair_mismatch",
                            "message": (
                                "Recovery targets do not match the "
                                "check-item catalog."
                            ),
                            "event_type": event_type,
                            "expected_recovery_targets": sorted(
                                expected_recovery_targets
                            ),
                            "actual_recovery_targets": sorted(
                                actual_recovery_targets
                            ),
                        }
                    )
            candidate = {
                "event_rule_id": str(root.id),
                "event_rule_version_id": version_id,
                "name": root.name,
                "event_type": version.event_type,
                "severity": version.severity,
                "event_class": version.event_class,
                "evaluation_scope": scope,
                "recovery": dict(version.recovery_json or {}),
                "observation_types": list(observation_types),
                "digest": version.digest,
                "blockers": rule_blockers,
                "ready": not rule_blockers,
            }
            rule_candidates.append(candidate)
            if explicitly_selected and rule_blockers:
                blockers.extend(
                    _issue(
                        str(item.get("code") or "event_rule_invalid"),
                        str(item.get("message") or "所选 EventRule 不满足运行包装配要求。"),
                        **{
                            "event_rule_version_id": version_id,
                            **{
                                key: value
                                for key, value in item.items()
                                if key not in {"code", "message"}
                            },
                        },
                    )
                    for item in rule_blockers
                )
            if (
                not rule_blockers
                and (not requested_rule_ids or explicitly_selected)
                and intersects
            ):
                selected_rule_bindings[version_id] = observation_types
                selected_rule_rows.append((version, root))

        current_required = set(
            current_requirement.get("required_observation_types")
            or []
        )
        current_covered: set[str] = set()
        for version, _root in selected_rule_rows:
            scope = govern_evaluation_scope(
                version.evaluation_scope,
                event_class=version.event_class,
            )
            if (
                str(version.event_class or "fault") != "recovery"
                and scope in {"current_state", "both"}
            ):
                current_covered.update(event_rule_observation_types(version))
        if assessment_mode == "health":
            for observation_type in sorted(current_required - current_covered):
                blockers.append(
                    _issue(
                        "current_state_event_rule_missing",
                        f"{observation_type} 缺少可用于当前状态判断的 Active EventRule。",
                        observation_type=observation_type,
                    )
                )

        selected_event_types_by_observation: dict[str, set[str]] = (
            defaultdict(set)
        )
        selected_event_class_by_type: dict[str, set[str]] = (
            defaultdict(set)
        )
        for version, _root in selected_rule_rows:
            for observation_type in event_rule_observation_types(
                version
            ):
                selected_event_types_by_observation[
                    observation_type
                ].add(str(version.event_type))
            selected_event_class_by_type[str(version.event_type)].add(
                str(version.event_class or "fault")
            )
        for (
            observation_type,
            required_event_types,
        ) in sorted(required_event_types_by_observation.items()):
            missing_event_types = sorted(
                required_event_types
                - selected_event_types_by_observation.get(
                    observation_type,
                    set(),
                )
            )
            if missing_event_types:
                blockers.append(
                    _issue(
                        "required_event_rule_missing",
                        (
                            f"{observation_type} 缺少检查项目录要求的 "
                            "Active EventRule。"
                        ),
                        observation_type=observation_type,
                        event_types=missing_event_types,
                    )
                )
        for event_type, required_event_class in (
            required_event_classes.items()
        ):
            if event_type not in {
                item
                for event_types in (
                    required_event_types_by_observation.values()
                )
                for item in event_types
            }:
                continue
            actual_classes = selected_event_class_by_type.get(
                str(event_type),
                set(),
            )
            if actual_classes and str(required_event_class) not in (
                actual_classes
            ):
                blockers.append(
                    _issue(
                        "event_rule_class_mismatch",
                        (
                            f"{event_type} 的 EventRule class "
                            "与检查项目录不一致。"
                        ),
                        event_type=event_type,
                        expected_event_class=required_event_class,
                        actual_event_classes=sorted(actual_classes),
                    )
                )

        selected_comparison_profile_id: str | None = None
        comparison_candidates: list[dict[str, Any]] = []
        if result_mode in {"normalized_diff", "hash_only"}:
            profile_rows = (
                db.query(
                    TextComparisonProfileVersionRecord,
                    TextComparisonProfileRecord,
                )
                .join(
                    TextComparisonProfileRecord,
                    TextComparisonProfileRecord.id
                    == TextComparisonProfileVersionRecord.profile_id,
                )
                .filter(TextComparisonProfileVersionRecord.status == "active")
                .order_by(
                    TextComparisonProfileRecord.id,
                    TextComparisonProfileVersionRecord.version,
                )
                .all()
            )
            hint = str(
                dict(check_version.canonical_contract_json or {}).get(
                    "comparison_profile"
                )
                or ""
            ).strip()
            matching_profiles = [
                (version, root)
                for version, root in profile_rows
                if not hint
                or str(root.id) == hint
                or str(root.id).startswith(f"{hint}.")
            ]
            comparison_candidates = [
                {
                    "profile_id": str(root.id),
                    "profile_version_id": str(version.id),
                    "name": root.name,
                    "version": version.version,
                    "digest": version.digest,
                }
                for version, root in matching_profiles
            ]
            if comparison_profile_version_id:
                chosen = next(
                    (
                        version
                        for version, _root in matching_profiles
                        if str(version.id) == str(comparison_profile_version_id)
                    ),
                    None,
                )
                if chosen is None:
                    blockers.append(
                        _issue(
                            "comparison_profile_unavailable",
                            "所选 ComparisonProfile 不存在、不是 Active 或与检查项不匹配。",
                        )
                    )
                else:
                    selected_comparison_profile_id = str(chosen.id)
            elif len(matching_profiles) == 1:
                selected_comparison_profile_id = str(matching_profiles[0][0].id)
            elif not matching_profiles:
                blockers.append(
                    _issue(
                        "comparison_profile_missing",
                        "非结构化比较检查项缺少 Active ComparisonProfile。",
                    )
                )
            else:
                blockers.append(
                    _issue(
                        "comparison_profile_ambiguous",
                        "存在多个可用 ComparisonProfile，请显式选择。",
                        comparison_profile_version_ids=[
                            str(version.id) for version, _root in matching_profiles
                        ],
                    )
                )
        elif comparison_profile_version_id:
            blockers.append(
                _issue(
                    "comparison_profile_not_allowed",
                    "结构化检查项不能绑定 ComparisonProfile。",
                )
            )

        identity_version = (
            db.query(EntityIdentityPolicyVersionRecord)
            .filter(EntityIdentityPolicyVersionRecord.status == "active")
            .order_by(
                EntityIdentityPolicyVersionRecord.version.desc(),
                EntityIdentityPolicyVersionRecord.id,
            )
            .first()
        )
        if identity_version is None:
            blockers.append(
                _issue(
                    "entity_identity_policy_missing",
                    "缺少 Active EntityIdentityPolicy。",
                )
            )

        active_bundle = (
            db.query(
                CheckItemRuntimeBundleRecord,
                CheckItemRuntimeBundleVersionRecord,
            )
            .join(
                CheckItemRuntimeBundleVersionRecord,
                CheckItemRuntimeBundleVersionRecord.bundle_id
                == CheckItemRuntimeBundleRecord.id,
            )
            .filter(
                CheckItemRuntimeBundleVersionRecord.check_item_version_id
                == check_version.id,
                CheckItemRuntimeBundleVersionRecord.implementation_version_id
                == implementation_version.id,
                CheckItemRuntimeBundleVersionRecord.status == "active",
            )
            .first()
        )
        draft_rows = (
            db.query(CheckItemRuntimeBundleVersionRecord)
            .filter(
                CheckItemRuntimeBundleVersionRecord.check_item_version_id
                == check_version.id,
                CheckItemRuntimeBundleVersionRecord.implementation_version_id
                == implementation_version.id,
                CheckItemRuntimeBundleVersionRecord.status == "draft",
            )
            .order_by(
                CheckItemRuntimeBundleVersionRecord.version.desc(),
                CheckItemRuntimeBundleVersionRecord.id,
            )
            .all()
        )

        executable_count = sum(
            item["status"] == "executable" for item in command_routes
        )
        command_availability = (
            "not_applicable"
            if not command_routes
            else "available"
            if executable_count == 1
            else "unavailable"
        )

        selected = {
            "parser_template_ids": sorted(selected_parser_ids),
            "observation_contract_ids": sorted(selected_contract_ids),
            "change_policy_version_ids": sorted(selected_policy_ids),
            "event_rule_version_ids": sorted(selected_rule_bindings),
            "event_rule_bindings": {
                key: list(value)
                for key, value in sorted(selected_rule_bindings.items())
            },
            "comparison_profile_version_id": selected_comparison_profile_id,
            "entity_identity_policy_version_id": (
                str(identity_version.id) if identity_version is not None else None
            ),
        }
        assessment_policy = normalize_assessment_policy_contract(
            dict(check_version.canonical_contract_json or {}),
            dict(check_version.applicability_json or {}),
        )
        compatibility = {
            "schema_version": 2,
            "catalog_candidate": (
                {
                    "change_id": str(governed_candidate_change.id),
                    "payload_digest": str(governed_candidate_change.payload_digest),
                    "impact_digest": str(governed_candidate_change.impact_digest or ""),
                    "status": str(governed_candidate_change.status),
                }
                if governed_candidate_change is not None
                else None
            ),
            "asset_requirement_profile": asset_requirement_profile,
            "asset_requirement_profile_digest": (
                asset_requirement_profile_digest
            ),
            "implementation_selector": dict(implementation_version.match_json or {}),
            "expected_observation_types": sorted(expected_types),
            "required_observation_types": sorted(required_types),
            "produced_observation_types": sorted(produced_types),
            "observation_projection": selected_projection,
            "current_state_rule_types": sorted(current_covered),
            "change_policy_types": sorted(
                {
                    str(item.observation_type)
                    for item in policy_rows
                    if str(item.id) in selected_policy_ids
                }
            ),
            "effective_event_role_bindings": dict(
                sorted(effective_event_role_bindings.items())
            ),
            "executable_commands": [
                item["canonical_command"]
                for item in command_routes
                if item["status"] == "executable"
            ],
            # Deprecated compatibility field. Fallback execution is disabled.
            "unavailable_fallbacks": [],
            "fallback_resilience": "not_applicable",
            "unavailable_commands": [
                item["canonical_command"]
                for item in command_routes
                if item["status"] != "executable"
            ],
            "command_availability": command_availability,
            "assessment_policy": assessment_policy,
            "parser_manifests": {
                item["parser_template_id"]: item["compatibility"]["parser_manifest"]
                for item in selected_routes
            },
            "evidence_policy": {
                "freshness_policy": freshness_policy,
                "empty_result_policy": empty_result_policy,
                "coverage_policy": coverage_policy,
                "output_quality_profile": {
                    "version_id": str(implementation_version.id),
                    "digest": canonical_digest(output_quality_policy),
                    "policy": output_quality_policy,
                },
            },
        }
        dependency_snapshot = {
            "catalog_candidate": (
                {
                    "change_id": str(governed_candidate_change.id),
                    "payload_digest": str(governed_candidate_change.payload_digest),
                    "impact_digest": str(governed_candidate_change.impact_digest or ""),
                    "status": str(governed_candidate_change.status),
                }
                if governed_candidate_change is not None
                else None
            ),
            "check_item": {
                "id": str(check_version.id),
                "digest": str(check_version.digest),
                "status": str(check_version.status),
            },
            "implementation": {
                "id": str(implementation_version.id),
                "digest": str(implementation_version.digest),
                "status": str(implementation_version.status),
            },
            "canonical_catalog": {
                "version": canonical_catalog.version,
                "digest": canonical_catalog.digest,
            },
            "asset_requirement_profile": {
                "digest": asset_requirement_profile_digest,
                "profile": asset_requirement_profile,
            },
            "identity_policy": (
                {
                    "id": str(identity_version.id),
                    "digest": str(identity_version.digest),
                    "status": str(identity_version.status),
                }
                if identity_version is not None
                else None
            ),
            "evidence_policy": {
                "freshness_policy_digest": canonical_digest(
                    freshness_policy
                ),
                "empty_result_policy_digest": canonical_digest(
                    empty_result_policy
                ),
                "coverage_policy_digest": canonical_digest(coverage_policy),
                "output_quality_policy_digest": canonical_digest(
                    output_quality_policy
                ),
            },
            "assessment_policy": {
                "digest": assessment_policy["digest"],
                "contract": {
                    key: value
                    for key, value in assessment_policy.items()
                    if key != "digest"
                },
            },
            "parsers": {
                str(item.id): {
                    "sha256": str(item.sha256),
                    "status": str(item.status),
                    "validation_digest": canonical_digest(
                        dict(item.validation_json or {}).get("compatibility") or {}
                    ),
                }
                for item in parser_rows
                if str(item.id) in selected_parser_ids
            },
            "contracts": {
                str(item.id): {
                    "digest": str(item.contract_sha256),
                    "status": str(item.status),
                }
                for item in contract_rows
                if str(item.id) in selected_contract_ids
            },
            "policies": {
                str(item.id): {
                    "digest": str(item.digest),
                    "status": str(item.status),
                }
                for item in policy_rows
                if str(item.id) in selected_policy_ids
            },
            "event_rules": {
                str(version.id): {
                    "digest": str(version.digest),
                    "status": str(version.status),
                    "observation_types": list(
                        selected_rule_bindings.get(str(version.id), ())
                    ),
                }
                for version, _root in selected_rule_rows
            },
            "comparison_profile": next(
                (
                    {
                        "id": item["profile_version_id"],
                        "digest": item["digest"],
                    }
                    for item in comparison_candidates
                    if item["profile_version_id"]
                    == selected_comparison_profile_id
                ),
                None,
            ),
            "active_bundle": (
                {
                    "version_id": str(active_bundle[1].id),
                    "digest": str(active_bundle[1].digest),
                }
                if active_bundle is not None
                else None
            ),
        }
        blockers = _actionable_issues(
            blockers,
            check_item_id=(
                str(check_item.id) if check_item is not None else None
            ),
            check_item_version_id=str(check_version.id),
            implementation_version_id=str(implementation_version.id),
        )
        warnings = _actionable_issues(
            warnings,
            check_item_id=(
                str(check_item.id) if check_item is not None else None
            ),
            check_item_version_id=str(check_version.id),
            implementation_version_id=str(implementation_version.id),
        )
        preview_payload = {
            "check_item_version_id": str(check_version.id),
            "implementation_version_id": str(implementation_version.id),
            "selected": selected,
            "compatibility": compatibility,
            "dependency_snapshot": dependency_snapshot,
            "blocker_codes": sorted(item["code"] for item in blockers),
            "warning_codes": sorted(item["code"] for item in warnings),
        }
        preview_digest = canonical_digest(preview_payload)
        return {
            "schema_version": 2,
            "check_item": {
                "id": str(check_item.id) if check_item is not None else None,
                "version_id": str(check_version.id),
                "name": check_item.name if check_item is not None else None,
                "display_name": (
                    check_item_display_name(check_item.id, check_item.name)
                    if check_item is not None
                    else None
                ),
                "result_mode": result_mode,
                "assessment_mode": assessment_mode,
            },
            "implementation": {
                "id": (
                    str(implementation.id) if implementation is not None else None
                ),
                "version_id": str(implementation_version.id),
                "name": implementation.name if implementation is not None else None,
                "selector": dict(implementation_version.match_json or {}),
                "commands": commands,
            },
            "selected": selected,
            "asset_requirement_profile": asset_requirement_profile,
            "asset_requirement_profile_digest": (
                asset_requirement_profile_digest
            ),
            "candidates": {
                "parsers": parser_candidates,
                "observation_contracts": [
                    {
                        "contract_id": str(item.id),
                        "source_template_id": str(item.source_template_id or ""),
                        "schema_id": str(item.schema_id),
                        "entity_type": str(item.entity_type),
                        "observation_types": sorted(
                            contract_observation_types(item)
                        ),
                        "digest": str(item.contract_sha256),
                    }
                    for item in contract_rows
                    if str(item.source_template_id or "") in parser_by_id
                ],
                "change_policies": [
                    {
                        "version_id": str(item.id),
                        "policy_id": str(item.policy_id),
                        "observation_type": str(item.observation_type),
                        "digest": str(item.digest),
                    }
                    for item in policy_rows
                ],
                "event_rules": rule_candidates,
                "comparison_profiles": comparison_candidates,
            },
            "command_routes": command_routes,
            "compatibility": compatibility,
            "dependency_snapshot": dependency_snapshot,
            "active_bundle": (
                {
                    "bundle_id": str(active_bundle[0].id),
                    "version_id": str(active_bundle[1].id),
                    "version": int(active_bundle[1].version),
                    "digest": str(active_bundle[1].digest),
                    "activated_at": active_bundle[1].activated_at,
                }
                if active_bundle is not None
                else None
            ),
            "drafts": [
                {
                    "version_id": str(item.id),
                    "version": int(item.version),
                    "digest": str(item.digest),
                    "created_at": item.created_at,
                }
                for item in draft_rows
            ],
            "operationally_ready": not blockers,
            "fallback_resilience": "not_applicable",
            "command_availability": command_availability,
            "blockers": blockers,
            "warnings": warnings,
            "preview_digest": preview_digest,
        }

    @staticmethod
    def _terminal_plan(
        *,
        check_item_version_id: str,
        implementation_version_id: str,
        blockers: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        blockers = _actionable_issues(
            blockers,
            check_item_id=None,
            check_item_version_id=check_item_version_id,
            implementation_version_id=implementation_version_id,
        )
        warnings = _actionable_issues(
            warnings,
            check_item_id=None,
            check_item_version_id=check_item_version_id,
            implementation_version_id=implementation_version_id,
        )
        payload = {
            "check_item_version_id": check_item_version_id,
            "implementation_version_id": implementation_version_id,
            "blocker_codes": [item["code"] for item in blockers],
        }
        return {
            "schema_version": 2,
            "check_item": {"version_id": check_item_version_id},
            "implementation": {"version_id": implementation_version_id},
            "selected": {
                "parser_template_ids": [],
                "observation_contract_ids": [],
                "change_policy_version_ids": [],
                "event_rule_version_ids": [],
                "event_rule_bindings": {},
                "comparison_profile_version_id": None,
                "entity_identity_policy_version_id": None,
            },
            "candidates": {
                "parsers": [],
                "observation_contracts": [],
                "change_policies": [],
                "event_rules": [],
                "comparison_profiles": [],
            },
            "command_routes": [],
            "compatibility": {},
            "asset_requirement_profile": None,
            "asset_requirement_profile_digest": None,
            "dependency_snapshot": {},
            "active_bundle": None,
            "drafts": [],
            "operationally_ready": False,
            "fallback_resilience": "none",
            "blockers": blockers,
            "warnings": warnings,
            "preview_digest": canonical_digest(payload),
        }
