"""Read model for atomic ObservationContract/ChangePolicy/EventRule bundles."""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, cast, or_

from src.apps.rule_governance.backend.rule_control.asset_requirements import (
    evaluate_requirement_replay_gate,
    normalize_rule_asset_requirements,
    rule_asset_composition_errors,
)
from src.apps.rule_governance.backend.rule_control.source_scope import (
    require_current_rule_generation_context,
)
from src.infrastructure.db.models import (
    ChangePolicyVersionRecord,
    EventRuleVersionRecord,
    OfflinePatrolPeriodRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


class RuleAssetBundleService:
    def list_bundles(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with get_db_session() as db:
            query = db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE"
            )
            if status:
                query = query.filter(RuleGenerationJobRecord.status == status)
            if search and str(search).strip():
                pattern = f"%{str(search).strip()}%"
                context_ids = db.query(RuleGenerationContextRecord.id).filter(
                    or_(
                        RuleGenerationContextRecord.offline_job_id.ilike(pattern),
                        cast(RuleGenerationContextRecord.dataset_ids_json, String).ilike(pattern),
                    )
                )
                query = query.filter(
                    or_(
                        RuleGenerationJobRecord.id.ilike(pattern),
                        RuleGenerationJobRecord.source_ref.ilike(pattern),
                        RuleGenerationJobRecord.asset_ref.ilike(pattern),
                        RuleGenerationJobRecord.payload_json["context_id"]
                        .as_string()
                        .in_(context_ids),
                    )
                )
            total = query.count()
            rows = (
                query.order_by(
                    RuleGenerationJobRecord.updated_at.desc(),
                    RuleGenerationJobRecord.id.desc(),
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            child_ids = {
                str(child_id)
                for row in rows
                for child_id in (row.payload_json or {}).get("child_job_ids") or []
            }
            children = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.id.in_(child_ids))
                .all()
                if child_ids
                else []
            )
            child_by_id = {str(item.id): item for item in children}
            asset_states = self._load_asset_states(db, children)
            context_by_id = self._load_contexts(db, rows)
            return {
                "items": [
                    self._serialize(row, child_by_id, asset_states, context_by_id) for row in rows
                ],
                "total": int(total),
                "limit": limit,
                "offset": offset,
            }

    def get_bundle(self, bundle_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, bundle_id)
            if row is None or row.job_type != "RULE_ASSET_BUNDLE":
                raise KeyError("rule asset bundle not found")
            child_ids = [str(item) for item in (row.payload_json or {}).get("child_job_ids") or []]
            children = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.id.in_(child_ids))
                .all()
                if child_ids
                else []
            )
            return self._serialize(
                row,
                {str(item.id): item for item in children},
                self._load_asset_states(db, children),
                self._load_contexts(db, [row]),
            )

    @staticmethod
    def _load_contexts(
        db: Any,
        rows: list[RuleGenerationJobRecord],
    ) -> dict[str, dict[str, Any]]:
        context_ids = {
            str((row.payload_json or {}).get("context_id") or "")
            for row in rows
            if (row.payload_json or {}).get("context_id")
        }
        contexts = (
            db.query(RuleGenerationContextRecord)
            .filter(RuleGenerationContextRecord.id.in_(context_ids))
            .all()
            if context_ids
            else []
        )
        dataset_ids = {
            str(dataset_id)
            for item in contexts
            for dataset_id in (item.dataset_ids_json or [])
            if dataset_id
        }
        periods = (
            db.query(OfflinePatrolPeriodRecord)
            .filter(OfflinePatrolPeriodRecord.dataset_id.in_(dataset_ids))
            .all()
            if dataset_ids
            else []
        )
        jobs_by_dataset: dict[str, set[str]] = {}
        for period in periods:
            jobs_by_dataset.setdefault(str(period.dataset_id), set()).add(str(period.job_id))
        result: dict[str, dict[str, Any]] = {}
        for item in contexts:
            offline_job_ids = {
                job_id
                for dataset_id in (item.dataset_ids_json or [])
                for job_id in jobs_by_dataset.get(str(dataset_id), set())
            }
            if item.offline_job_id:
                offline_job_ids.add(str(item.offline_job_id))
            try:
                source_integrity = require_current_rule_generation_context(
                    db,
                    item,
                    expected_context_digest=str(item.context_digest or ""),
                )
            except (KeyError, ValueError) as exc:
                source_integrity = {
                    "success": False,
                    "error": str(exc),
                }
            result[str(item.id)] = {
                "record": item,
                "offline_job_ids": sorted(offline_job_ids),
                "source_integrity": source_integrity,
            }
        return result

    @staticmethod
    def _load_asset_states(
        db: Any,
        children: list[RuleGenerationJobRecord],
    ) -> dict[str, dict[str, Any]]:
        """Resolve the immutable asset behind every child governance job."""

        states: dict[str, dict[str, Any]] = {}
        model_by_type = {
            "CHANGE_POLICY": ChangePolicyVersionRecord,
            "EVENT_RULE": EventRuleVersionRecord,
        }
        digest_field_by_type = {
            "CHANGE_POLICY": "digest",
            "EVENT_RULE": "digest",
        }
        for asset_type, model in model_by_type.items():
            matching = [
                child
                for child in children
                if child.asset_type == asset_type and child.asset_version_id
            ]
            version_ids = {str(child.asset_version_id) for child in matching}
            records = db.query(model).filter(model.id.in_(version_ids)).all() if version_ids else []
            by_id = {str(record.id): record for record in records}
            digest_field = digest_field_by_type[asset_type]
            for child in matching:
                record = by_id.get(str(child.asset_version_id))
                actual_digest = (
                    str(getattr(record, digest_field) or "") if record is not None else ""
                )
                expected_digest = str(child.asset_digest or "")
                states[str(child.id)] = {
                    "exists": record is not None,
                    "status": getattr(record, "status", None),
                    "digest": actual_digest or None,
                    "digest_matches": bool(
                        record is not None and expected_digest and actual_digest == expected_digest
                    ),
                    "evaluation_scope": (
                        getattr(record, "evaluation_scope", None)
                        if asset_type == "EVENT_RULE"
                        else None
                    ),
                    "event_class": (
                        getattr(record, "event_class", None)
                        if asset_type == "EVENT_RULE"
                        else None
                    ),
                    "event_type": (
                        getattr(record, "event_type", None)
                        if asset_type == "EVENT_RULE"
                        else None
                    ),
                    "observation_types": (
                        list(
                            dict(getattr(record, "condition_json", None) or {}).get(
                                "observation_types"
                            )
                            or []
                        )
                        if asset_type == "EVENT_RULE"
                        else sorted(
                            {
                                str(metadata.get("observation_type") or "").strip()
                                for metadata in dict(
                                    getattr(record, "state_semantics_json", None) or {}
                                ).values()
                                if isinstance(metadata, dict)
                                and str(metadata.get("observation_type") or "").strip()
                            }
                        )
                        if asset_type == "OBSERVATION_CONTRACT"
                        else []
                    ),
                }
        return states

    @staticmethod
    def _serialize(
        row: RuleGenerationJobRecord,
        child_by_id: dict[str, RuleGenerationJobRecord],
        asset_states: dict[str, dict[str, Any]] | None = None,
        context_by_id: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(row.payload_json or {})
        report = dict(row.validation_report_json or {})
        child_ids = [str(item) for item in payload.get("child_job_ids") or []]
        children = [child_by_id[item] for item in child_ids if item in child_by_id]
        asset_states = asset_states or {}
        child_items = []
        for child in children:
            asset_state = dict(asset_states.get(str(child.id)) or {})
            child_payload = dict(getattr(child, "payload_json", None) or {})
            policy = dict(child_payload.get("policy") or {})
            child_items.append(
                {
                    "id": child.id,
                    "job_type": child.job_type,
                    "status": child.status,
                    "asset_type": child.asset_type,
                    "asset_id": child.asset_id,
                    "asset_version_id": child.asset_version_id,
                    "asset_digest": child.asset_digest,
                    "validation_success": (child.validation_report_json or {}).get("success")
                    is True,
                    "asset_exists": asset_state.get("exists") is True,
                    "asset_status": asset_state.get("status"),
                    "asset_digest_matches": asset_state.get("digest_matches") is True,
                    "observation_type": policy.get("observation_type"),
                    "evaluation_scope": asset_state.get(
                        "evaluation_scope"
                    ),
                    "event_class": asset_state.get("event_class"),
                    "event_type": asset_state.get("event_type"),
                    "observation_types": list(
                        asset_state.get("observation_types") or []
                    ),
                }
            )
        counts: dict[str, int] = {}
        active_counts: dict[str, int] = {}
        for child in child_items:
            asset_type = str(child.get("asset_type") or child.get("job_type") or "unknown")
            counts[asset_type] = counts.get(asset_type, 0) + 1
            if child.get("asset_status") == "active":
                active_counts[asset_type] = active_counts.get(asset_type, 0) + 1
        context_gate = dict(report.get("context_gate") or {})
        context_privacy = dict(context_gate.get("privacy") or {})
        governance = dict(report.get("governance") or {})
        engineering_review = dict(governance.get("engineering_review") or {})
        engineering_review_ready = engineering_review.get("decision") == "accepted"
        automated_approval = dict(governance.get("automated_approval") or {})
        automatically_released = row.status == "active" and automated_approval.get("mode") == "administrator_delegated_automation"
        context_id = str(context_gate.get("context_id") or payload.get("context_id") or "")
        context_meta = dict((context_by_id or {}).get(context_id) or {})
        context_row = context_meta.get("record")
        source_integrity = dict(context_meta.get("source_integrity") or {})
        offline_job_ids = list(context_meta.get("offline_job_ids") or [])
        bundle_gate = dict(report.get("bundle_gate") or {})
        replay = dict(report.get("historical_replay") or {})
        expected_child_count = len(set(child_ids))
        children_complete = expected_child_count > 0 and len(children) == expected_child_count
        requirements = normalize_rule_asset_requirements(
            bundle_gate.get("asset_requirements")
            or report.get("asset_requirements")
            or payload.get("asset_requirements")
        )
        composition_errors = rule_asset_composition_errors(
            counts=counts,
            requirements=requirements,
            change_policy_roles=[
                item
                for item in child_items
                if item.get("asset_type") == "CHANGE_POLICY"
            ],
            event_rule_roles=[
                item
                for item in child_items
                if item.get("asset_type") == "EVENT_RULE"
            ],
        )
        composition_complete = not composition_errors
        children_release_ready = children_complete and all(
            child.get("status") == "pending_approval"
            and child.get("validation_success") is True
            and child.get("asset_exists") is True
            and child.get("asset_digest_matches") is True
            and child.get("asset_status") in {"draft", "pending_approval", "active"}
            for child in child_items
        )
        replay_gate = evaluate_requirement_replay_gate(
            requirements=requirements,
            replay=replay,
            context_gate=context_gate,
        )
        replay_ready = replay_gate["success"] is True
        context_mode = str(context_gate.get("mode") or "")
        source_integrity_ready = context_mode in {"legacy_manual", "check_item_semantics"} or (
            source_integrity.get("success") is True
            and context_row is not None
            and str(context_gate.get("context_digest") or "")
            == str(context_row.context_digest or "")
        )
        release_ready = (
            row.status == "pending_approval"
            and report.get("success") is True
            and bundle_gate.get("success") is True
            and context_gate.get("success") is True
            and replay_ready
            and source_integrity_ready
            and composition_complete
            and children_release_ready
            and (engineering_review_ready or automatically_released)
        )
        blockers = list(bundle_gate.get("blockers") or [])

        def add_blocker(message: str) -> None:
            if message not in blockers:
                blockers.append(message)

        if not children_complete:
            add_blocker("bundle child jobs are incomplete")
        if not composition_complete:
            for error in composition_errors:
                add_blocker(error)
        if children_complete and not children_release_ready and row.status == "pending_approval":
            add_blocker("one or more child assets are missing, mutated, invalid or not releasable")
        if context_gate.get("success") is not True:
            add_blocker("rule generation context gate failed")
        if not source_integrity_ready:
            add_blocker("immutable source evidence is stale or unavailable")
        if not replay_ready:
            for error in replay_gate["errors"]:
                add_blocker(error)
        if row.status == "pending_approval" and not engineering_review_ready:
            add_blocker("engineering review has not accepted the generated candidates")
        steps = [
            {
                "key": "context",
                "label": (
                    "CheckItem 标准语义 B"
                    if context_mode == "check_item_semantics"
                    else "脱敏数据上下文"
                ),
                "status": "passed" if context_gate.get("success") is True else "blocked",
                "detail": {
                    "context_id": context_gate.get("context_id"),
                    "context_digest": context_gate.get("context_digest"),
                    "raw_cli_included": context_privacy.get("raw_cli_included"),
                    "source_snapshot_count": context_privacy.get("source_snapshot_count"),
                    "sampled_record_count": context_privacy.get("sampled_record_count"),
                    "scope_type": context_privacy.get("scope_type"),
                },
            },
            (
                RuleAssetBundleService._asset_step(
                    "contract", "Observation Contract", "OBSERVATION_CONTRACT", child_items
                )
                if requirements["requires_observation_contract"]
                else {
                    "key": "contract",
                    "label": "Observation Contract",
                    "status": "skipped",
                    "detail": {"reason": "check_item_canonical_semantics"},
                }
            ),
            (
                RuleAssetBundleService._asset_step(
                    "policy",
                    "Change Policy",
                    "CHANGE_POLICY",
                    child_items,
                )
                if requirements["requires_change_policy"]
                else {
                    "key": "policy",
                    "label": "Change Policy",
                    "status": "skipped",
                    "detail": {
                        "reason": "asset_requirement_profile_not_required"
                    },
                }
            ),
            (
                RuleAssetBundleService._asset_step(
                    "rules",
                    "Current State / Event Rules",
                    "EVENT_RULE",
                    child_items,
                )
                if requirements["requires_event_rule_assets"]
                else {
                    "key": "rules",
                    "label": "Current State / Event Rules",
                    "status": "skipped",
                    "detail": {
                        "reason": "asset_requirement_profile_not_required"
                    },
                }
            ),
            {
                "key": "replay",
                "label": "历史回放",
                "status": "passed" if replay_ready else "blocked",
                "detail": {
                    "sampled_change_count": replay.get("sampled_change_count"),
                    "matched_change_count": replay.get("matched_change_count"),
                    "collision_count": replay.get("collision_count"),
                    "event_count": replay.get("event_count"),
                    "event_types": replay.get("event_types"),
                    "replacement_rule_ids": replay.get("replacement_rule_ids"),
                    "estimated_daily_event_count": replay.get("estimated_daily_event_count"),
                    "scope": replay.get("scope"),
                    "scope_gate": replay_gate,
                    "required": requirements[
                        "requires_historical_replay"
                    ],
                },
            },
            {
                "key": "review",
                "label": "授权自动验证" if automatically_released else "工程师复核",
                "status": "passed" if engineering_review_ready or automatically_released else "ready",
                "detail": automated_approval if automatically_released else engineering_review,
            },
            {
                "key": "release",
                "label": "审批发布",
                "status": (
                    "passed" if row.status == "active" else "ready" if release_ready else "blocked"
                ),
                "detail": {"bundle_status": row.status},
            },
        ]
        observation_types = sorted(
            {
                str(item).strip()
                for item in [
                    *(payload.get("observation_types") or []),
                    *(child.get("observation_type") for child in child_items),
                    *(
                        observation_type
                        for child in child_items
                        for observation_type in child.get("observation_types") or []
                    ),
                ]
                if str(item or "").strip()
            }
        )
        primary_observation_type = (
            observation_types[0]
            if len(observation_types) == 1
            else str(payload.get("observation_type") or "").strip() or None
        )
        semantic_label = (
            primary_observation_type
            if len(observation_types) == 1
            else (
                f"{payload.get('command') or 'command rule assets'} "
                f"({len(observation_types)} observation types)"
            )
        )
        return {
            "id": row.id,
            "status": row.status,
            "source_ref": row.source_ref,
            "observation_type": primary_observation_type,
            "observation_types": observation_types,
            "semantic_label": semantic_label,
            "asset_scope": payload.get("asset_scope")
            or ("single_observation" if len(observation_types) == 1 else "multi_observation"),
            "command": payload.get("command"),
            "vendor": payload.get("vendor"),
            "model": payload.get("model"),
            "context_id": context_id or None,
            "context_digest": context_gate.get("context_digest") or payload.get("context_digest"),
            "context_gate": {
                "success": context_gate.get("success") is True,
                "mode": context_gate.get("mode"),
                "context_id": context_gate.get("context_id") or context_id or None,
                "context_digest": context_gate.get("context_digest")
                or payload.get("context_digest"),
                "errors": [str(item) for item in context_gate.get("errors") or []],
                "replay_run_id_count": len(context_gate.get("replay_run_ids") or []),
                "privacy": {
                    "success": context_privacy.get("success") is True,
                    "raw_cli_included": context_privacy.get("raw_cli_included") is True,
                    "scope_type": context_privacy.get("scope_type"),
                    "source_snapshot_count": context_privacy.get("source_snapshot_count"),
                    "candidate_record_count": context_privacy.get("candidate_record_count"),
                    "sampled_record_count": context_privacy.get("sampled_record_count"),
                },
                "source_integrity": source_integrity,
            },
            "offline_job_id": (
                context_row.offline_job_id
                if context_row and context_row.offline_job_id
                else offline_job_ids[0]
                if len(offline_job_ids) == 1
                else None
            ),
            "offline_job_ids": offline_job_ids,
            "dataset_ids": list(context_row.dataset_ids_json or []) if context_row else [],
            "asset_counts": counts,
            "active_asset_counts": active_counts,
            "asset_requirements": requirements,
            "release_ready": release_ready,
            "engineering_review": engineering_review,
            "blockers": blockers,
            "replay": {
                key: replay.get(key)
                for key in (
                    "success",
                    "sampled_change_count",
                    "matched_change_count",
                    "collision_count",
                    "event_count",
                    "event_types",
                    "matched_entity_types",
                    "top_devices",
                    "replacement_rule_ids",
                    "replacement_logical_keys",
                    "estimated_daily_event_count",
                    "scope",
                )
            },
            "replay_gate": replay_gate,
            "steps": steps,
            "children": child_items,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "completed_at": row.completed_at,
        }

    @staticmethod
    def _asset_step(
        key: str,
        label: str,
        asset_type: str,
        children: list[dict[str, Any]],
    ) -> dict[str, Any]:
        matching = [item for item in children if item.get("asset_type") == asset_type]
        passed = bool(matching) and all(item.get("validation_success") is True for item in matching)
        return {
            "key": key,
            "label": label,
            "status": "passed" if passed else "blocked",
            "detail": {
                "asset_count": len(matching),
                "asset_version_ids": [item.get("asset_version_id") for item in matching],
            },
        }
