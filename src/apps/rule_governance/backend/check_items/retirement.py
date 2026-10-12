"""Take what a check item no longer asks for out of the patrol.

A rule that stops running leaves behind the events it opened: nothing will ever
recover them, so they are closed with the reason. A deleted check item takes its
rules, runtime bundles and the adapters that only serve it along. Versions,
reports and audit records are never touched.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func

from src.core.patrol.change_event_repository import ACTIVE_EVENT_STATUSES, close_events_in_session
from src.infrastructure.db.models import (
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckItemCatalogChangeRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    NetworkEventRecord,
)


def retire_rules(db: Any, check_item_version_ids: Iterable[str], *, now: datetime) -> list[dict[str, str]]:
    """Retire the change policies and event rules still active for these check item versions."""

    version_ids = [str(item) for item in check_item_version_ids]
    retired: list[dict[str, str]] = []
    if not version_ids:
        return retired
    for policy_version in (
        db.query(ChangePolicyVersionRecord)
        .filter(
            ChangePolicyVersionRecord.status == "active",
            ChangePolicyVersionRecord.check_item_version_id.in_(version_ids),
        )
        .with_for_update()
    ):
        policy_version.status = "deprecated"
        policy_version.deprecated_at = now
        policy = db.get(ChangePolicyRecord, policy_version.policy_id)
        if policy is not None and int(policy.current_version or 0) == int(policy_version.version):
            policy.current_version = 0
            policy.status = "deprecated"
            policy.updated_at = now
        retired.append({"kind": "change_policy", "name": str(policy_version.observation_type)})
    for rule_version in (
        db.query(EventRuleVersionRecord)
        .filter(
            EventRuleVersionRecord.status == "active",
            EventRuleVersionRecord.check_item_version_id.in_(version_ids),
        )
        .with_for_update()
    ):
        rule_version.status = "deprecated"
        rule_version.deprecated_at = now
        rule = db.get(EventRuleRecord, rule_version.rule_id)
        if rule is not None and int(rule.current_version or 0) == int(rule_version.version):
            rule.current_version = 0
            rule.status = "deprecated"
            rule.updated_at = now
        retired.append({"kind": "event_rule", "name": str(rule_version.event_type)})
    db.flush()
    return retired


def owned_event_types(db: Any, check_item_id: str) -> dict[str, dict[str, Any]]:
    """Every event type a rule of this check item has ever produced, as its latest rule describes it."""

    owned: dict[str, dict[str, Any]] = {}
    for rule_version in (
        db.query(EventRuleVersionRecord)
        .join(CheckItemVersionRecord, CheckItemVersionRecord.id == EventRuleVersionRecord.check_item_version_id)
        .filter(CheckItemVersionRecord.check_item_id == check_item_id)
        .filter(EventRuleVersionRecord.status.in_(["active", "deprecated"]))
        .order_by(CheckItemVersionRecord.version, EventRuleVersionRecord.version)
    ):
        fields = dict(rule_version.condition_json or {}).get("fields") or []
        owned[str(rule_version.event_type)] = {
            "event_type": str(rule_version.event_type),
            "event_class": str(rule_version.event_class or "fault"),
            "severity": str(rule_version.severity),
            "field": str(fields[0]) if fields else "",
        }
    return owned


def event_types_in_force(db: Any) -> set[str]:
    """Event types some active rule, of any check item, produces today."""

    return {
        str(row.event_type)
        for row in db.query(EventRuleVersionRecord.event_type)
        .join(EventRuleRecord, EventRuleRecord.id == EventRuleVersionRecord.rule_id)
        .filter(EventRuleRecord.status == "active", EventRuleVersionRecord.status == "active")
        .distinct()
    }


def event_counts(db: Any, event_types: Iterable[str]) -> dict[str, dict[str, int]]:
    """Stored events per type: how many are still open and how many there are in all."""

    wanted = sorted({str(item) for item in event_types if item})
    counts: dict[str, dict[str, int]] = {}
    if not wanted:
        return counts
    for event_type, status, number in (
        db.query(NetworkEventRecord.event_type, NetworkEventRecord.status, func.count(NetworkEventRecord.id))
        .filter(NetworkEventRecord.event_type.in_(wanted))
        .group_by(NetworkEventRecord.event_type, NetworkEventRecord.status)
    ):
        entry = counts.setdefault(str(event_type), {"open": 0, "total": 0})
        entry["total"] += int(number)
        if status in ACTIVE_EVENT_STATUSES:
            entry["open"] += int(number)
    return counts


def orphaned_events(db: Any, check_item_id: str) -> list[dict[str, Any]]:
    """Open events of this check item whose rule is no longer in force anywhere."""

    owned = owned_event_types(db, check_item_id)
    orphaned = sorted(set(owned) - event_types_in_force(db))
    counts = event_counts(db, orphaned)
    return [
        {**owned[event_type], **counts[event_type]}
        for event_type in orphaned
        if counts.get(event_type, {}).get("open")
    ]


def close_orphaned_events(
    db: Any,
    check_item_id: str,
    *,
    reason: str,
    note: str,
    actor: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Close what ``orphaned_events`` lists; an event type that is still judged is left alone."""

    db.flush()
    owned = owned_event_types(db, check_item_id)
    closed = close_events_in_session(
        db,
        event_types=set(owned) - event_types_in_force(db),
        reason=reason,
        note=note,
        actor=actor,
        now=now,
    )
    return [{**owned[event_type], "count": count} for event_type, count in sorted(closed.items())]


def _withdraw_adapter(db: Any, adapter: CollectionImplementationRecord, *, actor: str, reason: str, now: datetime) -> None:
    versions = (
        db.query(CollectionImplementationVersionRecord)
        .filter(CollectionImplementationVersionRecord.implementation_id == adapter.id)
        .with_for_update()
        .all()
    )
    for bundle in (
        db.query(CheckItemRuntimeBundleVersionRecord)
        .filter(
            CheckItemRuntimeBundleVersionRecord.status == "active",
            CheckItemRuntimeBundleVersionRecord.implementation_version_id.in_([str(item.id) for item in versions] or [""]),
        )
        .with_for_update()
    ):
        bundle.status = "deprecated"
        bundle.deprecated_at = now
    for bundle_root in (
        db.query(CheckItemRuntimeBundleRecord)
        .filter(CheckItemRuntimeBundleRecord.implementation_id == adapter.id)
        .with_for_update()
    ):
        bundle_root.status = "deprecated"
        bundle_root.updated_at = now
    for change in (
        db.query(CheckItemCatalogChangeRecord)
        .filter(
            CheckItemCatalogChangeRecord.asset_kind == "collection_implementation",
            CheckItemCatalogChangeRecord.asset_id == adapter.id,
            CheckItemCatalogChangeRecord.status.in_(["draft", "pending_approval"]),
        )
        .with_for_update()
    ):
        change.status = "cancelled"
        change.decided_by = actor
        change.decision_reason = reason
        change.decided_at = now
        change.updated_at = now
    for version in versions:
        if str(version.status) in {"draft", "active"}:
            version.status = "deprecated"
            version.deprecated_at = now
    adapter.status = "deprecated"
    adapter.updated_at = now


def retire_check_item(db: Any, root: CheckItemRecord, *, actor: str, reason: str, now: datetime) -> dict[str, Any]:
    """Take ``root`` out of the patrol together with everything that exists only because of it.

    An adapter that also serves another check item stays and keeps serving it.
    Joins the caller's transaction, so a trial is this call followed by a rollback.
    """

    check_item_id = str(root.id)
    versions = (
        db.query(CheckItemVersionRecord)
        .filter(CheckItemVersionRecord.check_item_id == check_item_id)
        .with_for_update()
        .all()
    )
    version_ids = [str(item.id) for item in versions]
    names = {str(row.id): str(row.name) for row in db.query(CheckItemRecord.id, CheckItemRecord.name)}

    bundles = (
        db.query(CheckItemRuntimeBundleVersionRecord)
        .filter(
            CheckItemRuntimeBundleVersionRecord.status == "active",
            CheckItemRuntimeBundleVersionRecord.check_item_version_id.in_(version_ids or [""]),
        )
        .with_for_update()
        .all()
    )
    for bundle in bundles:
        bundle.status = "deprecated"
        bundle.deprecated_at = now
    for bundle_root in (
        db.query(CheckItemRuntimeBundleRecord)
        .filter(CheckItemRuntimeBundleRecord.check_item_id == check_item_id)
        .with_for_update()
    ):
        bundle_root.status = "deprecated"
        bundle_root.updated_at = now

    withdrawn: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    adapters = (
        db.query(CollectionImplementationRecord, CollectionImplementationVersionRecord)
        .join(
            CollectionImplementationVersionRecord,
            CollectionImplementationVersionRecord.implementation_id == CollectionImplementationRecord.id,
        )
        .join(
            CheckItemImplementationLinkRecord,
            CheckItemImplementationLinkRecord.implementation_version_id == CollectionImplementationVersionRecord.id,
        )
        .filter(
            CheckItemImplementationLinkRecord.check_item_version_id.in_(version_ids or [""]),
            CollectionImplementationRecord.status == "active",
            CollectionImplementationVersionRecord.status == "active",
        )
        .order_by(CollectionImplementationRecord.id)
        .with_for_update(of=CollectionImplementationRecord)
        .all()
    )
    seen: set[str] = set()
    for adapter, adapter_version in adapters:
        if str(adapter.id) in seen:
            continue
        seen.add(str(adapter.id))
        others = sorted(
            {
                str(row.check_item_id)
                for row in db.query(CheckItemVersionRecord.check_item_id)
                .join(
                    CheckItemImplementationLinkRecord,
                    CheckItemImplementationLinkRecord.check_item_version_id == CheckItemVersionRecord.id,
                )
                .filter(
                    CheckItemImplementationLinkRecord.implementation_version_id == str(adapter_version.id),
                    CheckItemVersionRecord.status == "active",
                    CheckItemVersionRecord.check_item_id != check_item_id,
                )
            }
        )
        entry = {"implementation_id": str(adapter.id), "name": str(adapter.name)}
        if others:
            kept.append({**entry, "also_serves": [names.get(item, item) for item in others]})
            continue
        _withdraw_adapter(db, adapter, actor=actor, reason=reason, now=now)
        withdrawn.append(entry)

    rules = retire_rules(db, version_ids, now=now)

    pending = (
        db.query(CheckItemCatalogChangeRecord)
        .filter(
            CheckItemCatalogChangeRecord.asset_kind == "check_item",
            CheckItemCatalogChangeRecord.asset_id == check_item_id,
            CheckItemCatalogChangeRecord.status.in_(["draft", "pending_approval"]),
        )
        .with_for_update()
        .all()
    )
    for change in pending:
        change.status = "cancelled"
        change.decided_by = actor
        change.decision_reason = reason
        change.decided_at = now
        change.updated_at = now
    for version in versions:
        if str(version.status) in {"draft", "active"}:
            version.status = "deprecated"
            version.deprecated_at = now
    current_version = int(root.current_version or 0)
    root.status = "deprecated"
    root.updated_at = now

    events = close_orphaned_events(
        db, check_item_id, reason="check_item_deleted", note="检查项已删除", actor=actor, now=now
    )
    return {
        "check_item_id": check_item_id,
        "name": str(root.name),
        "version": current_version,
        "version_ids": version_ids,
        "version_count": len(versions),
        "cancelled_change_count": len(pending),
        "rules": rules,
        "runtime_bundle_version_ids": sorted(str(item.id) for item in bundles),
        "adapters_withdrawn": withdrawn,
        "adapters_kept": kept,
        "events_closed": events,
    }


__all__ = [
    "close_orphaned_events",
    "event_counts",
    "event_types_in_force",
    "orphaned_events",
    "owned_event_types",
    "retire_check_item",
    "retire_rules",
]
