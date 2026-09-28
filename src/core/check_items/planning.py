"""Check-item-centered effective planning and immutable runtime manifests."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.core.check_items.assessment import (
    CheckAssessmentInput,
    CheckAssessmentService,
)
from src.core.check_items.assessment_policy import (
    build_assessment_basis,
    build_target_assessment_policy_snapshot,
)
from src.core.check_items.assessment_policy_assets import AssessmentPolicyAssetService
from src.core.check_items.assessment_policy_migration import (
    AssessmentPolicyMigrationService,
)
from src.core.check_items.catalog import (
    CheckItemCatalog,
    CheckItemCatalogRepository,
)
from src.core.check_items.comparison import (
    BaselineCandidate,
    ComparisonContextService,
    ComparisonDecision,
    ProductionBaselineCandidateRepository,
    build_comparison_fact_contract,
)
from src.core.check_items.identity import load_entity_identity_policy
from src.core.check_items.resolver import CheckItemImplementationResolver
from src.core.check_items.runtime_bundle import (
    CheckItemRuntimeBundleService,
)
from src.core.check_items.scope import ScopeResolution, resolve_patrol_scope
from src.core.check_items.scope_repository import PatrolScopeRepository
from src.core.parser.policy import DEFAULT_COMMAND_ALIASES, ParserPolicy
from src.infrastructure.db.models import (
    AssetGapRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemCollectionActionRecord,
    CheckItemRuntimeManifestRecord,
    ComparisonContextRecord,
    PatrolBatchRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _canonical_command(value: Any) -> str:
    basic = ParserPolicy._basic_normalize(str(value or ""))
    return ParserPolicy._basic_normalize(DEFAULT_COMMAND_ALIASES.get(basic, basic))


def _device_id(device: Mapping[str, Any]) -> str:
    return str(device.get("device_id") or device.get("id") or "").strip()


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    aware = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc)


def build_target_manifest(
    *,
    devices: Iterable[Mapping[str, Any]],
    selector: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [
        {
            "device_id": _device_id(item),
            "device_name": item.get("device_name") or item.get("name"),
            "management_ip": item.get("management_ip") or item.get("ip"),
            "vendor": item.get("vendor"),
            "family": item.get("family"),
            "model": item.get("model"),
        }
        for item in devices
        if _device_id(item)
    ]
    rows.sort(key=lambda item: item["device_id"])
    payload = {
        "schema_version": "patrol-target/v2",
        "selector": dict(selector or {}),
        "devices": rows,
    }
    return {**payload, "digest": _digest(payload)}


def build_inventory_snapshot(devices: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = []
    for item in devices:
        if not _device_id(item):
            continue
        attributes = dict(
            item.get("attributes") or item.get("attributes_json") or {}
        )
        policy_bindings = list(
            attributes.get("assessment_policy_bindings")
            or attributes.get("assessment_policies")
            or []
        )
        rows.append(
            {
                "device_id": _device_id(item),
                "vendor": item.get("vendor"),
                "family": item.get("family"),
                "platform": item.get("platform"),
                "model": item.get("model"),
                "os_version": item.get("os_version"),
                "site": item.get("site"),
                "role": item.get("role"),
                "capabilities": sorted(
                    str(value)
                    for value in (
                        item.get("capabilities")
                        or attributes.get("capabilities")
                        or []
                    )
                ),
                "assessment_policy_source_count": len(policy_bindings),
                "assessment_policy_sources_digest": _digest(policy_bindings),
            }
        )
    rows.sort(key=lambda item: item["device_id"])
    payload = {
        "schema_version": "inventory-snapshot/v1",
        "devices": rows,
    }
    return {**payload, "digest": _digest(payload)}


@dataclass(frozen=True, slots=True)
class EffectivePlanRequest:
    patrol_batch_id: str
    devices: tuple[dict[str, Any], ...]
    check_item_ids: tuple[str, ...]
    target_manifest_digest: str
    inventory_snapshot_digest: str
    current_observed_at: datetime
    baseline_candidates: Mapping[
        tuple[str, str],
        tuple[BaselineCandidate, ...],
    ] = field(default_factory=dict)
    check_item_ids_by_device: Mapping[
        str,
        tuple[str, ...],
    ] = field(default_factory=dict)
    profile_version_ids_by_device: Mapping[
        str,
        str | None,
    ] = field(default_factory=dict)
    max_baseline_age_seconds: int | None = 30 * 24 * 60 * 60
    profile_version_id: str | None = None
    actor: str = "check-item-planner"
    report_purpose: str | None = None
    scope_contract: Mapping[str, Any] | None = None
    scope_exclusions: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    scope_not_applicable: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class EffectivePlan:
    patrol_batch_id: str
    manifest_id: str
    manifest_digest: str
    manifest: dict[str, Any]
    decisions: tuple[dict[str, Any], ...]
    collection_actions: tuple[dict[str, Any], ...]
    executions: tuple[dict[str, Any], ...]
    comparison_contexts: tuple[ComparisonDecision, ...]
    terminal_assessments: tuple[dict[str, Any], ...]
    asset_gaps: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "patrol_batch_id": self.patrol_batch_id,
            "manifest_id": self.manifest_id,
            "manifest_digest": self.manifest_digest,
            "manifest": dict(self.manifest),
            "decisions": [dict(item) for item in self.decisions],
            "collection_actions": [
                dict(item) for item in self.collection_actions
            ],
            "executions": [dict(item) for item in self.executions],
            "comparison_contexts": [
                item.to_record_values() for item in self.comparison_contexts
            ],
            "terminal_assessments": [
                dict(item) for item in self.terminal_assessments
            ],
            "asset_gaps": [dict(item) for item in self.asset_gaps],
        }


def _aggregate_asset_gaps(
    gaps: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Collapse device-level failures into one governed asset work item."""

    grouped: dict[str, dict[str, Any]] = {}
    for raw in gaps:
        gap = dict(raw)
        identity = {
            key: gap.get(key)
            for key in (
                "check_item_id",
                "check_item_version_id",
                "implementation_version_id",
                "canonical_command",
                "output_signature",
                "gap_type",
                "purpose",
                "reason_code",
            )
        }
        idempotency_key = _digest(identity)
        row = grouped.setdefault(
            idempotency_key,
            {
                **identity,
                "id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/asset-gap/{idempotency_key}",
                    )
                ),
                "idempotency_key": idempotency_key,
                "check_execution_ids": [],
                "device_ids": [],
                "metadata": {},
            },
        )
        execution_id = str(gap.get("check_execution_id") or "")
        device_id = str(gap.get("device_id") or "")
        if execution_id:
            row["check_execution_ids"].append(execution_id)
        if device_id:
            row["device_ids"].append(device_id)
        metadata = dict(gap.get("metadata") or {})
        row["metadata"]["all_reason_codes"] = sorted(
            {
                *(row["metadata"].get("all_reason_codes") or []),
                *(metadata.get("all_reason_codes") or []),
            }
        )

    result: list[dict[str, Any]] = []
    for key in sorted(grouped):
        row = grouped[key]
        execution_ids = sorted(set(row.pop("check_execution_ids")))
        device_ids = sorted(set(row.pop("device_ids")))
        metadata = {
            **dict(row.get("metadata") or {}),
            "affected_execution_count": len(execution_ids),
            "affected_device_count": len(device_ids),
            "sample_execution_ids": execution_ids[:20],
            "sample_device_ids": device_ids[:20],
        }
        result.append(
            {
                **row,
                "check_execution_id": (
                    execution_ids[0] if execution_ids else None
                ),
                "affected_execution_count": len(execution_ids),
                "affected_device_count": len(device_ids),
                "metadata": metadata,
            }
        )
    return tuple(result)


class CheckItemEffectivePlanService:
    """Resolve, freeze and persist all device x check-item decisions before dispatch."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
        *,
        catalog: CheckItemCatalog | None = None,
    ) -> None:
        self._session_scope = session_scope
        self._catalog = catalog

    def _load_catalog(self) -> CheckItemCatalog:
        return self._catalog or CheckItemCatalogRepository(
            session_scope=self._session_scope
        ).load_active()

    def preview(self, request: EffectivePlanRequest) -> EffectivePlan:
        if not request.patrol_batch_id.strip():
            raise ValueError("patrol_batch_id is required")
        if not request.devices:
            raise ValueError("effective plan requires at least one device")
        default_selected_ids = tuple(sorted(set(request.check_item_ids)))
        requested_by_device = {
            str(device_id): tuple(sorted(set(values)))
            for device_id, values in request.check_item_ids_by_device.items()
        }
        if not default_selected_ids and not requested_by_device:
            raise ValueError("effective plan requires at least one check item")
        if not request.target_manifest_digest or not request.inventory_snapshot_digest:
            raise ValueError("target and inventory manifest digests are required")

        catalog = self._load_catalog()
        item_by_id = {item.id: item for item in catalog.check_items}
        selected_ids_by_device: dict[str, tuple[str, ...]] = {}
        all_selected_ids: set[str] = set()
        for device in request.devices:
            device_id = _device_id(device)
            selected = requested_by_device.get(
                device_id,
                default_selected_ids,
            )
            if not selected:
                raise ValueError(
                    f"effective plan requires check items for device {device_id}"
                )
            selected_ids_by_device[device_id] = selected
            all_selected_ids.update(selected)
        missing = sorted(all_selected_ids - set(item_by_id))
        if missing:
            raise ValueError("unknown active check items: " + ", ".join(missing))
        scope_resolution = resolve_patrol_scope(
            contract=request.scope_contract,
            devices=request.devices,
            selected_check_item_ids_by_device=selected_ids_by_device,
            check_item_version_ids={
                item_id: item.version_id
                for item_id, item in item_by_id.items()
            },
            inventory_snapshot_digest=request.inventory_snapshot_digest,
            exclusions=request.scope_exclusions,
            not_applicable=request.scope_not_applicable,
        )
        implementation_by_version = {
            item.version_id: item for item in catalog.implementations
        }
        resolver = CheckItemImplementationResolver(catalog)
        bundle_service = CheckItemRuntimeBundleService(self._session_scope)
        active_bundle_index = bundle_service.get_active_many(
            (item_by_id[check_item_id].version_id, implementation.version_id)
            for implementation in catalog.implementations
            for check_item_id in implementation.check_item_ids
            if check_item_id in all_selected_ids
        )
        comparison_service = ComparisonContextService()
        identity_policy = load_entity_identity_policy()
        observed_at = _utc(request.current_observed_at)
        automatic_baseline_candidates: dict[
            tuple[str, str], tuple[BaselineCandidate, ...]
        ] = {}
        if str(request.report_purpose or "") == "production_current":
            automatic_baseline_candidates = (
                ProductionBaselineCandidateRepository(
                    self._session_scope
                ).load_many(
                    targets=(
                        (
                            device_id,
                            item_by_id[check_item_id].version_id,
                        )
                        for device_id, check_item_ids in (
                            selected_ids_by_device.items()
                        )
                        for check_item_id in check_item_ids
                    ),
                    current_observed_at=observed_at,
                    current_patrol_batch_id=request.patrol_batch_id,
                )
            )
        scope_policy_overrides = tuple(
            dict(item)
            for item in (
                dict(request.scope_contract or {}).get(
                    "assessment_policy_overrides",
                    [],
                )
                or []
            )
            if isinstance(item, Mapping)
        )
        governed_policy_sources = AssessmentPolicyAssetService(
            self._session_scope
        ).list_rendered_sources(check_item_ids=all_selected_ids)
        assessment_policy_cutovers = AssessmentPolicyMigrationService(
            self._session_scope
        ).effective_cutover_modes(
            (
                (item_by_id[check_item_id].version_id, device_id)
                for device_id, check_item_ids in selected_ids_by_device.items()
                for check_item_id in check_item_ids
            )
        )

        decisions: list[dict[str, Any]] = []
        contexts: list[ComparisonDecision] = []
        action_accumulator: dict[tuple[str, str, str], dict[str, Any]] = {}
        executions: list[dict[str, Any]] = []
        terminal_assessments: list[dict[str, Any]] = []
        gap_rows: list[dict[str, Any]] = []

        for device in sorted(request.devices, key=_device_id):
            device_id = _device_id(device)
            if not device_id:
                raise ValueError("every effective-plan device requires device_id")
            for check_item_id in selected_ids_by_device[device_id]:
                check_item = item_by_id[check_item_id]
                cutover = assessment_policy_cutovers.get(
                    (str(check_item.version_id), str(device_id))
                )
                governed_only = bool(
                    cutover
                    and str(cutover.get("resolver_mode") or "")
                    == "governed_only"
                )
                assessment_policy_snapshot = build_target_assessment_policy_snapshot(
                    check_item_id=check_item.id,
                    check_item_version_id=check_item.version_id,
                    canonical_contract=check_item.canonical_contract,
                    device=device,
                    scope_overrides=scope_policy_overrides,
                    governed_sources=governed_policy_sources.get(
                        check_item.id,
                        (),
                    ),
                    applicability_requirement=check_item.applicability,
                    policy_effective_at=observed_at,
                    allow_legacy_sources=not governed_only,
                    allow_legacy_rule_fallback=False,
                    resolver_transition=(
                        {
                            key: cutover.get(key)
                            for key in (
                                "id",
                                "resolver_mode",
                                "evidence_digest",
                                "binding_ids_json",
                                "shadow_result_ids_json",
                                "created_by",
                                "reason",
                                "created_at",
                            )
                        }
                        if cutover
                        else {}
                    ),
                )
                applicability_decision = dict(
                    assessment_policy_snapshot.get("applicability_decision") or {}
                )
                applicability = str(
                    applicability_decision.get("status") or "unknown"
                )
                applicability_reasons = tuple(
                    str(item)
                    for item in applicability_decision.get("reason_codes") or []
                    if str(item)
                )
                decision = resolver.resolve(
                    device=dict(device),
                    check_item_id=check_item_id,
                )
                status = decision.status
                reason_codes = list(decision.reason_codes)
                bundle: dict[str, Any] | None = None
                if applicability == "not_applicable":
                    status = "not_applicable"
                    reason_codes = list(applicability_reasons)
                elif applicability in {"unknown", "conflict"}:
                    status = "unsupported"
                    reason_codes = list(applicability_reasons) or [
                        "assessment_applicability_unknown"
                    ]
                elif decision.status == "resolved":
                    bundle_lookup = active_bundle_index.get(
                        (
                            str(decision.check_item_version_id),
                            str(decision.implementation_version_id),
                        )
                    )
                    bundle_blockers = tuple(
                        (bundle_lookup or {}).get("blockers") or ()
                    )
                    if bundle_blockers:
                        status = "blocked_asset_missing"
                        reason_codes = [
                            "runtime_bundle_invalid",
                            *bundle_blockers,
                        ]
                    else:
                        bundle = (
                            (bundle_lookup or {}).get("bundle")
                            if bundle_lookup
                            else None
                        )
                    if bundle is None and status == "resolved":
                        status = "blocked_asset_missing"
                        reason_codes = ["active_runtime_bundle_missing"]
                elif decision.status == "ambiguous":
                    status = "unsupported"
                    reason_codes = [
                        "ambiguous_implementation",
                        *reason_codes,
                    ]

                canonical_schema_version = (
                    f"{bundle['canonical_catalog_version']}:"
                    f"{bundle['canonical_catalog_digest']}"
                    if bundle
                    else "canonical:unresolved"
                )
                identity_version = (
                    str(bundle["entity_identity_policy_version_id"])
                    if bundle
                    else f"{identity_policy.policy_id}:v{identity_policy.version}"
                )
                current_fact_contract = (
                    build_comparison_fact_contract(
                        check_item_version_id=check_item.version_id,
                        check_item_digest=check_item.digest,
                        result_mode=check_item.result_mode,
                        assessment_mode=check_item.assessment_mode,
                        implementation_version_id=str(
                            decision.implementation_version_id or ""
                        ),
                        canonical_catalog_version=bundle[
                            "canonical_catalog_version"
                        ],
                        canonical_catalog_digest=bundle[
                            "canonical_catalog_digest"
                        ],
                        entity_identity_policy_version_id=bundle[
                            "entity_identity_policy_version_id"
                        ],
                        dependency_digests=dict(
                            bundle.get("dependency_digests") or {}
                        ),
                        comparison_profile_version_id=bundle.get(
                            "comparison_profile_version_id"
                        ),
                    )
                    if bundle
                    else None
                )
                requested_baseline_key = (device_id, check_item_id)
                if requested_baseline_key in request.baseline_candidates:
                    baseline_candidates = request.baseline_candidates[
                        requested_baseline_key
                    ]
                elif bundle is not None:
                    baseline_candidates = automatic_baseline_candidates.get(
                        (device_id, check_item.version_id),
                        (),
                    )
                else:
                    baseline_candidates = ()
                context = comparison_service.select_online(
                    device_id=device_id,
                    check_item_version_id=check_item.version_id,
                    current_observed_at=observed_at,
                    candidates=baseline_candidates,
                    max_baseline_age_seconds=request.max_baseline_age_seconds,
                    canonical_schema_version=canonical_schema_version,
                    entity_identity_policy_version=identity_version,
                    current_fact_contract=current_fact_contract,
                )
                contexts.append(context)

                execution_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/check-execution/{request.patrol_batch_id}/"
                        f"{device_id}/{check_item.version_id}",
                    )
                )
                execution_status = {
                    "resolved": "planned",
                    "not_applicable": "not_applicable",
                    "blocked_asset_missing": "blocked_asset_missing",
                    "unsupported": "unsupported",
                }.get(status, "unsupported")
                if bundle is not None:
                    frozen_policy = dict(
                        dict(bundle.get("compatibility") or {}).get(
                            "assessment_policy"
                        )
                        or {}
                    )
                    if str(frozen_policy.get("digest") or "") != str(
                        assessment_policy_snapshot.get("contract_digest")
                        or ""
                    ):
                        execution_status = "blocked_asset_missing"
                        status = "blocked_asset_missing"
                        reason_codes = [
                            "runtime_assessment_policy_mismatch"
                        ]
                        bundle = None
                action_keys: list[tuple[str, str, str]] = []
                unavailable_commands: list[dict[str, Any]] = []
                declared_command_count = 0
                executable_command_count = 0
                fallback_resilience = "not_applicable"
                command_availability = "not_applicable"
                if execution_status == "planned":
                    implementation = implementation_by_version[
                        str(decision.implementation_version_id)
                    ]
                    collector_options = dict(
                        (device.get("attributes") or {}).get(
                            "collector_options",
                            {},
                        )
                    )
                    parameter_key = json.dumps(
                        collector_options,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    ordered_commands = sorted(
                        implementation.commands,
                        key=lambda item: (
                            -item.priority,
                            item.command.casefold(),
                        ),
                    )
                    declared_command_count = len(ordered_commands)
                    executable_commands = {
                        ParserPolicy._basic_normalize(str(item or ""))
                        for item in (
                            dict(bundle.get("compatibility") or {}).get(
                                "executable_commands"
                            )
                            or (
                                list(
                                    dict(bundle.get("parser_bundle") or {}).keys()
                                )
                                if check_item.result_mode == "structured"
                                else [command.command for command in ordered_commands]
                            )
                        )
                    }
                    for fallback_rank, command in enumerate(
                        ordered_commands
                    ):
                        canonical = _canonical_command(command.command)
                        executable_command = ParserPolicy._basic_normalize(
                            command.command
                        )
                        if executable_command not in executable_commands:
                            unavailable_commands.append(
                                {
                                    "command": command.command,
                                    "canonical_command": canonical,
                                    "fallback_rank": fallback_rank,
                                    "reason_code": (
                                        "asset_unavailable_at_plan_time"
                                    ),
                                }
                            )
                            continue
                        key = (implementation.collector, canonical, parameter_key)
                        action = action_accumulator.setdefault(
                            (device_id, *key),
                            {
                                "device_id": device_id,
                                "collector": implementation.collector,
                                "command": command.command,
                                "canonical_command": canonical,
                                "fallback_rank": fallback_rank,
                                "priority": command.priority,
                                "parameters": collector_options,
                                "check_item_version_ids": [],
                                "implementation_version_ids": [],
                                "runtime_bundle_version_ids": [],
                                "bindings": [],
                            },
                        )
                        action["fallback_rank"] = min(
                            int(action["fallback_rank"]),
                            fallback_rank,
                        )
                        action["priority"] = max(
                            int(action["priority"]),
                            command.priority,
                        )
                        for field_name, value in (
                            ("check_item_version_ids", check_item.version_id),
                            (
                                "implementation_version_ids",
                                decision.implementation_version_id,
                            ),
                            ("runtime_bundle_version_ids", bundle["version_id"]),
                        ):
                            if value not in action[field_name]:
                                action[field_name].append(value)
                        binding = {
                            "check_item_id": check_item_id,
                            "check_item_version_id": check_item.version_id,
                            "implementation_version_id": (
                                decision.implementation_version_id
                            ),
                            "runtime_bundle_version_id": bundle["version_id"],
                            "observation_contract_refs": sorted(
                                (
                                    {
                                        "contract_id": str(
                                            asset.get("asset_version_id")
                                            or ""
                                        ),
                                        "schema_id": str(
                                            asset.get("asset_id") or ""
                                        ),
                                        "digest": str(
                                            asset.get("asset_digest") or ""
                                        ),
                                    }
                                    for asset in bundle.get("assets") or []
                                    if asset.get("asset_type")
                                    == "observation_contract"
                                ),
                                key=lambda item: (
                                    item["schema_id"],
                                    item["contract_id"],
                                ),
                            ),
                            "fallback_rank": fallback_rank,
                        }
                        if binding not in action["bindings"]:
                            action["bindings"].append(binding)
                        action_keys.append((device_id, *key))
                    executable_command_count = len(action_keys)
                    command_availability = (
                        "available"
                        if executable_command_count > 0
                        else "unavailable"
                    )

                decision_row = {
                    **decision.to_dict(),
                    "device_id": device_id,
                    "applicability": applicability,
                    "applicability_decision": applicability_decision,
                    "status": status,
                    "reason_codes": list(dict.fromkeys(reason_codes)),
                    "runtime_bundle_id": (
                        bundle["bundle_id"] if bundle else None
                    ),
                    "runtime_bundle_version_id": (
                        bundle["version_id"] if bundle else None
                    ),
                    "runtime_bundle_digest": bundle["digest"] if bundle else None,
                    "assessment_policy_snapshot": assessment_policy_snapshot,
                    "assessment_policy_snapshot_digest": (
                        assessment_policy_snapshot["digest"]
                    ),
                    "entity_identity_policy_version_id": (
                        bundle["entity_identity_policy_version_id"]
                        if bundle
                        else identity_version
                    ),
                    "comparison_context_id": context.id,
                    "comparison_context_digest": context.digest,
                    "execution_id": execution_id,
                    "fallback_resilience": fallback_resilience,
                    "command_availability": command_availability,
                    "declared_command_count": declared_command_count,
                    "executable_command_count": (
                        executable_command_count
                    ),
                    "unavailable_commands": unavailable_commands,
                    "_action_keys": action_keys,
                }
                decisions.append(decision_row)
                execution = {
                    "id": execution_id,
                    "idempotency_key": _digest(
                        {
                            "batch": request.patrol_batch_id,
                            "device": device_id,
                            "check_item_version": check_item.version_id,
                        }
                    ),
                    "patrol_batch_id": request.patrol_batch_id,
                    "device_id": device_id,
                    "check_item_id": check_item_id,
                    "check_item_version_id": check_item.version_id,
                    "implementation_version_id": (
                        decision.implementation_version_id
                        if execution_status
                        not in {"unsupported", "not_applicable"}
                        else None
                    ),
                    "runtime_bundle_id": bundle["bundle_id"] if bundle else None,
                    "runtime_bundle_version_id": (
                        bundle["version_id"] if bundle else None
                    ),
                    "runtime_bundle_digest": bundle["digest"] if bundle else None,
                    "assessment_policy_snapshot_json": assessment_policy_snapshot,
                    "assessment_policy_snapshot_digest": (
                        assessment_policy_snapshot["digest"]
                    ),
                    "comparison_context_id": context.id,
                    "execution_status": execution_status,
                    "reason_codes_json": list(dict.fromkeys(reason_codes)),
                    "target_manifest_digest": request.target_manifest_digest,
                    "asset_manifest_digest": bundle["digest"] if bundle else None,
                    "evidence_refs_json": [],
                    "_action_keys": action_keys,
                    "assessment_mode": check_item.assessment_mode,
                    "comparison_status": context.comparison_status,
                }
                executions.append(execution)
                if execution_status != "planned":
                    terminal_basis = build_assessment_basis(
                        entries=(),
                        policy_snapshot_digest=assessment_policy_snapshot["digest"],
                        runtime_bundle_version_id=(
                            str(bundle["version_id"]) if bundle else None
                        ),
                        runtime_bundle_digest=(
                            str(bundle["digest"]) if bundle else None
                        ),
                        applicability_decision=applicability_decision,
                    )
                    assessment = CheckAssessmentService.assess(
                        CheckAssessmentInput(
                            execution_id=execution_id,
                            patrol_batch_id=request.patrol_batch_id,
                            device_id=device_id,
                            check_item_version_id=check_item.version_id,
                            execution_status=execution_status,
                            assessment_mode=check_item.assessment_mode,
                            comparison_status=context.comparison_status,
                            observation_count=0,
                            observed_entity_count=0,
                            evaluated_entity_count=0,
                            rule_assets_ready=False,
                            blocking_reason_codes=tuple(reason_codes),
                            assessment_policy_snapshot_digest=(
                                assessment_policy_snapshot["digest"]
                            ),
                            assessment_basis=terminal_basis,
                            runtime_bundle_version_id=(
                                str(bundle["version_id"]) if bundle else None
                            ),
                            assessment_rule_bundle_digest=(
                                str(bundle["digest"]) if bundle else None
                            ),
                            comparison_context_digest=context.digest,
                            assessed_at=observed_at,
                        )
                    )
                    terminal_assessments.append(assessment.values)
                if execution_status == "blocked_asset_missing":
                    gap_rows.append(
                        {
                            "device_id": device_id,
                            "check_item_id": check_item_id,
                            "check_item_version_id": check_item.version_id,
                            "implementation_version_id": (
                                decision.implementation_version_id
                            ),
                            "check_execution_id": execution_id,
                            "gap_type": "runtime_bundle_missing",
                            "reason_code": str(reason_codes[0]),
                            "purpose": "production_patrol",
                            "canonical_command": None,
                            "metadata": {
                                "all_reason_codes": list(
                                    dict.fromkeys(reason_codes)
                                )
                            },
                        }
                    )

        actions: list[dict[str, Any]] = []
        action_id_by_key: dict[tuple[str, str, str, str], str] = {}
        for key, raw in sorted(
            action_accumulator.items(),
            key=lambda item: (
                item[0][0],
                int(item[1]["fallback_rank"]),
                -int(item[1]["priority"]),
                item[1]["canonical_command"],
            ),
        ):
            normalized = {
                **raw,
                "check_item_version_ids": sorted(
                    set(raw["check_item_version_ids"])
                ),
                "implementation_version_ids": sorted(
                    set(raw["implementation_version_ids"])
                ),
                "runtime_bundle_version_ids": sorted(
                    set(raw["runtime_bundle_version_ids"])
                ),
                "bindings": sorted(
                    raw["bindings"],
                    key=lambda item: (
                        item["check_item_version_id"],
                        item["implementation_version_id"],
                        int(item["fallback_rank"]),
                    ),
                ),
            }
            action_digest = _digest(normalized)
            action_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/check-item-action/{request.patrol_batch_id}/"
                    f"{raw['device_id']}/{action_digest}",
                )
            )
            normalized["id"] = action_id
            normalized["digest"] = action_digest
            normalized["status"] = "planned"
            actions.append(normalized)
            action_id_by_key[key] = action_id

        public_decisions: list[dict[str, Any]] = []
        for row in decisions:
            public = {key: value for key, value in row.items() if key != "_action_keys"}
            public['change_tracking'] = {
                observation['observation_type']: observation.get('change_tracking', True)
                for observation in item_by_id[row['check_item_id']].canonical_contract.get('observations', [])
            }
            public["collection_action_ids"] = sorted(
                action_id_by_key[key] for key in row["_action_keys"]
            )
            public_decisions.append(public)
        public_executions: list[dict[str, Any]] = []
        for row in executions:
            public = {
                key: value
                for key, value in row.items()
                if key
                not in {
                    "_action_keys",
                    "assessment_mode",
                    "comparison_status",
                    "check_item_id",
                }
            }
            public["collection_action_ids_json"] = sorted(
                action_id_by_key[key] for key in row["_action_keys"]
            )
            public_executions.append(public)

        runtime_bundle_refs = sorted(
            [
                {
                    "device_id": row["device_id"],
                    "check_item_version_id": row["check_item_version_id"],
                    "implementation_version_id": row[
                        "implementation_version_id"
                    ],
                    "bundle_id": row["runtime_bundle_id"],
                    "bundle_version_id": row["runtime_bundle_version_id"],
                    "digest": row["runtime_bundle_digest"],
                    "entity_identity_policy_version_id": row[
                        "entity_identity_policy_version_id"
                    ],
                }
                for row in public_decisions
                if row["runtime_bundle_version_id"]
            ],
            key=lambda item: (
                item["device_id"],
                item["check_item_version_id"],
            ),
        )
        aggregated_gaps = _aggregate_asset_gaps(gap_rows)
        manifest_payload = {
            "schema_version": 2,
            "patrol_batch_id": request.patrol_batch_id,
            "profile_version_id": request.profile_version_id,
            "profile_version_ids_by_device": {
                device_id: request.profile_version_ids_by_device.get(
                    device_id
                )
                for device_id in sorted(selected_ids_by_device)
            },
            **scope_resolution.to_dict(),
            "target_manifest_digest": request.target_manifest_digest,
            "inventory_snapshot_digest": request.inventory_snapshot_digest,
            "check_item_versions": sorted(
                {
                    item_by_id[item].version_id
                    for item in all_selected_ids
                }
            ),
            "device_check_item_versions": {
                device_id: [
                    item_by_id[item].version_id
                    for item in selected_ids_by_device[device_id]
                ]
                for device_id in sorted(selected_ids_by_device)
            },
            "decisions": public_decisions,
            "collection_actions": actions,
            "runtime_bundle_refs": runtime_bundle_refs,
            "comparison_contexts": [
                {
                    "id": item.id,
                    "digest": item.digest,
                    "mode": item.mode,
                    "comparison_status": item.comparison_status,
                    "device_id": item.device_id,
                    "check_item_version_id": item.check_item_version_id,
                    "current_observed_at": (
                        item.current_observed_at.isoformat()
                        if item.current_observed_at
                        else None
                    ),
                    "baseline_observed_at": (
                        item.baseline_observed_at.isoformat()
                        if item.baseline_observed_at
                        else None
                    ),
                    "baseline_run_id": item.baseline_run_id,
                    "baseline_snapshot_ids": list(
                        item.baseline_snapshot_ids
                    ),
                    "selection_reason": item.selection_reason,
                }
                for item in contexts
            ],
            "entity_identity_policy_versions": sorted(
                {
                    item["entity_identity_policy_version_id"]
                    for item in public_decisions
                    if item.get("entity_identity_policy_version_id")
                }
            ),
            "assessment_schema_version": "check-assessment/v1",
            "report_schema_version": "patrol-report/v2",
            "report_cutoff_policy": {
                "business_time_source": "batch_terminal_server_time",
                "ingestion_boundary_source": "report_requested_at",
                "clock": "server_utc",
                "late_fact_strategy": "new_report_manifest_revision",
            },
            "asset_gap_ids": [item["id"] for item in aggregated_gaps],
        }
        # Bundle refs intentionally repeat the immutable dependency through decisions.
        manifest_digest = _digest(manifest_payload)
        manifest_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/check-item-manifest/{request.patrol_batch_id}/"
                f"{manifest_digest}",
            )
        )
        return EffectivePlan(
            patrol_batch_id=request.patrol_batch_id,
            manifest_id=manifest_id,
            manifest_digest=manifest_digest,
            manifest={**manifest_payload, "digest": manifest_digest},
            decisions=tuple(public_decisions),
            collection_actions=tuple(actions),
            executions=tuple(public_executions),
            comparison_contexts=tuple(contexts),
            terminal_assessments=tuple(terminal_assessments),
            asset_gaps=aggregated_gaps,
        )

    def freeze(self, request: EffectivePlanRequest) -> EffectivePlan:
        frozen = self.load_frozen(request.patrol_batch_id)
        if frozen is not None:
            if (
                frozen.manifest["target_manifest_digest"]
                != request.target_manifest_digest
                or frozen.manifest["inventory_snapshot_digest"]
                != request.inventory_snapshot_digest
            ):
                raise ValueError(
                    "retry target or inventory differs from the frozen runtime manifest"
                )
            catalog = self._load_catalog()
            item_by_id = {
                item.id: item for item in catalog.check_items
            }
            default_ids = tuple(sorted(set(request.check_item_ids)))
            requested_by_device = {
                str(device_id): tuple(sorted(set(values)))
                for device_id, values in (
                    request.check_item_ids_by_device.items()
                )
            }
            requested_ids = sorted(
                {
                    *default_ids,
                    *(
                        item
                        for values in requested_by_device.values()
                        for item in values
                    ),
                }
            )
            missing = sorted(set(requested_ids) - set(item_by_id))
            if missing:
                raise ValueError(
                    "unknown active check items: " + ", ".join(missing)
                )
            requested_versions = sorted(
                item_by_id[item].version_id for item in requested_ids
            )
            if requested_versions != list(
                frozen.manifest["check_item_versions"]
            ):
                raise ValueError(
                    "retry check-item selection differs from the frozen runtime manifest"
                )
            scope_resolution = resolve_patrol_scope(
                contract=request.scope_contract,
                devices=request.devices,
                selected_check_item_ids_by_device={
                    device_id: requested_by_device.get(
                        device_id,
                        default_ids,
                    )
                    for device_id in sorted(
                        {
                            _device_id(device)
                            for device in request.devices
                            if _device_id(device)
                        }
                    )
                },
                check_item_version_ids={
                    item_id: item.version_id
                    for item_id, item in item_by_id.items()
                },
                inventory_snapshot_digest=(
                    request.inventory_snapshot_digest
                ),
                exclusions=request.scope_exclusions,
                not_applicable=request.scope_not_applicable,
            )
            if (
                str(frozen.manifest.get("scope_contract_digest") or "")
                != scope_resolution.contract_digest
                or str(
                    frozen.manifest.get(
                        "intended_target_matrix_digest"
                    )
                    or ""
                )
                != scope_resolution.intended_target_matrix_digest
                or str(
                    frozen.manifest.get("scope_decision_digest") or ""
                )
                != scope_resolution.scope_decision_digest
            ):
                raise ValueError(
                    "retry patrol scope differs from the frozen runtime manifest"
                )
            requested_device_versions = {
                _device_id(device): [
                    item_by_id[item].version_id
                    for item in requested_by_device.get(
                        _device_id(device),
                        default_ids,
                    )
                ]
                for device in sorted(request.devices, key=_device_id)
            }
            if requested_device_versions != dict(
                frozen.manifest.get("device_check_item_versions") or {}
            ):
                raise ValueError(
                    "retry device check-item selection differs from the "
                    "frozen runtime manifest"
                )
            return frozen
        plan = self.preview(request)
        scope_resolution = ScopeResolution.from_dict(plan.manifest)
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            batch = (
                db.query(PatrolBatchRecord)
                .filter(
                    PatrolBatchRecord.patrol_batch_id
                    == request.patrol_batch_id
                )
                .with_for_update()
                .first()
            )
            if batch is None:
                raise ValueError("patrol batch must exist before effective-plan freeze")
            existing_manifest = (
                db.query(CheckItemRuntimeManifestRecord)
                .filter(
                    CheckItemRuntimeManifestRecord.patrol_batch_id
                    == request.patrol_batch_id
                )
                .first()
            )
            if existing_manifest is not None:
                if str(existing_manifest.digest) != plan.manifest_digest:
                    raise ValueError(
                        "patrol batch already has a different frozen runtime manifest"
                    )
                persisted_scope = PatrolScopeRepository(
                    self._session_scope
                ).persist_in_session(
                    db,
                    batch=batch,
                    resolution=scope_resolution,
                    inventory_snapshot_digest=(
                        request.inventory_snapshot_digest
                    ),
                    actor=request.actor,
                    created_at=now,
                )
                existing_manifest.scope_contract_version_id = (
                    persisted_scope.scope_contract_version_id
                )
                existing_manifest.scope_contract_digest = (
                    scope_resolution.contract_digest
                )
                existing_manifest.intended_target_matrix_digest = (
                    scope_resolution.intended_target_matrix_digest
                )
                existing_manifest.scope_decision_digest = (
                    scope_resolution.scope_decision_digest
                )
                db.flush()
                return plan

            persisted_scope = PatrolScopeRepository(
                self._session_scope
            ).persist_in_session(
                db,
                batch=batch,
                resolution=scope_resolution,
                inventory_snapshot_digest=request.inventory_snapshot_digest,
                actor=request.actor,
                created_at=now,
            )

            context_ids = [item.id for item in plan.comparison_contexts]
            existing_context_ids = {
                str(row[0])
                for row in (
                    db.query(ComparisonContextRecord.id)
                    .filter(ComparisonContextRecord.id.in_(context_ids))
                    .all()
                    if context_ids
                    else []
                )
            }
            for context in plan.comparison_contexts:
                if context.id not in existing_context_ids:
                    db.add(
                        ComparisonContextRecord(
                            **context.to_record_values(),
                            created_at=now,
                        )
                    )
            db.flush()
            for action in plan.collection_actions:
                db.add(
                    CheckItemCollectionActionRecord(
                        id=action["id"],
                        patrol_batch_id=request.patrol_batch_id,
                        device_id=action["device_id"],
                        collector=action["collector"],
                        command=action["command"],
                        canonical_command=action["canonical_command"],
                        fallback_rank=action["fallback_rank"],
                        priority=action["priority"],
                        parameters_json=dict(action["parameters"]),
                        check_item_version_ids_json=list(
                            action["check_item_version_ids"]
                        ),
                        implementation_version_ids_json=list(
                            action["implementation_version_ids"]
                        ),
                        runtime_bundle_version_ids_json=list(
                            action["runtime_bundle_version_ids"]
                        ),
                        digest=action["digest"],
                        status="planned",
                        created_at=now,
                        updated_at=now,
                    )
                )
            db.flush()
            assessments_by_execution = {
                str(values["execution_id"]): values
                for values in plan.terminal_assessments
            }
            for execution in plan.executions:
                assessment = assessments_by_execution.get(
                    str(execution["id"])
                )
                db.add(
                    CheckExecutionRecord(
                        **execution,
                        observation_count=0,
                        observed_entity_count=0,
                        change_count=0,
                        event_count=0,
                        current_finding_count=0,
                        assessment_id=(
                            assessment["id"] if assessment else None
                        ),
                        completed_at=(
                            assessment["assessed_at"]
                            if assessment
                            else None
                        ),
                        created_at=now,
                        updated_at=now,
                    )
                )
            db.flush()
            for values in plan.terminal_assessments:
                db.add(CheckAssessmentRecord(**values))
            gap_keys = [
                str(gap["idempotency_key"])
                for gap in plan.asset_gaps
            ]
            existing_gaps = {
                str(row.idempotency_key): row
                for row in (
                    db.query(AssetGapRecord)
                    .filter(AssetGapRecord.idempotency_key.in_(gap_keys))
                    .with_for_update()
                    .all()
                    if gap_keys
                    else []
                )
            }
            for gap in plan.asset_gaps:
                idempotency_key = str(gap["idempotency_key"])
                affected_count = max(
                    1,
                    int(gap.get("affected_execution_count") or 0),
                )
                existing_gap = existing_gaps.get(idempotency_key)
                metadata = {
                    **dict(gap.get("metadata") or {}),
                    "latest_patrol_batch_id": request.patrol_batch_id,
                }
                if existing_gap is None:
                    db.add(
                        AssetGapRecord(
                            id=str(gap["id"]),
                            idempotency_key=idempotency_key,
                            patrol_batch_id=request.patrol_batch_id,
                            check_execution_id=gap["check_execution_id"],
                            check_item_id=gap["check_item_id"],
                            check_item_version_id=gap[
                                "check_item_version_id"
                            ],
                            implementation_version_id=gap[
                                "implementation_version_id"
                            ],
                            canonical_command=gap["canonical_command"],
                            output_signature=None,
                            gap_type=gap["gap_type"],
                            purpose=gap["purpose"],
                            status="open",
                            reason_code=gap["reason_code"],
                            sanitized_sample_refs_json=[],
                            evidence_refs_json=[],
                            occurrence_count=affected_count,
                            first_seen_at=now,
                            last_seen_at=now,
                            metadata_json=metadata,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                else:
                    existing_gap.patrol_batch_id = request.patrol_batch_id
                    existing_gap.check_execution_id = gap[
                        "check_execution_id"
                    ]
                    existing_gap.status = "open"
                    existing_gap.resolved_at = None
                    existing_gap.resolved_by = None
                    existing_gap.resolution_note = None
                    existing_gap.occurrence_count = (
                        int(existing_gap.occurrence_count or 0)
                        + affected_count
                    )
                    existing_gap.last_seen_at = now
                    existing_gap.metadata_json = {
                        **dict(existing_gap.metadata_json or {}),
                        **metadata,
                    }
                    existing_gap.updated_at = now

            runtime_bundle_refs = list(
                plan.manifest["runtime_bundle_refs"]
            )
            identity_versions = sorted(
                {
                    str(item.get("entity_identity_policy_version_id"))
                    for item in plan.decisions
                    if item.get("entity_identity_policy_version_id")
                }
            )
            manifest = CheckItemRuntimeManifestRecord(
                id=plan.manifest_id,
                patrol_batch_id=request.patrol_batch_id,
                schema_version=2,
                target_manifest_digest=request.target_manifest_digest,
                inventory_snapshot_digest=request.inventory_snapshot_digest,
                scope_contract_version_id=(
                    persisted_scope.scope_contract_version_id
                ),
                scope_contract_digest=scope_resolution.contract_digest,
                intended_target_matrix_digest=(
                    scope_resolution.intended_target_matrix_digest
                ),
                scope_decision_digest=(
                    scope_resolution.scope_decision_digest
                ),
                check_item_versions_json=list(
                    plan.manifest["check_item_versions"]
                ),
                collection_actions_json=[
                    item["id"] for item in plan.collection_actions
                ],
                runtime_bundle_refs_json=runtime_bundle_refs,
                comparison_context_ids_json=[
                    item.id for item in plan.comparison_contexts
                ],
                entity_identity_policy_versions_json=identity_versions,
                assessment_schema_version="check-assessment/v1",
                report_schema_version="patrol-report/v2",
                manifest_json=dict(plan.manifest),
                digest=plan.manifest_digest,
                status="frozen",
                created_by=request.actor,
                created_at=now,
            )
            db.add(manifest)
            batch.target_manifest_digest = request.target_manifest_digest
            batch.inventory_snapshot_digest = request.inventory_snapshot_digest
            batch.scope_contract_version_id = (
                persisted_scope.scope_contract_version_id
            )
            batch.scope_contract_digest = scope_resolution.contract_digest
            batch.intended_target_matrix_digest = (
                scope_resolution.intended_target_matrix_digest
            )
            batch.scope_decision_digest = (
                scope_resolution.scope_decision_digest
            )
            batch.runtime_manifest_id = plan.manifest_id
            batch.runtime_manifest_digest = plan.manifest_digest
            batch.updated_at = now
            db.flush()
        return plan

    def load_frozen(self, patrol_batch_id: str) -> EffectivePlan | None:
        with self._session_scope() as db:
            manifest = (
                db.query(CheckItemRuntimeManifestRecord)
                .filter(
                    CheckItemRuntimeManifestRecord.patrol_batch_id
                    == patrol_batch_id
                )
                .first()
            )
            if manifest is None:
                return None
            payload = dict(manifest.manifest_json or {})
            supplied_digest = str(payload.get("digest") or "")
            unsigned = {
                key: value for key, value in payload.items() if key != "digest"
            }
            if supplied_digest != str(manifest.digest) or _digest(
                unsigned
            ) != str(manifest.digest):
                raise ValueError("frozen check-item runtime manifest digest mismatch")
            context_rows = (
                db.query(ComparisonContextRecord)
                .filter(
                    ComparisonContextRecord.id.in_(
                        manifest.comparison_context_ids_json or []
                    )
                )
                .all()
            )
            contexts = tuple(
                ComparisonDecision(
                    id=str(item.id),
                    digest=str(item.digest),
                    mode=str(item.mode),
                    comparison_status=str(
                        (item.metadata_json or {}).get(
                            "comparison_status",
                            "baseline_missing",
                        )
                    ),
                    device_id=str(item.device_id),
                    check_item_version_id=str(item.check_item_version_id),
                    current_observed_at=item.current_observed_at,
                    baseline_observed_at=item.baseline_observed_at,
                    baseline_run_id=item.baseline_run_id,
                    baseline_snapshot_ids=tuple(
                        item.baseline_snapshot_ids_json or []
                    ),
                    max_baseline_age_seconds=item.max_baseline_age_seconds,
                    canonical_schema_version=item.canonical_schema_version,
                    entity_identity_policy_version=(
                        item.entity_identity_policy_version
                    ),
                    selection_reason=str(item.selection_reason),
                    metadata={
                        key: value
                        for key, value in dict(item.metadata_json or {}).items()
                        if key != "comparison_status"
                    },
                )
                for item in sorted(context_rows, key=lambda row: row.id)
            )
            execution_rows = (
                db.query(CheckExecutionRecord)
                .filter(
                    CheckExecutionRecord.patrol_batch_id == patrol_batch_id
                )
                .order_by(
                    CheckExecutionRecord.device_id,
                    CheckExecutionRecord.check_item_version_id,
                )
                .all()
            )
            assessment_rows = (
                db.query(CheckAssessmentRecord)
                .filter(
                    CheckAssessmentRecord.patrol_batch_id == patrol_batch_id
                )
                .order_by(
                    CheckAssessmentRecord.device_id,
                    CheckAssessmentRecord.check_item_version_id,
                )
                .all()
            )
            gap_ids = [
                str(item)
                for item in payload.get("asset_gap_ids") or []
                if str(item)
            ]
            gap_rows = (
                db.query(AssetGapRecord)
                .filter(AssetGapRecord.id.in_(gap_ids))
                .order_by(AssetGapRecord.id)
                .all()
                if gap_ids
                else []
            )
            return EffectivePlan(
                patrol_batch_id=patrol_batch_id,
                manifest_id=str(manifest.id),
                manifest_digest=str(manifest.digest),
                manifest=payload,
                decisions=tuple(
                    dict(item) for item in payload.get("decisions") or []
                ),
                collection_actions=tuple(
                    dict(item)
                    for item in payload.get("collection_actions") or []
                ),
                executions=tuple(item.to_dict() for item in execution_rows),
                comparison_contexts=contexts,
                terminal_assessments=tuple(
                    item.to_dict() for item in assessment_rows
                ),
                asset_gaps=tuple(item.to_dict() for item in gap_rows),
            )
