"""Auditable migration from embedded assessment policies to governed assets."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from src.apps.rule_governance.backend.check_items.assessment_policy_assets import (
    AssessmentPolicyAssetService,
)
from src.core.check_items.assessment_policy import (
    build_target_assessment_policy_snapshot,
    canonical_digest,
    normalize_assessment_policy_contract,
    resolve_assessment_basis,
)
from src.infrastructure.db.models import (
    AssessmentPolicyCutoverRecord,
    AssessmentPolicyMigrationSourceRecord,
    AssessmentPolicyShadowResultRecord,
    CheckExecutionRecord,
    CheckItemVersionRecord,
    EventRuleVersionRecord,
    NetworkDevice,
)
from src.infrastructure.db.postgres import get_db_session

_THRESHOLD_RULES = {
    "threshold": ("high", "gte"),
    "threshold_field_gte": ("high", "gte"),
    "threshold_lte": ("low", "lte"),
    "threshold_field_lte": ("low", "lte"),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        values: Iterable[Any] = [value]
    elif isinstance(value, Iterable):
        values = value
    else:
        values = []
    return sorted({_text(item) for item in values if _text(item)})


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat()
    return value


def _semantic_comparison_value(value: Any) -> Any:
    """Normalize representation-only differences before shadow comparison."""

    if isinstance(value, Mapping):
        return {
            str(key): _semantic_comparison_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_comparison_value(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _source_matches_device(
    source: Mapping[str, Any],
    device: Mapping[str, Any],
) -> bool:
    scope = dict(source.get("scope") or {})
    values = {
        "device_ids": _text(device.get("device_id") or device.get("id")),
        "sites": _text(device.get("site")),
        "roles": _text(device.get("role")),
    }
    return all(
        not _strings(scope.get(key)) or value in _strings(scope.get(key))
        for key, value in values.items()
    )


class AssessmentPolicyMigrationService:
    """Inventory, draft and compare legacy policy sources without activating them."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def _session(self, session: Any | None):
        return nullcontext(session) if session is not None else self._session_scope()

    def inventory_legacy_sources(
        self,
        *,
        actor: str,
        check_item_ids: Iterable[str] = (),
        include_execution_snapshots: bool = True,
        after_execution_id: str | None = None,
        execution_page_size: int = 1000,
    ) -> dict[str, Any]:
        """Inventory one bounded page of legacy sources and all small static sources."""

        inventor = _text(actor)
        if not inventor:
            raise ValueError("migration inventory requires an authenticated actor")
        selected_items = set(_strings(check_item_ids))
        page_size = max(1, min(int(execution_page_size), 5000))
        now = _utcnow()
        touched: list[str] = []
        counts = {
            "inventoried": 0,
            "review_required": 0,
            "unchanged": 0,
        }
        next_execution_id: str | None = None
        execution_complete = True

        with self._session(None) as db:
            versions_query = db.query(CheckItemVersionRecord).filter(
                CheckItemVersionRecord.status == "active"
            )
            if selected_items:
                versions_query = versions_query.filter(
                    CheckItemVersionRecord.check_item_id.in_(selected_items)
                )
            versions = versions_query.order_by(CheckItemVersionRecord.id).all()
            version_by_item = {
                str(row.check_item_id): row for row in versions
            }
            observation_index = self._observation_index(versions)

            devices = db.query(NetworkDevice).order_by(NetworkDevice.id).all()
            for device in devices:
                attributes = dict(device.attributes_json or {})
                raw_sources = list(
                    attributes.get("assessment_policy_bindings")
                    or attributes.get("assessment_policies")
                    or []
                )
                for index, raw in enumerate(raw_sources):
                    payload = dict(raw) if isinstance(raw, Mapping) else {"value": raw}
                    item_id = _text(payload.get("check_item_id"))
                    if selected_items and item_id not in selected_items:
                        continue
                    check_version = version_by_item.get(item_id)
                    reasons = self._source_reasons(
                        payload=payload,
                        check_item_version=check_version,
                    )
                    source_id, created = self._upsert_source(
                        db,
                        source_type="inventory_attribute",
                        source_locator=(
                            f"inventory:{device.id}:attributes_json:"
                            f"assessment_policy_bindings:{index}"
                        ),
                        payload=payload,
                        check_item_id=item_id or None,
                        check_item_version_id=(
                            str(check_version.id) if check_version is not None else None
                        ),
                        device_id=str(device.id),
                        observation_type=_text(payload.get("observation_type")) or None,
                        policy_role=_text(payload.get("policy_role")).lower() or None,
                        binding_type="inventory_profile",
                        scope=dict(payload.get("scope") or {"device_ids": [str(device.id)]}),
                        reasons=reasons,
                        actor=inventor,
                        now=now,
                    )
                    touched.append(source_id)
                    self._increment_inventory_count(counts, reasons, created)

            for version in versions:
                for source in self._catalog_sources(version):
                    reasons = self._source_reasons(
                        payload=source,
                        check_item_version=version,
                    )
                    source_id, created = self._upsert_source(
                        db,
                        source_type=str(source.pop("source_type")),
                        source_locator=str(source.pop("source_locator")),
                        payload=source,
                        check_item_id=str(version.check_item_id),
                        check_item_version_id=str(version.id),
                        observation_type=_text(source.get("observation_type")) or None,
                        policy_role=_text(source.get("policy_role")).lower() or None,
                        binding_type="catalog_default",
                        scope={},
                        reasons=reasons,
                        actor=inventor,
                        now=now,
                    )
                    touched.append(source_id)
                    self._increment_inventory_count(counts, reasons, created)

            for version in (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.status == "active")
                .order_by(EventRuleVersionRecord.id)
                .all()
            ):
                source = self._rule_constant_source(
                    version,
                    observation_index=observation_index,
                )
                if source is None:
                    continue
                item_id = _text(source.get("check_item_id"))
                if selected_items and item_id not in selected_items:
                    continue
                check_version = version_by_item.get(item_id)
                reasons = self._source_reasons(
                    payload=source,
                    check_item_version=check_version,
                )
                source_id, created = self._upsert_source(
                    db,
                    source_type="rule_constant",
                    source_locator=f"event-rule-version:{version.id}:condition_json",
                    payload=source,
                    check_item_id=item_id or None,
                    check_item_version_id=(
                        str(check_version.id) if check_version is not None else None
                    ),
                    observation_type=_text(source.get("observation_type")) or None,
                    policy_role=_text(source.get("policy_role")) or None,
                    binding_type="catalog_default",
                    scope=dict(source.get("scope") or {}),
                    reasons=reasons,
                    actor=inventor,
                    now=now,
                )
                touched.append(source_id)
                self._increment_inventory_count(counts, reasons, created)

            if include_execution_snapshots:
                query = db.query(CheckExecutionRecord).filter(
                    CheckExecutionRecord.assessment_policy_snapshot_digest.isnot(None)
                )
                if after_execution_id:
                    query = query.filter(CheckExecutionRecord.id > str(after_execution_id))
                if selected_items:
                    version_ids = [str(row.id) for row in versions]
                    query = query.filter(
                        CheckExecutionRecord.check_item_version_id.in_(version_ids)
                    )
                rows = query.order_by(CheckExecutionRecord.id).limit(page_size + 1).all()
                execution_complete = len(rows) <= page_size
                rows = rows[:page_size]
                next_execution_id = str(rows[-1].id) if rows and not execution_complete else None
                for execution in rows:
                    snapshot = dict(execution.assessment_policy_snapshot_json or {})
                    for source in snapshot.get("sources") or []:
                        if not isinstance(source, Mapping):
                            continue
                        payload = dict(source)
                        logical_ref = (
                            _text(payload.get("binding_id"))
                            or _text(payload.get("policy_version_id"))
                            or canonical_digest(payload)
                        )
                        check_version = db.get(
                            CheckItemVersionRecord,
                            str(execution.check_item_version_id),
                        )
                        reasons = self._source_reasons(
                            payload=payload,
                            check_item_version=check_version,
                        )
                        source_id, created = self._upsert_source(
                            db,
                            source_type="execution_snapshot",
                            source_locator=f"execution-snapshot-source:{logical_ref}",
                            payload=payload,
                            check_item_id=(
                                str(check_version.check_item_id)
                                if check_version is not None
                                else None
                            ),
                            check_item_version_id=str(execution.check_item_version_id),
                            device_id=str(execution.device_id),
                            observation_type=_text(payload.get("observation_type")) or None,
                            policy_role=_text(payload.get("policy_role")) or None,
                            binding_type=_text(payload.get("source_kind")) or None,
                            scope=dict(payload.get("scope") or {}),
                            reasons=reasons,
                            actor=inventor,
                            now=now,
                        )
                        touched.append(source_id)
                        self._increment_inventory_count(counts, reasons, created)

        return {
            "schema_version": "assessment-policy-migration-inventory/v1",
            "touched_source_ids": sorted(set(touched)),
            "counts": counts,
            "execution_scan": {
                "enabled": bool(include_execution_snapshots),
                "page_size": page_size,
                "next_execution_id": next_execution_id,
                "complete": execution_complete,
            },
        }

    def list_sources(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        conversion_status: str | None = None,
        source_type: str | None = None,
        check_item_id: str | None = None,
    ) -> dict[str, Any]:
        current_page = max(1, int(page))
        size = max(1, min(int(page_size), 100))
        with self._session(None) as db:
            query = db.query(AssessmentPolicyMigrationSourceRecord)
            if conversion_status:
                query = query.filter(
                    AssessmentPolicyMigrationSourceRecord.conversion_status
                    == str(conversion_status)
                )
            if source_type:
                query = query.filter(
                    AssessmentPolicyMigrationSourceRecord.source_type == str(source_type)
                )
            if check_item_id:
                query = query.filter(
                    AssessmentPolicyMigrationSourceRecord.check_item_id
                    == str(check_item_id)
                )
            pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None
            if pattern:
                query = query.filter(
                    or_(
                        AssessmentPolicyMigrationSourceRecord.source_locator.ilike(pattern),
                        AssessmentPolicyMigrationSourceRecord.check_item_id.ilike(pattern),
                        AssessmentPolicyMigrationSourceRecord.observation_type.ilike(pattern),
                        AssessmentPolicyMigrationSourceRecord.device_id.ilike(pattern),
                    )
                )
            total = int(query.count())
            rows = (
                query.order_by(
                    AssessmentPolicyMigrationSourceRecord.updated_at.desc(),
                    AssessmentPolicyMigrationSourceRecord.id,
                )
                .offset((current_page - 1) * size)
                .limit(size)
                .all()
            )
            return {
                "items": [self._serialize_source(row) for row in rows],
                "page": current_page,
                "page_size": size,
                "total": total,
                "page_count": (total + size - 1) // size if total else 0,
            }

    def create_draft_from_source(
        self,
        source_id: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        source_ref = _text(source_id)
        creator = _text(actor)
        if not creator:
            raise ValueError("migration draft creation requires an authenticated actor")
        with self._session(None) as db:
            row = db.get(AssessmentPolicyMigrationSourceRecord, source_ref)
            if row is None:
                raise ValueError("assessment-policy migration source not found")
            if row.draft_policy_version_id:
                existing_id = str(row.draft_policy_version_id)
                source_payload = self._serialize_source(row)
                return {
                    "source": source_payload,
                    "draft": AssessmentPolicyAssetService(
                        self._session_scope
                    ).get_version(existing_id),
                }
            candidate, reasons = self._draft_candidate(row)
            if reasons:
                row.conversion_status = "review_required"
                row.reason_codes_json = reasons
                row.updated_at = _utcnow()
                return {"source": self._serialize_source(row), "draft": None}

        draft = AssessmentPolicyAssetService(self._session_scope).create_version(
            check_item_id=str(candidate["check_item_id"]),
            check_item_version_id=str(candidate["check_item_version_id"]),
            observation_type=str(candidate["observation_type"]),
            policy_role=str(candidate["policy_role"]),
            name=str(candidate["name"]),
            description="由历史策略来源生成的待复核草稿；不会自动批准或绑定。",
            policy=dict(candidate["policy"]),
            evidence_refs=[
                f"legacy-policy:{candidate['source_digest']}",
                str(candidate["source_locator"]),
            ],
            provenance={
                "source": "legacy_policy_migration",
                "source_type": candidate["source_type"],
                "source_locator": candidate["source_locator"],
                "source_digest": candidate["source_digest"],
                "automatic_approval": False,
            },
            created_by=creator,
            idempotency_key=f"policy-migrate-{candidate['source_digest'][:48]}",
        )
        with self._session(None) as db:
            row = db.get(AssessmentPolicyMigrationSourceRecord, source_ref)
            if row is None:
                raise ValueError("assessment-policy migration source disappeared")
            row.draft_policy_version_id = str(draft["id"])
            row.conversion_status = "draft_created"
            row.reason_codes_json = []
            row.converted_at = _utcnow()
            row.updated_at = row.converted_at
            source_payload = self._serialize_source(row)
        return {"source": source_payload, "draft": draft}

    def compare_shadow(
        self,
        *,
        check_item_version_id: str,
        device: Mapping[str, Any],
        observation: Mapping[str, Any],
        rule: Mapping[str, Any],
        actor: str,
        policy_effective_at: datetime | str,
        runtime_bundle_version_id: str = "assessment-policy-shadow",
        runtime_bundle_digest: str = "0" * 64,
    ) -> dict[str, Any]:
        creator = _text(actor)
        if not creator:
            raise ValueError("policy shadow comparison requires an authenticated actor")
        with self._session(None) as db:
            check_version = db.get(CheckItemVersionRecord, str(check_item_version_id))
            if check_version is None:
                raise ValueError("check-item version not found")
            check_item_ref = str(check_version.check_item_id)
            check_version_ref = str(check_version.id)
            target = self._hydrate_device(db, device)
            governed_sources = AssessmentPolicyAssetService(
                self._session_scope
            ).list_rendered_sources(
                check_item_ids=[check_item_ref],
                session=db,
            ).get(check_item_ref, ())
            common = {
                "check_item_id": check_item_ref,
                "check_item_version_id": check_version_ref,
                "canonical_contract": dict(check_version.canonical_contract_json or {}),
                "device": target,
                "applicability_requirement": dict(
                    check_version.applicability_json or {}
                ),
                "policy_effective_at": policy_effective_at,
            }
            legacy_snapshot = build_target_assessment_policy_snapshot(
                **common,
                governed_sources=(),
                allow_legacy_sources=True,
                allow_legacy_rule_fallback=True,
            )
            governed_snapshot = build_target_assessment_policy_snapshot(
                **common,
                governed_sources=governed_sources,
                allow_legacy_sources=False,
                allow_legacy_rule_fallback=False,
            )

        legacy = resolve_assessment_basis(
            rule=dict(rule),
            observation=dict(observation),
            policy_snapshot=legacy_snapshot,
            runtime_bundle_version_id=runtime_bundle_version_id,
            runtime_bundle_digest=runtime_bundle_digest,
        )
        governed = resolve_assessment_basis(
            rule=dict(rule),
            observation=dict(observation),
            policy_snapshot=governed_snapshot,
            runtime_bundle_version_id=runtime_bundle_version_id,
            runtime_bundle_digest=runtime_bundle_digest,
        )
        legacy_result = self._resolution_result(legacy)
        governed_result = self._resolution_result(governed)
        differences = self._differences(legacy_result, governed_result)
        comparison_status = self._comparison_status(
            legacy_result,
            governed_result,
            differences,
        )
        input_payload = {
            "schema_version": "assessment-policy-shadow-input/v1",
            "check_item_version_id": str(check_item_version_id),
            "device_id": _text(target.get("device_id") or target.get("id")),
            "observation": _json_value(observation),
            "rule": _json_value(rule),
            "legacy_snapshot_digest": legacy_snapshot["digest"],
            "governed_snapshot_digest": governed_snapshot["digest"],
            "runtime_bundle_version_id": runtime_bundle_version_id,
            "runtime_bundle_digest": runtime_bundle_digest,
        }
        input_digest = canonical_digest(input_payload)
        now = _utcnow()
        with self._session(None) as db:
            row = (
                db.query(AssessmentPolicyShadowResultRecord)
                .filter(AssessmentPolicyShadowResultRecord.input_digest == input_digest)
                .first()
            )
            if row is None:
                row = AssessmentPolicyShadowResultRecord(
                    id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"netops/assessment-policy-shadow/{input_digest}",
                        )
                    ),
                    input_digest=input_digest,
                    check_item_id=check_item_ref,
                    check_item_version_id=check_version_ref,
                    device_id=_text(target.get("device_id") or target.get("id")),
                    observation_type=_text(observation.get("observation_type")),
                    rule_version_id=(
                        _text(rule.get("version_id") or rule.get("version")) or None
                    ),
                    legacy_snapshot_digest=str(legacy_snapshot["digest"]),
                    governed_snapshot_digest=str(governed_snapshot["digest"]),
                    legacy_result_json=legacy_result,
                    governed_result_json=governed_result,
                    difference_json=differences,
                    comparison_status=comparison_status,
                    created_by=creator,
                    created_at=now,
                )
                db.add(row)
                db.flush()
            result = self._serialize_shadow(row)
        return {
            **result,
            "legacy_snapshot": legacy_snapshot,
            "governed_snapshot": governed_snapshot,
        }

    def list_shadow_results(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        comparison_status: str | None = None,
        check_item_id: str | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        current_page = max(1, int(page))
        size = max(1, min(int(page_size), 100))
        with self._session(None) as db:
            query = db.query(AssessmentPolicyShadowResultRecord)
            if comparison_status:
                query = query.filter(
                    AssessmentPolicyShadowResultRecord.comparison_status
                    == str(comparison_status)
                )
            if check_item_id:
                query = query.filter(
                    AssessmentPolicyShadowResultRecord.check_item_id
                    == str(check_item_id)
                )
            if device_id:
                query = query.filter(
                    AssessmentPolicyShadowResultRecord.device_id == str(device_id)
                )
            total = int(query.count())
            rows = (
                query.order_by(
                    AssessmentPolicyShadowResultRecord.created_at.desc(),
                    AssessmentPolicyShadowResultRecord.id,
                )
                .offset((current_page - 1) * size)
                .limit(size)
                .all()
            )
            return {
                "items": [self._serialize_shadow(row) for row in rows],
                "page": current_page,
                "page_size": size,
                "total": total,
                "page_count": (total + size - 1) // size if total else 0,
            }

    def activate_cutover(
        self,
        *,
        check_item_version_id: str,
        device_id: str,
        shadow_result_ids: Iterable[str],
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Switch one exact device/check-item target after matched shadow evidence."""

        creator = _text(actor)
        rationale = _text(reason)
        request_key = _text(idempotency_key)
        shadow_ids = _strings(shadow_result_ids)
        if not creator:
            raise ValueError("assessment policy cutover requires an authenticated actor")
        if len(rationale) < 8:
            raise ValueError("assessment policy cutover reason must contain at least 8 characters")
        if not request_key:
            raise ValueError("assessment policy cutover requires an idempotency key")
        if not shadow_ids:
            raise ValueError("assessment policy cutover requires matched shadow evidence")

        try:
            with self._session(None) as db:
                replay = db.query(AssessmentPolicyCutoverRecord).filter(
                    AssessmentPolicyCutoverRecord.idempotency_key == request_key
                ).first()
                if replay is not None:
                    if (
                        str(replay.check_item_version_id)
                        != _text(check_item_version_id)
                        or str(replay.device_id) != _text(device_id)
                        or str(replay.resolver_mode) != "governed_only"
                        or _strings(replay.shadow_result_ids_json or []) != shadow_ids
                    ):
                        raise ValueError(
                            "assessment policy cutover idempotency key payload conflict"
                        )
                    return self._serialize_cutover(replay, idempotent_replay=True)

                check_version = db.get(
                    CheckItemVersionRecord, _text(check_item_version_id)
                )
                if check_version is None:
                    raise ValueError("check-item version not found")
                device = db.get(NetworkDevice, _text(device_id))
                if device is None:
                    raise ValueError("assessment policy cutover device not found")
                shadows = db.query(AssessmentPolicyShadowResultRecord).filter(
                    AssessmentPolicyShadowResultRecord.id.in_(shadow_ids)
                ).all()
                if len(shadows) != len(shadow_ids):
                    raise ValueError("assessment policy cutover shadow evidence is incomplete")
                for shadow in shadows:
                    if (
                        str(shadow.check_item_version_id) != str(check_version.id)
                        or str(shadow.device_id) != str(device.id)
                    ):
                        raise ValueError(
                            "assessment policy cutover shadow evidence belongs to another target"
                        )
                    if str(shadow.comparison_status) != "matched":
                        raise ValueError(
                            "assessment policy cutover requires all shadow differences to be resolved"
                        )
                    governed = dict(shadow.governed_result_json or {})
                    if str(governed.get("status") or "") != "resolved":
                        raise ValueError(
                            "assessment policy cutover governed shadow result is unresolved"
                        )

                target = self._hydrate_device(db, {"device_id": str(device.id)})
                rendered_sources = AssessmentPolicyAssetService(
                    self._session_scope
                ).list_rendered_sources(
                    check_item_ids=[str(check_version.check_item_id)],
                    session=db,
                ).get(str(check_version.check_item_id), ())
                governed_snapshot = build_target_assessment_policy_snapshot(
                    check_item_id=str(check_version.check_item_id),
                    check_item_version_id=str(check_version.id),
                    canonical_contract=dict(check_version.canonical_contract_json or {}),
                    device=target,
                    governed_sources=rendered_sources,
                    applicability_requirement=dict(
                        check_version.applicability_json or {}
                    ),
                    policy_effective_at=_utcnow(),
                    allow_legacy_sources=False,
                    allow_legacy_rule_fallback=False,
                )
                if governed_snapshot.get("invalid_sources"):
                    raise ValueError(
                        "assessment policy cutover has invalid governed policy sources"
                    )
                applicability = dict(
                    governed_snapshot.get("applicability_decision") or {}
                )
                if str(applicability.get("status") or "") not in {
                    "applicable",
                    "not_applicable",
                }:
                    raise ValueError(
                        "assessment policy cutover requires an active applicability policy"
                    )
                selected_applicability = dict(
                    applicability.get("selected_policy") or {}
                )
                if not _text(selected_applicability.get("binding_id")):
                    raise ValueError(
                        "assessment policy cutover applicability decision is not bound"
                    )

                target_sources = [
                    dict(source)
                    for source in rendered_sources
                    if isinstance(source, Mapping)
                    and _source_matches_device(source, target)
                ]
                binding_ids = _strings(
                    source.get("binding_id") for source in target_sources
                )
                if not binding_ids:
                    raise ValueError(
                        "assessment policy cutover requires active governed bindings"
                    )
                selected_binding_ids = {
                    _text(binding.get("binding_id"))
                    for shadow in shadows
                    for binding in (
                        dict(dict(shadow.governed_result_json or {}).get("applicability") or {}),
                        dict(dict(shadow.governed_result_json or {}).get("threshold") or {}),
                        dict(dict(shadow.governed_result_json or {}).get("expected_state") or {}),
                    )
                    if _text(binding.get("binding_id"))
                }
                if not selected_binding_ids.issubset(set(binding_ids)):
                    raise ValueError(
                        "assessment policy cutover shadow evidence references stale bindings"
                    )

                evidence_payload = {
                    "schema_version": "assessment-policy-cutover-evidence/v1",
                    "check_item_version_id": str(check_version.id),
                    "device_id": str(device.id),
                    "binding_ids": binding_ids,
                    "binding_digests": sorted(
                        _text(source.get("binding_digest"))
                        for source in target_sources
                        if _text(source.get("binding_digest"))
                    ),
                    "shadow_results": [
                        {
                            "id": str(shadow.id),
                            "input_digest": str(shadow.input_digest),
                            "legacy_snapshot_digest": str(
                                shadow.legacy_snapshot_digest
                            ),
                            "governed_snapshot_digest": str(
                                shadow.governed_snapshot_digest
                            ),
                        }
                        for shadow in sorted(shadows, key=lambda item: str(item.id))
                    ],
                    "governed_snapshot_digest": governed_snapshot["digest"],
                }
                return self._write_cutover(
                    db,
                    check_item_id=str(check_version.check_item_id),
                    check_item_version_id=str(check_version.id),
                    device_id=str(device.id),
                    resolver_mode="governed_only",
                    binding_ids=binding_ids,
                    shadow_result_ids=shadow_ids,
                    evidence_digest=canonical_digest(evidence_payload),
                    actor=creator,
                    reason=rationale,
                    idempotency_key=request_key,
                )
        except IntegrityError as exc:
            raise ValueError(
                "assessment policy cutover changed concurrently; refresh and retry"
            ) from exc

    def rollback_cutover(
        self,
        cutover_id: str,
        *,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Append a legacy-compatible resolver decision without deleting cutover history."""

        creator = _text(actor)
        rationale = _text(reason)
        request_key = _text(idempotency_key)
        if not creator:
            raise ValueError("assessment policy rollback requires an authenticated actor")
        if len(rationale) < 8:
            raise ValueError("assessment policy rollback reason must contain at least 8 characters")
        if not request_key:
            raise ValueError("assessment policy rollback requires an idempotency key")
        try:
            with self._session(None) as db:
                replay = db.query(AssessmentPolicyCutoverRecord).filter(
                    AssessmentPolicyCutoverRecord.idempotency_key == request_key
                ).first()
                if replay is not None:
                    if (
                        str(replay.resolver_mode) != "legacy_compat"
                        or str(replay.supersedes_id or "") != _text(cutover_id)
                    ):
                        raise ValueError(
                            "assessment policy rollback idempotency key payload conflict"
                        )
                    return self._serialize_cutover(replay, idempotent_replay=True)
                active = db.query(AssessmentPolicyCutoverRecord).filter(
                    AssessmentPolicyCutoverRecord.id == _text(cutover_id),
                    AssessmentPolicyCutoverRecord.status == "active",
                    AssessmentPolicyCutoverRecord.resolver_mode == "governed_only",
                ).with_for_update().first()
                if active is None:
                    raise ValueError("active governed assessment policy cutover not found")
                rollback_evidence = canonical_digest(
                    {
                        "schema_version": "assessment-policy-cutover-rollback/v1",
                        "cutover_id": str(active.id),
                        "cutover_evidence_digest": str(active.evidence_digest),
                    }
                )
                return self._write_cutover(
                    db,
                    check_item_id=str(active.check_item_id),
                    check_item_version_id=str(active.check_item_version_id),
                    device_id=str(active.device_id),
                    resolver_mode="legacy_compat",
                    binding_ids=_strings(active.binding_ids_json or []),
                    shadow_result_ids=_strings(active.shadow_result_ids_json or []),
                    evidence_digest=rollback_evidence,
                    actor=creator,
                    reason=rationale,
                    idempotency_key=request_key,
                    expected_active_id=str(active.id),
                )
        except IntegrityError as exc:
            raise ValueError(
                "assessment policy rollback changed concurrently; refresh and retry"
            ) from exc

    def list_cutovers(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
        resolver_mode: str | None = None,
        check_item_id: str | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        current_page = max(1, int(page))
        size = max(1, min(int(page_size), 100))
        with self._session(None) as db:
            query = db.query(AssessmentPolicyCutoverRecord)
            if status:
                query = query.filter(AssessmentPolicyCutoverRecord.status == _text(status))
            if resolver_mode:
                query = query.filter(
                    AssessmentPolicyCutoverRecord.resolver_mode == _text(resolver_mode)
                )
            if check_item_id:
                query = query.filter(
                    AssessmentPolicyCutoverRecord.check_item_id == _text(check_item_id)
                )
            if device_id:
                query = query.filter(
                    AssessmentPolicyCutoverRecord.device_id == _text(device_id)
                )
            total = int(query.count())
            rows = query.order_by(
                AssessmentPolicyCutoverRecord.created_at.desc(),
                AssessmentPolicyCutoverRecord.id,
            ).offset((current_page - 1) * size).limit(size).all()
            return {
                "items": [self._serialize_cutover(row) for row in rows],
                "page": current_page,
                "page_size": size,
                "total": total,
                "page_count": (total + size - 1) // size if total else 0,
            }

    def effective_cutover_modes(
        self,
        targets: Iterable[tuple[str, str]],
        *,
        session: Any | None = None,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        requested = {
            (_text(check_item_version_id), _text(device_id))
            for check_item_version_id, device_id in targets
            if _text(check_item_version_id) and _text(device_id)
        }
        if not requested:
            return {}
        version_ids = sorted({item[0] for item in requested})
        device_ids = sorted({item[1] for item in requested})
        with self._session(session) as db:
            rows = db.query(AssessmentPolicyCutoverRecord).filter(
                AssessmentPolicyCutoverRecord.status == "active",
                AssessmentPolicyCutoverRecord.check_item_version_id.in_(version_ids),
                AssessmentPolicyCutoverRecord.device_id.in_(device_ids),
            ).all()
            return {
                (str(row.check_item_version_id), str(row.device_id)): self._serialize_cutover(row)
                for row in rows
                if (str(row.check_item_version_id), str(row.device_id)) in requested
            }

    def _write_cutover(
        self,
        db: Any,
        *,
        check_item_id: str,
        check_item_version_id: str,
        device_id: str,
        resolver_mode: str,
        binding_ids: list[str],
        shadow_result_ids: list[str],
        evidence_digest: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        expected_active_id: str | None = None,
    ) -> dict[str, Any]:
        request_payload = {
            "check_item_version_id": check_item_version_id,
            "device_id": device_id,
            "resolver_mode": resolver_mode,
            "binding_ids": binding_ids,
            "shadow_result_ids": shadow_result_ids,
            "evidence_digest": evidence_digest,
            "reason": reason,
        }
        request_digest = canonical_digest(request_payload)
        active = db.query(AssessmentPolicyCutoverRecord).filter(
            AssessmentPolicyCutoverRecord.check_item_version_id
            == check_item_version_id,
            AssessmentPolicyCutoverRecord.device_id == device_id,
            AssessmentPolicyCutoverRecord.status == "active",
        ).with_for_update().first()
        if expected_active_id and (
            active is None or str(active.id) != str(expected_active_id)
        ):
            raise ValueError("assessment policy cutover changed before rollback")
        if (
            active is not None
            and str(active.resolver_mode) == resolver_mode
            and str(active.evidence_digest) == evidence_digest
        ):
            return self._serialize_cutover(active, idempotent_replay=True)

        now = _utcnow()
        if active is not None:
            active.status = "superseded"
            active.superseded_at = now
            db.flush()
        row = AssessmentPolicyCutoverRecord(
            id=str(uuid.uuid4()),
            check_item_id=check_item_id,
            check_item_version_id=check_item_version_id,
            device_id=device_id,
            resolver_mode=resolver_mode,
            status="active",
            binding_ids_json=list(binding_ids),
            shadow_result_ids_json=list(shadow_result_ids),
            evidence_digest=evidence_digest,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            created_by=actor,
            reason=reason,
            supersedes_id=str(active.id) if active is not None else None,
            created_at=now,
        )
        db.add(row)
        db.flush()
        if active is not None:
            active.superseded_by_id = str(row.id)
            db.flush()
        return self._serialize_cutover(row)

    @staticmethod
    def _observation_index(
        versions: Iterable[CheckItemVersionRecord],
    ) -> dict[str, list[tuple[CheckItemVersionRecord, dict[str, Any]]]]:
        index: dict[str, list[tuple[CheckItemVersionRecord, dict[str, Any]]]] = {}
        for version in versions:
            for observation in dict(version.canonical_contract_json or {}).get(
                "observations", []
            ):
                if not isinstance(observation, Mapping):
                    continue
                observation_type = _text(observation.get("observation_type"))
                if observation_type:
                    index.setdefault(observation_type, []).append(
                        (version, dict(observation))
                    )
        return index

    @staticmethod
    def _catalog_sources(version: CheckItemVersionRecord) -> list[dict[str, Any]]:
        contract = normalize_assessment_policy_contract(
            dict(version.canonical_contract_json or {}),
            dict(version.applicability_json or {}),
        )
        sources: list[dict[str, Any]] = []
        for observation_type, requirement in dict(contract.get("thresholds") or {}).items():
            for role, threshold in dict(dict(requirement).get("defaults") or {}).items():
                if not isinstance(threshold, Mapping):
                    continue
                sources.append(
                    {
                        "source_type": "catalog_default",
                        "source_locator": (
                            f"check-item-version:{version.id}:canonical_contract:"
                            f"thresholds:{observation_type}:{role}"
                        ),
                        "check_item_id": str(version.check_item_id),
                        "observation_type": str(observation_type),
                        "policy_role": str(role),
                        "migration_policy": {
                            **dict(threshold),
                            "unit": threshold.get("unit") or requirement.get("unit"),
                        },
                    }
                )
        for observation_type, requirement in dict(
            contract.get("expected_states") or {}
        ).items():
            expected = _strings(dict(requirement).get("default_states"))
            if expected:
                sources.append(
                    {
                        "source_type": "catalog_default",
                        "source_locator": (
                            f"check-item-version:{version.id}:canonical_contract:"
                            f"expected_states:{observation_type}"
                        ),
                        "check_item_id": str(version.check_item_id),
                        "observation_type": str(observation_type),
                        "policy_role": "expected_state",
                        "migration_policy": {"expected_states": expected},
                    }
                )
        applicability = dict(version.applicability_json or {})
        if applicability:
            capability = _text(applicability.get("capability"))
            migration_policy = dict(applicability)
            if capability and not migration_policy.get("mode"):
                migration_policy = {
                    "mode": "capability",
                    "required_capabilities": [capability],
                    "reason_code": _text(applicability.get("reason_code"))
                    or "capability_explicitly_absent",
                }
            sources.append(
                {
                    "source_type": "check_item_applicability",
                    "source_locator": f"check-item-version:{version.id}:applicability_json",
                    "check_item_id": str(version.check_item_id),
                    "observation_type": "*",
                    "policy_role": "applicability",
                    "migration_policy": migration_policy,
                }
            )
        empty_policy = dict(version.empty_result_policy_json or {})
        if empty_policy:
            sources.append(
                {
                    "source_type": "check_item_empty_policy",
                    "source_locator": f"check-item-version:{version.id}:empty_result_policy_json",
                    "check_item_id": str(version.check_item_id),
                    "observation_type": "*",
                    "policy_role": "applicability",
                    "legacy_empty_result_policy": empty_policy,
                    "migration_review_required": True,
                }
            )
        return sources

    @staticmethod
    def _rule_constant_source(
        version: EventRuleVersionRecord,
        *,
        observation_index: Mapping[
            str, list[tuple[CheckItemVersionRecord, dict[str, Any]]]
        ],
    ) -> dict[str, Any] | None:
        condition = dict(version.condition_json or {})
        kind = _text(condition.get("kind"))
        role_and_key = _THRESHOLD_RULES.get(kind)
        if role_and_key is None:
            return None
        role, key = role_and_key
        value = _number(condition.get(key))
        if value is None:
            return None
        observations = _strings(condition.get("observation_types"))
        matches = observation_index.get(observations[0], []) if len(observations) == 1 else []
        unique_versions = {str(item[0].id): item for item in matches}
        check_version, observation = (
            next(iter(unique_versions.values()))
            if len(unique_versions) == 1
            else (None, {})
        )
        unit = _text(observation.get("unit"))
        return {
            "check_item_id": (
                str(check_version.check_item_id) if check_version is not None else None
            ),
            "observation_type": observations[0] if len(observations) == 1 else None,
            "policy_role": role,
            "migration_policy": {
                "value": value,
                "unit": unit or None,
                "operator": key,
                "hysteresis": condition.get("hysteresis"),
            },
            "scope": {
                "entity_types": _strings(condition.get("entity_types")),
            },
            "rule_version_id": str(version.id),
            "migration_review_required": check_version is None or not unit,
        }

    @staticmethod
    def _source_reasons(
        *,
        payload: Mapping[str, Any],
        check_item_version: CheckItemVersionRecord | None,
    ) -> list[str]:
        reasons: list[str] = []
        if payload.get("migration_review_required"):
            reasons.append("policy_migration_review_required")
        if check_item_version is None:
            reasons.append("check_item_version_missing")
        if not _text(payload.get("observation_type")):
            reasons.append("observation_type_missing")
        role = _text(payload.get("policy_role")).lower()
        if role not in {
            "high",
            "critical_high",
            "low",
            "critical_low",
            "expected_state",
            "applicability",
        }:
            reasons.append("policy_role_missing_or_invalid")
        try:
            AssessmentPolicyMigrationService._policy_payload(payload, role)
        except ValueError as exc:
            reasons.append(str(exc))
        return sorted(set(reasons))

    @staticmethod
    def _policy_payload(payload: Mapping[str, Any], role: str) -> dict[str, Any]:
        if isinstance(payload.get("migration_policy"), Mapping):
            policy = dict(payload.get("migration_policy") or {})
        elif role == "applicability":
            policy = dict(payload.get("applicability") or {})
        elif role == "expected_state":
            policy = {"expected_states": _strings(payload.get("expected_states"))}
        else:
            policy = dict(dict(payload.get("thresholds") or {}).get(role) or {})
        if not policy:
            raise ValueError("policy_payload_not_convertible")
        return policy

    @staticmethod
    def _increment_inventory_count(
        counts: dict[str, int],
        reasons: list[str],
        created: bool,
    ) -> None:
        if not created:
            counts["unchanged"] += 1
        elif reasons:
            counts["review_required"] += 1
        else:
            counts["inventoried"] += 1

    def _upsert_source(
        self,
        db: Any,
        *,
        source_type: str,
        source_locator: str,
        payload: Mapping[str, Any],
        check_item_id: str | None,
        check_item_version_id: str | None,
        observation_type: str | None,
        policy_role: str | None,
        binding_type: str | None,
        scope: Mapping[str, Any],
        reasons: list[str],
        actor: str,
        now: datetime,
        device_id: str | None = None,
    ) -> tuple[str, bool]:
        frozen_payload = _json_value(payload)
        frozen_scope = _json_value(scope)
        source_digest = canonical_digest(
            {
                "source_type": source_type,
                "source_locator": source_locator,
                "payload": frozen_payload,
                "scope": frozen_scope,
            }
        )
        row = (
            db.query(AssessmentPolicyMigrationSourceRecord)
            .filter(
                AssessmentPolicyMigrationSourceRecord.source_digest
                == source_digest
            )
            .first()
        )
        if row is not None:
            row.last_seen_at = now
            row.updated_at = now
            return str(row.id), False
        status = "review_required" if reasons else "inventoried"
        row = AssessmentPolicyMigrationSourceRecord(
            id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/assessment-policy-migration-source/{source_digest}",
                )
            ),
            source_type=source_type,
            source_locator=source_locator,
            source_digest=source_digest,
            check_item_id=check_item_id,
            check_item_version_id=check_item_version_id,
            device_id=device_id,
            observation_type=observation_type,
            policy_role=policy_role,
            binding_type=binding_type,
            scope_json=frozen_scope,
            source_payload_json=frozen_payload,
            conversion_status=status,
            reason_codes_json=reasons,
            inventoried_by=actor,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        return str(row.id), True

    @staticmethod
    def _draft_candidate(
        row: AssessmentPolicyMigrationSourceRecord,
    ) -> tuple[dict[str, Any], list[str]]:
        payload = dict(row.source_payload_json or {})
        reasons = list(row.reason_codes_json or [])
        role = _text(row.policy_role).lower()
        try:
            policy = AssessmentPolicyMigrationService._policy_payload(payload, role)
        except ValueError as exc:
            reasons.append(str(exc))
            policy = {}
        required = {
            "check_item_id": _text(row.check_item_id),
            "check_item_version_id": _text(row.check_item_version_id),
            "observation_type": _text(row.observation_type),
            "policy_role": role,
        }
        reasons.extend(
            f"{key}_missing" for key, value in required.items() if not value
        )
        if reasons:
            return {}, sorted(set(reasons))
        name = (
            f"历史迁移草稿 / {required['check_item_id']} / "
            f"{required['policy_role']} / {row.source_type}"
        )[:256]
        return {
            **required,
            "name": name,
            "policy": policy,
            "source_type": str(row.source_type),
            "source_locator": str(row.source_locator),
            "source_digest": str(row.source_digest),
        }, []

    @staticmethod
    def _hydrate_device(db: Any, device: Mapping[str, Any]) -> dict[str, Any]:
        target = dict(device)
        device_id = _text(target.get("device_id") or target.get("id"))
        row = db.get(NetworkDevice, device_id) if device_id else None
        if row is None:
            if not device_id:
                raise ValueError("policy shadow comparison requires device_id")
            target["device_id"] = device_id
            return target
        inventory = row.to_dict()
        target.setdefault("device_id", str(row.id))
        for key in (
            "device_name",
            "management_ip",
            "vendor",
            "model",
            "family",
            "platform",
            "device_type",
            "os_version",
            "site",
            "role",
        ):
            if target.get(key) in (None, ""):
                target[key] = inventory.get(key)
        attributes = dict(row.attributes_json or {})
        attributes.update(
            dict(target.get("attributes") or target.get("attributes_json") or {})
        )
        target["attributes"] = attributes
        target.pop("attributes_json", None)
        return target

    @staticmethod
    def _resolution_result(resolution: Any) -> dict[str, Any]:
        basis = dict(resolution.basis or {})
        threshold = dict(basis.get("threshold_binding") or {})
        expected = dict(basis.get("expected_state_binding") or {})
        applicability = dict(basis.get("applicability_decision") or {})
        selected_applicability = dict(applicability.get("selected_policy") or {})
        return _json_value(
            {
                "status": resolution.status,
                "blocker_code": resolution.blocker_code,
                "should_evaluate": bool(resolution.should_evaluate),
                "effective_condition": dict(resolution.effective_condition or {}),
                "applicability": {
                    "status": applicability.get("status"),
                    "reason_codes": applicability.get("reason_codes") or [],
                    "source_kind": applicability.get("source_kind"),
                    "policy_version_id": selected_applicability.get(
                        "policy_version_id"
                    ),
                    "binding_id": selected_applicability.get("binding_id"),
                    "digest": applicability.get("digest"),
                },
                "threshold": {
                    key: threshold.get(key)
                    for key in (
                        "status",
                        "source_kind",
                        "policy_id",
                        "policy_version_id",
                        "binding_id",
                        "value",
                        "unit",
                        "hysteresis",
                        "window",
                        "threshold_role",
                        "digest",
                    )
                    if threshold.get(key) is not None
                },
                "expected_state": {
                    key: expected.get(key)
                    for key in (
                        "status",
                        "source_kind",
                        "policy_id",
                        "policy_version_id",
                        "binding_id",
                        "expected_states",
                        "digest",
                    )
                    if expected.get(key) is not None
                },
                "basis_digest": basis.get("digest"),
            }
        )

    @staticmethod
    def _differences(legacy: Mapping[str, Any], governed: Mapping[str, Any]) -> dict[str, Any]:
        semantic_values = {
            "status": (legacy.get("status"), governed.get("status")),
            "blocker_code": (
                legacy.get("blocker_code"),
                governed.get("blocker_code"),
            ),
            "should_evaluate": (
                legacy.get("should_evaluate"),
                governed.get("should_evaluate"),
            ),
            "effective_condition": (
                legacy.get("effective_condition"),
                governed.get("effective_condition"),
            ),
            "applicability": (
                {
                    "status": dict(legacy.get("applicability") or {}).get("status")
                },
                {
                    "status": dict(governed.get("applicability") or {}).get("status")
                },
            ),
            "threshold": (
                {
                    key: dict(legacy.get("threshold") or {}).get(key)
                    for key in (
                        "status",
                        "value",
                        "unit",
                        "hysteresis",
                        "window",
                        "threshold_role",
                    )
                    if dict(legacy.get("threshold") or {}).get(key) is not None
                },
                {
                    key: dict(governed.get("threshold") or {}).get(key)
                    for key in (
                        "status",
                        "value",
                        "unit",
                        "hysteresis",
                        "window",
                        "threshold_role",
                    )
                    if dict(governed.get("threshold") or {}).get(key) is not None
                },
            ),
            "expected_state": (
                {
                    key: dict(legacy.get("expected_state") or {}).get(key)
                    for key in ("status", "expected_states")
                    if dict(legacy.get("expected_state") or {}).get(key) is not None
                },
                {
                    key: dict(governed.get("expected_state") or {}).get(key)
                    for key in ("status", "expected_states")
                    if dict(governed.get("expected_state") or {}).get(key) is not None
                },
            ),
        }
        differences = {
            field: {
                "legacy": _json_value(values[0]),
                "governed": _json_value(values[1]),
            }
            for field, values in semantic_values.items()
            if canonical_digest(_semantic_comparison_value(values[0]))
            != canonical_digest(_semantic_comparison_value(values[1]))
        }
        provenance = {
            field: {
                "legacy": _json_value(legacy.get(field)),
                "governed": _json_value(governed.get(field)),
            }
            for field in ("applicability", "threshold", "expected_state")
            if canonical_digest(legacy.get(field))
            != canonical_digest(governed.get(field))
        }
        if provenance:
            differences["provenance_transition"] = provenance
        return differences

    @staticmethod
    def _comparison_status(
        legacy: Mapping[str, Any],
        governed: Mapping[str, Any],
        differences: Mapping[str, Any],
    ) -> str:
        semantic_differences = {
            key: value
            for key, value in differences.items()
            if key != "provenance_transition"
        }
        if not semantic_differences:
            return "matched"
        legacy_missing = legacy.get("status") in {"missing", "unknown"} or bool(
            legacy.get("blocker_code")
        )
        governed_missing = governed.get("status") in {"missing", "unknown"} or bool(
            governed.get("blocker_code")
        )
        if governed_missing and not legacy_missing:
            return "governed_missing"
        if legacy_missing and not governed_missing:
            return "legacy_missing"
        return "different"

    @staticmethod
    def _serialize_source(row: AssessmentPolicyMigrationSourceRecord) -> dict[str, Any]:
        payload = row.to_dict()
        return _json_value(payload)

    @staticmethod
    def _serialize_shadow(row: AssessmentPolicyShadowResultRecord) -> dict[str, Any]:
        payload = row.to_dict()
        return _json_value(payload)

    @staticmethod
    def _serialize_cutover(
        row: AssessmentPolicyCutoverRecord,
        *,
        idempotent_replay: bool = False,
    ) -> dict[str, Any]:
        payload = _json_value(row.to_dict())
        if idempotent_replay:
            payload["idempotent_replay"] = True
        return payload


__all__ = ["AssessmentPolicyMigrationService"]
