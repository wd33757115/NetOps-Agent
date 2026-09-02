"""Auditable human review and immutable rule asset bundle revisions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.common.governance import actor_matches_any
from src.core.rule_control.asset_requirements import (
    evaluate_requirement_replay_gate,
    normalize_rule_asset_requirements,
)
from src.core.rule_control.repository import RuleGenerationJobRepository
from src.core.rule_control.source_scope import (
    require_current_rule_generation_context,
)
from src.core.skills.executor import SkillExecutionError, execute_skill
from src.infrastructure.db.models import (
    ChangePolicyVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    ObservationContractRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skill_failure_detail(exc: SkillExecutionError) -> dict[str, Any]:
    message = str(exc)
    payload_start = message.find("{")
    if payload_start >= 0:
        try:
            payload = json.loads(message[payload_start:])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return {"success": False, "error": message}


class RuleAssetEngineeringRevisionError(ValueError):
    def __init__(self, message: str, *, validation: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.validation = validation or {}


def _execute_revision_skill(
    skill_name: str,
    params: dict[str, Any],
    *,
    stage: str,
) -> dict[str, Any]:
    try:
        return execute_skill(skill_name, params)
    except SkillExecutionError as exc:
        raise RuleAssetEngineeringRevisionError(
            f"engineering revision {stage} validation failed",
            validation={stage: _skill_failure_detail(exc)},
        ) from exc


def _active_asset_equivalence(
    *,
    change_policies: list[dict[str, Any]],
    event_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prove that a revision introduces no ungrounded policy or rule behavior."""

    if not change_policies or not event_rules:
        return {
            "success": False,
            "change_policy_count": 0,
            "event_rule_count": 0,
        }
    matched_policy_ids: list[str] = []
    evidenced_policy_types: list[str] = []
    matched_rule_ids: list[str] = []
    adapted_rule_names: list[str] = []

    def policy_behavior(value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item
            for key, item in value.items()
            if key not in {"confidence", "reason"}
        }

    def is_evidenced_threshold_policy(value: dict[str, Any]) -> bool:
        references = dict(value.get("threshold_references") or {})
        return (
            str(value.get("source") or "") == "structured_threshold_reference"
            and bool(references)
            and not bool(value.get("allow_static_threshold_fallback"))
            and not dict(value.get("thresholds") or {})
            and all(
                isinstance(reference, dict)
                and str(reference.get("source") or "")
                in {"structured_reference", "cross_record_set_reference"}
                and bool(str(reference.get("source_field_name") or "").strip())
                for reference in references.values()
            )
        )

    def condition_is_contract_adaptation(
        active: dict[str, Any],
        candidate: dict[str, Any],
    ) -> bool:
        if str(active.get("kind") or "") != str(candidate.get("kind") or ""):
            return False
        for key in ("entity_types", "observation_types"):
            if list(active.get(key) or []) != list(candidate.get(key) or []):
                return False
        kind = str(candidate.get("kind") or "")
        if kind == "transition":
            return all(
                active.get(key) == candidate.get(key)
                for key in ("from_group", "to_group", "allow_initial_target")
            ) and len(active.get("fields") or []) == len(candidate.get("fields") or [])
        if kind not in {"threshold_field_lte", "threshold_field_low_recovery"}:
            return False
        if str(active.get("reference_field") or "") != str(
            candidate.get("reference_field") or ""
        ):
            if not (
                kind == "threshold_field_low_recovery"
                and str(active.get("threshold_role") or "") == "critical_low"
                and str(candidate.get("threshold_role") or "") == "low"
                and str(active.get("reference_field") or "")
                in set(candidate.get("fallback_reference_fields") or [])
            ):
                return False
        elif str(active.get("threshold_role") or "") != str(
            candidate.get("threshold_role") or ""
        ):
            return False
        if len(active.get("fields") or []) != len(candidate.get("fields") or []):
            return False
        if float(candidate.get("hysteresis") or 0) < float(
            active.get("hysteresis") or 0
        ):
            return False
        active_exclusions = {
            str(item.get("code") or "")
            for item in dict(active.get("applicability") or {}).get(
                "exclude_when", []
            )
            if isinstance(item, dict)
        }
        candidate_exclusions = {
            str(item.get("code") or "")
            for item in dict(candidate.get("applicability") or {}).get(
                "exclude_when", []
            )
            if isinstance(item, dict)
        }
        return active_exclusions.issubset(candidate_exclusions)

    def rule_is_contract_adaptation(
        root: EventRuleRecord,
        active: EventRuleVersionRecord,
        candidate: dict[str, Any],
    ) -> bool:
        if any(
            (
                str(root.name or "") != str(candidate.get("name") or ""),
                str(active.event_type or "")
                != str(candidate.get("event_type") or ""),
                str(active.severity or "") != str(candidate.get("severity") or ""),
                str(active.event_class or "")
                != str(candidate.get("event_class") or ""),
                str(active.evaluation_scope or "")
                != str(candidate.get("evaluation_scope") or ""),
                bool(active.exclusive) is not bool(candidate.get("exclusive")),
                int(candidate.get("priority") or 0) < int(active.priority or 0),
            )
        ):
            return False
        active_recovery = dict(active.recovery_json or {})
        candidate_recovery = dict(candidate.get("recovery") or {})
        if not set(active_recovery.get("resolves") or []).issubset(
            set(candidate_recovery.get("resolves") or [])
        ):
            return False
        active_keys = set(active.correlation_key_json or [])
        candidate_keys = set(candidate.get("correlation_keys") or [])
        core_keys = {"device_id", "entity_type", "entity_key"}
        if not core_keys.issubset(active_keys) or not core_keys.issubset(
            candidate_keys
        ):
            return False
        return condition_is_contract_adaptation(
            dict(active.condition_json or {}),
            dict(candidate.get("condition") or {}),
        )

    with get_db_session() as db:
        active_policies = (
            db.query(ChangePolicyVersionRecord)
            .filter(ChangePolicyVersionRecord.status == "active")
            .all()
        )
        for policy in change_policies:
            match = next(
                (
                    version
                    for version in active_policies
                    if str(version.observation_type or "")
                    == str(policy.get("observation_type") or "")
                    and str(version.semantic_type or "")
                    == str(policy.get("semantic_type") or "")
                    and str(version.compare_mode or "")
                    == str(policy.get("compare_mode") or "")
                    and policy_behavior(dict(version.policy_json or {}))
                    == policy_behavior(policy)
                ),
                None,
            )
            if match is None:
                if is_evidenced_threshold_policy(policy):
                    evidenced_policy_types.append(
                        str(policy.get("observation_type") or "")
                    )
                    continue
                return {
                    "success": False,
                    "change_policy_count": (
                        len(matched_policy_ids) + len(evidenced_policy_types)
                    ),
                    "event_rule_count": 0,
                    "unmatched_asset": "change_policy",
                    "observation_type": policy.get("observation_type"),
                }
            matched_policy_ids.append(str(match.id))

        active_rule_rows = (
            db.query(EventRuleRecord, EventRuleVersionRecord)
            .join(
                EventRuleVersionRecord,
                EventRuleVersionRecord.rule_id == EventRuleRecord.id,
            )
            .filter(EventRuleVersionRecord.status == "active")
            .all()
        )
        for rule in event_rules:
            match = next(
                (
                    version
                    for root, version in active_rule_rows
                    if str(root.name or "") == str(rule.get("name") or "")
                    and str(version.event_type or "")
                    == str(rule.get("event_type") or "")
                    and str(version.severity or "")
                    == str(rule.get("severity") or "")
                    and dict(version.condition_json or {})
                    == dict(rule.get("condition") or {})
                    and int(version.priority or 0) == int(rule.get("priority") or 0)
                    and bool(version.exclusive) is bool(rule.get("exclusive"))
                    and str(version.event_class or "")
                    == str(rule.get("event_class") or "")
                    and str(version.evaluation_scope or "")
                    == str(rule.get("evaluation_scope") or "")
                    and dict(version.recovery_json or {})
                    == dict(rule.get("recovery") or {})
                    and list(version.correlation_key_json or [])
                    == list(rule.get("correlation_keys") or [])
                ),
                None,
            )
            if match is None:
                adapted_match = next(
                    (
                        version
                        for root, version in active_rule_rows
                        if rule_is_contract_adaptation(root, version, rule)
                    ),
                    None,
                )
                if adapted_match is not None:
                    match = adapted_match
                    adapted_rule_names.append(str(rule.get("name") or ""))
            if match is None:
                return {
                    "success": False,
                    "change_policy_count": len(matched_policy_ids),
                    "event_rule_count": len(matched_rule_ids),
                    "unmatched_asset": "event_rule",
                    "rule_name": rule.get("name"),
                }
            matched_rule_ids.append(str(match.id))
    return {
        "success": True,
        "change_policy_count": len(matched_policy_ids) + len(evidenced_policy_types),
        "event_rule_count": len(matched_rule_ids),
        "active_change_policy_version_ids": matched_policy_ids,
        "evidenced_change_policy_observation_types": evidenced_policy_types,
        "active_event_rule_version_ids": matched_rule_ids,
        "contract_adapted_event_rule_names": adapted_rule_names,
    }


class RuleAssetEngineeringRevisionService:
    """Review or replace a generated Bundle without mutating its asset versions."""

    def __init__(self, repository: RuleGenerationJobRepository | None = None) -> None:
        self.repository = repository or RuleGenerationJobRepository()

    def get_draft(self, bundle_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            bundle = db.get(RuleGenerationJobRecord, bundle_id)
            if bundle is None or bundle.job_type != "RULE_ASSET_BUNDLE":
                raise KeyError("rule asset bundle not found")
            child_ids = [
                str(item) for item in (bundle.payload_json or {}).get("child_job_ids") or []
            ]
            children = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.id.in_(child_ids))
                .all()
                if child_ids
                else []
            )
            by_id = {str(item.id): item for item in children}
            ordered = [by_id[item] for item in child_ids if item in by_id]
            contracts = [
                dict((item.payload_json or {}).get("proposal") or {})
                for item in ordered
                if item.job_type == "OBSERVATION_CONTRACT"
            ]
            policies = [
                dict((item.payload_json or {}).get("policy") or {})
                for item in ordered
                if item.job_type == "CHANGE_POLICY"
            ]
            rules = [
                dict((item.payload_json or {}).get("rule") or {})
                for item in ordered
                if item.job_type == "EVENT_RULE"
            ]
            context_id = str((bundle.payload_json or {}).get("context_id") or "")
            context = db.get(RuleGenerationContextRecord, context_id) if context_id else None
            report = dict(bundle.validation_report_json or {})
            review = dict((report.get("governance") or {}).get("engineering_review") or {})
            context_gate = dict(report.get("context_gate") or {})
            requirements = dict(
                report.get("asset_requirements")
                or (bundle.payload_json or {}).get("asset_requirements")
                or {}
            )
            return {
                "bundle_id": bundle.id,
                "bundle_status": bundle.status,
                "base_digest": bundle.asset_digest,
                "context_id": context_id or None,
                "context_digest": (
                    context.context_digest
                    if context else context_gate.get("context_digest")
                ),
                "offline_job_id": context.offline_job_id if context else None,
                "contract": contracts[0] if len(contracts) == 1 else None,
                "change_policies": policies,
                "event_rules": rules,
                "review": review,
                "editable": bundle.status in {"pending_approval", "rejected"},
                "context_mode": context_gate.get("mode"),
                "asset_requirements": requirements,
                "completeness": {
                    "contract_count": len(contracts),
                    "change_policy_count": len(policies),
                    "event_rule_count": len(rules),
                    "complete": (
                        (len(contracts) == 1 if requirements.get("requires_observation_contract") is True else not contracts)
                        and (bool(policies) if requirements.get("requires_change_policy") is True else not policies)
                        and (bool(rules) if requirements.get("requires_event_rule_assets") is True else not rules)
                    ),
                },
            }

    def review(
        self,
        bundle_id: str,
        *,
        decision: str,
        actor: str,
        reason: str | None = None,
        allow_same_actor: bool = False,
    ) -> dict[str, Any]:
        normalized = str(decision or "").strip().lower()
        if normalized not in {"accepted", "revision_requested"}:
            raise ValueError("engineering review decision must be accepted or revision_requested")
        normalized_reason = str(reason or "").strip()
        if normalized == "revision_requested" and len(normalized_reason) < 5:
            raise ValueError("engineering revision request must contain at least 5 characters")
        with get_db_session() as db:
            bundle = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.id == bundle_id,
                    RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE",
                )
                .with_for_update()
                .one_or_none()
            )
            if bundle is None:
                raise KeyError("rule asset bundle not found")
            if bundle.status != "pending_approval":
                raise ValueError("only a pending approval bundle can be reviewed")
            child_ids = [
                str(item)
                for item in dict(bundle.payload_json or {}).get("child_job_ids") or []
                if str(item)
            ]
            child_creators = (
                [
                    row.created_by
                    for row in (
                        db.query(RuleGenerationJobRecord)
                        .filter(RuleGenerationJobRecord.id.in_(child_ids))
                        .all()
                    )
                ]
                if child_ids
                else []
            )
            same_as_creator = actor_matches_any(
                actor,
                [bundle.created_by, *child_creators],
            )
            if same_as_creator and not allow_same_actor:
                raise ValueError(
                    "engineering review requires a reviewer different from "
                    "the candidate creator"
                )
            report = dict(bundle.validation_report_json or {})
            context_gate = dict(report.get("context_gate") or {})
            if context_gate.get("mode") == "immutable_dataset_context":
                context_id = str(context_gate.get("context_id") or "").strip()
                context = (
                    db.query(RuleGenerationContextRecord)
                    .filter(RuleGenerationContextRecord.id == context_id)
                    .with_for_update()
                    .one_or_none()
                )
                if context is None:
                    raise ValueError(
                        "engineering review immutable context is unavailable"
                    )
                require_current_rule_generation_context(
                    db,
                    context,
                    expected_context_digest=str(
                        context_gate.get("context_digest")
                        or (bundle.payload_json or {}).get("context_digest")
                        or ""
                    ),
                )
            governance = dict(report.get("governance") or {})
            history = list(governance.get("engineering_review_history") or [])
            review = {
                "decision": normalized,
                "actor": actor,
                "reason": normalized_reason,
                "reviewed_at": _now_iso(),
                "separation_of_duties_override": bool(
                    same_as_creator and allow_same_actor
                ),
                "base_digest": bundle.asset_digest,
            }
            history.append(review)
            governance["engineering_review"] = review
            governance["engineering_review_history"] = history
            report["governance"] = governance
            bundle.validation_report_json = report
            bundle.updated_at = datetime.now(timezone.utc)
            db.flush()
            return {"success": True, "bundle_id": bundle.id, "review": review}

    def revise(
        self,
        bundle_id: str,
        *,
        contract: dict[str, Any],
        change_policies: list[dict[str, Any]],
        event_rules: list[dict[str, Any]],
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if len(str(reason or "").strip()) < 5:
            raise ValueError("engineering revision reason must contain at least 5 characters")
        if len(str(idempotency_key or "").strip()) < 8:
            raise ValueError("engineering revision idempotency_key is required")
        if not isinstance(contract, dict) or not contract:
            raise ValueError("engineering revision requires one ObservationContract")
        if not isinstance(change_policies, list) or not all(
            isinstance(item, dict) for item in change_policies
        ):
            raise ValueError("engineering revision ChangePolicies must be a list")
        if not isinstance(event_rules, list) or not all(
            isinstance(item, dict) for item in event_rules
        ):
            raise ValueError("engineering revision EventRules must be a list")

        source_ref = f"engineering_revision:{bundle_id}:{str(idempotency_key).strip()}"
        existing = self.repository.find_job(job_type="RULE_ASSET_BUNDLE", source_ref=source_ref)
        if existing:
            return {
                "success": existing.get("status") in {"pending_approval", "active"},
                "created": False,
                "parent_bundle_id": bundle_id,
                "bundle": existing,
            }

        with get_db_session() as db:
            parent = db.get(RuleGenerationJobRecord, bundle_id)
            if parent is None or parent.job_type != "RULE_ASSET_BUNDLE":
                raise KeyError("rule asset bundle not found")
            if parent.status not in {"pending_approval", "rejected"}:
                raise ValueError("only pending or rejected bundles can be revised")
            payload = dict(parent.payload_json or {})
            context_id = str(payload.get("context_id") or "")
            context = db.get(RuleGenerationContextRecord, context_id) if context_id else None
            if context is None:
                raise ValueError("engineering revision requires the original immutable context")
            parent_report = dict(parent.validation_report_json or {})
            parent_context_gate = dict(parent_report.get("context_gate") or {})
            source_integrity = require_current_rule_generation_context(
                db,
                context,
                expected_context_digest=str(
                    parent_context_gate.get("context_digest")
                    or payload.get("context_digest")
                    or ""
                ),
            )
            child_ids = [str(item) for item in payload.get("child_job_ids") or []]
            contract_child = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.id.in_(child_ids),
                    RuleGenerationJobRecord.job_type == "OBSERVATION_CONTRACT",
                )
                .first()
                if child_ids
                else None
            )
            contract_record = (
                db.get(ObservationContractRecord, contract_child.asset_version_id)
                if contract_child and contract_child.asset_version_id
                else None
            )
            if contract_record is None:
                raise ValueError("original bundle ObservationContract is unavailable")
            context_payload = dict(context.context_json or {})
            detached = {
                "parent_source_ref": parent.source_ref,
                "command": payload.get("command"),
                "vendor": payload.get("vendor"),
                "model": payload.get("model"),
                "context_id": context.id,
                "context_digest": context.context_digest,
                "context": context_payload,
                "schema_id": contract_record.schema_id,
                "schema_ref": contract_record.schema_ref,
                "source_template_id": contract_record.source_template_id,
                "source_integrity": source_integrity,
                "observation_type": payload.get("observation_type"),
                "rule_asset_requirements": normalize_rule_asset_requirements(
                    context_payload.get("rule_asset_requirements")
                    or payload.get("rule_asset_requirements")
                ),
            }

        requirements = detached["rule_asset_requirements"]
        if requirements["requires_change_policy"] and not change_policies:
            raise ValueError("engineering revision requires one or more ChangePolicies")
        if requirements["requires_event_rule_assets"] and not event_rules:
            raise ValueError("engineering revision requires one or more EventRules")

        contract_result = _execute_revision_skill(
            "observation-contract-generator",
            {
                "mode": "validate",
                "command": detached["command"],
                "vendor": detached["vendor"],
                "model": detached["model"],
                "records": detached["context"].get("records") or [],
                "record_groups": detached["context"].get("record_groups") or [],
                "proposal": contract,
                "generation_provider": "manual",
                "agent_loop": False,
                "require_policy_targets": requirements["requires_change_policy"],
                "allow_semantic_overrides": True,
            },
            stage="contract",
        )
        policy_results = [
            _execute_revision_skill(
                "change-policy-generator",
                {
                    "mode": "validate",
                    "proposal": policy,
                    "observation_type": policy.get("observation_type"),
                    "generation_provider": "manual",
                    "agent_loop": False,
                },
                stage=f"change_policy[{index}]",
            )
            for index, policy in enumerate(change_policies)
        ]
        event_validation = (
            _execute_revision_skill(
                "event-rule-validator",
                {
                    "mode": "candidates",
                    "candidate_rules": event_rules,
                    "agent_loop": False,
                },
                stage="event_rules",
            )
            if event_rules
            else {
                "success": True,
                "rules": [],
                "errors": [],
                "skipped": True,
                "reason": "asset_requirement_profile_does_not_require_event_rules",
            }
        )
        replay = (
            _execute_revision_skill(
                "event-rule-replay",
                {
                    "candidate_rules": event_rules,
                    "candidate_contract": (
                        contract_result.get("proposal") or contract
                    ),
                    "limit": 1000,
                    "run_ids": detached["context"].get("replay_run_ids") or [],
                    "include_active_collisions": True,
                },
                stage="historical_replay",
            )
            if requirements["requires_historical_replay"]
            else {
                "success": True,
                "changes": [],
                "errors": [],
                "skipped": True,
                "reason": "asset_requirement_profile_does_not_require_historical_replay",
            }
        )
        active_equivalence = _active_asset_equivalence(
            change_policies=[
                dict(result.get("policy") or policy)
                for policy, result in zip(change_policies, policy_results)
            ],
            event_rules=event_rules,
        )
        if active_equivalence.get("success") is True:
            replay = {
                **replay,
                "active_asset_equivalence": active_equivalence,
            }
        context_gate = {
            "success": True,
            "mode": "immutable_dataset_context",
            "context_id": detached["context_id"],
            "context_digest": detached["context_digest"],
            "replay_run_ids": detached["context"].get("replay_run_ids") or [],
            "source_integrity": detached["source_integrity"],
        }
        replay_gate = evaluate_requirement_replay_gate(
            requirements=requirements,
            replay=replay,
            context_gate=context_gate,
        )
        validation = {
            "contract": contract_result,
            "change_policies": policy_results,
            "event_rules": event_validation,
            "historical_replay": replay,
            "replay_gate": replay_gate,
        }
        validation_success = (
            contract_result.get("success") is True
            and all(item.get("success") is True for item in policy_results)
            and event_validation.get("success") is True
            and replay_gate.get("success") is True
        )
        if not validation_success:
            raise RuleAssetEngineeringRevisionError(
                "engineering revision failed deterministic validation or historical replay",
                validation=validation,
            )

        contract_proposal = contract_result.get("proposal") or contract
        contract_observation_types = [
            str(semantic.get("observation_type") or "").strip()
            for semantic in dict(contract_proposal.get("state_semantics") or {}).values()
            if isinstance(semantic, dict)
            and str(semantic.get("observation_type") or "").strip()
        ]
        observation_type = str(
            (change_policies[0].get("observation_type") if change_policies else None)
            or detached.get("observation_type")
            or (contract_observation_types[0] if contract_observation_types else "")
        )
        persisted = execute_skill(
            "rule-asset-persister",
            {
                "source_ref": source_ref,
                "created_by": actor,
                "schema_id": detached["schema_id"],
                "schema_ref": detached["schema_ref"],
                "source_template_id": detached["source_template_id"],
                "command": detached["command"],
                "vendor": detached["vendor"],
                "model": detached["model"],
                "observation_type": observation_type,
                "rule_generation_context_id": detached["context_id"],
                "rule_generation_context_digest": detached["context_digest"],
                "contract_result": {**contract_result, "generation_provider": "manual"},
                "change_policy_result": {
                    "success": True,
                    "results": [
                        {**item, "generation_provider": "manual"} for item in policy_results
                    ],
                    "policies": change_policies,
                    "generation_provider": "manual",
                },
                "event_rule_result": {
                    "success": True,
                    "rules": event_rules,
                    "validation": event_validation,
                    "generation_provider": "manual",
                },
                "event_rule_validation": event_validation,
                "event_rule_replay": replay,
            },
        )
        new_bundle = dict(persisted.get("bundle") or {})
        if not new_bundle.get("id"):
            raise RuleAssetEngineeringRevisionError(
                "engineering revision did not create a replacement bundle",
                validation=validation,
            )
        new_payload = dict(new_bundle.get("payload_json") or {})
        new_payload["engineering_revision"] = {
            "parent_bundle_id": bundle_id,
            "actor": actor,
            "reason": str(reason).strip(),
            "created_at": _now_iso(),
            "idempotency_key": str(idempotency_key).strip(),
        }
        self.repository.update_payload(new_bundle["id"], payload=new_payload)
        new_report = dict(new_bundle.get("validation_report_json") or {})
        new_governance = dict(new_report.get("governance") or {})
        new_governance["engineering_revision"] = dict(new_payload["engineering_revision"])
        new_report["governance"] = new_governance
        self.repository.attach_validation_report(new_bundle["id"], validation_report=new_report)

        parent = self.repository.get_job(bundle_id) or {}
        parent_report = dict(parent.get("validation_report_json") or {})
        parent_governance = dict(parent_report.get("governance") or {})
        parent_governance["engineering_review"] = {
            "decision": "revision_created",
            "actor": actor,
            "reason": str(reason).strip(),
            "reviewed_at": _now_iso(),
            "replacement_bundle_id": new_bundle["id"],
        }
        parent_report["governance"] = parent_governance
        self.repository.attach_validation_report(bundle_id, validation_report=parent_report)
        return {
            "success": True,
            "created": True,
            "parent_bundle_id": bundle_id,
            "bundle": self.repository.get_job(new_bundle["id"]),
            "validation": validation,
        }
