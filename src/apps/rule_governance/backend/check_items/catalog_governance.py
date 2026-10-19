"""Governed UI revisions for check-item collection implementations."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_

from src.apps.rule_governance.backend.check_items.catalog import (
    CheckItemCatalog,
    CheckItemCatalogRepository,
    CheckItemDefinition,
    CollectionCommand,
    CollectionImplementation,
    build_check_item_definition,
    build_collection_implementation,
    load_catalog_defaults,
    primary_collection_commands,
    validate_check_item_definition,
)
from src.apps.rule_governance.backend.check_items.import_release import (
    incoming_adapters,
    withdraw_draft_templates,
    withdraw_incoming,
)
from src.apps.rule_governance.backend.check_items.release import (
    CheckItemReleaseBlocked,
    prepare_rules,
    release_in_session,
)
from src.apps.rule_governance.backend.check_items.retirement import (
    close_orphaned_events,
    orphaned_events,
    retire_check_item,
)
from src.apps.rule_governance.backend.check_items.rollout import (
    RolloutCampaignService,
)
from src.apps.rule_governance.backend.check_items.runtime_bundle import (
    CheckItemRuntimeBundleService,
    RuntimeBundleValidationError,
)
from src.apps.rule_governance.backend.check_items.system_items import (
    SystemCheckItemError,
    ensure_compatible,
    ensure_deletable,
)
from src.common.governance import actor_matches_any
from src.core.check_items.canonical import freeze_rule_semantics
from src.core.check_items.local_semantics import derive_assessment_mode
from src.core.check_items.command_adapter import (
    is_direct_projection_adapter,
    validate_adapter_spec,
)
from src.core.check_items.resolver import CheckItemImplementationResolver
from src.infrastructure.db.models import (
    AuditLogRecord,
    CheckItemCatalogChangeRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    RolloutCampaignRecord,
)
from src.infrastructure.db.postgres import get_db_session
from src.platform.inventory.repository import DeviceRepository


@contextmanager
def _joined(db: Any):
    """A session scope that stays inside the caller's transaction."""

    yield db
    db.flush()


def _ensure_deletable(**asset: str) -> None:
    try:
        ensure_deletable(**asset)
    except SystemCheckItemError as exc:
        raise CatalogGovernanceConflictError(str(exc)) from exc


class CatalogGovernanceConflictError(ValueError):
    """The requested catalog lifecycle transition is stale or unsafe."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _require_text(value: Any, *, label: str, minimum: int = 1) -> str:
    normalized = str(value or "").strip()
    if len(normalized) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} characters")
    return normalized


def _implementation_from_row(
    root: CollectionImplementationRecord,
    version: CollectionImplementationVersionRecord,
    *,
    check_item_ids: list[str],
    candidate_payload: Mapping[str, Any] | None = None,
) -> CollectionImplementation:
    frozen = dict(candidate_payload or {})
    return CollectionImplementation(
        id=str(root.id),
        version=int(version.version),
        name=str(frozen.get("name") or root.name),
        description=str(
            frozen.get("description")
            if frozen.get("description") is not None
            else root.description or ""
        ),
        check_item_ids=tuple(sorted(set(check_item_ids))),
        match=dict(version.match_json or {}),
        commands=primary_collection_commands(
            (
                CollectionCommand(
                    command=str(item.get("command") or ""),
                    priority=int(item.get("priority") or 0),
                )
                for item in (version.commands_json or [])
            ),
            implementation_id=str(root.id),
            version_id=str(version.id),
        ),
        collector=str(version.collector),
        parser_requirement=str(version.parser_requirement),
        template_scope=(
            str(version.template_scope) if version.template_scope is not None else None
        ),
        adapter_spec=dict(version.adapter_spec_json or {}),
        output_quality_policy=dict(version.output_quality_policy_json or {}),
        selection_priority=int(version.selection_priority),
        status=str(version.status),
        digest=str(version.digest),
        version_id=str(version.id),
    )


def _check_item_from_row(
    root: CheckItemRecord,
    version: CheckItemVersionRecord,
    *,
    candidate_payload: Mapping[str, Any] | None = None,
) -> CheckItemDefinition:
    frozen = dict(candidate_payload or {})
    return CheckItemDefinition(
        id=str(root.id),
        version=int(version.version),
        name=str(frozen.get("name") or root.name),
        category=str(frozen.get("category") or root.category),
        description=str(
            frozen.get("description")
            if frozen.get("description") is not None
            else root.description or ""
        ),
        result_mode=str(version.result_mode),
        assessment_mode=str(version.assessment_mode),
        canonical_contract=dict(version.canonical_contract_json or {}),
        report_projection=str(version.report_projection),
        applicability=dict(version.applicability_json or {}),
        freshness_policy=dict(version.freshness_policy_json or {}),
        empty_result_policy=dict(version.empty_result_policy_json or {}),
        coverage_policy=dict(version.coverage_policy_json or {}),
        status=str(version.status),
        digest=str(version.digest),
        version_id=str(version.id),
    )


class CheckItemCatalogGovernanceService:
    """Create, preview and independently publish catalog asset revisions."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
        *,
        inventory_loader: Callable[[], list[dict[str, Any]]] | None = None,
        release_derived_assets: bool | None = None,
    ) -> None:
        self._session_scope = session_scope
        # Rules and runtime bundles live in the default database. A catalog opened
        # on another store has none to switch and keeps the guard against
        # replacing a version that is in use.
        self._release = (
            session_scope is get_db_session if release_derived_assets is None else bool(release_derived_assets)
        )
        self._catalog = CheckItemCatalogRepository(session_scope=session_scope)
        self._inventory_loader = inventory_loader or (
            lambda: DeviceRepository().get_devices_by_filter(status="active")
        )

    def create_check_item_draft(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str,
        reason: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create an immutable check-item version and preserve its active links."""

        actor = _require_text(actor, label="actor")
        reason = str(reason or "").strip()
        idempotency_key = _require_text(
            idempotency_key,
            label="idempotency key",
            minimum=8,
        )
        raw = dict(payload or {})
        check_item_id = str(raw.get("id") or "").strip()
        request_digest = _digest(
            {
                "asset_kind": "check_item",
                "payload": raw,
                "reason": reason,
            }
        )
        now = _utcnow()
        defaults = load_catalog_defaults()["check_item"]
        with self._session_scope() as db:
            replay = (
                db.query(CheckItemCatalogChangeRecord)
                .filter(
                    CheckItemCatalogChangeRecord.creation_idempotency_key
                    == idempotency_key
                )
                .first()
            )
            if replay is not None:
                if str(replay.request_digest) != request_digest:
                    raise CatalogGovernanceConflictError(
                        "idempotency key was already used with another catalog revision"
                    )
                return self._serialize_change(db, replay)

            root = db.get(CheckItemRecord, check_item_id)
            latest_version = (
                db.query(func.max(CheckItemVersionRecord.version))
                .filter(CheckItemVersionRecord.check_item_id == check_item_id)
                .scalar()
            )
            version_number = int(latest_version or 0) + 1
            raw.update(
                {
                    "id": check_item_id,
                    "version": version_number,
                    "status": "draft",
                }
            )
            raw["canonical_contract"] = freeze_rule_semantics(dict(raw.get("canonical_contract") or {}))
            rows = raw["canonical_contract"].get("observations") or []
            if raw.get("result_mode") == "structured" and rows and all("semantic_definition" in row for row in rows):
                # Each Value states its own use; the check item follows from them.
                raw["assessment_mode"] = derive_assessment_mode(raw["canonical_contract"])
            candidate = build_check_item_definition(raw, defaults=defaults)
            validate_check_item_definition(candidate)
            # A system base check item keeps the Values topology discovery reads.
            ensure_compatible(candidate.id, candidate.canonical_contract)
            _require_text(candidate.name, label="check item name", minimum=2)
            _require_text(candidate.category, label="check item category")

            baseline = (
                db.query(CheckItemVersionRecord)
                .filter(
                    CheckItemVersionRecord.check_item_id == candidate.id,
                    CheckItemVersionRecord.status == "active",
                )
                .with_for_update()
                .first()
            )
            if root is None:
                root = CheckItemRecord(
                    id=candidate.id,
                    name=candidate.name,
                    category=candidate.category,
                    description=candidate.description,
                    status="draft",
                    current_version=0,
                    created_at=now,
                    updated_at=now,
                )
                db.add(root)
            else:
                root.updated_at = now
            version = CheckItemVersionRecord(
                id=candidate.version_id,
                check_item_id=candidate.id,
                version=candidate.version,
                result_mode=candidate.result_mode,
                assessment_mode=candidate.assessment_mode,
                canonical_contract_json=candidate.canonical_contract,
                report_projection=candidate.report_projection,
                applicability_json=candidate.applicability or None,
                freshness_policy_json=candidate.freshness_policy or None,
                empty_result_policy_json=candidate.empty_result_policy or None,
                coverage_policy_json=candidate.coverage_policy or None,
                status="draft",
                digest=candidate.digest,
                created_by=actor,
                created_at=now,
            )
            db.add(version)
            db.flush()

            if baseline is not None:
                baseline_links = (
                    db.query(CheckItemImplementationLinkRecord)
                    .filter(
                        CheckItemImplementationLinkRecord.check_item_version_id
                        == str(baseline.id)
                    )
                    .all()
                )
                for link in baseline_links:
                    link_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            "netops/check-item-link/"
                            f"{candidate.version_id}/{link.implementation_version_id}",
                        )
                    )
                    db.add(
                        CheckItemImplementationLinkRecord(
                            id=link_id,
                            check_item_version_id=candidate.version_id,
                            implementation_version_id=str(
                                link.implementation_version_id
                            ),
                            priority=int(link.priority),
                            required=bool(link.required),
                            created_at=now,
                        )
                    )

            change = CheckItemCatalogChangeRecord(
                id=str(uuid.uuid4()),
                asset_kind="check_item",
                asset_id=candidate.id,
                version_id=candidate.version_id,
                version=candidate.version,
                baseline_version_id=(
                    str(baseline.id) if baseline is not None else None
                ),
                status="draft",
                payload_digest=candidate.digest,
                candidate_payload_json={
                    "name": candidate.name,
                    "category": candidate.category,
                    "description": candidate.description,
                    "result_mode": candidate.result_mode,
                    "assessment_mode": candidate.assessment_mode,
                    "canonical_contract": candidate.canonical_contract,
                    "report_projection": candidate.report_projection,
                    "applicability": candidate.applicability,
                    "freshness_policy": candidate.freshness_policy,
                    "empty_result_policy": candidate.empty_result_policy,
                    "coverage_policy": candidate.coverage_policy,
                },
                created_by=actor,
                creation_reason=reason,
                creation_idempotency_key=idempotency_key,
                request_digest=request_digest,
                created_at=now,
                updated_at=now,
            )
            db.add(change)
            self._audit(
                db,
                action="check_item_catalog_draft_created",
                actor=actor,
                resource_id=str(change.id),
                idempotency_key=f"catalog-change:create:{idempotency_key}",
                detail={
                    "asset_kind": change.asset_kind,
                    "asset_id": change.asset_id,
                    "version_id": change.version_id,
                    "version": change.version,
                    "reason": reason,
                },
            )
            db.flush()
            return self._serialize_change(db, change)

    def create_implementation_draft(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str,
        reason: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        actor = _require_text(actor, label="actor")
        reason = str(reason or "").strip()
        idempotency_key = _require_text(
            idempotency_key,
            label="idempotency key",
            minimum=8,
        )
        raw = dict(payload or {})
        implementation_id = str(raw.get("id") or "").strip()
        command_rows = list(raw.get("commands") or [])
        if len(command_rows) != 1:
            raise ValueError(
                "collection implementation requires exactly one command; "
                "fallback commands are no longer supported"
            )
        request_digest = _digest(
            {
                "asset_kind": "collection_implementation",
                "payload": raw,
                "reason": reason,
            }
        )
        now = _utcnow()
        defaults = load_catalog_defaults()["implementation"]
        with self._session_scope() as db:
            replay = (
                db.query(CheckItemCatalogChangeRecord)
                .filter(
                    CheckItemCatalogChangeRecord.creation_idempotency_key
                    == idempotency_key
                )
                .first()
            )
            if replay is not None:
                if str(replay.request_digest) != request_digest:
                    raise CatalogGovernanceConflictError(
                        "idempotency key was already used with another catalog revision"
                    )
                return self._serialize_change(db, replay)

            active_items = (
                db.query(CheckItemVersionRecord)
                .filter(CheckItemVersionRecord.status == "active")
                .all()
            )
            known_check_item_ids = {str(item.check_item_id) for item in active_items}
            requested_check_item_ids = {
                str(item).strip()
                for item in (raw.get("check_item_ids") or [])
                if str(item).strip()
            }
            unknown = sorted(requested_check_item_ids - known_check_item_ids)
            if unknown:
                raise ValueError(
                    "collection implementation references inactive or unknown "
                    f"check items: {unknown}"
                )

            root = db.get(CollectionImplementationRecord, implementation_id)
            latest_version = (
                db.query(func.max(CollectionImplementationVersionRecord.version))
                .filter(
                    CollectionImplementationVersionRecord.implementation_id
                    == implementation_id
                )
                .scalar()
            )
            version_number = int(latest_version or 0) + 1
            raw.update(
                {
                    "id": implementation_id,
                    "version": version_number,
                    "status": "draft",
                }
            )
            if is_direct_projection_adapter(raw.get("adapter_spec")):
                active_targets = {
                    str(item.check_item_id): {
                        "version_id": str(item.id),
                        "canonical_contract": dict(
                            item.canonical_contract_json or {}
                        ),
                    }
                    for item in active_items
                    if str(item.check_item_id) in requested_check_item_ids
                }
                raw["adapter_spec"] = validate_adapter_spec(
                    raw.get("adapter_spec"),
                    check_item_ids=tuple(requested_check_item_ids),
                    check_items=active_targets,
                    require_pinned_check_item_versions=True,
                )
            if len(raw.get("commands") or []) != 1:
                raise ValueError(
                    "a command adapter must declare exactly one command; "
                    "fallback commands are not supported"
                )
            candidate = build_collection_implementation(
                raw,
                known_check_item_ids=known_check_item_ids,
                defaults=defaults,
            )
            result_modes_by_item = {
                str(item.check_item_id): str(item.result_mode)
                for item in active_items
                if str(item.check_item_id) in candidate.check_item_ids
            }
            required_result_modes = set(result_modes_by_item.values())
            if len(required_result_modes) != 1:
                raise ValueError(
                    "one collection implementation cannot serve check items with "
                    "different result modes"
                )
            required_result_mode = next(iter(required_result_modes))
            if candidate.parser_requirement != required_result_mode:
                raise ValueError(
                    "collection implementation parser_requirement must match the "
                    f"linked check item result_mode: {required_result_mode}"
                )
            baseline = (
                db.query(CollectionImplementationVersionRecord)
                .filter(
                    CollectionImplementationVersionRecord.implementation_id
                    == candidate.id,
                    CollectionImplementationVersionRecord.status == "active",
                )
                .with_for_update()
                .first()
            )
            if root is None:
                root = CollectionImplementationRecord(
                    id=candidate.id,
                    name=candidate.name,
                    description=candidate.description,
                    status="draft",
                    current_version=0,
                    created_at=now,
                    updated_at=now,
                )
                db.add(root)
            else:
                root.updated_at = now
            version = CollectionImplementationVersionRecord(
                id=candidate.version_id,
                implementation_id=candidate.id,
                version=candidate.version,
                collector=candidate.collector,
                match_json=candidate.match,
                commands_json=[
                    {"command": item.command, "priority": item.priority}
                    for item in candidate.commands
                ],
                parser_requirement=candidate.parser_requirement,
                template_scope=candidate.template_scope,
                adapter_spec_json=candidate.adapter_spec or None,
                output_quality_policy_json=candidate.output_quality_policy or None,
                selection_priority=candidate.selection_priority,
                status="draft",
                digest=candidate.digest,
                created_by=actor,
                created_at=now,
            )
            db.add(version)
            db.flush()
            item_versions = {
                str(item.check_item_id): str(item.id) for item in active_items
            }
            for check_item_id in candidate.check_item_ids:
                link_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "netops/check-item-link/"
                        f"{item_versions[check_item_id]}/{candidate.version_id}",
                    )
                )
                db.add(
                    CheckItemImplementationLinkRecord(
                        id=link_id,
                        check_item_version_id=item_versions[check_item_id],
                        implementation_version_id=candidate.version_id,
                        priority=candidate.selection_priority,
                        required=True,
                        created_at=now,
                    )
                )
            change = CheckItemCatalogChangeRecord(
                id=str(uuid.uuid4()),
                asset_kind="collection_implementation",
                asset_id=candidate.id,
                version_id=candidate.version_id,
                version=candidate.version,
                baseline_version_id=(str(baseline.id) if baseline is not None else None),
                status="draft",
                payload_digest=candidate.digest,
                candidate_payload_json={
                    "name": candidate.name,
                    "description": candidate.description,
                    "check_item_ids": list(candidate.check_item_ids),
                    "match": candidate.match,
                    "commands": [
                        {"command": item.command, "priority": item.priority}
                        for item in candidate.commands
                    ],
                    "collector": candidate.collector,
                    "parser_requirement": candidate.parser_requirement,
                    "template_scope": candidate.template_scope,
                    "adapter_spec": candidate.adapter_spec,
                    "selection_priority": candidate.selection_priority,
                    "output_quality_policy": candidate.output_quality_policy,
                },
                created_by=actor,
                creation_reason=reason,
                creation_idempotency_key=idempotency_key,
                request_digest=request_digest,
                created_at=now,
                updated_at=now,
            )
            db.add(change)
            self._audit(
                db,
                action="check_item_catalog_draft_created",
                actor=actor,
                resource_id=str(change.id),
                idempotency_key=f"catalog-change:create:{idempotency_key}",
                detail={
                    "asset_kind": change.asset_kind,
                    "asset_id": change.asset_id,
                    "version_id": change.version_id,
                    "version": change.version,
                    "reason": reason,
                },
            )
            db.flush()
            return self._serialize_change(db, change)

    def delete_check_item(
        self,
        check_item_id: str,
        *,
        actor: str,
        reason: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Withdraw a check item without destroying versions or audit evidence."""

        check_item_id = _require_text(check_item_id, label="check item id")
        _ensure_deletable(check_item_id=check_item_id)
        actor = _require_text(actor, label="actor")
        reason = str(reason or "").strip()
        idempotency_key = _require_text(
            idempotency_key,
            label="idempotency key",
            minimum=8,
        )
        audit_key = f"catalog-check-item:delete:{idempotency_key}"
        now = _utcnow()
        with self._session_scope() as db:
            replay = (
                db.query(AuditLogRecord)
                .filter(AuditLogRecord.idempotency_key == audit_key)
                .first()
            )
            if replay is not None:
                if str(replay.resource_id or "") != check_item_id:
                    raise CatalogGovernanceConflictError(
                        "idempotency key was already used for another check item"
                    )
                detail = dict(replay.detail or {})
                return {
                    "success": True,
                    "check_item_id": check_item_id,
                    "status": "deprecated",
                    "cancelled_change_count": int(
                        detail.get("cancelled_change_count") or 0
                    ),
                    "idempotent_replay": True,
                }

            root = (
                db.query(CheckItemRecord)
                .filter(CheckItemRecord.id == check_item_id)
                .with_for_update()
                .first()
            )
            if root is None:
                raise ValueError("check item not found")
            # Rules, runtime bundles and the adapters that only serve it go with it.
            retired = retire_check_item(db, root, actor=actor, reason=reason, now=now)
            self._audit(
                db,
                action="check_item_deleted",
                actor=actor,
                resource_id=check_item_id,
                idempotency_key=audit_key,
                detail={**retired, "reason": reason},
            )
            db.flush()
            return {
                "success": True,
                "status": "deprecated",
                "idempotent_replay": False,
                **retired,
            }

    def preview_delete(self, check_item_id: str, *, actor: str) -> dict[str, Any]:
        """Say what deleting a check item would take with it, by doing it and rolling back."""

        check_item_id = _require_text(check_item_id, label="check item id")
        try:
            _ensure_deletable(check_item_id=check_item_id)
        except CatalogGovernanceConflictError as exc:
            return {"check_item_id": check_item_id, "deletable": False, "blockers": [str(exc)]}
        with self._session_scope() as db:
            try:
                root = db.get(CheckItemRecord, check_item_id)
                if root is None or str(root.status) != "active":
                    raise ValueError("check item not found")
                retired = retire_check_item(db, root, actor=actor, reason="删除前试算", now=_utcnow())
            finally:
                db.rollback()
        return {"deletable": True, "blockers": [], **retired}

    @staticmethod
    def _previous_release(
        db: Any, check_item_id: str
    ) -> tuple[CheckItemVersionRecord | None, CheckItemVersionRecord | None]:
        """The version in force and the one that was in force before it.

        A draft that was withdrawn or rejected was never in force and is skipped.
        """

        active = (
            db.query(CheckItemVersionRecord)
            .filter(
                CheckItemVersionRecord.check_item_id == check_item_id,
                CheckItemVersionRecord.status == "active",
            )
            .first()
        )
        if active is None:
            return None, None
        previous = (
            db.query(CheckItemVersionRecord)
            .filter(
                CheckItemVersionRecord.check_item_id == check_item_id,
                CheckItemVersionRecord.version < active.version,
                CheckItemVersionRecord.activated_at.isnot(None),
            )
            .order_by(CheckItemVersionRecord.version.desc())
            .first()
        )
        return active, previous

    def preview_rollback(self, check_item_id: str, *, actor: str) -> dict[str, Any]:
        """Draft the previous release again as a new version and say what publishing it would switch.

        Versions are never reactivated: a rollback is a new version with the
        earlier content, released like any other, so rules, adapters and runtime
        bundles go back together or not at all. The draft is kept, so confirming
        publishes exactly what was shown; discard_draft drops it.
        """

        check_item_id = _require_text(check_item_id, label="check item id")
        actor = _require_text(actor, label="actor")
        with self._session_scope() as db:
            root = db.get(CheckItemRecord, check_item_id)
            active, previous = self._previous_release(db, check_item_id)
            if root is None or active is None:
                raise ValueError("check item not found")
            if previous is None:
                raise CatalogGovernanceConflictError("这个检查项没有更早发布过的版本，无法回退")
            released = (
                db.query(CheckItemCatalogChangeRecord)
                .filter(CheckItemCatalogChangeRecord.version_id == str(previous.id))
                .first()
            )
            # The name and description are kept with the change that released the version. A version
            # that was seeded or imported has no such change, and keeps the wording in use today.
            then = dict(released.candidate_payload_json or {}) if released is not None else {}
            payload = {
                "id": check_item_id,
                "name": str(then.get("name") or root.name),
                "category": str(then.get("category") or root.category),
                "description": str(
                    then["description"] if then.get("description") is not None else root.description or ""
                ),
                "result_mode": str(previous.result_mode),
                "assessment_mode": str(previous.assessment_mode),
                "canonical_contract": dict(previous.canonical_contract_json or {}),
                "report_projection": str(previous.report_projection),
                "applicability": dict(previous.applicability_json or {}),
                "freshness_policy": dict(previous.freshness_policy_json or {}),
                "empty_result_policy": dict(previous.empty_result_policy_json or {}),
                "coverage_policy": dict(previous.coverage_policy_json or {}),
            }
            current_version, restores_version = int(active.version), int(previous.version)
            wording_known = released is not None
        change = self.create_check_item_draft(
            payload,
            actor=actor,
            reason=f"回退到 v{restores_version}",
            idempotency_key=f"catalog-rollback-{uuid.uuid4()}",
        )
        impact = self.preview_impact(change["id"])
        return {
            "change_id": change["id"],
            "check_item_id": check_item_id,
            "current_version": current_version,
            "restores_version": restores_version,
            "new_version": int(change["version"]),
            "restores_wording": wording_known,
            "impact_digest": impact["impact_digest"],
            "preview": self.preview_release(change["id"], actor=actor),
        }

    def discard_draft(self, change_id: str, *, actor: str) -> dict[str, Any]:
        """Withdraw a draft nobody submitted. Its version was never in force and stays on record."""

        actor = _require_text(actor, label="actor")
        now = _utcnow()
        with self._session_scope() as db:
            change = (
                db.query(CheckItemCatalogChangeRecord)
                .filter(CheckItemCatalogChangeRecord.id == str(change_id))
                .with_for_update()
                .first()
            )
            if change is None:
                raise ValueError("catalog change request not found")
            if change.status == "cancelled":
                return self._serialize_change(db, change)
            if change.status != "draft":
                raise CatalogGovernanceConflictError(
                    f"catalog change request cannot be discarded from {change.status}"
                )
            is_check_item = str(change.asset_kind) == "check_item"
            version = db.get(
                CheckItemVersionRecord if is_check_item else CollectionImplementationVersionRecord,
                str(change.version_id),
            )
            if version is not None and str(version.status) == "draft":
                version.status = "deprecated"
                version.deprecated_at = now
            if is_check_item:
                # Rules drafted for a preview of this version have nothing left to be approved with.
                from src.infrastructure.db.models import ChangePolicyVersionRecord, EventRuleVersionRecord

                for model in (ChangePolicyVersionRecord, EventRuleVersionRecord):
                    for row in db.query(model).filter(
                        model.check_item_version_id == str(change.version_id),
                        model.status == "draft",
                    ):
                        row.status = "rejected"
                # Adapters imported with it would only ever have gone into force with it.
                withdraw_incoming(db, str(change.version_id), now=now)
            elif version is not None:
                withdraw_draft_templates(db, [version], now=now)
            root = db.get(
                CheckItemRecord if is_check_item else CollectionImplementationRecord,
                str(change.asset_id),
            )
            # An asset that never had a published version goes with its only draft.
            if root is not None and str(root.status) == "draft" and int(root.current_version or 0) == 0:
                root.status = "deprecated"
                root.updated_at = now
            change.status = "cancelled"
            change.decided_by = actor
            change.decision_reason = "草稿已撤销"
            change.decided_at = now
            change.updated_at = now
            self._audit(
                db,
                action="check_item_catalog_draft_discarded",
                actor=actor,
                resource_id=str(change.id),
                idempotency_key=None,
                detail={"asset_id": str(change.asset_id), "version_id": str(change.version_id)},
            )
            db.flush()
            result = self._serialize_change(db, change)
            version_id = str(change.version_id)
        if self._release and is_check_item:
            # Their approval request goes the same way.
            self._reject_rule_drafts(version_id, actor=actor)
        return result

    @staticmethod
    def _reject_rule_drafts(check_item_version_id: str, *, actor: str) -> None:
        from src.apps.rule_governance.backend.rule_control.approval import RuleAssetApprovalService
        from src.infrastructure.db.models import RuleGenerationJobRecord

        with get_db_session() as db:
            bundle_ids = [
                str(row.id)
                for row in db.query(RuleGenerationJobRecord.id).filter(
                    RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE",
                    RuleGenerationJobRecord.asset_ref == f"check_item_version:{check_item_version_id}",
                    RuleGenerationJobRecord.status == "pending_approval",
                )
            ]
        for bundle_id in bundle_ids:
            RuleAssetApprovalService().reject(bundle_id, actor=actor, reason="检查项草稿已撤销")

    def retired_rule_events(self, check_item_id: str) -> list[dict[str, Any]]:
        """Open events of a check item whose rule no longer runs."""

        with self._session_scope() as db:
            return orphaned_events(db, _require_text(check_item_id, label="check item id"))

    def close_retired_rule_events(self, check_item_id: str, *, actor: str) -> dict[str, Any]:
        """Close the events a retired rule of this check item left open."""

        check_item_id = _require_text(check_item_id, label="check item id")
        actor = _require_text(actor, label="actor")
        with self._session_scope() as db:
            if db.get(CheckItemRecord, check_item_id) is None:
                raise ValueError("check item not found")
            closed = close_orphaned_events(
                db, check_item_id, reason="rule_retired", note="规则已停用", actor=actor, now=_utcnow()
            )
            if closed:
                self._audit(
                    db,
                    action="check_item_retired_rule_events_closed",
                    actor=actor,
                    resource_id=check_item_id,
                    idempotency_key=None,
                    detail={"events_closed": closed},
                )
            return {"check_item_id": check_item_id, "events_closed": closed}

    def delete_implementation(
        self,
        implementation_id: str,
        *,
        actor: str,
        reason: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Withdraw an implementation while preserving immutable audit history."""

        implementation_id = _require_text(
            implementation_id,
            label="implementation id",
        )
        _ensure_deletable(implementation=implementation_id)
        actor = _require_text(actor, label="actor")
        reason = str(reason or "").strip()
        idempotency_key = _require_text(
            idempotency_key,
            label="idempotency key",
            minimum=8,
        )
        audit_key = f"catalog-implementation:delete:{idempotency_key}"
        now = _utcnow()
        with self._session_scope() as db:
            replay = (
                db.query(AuditLogRecord)
                .filter(AuditLogRecord.idempotency_key == audit_key)
                .first()
            )
            if replay is not None:
                if str(replay.resource_id or "") != implementation_id:
                    raise CatalogGovernanceConflictError(
                        "idempotency key was already used for another collection implementation"
                    )
                detail = dict(replay.detail or {})
                return {
                    "success": True,
                    "implementation_id": implementation_id,
                    "status": "deprecated",
                    "cancelled_change_count": int(
                        detail.get("cancelled_change_count") or 0
                    ),
                    "idempotent_replay": True,
                }

            # Shared with runtime activation: serialize deletion and reactivation.
            root = (
                db.query(CollectionImplementationRecord)
                .filter(CollectionImplementationRecord.id == implementation_id)
                .with_for_update()
                .first()
            )
            if root is None:
                raise ValueError("collection implementation not found")
            versions = (
                db.query(CollectionImplementationVersionRecord)
                .filter(
                    CollectionImplementationVersionRecord.implementation_id
                    == implementation_id
                )
                .with_for_update()
                .all()
            )
            version_ids = [str(item.id) for item in versions]
            active_runtime = (
                db.query(CheckItemRuntimeBundleVersionRecord)
                .filter(
                    CheckItemRuntimeBundleVersionRecord.status == "active",
                    CheckItemRuntimeBundleVersionRecord.implementation_version_id.in_(
                        version_ids or [""]
                    ),
                )
                .with_for_update()
                .all()
            )
            retired_bundle_ids = sorted(str(item.id) for item in active_runtime)
            for bundle in active_runtime:
                bundle.status = "deprecated"
                bundle.deprecated_at = now
            # Keep frozen bundle contents and execution evidence intact; only remove
            # this implementation's bundles from production selection.
            for bundle_root in (
                db.query(CheckItemRuntimeBundleRecord)
                .filter(CheckItemRuntimeBundleRecord.implementation_id == implementation_id)
                .with_for_update()
                .all()
            ):
                bundle_root.status = "deprecated"
                bundle_root.updated_at = now

            pending_changes = (
                db.query(CheckItemCatalogChangeRecord)
                .filter(
                    CheckItemCatalogChangeRecord.asset_kind
                    == "collection_implementation",
                    CheckItemCatalogChangeRecord.asset_id == implementation_id,
                    CheckItemCatalogChangeRecord.status.in_(
                        ["draft", "pending_approval"]
                    ),
                )
                .with_for_update()
                .all()
            )
            for change in pending_changes:
                change.status = "cancelled"
                change.decided_by = actor
                change.decision_reason = reason
                change.decided_at = now
                change.updated_at = now
            for version in versions:
                if str(version.status) in {"draft", "active"}:
                    version.status = "deprecated"
                    version.deprecated_at = now
            root.status = "deprecated"
            root.updated_at = now
            detail = {
                "implementation_id": implementation_id,
                "version_ids": version_ids,
                "retired_runtime_bundle_version_ids": retired_bundle_ids,
                "cancelled_change_count": len(pending_changes),
                "reason": reason,
            }
            self._audit(
                db,
                action="collection_implementation_deleted",
                actor=actor,
                resource_id=implementation_id,
                idempotency_key=audit_key,
                detail=detail,
            )
            db.flush()
            return {
                "success": True,
                "implementation_id": implementation_id,
                "status": "deprecated",
                "cancelled_change_count": len(pending_changes),
                "idempotent_replay": False,
            }

    def list_changes(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 100))
        page_offset = max(0, int(offset))
        with self._session_scope() as db:
            query = db.query(CheckItemCatalogChangeRecord)
            if status:
                query = query.filter(CheckItemCatalogChangeRecord.status == status)
            if search:
                pattern = f"%{str(search).strip()}%"
                query = query.filter(
                    or_(
                        CheckItemCatalogChangeRecord.id.ilike(pattern),
                        CheckItemCatalogChangeRecord.asset_id.ilike(pattern),
                        CheckItemCatalogChangeRecord.version_id.ilike(pattern),
                    )
                )
            total = int(query.count())
            rows = (
                query.order_by(
                    CheckItemCatalogChangeRecord.created_at.desc(),
                    CheckItemCatalogChangeRecord.id.desc(),
                )
                .offset(page_offset)
                .limit(page_size)
                .all()
            )
            return {
                "items": [self._serialize_change(db, row) for row in rows],
                "total": total,
                "limit": page_size,
                "offset": page_offset,
            }

    def get_change(self, change_id: str) -> dict[str, Any]:
        with self._session_scope() as db:
            change = db.get(CheckItemCatalogChangeRecord, str(change_id))
            if change is None:
                raise ValueError("catalog change request not found")
            return self._serialize_change(db, change)

    def preview_impact(self, change_id: str) -> dict[str, Any]:
        with self._session_scope() as db:
            change = db.get(CheckItemCatalogChangeRecord, str(change_id))
            if change is None:
                raise ValueError("catalog change request not found")
            candidate = self._candidate(db, change)
            baseline_version_id = str(change.baseline_version_id or "")
            bundle_reference_column = (
                CheckItemRuntimeBundleVersionRecord.check_item_version_id
                if str(change.asset_kind) == "check_item"
                else CheckItemRuntimeBundleVersionRecord.implementation_version_id
            )
            active_bundle_rows = (
                db.query(CheckItemRuntimeBundleVersionRecord)
                .filter(CheckItemRuntimeBundleVersionRecord.status == "active")
                .filter(bundle_reference_column == baseline_version_id)
                .all()
                if baseline_version_id
                else []
            )
            active_bundle_version_ids = sorted(
                str(item.id) for item in active_bundle_rows
            )
            # Adapters imported with this version are still drafts, outside the
            # active catalog, but they go into force with its release.
            incoming_implementation_ids = (
                sorted(
                    {
                        str(item.implementation_id)
                        for item in incoming_adapters(db, str(change.version_id))
                    }
                )
                if str(change.asset_kind) == "check_item"
                else []
            )
        current = self._catalog.load_active()
        proposed = self._proposed_catalog(current, candidate)
        if isinstance(candidate, CheckItemDefinition):
            return self._preview_check_item_impact(
                release_switches_bundles=self._release,
                change_id=str(change_id),
                candidate=candidate,
                baseline_version_id=baseline_version_id or None,
                active_bundle_version_ids=active_bundle_version_ids,
                incoming_implementation_ids=incoming_implementation_ids,
                current=current,
                proposed=proposed,
                devices=list(self._inventory_loader()),
            )
        current_resolver = CheckItemImplementationResolver(current)
        proposed_resolver = CheckItemImplementationResolver(proposed)
        devices = list(self._inventory_loader())
        current_by_id = {item.id: item for item in current.implementations}
        affected = sorted(
            set(candidate.check_item_ids)
            | set(current_by_id.get(candidate.id, candidate).check_item_ids)
        )
        rows: list[dict[str, Any]] = []
        rollout_scope: list[dict[str, str]] = []
        summary = {
            "unchanged": 0,
            "changed": 0,
            "newly_supported": 0,
            "newly_unsupported": 0,
            "ambiguous": 0,
        }
        for device in devices:
            for check_item_id in affected:
                before = current_resolver.resolve(
                    device=device,
                    check_item_id=check_item_id,
                )
                after = proposed_resolver.resolve(
                    device=device,
                    check_item_id=check_item_id,
                )
                if after.status == "ambiguous":
                    category = "ambiguous"
                elif before.status != "resolved" and after.status == "resolved":
                    category = "newly_supported"
                elif before.status == "resolved" and after.status != "resolved":
                    category = "newly_unsupported"
                elif (
                    before.status != after.status
                    or before.implementation_version_id
                    != after.implementation_version_id
                ):
                    category = "changed"
                else:
                    category = "unchanged"
                summary[category] += 1
                if (
                    category in {"changed", "newly_supported"}
                    and after.status == "resolved"
                    and str(after.implementation_version_id or "")
                    == str(candidate.version_id)
                ):
                    rollout_scope.append(
                        {
                            "device_id": str(
                                device.get("device_id") or device.get("id") or ""
                            ),
                            "check_item_id": str(check_item_id),
                        }
                    )
                if category != "unchanged" and len(rows) < 100:
                    rows.append(
                        {
                            "device_id": str(
                                device.get("device_id") or device.get("id") or ""
                            ),
                            "device_name": str(device.get("device_name") or ""),
                            "vendor": device.get("vendor"),
                            "family": device.get("family"),
                            "model": device.get("model"),
                            "check_item_id": check_item_id,
                            "change": category,
                            "before": before.to_dict(),
                            "after": after.to_dict(),
                        }
                    )
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        if summary["ambiguous"]:
            blockers.append(
                {
                    "code": "implementation_resolution_ambiguous",
                    "message": "候选范围会让部分设备出现多个同等优先级采集实现。",
                    "count": summary["ambiguous"],
                    "next_action": "narrow_compatibility_selector",
                }
            )
        if summary["newly_unsupported"]:
            blockers.append(
                {
                    "code": "inventory_support_regression",
                    "message": "候选适用范围会让现网设备失去检查项支持。",
                    "count": summary["newly_unsupported"],
                    "next_action": "narrow_compatibility_selector",
                }
            )
        if active_bundle_version_ids:
            blockers.append(
                {
                    "code": "active_runtime_bundle_requires_rollout",
                    "message": (
                        "当前实现仍被生产运行包引用；现有发布模型尚不能证明"
                        "旧、新采集实现版本之间的安全切换。"
                    ),
                    "count": len(active_bundle_version_ids),
                    "next_action": "prepare_dual_implementation_rollout",
                }
            )
        if not devices:
            warnings.append(
                {
                    "code": "inventory_empty",
                    "message": "当前没有活动设备，无法证明兼容范围的实际命中与唯一性。",
                    "next_action": "import_inventory_or_review_manually",
                }
            )
        elif not (
            summary["changed"]
            + summary["newly_supported"]
            + summary["newly_unsupported"]
            + summary["ambiguous"]
        ):
            warnings.append(
                {
                    "code": "no_inventory_effect",
                    "message": "候选对当前活动设备没有可见影响，请确认是否为未来型号预配置。",
                    "next_action": "confirm_future_compatibility_scope",
                }
            )
        if candidate.parser_requirement == "structured":
            warnings.append(
                {
                    "code": "runtime_assets_require_reassembly",
                    "message": "发布采集实现后仍需为新版本准备 Parser、规则资产和 RuntimeBundle。",
                    "next_action": "prepare_runtime_bundle",
                }
            )
        snapshot: dict[str, Any] = {
            "schema_version": "check-item-catalog-impact/v1",
            "change_id": str(change_id),
            "asset_kind": "collection_implementation",
            "asset_id": candidate.id,
            "candidate_version_id": candidate.version_id,
            "baseline_version_id": baseline_version_id or None,
            "candidate_digest": candidate.digest,
            "inventory_device_count": len(devices),
            "target_pair_count": len(devices) * len(affected),
            "rollout_scope": sorted(
                rollout_scope,
                key=lambda item: (item["device_id"], item["check_item_id"]),
            ),
            "rollout_scope_count": len(rollout_scope),
            "affected_check_item_ids": affected,
            "summary": summary,
            "rows": rows,
            "rows_truncated": (
                sum(value for key, value in summary.items() if key != "unchanged")
                > len(rows)
            ),
            "active_runtime_bundle_version_ids": active_bundle_version_ids,
            "blockers": blockers,
            "warnings": warnings,
        }
        snapshot["impact_digest"] = _digest(snapshot)
        return snapshot

    @staticmethod
    def _preview_check_item_impact(
        *,
        release_switches_bundles: bool,
        change_id: str,
        candidate: CheckItemDefinition,
        baseline_version_id: str | None,
        active_bundle_version_ids: list[str],
        current: CheckItemCatalog,
        proposed: CheckItemCatalog,
        devices: list[dict[str, Any]],
        incoming_implementation_ids: list[str],
    ) -> dict[str, Any]:
        baseline = current.check_item(candidate.id)
        field_names = (
            "name",
            "category",
            "description",
            "result_mode",
            "assessment_mode",
            "canonical_contract",
            "report_projection",
            "applicability",
            "freshness_policy",
            "empty_result_policy",
            "coverage_policy",
        )
        changed_fields = [
            name
            for name in field_names
            if baseline is None or getattr(baseline, name) != getattr(candidate, name)
        ]

        def observation_types(item: CheckItemDefinition | None) -> set[str]:
            if item is None:
                return set()
            return {
                str(row.get("observation_type") or "")
                for row in item.canonical_contract.get("observations") or []
                if str(row.get("observation_type") or "")
            }

        before_observations = observation_types(baseline)
        after_observations = observation_types(candidate)
        implementation_ids = sorted(
            {
                implementation.id
                for implementation in proposed.implementations
                if candidate.id in implementation.check_item_ids
            }
            | set(incoming_implementation_ids)
        )
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        if active_bundle_version_ids and not release_switches_bundles:
            blockers.append(
                {
                    "code": "active_runtime_bundle_requires_check_item_retirement",
                    "message": "当前检查项版本仍被生产运行包引用，不能直接切换语义版本。",
                    "count": len(active_bundle_version_ids),
                    "next_action": "retire_or_rebuild_runtime_bundles",
                }
            )
        if not implementation_ids:
            warnings.append(
                {
                    "code": "check_item_has_no_collection_implementation",
                    "message": "该检查项尚未关联采集实现，发布后不会自动进入设备巡检计划。",
                    "next_action": "create_collection_implementation",
                }
            )
        if baseline is None:
            warnings.append(
                {
                    "code": "check_item_requires_profile_selection",
                    "message": "新检查项发布后仍需加入巡检 Profile，才会进入生产执行范围。",
                    "next_action": "add_check_item_to_patrol_profile",
                }
            )
        elif changed_fields and not release_switches_bundles:
            warnings.append(
                {
                    "code": "check_item_runtime_assets_require_reassembly",
                    "message": (
                        "检查项语义发生变化，关联 Parser、规则资产和运行包需要按新版本复核。"
                    ),
                    "next_action": "prepare_runtime_bundle",
                }
            )
        snapshot: dict[str, Any] = {
            "schema_version": "check-item-catalog-impact/v1",
            "change_id": change_id,
            "asset_kind": "check_item",
            "asset_id": candidate.id,
            "candidate_version_id": candidate.version_id,
            "baseline_version_id": baseline_version_id,
            "candidate_digest": candidate.digest,
            "inventory_device_count": len(devices),
            "target_pair_count": len(devices),
            "rollout_scope": [],
            "rollout_scope_count": 0,
            "affected_check_item_ids": [candidate.id],
            "summary": {
                "unchanged": 0,
                "changed": len(devices) if baseline is not None else 0,
                "newly_supported": len(devices) if baseline is None else 0,
                "newly_unsupported": 0,
                "ambiguous": 0,
            },
            "rows": [],
            "rows_truncated": False,
            "active_runtime_bundle_version_ids": active_bundle_version_ids,
            "semantic_diff": {
                "changed_fields": changed_fields,
                "added_observation_types": sorted(
                    after_observations - before_observations
                ),
                "removed_observation_types": sorted(
                    before_observations - after_observations
                ),
                "collection_implementation_ids": implementation_ids,
            },
            "blockers": blockers,
            "warnings": warnings,
        }
        snapshot["impact_digest"] = _digest(snapshot)
        return snapshot

    def publication_options(self, change_id: str) -> dict[str, Any]:
        """Return completed rollout campaigns that can authorize this cutover."""

        impact = self.preview_impact(change_id)
        with self._session_scope() as db:
            change = db.get(CheckItemCatalogChangeRecord, str(change_id))
            if change is None:
                raise ValueError("catalog change request not found")
            if str(change.asset_kind) == "check_item":
                return {
                    "change_id": str(change.id),
                    "requires_rollout": False,
                    "eligible_campaigns": [],
                }
            campaigns = (
                db.query(RolloutCampaignRecord)
                .filter(RolloutCampaignRecord.status == "completed")
                .order_by(
                    RolloutCampaignRecord.completed_at.desc(),
                    RolloutCampaignRecord.id.desc(),
                )
                .limit(100)
                .all()
            )
            eligible: list[dict[str, Any]] = []
            for campaign in campaigns:
                try:
                    evidence = self._validate_rollout_cutover(
                        db,
                        change,
                        impact,
                        str(campaign.id),
                    )
                except CatalogGovernanceConflictError:
                    continue
                eligible.append(
                    {
                        "campaign_id": str(campaign.id),
                        "name": str(campaign.name),
                        "completed_at": campaign.completed_at,
                        "candidate_runtime_bundle_version_ids": list(
                            evidence["candidate_runtime_bundle_version_ids"]
                        ),
                        "target_count": int(evidence["target_count"]),
                        "evidence_digest": str(evidence["evidence_digest"]),
                    }
                )
            requires_rollout = any(
                item.get("code") == "active_runtime_bundle_requires_rollout"
                for item in impact.get("blockers") or []
            )
            return {
                "change_id": str(change.id),
                "requires_rollout": requires_rollout,
                "eligible_campaigns": eligible,
            }

    @staticmethod
    def _validate_rollout_cutover(
        db: Any,
        change: CheckItemCatalogChangeRecord,
        impact: Mapping[str, Any],
        campaign_id: str,
    ) -> dict[str, Any]:
        campaign = db.get(RolloutCampaignRecord, str(campaign_id))
        if campaign is None or str(campaign.status) != "completed":
            raise CatalogGovernanceConflictError(
                "catalog cutover requires a completed rollout campaign"
            )
        target = dict(campaign.target_manifest_json or {})
        if str(target.get("schema_version") or "") != "rollout-target/v3":
            raise CatalogGovernanceConflictError(
                "catalog cutover requires a dual-implementation v3 rollout"
            )
        live_blockers = RolloutCampaignService._live_digest_blockers(db, campaign)
        if live_blockers:
            raise CatalogGovernanceConflictError(
                "rollout evidence changed after completion: "
                + "; ".join(live_blockers)
            )
        matrix = [dict(item) for item in target.get("matrix") or []]
        candidate_version_id = str(change.version_id)
        baseline_version_id = str(change.baseline_version_id or "")
        scoped_rows = [
            item
            for item in matrix
            if str(item.get("candidate_implementation_version_id") or "")
            == candidate_version_id
        ]
        if not scoped_rows:
            raise CatalogGovernanceConflictError(
                "rollout target does not contain the candidate implementation"
            )
        if any(
            str(dict(item.get("baseline_route") or {}).get("mode") or "")
            != "no_prior_production"
            and str(
                dict(item.get("baseline_route") or {}).get(
                    "implementation_version_id"
                )
                or ""
            )
            != baseline_version_id
            for item in scoped_rows
        ):
            raise CatalogGovernanceConflictError(
                "rollout baseline implementation differs from the catalog revision"
            )

        check_version_ids = sorted(
            {str(item.get("check_item_version_id") or "") for item in scoped_rows}
        )
        check_versions = (
            db.query(CheckItemVersionRecord)
            .filter(CheckItemVersionRecord.id.in_(check_version_ids))
            .all()
        )
        check_item_by_version = {
            str(item.id): str(item.check_item_id) for item in check_versions
        }
        actual_scope = {
            (
                str(item.get("device_id") or ""),
                check_item_by_version.get(
                    str(item.get("check_item_version_id") or ""),
                    "",
                ),
            )
            for item in scoped_rows
        }
        required_scope = {
            (str(item.get("device_id") or ""), str(item.get("check_item_id") or ""))
            for item in impact.get("rollout_scope") or []
        }
        if not required_scope.issubset(actual_scope):
            raise CatalogGovernanceConflictError(
                "rollout target does not cover every affected inventory device/check item"
            )

        refs = [
            dict(item)
            for item in campaign.runtime_bundle_refs_json or []
            if str(dict(item).get("role") or "") == "candidate"
        ]
        candidate_bundle_ids = sorted(
            {str(item.get("version_id") or "") for item in refs if item.get("version_id")}
        )
        matrix_bundle_ids = sorted(
            {
                str(item.get("candidate_runtime_bundle_version_id") or "")
                for item in scoped_rows
            }
        )
        if candidate_bundle_ids != matrix_bundle_ids:
            raise CatalogGovernanceConflictError(
                "rollout candidate RuntimeBundle references do not match the target"
            )
        bundles = (
            db.query(CheckItemRuntimeBundleVersionRecord)
            .filter(CheckItemRuntimeBundleVersionRecord.id.in_(candidate_bundle_ids))
            .all()
        )
        if len(bundles) != len(candidate_bundle_ids) or any(
            str(item.status) != "draft"
            or str(item.implementation_version_id) != candidate_version_id
            or not (
                dict(item.validation_report_json or {}).get("success") is True
                or dict(item.validation_report_json or {}).get("passed") is True
            )
            for item in bundles
        ):
            raise CatalogGovernanceConflictError(
                "rollout candidate RuntimeBundle is missing, changed, or no longer draft"
            )
        active_bundle_ids = set(
            str(item) for item in impact.get("active_runtime_bundle_version_ids") or []
        )
        baseline_bundle_ids = {
            str(dict(item.get("baseline_route") or {}).get("runtime_bundle_version_id") or "")
            for item in scoped_rows
            if str(dict(item.get("baseline_route") or {}).get("mode") or "")
            == "runtime_bundle"
        }
        if not active_bundle_ids.issubset(baseline_bundle_ids):
            raise CatalogGovernanceConflictError(
                "rollout does not cover every active baseline RuntimeBundle"
            )
        evidence = {
            "schema_version": "check-item-catalog-cutover/v1",
            "change_id": str(change.id),
            "rollout_campaign_id": str(campaign.id),
            "rollout_target_digest": str(campaign.target_manifest_digest or ""),
            "rollout_runtime_bundle_digest": str(campaign.runtime_bundle_digest or ""),
            "candidate_implementation_version_id": candidate_version_id,
            "baseline_implementation_version_id": baseline_version_id or None,
            "candidate_runtime_bundle_version_ids": candidate_bundle_ids,
            "baseline_runtime_bundle_version_ids": sorted(active_bundle_ids),
            "target_count": len(scoped_rows),
            "required_scope_count": len(required_scope),
        }
        evidence["evidence_digest"] = _digest(evidence)
        return evidence

    def submit(
        self,
        change_id: str,
        *,
        actor: str,
        reason: str | None = None,
        expected_impact_digest: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        actor = _require_text(actor, label="actor")
        reason = str(reason or "").strip()
        idempotency_key = _require_text(idempotency_key, label="idempotency key", minimum=8)
        impact = self.preview_impact(change_id)
        if str(expected_impact_digest or "") != str(impact["impact_digest"]):
            raise CatalogGovernanceConflictError(
                "catalog impact preview changed; refresh the preview before submission"
            )
        now = _utcnow()
        with self._session_scope() as db:
            change = db.get(CheckItemCatalogChangeRecord, str(change_id))
            if change is None:
                raise ValueError("catalog change request not found")
            if change.status == "pending_approval":
                if change.submission_idempotency_key == idempotency_key:
                    return self._serialize_change(db, change)
                raise CatalogGovernanceConflictError(
                    "catalog change request is already pending approval"
                )
            if change.status != "draft":
                raise CatalogGovernanceConflictError(
                    f"catalog change request cannot be submitted from {change.status}"
                )
            change.status = "pending_approval"
            change.impact_snapshot_json = impact
            change.impact_digest = str(impact["impact_digest"])
            change.submitted_by = actor
            change.submission_reason = reason
            change.submission_idempotency_key = idempotency_key
            change.submitted_at = now
            change.updated_at = now
            self._audit(
                db,
                action="check_item_catalog_change_submitted",
                actor=actor,
                resource_id=str(change.id),
                idempotency_key=f"catalog-change:submit:{idempotency_key}",
                detail={
                    "impact_digest": change.impact_digest,
                    "reason": reason,
                    "blocker_count": len(impact["blockers"]),
                },
            )
            db.flush()
            return self._serialize_change(db, change)

    def decide(
        self,
        change_id: str,
        *,
        decision: str,
        actor: str,
        reason: str | None = None,
        expected_impact_digest: str,
        idempotency_key: str,
        rollout_campaign_id: str | None = None,
        allow_same_actor: bool = False,
    ) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("catalog decision must be approved or rejected")
        actor = _require_text(actor, label="actor")
        reason = str(reason or "").strip()
        idempotency_key = _require_text(idempotency_key, label="idempotency key", minimum=8)
        impact = self.preview_impact(change_id)
        release_bundle_id = None
        releasing = self._release and decision == "approved" and impact.get("asset_kind") == "check_item"
        if releasing and not impact["blockers"]:
            # Rule drafts are compiled before the switch; they change nothing until approved with it.
            try:
                release_bundle_id = prepare_rules(str(impact["candidate_version_id"]), actor=actor)
            except CheckItemReleaseBlocked as exc:
                raise CatalogGovernanceConflictError(str(exc)) from exc
        now = _utcnow()
        with self._session_scope() as db:
            change = (
                db.query(CheckItemCatalogChangeRecord)
                .filter(CheckItemCatalogChangeRecord.id == str(change_id))
                .with_for_update()
                .first()
            )
            if change is None:
                raise ValueError("catalog change request not found")
            if change.status in {"approved", "rejected"}:
                if change.decision_idempotency_key == idempotency_key:
                    return self._serialize_change(db, change)
                raise CatalogGovernanceConflictError(
                    f"catalog change request is already {change.status}"
                )
            if change.status != "pending_approval":
                raise CatalogGovernanceConflictError(
                    f"catalog change request cannot be decided from {change.status}"
                )
            same_actor_override = bool(
                allow_same_actor
                and actor_matches_any(
                    actor,
                    [change.created_by, change.submitted_by],
                )
            )
            if (
                actor_matches_any(actor, [change.created_by, change.submitted_by])
                and not same_actor_override
            ):
                raise CatalogGovernanceConflictError(
                    "catalog publication approver must differ from the creator and submitter"
                )
            if decision == "approved" and (
                str(expected_impact_digest or "") != str(impact["impact_digest"])
                or str(change.impact_digest or "") != str(impact["impact_digest"])
            ):
                raise CatalogGovernanceConflictError(
                    "catalog impact changed after submission; create or resubmit a revision"
                )
            unresolved_blockers = [
                item
                for item in impact["blockers"]
                if item.get("code") != "active_runtime_bundle_requires_rollout"
            ]
            if decision == "approved" and unresolved_blockers:
                codes = ", ".join(item["code"] for item in unresolved_blockers)
                raise CatalogGovernanceConflictError(
                    f"catalog change request has unresolved blockers: {codes}"
                )
            requires_rollout = any(
                item.get("code") == "active_runtime_bundle_requires_rollout"
                for item in impact["blockers"]
            )
            if (
                decision == "approved"
                and requires_rollout
                and not str(rollout_campaign_id or "").strip()
            ):
                raise CatalogGovernanceConflictError(
                    "catalog change requires a completed dual-implementation rollout"
                )
            is_check_item = str(change.asset_kind) == "check_item"
            version_model = (
                CheckItemVersionRecord
                if is_check_item
                else CollectionImplementationVersionRecord
            )
            root_model = (
                CheckItemRecord if is_check_item else CollectionImplementationRecord
            )
            version = db.get(version_model, str(change.version_id))
            if version is None or str(version.status) != "draft":
                raise CatalogGovernanceConflictError(
                    "catalog candidate version is missing or no longer draft"
                )
            root = db.get(root_model, str(change.asset_id))
            if root is None:
                raise CatalogGovernanceConflictError(
                    "catalog candidate root no longer exists"
                )
            if is_check_item:
                active = (
                    db.query(CheckItemVersionRecord)
                    .filter(
                        CheckItemVersionRecord.check_item_id == change.asset_id,
                        CheckItemVersionRecord.status == "active",
                    )
                    .with_for_update()
                    .first()
                )
            else:
                active = (
                    db.query(CollectionImplementationVersionRecord)
                    .filter(
                        CollectionImplementationVersionRecord.implementation_id
                        == change.asset_id,
                        CollectionImplementationVersionRecord.status == "active",
                    )
                    .with_for_update()
                    .first()
                )
            actual_baseline = str(active.id) if active is not None else None
            expected_baseline = (
                str(change.baseline_version_id)
                if change.baseline_version_id
                else None
            )
            if actual_baseline != expected_baseline:
                raise CatalogGovernanceConflictError(
                    "active catalog asset changed after this revision was created"
                )
            if decision == "approved":
                cutover_evidence = None
                if requires_rollout:
                    cutover_evidence = self._validate_rollout_cutover(
                        db,
                        change,
                        impact,
                        str(rollout_campaign_id),
                    )
                    bundle_service = CheckItemRuntimeBundleService(
                        self._session_scope
                    )
                    bundle_ids = list(
                        cutover_evidence[
                            "candidate_runtime_bundle_version_ids"
                        ]
                    )
                    bundle_rows = (
                        db.query(CheckItemRuntimeBundleVersionRecord)
                        .filter(
                            CheckItemRuntimeBundleVersionRecord.id.in_(bundle_ids)
                        )
                        .all()
                    )
                    if not allow_same_actor and any(
                        actor_matches_any(actor, [item.created_by])
                        for item in bundle_rows
                    ):
                        raise CatalogGovernanceConflictError(
                            "catalog cutover approver must differ from every "
                            "candidate RuntimeBundle creator"
                        )
                    for bundle_id in bundle_ids:
                        try:
                            bundle_service.activate_in_session(
                                db,
                                bundle_id,
                                actor=actor,
                                reason=reason,
                                now=now,
                                allowed_draft_implementation_version_id=str(
                                    version.id
                                ),
                                audit_action="runtime_bundle_catalog_cutover",
                            )
                        except RuntimeBundleValidationError as exc:
                            raise CatalogGovernanceConflictError(
                                "candidate RuntimeBundle cutover validation failed: "
                                + "; ".join(exc.blockers)
                            ) from exc
                    change.rollout_campaign_id = str(rollout_campaign_id)
                    change.cutover_bundle_version_ids_json = bundle_ids
                    change.cutover_evidence_json = cutover_evidence
                    change.cutover_digest = str(
                        cutover_evidence["evidence_digest"]
                    )
                candidate_snapshot = self._candidate(db, change)
                if active is not None:
                    active.status = "deprecated"
                    active.deprecated_at = now
                    db.flush()
                version.status = "active"
                version.activated_at = now
                root.status = "active"
                root.current_version = int(version.version)
                root.name = str(candidate_snapshot.name)
                root.description = str(candidate_snapshot.description)
                if is_check_item:
                    root.category = str(candidate_snapshot.category)
                if releasing:
                    # One approval: rules, adapters and runtime bundles switch with the version or not at all.
                    try:
                        release = release_in_session(
                            db,
                            session_scope=lambda: _joined(db),
                            change_id=str(change.id),
                            root=root,
                            version=version,
                            previous=active,
                            bundle_id=release_bundle_id,
                            actor=actor,
                            reason=reason,
                            now=now,
                        )
                    except CheckItemReleaseBlocked as exc:
                        raise CatalogGovernanceConflictError(f"检查项无法发布：{exc}") from exc
                    change.cutover_bundle_version_ids_json = release["runtime_bundle_version_ids"]
                    change.cutover_evidence_json = release
                elif self._release and not requires_rollout:
                    # An adapter goes into force whole: the templates it reads and a runtime bundle
                    # for every check item version in force it serves, or not at all.
                    from src.apps.rule_governance.backend.check_items.import_release import (
                        activate_templates,
                        assemble_bundles,
                    )

                    db.flush()
                    templates, problems = activate_templates(
                        db, version, replaced=active, implementation_id=str(root.id), actor=actor, now=now
                    )
                    if problems:
                        raise CatalogGovernanceConflictError(
                            "适配器无法发布：" + "；".join(f"“{root.name}”{problem}" for problem in problems)
                        )
                    try:
                        bundles = assemble_bundles(
                            db, str(version.id), session_scope=lambda: _joined(db), actor=actor,
                            reason=reason or f"发布适配器 {root.name}", now=now,
                        )
                    except ValueError as exc:
                        raise CatalogGovernanceConflictError(f"适配器无法发布：{exc}") from exc
                    change.cutover_bundle_version_ids_json = bundles
                    change.cutover_evidence_json = {"kind": "adapter_release", "templates": templates,
                                                    "runtime_bundle_version_ids": bundles}
            else:
                version.status = "deprecated"
                version.deprecated_at = now
                if int(root.current_version or 0) == 0:
                    root.status = "deprecated"
            root.updated_at = now
            change.status = decision
            change.decided_by = actor
            change.decision_reason = reason
            change.decision_idempotency_key = idempotency_key
            change.decided_at = now
            change.updated_at = now
            self._audit(
                db,
                action=f"check_item_catalog_change_{decision}",
                actor=actor,
                resource_id=str(change.id),
                idempotency_key=f"catalog-change:{decision}:{idempotency_key}",
                detail={
                    "asset_id": str(change.asset_id),
                    "version_id": str(change.version_id),
                    "impact_digest": str(change.impact_digest),
                    "reason": reason,
                    "rollout_campaign_id": change.rollout_campaign_id,
                    "cutover_digest": change.cutover_digest,
                    "separation_of_duties_override": same_actor_override,
                },
            )
            db.flush()
            return self._serialize_change(db, change)

    def preview_release(self, change_id: str, *, actor: str) -> dict[str, Any]:
        """Say what approving a check item change would switch, by doing it and rolling back.

        The rule drafts compiled for the trial are kept; the switch itself is not.
        """

        from src.apps.rule_governance.backend.check_items.relations import (
            compile_relations,
            count_relations,
            diff_relations,
        )
        from src.apps.rule_governance.backend.check_items.rule_preview import (
            describe_compiled_rules,
            rules_in_force,
        )
        from src.apps.rule_governance.backend.check_items.trial import trial_on_last_patrol

        with self._session_scope() as db:
            change = db.get(CheckItemCatalogChangeRecord, str(change_id))
            if change is None:
                raise ValueError("catalog change request not found")
            if (
                not self._release
                or str(change.asset_kind) != "check_item"
                or str(change.status) not in {"draft", "pending_approval"}
            ):
                return {"applicable": False, "releasable": False, "blockers": [], "fields": [], "release": None}
            version = db.get(CheckItemVersionRecord, str(change.version_id))
            candidate = self._candidate(db, change)
            check_item_id, version_id = str(change.asset_id), str(change.version_id)
            baseline = db.get(CheckItemVersionRecord, str(change.baseline_version_id or ""))
            result = {
                "applicable": True,
                "check_item_id": check_item_id,
                "from_version": int(baseline.version) if baseline is not None else None,
                "to_version": int(version.version),
            }
            result["graph"] = count_relations(
                db,
                diff_relations(
                    compile_relations(
                        check_item_id=check_item_id,
                        result_mode=str(baseline.result_mode),
                        assessment_mode=str(baseline.assessment_mode),
                        canonical_contract=dict(baseline.canonical_contract_json or {}),
                    )
                    if baseline is not None
                    else None,
                    compile_relations(
                        check_item_id=check_item_id,
                        result_mode=candidate.result_mode,
                        assessment_mode=candidate.assessment_mode,
                        canonical_contract=candidate.canonical_contract,
                    ),
                ),
            )
            try:
                result["trial"] = trial_on_last_patrol(
                    db,
                    check_item_id=check_item_id,
                    current={
                        "version_id": str(baseline.id),
                        "result_mode": str(baseline.result_mode),
                        "assessment_mode": str(baseline.assessment_mode),
                        "canonical_contract": dict(baseline.canonical_contract_json or {}),
                    }
                    if baseline is not None
                    else None,
                    candidate={
                        "version_id": version_id,
                        "result_mode": candidate.result_mode,
                        "assessment_mode": candidate.assessment_mode,
                        "canonical_contract": candidate.canonical_contract,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - a trial that cannot run must not hide whether the release can
                result["trial"] = {"available": False, "reason": "error", "detail": str(exc)}
        after = describe_compiled_rules(
            check_item_id=check_item_id,
            result_mode=candidate.result_mode,
            assessment_mode=candidate.assessment_mode,
            canonical_contract=candidate.canonical_contract,
        )
        before = {}
        if result["from_version"] is not None:
            try:
                before = {field["canonical_field"]: field["lines"] for field in rules_in_force(check_item_id)["fields"]}
            except KeyError:
                before = {}
        fields = []
        for name, lines in after["fields"].items():
            now_lines = before.pop(name, None)
            kind = "added" if now_lines is None and result["from_version"] is not None else (
                "unchanged" if (now_lines or []) == lines else "changed"
            )
            fields.append({"canonical_field": name, "change": kind, "before": now_lines or [], "after": lines})
        fields.extend(
            {"canonical_field": name, "change": "removed", "before": lines, "after": []}
            for name, lines in before.items()
        )
        result["fields"] = fields
        blockers = list(after["errors"])
        release = None
        if not blockers:
            try:
                bundle_id = prepare_rules(version_id, actor=actor)
                with self._session_scope() as db:
                    try:
                        change = db.get(CheckItemCatalogChangeRecord, str(change_id))
                        version = db.get(CheckItemVersionRecord, version_id)
                        root = db.get(CheckItemRecord, check_item_id)
                        active = (
                            db.query(CheckItemVersionRecord)
                            .filter(
                                CheckItemVersionRecord.check_item_id == check_item_id,
                                CheckItemVersionRecord.status == "active",
                            )
                            .first()
                        )
                        now = _utcnow()
                        if active is not None:
                            active.status = "deprecated"
                            active.deprecated_at = now
                            db.flush()
                        version.status = "active"
                        version.activated_at = now
                        root.status = "active"
                        root.current_version = int(version.version)
                        release = release_in_session(
                            db,
                            session_scope=lambda: _joined(db),
                            change_id=str(change.id),
                            root=root,
                            version=version,
                            previous=active,
                            bundle_id=bundle_id,
                            actor=actor,
                            reason="发布前试算",
                            now=now,
                        )
                    finally:
                        db.rollback()
            except CheckItemReleaseBlocked as exc:
                blockers.extend(exc.blockers)
        result["blockers"] = blockers
        result["releasable"] = not blockers
        result["release"] = release
        return result

    def _candidate(
        self,
        db: Any,
        change: CheckItemCatalogChangeRecord,
    ) -> CheckItemDefinition | CollectionImplementation:
        if str(change.asset_kind) == "check_item":
            version = db.get(CheckItemVersionRecord, str(change.version_id))
            root = db.get(CheckItemRecord, str(change.asset_id))
            if version is None or root is None:
                raise ValueError("catalog candidate asset is incomplete")
            return _check_item_from_row(
                root,
                version,
                candidate_payload=dict(change.candidate_payload_json or {}),
            )
        if str(change.asset_kind) != "collection_implementation":
            raise ValueError("unsupported catalog change asset kind")
        version = db.get(CollectionImplementationVersionRecord, str(change.version_id))
        root = db.get(CollectionImplementationRecord, str(change.asset_id))
        if version is None or root is None:
            raise ValueError("catalog candidate asset is incomplete")
        links = (
            db.query(CheckItemImplementationLinkRecord, CheckItemVersionRecord)
            .join(
                CheckItemVersionRecord,
                CheckItemVersionRecord.id
                == CheckItemImplementationLinkRecord.check_item_version_id,
            )
            .filter(
                CheckItemImplementationLinkRecord.implementation_version_id
                == version.id
            )
            .all()
        )
        return _implementation_from_row(
            root,
            version,
            check_item_ids=[str(item.check_item_id) for _link, item in links],
            candidate_payload=dict(change.candidate_payload_json or {}),
        )

    @staticmethod
    def _proposed_catalog(
        current: CheckItemCatalog,
        candidate: CheckItemDefinition | CollectionImplementation,
    ) -> CheckItemCatalog:
        proposed_candidate = replace(candidate, status="active")
        if isinstance(candidate, CheckItemDefinition):
            check_items = [
                item for item in current.check_items if item.id != candidate.id
            ]
            check_items.append(proposed_candidate)
            return CheckItemCatalog(
                catalog_version=max(current.catalog_version, candidate.version),
                check_items=tuple(sorted(check_items, key=lambda item: item.id)),
                implementations=current.implementations,
            )
        implementations = [
            item for item in current.implementations if item.id != candidate.id
        ]
        implementations.append(proposed_candidate)
        return CheckItemCatalog(
            catalog_version=max(current.catalog_version, candidate.version),
            check_items=current.check_items,
            implementations=tuple(sorted(implementations, key=lambda item: item.id)),
        )

    def _serialize_change(
        self,
        db: Any,
        change: CheckItemCatalogChangeRecord,
    ) -> dict[str, Any]:
        payload = change.to_dict()
        candidate = self._candidate(db, change)
        if isinstance(candidate, CheckItemDefinition):
            payload["candidate"] = {
                "check_item_id": candidate.id,
                "check_item_version_id": candidate.version_id,
                "version": candidate.version,
                "name": candidate.name,
                "category": candidate.category,
                "description": candidate.description,
                "result_mode": candidate.result_mode,
                "assessment_mode": candidate.assessment_mode,
                "canonical_contract": candidate.canonical_contract,
                "report_projection": candidate.report_projection,
                "applicability": candidate.applicability,
                "freshness_policy": candidate.freshness_policy,
                "empty_result_policy": candidate.empty_result_policy,
                "coverage_policy": candidate.coverage_policy,
                "status": candidate.status,
                "digest": candidate.digest,
            }
            return payload
        payload["candidate"] = {
            "implementation_id": candidate.id,
            "implementation_version_id": candidate.version_id,
            "version": candidate.version,
            "name": candidate.name,
            "description": candidate.description,
            "check_item_ids": list(candidate.check_item_ids),
            "match": candidate.match,
            "commands": [
                {"command": item.command, "priority": item.priority}
                for item in candidate.commands
            ],
            "collector": candidate.collector,
            "parser_requirement": candidate.parser_requirement,
            "template_scope": candidate.template_scope,
            "adapter_spec": candidate.adapter_spec,
            "selection_priority": candidate.selection_priority,
            "output_quality_policy": candidate.output_quality_policy,
            "status": candidate.status,
            "digest": candidate.digest,
        }
        return payload

    @staticmethod
    def _audit(
        db: Any,
        *,
        action: str,
        actor: str,
        resource_id: str,
        idempotency_key: str,
        detail: Mapping[str, Any],
    ) -> None:
        db.add(
            AuditLogRecord(
                id=str(uuid.uuid4()),
                idempotency_key=idempotency_key,
                user_id=actor,
                username=actor,
                action=action,
                resource_type="check_item_catalog_change",
                resource_id=resource_id,
                detail=dict(detail),
                status="success",
                created_at=_utcnow(),
            )
        )
