"""Build the rule-generation input owned by a CheckItem version.

The context deliberately contains canonical semantics only.  Parser templates,
raw CLI records, sample datasets and vendor fields belong to collection and are
not valid inputs to ChangePolicy/EventRule generation.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from src.apps.rule_governance.backend.check_items.runtime_bundle_assembly import (
    derive_asset_requirement_profile,
)
from src.apps.rule_governance.backend.rule_control.asset_requirements import (
    rule_asset_composition_errors,
)
from src.core.check_items.assessment_policy import canonical_digest
from src.core.check_items.canonical import rule_semantics_catalog
from src.core.patrol.change_policy import (
    infer_change_policy,
    policy_from_dict,
    validate_change_policy,
)
from src.core.patrol.change_policy_repository import (
    ChangePolicyRepository,
    change_policy_digest,
)
from src.core.patrol.event_constraint_governance import scope_event_constraints_to_policy
from src.core.patrol.event_rule_compiler import compile_event_rule_intents
from src.core.patrol.event_rules import EventRuleRepository
from src.infrastructure.db.models import (
    CheckItemRecord,
    CheckItemVersionRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session

_FORBIDDEN_COLLECTION_KEYS = {
    "dataset_id",
    "dataset_ids",
    "historical_records",
    "parser_template_id",
    "parser_template_ids",
    "raw_cli",
    "raw_output",
    "records",
    "sample",
    "samples",
    "source_fields",
    "textfsm",
    "vendor_fields",
}


def _forbidden_paths(value: Any, *, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key.casefold() in _FORBIDDEN_COLLECTION_KEYS:
                errors.append(child_path)
            errors.extend(_forbidden_paths(child, path=child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            errors.extend(_forbidden_paths(child, path=f"{path}[{index}]"))
    return errors


def _rule_requirements(profile: Mapping[str, Any]) -> dict[str, Any]:
    requirements = dict(profile.get("requirements") or {})
    current = dict(requirements.get("current_state_rule") or {})
    change = dict(requirements.get("change_policy") or {})
    event = dict(requirements.get("event_rule") or {})
    return {
        "requires_observation_contract": False,
        "requires_change_policy": change.get("required") is True or bool(change.get("conditional_observation_types")),
        "requires_current_state_rule": current.get("required") is True or bool(current.get("conditional_observation_types")),
        "requires_event_rule": event.get("required") is True or bool(event.get("conditional_event_types_by_observation")),
        "requires_event_rule_assets": (
            current.get("required") is True or event.get("required") is True
            or bool(current.get("conditional_observation_types")) or bool(event.get("conditional_event_types_by_observation"))
        ),
        "requires_historical_replay": False,
        "current_state_observation_types": list(
            [*(current.get("required_observation_types") or []), *(current.get("conditional_observation_types") or [])]
        ),
        "change_policy_observation_types": list(
            [*(change.get("required_observation_types") or []), *(change.get("conditional_observation_types") or [])]
        ),
        "required_event_types_by_observation": dict(
            {**(event.get("required_event_types_by_observation") or {}), **(event.get("conditional_event_types_by_observation") or {})}
        ),
    }


def build_canonical_rule_context(
    *,
    check_item_id: str,
    check_item_version_id: str,
    result_mode: str,
    assessment_mode: str,
    canonical_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile one immutable, vendor-neutral rule-generation context."""

    from src.core.check_items.value_contract import normalize_value_contract
    contract = normalize_value_contract(dict(canonical_contract or {}))
    forbidden = _forbidden_paths(contract)
    if forbidden:
        raise ValueError(
            "canonical rule context cannot contain collection evidence: "
            + ", ".join(sorted(forbidden))
        )
    rows = [
        dict(item)
        for item in contract.get("observations") or []
        if isinstance(item, Mapping)
        and str(item.get("observation_type") or "").strip()
    ]
    if result_mode == "structured" and not rows:
        raise ValueError("structured CheckItem requires canonical observations")

    catalog = rule_semantics_catalog(contract)
    state_semantics: dict[str, dict[str, Any]] = {}
    targets: list[dict[str, Any]] = []
    for row in rows:
        observation_type = str(row["observation_type"]).strip()
        definition = catalog.get(observation_type)
        if definition is None:
            raise ValueError(
                f"unknown canonical observation type: {observation_type}"
            )
        canonical_field = str(
            row.get("canonical_field") or observation_type.replace(".", "_")
        ).strip()
        dimensions = list(row.get("dimensions") or definition.dimensions)
        semantics = {
            "change_tracking": row.get("change_tracking", True),
            "event_on_change": row.get("event_on_change", False),
            "observation_type": observation_type,
            "semantic_type": str(
                row.get("semantic_type") or definition.semantic_type
            ),
            "value_types": list(definition.value_types),
            "unit": row.get("unit") or definition.canonical_unit,
            "dimensions": dimensions,
            "fixed_dimensions": dict(row.get("fixed_dimensions") or {}),
            "allowed_values": list(definition.allowed_values),
            "state_groups": {
                key: list(values)
                for key, values in definition.state_groups.items()
            },
            "assessment_roles": list(
                row.get("assessment_roles") or definition.assessment_roles
            ),
            "policy_constraints": dict(row.get("policy_constraints") or {}),
            "event_constraints": dict(row.get("event_constraints") or {}),
            "threshold_references": dict(
                row.get("threshold_references") or {}
            ),
            "baseline_comparison": dict(
                row.get("baseline_comparison") or {}
            ),
            "health_policy": dict(row.get("health_policy") or {}),
            "context_fields": list(row.get("context_fields") or []),
        }
        state_semantics[canonical_field] = semantics
        targets.append(
            {
                "observation_type": observation_type,
                "canonical_field": canonical_field,
                "required": row.get("required", True) is not False,
                **semantics,
            }
        )

    entity_type = str(
        contract.get("canonical_entity_type")
        or contract.get("entity_type")
        or ""
    ).strip()
    identity_dimensions = [
        str(item).strip()
        for item in contract.get("identity_dimensions") or []
        if str(item).strip()
    ]
    if result_mode == "structured" and (not entity_type or not identity_dimensions):
        raise ValueError(
            "structured CheckItem requires entity_type and identity_dimensions"
        )

    asset_profile = derive_asset_requirement_profile(
        result_mode=result_mode,
        assessment_mode=assessment_mode,
        canonical_rows=rows,
        canonical_catalog=catalog,
        direct_projection=True,
    )
    rule_contract = {
        "schema_version": "canonical-rule-contract/v1",
        "canonical_entity_type": entity_type,
        "entity_type": entity_type,
        "entity_identity_fields": identity_dimensions,
        "identity_dimensions": identity_dimensions,
        "identity_dimension_rules": dict(
            contract.get("identity_dimension_rules") or {}
        ),
        "required_fields": [
            item["canonical_field"] for item in targets if item["required"]
        ],
        "optional_fields": [
            item["canonical_field"] for item in targets if not item["required"]
        ],
        "state_semantics": state_semantics,
    }
    payload = {
        "schema_version": "canonical-rule-context/v1",
        "check_item_id": str(check_item_id),
        "check_item_version_id": str(check_item_version_id),
        "result_mode": str(result_mode),
        "assessment_mode": str(assessment_mode),
        "contract": rule_contract,
        "targets": targets,
        "asset_requirement_profile": asset_profile,
        "rule_asset_requirements": _rule_requirements(asset_profile),
    }
    payload["digest"] = canonical_digest(payload)
    return payload


def _compile_policy_candidate(target: Mapping[str, Any]) -> dict[str, Any]:
    constraints = dict(target.get("policy_constraints") or {})
    dimension_selector = {
        str(name): values[0]
        for name, values in dict(
            constraints.get("applies_to_dimensions") or {}
        ).items()
        if isinstance(values, list) and len(values) == 1
    }
    proposal = infer_change_policy(
        observation_type=str(target["observation_type"]),
        field_name=str(target["canonical_field"]),
        field_semantics={
            "semantic_type": target.get("semantic_type"),
            "unit": target.get("unit"),
            "threshold_references": target.get("threshold_references") or {},
        },
        dimension_selector=dimension_selector,
    ).to_dict()
    if target.get("semantic_type") in {"enum", "boolean"}:
        # An enum or a boolean names its normal and abnormal values the way a state does.
        proposal["compare_mode"] = "state_transition"
    references = dict(target.get('threshold_references') or {})
    if references and target.get('semantic_type') in {'gauge', 'threshold'}:
        proposal['threshold_references'] = references
        proposal['thresholds'] = {}
        proposal['recovery_threshold'] = None
        proposal['compare_mode'] = 'threshold_and_deadband' if {'high', 'critical_high'} & references.keys() else 'threshold_low_and_deadband'
    if target.get("event_on_change"):
        proposal.update(deadband=0, thresholds={}, threshold_references={})
        # State, enum and boolean are compared as a move between values, so that one
        # with normal and abnormal values can say which way it moved.
        proposal["compare_mode"] = {
            "counter": "counter_delta", "gauge": "numeric_delta", "threshold": "numeric_delta",
            "state": "state_transition", "enum": "state_transition", "boolean": "state_transition",
        }.get(target.get("semantic_type"), "exact")
        modes = constraints.get('allowed_compare_modes') or []
        if len(modes) == 1 and modes[0] in {'exact', 'state_transition', 'enum_transition', 'numeric_delta', 'counter_delta', 'hash_diff'}:
            proposal['compare_mode'] = modes[0]
        proposal["source"] = "check_item_all_value_changes"
        proposal["reason"] = "Every available standard Value change produces an informational event."
        return proposal
    allowed_modes = [
        str(item)
        for item in constraints.get("allowed_compare_modes") or []
        if str(item)
    ]
    if len(allowed_modes) == 1:
        proposal["compare_mode"] = allowed_modes[0]
    if isinstance(constraints.get("required_thresholds"), Mapping):
        proposal["thresholds"] = dict(constraints["required_thresholds"])
    if constraints.get("required_recovery_threshold") is not None:
        proposal["recovery_threshold"] = constraints[
            "required_recovery_threshold"
        ]
    if constraints.get("required_deadband") is not None:
        proposal["deadband"] = constraints["required_deadband"]
    elif constraints.get("minimum_hysteresis") is not None:
        proposal["deadband"] = constraints["minimum_hysteresis"]
    if target.get("baseline_comparison"):
        proposal["baseline_comparison"] = dict(target["baseline_comparison"])
    allowed_durations = list(
        constraints.get("allowed_minimum_durations") or []
    )
    if (
        "allowed_minimum_durations" in constraints
        and proposal.get("minimum_duration") not in allowed_durations
    ):
        proposal["minimum_duration"] = (
            None if None in allowed_durations else allowed_durations[0]
            if allowed_durations else None
        )
    proposal["source"] = "canonical_check_item_semantics"
    proposal["confidence"] = 1.0
    proposal["reason"] = (
        "Compiled deterministically from the versioned CheckItem canonical "
        "semantics; no parser records or sample datasets were used."
    )
    return proposal


def generate_canonical_rule_candidates(
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Generate deterministic ChangePolicy/EventRule candidates from B only."""

    forbidden = _forbidden_paths(context)
    if forbidden:
        raise ValueError(
            "rule generation context contains forbidden collection evidence: "
            + ", ".join(sorted(forbidden))
        )
    contract = dict(context.get("contract") or {})
    requirements = dict(context.get("rule_asset_requirements") or {})
    change_targets = set(requirements.get("change_policy_observation_types") or [])
    current_targets = set(requirements.get("current_state_observation_types") or [])
    event_targets = set(
        dict(requirements.get("required_event_types_by_observation") or {})
    )
    wanted = change_targets | current_targets | event_targets
    evidence_bound = set(
        dict(
            dict(
                dict(context.get("asset_requirement_profile") or {}).get("requirements") or {}
            ).get("event_rule")
            or {}
        ).get("evidence_bound_event_constraints_by_observation")
        or {}
    )
    policies: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []

    for target in context.get("targets") or []:
        if not isinstance(target, Mapping):
            continue
        observation_type = str(target.get("observation_type") or "")
        if observation_type not in wanted:
            continue
        constraints = dict(target.get("policy_constraints") or {})
        references = dict(target.get("threshold_references") or {})
        if (
            constraints.get("require_structured_threshold_references") is True
            and not references
        ):
            blockers.append(
                {
                    "code": "canonical_threshold_reference_missing",
                    "observation_type": observation_type,
                    "message": (
                        "标准语义 B 要求设备阈值，但尚未声明标准阈值字段关系。"
                    ),
                }
            )
            continue
        candidate = _compile_policy_candidate(
            {**dict(target), "threshold_references": references}
        )
        validation = validate_change_policy(policy_from_dict(candidate))
        if validation.get("success") is not True:
            blockers.append(
                {
                    "code": "change_policy_invalid",
                    "observation_type": observation_type,
                    "message": "; ".join(validation.get("errors") or []),
                }
            )
            continue
        tracked = bool(target.get('change_tracking', True)) and observation_type in change_targets
        if tracked:
            policies.append(candidate)
        event_constraints = dict(target.get("event_constraints") or {})
        # Threshold events the device's own thresholds back become required once the
        # value is tracked: the runtime bundle asks for exactly the roles this policy
        # proves, so the same roles are compiled here.
        bound = (
            tracked
            and observation_type in evidence_bound
            and observation_type not in current_targets | event_targets
        )
        if bound:
            scoped = scope_event_constraints_to_policy(event_constraints, candidate)
            if scoped.get("success") is not True:
                blockers.append(
                    {
                        "code": "evidence_bound_event_roles_unresolved",
                        "observation_type": observation_type,
                        "message": "; ".join(scoped.get("errors") or []),
                    }
                )
                continue
            event_constraints = dict(scoped.get("constraints") or {})
        elif observation_type not in current_targets | event_targets:
            continue
        compiled = compile_event_rule_intents(
            candidate_rules=[],
            contract=contract,
            change_policy=candidate,
            event_constraints=event_constraints,
            rule_asset_requirements=requirements,
        )
        if compiled.get("success") is not True:
            blockers.append(
                {
                    "code": "event_rule_invalid",
                    "observation_type": observation_type,
                    "message": "; ".join(compiled.get("errors") or []),
                }
            )
            continue
        compiled_rules = [
            dict(rule)
            for rule in compiled.get("rules") or []
            if isinstance(rule, Mapping)
        ]
        if (
            (bound or observation_type in current_targets | event_targets)
            and (
                compiled.get("compiled") is not True
                or not compiled_rules
            )
        ):
            blockers.append(
                {
                    "code": "canonical_rule_semantics_missing",
                    "observation_type": observation_type,
                    "message": (
                        "标准语义 B 声明需要健康判断，但尚未提供可执行的"
                        "状态分组或阈值约束。"
                    ),
                }
            )
            continue
        rules.extend(
            {
                **rule,
                "observation_type": observation_type,
            }
            for rule in compiled_rules
        )
    payload = {
        "schema_version": "canonical-rule-candidates/v1",
        "check_item_id": context.get("check_item_id"),
        "check_item_version_id": context.get("check_item_version_id"),
        "context_digest": context.get("digest"),
        "success": not blockers,
        "change_policies": policies,
        "event_rules": rules,
        "blockers": blockers,
        "asset_requirements": requirements,
        "input_summary": {
            "source": "canonical_semantics_only",
            "sample_count": 0,
            "parser_template_count": 0,
        },
    }
    payload["digest"] = canonical_digest(payload)
    return payload


class CanonicalRuleContextService:
    """Load a CheckItem version and expose its deterministic rule context."""

    def __init__(
        self,
        session_scope: Callable[
            [], AbstractContextManager[Any]
        ] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def get(self, check_item_version_id: str) -> dict[str, Any]:
        with self._session_scope() as db:
            version = db.get(CheckItemVersionRecord, str(check_item_version_id))
            if version is None:
                raise ValueError(
                    f"CheckItem version not found: {check_item_version_id}"
                )
            root = db.get(CheckItemRecord, str(version.check_item_id))
            if root is None:
                raise ValueError(
                    f"CheckItem not found: {version.check_item_id}"
                )
            context = build_canonical_rule_context(
                check_item_id=str(root.id),
                check_item_version_id=str(version.id),
                result_mode=str(version.result_mode),
                assessment_mode=str(version.assessment_mode),
                canonical_contract=dict(version.canonical_contract_json or {}),
            )
            context["check_item"] = {
                "name": str(root.name),
                "category": str(root.category),
                "status": str(root.status),
                "version": int(version.version),
                "version_status": str(version.status),
            }
            return context

    def generate_candidates(self, check_item_version_id: str) -> dict[str, Any]:
        return generate_canonical_rule_candidates(self.get(check_item_version_id))

    def create_drafts(
        self,
        check_item_version_id: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        candidates = self.generate_candidates(check_item_version_id)
        if candidates.get("success") is not True:
            raise ValueError(
                "canonical rule candidates are blocked: "
                + "; ".join(
                    str(item.get("message") or "")
                    for item in candidates.get("blockers") or []
                )
            )
        check_item_id = str(candidates["check_item_id"])
        # A second vendor adapter reuses the exact rules of the same human-owned
        # CheckItem version; do not create replacement drafts for every command.
        with self._session_scope() as db:
            existing = db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE",
                RuleGenerationJobRecord.asset_ref == f"check_item_version:{check_item_version_id}",
                RuleGenerationJobRecord.asset_digest == candidates["digest"],
                RuleGenerationJobRecord.status.in_(["active", "pending_approval"]),
            ).order_by(RuleGenerationJobRecord.created_at.desc()).first()
            if existing:
                children = db.query(RuleGenerationJobRecord).filter(RuleGenerationJobRecord.id.in_(existing.payload_json.get("child_job_ids", []))).all()
                return {**candidates, "drafts_created": True, "reused": True,
                        "bundle_id": existing.id, "bundle_status": existing.status,
                        "change_policy_assets": [{"version_id": child.asset_version_id, "policy_id": child.asset_id,
                                                  "policy": child.payload_json.get("policy", {}), "digest": child.asset_digest}
                                                 for child in children if child.job_type == "CHANGE_POLICY"],
                        "event_rule_version_ids": [child.asset_version_id for child in children if child.job_type == "EVENT_RULE"],
                        "rule_job_ids": [child.id for child in children]}
        policy_assets: list[dict[str, Any]] = []
        rule_assets: list[dict[str, Any]] = []
        for policy in candidates.get("change_policies") or []:
            observation_type = str(policy.get("observation_type") or "")
            policy_repository = ChangePolicyRepository()
            policy_digest = change_policy_digest(
                policy_from_dict(dict(policy)).to_dict()
            )
            existing_policy = next(
                (
                    item
                    for item in policy_repository.list_versions(
                        status="draft",
                        observation_type=observation_type,
                    )
                    if str(item.get("check_item_version_id") or "")
                    == str(check_item_version_id)
                    and str(item.get("digest") or "") == policy_digest
                ),
                None,
            )
            policy_assets.append(
                existing_policy
                or policy_repository.create_policy_version(
                    name=f"{check_item_id}.{observation_type}.change",
                    description=(
                        f"Generated from canonical semantics of {check_item_id}."
                    ),
                    policy=dict(policy),
                    status="draft",
                    check_item_version_id=check_item_version_id,
                    validation_report={
                        "success": True,
                        "source": "canonical_semantics_only",
                        "context_digest": candidates["context_digest"],
                    },
                    created_by=actor,
                )
            )
        for rule in candidates.get("event_rules") or []:
            rule_name = f"{check_item_id}.{rule.get('name') or rule['event_type']}"
            rule_repository = EventRuleRepository()
            existing_rule = next(
                (
                    item
                    for item in rule_repository.list_versions(
                        status="draft",
                        event_type=str(rule["event_type"]),
                        limit=None,
                    )
                    if str(item.get("check_item_version_id") or "")
                    == str(check_item_version_id)
                    and str(item.get("rule_name") or "") == rule_name
                    and dict(item.get("condition") or {})
                    == dict(rule.get("condition") or {})
                    and str(item.get("severity") or "")
                    == str(rule.get("severity") or "warning")
                ),
                None,
            )
            version_id = (
                str(existing_rule["version_id"])
                if existing_rule is not None
                else rule_repository.create_rule_version(
                    name=rule_name,
                    event_type=str(rule["event_type"]),
                    severity=str(rule.get("severity") or "warning"),
                    condition=dict(rule.get("condition") or {}),
                    description=str(rule.get("description") or ""),
                    created_by=actor,
                    check_item_version_id=check_item_version_id,
                    validation_report={
                        "success": True,
                        "source": "canonical_semantics_only",
                        "context_digest": candidates["context_digest"],
                    },
                    status="draft",
                    priority=int(rule.get("priority") or 100),
                    exclusive=rule.get("exclusive") is True,
                    event_class=str(rule.get("event_class") or "fault"),
                    evaluation_scope=str(rule.get("evaluation_scope") or "both"),
                    recovery=dict(rule.get("recovery") or {}),
                    correlation_keys=list(rule.get("correlation_keys") or []),
                )
            )
            rule_asset = next(
                (
                    item
                    for item in rule_repository.list_versions(limit=None)
                    if str(item.get("version_id") or "") == str(version_id)
                ),
                None,
            )
            if rule_asset is None:
                raise ValueError(f"event rule version was not persisted: {version_id}")
            rule_assets.append(
                {
                    **rule_asset,
                    "observation_type": rule.get("observation_type"),
                }
            )

        bundle = self._create_semantic_bundle(
            candidates=candidates,
            policy_assets=policy_assets,
            rule_assets=rule_assets,
            actor=actor,
        )
        return {
            **candidates,
            "drafts_created": True,
            "change_policy_assets": policy_assets,
            "event_rule_version_ids": [
                str(item["version_id"]) for item in rule_assets
            ],
            "bundle_id": bundle.get("id") if bundle else None,
            "bundle_status": bundle.get("status") if bundle else None,
            "rule_job_ids": list(bundle.get("child_job_ids") or []) if bundle else [],
        }

    def _create_semantic_bundle(
        self,
        *,
        candidates: Mapping[str, Any],
        policy_assets: list[Mapping[str, Any]],
        rule_assets: list[Mapping[str, Any]],
        actor: str,
    ) -> dict[str, Any] | None:
        """Attach B-derived assets to the existing bundle approval flow.

        A CheckItem already owns the canonical semantics, so this deliberately
        creates no ObservationContract or sample context.  It only materializes
        the small parent/child governance graph used by the normal review and
        atomic-release UI.
        """

        requirements = dict(candidates.get("asset_requirements") or {})
        if not policy_assets and not rule_assets:
            return None
        counts = {
            "CHANGE_POLICY": len(policy_assets),
            "EVENT_RULE": len(rule_assets),
        }
        composition_errors = rule_asset_composition_errors(
            counts=counts,
            requirements=requirements,
            change_policy_roles=[dict(item.get("policy") or {}) for item in policy_assets],
            event_rule_roles=[
                {
                    "condition": dict(item.get("condition") or {}),
                    "event_type": item.get("event_type"),
                    "event_class": item.get("event_class"),
                    "evaluation_scope": item.get("evaluation_scope"),
                }
                for item in rule_assets
            ],
        )
        if composition_errors:
            raise ValueError("; ".join(composition_errors))

        check_item_id = str(candidates["check_item_id"])
        check_item_version_id = str(candidates["check_item_version_id"])
        digest = str(candidates["digest"])
        asset_ref = f"check_item_version:{check_item_version_id}"
        with self._session_scope() as db:
            existing = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE",
                    RuleGenerationJobRecord.asset_ref == asset_ref,
                    RuleGenerationJobRecord.asset_digest == digest,
                    RuleGenerationJobRecord.status.in_(("pending_approval", "active")),
                )
                .order_by(RuleGenerationJobRecord.created_at.desc())
                .first()
            )
            if existing is not None:
                return {
                    "id": str(existing.id),
                    "status": str(existing.status),
                    "child_job_ids": list(
                        dict(existing.payload_json or {}).get("child_job_ids") or []
                    ),
                }

            now = datetime.now(timezone.utc)
            bundle_id = str(uuid.uuid4())
            child_jobs: list[RuleGenerationJobRecord] = []
            for asset in policy_assets:
                child_jobs.append(
                    RuleGenerationJobRecord(
                        id=str(uuid.uuid4()),
                        job_type="CHANGE_POLICY",
                        status="pending_approval",
                        source_ref=f"rule_asset_bundle:{bundle_id}",
                        asset_ref=f"change_policy_version:{asset['version_id']}",
                        asset_type="CHANGE_POLICY",
                        asset_id=str(asset["policy_id"]),
                        asset_version_id=str(asset["version_id"]),
                        asset_digest=str(asset["digest"]),
                        payload_json={
                            "schema_version": "check-item-rule-bundle/v1",
                            "check_item_id": check_item_id,
                            "check_item_version_id": check_item_version_id,
                            "policy": dict(asset.get("policy") or {}),
                        },
                        validation_report_json={
                            "success": True,
                            "source": "canonical_semantics_only",
                            "context_digest": candidates["context_digest"],
                        },
                        approval_required=True,
                        created_by=actor,
                        created_at=now,
                        updated_at=now,
                    )
                )
            for asset in rule_assets:
                child_jobs.append(
                    RuleGenerationJobRecord(
                        id=str(uuid.uuid4()),
                        job_type="EVENT_RULE",
                        status="pending_approval",
                        source_ref=f"rule_asset_bundle:{bundle_id}",
                        asset_ref=f"event_rule_version:{asset['version_id']}",
                        asset_type="EVENT_RULE",
                        asset_id=str(asset["rule_id"]),
                        asset_version_id=str(asset["version_id"]),
                        asset_digest=str(asset["digest"]),
                        payload_json={
                            "schema_version": "check-item-rule-bundle/v1",
                            "check_item_id": check_item_id,
                            "check_item_version_id": check_item_version_id,
                            "rule": {
                                "name": asset.get("rule_name"),
                                "event_type": asset.get("event_type"),
                                "severity": asset.get("severity"),
                                "condition": dict(asset.get("condition") or {}),
                                "event_class": asset.get("event_class"),
                                "evaluation_scope": asset.get("evaluation_scope"),
                            },
                        },
                        validation_report_json={
                            "success": True,
                            "source": "canonical_semantics_only",
                            "context_digest": candidates["context_digest"],
                        },
                        approval_required=True,
                        created_by=actor,
                        created_at=now,
                        updated_at=now,
                    )
                )
            child_ids = [str(item.id) for item in child_jobs]
            report = {
                "success": True,
                "asset_requirements": requirements,
                "bundle_gate": {
                    "success": True,
                    "blockers": [],
                    "asset_requirements": requirements,
                },
                "context_gate": {
                    "success": True,
                    "mode": "check_item_semantics",
                    "check_item_id": check_item_id,
                    "check_item_version_id": check_item_version_id,
                    "context_digest": candidates["context_digest"],
                    "privacy": {
                        "success": True,
                        "raw_cli_included": False,
                        "scope_type": "canonical_semantics_only",
                        "source_snapshot_count": 0,
                        "candidate_record_count": 0,
                        "sampled_record_count": 0,
                    },
                },
                "historical_replay": {"success": True, "skipped": True},
            }
            bundle = RuleGenerationJobRecord(
                id=bundle_id,
                job_type="RULE_ASSET_BUNDLE",
                status="pending_approval",
                source_ref=f"check_item_rule_bundle:{check_item_version_id}",
                asset_ref=asset_ref,
                asset_type="CHECK_ITEM_RULES",
                asset_id=check_item_id,
                asset_version_id=check_item_version_id,
                asset_digest=digest,
                payload_json={
                    "schema_version": "check-item-rule-bundle/v1",
                    "flow": "check_item_semantics",
                    "check_item_id": check_item_id,
                    "check_item_version_id": check_item_version_id,
                    "context_digest": candidates["context_digest"],
                    "asset_scope": "check_item_rule_assets",
                    "asset_requirements": requirements,
                    "observation_types": sorted(
                        {
                            str(item.get("observation_type") or "").strip()
                            for item in [*policy_assets, *rule_assets]
                            if str(item.get("observation_type") or "").strip()
                        }
                    ),
                    "child_job_ids": child_ids,
                },
                validation_report_json=report,
                approval_required=True,
                created_by=actor,
                created_at=now,
                updated_at=now,
            )
            db.add_all([*child_jobs, bundle])
            db.flush()
            return {"id": bundle_id, "status": bundle.status, "child_job_ids": child_ids}


__all__ = [
    "CanonicalRuleContextService",
    "build_canonical_rule_context",
    "generate_canonical_rule_candidates",
]
