"""Atomic, immutable production asset bundles for one check-item implementation."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from src.apps.rule_governance.backend.check_items.catalog import CheckItemCatalogRepository
from src.apps.rule_governance.backend.check_items.runtime_bundle_assembly import (
    RuntimeBundleAssemblyPlanner,
    derive_asset_requirement_profile,
    event_rule_observation_types,
    govern_evaluation_scope,
    normalize_rule_bindings,
)
from src.common.governance import actor_matches_any
from src.core.check_items.assessment_policy import (
    normalize_assessment_policy_contract,
)
from src.core.check_items.canonical import load_canonical_catalog, rule_semantics_catalog
from src.core.check_items.command_adapter import is_direct_projection_adapter
from src.core.check_items.identity import EntityIdentityPolicyRepository
from src.core.check_items.presentation import check_item_display_name
from src.infrastructure.db.models import (
    AuditLogRecord,
    ChangePolicyRecord,
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
    ParserTemplate,
    RuntimeBundleAssetRecord,
    TextComparisonProfileVersionRecord,
)
from src.infrastructure.db.postgres import get_db_session


class RuntimeBundleValidationError(ValueError):
    """Raised when child assets do not form a compatible runtime unit."""

    def __init__(self, blockers: Iterable[str]) -> None:
        self.blockers = tuple(dict.fromkeys(str(item) for item in blockers))
        super().__init__("; ".join(self.blockers))


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


class CheckItemRuntimeBundleService:
    """Assemble, activate and roll back compatible asset versions transactionally."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    @staticmethod
    def _bundle_id(check_item_id: str, implementation_id: str) -> str:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/runtime-bundle/{check_item_id}/{implementation_id}",
            )
        )

    @staticmethod
    def _asset_id(
        bundle_version_id: str,
        asset_type: str,
        asset_version_id: str,
        role: str,
    ) -> str:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/runtime-bundle-asset/{bundle_version_id}/"
                f"{asset_type}/{asset_version_id}/{role}",
            )
        )

    def preview_assembly(
        self,
        *,
        check_item_version_id: str,
        implementation_version_id: str,
        parser_template_ids: Iterable[str] = (),
        change_policy_version_ids: Iterable[str] = (),
        event_rule_version_ids: Iterable[str] = (),
        comparison_profile_version_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a stable, side-effect-free runtime assembly decision."""

        with self._session_scope() as db:
            return RuntimeBundleAssemblyPlanner(db).build(
                check_item_version_id=check_item_version_id,
                implementation_version_id=implementation_version_id,
                parser_template_ids=parser_template_ids,
                change_policy_version_ids=change_policy_version_ids,
                event_rule_version_ids=event_rule_version_ids,
                comparison_profile_version_id=comparison_profile_version_id,
            )

    def assemble(
        self,
        *,
        check_item_version_id: str,
        implementation_version_id: str,
        preview_digest: str,
        actor: str,
        parser_template_ids: Iterable[str] = (),
        change_policy_version_ids: Iterable[str] = (),
        event_rule_version_ids: Iterable[str] = (),
        comparison_profile_version_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one immutable draft from an unchanged assembly preview."""

        preview = self.preview_assembly(
            check_item_version_id=check_item_version_id,
            implementation_version_id=implementation_version_id,
            parser_template_ids=parser_template_ids,
            change_policy_version_ids=change_policy_version_ids,
            event_rule_version_ids=event_rule_version_ids,
            comparison_profile_version_id=comparison_profile_version_id,
        )
        if str(preview.get("preview_digest") or "") != str(preview_digest or ""):
            raise RuntimeBundleValidationError(
                ["assembly preview is stale; refresh before saving the draft"]
            )
        blockers = [
            str(item.get("message") or item.get("code"))
            for item in preview.get("blockers") or []
        ]
        if blockers:
            raise RuntimeBundleValidationError(blockers)
        selected = dict(preview["selected"])
        return self.create_version(
            check_item_version_id=check_item_version_id,
            implementation_version_id=implementation_version_id,
            parser_template_ids=selected["parser_template_ids"],
            change_policy_version_ids=selected["change_policy_version_ids"],
            event_rule_bindings=dict(selected["event_rule_bindings"]),
            comparison_profile_version_id=selected.get(
                "comparison_profile_version_id"
            ),
            actor=actor,
            assembly_preview_digest=preview_digest,
        )

    def create_version(
        self,
        *,
        check_item_version_id: str,
        implementation_version_id: str,
        parser_template_ids: Iterable[str],
        change_policy_version_ids: Iterable[str],
        event_rule_bindings: Mapping[str, str | Iterable[str]],
        comparison_profile_version_id: str | None = None,
        actor: str,
        assembly_preview_digest: str | None = None,
    ) -> dict[str, Any]:
        identity_repository = EntityIdentityPolicyRepository(self._session_scope)
        identity_repository.bootstrap(actor=actor)
        now = datetime.now(timezone.utc)
        requested_rule_bindings = normalize_rule_bindings(event_rule_bindings)
        with self._session_scope() as db:
            assembly_plan = RuntimeBundleAssemblyPlanner(db).build(
                check_item_version_id=check_item_version_id,
                implementation_version_id=implementation_version_id,
                parser_template_ids=parser_template_ids,
                change_policy_version_ids=change_policy_version_ids,
                event_rule_version_ids=requested_rule_bindings,
                comparison_profile_version_id=comparison_profile_version_id,
            )
            if (
                assembly_preview_digest
                and str(assembly_plan["preview_digest"]) != str(assembly_preview_digest)
            ):
                raise RuntimeBundleValidationError(
                    [
                        "assembly preview is stale; refresh the preview before saving "
                        "the runtime bundle draft"
                    ]
                )
            assembly_blockers = [
                str(item.get("message") or item.get("code"))
                for item in assembly_plan.get("blockers") or []
            ]
            if assembly_blockers:
                raise RuntimeBundleValidationError(assembly_blockers)
            selected = dict(assembly_plan["selected"])
            derived_rule_bindings = normalize_rule_bindings(
                dict(selected.get("event_rule_bindings") or {})
            )
            if requested_rule_bindings != derived_rule_bindings:
                raise RuntimeBundleValidationError(
                    [
                        "EventRule observation bindings must match explicit "
                        "condition_json.observation_types"
                    ]
                )
            parser_template_ids = tuple(selected["parser_template_ids"])
            change_policy_version_ids = tuple(
                selected["change_policy_version_ids"]
            )
            comparison_profile_version_id = selected.get(
                "comparison_profile_version_id"
            )

            check_version = db.get(CheckItemVersionRecord, check_item_version_id)
            implementation_version = db.get(
                CollectionImplementationVersionRecord,
                implementation_version_id,
            )
            if check_version is None or implementation_version is None:
                raise RuntimeBundleValidationError(
                    ["check item or implementation version does not exist"]
                )
            canonical_catalog = rule_semantics_catalog(dict(check_version.canonical_contract_json or {}))
            check_item = db.get(CheckItemRecord, check_version.check_item_id)
            implementation = db.get(
                CollectionImplementationRecord,
                implementation_version.implementation_id,
            )
            if check_item is None or implementation is None:
                raise RuntimeBundleValidationError(
                    ["check item or implementation root does not exist"]
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
            blockers: list[str] = []
            if link is None:
                blockers.append("implementation is not linked to the check item version")
            if check_version.status != "active":
                blockers.append("check item version is not active")
            catalog_candidate = dict(
                dict(assembly_plan.get("compatibility") or {}).get(
                    "catalog_candidate"
                )
                or {}
            )
            if (
                implementation_version.status != "active"
                and str(catalog_candidate.get("change_id") or "") == ""
            ):
                blockers.append("implementation version is not active")

            parser_ids = tuple(sorted(set(str(item) for item in parser_template_ids if item)))
            policy_ids = tuple(
                sorted(set(str(item) for item in change_policy_version_ids if item))
            )
            rule_bindings = derived_rule_bindings
            parser_rows = (
                db.query(ParserTemplate).filter(ParserTemplate.id.in_(parser_ids)).all()
                if parser_ids
                else []
            )
            policy_rows = (
                db.query(ChangePolicyVersionRecord)
                .filter(ChangePolicyVersionRecord.id.in_(policy_ids))
                .all()
                if policy_ids
                else []
            )
            rule_rows = (
                db.query(EventRuleVersionRecord, EventRuleRecord)
                .join(EventRuleRecord, EventRuleRecord.id == EventRuleVersionRecord.rule_id)
                .filter(EventRuleVersionRecord.id.in_(tuple(rule_bindings)))
                .all()
                if rule_bindings
                else []
            )
            comparison_profile = (
                db.get(
                    TextComparisonProfileVersionRecord,
                    comparison_profile_version_id,
                )
                if comparison_profile_version_id
                else None
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
                blockers.append("active entity identity policy is missing")

            if len(parser_rows) != len(parser_ids):
                blockers.append("one or more Parser template versions do not exist")
            if len(policy_rows) != len(policy_ids):
                blockers.append("one or more ChangePolicy versions do not exist")
            if len(rule_rows) != len(rule_bindings):
                blockers.append("one or more EventRule versions do not exist")

            result_mode = str(check_version.result_mode)
            direct_projection = bool(
                result_mode == "structured"
                and is_direct_projection_adapter(
                    dict(implementation_version.adapter_spec_json or {})
                )
            )
            projection_manifest = dict(
                dict(assembly_plan.get("selected") or {}).get(
                    "command_adapter_projection"
                )
                or {}
            )
            if result_mode == "structured" and not parser_rows:
                blockers.append("structured implementation requires a Parser template")
            if result_mode == "structured" and direct_projection and not projection_manifest:
                blockers.append(
                    "structured CommandAdapter V2 implementation requires a projection"
                )
            if result_mode == "structured" and not direct_projection:
                blockers.append("structured implementation requires command-adapter/v2")
            if result_mode != "structured" and parser_rows:
                blockers.append("non-structured implementation cannot bind Parser templates")
            if (
                result_mode in {"normalized_diff", "hash_only"}
                and not comparison_profile_version_id
            ):
                blockers.append("text comparison implementation requires a comparison profile")
            if comparison_profile_version_id and comparison_profile is None:
                blockers.append("comparison profile version does not exist")
            if (
                comparison_profile is not None
                and comparison_profile.status != "active"
            ):
                blockers.append("comparison profile version is not active")

            parser_by_id = {str(item.id): item for item in parser_rows}
            for parser in parser_rows:
                if parser.status != "active":
                    blockers.append(f"Parser template is not active: {parser.id}")
            produced_types: set[str] = set()
            canonical_rows = list(
                dict(check_version.canonical_contract_json or {}).get("observations") or []
            )
            asset_requirement_profile = dict(
                assembly_plan.get("asset_requirement_profile") or {}
            )
            if not asset_requirement_profile:
                blockers.append("asset requirement profile is missing")
            asset_requirements = dict(
                asset_requirement_profile.get("requirements") or {}
            )
            expected_types = {
                str(item.get("observation_type") or "")
                for item in canonical_rows
                if isinstance(item, dict) and item.get("observation_type")
            }
            produced_types.update(
                str(item)
                for item in dict(assembly_plan.get("compatibility") or {}).get(
                    "observation_projection"
                )
                or []
                if str(item).strip()
            )
            required_types = {
                str(item.get("observation_type") or "")
                for item in canonical_rows
                if (
                    isinstance(item, dict)
                    and item.get("observation_type")
                    and item.get("required", True) is not False
                )
            }
            missing_types = sorted(required_types - produced_types)
            approved_types = {
                item.id for item in canonical_catalog.observations
            }
            unknown_types = sorted(produced_types - approved_types)
            if result_mode == "structured" and missing_types:
                blockers.append(
                    "Adapter projection does not produce required canonical observations: "
                    + ", ".join(missing_types)
                )
            if unknown_types:
                blockers.append(
                    "Adapter projection produces observations outside the Canonical Catalog: "
                    + ", ".join(unknown_types)
                )

            policy_by_type = {
                str(item.observation_type): item for item in policy_rows
            }
            for policy in policy_rows:
                if policy.status != "active":
                    blockers.append(f"ChangePolicy is not active: {policy.id}")
            change_requirement = dict(
                asset_requirements.get("change_policy") or {}
            )
            change_required = set(
                change_requirement.get("required_observation_types")
                or []
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
            missing_policies = sorted(
                change_required - set(policy_by_type)
            )
            if missing_policies:
                blockers.append(
                    "ChangePolicy coverage is incomplete: "
                    + ", ".join(missing_policies)
                )

            current_requirement = dict(
                asset_requirements.get("current_state_rule") or {}
            )
            current_required = set(
                current_requirement.get("required_observation_types")
                or []
            )
            current_rule_types: set[str] = set()
            for rule_version, _rule in rule_rows:
                observation_types = rule_bindings[str(rule_version.id)]
                explicit_types = event_rule_observation_types(rule_version)
                if tuple(observation_types) != tuple(explicit_types):
                    blockers.append(
                        "EventRule binding differs from explicit "
                        f"condition_json.observation_types: {rule_version.id}"
                    )
                outside_types = sorted(set(observation_types) - expected_types)
                if outside_types:
                    blockers.append(
                        "EventRule binding uses observations outside check item: "
                        + ", ".join(outside_types)
                    )
                if rule_version.status != "active":
                    blockers.append(f"EventRule is not active: {rule_version.id}")
                scope = govern_evaluation_scope(
                    rule_version.evaluation_scope,
                    event_class=rule_version.event_class,
                )
                if (
                    str(rule_version.event_class or "fault") != "recovery"
                    and scope in {"current_state", "both"}
                ):
                    current_rule_types.update(observation_types)
            if str(check_version.assessment_mode) == "health":
                missing_current_rules = sorted(current_required - current_rule_types)
                if missing_current_rules:
                    blockers.append(
                        "current-state EventRule coverage is incomplete: "
                        + ", ".join(missing_current_rules)
                    )

            if blockers:
                raise RuntimeBundleValidationError(blockers)

            bundle_id = self._bundle_id(check_item.id, implementation.id)
            root = db.get(CheckItemRuntimeBundleRecord, bundle_id)
            if root is None:
                root = CheckItemRuntimeBundleRecord(
                    id=bundle_id,
                    check_item_id=check_item.id,
                    implementation_id=implementation.id,
                    name=f"{check_item.id} / {implementation.id}",
                    status="draft",
                    current_version=0,
                    created_at=now,
                    updated_at=now,
                )
                db.add(root)
                db.flush()

            dependency_digests = {
                "check_item": str(check_version.digest),
                "implementation": str(implementation_version.digest),
                "freshness_policy": _digest(
                    dict(check_version.freshness_policy_json or {})
                ),
                "empty_result_policy": _digest(
                    dict(check_version.empty_result_policy_json or {})
                ),
                "coverage_policy": _digest(
                    dict(check_version.coverage_policy_json or {})
                ),
                "assessment_policy": str(
                    dict(assembly_plan.get("compatibility") or {})
                    .get("assessment_policy", {})
                    .get("digest")
                    or ""
                ),
                "output_quality_policy": _digest(
                    dict(
                        implementation_version.output_quality_policy_json
                        or {}
                    )
                ),
                "canonical_catalog": canonical_catalog.digest,
                "asset_requirement_profile": _digest(
                    asset_requirement_profile
                ),
                "identity_policy": str(identity_version.digest),
                "parser_templates": {
                    str(item.id): str(item.sha256) for item in parser_rows
                },
                "command_adapter_projection": (
                    {
                        "implementation_version_id": str(implementation_version.id),
                        "implementation_digest": str(implementation_version.digest),
                        "projection_digest": str(
                            projection_manifest.get("digest") or ""
                        ),
                    }
                    if direct_projection
                    else {}
                ),
                "change_policies": {
                    str(item.id): str(item.digest) for item in policy_rows
                },
                "event_rules": {
                    str(version.id): str(version.digest)
                    for version, _rule in rule_rows
                },
                "comparison_profile": (
                    {
                        str(comparison_profile.id): str(
                            comparison_profile.digest
                        )
                    }
                    if comparison_profile is not None
                    else {}
                ),
            }
            parser_bundle = {
                str(item.canonical_command): {
                    "template_id": str(item.id),
                    "version": str(item.version),
                    "sha256": str(item.sha256),
                }
                for item in sorted(parser_rows, key=lambda row: (row.canonical_command, row.id))
            }
            compatibility = {
                **dict(assembly_plan["compatibility"]),
                "expected_observation_types": sorted(expected_types),
                "produced_observation_types": sorted(produced_types),
                "current_state_rule_types": sorted(current_rule_types),
                "change_policy_types": sorted(policy_by_type),
            }
            payload = {
                "bundle_id": bundle_id,
                "check_item_version_id": str(check_version.id),
                "implementation_version_id": str(implementation_version.id),
                "parser_bundle": parser_bundle,
                "canonical_catalog_version": canonical_catalog.version,
                "canonical_catalog_digest": canonical_catalog.digest,
                "entity_identity_policy_version_id": str(identity_version.id),
                "comparison_profile_version_id": comparison_profile_version_id,
                "dependency_digests": dependency_digests,
                "event_rule_bindings": {
                    key: list(value)
                    for key, value in sorted(rule_bindings.items())
                },
                "compatibility": compatibility,
            }
            bundle_digest = _digest(payload)
            existing = (
                db.query(CheckItemRuntimeBundleVersionRecord)
                .filter(CheckItemRuntimeBundleVersionRecord.digest == bundle_digest)
                .first()
            )
            if existing is not None:
                if dict(existing.compatibility_json or {}) != compatibility:
                    if str(existing.status) != "draft":
                        raise RuntimeBundleValidationError(
                            [
                                "runtime bundle digest conflicts with an immutable "
                                "published payload"
                            ]
                        )
                    existing.compatibility_json = compatibility
                    db.flush()
                return self._serialize(db, root, existing)
            latest = (
                db.query(CheckItemRuntimeBundleVersionRecord.version)
                .filter(CheckItemRuntimeBundleVersionRecord.bundle_id == bundle_id)
                .order_by(CheckItemRuntimeBundleVersionRecord.version.desc())
                .first()
            )
            version_number = int(latest[0]) + 1 if latest else 1
            version_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/runtime-bundle/{bundle_id}/v{version_number}/{bundle_digest}",
                )
            )
            version = CheckItemRuntimeBundleVersionRecord(
                id=version_id,
                bundle_id=bundle_id,
                version=version_number,
                check_item_version_id=check_version.id,
                implementation_version_id=implementation_version.id,
                parser_bundle_json=parser_bundle,
                canonical_catalog_version=canonical_catalog.version,
                canonical_catalog_digest=canonical_catalog.digest,
                entity_identity_policy_version_id=identity_version.id,
                comparison_profile_version_id=comparison_profile_version_id,
                dependency_digests_json=dependency_digests,
                compatibility_json=compatibility,
                validation_report_json={
                    "success": True,
                    "blockers": [],
                    "warnings": list(assembly_plan.get("warnings") or []),
                    "assembly_preview_digest": assembly_plan["preview_digest"],
                },
                digest=bundle_digest,
                status="draft",
                created_by=actor,
                created_at=now,
            )
            db.add(version)
            db.flush()

            assets: list[RuntimeBundleAssetRecord] = []
            for parser in parser_rows:
                parser_snapshot = dict(
                    dict(assembly_plan.get("dependency_snapshot") or {})
                    .get("parsers", {})
                    .get(str(parser.id), {})
                )
                assets.append(
                    self._asset(
                        version_id,
                        "parser_template",
                        str(parser.id),
                        str(parser.id),
                        str(parser.sha256),
                        canonical_command=str(parser.canonical_command),
                        metadata={
                            "compatibility_manifest": dict(
                                dict(compatibility.get("parser_manifests") or {}).get(
                                    str(parser.id),
                                    {},
                                )
                            ),
                            "compatibility_digest": parser_snapshot.get(
                                "validation_digest"
                            ),
                        },
                    )
                )
            if direct_projection:
                assets.append(
                    self._asset(
                        version_id,
                        "command_adapter_projection",
                        str(implementation.id),
                        str(implementation_version.id),
                        str(implementation_version.digest),
                        metadata={
                            "schema_version": "command-adapter/v2",
                            "projection_digest": str(
                                projection_manifest.get("digest") or ""
                            ),
                            "projection": projection_manifest,
                        },
                    )
                )
            for policy in policy_rows:
                assets.append(
                    self._asset(
                        version_id,
                        "change_policy",
                        str(policy.policy_id),
                        str(policy.id),
                        str(policy.digest),
                        observation_type=str(policy.observation_type),
                    )
                )
            for rule_version, rule in rule_rows:
                scope = govern_evaluation_scope(
                    rule_version.evaluation_scope,
                    event_class=rule_version.event_class,
                )
                asset_types = ["event_rule"] if scope == "change" else ["current_state_rule"]
                if scope == "both":
                    asset_types.append("event_rule")
                for asset_type in asset_types:
                    for observation_type in rule_bindings[str(rule_version.id)]:
                        binding_role = (
                            "observation:"
                            + hashlib.sha256(
                                observation_type.encode("utf-8")
                            ).hexdigest()[:12]
                        )
                        assets.append(
                            self._asset(
                                version_id,
                                asset_type,
                                str(rule.id),
                                str(rule_version.id),
                                str(rule_version.digest),
                                observation_type=observation_type,
                                role=binding_role,
                            )
                        )
            assets.append(
                self._asset(
                    version_id,
                    "identity_policy",
                    str(identity_version.policy_id),
                    str(identity_version.id),
                    str(identity_version.digest),
                )
            )
            if comparison_profile is not None:
                assets.append(
                    self._asset(
                        version_id,
                        "comparison_profile",
                        str(comparison_profile.profile_id),
                        str(comparison_profile.id),
                        str(comparison_profile.digest),
                    )
                )
            db.add_all(assets)
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    idempotency_key=f"runtime-bundle:draft:{version_id}",
                    user_id=actor,
                    username=actor,
                    action="runtime_bundle_draft_created",
                    resource_type="check_item_runtime_bundle_version",
                    resource_id=version_id,
                    detail={
                        "bundle_id": bundle_id,
                        "digest": bundle_digest,
                        "assembly_preview_digest": assembly_plan[
                            "preview_digest"
                        ],
                    },
                    status="success",
                    created_at=now,
                )
            )
            db.flush()
            root.updated_at = now
            return self._serialize(db, root, version)

    def _asset(
        self,
        bundle_version_id: str,
        asset_type: str,
        asset_id: str,
        asset_version_id: str,
        asset_digest: str,
        *,
        canonical_command: str | None = None,
        observation_type: str | None = None,
        role: str = "primary",
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeBundleAssetRecord:
        return RuntimeBundleAssetRecord(
            id=self._asset_id(bundle_version_id, asset_type, asset_version_id, role),
            bundle_version_id=bundle_version_id,
            asset_type=asset_type,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            asset_digest=asset_digest,
            role=role,
            canonical_command=canonical_command,
            observation_type=observation_type,
            required=True,
            metadata_json=dict(metadata or {}),
            created_at=datetime.now(timezone.utc),
        )

    def activate(
        self,
        version_id: str,
        *,
        actor: str,
        reason: str = "",
        allow_deprecated_dependencies: bool = False,
        allow_same_actor: bool = False,
        _audit_action: str = "runtime_bundle_activated",
    ) -> dict[str, Any]:
        if not actor.strip():
            raise ValueError("runtime bundle activation requires actor")
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            version = self.activate_in_session(
                db,
                version_id,
                actor=actor,
                reason=reason,
                now=now,
                require_active_dependencies=not allow_deprecated_dependencies,
                allow_same_actor=allow_same_actor,
                audit_action=_audit_action,
            )
            root = db.get(CheckItemRuntimeBundleRecord, version.bundle_id)
            return self._serialize(db, root, version)

    def activate_in_session(
        self,
        db: Any,
        version_id: str,
        *,
        actor: str,
        reason: str,
        now: datetime,
        require_active_dependencies: bool = True,
        allowed_draft_implementation_version_id: str | None = None,
        allow_same_actor: bool = False,
        audit_action: str = "runtime_bundle_activated",
    ) -> CheckItemRuntimeBundleVersionRecord:
        """Activate one bundle inside the caller's transaction."""

        candidate = db.get(CheckItemRuntimeBundleVersionRecord, version_id)
        if candidate is None:
            raise ValueError("runtime bundle version not found")
        # Lock the implementation root first, in the same order as deletion.
        # A rollback may reuse a historical version, but must not resurrect a
        # deliberately deleted implementation or race its retirement.
        implementation = (
            db.query(CollectionImplementationRecord)
            .join(
                CollectionImplementationVersionRecord,
                CollectionImplementationVersionRecord.implementation_id
                == CollectionImplementationRecord.id,
            )
            .filter(CollectionImplementationVersionRecord.id == candidate.implementation_version_id)
            .with_for_update(of=CollectionImplementationRecord)
            .populate_existing()
            .first()
        )
        if implementation is None or implementation.status == "deprecated":
            raise RuntimeBundleValidationError(["命令适配器已删除，不能重新启用其运行包"])
        # Serialize asset withdrawal with activation, including rollback. Deleted
        # assets must never be resurrected through allow_deprecated_dependencies.
        asset_models = {"parser_template": ParserTemplate,
                        "change_policy": ChangePolicyVersionRecord, "event_rule": EventRuleVersionRecord,
                        "current_state_rule": EventRuleVersionRecord}
        dependencies = db.query(RuntimeBundleAssetRecord).filter_by(bundle_version_id=version_id).all()
        for asset in sorted(dependencies, key=lambda item: (
            0 if item.asset_type == "parser_template" else 1, item.asset_type, item.asset_version_id
        )):
            model = asset_models.get(asset.asset_type)
            if model is not None:
                dependency = db.query(model).filter(model.id == asset.asset_version_id).with_for_update().populate_existing().first()
                if dependency is None or dependency.status == "deleted":
                    raise RuntimeBundleValidationError(["运行包依赖的资产已删除，不能启用或回滚"])
        version = (
            db.query(CheckItemRuntimeBundleVersionRecord)
            .filter(CheckItemRuntimeBundleVersionRecord.id == version_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if version is None:
            raise ValueError("runtime bundle version not found")
        root = db.get(CheckItemRuntimeBundleRecord, version.bundle_id)
        if root is None:
            raise ValueError("runtime bundle root not found")
        if version.status == "active":
            return version
        if (
            audit_action == "runtime_bundle_activated"
            and actor_matches_any(actor, [version.created_by])
            and not allow_same_actor
        ):
            raise RuntimeBundleValidationError(
                [
                    "runtime bundle activation requires an approver "
                    "different from the version creator"
                ]
            )
        blockers = self._dependency_blockers(
            db,
            version,
            require_active=require_active_dependencies,
            allowed_draft_implementation_version_id=(
                allowed_draft_implementation_version_id
            ),
        )
        if blockers:
            raise RuntimeBundleValidationError(blockers)
        (
            db.query(CheckItemRuntimeBundleVersionRecord)
            .filter(
                CheckItemRuntimeBundleVersionRecord.bundle_id == version.bundle_id,
                CheckItemRuntimeBundleVersionRecord.status == "active",
                CheckItemRuntimeBundleVersionRecord.id != version.id,
            )
            .update(
                {"status": "deprecated", "deprecated_at": now},
                synchronize_session=False,
            )
        )
        version.status = "active"
        version.activated_at = now
        version.deprecated_at = None
        root.status = "active"
        root.current_version = int(version.version)
        root.updated_at = now
        db.add(
            AuditLogRecord(
                id=str(uuid.uuid4()),
                idempotency_key=f"{audit_action}:{version.id}",
                user_id=actor,
                username=actor,
                action=audit_action,
                resource_type="check_item_runtime_bundle_version",
                resource_id=str(version.id),
                detail={
                    "bundle_id": str(version.bundle_id),
                    "version": int(version.version),
                    "digest": str(version.digest),
                    "reason": reason.strip(),
                    "allowed_draft_implementation_version_id": (
                        allowed_draft_implementation_version_id
                    ),
                    "separation_of_duties_override": bool(
                        allow_same_actor
                        and actor_matches_any(actor, [version.created_by])
                    ),
                },
                status="success",
                created_at=now,
            )
        )
        db.flush()
        return version

    def rollback(
        self,
        version_id: str,
        *,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        return self.activate(
            version_id,
            actor=actor,
            reason=reason,
            allow_deprecated_dependencies=True,
            _audit_action="runtime_bundle_rolled_back",
        )

    def get_active(
        self,
        *,
        check_item_version_id: str,
        implementation_version_id: str,
    ) -> dict[str, Any] | None:
        key = (str(check_item_version_id), str(implementation_version_id))
        result = self.get_active_many((key,)).get(key)
        if result is None:
            return None
        blockers = tuple(result.get("blockers") or ())
        if blockers:
            raise RuntimeBundleValidationError(blockers)
        return result.get("bundle")

    def get_active_many(
        self,
        pairs: Iterable[tuple[str, str]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Preload active bundles once per unique check-item implementation pair."""

        requested = {
            (str(check_item_version_id), str(implementation_version_id))
            for check_item_version_id, implementation_version_id in pairs
            if str(check_item_version_id) and str(implementation_version_id)
        }
        if not requested:
            return {}
        check_item_version_ids = sorted({item[0] for item in requested})
        implementation_version_ids = sorted({item[1] for item in requested})
        result: dict[tuple[str, str], dict[str, Any]] = {}
        with self._session_scope() as db:
            rows = (
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
                    .in_(check_item_version_ids),
                    CheckItemRuntimeBundleVersionRecord.implementation_version_id.in_(
                        implementation_version_ids
                    ),
                    CheckItemRuntimeBundleVersionRecord.status == "active",
                )
                .all()
            )
            for root, version in rows:
                key = (
                    str(version.check_item_version_id),
                    str(version.implementation_version_id),
                )
                if key not in requested:
                    continue
                blockers = self._dependency_blockers(
                    db,
                    version,
                    require_active=False,
                )
                result[key] = {
                    "bundle": (
                        None
                        if blockers
                        else self._serialize(db, root, version)
                    ),
                    "blockers": tuple(blockers),
                }
        return result

    def catalog_health(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Report true RuntimeBundle coverage for the active catalog."""

        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        supported_statuses = {"ready", "missing", "invalid"}
        if status and status not in supported_statuses:
            raise ValueError(f"unsupported runtime bundle status: {status}")

        catalog = CheckItemCatalogRepository(self._session_scope).load_active()
        check_items = {item.id: item for item in catalog.check_items}
        check_items_by_version = {
            item.version_id: item for item in catalog.check_items
        }
        pairs = sorted(
            {
                (check_items[check_item_id].version_id, implementation.version_id)
                for implementation in catalog.implementations
                for check_item_id in implementation.check_item_ids
                if check_item_id in check_items
            }
        )
        active = self.get_active_many(pairs)
        implementations = {
            item.version_id: item for item in catalog.implementations
        }
        rows: list[dict[str, Any]] = []
        for check_item_version_id, implementation_version_id in pairs:
            check_item = check_items_by_version.get(check_item_version_id)
            implementation = implementations.get(implementation_version_id)
            if check_item is None or implementation is None:
                continue
            resolved = active.get(
                (check_item_version_id, implementation_version_id)
            )
            blockers = list((resolved or {}).get("blockers") or ())
            bundle = (resolved or {}).get("bundle")
            runtime_status = (
                "ready"
                if bundle is not None and not blockers
                else "invalid"
                if blockers
                else "missing"
            )
            rows.append(
                {
                    "check_item_id": check_item.id,
                    "check_item_version_id": check_item.version_id,
                    "check_item_name": check_item.name,
                    "check_item_display_name": check_item_display_name(
                        check_item.id,
                        check_item.name,
                    ),
                    "result_mode": check_item.result_mode,
                    "assessment_mode": check_item.assessment_mode,
                    "implementation_id": implementation.id,
                    "implementation_version_id": implementation.version_id,
                    "implementation_name": implementation.name,
                    "commands": [
                        item.command for item in implementation.commands
                    ],
                    "status": runtime_status,
                    "ready": runtime_status == "ready",
                    "blocking_reasons": (
                        blockers
                        if blockers
                        else (
                            []
                            if runtime_status == "ready"
                            else ["active RuntimeBundle is missing"]
                        )
                    ),
                    "bundle_id": (
                        bundle.get("bundle_id") if bundle is not None else None
                    ),
                    "bundle_version_id": (
                        bundle.get("version_id") if bundle is not None else None
                    ),
                    "bundle_digest": (
                        bundle.get("digest") if bundle is not None else None
                    ),
                    "activated_at": (
                        bundle.get("activated_at") if bundle is not None else None
                    ),
                }
            )

        status_counts = {
            value: sum(item["status"] == value for item in rows)
            for value in sorted(supported_statuses)
        }
        ready_count = int(status_counts["ready"])
        total_count = len(rows)
        summary = {
            "scope": "active_catalog",
            "catalog_version": catalog.catalog_version,
            "check_item_count": len(
                {item["check_item_id"] for item in rows}
            ),
            "implementation_pair_count": total_count,
            "ready_count": ready_count,
            "missing_count": int(status_counts["missing"]),
            "invalid_count": int(status_counts["invalid"]),
            "ready_rate": (
                ready_count / total_count if total_count else 0.0
            ),
            "ready": total_count > 0 and ready_count == total_count,
            "status_counts": status_counts,
        }
        query_text = " ".join(str(search or "").strip().casefold().split())
        filtered = rows
        if query_text:
            filtered = [
                item
                for item in filtered
                if query_text
                in " ".join(
                    [
                        str(item["check_item_id"]),
                        str(item["check_item_name"]),
                        str(item["check_item_display_name"]),
                        str(item["implementation_id"]),
                        str(item["implementation_name"]),
                        *[str(command) for command in item["commands"]],
                    ]
                ).casefold()
            ]
        if status:
            filtered = [
                item for item in filtered if item["status"] == status
            ]
        filtered.sort(
            key=lambda item: (
                {"invalid": 0, "missing": 1, "ready": 2}[item["status"]],
                item["check_item_id"],
                item["implementation_id"],
            )
        )
        total = len(filtered)
        start = (page - 1) * page_size
        return {
            "items": filtered[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "total": total,
            "page_count": (total + page_size - 1) // page_size,
            "summary": summary,
        }

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        """Read one frozen version for retry/replay, even after it is deprecated."""

        with self._session_scope() as db:
            version = db.get(CheckItemRuntimeBundleVersionRecord, version_id)
            if version is None:
                return None
            root = db.get(CheckItemRuntimeBundleRecord, version.bundle_id)
            if root is None:
                raise RuntimeBundleValidationError(["runtime bundle root is missing"])
            blockers = self._dependency_blockers(db, version, require_active=False)
            if blockers:
                raise RuntimeBundleValidationError(blockers)
            return self._serialize(db, root, version)

    def get_rule_assets(self, version_id: str) -> dict[str, Any] | None:
        """Read the exact rule assets frozen into one RuntimeBundle version."""

        with self._session_scope() as db:
            version = db.get(CheckItemRuntimeBundleVersionRecord, version_id)
            if version is None:
                return None
            root = db.get(CheckItemRuntimeBundleRecord, version.bundle_id)
            if root is None:
                raise RuntimeBundleValidationError(["runtime bundle root is missing"])
            blockers = self._dependency_blockers(db, version, require_active=False)
            if blockers:
                raise RuntimeBundleValidationError(blockers)

            bundle = self._serialize(db, root, version)
            assets = list(bundle.get("assets") or [])
            policy_ids = sorted(
                {
                    str(item["asset_version_id"])
                    for item in assets
                    if item.get("asset_type") == "change_policy"
                }
            )
            event_rule_ids = sorted(
                {
                    str(item["asset_version_id"])
                    for item in assets
                    if item.get("asset_type") == "event_rule"
                }
            )
            policy_rows = (
                db.query(ChangePolicyRecord, ChangePolicyVersionRecord)
                .join(
                    ChangePolicyVersionRecord,
                    ChangePolicyVersionRecord.policy_id
                    == ChangePolicyRecord.id,
                )
                .filter(ChangePolicyVersionRecord.id.in_(policy_ids))
                .all()
                if policy_ids
                else []
            )
            if len(policy_rows) != len(policy_ids):
                raise RuntimeBundleValidationError(
                    ["one or more frozen ChangePolicies are missing"]
                )
            rule_rows = (
                db.query(EventRuleVersionRecord, EventRuleRecord)
                .join(
                    EventRuleRecord,
                    EventRuleRecord.id == EventRuleVersionRecord.rule_id,
                )
                .filter(EventRuleVersionRecord.id.in_(event_rule_ids))
                .all()
                if event_rule_ids
                else []
            )
            if len(rule_rows) != len(event_rule_ids):
                raise RuntimeBundleValidationError(
                    ["one or more frozen EventRules are missing"]
                )

            return {
                "bundle": bundle,
                "change_policies": [
                    {
                        **policy.to_dict(),
                        "policy_id": str(policy.id),
                        "policy_name": str(policy.name),
                        "version_id": str(policy_version.id),
                        "version": int(policy_version.version),
                        "observation_type": str(
                            policy_version.observation_type
                        ),
                        "semantic_type": str(policy_version.semantic_type),
                        "compare_mode": str(policy_version.compare_mode),
                        "policy": dict(policy_version.policy_json or {}),
                        "digest": str(policy_version.digest or ""),
                    }
                    for policy, policy_version in policy_rows
                ],
                "event_rules": [
                    {
                        "rule_id": str(rule.id),
                        "rule_name": str(rule.name),
                        "version_id": str(rule_version.id),
                        "version": int(rule_version.version),
                        "event_type": str(rule_version.event_type),
                        "severity": str(rule_version.severity),
                        "condition": dict(rule_version.condition_json or {}),
                        "priority": int(rule_version.priority or 100),
                        "exclusive": bool(rule_version.exclusive),
                        "event_class": str(
                            rule_version.event_class or "fault"
                        ),
                        "evaluation_scope": str(
                            rule_version.evaluation_scope or "both"
                        ),
                        "recovery": dict(rule_version.recovery_json or {}),
                        "correlation_keys": list(
                            rule_version.correlation_key_json or []
                        ),
                        "digest": str(rule_version.digest or ""),
                    }
                    for rule_version, rule in rule_rows
                ],
            }

    def _dependency_blockers(
        self,
        db: Any,
        version: CheckItemRuntimeBundleVersionRecord,
        *,
        require_active: bool,
        allowed_draft_implementation_version_id: str | None = None,
    ) -> list[str]:
        blockers: list[str] = []
        expected_dependencies = dict(version.dependency_digests_json or {})
        check_version = db.get(
            CheckItemVersionRecord,
            version.check_item_version_id,
        )
        implementation_version = db.get(
            CollectionImplementationVersionRecord,
            version.implementation_version_id,
        )
        if check_version is None:
            blockers.append("runtime check-item version is missing")
        else:
            if require_active and str(check_version.status) != "active":
                blockers.append("runtime check-item version is not active")
            current_check_dependencies = {
                "check_item": str(check_version.digest),
                "freshness_policy": _digest(
                    dict(check_version.freshness_policy_json or {})
                ),
                "empty_result_policy": _digest(
                    dict(check_version.empty_result_policy_json or {})
                ),
                "coverage_policy": _digest(
                    dict(check_version.coverage_policy_json or {})
                ),
                "assessment_policy": str(
                    normalize_assessment_policy_contract(
                        dict(check_version.canonical_contract_json or {}),
                        dict(check_version.applicability_json or {}),
                    )["digest"]
                ),
            }
            for key, current_digest in current_check_dependencies.items():
                if str(expected_dependencies.get(key) or "") != current_digest:
                    blockers.append(
                        f"runtime dependency digest changed: {key}"
                    )
        if implementation_version is None:
            blockers.append("runtime implementation version is missing")
        else:
            if (
                require_active
                and str(implementation_version.status) != "active"
                and str(implementation_version.id)
                != str(allowed_draft_implementation_version_id or "")
            ):
                blockers.append("runtime implementation version is not active")
            current_implementation_dependencies = {
                "implementation": str(implementation_version.digest),
                "output_quality_policy": _digest(
                    dict(
                        implementation_version.output_quality_policy_json
                        or {}
                    )
                ),
            }
            for key, current_digest in (
                current_implementation_dependencies.items()
            ):
                if str(expected_dependencies.get(key) or "") != current_digest:
                    blockers.append(
                        f"runtime dependency digest changed: {key}"
                    )
        canonical_catalog = rule_semantics_catalog(dict(check_version.canonical_contract_json or {})) if check_version else load_canonical_catalog()
        if (
            str(expected_dependencies.get("canonical_catalog") or "")
            != str(canonical_catalog.digest)
            or str(version.canonical_catalog_digest or "")
            != str(canonical_catalog.digest)
        ):
            blockers.append(
                "runtime dependency digest changed: canonical_catalog"
            )
        if check_version is not None and implementation_version is not None:
            canonical_rows = [
                dict(item)
                for item in dict(
                    check_version.canonical_contract_json or {}
                ).get("observations")
                or []
                if isinstance(item, dict)
            ]
            current_requirement_profile = (
                derive_asset_requirement_profile(
                    result_mode=str(check_version.result_mode),
                    assessment_mode=str(
                        check_version.assessment_mode
                    ),
                    canonical_rows=canonical_rows,
                    canonical_catalog=canonical_catalog,
                    direct_projection=is_direct_projection_adapter(
                        dict(implementation_version.adapter_spec_json or {})
                    ),
                )
            )
            current_requirement_digest = _digest(
                current_requirement_profile
            )
            if (
                str(
                    expected_dependencies.get(
                        "asset_requirement_profile"
                    )
                    or ""
                )
                != current_requirement_digest
            ):
                blockers.append(
                    "runtime dependency digest changed: "
                    "asset_requirement_profile"
                )
            frozen_requirement_profile = dict(
                dict(version.compatibility_json or {}).get(
                    "asset_requirement_profile"
                )
                or {}
            )
            if (
                not frozen_requirement_profile
                or _digest(frozen_requirement_profile)
                != current_requirement_digest
            ):
                blockers.append(
                    "runtime asset requirement profile is missing or stale"
                )
            current_assessment_policy = normalize_assessment_policy_contract(
                dict(check_version.canonical_contract_json or {}),
                dict(check_version.applicability_json or {}),
            )
            frozen_assessment_policy = dict(
                dict(version.compatibility_json or {}).get(
                    "assessment_policy"
                )
                or {}
            )
            if (
                not frozen_assessment_policy
                or str(frozen_assessment_policy.get("digest") or "")
                != str(current_assessment_policy["digest"])
            ):
                blockers.append(
                    "runtime assessment policy is missing or stale"
                )
        assets = (
            db.query(RuntimeBundleAssetRecord)
            .filter(RuntimeBundleAssetRecord.bundle_version_id == version.id)
            .all()
        )
        for asset in assets:
            model, digest_field = {
                "parser_template": (ParserTemplate, "sha256"),
                "command_adapter_projection": (
                    CollectionImplementationVersionRecord,
                    "digest",
                ),
                "change_policy": (ChangePolicyVersionRecord, "digest"),
                "current_state_rule": (EventRuleVersionRecord, "digest"),
                "event_rule": (EventRuleVersionRecord, "digest"),
                "identity_policy": (EntityIdentityPolicyVersionRecord, "digest"),
                "comparison_profile": (
                    TextComparisonProfileVersionRecord,
                    "digest",
                ),
            }.get(str(asset.asset_type), (None, None))
            if model is None:
                continue
            row = db.get(model, asset.asset_version_id)
            if row is None:
                blockers.append(
                    f"runtime dependency is missing: {asset.asset_type}:{asset.asset_version_id}"
                )
                continue
            if str(getattr(row, digest_field) or "") != str(asset.asset_digest):
                blockers.append(
                    f"runtime dependency digest changed: "
                    f"{asset.asset_type}:{asset.asset_version_id}"
                )
            if asset.asset_type == "parser_template":
                frozen_compatibility_digest = str(
                    dict(asset.metadata_json or {}).get(
                        "compatibility_digest"
                    )
                    or ""
                )
                current_compatibility_digest = _digest(
                    dict(getattr(row, "validation_json", None) or {}).get(
                        "compatibility"
                    )
                    or {}
                )
                if (
                    frozen_compatibility_digest
                    and frozen_compatibility_digest
                    != current_compatibility_digest
                ):
                    blockers.append(
                        "runtime Parser compatibility evidence changed: "
                        f"{asset.asset_version_id}"
                    )
            if require_active and str(getattr(row, "status", "")) != "active":
                blockers.append(
                    f"runtime dependency is not active: "
                    f"{asset.asset_type}:{asset.asset_version_id}"
                )
        event_rule_bindings: dict[str, set[str]] = {}
        for item in assets:
            if (
                item.asset_type not in {"current_state_rule", "event_rule"}
                or not item.observation_type
            ):
                continue
            event_rule_bindings.setdefault(
                str(item.asset_version_id),
                set(),
            ).add(str(item.observation_type))
        if _digest(
            {
                "bundle_id": version.bundle_id,
                "check_item_version_id": version.check_item_version_id,
                "implementation_version_id": version.implementation_version_id,
                "parser_bundle": version.parser_bundle_json,
                "canonical_catalog_version": version.canonical_catalog_version,
                "canonical_catalog_digest": version.canonical_catalog_digest,
                "entity_identity_policy_version_id": (
                    version.entity_identity_policy_version_id
                ),
                "comparison_profile_version_id": version.comparison_profile_version_id,
                "dependency_digests": version.dependency_digests_json,
                "event_rule_bindings": {
                    version_id: sorted(observation_types)
                    for version_id, observation_types in sorted(
                        event_rule_bindings.items()
                    )
                },
                "compatibility": version.compatibility_json,
            }
        ) != str(version.digest):
            blockers.append("runtime bundle payload digest mismatch")
        return list(dict.fromkeys(blockers))

    @staticmethod
    def _serialize(
        db: Any,
        root: CheckItemRuntimeBundleRecord,
        version: CheckItemRuntimeBundleVersionRecord,
    ) -> dict[str, Any]:
        assets = (
            db.query(RuntimeBundleAssetRecord)
            .filter(RuntimeBundleAssetRecord.bundle_version_id == version.id)
            .order_by(
                RuntimeBundleAssetRecord.asset_type,
                RuntimeBundleAssetRecord.observation_type,
                RuntimeBundleAssetRecord.asset_version_id,
            )
            .all()
        )
        return {
            **root.to_dict(),
            "bundle_id": root.id,
            "version_id": version.id,
            "version": version.version,
            "version_status": version.status,
            "digest": version.digest,
            "check_item_version_id": version.check_item_version_id,
            "implementation_version_id": version.implementation_version_id,
            "parser_bundle": dict(version.parser_bundle_json or {}),
            "canonical_catalog_version": version.canonical_catalog_version,
            "canonical_catalog_digest": version.canonical_catalog_digest,
            "entity_identity_policy_version_id": (
                version.entity_identity_policy_version_id
            ),
            "comparison_profile_version_id": version.comparison_profile_version_id,
            "dependency_digests": dict(version.dependency_digests_json or {}),
            "compatibility": dict(version.compatibility_json or {}),
            "asset_requirement_profile": dict(
                dict(version.compatibility_json or {}).get(
                    "asset_requirement_profile"
                )
                or {}
            ),
            "asset_requirement_profile_digest": dict(
                version.dependency_digests_json or {}
            ).get("asset_requirement_profile"),
            "validation_report": dict(version.validation_report_json or {}),
            "assets": [item.to_dict() for item in assets],
            "activated_at": version.activated_at,
            "deprecated_at": version.deprecated_at,
        }
