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

from src.core.parser.contract_inference import infer_contract  # noqa: E402
from src.core.parser.contract_validation import (  # noqa: E402
    infer_and_validate_contract,
    proposal_from_dict,
)
from src.core.parser.semantic_catalog import (  # noqa: E402
    canonical_field_for_name,
)
from src.core.parser.semantic_catalog import (
    field_semantics as catalog_field_semantics,
)
from src.core.rule_control.generation_agent import (  # noqa: E402
    DEFAULT_RULE_GENERATION_PROVIDERS,
    RuleGenerationAgent,
)
from src.core.rule_control.planning import (  # noqa: E402
    COMPARABLE_SEMANTIC_TYPES,
    NON_TARGET_ROLES,
    enrich_contract_reference_semantics,
    profile_fields,
)
from src.core.state.observation_applicability import (  # noqa: E402
    validate_applicability,
)

OBSERVATION_TYPE_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
)
NUMERIC_SEMANTIC_TYPES = frozenset({"counter", "gauge", "percentage"})


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
) -> dict[str, Any]:
    declared = {
        str(item)
        for item in [
            *(candidate.get("required_fields") or []),
            *(candidate.get("optional_fields") or []),
        ]
    }
    semantic_fields = {str(item) for item in (candidate.get("state_semantics") or {})}
    unknown_fields = sorted((declared | semantic_fields) - set(field_inventory))
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
        field_profiles=profile_fields(
            all_records,
            field_inventory=field_inventory,
        ),
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


def _governed_semantic_hints(
    field_inventory: list[str], contract_seed: dict[str, Any] | None
) -> dict[str, dict[str, Any]]:
    seed_semantics = dict((contract_seed or {}).get("state_semantics") or {})
    hints: dict[str, dict[str, Any]] = {}
    primary_keys = {str(item) for item in (contract_seed or {}).get("primary_keys") or []}
    ignored = {str(item) for item in (contract_seed or {}).get("ignore_fields") or []}
    for field in field_inventory:
        expected: dict[str, Any] = {}
        catalog_hint = catalog_field_semantics(field)
        if isinstance(catalog_hint, dict):
            expected.update(catalog_hint)
        if isinstance(seed_semantics.get(field), dict):
            expected.update(seed_semantics[field])
        if field in primary_keys:
            expected["semantic_type"] = "identity"
        elif field in ignored:
            expected.setdefault("semantic_type", "text")
            expected["ignore"] = True
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
    for field, raw in tuple(semantics.items()):
        actual = dict(raw or {}) if isinstance(raw, dict) else {}
        if "applicability" not in hints.get(str(field), {}):
            actual.pop("applicability", None)
        semantics[field] = actual
    for field, governed in hints.items():
        actual = dict(semantics.get(field) or {})
        semantics[field] = {**actual, **governed}
    output["state_semantics"] = semantics
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
        "primary_keys",
        "required_fields",
        "optional_fields",
        "validators",
        "allow_empty",
        "empty_patterns",
        "ignore_fields",
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
    output["evidence"] = evidence
    return output


def _target_field_matches(
    field_inventory: list[str],
    target: dict[str, Any],
    semantics: dict[str, Any],
) -> list[str]:
    expected = str(target.get("canonical_field") or "").strip().casefold()
    observation_type = str(target.get("observation_type") or "").strip()
    matches: list[str] = []
    for field in field_inventory:
        normalized = str(field).strip().casefold()
        if re.search(r"(?:^|_)(?:alarm|warning|threshold|high|low)(?:_|$)", normalized):
            if not re.search(
                r"(?:^|_)(?:alarm|warning|threshold|high|low)(?:_|$)", expected
            ):
                continue
        canonical = canonical_field_for_name(field) or normalized
        if canonical == expected or normalized == expected:
            matches.append(field)
    preferred = [
        field
        for field in matches
        if str((semantics.get(field) or {}).get("observation_type") or "")
        == observation_type
    ]
    return preferred if len(preferred) == 1 else matches


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
    resolved: dict[str, str] = {}
    for target in observation_targets:
        observation_type = str(target.get("observation_type") or "").strip()
        matches = _target_field_matches(field_inventory, target, semantics)
        if not observation_type or len(matches) != 1:
            continue
        field = matches[0]
        metadata = dict(semantics.get(field) or {})
        metadata["observation_type"] = observation_type
        metadata["comparison_role"] = "observation"
        metadata["monitor"] = True
        semantics[field] = metadata
        resolved[observation_type] = field
    if strict:
        target_fields = set(resolved.values())
        for field, raw in tuple(semantics.items()):
            if field in target_fields or not isinstance(raw, dict):
                continue
            metadata = dict(raw)
            semantic_type = str(metadata.get("semantic_type") or "").strip().casefold()
            if semantic_type not in COMPARABLE_SEMANTIC_TYPES:
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
    }
    output["state_semantics"] = semantics
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
    field_profiles: dict[str, dict[str, Any]] | None = None,
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

    semantics = dict(candidate.get("state_semantics") or {})
    policy_target_count = 0
    observation_fields: dict[str, list[str]] = {}
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
                    "lowercase dot namespace such as interface.oper_status"
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
        if len(fields) > 1:
            errors.append(
                f"observation_type {observation_type} maps to multiple fields: "
                + ", ".join(sorted(fields))
            )
    expected_observation_types: set[str] = set()
    for target in observation_targets or []:
        observation_type = str(target.get("observation_type") or "").strip()
        if not observation_type:
            errors.append("observation target requires observation_type")
            continue
        expected_observation_types.add(observation_type)
        matches = _target_field_matches(field_inventory, target, semantics)
        if len(matches) != 1:
            errors.append(
                f"observation target {observation_type} must resolve to exactly one field; "
                f"matched={matches}"
            )
            continue
        field = matches[0]
        if str((semantics.get(field) or {}).get("observation_type") or "") != observation_type:
            errors.append(
                f"state_semantics.{field}.observation_type must match governed value "
                f"{observation_type!r}"
            )
    if strict_observation_targets:
        extras = sorted(set(observation_fields) - expected_observation_types)
        if extras:
            errors.append("contract exposes unapproved observation types: " + ", ".join(extras))
    for field, expected in _governed_semantic_hints(field_inventory, seed).items():
        actual = semantics.get(field)
        if not isinstance(actual, dict):
            continue
        for key in (
            "semantic_type",
            "unit",
            "observation_type",
            "ignore",
            "applicability",
        ):
            if key in expected and actual.get(key) != expected[key]:
                errors.append(
                    f"state_semantics.{field}.{key} must match governed value {expected[key]!r}"
                )
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


def run_contract_generation(params: dict[str, Any]) -> dict[str, Any]:
    mode = str(params.get("mode") or "infer_and_validate").strip().lower()
    command = str(params.get("command") or "").strip()
    if not command:
        raise ValueError("command is required")
    samples_text = str(params.get("samples_text") or params.get("samples_summary") or "")
    records = _records(params)
    record_groups = _record_groups(params, records)
    proposal_payload = params.get("proposal") or params.get("contract")
    proposal = proposal_from_dict(proposal_payload) if isinstance(proposal_payload, dict) else None

    generation_provider = str(params.get("generation_provider") or "").strip().casefold()
    if generation_provider == "llm" and mode != "validate":
        field_inventory = sorted(
            {str(field) for record in records for field in record if str(field).strip()}
        )

        governed_hints = _governed_semantic_hints(
            field_inventory,
            proposal_payload if isinstance(proposal_payload, dict) else None,
        )
        observation_targets = [
            dict(item)
            for item in params.get("observation_targets") or []
            if isinstance(item, dict)
        ]
        strict_observation_targets = bool(
            params.get("strict_observation_targets", bool(observation_targets))
        )

        def validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
            schema_bound = _inherit_approved_parser_schema(
                candidate,
                proposal_payload if isinstance(proposal_payload, dict) else None,
            )
            governed = _apply_governed_semantic_hints(schema_bound, governed_hints)
            target_bound = _apply_observation_target_constraints(
                governed,
                field_inventory=field_inventory,
                observation_targets=observation_targets,
                strict=strict_observation_targets,
            )
            enriched = enrich_contract_reference_semantics(
                contract=target_bound,
                records=records,
            )
            return _validate_contract_by_group(
                command=command,
                groups=record_groups,
                candidate=enriched,
                field_inventory=field_inventory,
                contract_seed=(proposal_payload if isinstance(proposal_payload, dict) else None),
                require_policy_targets=True,
                observation_targets=observation_targets,
                strict_observation_targets=strict_observation_targets,
            )

        context = {
            "command": command,
            "vendor_hint": params.get("vendor"),
            "model_hint": params.get("model"),
            "field_inventory": field_inventory,
            "field_profiles": profile_fields(
                records,
                field_inventory=field_inventory,
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
