"""Safe migration and candidate tooling for CommandAdapter V2 projections.

This module deliberately owns no persistence model.  A projection is part of an
immutable ``CollectionImplementationVersion`` and therefore must be reviewed,
published and rolled back with that implementation.  The helpers below only
produce a candidate and a deterministic readiness report; they never infer a
new canonical meaning or mutate an ObservationContract.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from src.apps.rule_governance.backend.rule_control.generation_agent import (
    DEFAULT_RULE_GENERATION_PROVIDERS,
    RuleGenerationAgent,
)
from src.core.check_items.command_adapter import (
    ADAPTER_SCHEMA_VERSION,
    LEGACY_ADAPTER_SCHEMA_VERSION,
    CommandAdapterError,
    direct_projection_manifest,
    direct_projection_source_field_errors,
    validate_adapter_spec,
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _issue(code: str, message: str, **context: Any) -> dict[str, Any]:
    payload = {"code": code, "message": message}
    payload.update({key: value for key, value in context.items() if value is not None})
    return payload


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


_REPLAY_ATTESTATION_FIELDS = (
    "success",
    "status",
    "collected_sample_count",
    "sample_count",
    "standard_records_verified",
    "primary_keys_verified",
    "quality_status_verified",
    "rule_results_verified",
    "evidence_summary_verified",
    "evidence_digest",
)


def _sanitize_replay_attestation(value: Any) -> dict[str, Any]:
    """Keep only the fixed proof summary; raw evidence must never enter audit/UI."""

    raw = _as_mapping(value)
    return {
        field: raw[field]
        for field in _REPLAY_ATTESTATION_FIELDS
        if field in raw
    }


def _canonical_rows(check_item: Mapping[str, Any]) -> list[dict[str, Any]]:
    contract = _as_mapping(check_item.get("canonical_contract"))
    return [
        dict(item)
        for item in contract.get("observations") or []
        if isinstance(item, Mapping)
        and str(item.get("canonical_field") or "").strip()
    ]


def _primary_fields(check_item: Mapping[str, Any]) -> list[str]:
    contract = _as_mapping(check_item.get("canonical_contract"))
    return [
        str(value)
        for value in (
            contract.get("primary_keys")
            or contract.get("identity_dimensions")
            or []
        )
        if str(value).strip() and str(value) != "device_id"
    ]


def _selected_check_item(
    raw: Mapping[str, Any],
    *,
    check_item_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    item = dict(raw)
    item_id = str(check_item_id or item.get("id") or item.get("check_item_id") or "").strip()
    if not item_id:
        raise ValueError("selected CheckItem id is required")
    version_id = str(item.get("version_id") or item.get("check_item_version_id") or "").strip()
    if not version_id:
        raise ValueError("selected CheckItem version_id is required")
    contract = _as_mapping(item.get("canonical_contract"))
    if not contract:
        raise ValueError("selected CheckItem canonical_contract is required")
    if not _canonical_rows({"canonical_contract": contract}):
        raise ValueError("selected CheckItem must declare canonical observations")
    return item_id, {"version_id": version_id, "canonical_contract": contract}


def build_projection_generation_context(
    *,
    check_item_id: str,
    check_item: Mapping[str, Any],
    parser_template_id: str,
    source_record_set: str,
    parser_source_fields: Sequence[str],
) -> dict[str, Any]:
    """Build the only context that a projection candidate generator may see.

    Raw CLI and parsed values are intentionally excluded.  A model can choose
    a binding among known Parser columns, but cannot mint a semantic field or
    observation type outside the selected CheckItem contract.
    """

    item_id, item = _selected_check_item(check_item, check_item_id=check_item_id)
    template_id = str(parser_template_id or "").strip()
    record_set = str(source_record_set or "").strip()
    source_fields = sorted(
        {
            str(field).strip()
            for field in parser_source_fields
            if str(field).strip()
        }
    )
    if not template_id:
        raise ValueError("Parser template_id is required")
    if not record_set:
        raise ValueError("Parser source_record_set is required")
    if not source_fields:
        raise ValueError("Parser source fields are required")

    rows = _canonical_rows(item)
    primary_fields = _primary_fields(item)
    canonical_by_field = {
        str(row["canonical_field"]): row
        for row in rows
    }
    allowed_target_fields = sorted(set(primary_fields) | set(canonical_by_field))
    required_target_fields = sorted(
        set(primary_fields)
        | {
            field
            for field, row in canonical_by_field.items()
            if row.get("required", True) is not False
        }
    )
    return {
        "schema_version": "adapter-projection-generation/v1",
        "selected_check_item": {
            "id": item_id,
            "version_id": item["version_id"],
            "entity_type": str(
                item["canonical_contract"].get("canonical_entity_type")
                or item["canonical_contract"].get("entity_type")
                or item_id
            ),
            "identity_fields": primary_fields,
        },
        "parser": {
            "template_id": template_id,
            "source_record_set": record_set,
            "source_fields": source_fields,
        },
        "allowed_target_fields": allowed_target_fields,
        "required_target_fields": required_target_fields,
        "observation_targets": [
            {
                key: row.get(key)
                for key in (
                    "canonical_field",
                    "observation_type",
                    "semantic_type",
                    "value_type",
                    "unit",
                    "canonical_unit",
                    "allowed_values",
                    "dimensions",
                    "mapping_guidance",
                    "policy_constraints",
                )
                if row.get(key) is not None
            }
            for row in rows
        ],
    }


def _candidate_spec(
    candidate: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    raw = dict(candidate)
    selected = _as_mapping(context.get("selected_check_item"))
    parser = _as_mapping(context.get("parser"))
    if raw.get("outputs") is not None or raw.get("schema_version") is not None:
        return raw
    projection = _as_mapping(raw.get("projection"))
    fields = projection.get("fields") if projection else raw.get("fields")
    return {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "parser": {"template_id": parser.get("template_id")},
        "outputs": [
            {
                "check_item_id": selected.get("id"),
                "check_item_version_id": selected.get("version_id"),
                "source_record_set": parser.get("source_record_set"),
                "projection": {"fields": fields or {}},
            }
        ],
    }


def _enum_mapping_errors(
    adapter_spec: Mapping[str, Any],
    *,
    check_item: Mapping[str, Any],
) -> list[str]:
    rows = {
        str(item["canonical_field"]): item
        for item in _canonical_rows(check_item)
    }
    errors: list[str] = []
    for output in adapter_spec.get("outputs") or []:
        if not isinstance(output, Mapping):
            continue
        fields = _as_mapping(_as_mapping(output.get("projection")).get("fields"))
        for field, declaration in rows.items():
            if not declaration.get("allowed_values") or field not in fields:
                continue
            binding = _as_mapping(fields[field])
            if not binding.get("value_mapping"):
                errors.append(
                    f"enum target {field} requires an explicit fail-closed value_mapping"
                )
            elif str(binding.get("on_unmapped") or "reject").casefold() != "reject":
                errors.append(
                    f"enum target {field} must use on_unmapped=reject"
                )
    return errors


def _policy_dimension_errors(
    adapter_spec: Mapping[str, Any],
    *,
    check_item: Mapping[str, Any],
) -> list[str]:
    """Reject a static dimension value which contradicts the CheckItem policy.

    The projection is allowed to select a Parser source for a dimension when
    its value is only known at collection time.  A literal is different: it is
    a concrete A→B claim and must already satisfy the selected CheckItem's
    declared policy scope.  Keeping this check here makes the same invariant
    apply to both generated and manually edited projections.
    """

    rows = {
        str(row["canonical_field"]): row
        for row in _canonical_rows(check_item)
    }
    errors: list[str] = []
    for output in adapter_spec.get("outputs") or []:
        if not isinstance(output, Mapping):
            continue
        fields = _as_mapping(_as_mapping(output.get("projection")).get("fields"))
        for field, declaration in rows.items():
            constraints = _as_mapping(
                _as_mapping(declaration.get("policy_constraints")).get(
                    "applies_to_dimensions"
                )
            )
            binding = _as_mapping(fields.get(field))
            dimensions = _as_mapping(binding.get("dimensions"))
            for dimension, permitted_values in constraints.items():
                permitted = {
                    str(value)
                    for value in permitted_values
                    if str(value).strip()
                } if isinstance(permitted_values, Sequence) and not isinstance(
                    permitted_values, (str, bytes)
                ) else set()
                if not permitted:
                    continue
                resolved = _as_mapping(dimensions.get(str(dimension)))
                if set(resolved) == {"literal"} and str(resolved["literal"]) not in permitted:
                    errors.append(
                        f"{field} dimension {dimension} literal "
                        f"{resolved['literal']!r} is not permitted by CheckItem policy "
                        f"({', '.join(sorted(permitted))})"
                    )
    return errors


def validate_projection_candidate(
    candidate: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    check_item: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a candidate against one frozen CheckItem and Parser schema."""

    selected = _as_mapping(context.get("selected_check_item"))
    parser = _as_mapping(context.get("parser"))
    check_item_id = str(selected.get("id") or "").strip()
    errors: list[str] = []
    if not check_item_id:
        return {"success": False, "errors": ["selected CheckItem id is missing"]}
    try:
        raw_spec = _candidate_spec(candidate, context=context)
        normalized = validate_adapter_spec(
            raw_spec,
            check_item_ids=(check_item_id,),
            check_items={check_item_id: check_item},
            require_pinned_check_item_versions=True,
        )
        if normalized.get("schema_version") != ADAPTER_SCHEMA_VERSION:
            errors.append("candidate must use command-adapter/v2")
        if str(normalized.get("parser", {}).get("template_id") or "") != str(
            parser.get("template_id") or ""
        ):
            errors.append("candidate Parser template differs from the selected Parser")
        output = next(
            (
                item
                for item in normalized.get("outputs") or []
                if str(item.get("check_item_id") or "") == check_item_id
            ),
            None,
        )
        if output is None:
            errors.append("candidate has no output for the selected CheckItem")
        elif str(output.get("source_record_set") or "") != str(
            parser.get("source_record_set") or ""
        ):
            errors.append("candidate source_record_set differs from the selected Parser record set")
        manifest = direct_projection_manifest(
            normalized,
            check_item_id=check_item_id,
            check_item=check_item,
            require_pinned_check_item_version=True,
        )
        errors.extend(
            direct_projection_source_field_errors(
                manifest,
                parser_field_contract={
                    "parser_spec_record_sets": {
                        str(parser.get("source_record_set") or ""): list(
                            parser.get("source_fields") or []
                        )
                    }
                },
            )
        )
        errors.extend(_enum_mapping_errors(normalized, check_item=check_item))
        errors.extend(_policy_dimension_errors(normalized, check_item=check_item))
    except (CommandAdapterError, ValueError) as exc:
        return {"success": False, "errors": [str(exc)]}
    return {
        "success": not errors,
        "errors": list(dict.fromkeys(errors)),
        "adapter_spec": normalized,
        "projection_manifest": manifest,
    }


def deterministic_projection_candidate(
    *,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Offer only exact-name bindings; aliases require a human/LLM proposal."""

    parser = _as_mapping(context.get("parser"))
    source_fields = {str(item) for item in parser.get("source_fields") or []}
    fields = {
        field: {"source": field}
        for field in context.get("allowed_target_fields") or []
        if str(field) in source_fields
    }
    return _candidate_spec({"fields": fields}, context=context)


def generate_projection_candidate(
    params: Mapping[str, Any], *, execution_validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate or validate a V2 projection candidate with a three-round cap."""

    raw = dict(params)
    selected_raw = _as_mapping(
        raw.get("selected_check_item") or raw.get("check_item")
    )
    check_item_id = str(raw.get("check_item_id") or selected_raw.get("id") or "").strip()
    parser_raw = _as_mapping(raw.get("parser"))
    context = build_projection_generation_context(
        check_item_id=check_item_id,
        check_item=selected_raw,
        parser_template_id=str(
            parser_raw.get("template_id")
            or raw.get("parser_template_id")
            or ""
        ),
        source_record_set=str(
            parser_raw.get("source_record_set")
            or raw.get("source_record_set")
            or ""
        ),
        parser_source_fields=(
            parser_raw.get("source_fields")
            or raw.get("parser_source_fields")
            or []
        ),
    )
    _, check_item = _selected_check_item(selected_raw, check_item_id=check_item_id)
    strict = check_item["canonical_contract"].get("value_contract") == "same-name/v1"
    def validate(candidate):
        validation = validate_projection_candidate(candidate, context=context, check_item=check_item)
        if validation.get("success") and execution_validator:
            executed = execution_validator(validation["adapter_spec"])
            validation["execution"] = executed
            if executed.get("success") is not True:
                validation["success"] = False
                validation["errors"] = [*validation.get("errors", []), *executed.get("errors", [])]
        return validation
    mode = str(raw.get("mode") or "projection_candidate").strip().casefold()
    proposal = _as_mapping(raw.get("proposal") or raw.get("adapter_spec"))
    if strict:
        from src.core.check_items.value_contract import same_name_fields
        try:
            fields = same_name_fields(check_item["canonical_contract"], context["parser"]["source_fields"])
            direct = {"schema_version": "command-adapter/v2", "parser": {"template_id": context["parser"]["template_id"]}, "outputs": [{"check_item_id": check_item_id, "check_item_version_id": check_item["version_id"], "source_record_set": context["parser"]["source_record_set"], "projection": {"fields": fields}}]}
            if proposal and proposal != direct:
                raise ValueError("同名 Value 契约不允许改名或转换，请修正 TextFSM")
            validation = validate(direct)
            return {"success": validation["success"], "mode": "same_name_values", "proposal": direct, "validation": validation, "generation_provider": "same_name", "generation_context": context}
        except ValueError as exc:
            return {"success": False, "mode": "same_name_values", "proposal": {}, "validation": {"success": False, "errors": [str(exc)]}, "generation_provider": "same_name"}
    if mode in {"projection_validate", "validate_projection"}:
        if not proposal:
            raise ValueError("projection_validate requires proposal or adapter_spec")
        validation = validate(proposal)
        return {
            "success": validation["success"],
            "mode": "projection_validate",
            "proposal": validation.get("adapter_spec") or proposal,
            "validation": validation,
            "generation_context": context,
        }

    provider_name = str(raw.get("generation_provider") or "").strip().casefold()
    if provider_name == "llm":
        generated = RuleGenerationAgent(
            DEFAULT_RULE_GENERATION_PROVIDERS.create(provider_name),
            max_attempts=min(3, max(1, int(raw.get("max_agent_attempts") or 3))),
        ).run(
            stage="adapter_projection",
            context=context,
            validator=validate,
        )
        validation = dict(generated.get("validation") or {})
        return {
            "success": generated.get("success") is True,
            "mode": "projection_candidate",
            "proposal": validation.get("adapter_spec") or generated.get("candidate"),
            "validation": validation,
            "agent_loop": generated.get("agent_loop"),
            "generation_provider": provider_name,
            "generation_context": context,
        }

    candidate = proposal or deterministic_projection_candidate(context=context)
    validation = validate(candidate)
    return {
        "success": validation["success"],
        "mode": "projection_candidate",
        "proposal": validation.get("adapter_spec") or candidate,
        "validation": validation,
        "generation_context": context,
        "generation_provider": "deterministic",
    }


def _semantic_projection_for_target(
    contract: Mapping[str, Any],
    target: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Copy only explicit legacy semantic operations for an exact target field."""

    semantics = _as_mapping(contract.get("state_semantics"))
    matching = _as_mapping(semantics.get(target))
    candidates = [
        _as_mapping(value)
        for value in semantics.values()
        if isinstance(value, Mapping)
        and str(value.get("canonical_field") or "").strip() == target
    ]
    if not matching and len(candidates) == 1:
        matching = candidates[0]
    if not matching or len(candidates) > 1:
        return {}, (
            [
                _issue(
                    "legacy_semantic_mapping_ambiguous",
                    "旧 ObservationContract 对目标字段存在多个语义投影，不能安全迁移。",
                    canonical_field=target,
                )
            ]
            if len(candidates) > 1
            else []
        )

    copied: dict[str, Any] = {}
    blockers: list[dict[str, Any]] = []
    for key in (
        "transform",
        "value_mapping",
        "value_mapping_mode",
        "on_unmapped",
        "source_unit",
        "canonical_unit",
        "dimensions",
    ):
        if key in matching:
            copied[key] = json.loads(_canonical_json(matching[key]))
    alias_mappings = [
        key for key in ("enum_mapping", "enum_map") if key in matching
    ]
    if alias_mappings and "value_mapping" in copied:
        blockers.append(
            _issue(
                "legacy_enum_mapping_ambiguous",
                "旧 ObservationContract 同时声明多种枚举映射格式，不能猜测优先级。",
                canonical_field=target,
            )
        )
    elif len(alias_mappings) == 1:
        copied["value_mapping"] = json.loads(
            _canonical_json(matching[alias_mappings[0]])
        )
        copied["value_mapping_mode"] = str(
            matching.get("value_mapping_mode") or "casefold"
        )
        copied["on_unmapped"] = str(matching.get("on_unmapped") or "reject")
    elif len(alias_mappings) > 1:
        blockers.append(
            _issue(
                "legacy_enum_mapping_ambiguous",
                "旧 ObservationContract 声明多个枚举映射别名，不能猜测。",
                canonical_field=target,
            )
        )
    return copied, blockers


def _sample_replay_blockers(sample_replay: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    replay = _sanitize_replay_attestation(sample_replay)
    if not replay:
        return [
            _issue(
                "sample_replay_missing",
                "缺少已收集原始 CLI 的预发布回放证明，禁止发布切换。",
            )
        ]
    passed = replay.get("success") is True or str(replay.get("status") or "").casefold() in {
        "passed",
        "complete",
        "ready",
    }
    if not passed:
        return [
            _issue(
                "sample_replay_failed",
                "原始 CLI 回放未通过，禁止发布切换。",
                replay_status=replay.get("status"),
            )
        ]
    try:
        sample_count = int(replay.get("sample_count") or 0)
    except (TypeError, ValueError):
        sample_count = 0
    try:
        collected_sample_count = int(replay.get("collected_sample_count") or 0)
    except (TypeError, ValueError):
        collected_sample_count = 0
    if collected_sample_count < 1:
        return [
            _issue(
                "sample_replay_inventory_missing",
                "回放证明必须声明该实现已收集的原始 CLI 样本总数。",
            )
        ]
    if sample_count != collected_sample_count:
        return [
            _issue(
                "sample_replay_coverage_incomplete",
                "回放样本数必须与已收集原始 CLI 样本数完全一致。",
                sample_count=sample_count,
                collected_sample_count=collected_sample_count,
            )
        ]
    if sample_count < 1:
        return [
            _issue(
                "sample_replay_evidence_missing",
                "回放证明必须至少覆盖一条已收集原始 CLI。",
            )
        ]
    verification_fields = (
        "standard_records_verified",
        "primary_keys_verified",
        "quality_status_verified",
        "rule_results_verified",
        "evidence_summary_verified",
    )
    missing_verifications = [
        field for field in verification_fields if replay.get(field) is not True
    ]
    if missing_verifications:
        return [
            _issue(
                "sample_replay_verification_incomplete",
                "回放证明必须确认标准记录、主键、质量、规则和证据摘要。",
                missing_verifications=missing_verifications,
            )
        ]
    digest = str(
        replay.get("evidence_digest") or replay.get("digest") or ""
    ).strip()
    if len(digest) != 64:
        return [
            _issue(
                "sample_replay_digest_missing",
                "回放证明必须提供不可变的 64 位 evidence_digest。",
            )
        ]
    return []


def migrate_legacy_adapter_to_v2(
    legacy_adapter_spec: Mapping[str, Any] | None,
    *,
    check_items: Mapping[str, Mapping[str, Any]],
    observation_contracts_by_check_item: Mapping[str, Mapping[str, Any]] | None = None,
    parser_field_contract: Mapping[str, Any] | None = None,
    sample_replay: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce a V2 candidate only where every copied semantic is explicit.

    The returned candidate is not persisted.  Any ambiguous source field,
    unpinned CheckItem, parser-schema drift or missing replay evidence remains a
    blocker, so callers cannot accidentally turn a best-effort conversion into
    a production release.
    """

    raw = _as_mapping(legacy_adapter_spec)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not raw:
        return {
            "success": False,
            "status": "blocked",
            "adapter_spec": None,
            "blockers": [
                _issue(
                    "legacy_adapter_missing",
                    "活动结构化实现没有 CommandAdapter，无法无歧义生成 V2 Projection。",
                ),
                *_sample_replay_blockers(sample_replay),
            ],
            "warnings": [],
        }
    try:
        normalized_legacy = validate_adapter_spec(
            raw,
            check_item_ids=tuple(check_items),
            check_items=check_items,
        )
    except CommandAdapterError as exc:
        return {
            "success": False,
            "status": "blocked",
            "adapter_spec": None,
            "blockers": [
                _issue("legacy_adapter_invalid", str(exc)),
                *_sample_replay_blockers(sample_replay),
            ],
            "warnings": [],
        }

    if normalized_legacy.get("schema_version") == ADAPTER_SCHEMA_VERSION:
        candidate = normalized_legacy
    elif normalized_legacy.get("schema_version") != LEGACY_ADAPTER_SCHEMA_VERSION:
        return {
            "success": False,
            "status": "blocked",
            "adapter_spec": None,
            "blockers": [
                _issue(
                    "legacy_adapter_schema_unsupported",
                    "无法迁移未知的 CommandAdapter schema。",
                    schema_version=normalized_legacy.get("schema_version"),
                )
            ],
            "warnings": [],
        }
    else:
        contracts = {
            str(key): _as_mapping(value)
            for key, value in dict(observation_contracts_by_check_item or {}).items()
        }
        outputs: list[dict[str, Any]] = []
        unsafe = False
        for output in normalized_legacy.get("outputs") or []:
            check_item_id = str(output.get("check_item_id") or "").strip()
            check_item = _as_mapping(check_items.get(check_item_id))
            if not check_item:
                blockers.append(
                    _issue(
                        "check_item_missing",
                        "旧 Adapter 输出未找到活动 CheckItem，不能迁移。",
                        check_item_id=check_item_id,
                    )
                )
                unsafe = True
                continue
            version_id = str(
                check_item.get("version_id")
                or check_item.get("check_item_version_id")
                or ""
            ).strip()
            if not version_id:
                blockers.append(
                    _issue(
                        "check_item_version_missing",
                        "V2 Projection 必须固定绑定 CheckItem 版本。",
                        check_item_id=check_item_id,
                    )
                )
                unsafe = True
                continue
            legacy_fields = _as_mapping(output.get("fields"))
            projection_fields: dict[str, Any] = {}
            for target, binding in legacy_fields.items():
                target_name = str(target).strip()
                base = _as_mapping(binding) if isinstance(binding, Mapping) else {"source": binding}
                semantics, semantic_blockers = _semantic_projection_for_target(
                    contracts.get(check_item_id, {}),
                    target_name,
                )
                blockers.extend(semantic_blockers)
                if semantic_blockers:
                    unsafe = True
                if semantics.get("transform") is not None and "expression" in base:
                    blockers.append(
                        _issue(
                            "legacy_transform_composition_ambiguous",
                            "旧 Adapter 表达式与 ObservationContract transform 不能安全自动组合。",
                            check_item_id=check_item_id,
                            canonical_field=target_name,
                        )
                    )
                    unsafe = True
                projection_fields[target_name] = {**base, **semantics}
            outputs.append(
                {
                    "check_item_id": check_item_id,
                    "check_item_version_id": version_id,
                    "source_record_set": str(output.get("source_record_set") or ""),
                    "empty_status": str(output.get("empty_status") or "unavailable"),
                    "projection": {"fields": projection_fields},
                }
            )
        candidate = {
            "schema_version": ADAPTER_SCHEMA_VERSION,
            "parser": normalized_legacy.get("parser"),
            "outputs": outputs,
        }
        if unsafe:
            candidate = None

    if candidate is not None:
        for output in candidate.get("outputs") or []:
            item_id = str(output.get("check_item_id") or "")
            item = _as_mapping(check_items.get(item_id))
            if not item:
                continue
            try:
                context = build_projection_generation_context(
                    check_item_id=item_id,
                    check_item=item,
                    parser_template_id=str(
                        _as_mapping(candidate.get("parser")).get("template_id") or ""
                    ),
                    source_record_set=str(output.get("source_record_set") or ""),
                    parser_source_fields=_parser_fields_for_record_set(
                        parser_field_contract,
                        str(output.get("source_record_set") or ""),
                    ),
                )
            except ValueError as exc:
                blockers.append(_issue("parser_field_contract_missing", str(exc)))
                continue
            validation = validate_projection_candidate(
                {
                    "schema_version": ADAPTER_SCHEMA_VERSION,
                    "parser": candidate.get("parser"),
                    "outputs": [output],
                },
                context=context,
                check_item=item,
            )
            blockers.extend(
                _issue("projection_validation_failed", error, check_item_id=item_id)
                for error in validation.get("errors") or []
            )
    blockers.extend(_sample_replay_blockers(sample_replay))
    deduplicated_blockers = list(
        {
            _canonical_json(item): item
            for item in blockers
        }.values()
    )
    return {
        "success": candidate is not None and not deduplicated_blockers,
        "status": "ready" if candidate is not None and not deduplicated_blockers else "blocked",
        "adapter_spec": candidate,
        "blockers": deduplicated_blockers,
        "warnings": warnings,
        "migration_digest": _digest(
            {
                "adapter_spec": candidate,
                "blockers": deduplicated_blockers,
                "warnings": warnings,
            }
        ),
    }


def _parser_fields_for_record_set(
    parser_field_contract: Mapping[str, Any] | None,
    record_set: str,
) -> list[str]:
    contract = _as_mapping(parser_field_contract)
    sets = _as_mapping(contract.get("parser_spec_record_sets")) or _as_mapping(
        contract.get("template_record_sets")
    )
    fields = sets.get(record_set)
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
        return []
    return [str(field) for field in fields if str(field).strip()]


def build_cutover_readiness_report(
    routes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply the all-or-nothing cutover gate to active structured routes.

    ``routes`` must already represent the complete active structured inventory.
    Supplying an empty or partial list never grants production-cutover approval.
    """

    reports: list[dict[str, Any]] = []
    for index, route in enumerate(routes):
        item = dict(route)
        result = migrate_legacy_adapter_to_v2(
            _as_mapping(item.get("adapter_spec")),
            check_items={
                str(key): _as_mapping(value)
                for key, value in _as_mapping(item.get("check_items")).items()
            },
            observation_contracts_by_check_item=_as_mapping(
                item.get("observation_contracts_by_check_item")
            ),
            parser_field_contract=_as_mapping(item.get("parser_field_contract")),
            sample_replay=_sanitize_replay_attestation(item.get("sample_replay")),
        )
        preflight_blockers = [
            dict(blocker)
            for blocker in item.get("preflight_blockers") or []
            if isinstance(blocker, Mapping)
        ]
        if preflight_blockers:
            result["blockers"] = list(
                {
                    _canonical_json(blocker): blocker
                    for blocker in [*result["blockers"], *preflight_blockers]
                }.values()
            )
            result["success"] = False
            result["status"] = "blocked"
        reports.append(
            {
                "route_id": str(
                    item.get("route_id")
                    or item.get("implementation_version_id")
                    or f"route-{index + 1}"
                ),
                "implementation_version_id": item.get("implementation_version_id"),
                "check_item_version_ids": sorted(
                    str(value.get("version_id") or "")
                    for value in _as_mapping(item.get("check_items")).values()
                    if str(value.get("version_id") or "")
                ),
                # This is a sanitized attestation only.  It intentionally
                # contains no raw CLI or parsed source values.
                "replay_attestation": _sanitize_replay_attestation(
                    item.get("sample_replay")
                ),
                **result,
            }
        )
    ready_count = sum(item["status"] == "ready" for item in reports)
    blockers = [
        {
            "route_id": item["route_id"],
            "blockers": item["blockers"],
        }
        for item in reports
        if item["status"] != "ready"
    ]
    return {
        "schema_version": "adapter-projection-cutover/v1",
        "route_count": len(reports),
        "ready_route_count": ready_count,
        "blocked_route_count": len(reports) - ready_count,
        "production_cutover_permitted": bool(reports) and ready_count == len(reports),
        "hard_gate": "all_active_structured_routes_parser_projection_and_raw_cli_replay",
        "routes": reports,
        "blockers": blockers,
        "digest": _digest(
            {
                "routes": reports,
                "hard_gate": "all_active_structured_routes_parser_projection_and_raw_cli_replay",
            }
        ),
    }


class AdapterProjectionCutoverInventoryService:
    """Read the complete active structured inventory through the cutover gate.

    Replay evidence is passed in from the pre-release replay pipeline rather
    than stored in a new mapping table.  Sanitized attestations are retained in
    the existing immutable audit log so a refreshed UI sees the same evidence;
    raw CLI stays in the evidence store.  In the absence of an attestation every
    route remains blocked, which is the intended all-at-once production safety
    posture.
    """

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] | None = None,
    ) -> None:
        if session_scope is None:
            from src.infrastructure.db.postgres import get_db_session

            session_scope = get_db_session
        self._session_scope = session_scope

    def readiness(
        self,
        *,
        replay_by_implementation_version: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Return a no-write inventory/report for every active structured route."""

        from src.apps.rule_governance.backend.check_items.runtime_bundle_assembly import (
            build_parser_field_contract,
        )
        from src.infrastructure.db.models import (
            AuditLogRecord,
            CheckItemImplementationLinkRecord,
            CheckItemRecord,
            CheckItemVersionRecord,
            CollectionImplementationParserBindingRecord,
            CollectionImplementationVersionRecord,
            ParserTemplate,
        )

        def freeze_row(row: Any, *fields: str) -> SimpleNamespace:
            """Copy the scalar/JSON fields needed after the session closes.

            ``get_db_session`` commits on scope exit, so SQLAlchemy expires
            loaded instances.  Readiness deliberately performs validation
            outside the transaction; it must therefore never retain ORM rows
            across that boundary.
            """

            values: dict[str, Any] = {}
            for field in fields:
                value = getattr(row, field)
                if isinstance(value, Mapping):
                    value = dict(value)
                elif isinstance(value, list):
                    value = list(value)
                values[field] = value
            return SimpleNamespace(**values)

        with self._session_scope() as db:
            implementations = (
                db.query(CollectionImplementationVersionRecord)
                .filter(
                    CollectionImplementationVersionRecord.status == "active",
                    CollectionImplementationVersionRecord.parser_requirement
                    == "structured",
                )
                .order_by(
                    CollectionImplementationVersionRecord.implementation_id,
                    CollectionImplementationVersionRecord.version,
                    CollectionImplementationVersionRecord.id,
                )
                .all()
            )
            implementation_ids = [str(item.id) for item in implementations]
            links = (
                db.query(CheckItemImplementationLinkRecord)
                .filter(
                    CheckItemImplementationLinkRecord.implementation_version_id.in_(
                        implementation_ids or [""]
                    )
                )
                .all()
            )
            linked_version_ids = [str(item.check_item_version_id) for item in links]
            check_versions = {
                str(item.id): item
                for item in (
                    db.query(CheckItemVersionRecord)
                    .filter(CheckItemVersionRecord.id.in_(linked_version_ids or [""]))
                    .all()
                )
            }
            check_roots = {
                str(item.id): item
                for item in (
                    db.query(CheckItemRecord)
                    .filter(
                        CheckItemRecord.id.in_(
                            [
                                str(version.check_item_id)
                                for version in check_versions.values()
                            ]
                            or [""]
                        )
                    )
                    .all()
                )
            }
            bindings = (
                db.query(CollectionImplementationParserBindingRecord)
                .filter(
                    CollectionImplementationParserBindingRecord.implementation_version_id.in_(
                        implementation_ids or [""]
                    ),
                    CollectionImplementationParserBindingRecord.status == "active",
                )
                .all()
            )
            parser_ids = [
                str(item.parser_template_id)
                for item in bindings
                if item.parser_template_id
            ]
            templates = {
                str(item.id): item
                for item in (
                    db.query(ParserTemplate)
                    .filter(ParserTemplate.id.in_(parser_ids or [""]))
                    .all()
                )
            }
            audit_rows = (
                db.query(AuditLogRecord)
                .filter(
                    AuditLogRecord.action.in_(
                        (
                            "adapter_projection_replay_attested",
                            "adapter_projection_cutover_authorized",
                        )
                    )
                )
                .order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
                .all()
            )
            implementations = [
                freeze_row(item, "id", "adapter_spec_json")
                for item in implementations
            ]
            links = [
                freeze_row(
                    item,
                    "implementation_version_id",
                    "check_item_version_id",
                )
                for item in links
            ]
            check_versions = {
                str(item.id): freeze_row(
                    item,
                    "id",
                    "check_item_id",
                    "status",
                    "canonical_contract_json",
                )
                for item in check_versions.values()
            }
            check_roots = {
                str(item.id): freeze_row(item, "id")
                for item in check_roots.values()
            }
            bindings = [
                freeze_row(
                    item,
                    "implementation_version_id",
                    "parser_template_id",
                )
                for item in bindings
            ]
            templates = {
                str(item.id): freeze_row(
                    item,
                    "id",
                    "validation_json",
                    "template_text",
                    "status",
                )
                for item in templates.values()
            }
            audit_rows = [
                freeze_row(
                    item,
                    "id",
                    "action",
                    "detail",
                    "resource_id",
                    "username",
                    "created_at",
                )
                for item in audit_rows
            ]

        replay_index: dict[str, dict[str, Any]] = {}
        replay_sources: dict[str, str] = {}
        latest_authorization: dict[str, Any] | None = None
        for audit in audit_rows:
            detail = _as_mapping(audit.detail)
            action = str(audit.action or "")
            if action == "adapter_projection_replay_attested":
                implementation_id = str(audit.resource_id or "").strip()
                attestation = _sanitize_replay_attestation(
                    detail.get("attestation")
                )
                if implementation_id and attestation and implementation_id not in replay_index:
                    replay_index[implementation_id] = attestation
                    replay_sources[implementation_id] = "audit"
            elif (
                action == "adapter_projection_cutover_authorized"
                and latest_authorization is None
            ):
                latest_authorization = {
                    "authorization_id": str(audit.id),
                    "actor": str(audit.username or ""),
                    "authorized_at": (
                        audit.created_at.isoformat()
                        if getattr(audit, "created_at", None) is not None
                        else None
                    ),
                    **detail,
                }
        for key, value in dict(replay_by_implementation_version or {}).items():
            implementation_id = str(key).strip()
            if implementation_id:
                replay_index[implementation_id] = _sanitize_replay_attestation(
                    value
                )
                replay_sources[implementation_id] = "request"

        links_by_implementation: dict[str, list[Any]] = {}
        for link in links:
            links_by_implementation.setdefault(
                str(link.implementation_version_id), []
            ).append(link)
        bindings_by_implementation: dict[str, list[Any]] = {}
        for binding in bindings:
            bindings_by_implementation.setdefault(
                str(binding.implementation_version_id), []
            ).append(binding)
        routes: list[dict[str, Any]] = []
        for implementation in implementations:
            implementation_id = str(implementation.id)
            linked = links_by_implementation.get(implementation_id, [])
            check_items: dict[str, dict[str, Any]] = {}
            preflight: list[dict[str, Any]] = []
            for link in linked:
                version = check_versions.get(str(link.check_item_version_id))
                root = (
                    check_roots.get(str(version.check_item_id))
                    if version is not None
                    else None
                )
                if version is None or root is None:
                    preflight.append(
                        _issue(
                            "linked_check_item_missing",
                            "活动结构化实现绑定了不存在的 CheckItem 版本。",
                            check_item_version_id=str(link.check_item_version_id),
                        )
                    )
                    continue
                if str(version.status) != "active":
                    preflight.append(
                        _issue(
                            "linked_check_item_not_active",
                            "活动结构化实现绑定的 CheckItem 版本不是 Active。",
                            check_item_version_id=str(version.id),
                        )
                    )
                check_items[str(root.id)] = {
                    "version_id": str(version.id),
                    "canonical_contract": dict(version.canonical_contract_json or {}),
                }
            if not linked:
                preflight.append(
                    _issue(
                        "structured_implementation_unlinked",
                        "活动结构化实现没有绑定 CheckItem 版本。",
                    )
                )

            adapter_spec = dict(implementation.adapter_spec_json or {})
            adapter_parser_id = str(
                _as_mapping(adapter_spec.get("parser")).get("template_id") or ""
            ).strip()
            active_bindings = bindings_by_implementation.get(implementation_id, [])
            bound_parser_ids = {
                str(item.parser_template_id)
                for item in active_bindings
                if item.parser_template_id
            }
            parser_id = adapter_parser_id or (
                next(iter(bound_parser_ids)) if len(bound_parser_ids) == 1 else ""
            )
            if adapter_parser_id and adapter_parser_id not in bound_parser_ids:
                preflight.append(
                    _issue(
                        "adapter_parser_binding_missing",
                        "CommandAdapter 绑定的 Parser 不在活动实现命令路由中。",
                        parser_template_id=adapter_parser_id,
                    )
                )
            if not parser_id:
                preflight.append(
                    _issue(
                        "structured_parser_route_missing",
                        "活动结构化实现没有唯一的活动 Parser 路由。",
                    )
                )
            template = templates.get(parser_id)
            parser_field_contract = (
                build_parser_field_contract(
                    validation=dict(template.validation_json or {}),
                    template_text=str(template.template_text or ""),
                )
                if template is not None
                else {}
            )
            if template is None and parser_id:
                preflight.append(
                    _issue(
                        "adapter_parser_missing",
                        "活动 Parser 路由引用的模板不存在。",
                        parser_template_id=parser_id,
                    )
                )
            if template is not None and str(template.status) != "active":
                preflight.append(
                    _issue(
                        "adapter_parser_not_active",
                        "活动实现路由的 Parser 模板不是 Active。",
                        parser_template_id=parser_id,
                    )
                )

            routes.append(
                {
                    "route_id": implementation_id,
                    "implementation_version_id": implementation_id,
                    "adapter_spec": adapter_spec,
                    "check_items": check_items,
                    "parser_field_contract": parser_field_contract,
                    "sample_replay": replay_index.get(implementation_id, {}),
                    "preflight_blockers": preflight,
                }
            )
        report = build_cutover_readiness_report(routes)
        report["inventory_scope"] = {
            "active_structured_implementation_count": len(implementations),
            "replay_attestation_count": len(replay_index),
        }
        report["replay_attestation_sources"] = replay_sources
        if latest_authorization is not None:
            report["cutover_authorization"] = {
                **latest_authorization,
                "matches_current_readiness": (
                    str(latest_authorization.get("readiness_digest") or "")
                    == str(report["digest"])
                ),
            }
        else:
            report["cutover_authorization"] = None
        return report

    def record_replay_attestations(
        self,
        *,
        replay_by_implementation_version: Mapping[str, Mapping[str, Any]],
        actor: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Append sanitized replay proof to the existing immutable audit log."""

        actor = str(actor or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        attestations: dict[str, dict[str, Any]] = {}
        for key, value in dict(replay_by_implementation_version or {}).items():
            implementation_id = str(key).strip()
            attestation = _sanitize_replay_attestation(value)
            if implementation_id and attestation:
                attestations[implementation_id] = attestation
        if not actor:
            raise ValueError("replay attestation actor is required")
        if not idempotency_key:
            raise ValueError("replay attestation idempotency key is required")
        if not attestations:
            raise ValueError("at least one replay attestation is required")

        preflight = self.readiness(
            replay_by_implementation_version=attestations,
        )
        known_ids = {
            str(item.get("implementation_version_id") or "")
            for item in preflight.get("routes") or []
            if str(item.get("implementation_version_id") or "")
        }
        unknown_ids = sorted(set(attestations) - known_ids)
        if unknown_ids:
            raise ValueError(
                "replay attestation references a non-active structured "
                f"implementation: {', '.join(unknown_ids)}"
            )

        from src.infrastructure.db.models import AuditLogRecord

        now = datetime.now(timezone.utc)
        recorded: list[str] = []
        with self._session_scope() as db:
            for implementation_id, attestation in sorted(attestations.items()):
                attestation_digest = _digest(attestation)
                audit_key = (
                    "adapter-projection-replay:"
                    f"{idempotency_key}:{implementation_id}"
                )
                existing = (
                    db.query(AuditLogRecord)
                    .filter(AuditLogRecord.idempotency_key == audit_key)
                    .first()
                )
                if existing is not None:
                    detail = _as_mapping(existing.detail)
                    if str(detail.get("attestation_digest") or "") != attestation_digest:
                        raise ValueError(
                            "idempotency key was already used with a different "
                            "replay attestation"
                        )
                    recorded.append(implementation_id)
                    continue
                db.add(
                    AuditLogRecord(
                        id=str(uuid.uuid4()),
                        idempotency_key=audit_key,
                        username=actor,
                        action="adapter_projection_replay_attested",
                        resource_type="collection_implementation_version",
                        resource_id=implementation_id,
                        detail={
                            "schema_version": "adapter-projection-replay-attestation/v1",
                            "attestation": attestation,
                            "attestation_digest": attestation_digest,
                            "preflight_digest": str(preflight.get("digest") or ""),
                        },
                        status="success",
                        created_at=now,
                    )
                )
                recorded.append(implementation_id)
            db.flush()

        report = self.readiness()
        report["recorded_implementation_version_ids"] = recorded
        return report

    def authorize_cutover(
        self,
        *,
        actor: str,
        reason: str,
        expected_readiness_digest: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Record a human authorization only after the global hard gate passes.

        Runtime retirement is deliberately not toggled inside one API process:
        it is a deployment-wide setting and must change atomically on every
        production worker.  This authorization supplies the audited, immutable
        readiness digest that the deployment uses.
        """

        actor = str(actor or "").strip()
        reason = str(reason or "").strip()
        expected_readiness_digest = str(expected_readiness_digest or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        if not actor:
            raise ValueError("cutover authorization actor is required")
        if not reason:
            raise ValueError("cutover authorization reason is required")
        if not idempotency_key:
            raise ValueError("cutover authorization idempotency key is required")

        report = self.readiness()
        if str(report.get("digest") or "") != expected_readiness_digest:
            raise ValueError(
                "cutover readiness changed; refresh the all-route replay report"
            )
        if report.get("production_cutover_permitted") is not True:
            raise ValueError(
                "all active structured routes must have valid V2 projection and "
                "complete raw-CLI replay proof before cutover authorization"
            )

        from src.infrastructure.db.models import AuditLogRecord

        audit_key = f"adapter-projection-cutover:{idempotency_key}"
        detail = {
            "schema_version": "adapter-projection-cutover-authorization/v1",
            "readiness_digest": str(report["digest"]),
            "route_count": int(report["route_count"]),
            "reason": reason,
            "runtime_mode": "command-adapter/v2",
        }
        with self._session_scope() as db:
            existing = (
                db.query(AuditLogRecord)
                .filter(AuditLogRecord.idempotency_key == audit_key)
                .first()
            )
            if existing is not None:
                existing_detail = _as_mapping(existing.detail)
                if _digest(existing_detail) != _digest(detail):
                    raise ValueError(
                        "idempotency key was already used with a different "
                        "cutover authorization"
                    )
                authorization = {
                    "authorization_id": str(existing.id),
                    "actor": str(existing.username or ""),
                    "authorized_at": (
                        existing.created_at.isoformat()
                        if existing.created_at is not None
                        else None
                    ),
                    **existing_detail,
                    "idempotent_replay": True,
                }
            else:
                now = datetime.now(timezone.utc)
                row = AuditLogRecord(
                    id=str(uuid.uuid4()),
                    idempotency_key=audit_key,
                    username=actor,
                    action="adapter_projection_cutover_authorized",
                    resource_type="adapter_projection_cutover",
                    resource_id=str(report["digest"]),
                    detail=detail,
                    status="success",
                    created_at=now,
                )
                db.add(row)
                db.flush()
                authorization = {
                    "authorization_id": str(row.id),
                    "actor": actor,
                    "authorized_at": now.isoformat(),
                    **detail,
                    "idempotent_replay": False,
                }
        return {
            "success": True,
            "status": "authorized",
            "authorization": authorization,
            "readiness": report,
        }
