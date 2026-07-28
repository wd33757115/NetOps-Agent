"""Deterministic, read-only planning for CheckItemRuntimeBundle assembly."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from src.core.check_items.canonical import load_canonical_catalog
from src.core.observation.semantic_projection import (
    SemanticProjectionError,
    dimension_field_references,
    normalize_dimensions,
)
from src.core.patrol.event_rules import govern_evaluation_scope
from src.infrastructure.db.models import (
    ChangePolicyVersionRecord,
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
        if tuple(item["identity_fields"]) != expected_identity_fields:
            blockers.append(
                {
                    "code": "canonical_identity_dimensions_mismatch",
                    "message": (
                        "Contract 的规范实体身份维度与检查项目录不一致。"
                    ),
                    "record_set": item["name"],
                    "expected": list(expected_identity_fields),
                    "actual": list(item["identity_fields"]),
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

    implementation_vendor = str(selector.get("vendor") or "").strip().casefold()
    parser_vendor = str(manifest.get("vendor") or parser.vendor or "").strip().casefold()
    if not implementation_vendor:
        block(
            "implementation_vendor_missing",
            "采集实现未声明厂商，无法证明 Parser 兼容范围。",
        )
    elif implementation_vendor != parser_vendor:
        block(
            "parser_vendor_mismatch",
            f"采集实现厂商 {selector.get('vendor')} 与 Parser 厂商 "
            f"{manifest.get('vendor') or parser.vendor} 不一致。",
        )

    implementation_commands = {
        normalize_command(item.get("command"))
        for item in implementation.commands_json or []
        if isinstance(item, dict) and normalize_command(item.get("command"))
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
    parser_families = set(
        _selector_values(
            manifest,
            "family",
            "families",
        )
        or _string_values(parser.family)
    )
    parser_models = set(
        _selector_values(
            manifest,
            "model",
            "models",
        )
        or _string_values(parser.model)
    )
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
        if implementation_models:
            if not implementation_models.issubset(parser_models):
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
        if implementation_version.status != "active":
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

        commands = sorted(
            [
                {
                    "command": str(item.get("command") or "").strip(),
                    "canonical_command": normalize_command(item.get("command")),
                    "priority": int(item.get("priority") or 0),
                }
                for item in implementation_version.commands_json or []
                if isinstance(item, dict) and normalize_command(item.get("command"))
            ],
            key=lambda item: (-item["priority"], item["canonical_command"]),
        )
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
            available_contracts = [
                item
                for item in contracts_by_parser.get(parser_id, [])
                if not requested_contract_ids or str(item.id) in requested_contract_ids
            ]
            relevant_contracts = [
                item
                for item in available_contracts
                if contract_observation_types(item) & expected_types
            ]
            produced_counts = Counter(
                observation_type
                for contract in relevant_contracts
                for observation_type in contract_observation_types(contract)
                if observation_type in expected_types
            )
            duplicate_types = sorted(
                key for key, count in produced_counts.items() if count > 1
            )
            produced_types = {
                observation_type
                for contract in relevant_contracts
                for observation_type in contract_observation_types(contract)
            }
            missing_types = sorted(expected_types - produced_types)
            unapproved_types = sorted(produced_types - approved_types)
            route_blockers = list(compatibility["blockers"])
            for contract in relevant_contracts:
                route_blockers.extend(
                    contract_identity_blockers(
                        contract,
                        expected_entity_type=expected_entity_type,
                        expected_identity_fields=expected_identity_fields,
                        expected_observation_types=expected_types,
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
                        "message": "Contract 未覆盖全部检查项标准语义。",
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
                            parser_template_id=candidate[
                                "parser_template_id"
                            ],
                            observation_types=item.get(
                                "observation_types"
                            ),
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

        change_required = {
            observation_type
            for observation_type in expected_types
            if (
                (definition := canonical_catalog.get(observation_type)) is not None
                and "change" in definition.assessment_roles
            )
        }
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
        for observation_type in sorted(change_required):
            candidates = policies_by_type.get(observation_type, [])
            if len(candidates) == 1:
                selected_policy_ids.add(str(candidates[0].id))
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
            intersects = bool(set(observation_types) & expected_types)
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
            scope = govern_evaluation_scope(
                version.evaluation_scope,
                event_class=version.event_class,
            )
            candidate = {
                "event_rule_id": str(root.id),
                "event_rule_version_id": version_id,
                "name": root.name,
                "event_type": version.event_type,
                "event_class": version.event_class,
                "evaluation_scope": scope,
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
                        event_rule_version_id=version_id,
                        observation_types=item.get("observation_types"),
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

        current_required = {
            observation_type
            for observation_type in expected_types
            if (
                (definition := canonical_catalog.get(observation_type)) is not None
                and "current_state" in definition.assessment_roles
            )
        }
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
        fallback_resilience = (
            "not_applicable"
            if result_mode != "structured"
            else "full"
            if executable_count == len(command_routes) and executable_count > 0
            else "reduced"
            if executable_count > 0
            else "none"
        )
        if fallback_resilience == "reduced":
            warnings.append(
                _issue(
                    "fallback_resilience_reduced",
                    "运行路径可用，但部分声明命令缺少完整资产，fallback 韧性降低。",
                )
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
        compatibility = {
            "schema_version": 2,
            "implementation_selector": dict(implementation_version.match_json or {}),
            "expected_observation_types": sorted(expected_types),
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
            "executable_commands": [
                item["canonical_command"]
                for item in command_routes
                if item["status"] == "executable"
            ],
            "unavailable_fallbacks": [
                item["canonical_command"]
                for item in command_routes
                if item["status"] != "executable"
            ],
            "fallback_resilience": fallback_resilience,
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
            "fallback_resilience": fallback_resilience,
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
            "dependency_snapshot": {},
            "active_bundle": None,
            "drafts": [],
            "operationally_ready": False,
            "fallback_resilience": "none",
            "blockers": blockers,
            "warnings": warnings,
            "preview_digest": canonical_digest(payload),
        }
