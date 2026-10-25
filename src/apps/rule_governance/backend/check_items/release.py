"""Release a check item version together with everything derived from it.

Change policies, event rules and runtime bundles are compiled from a check
item. When a new version of it is approved they switch with it, inside the
approval's own transaction: either the whole set moves to the new version or
nothing does, and the previous version keeps running. Events left open by a
rule the new version no longer has are closed in the same step.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any

from src.apps.rule_governance.backend.check_items.catalog import build_collection_implementation
from src.apps.rule_governance.backend.check_items.import_release import bring_in, incoming_adapters
from src.apps.rule_governance.backend.check_items.retirement import close_orphaned_events, retire_rules
from src.apps.rule_governance.backend.check_items.system_items import system_check_item_ids
from src.core.check_items.command_adapter import validate_adapter_spec
from src.infrastructure.db.models import (
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    RuleGenerationJobRecord,
)


class CheckItemReleaseBlocked(ValueError):
    """The new version cannot take over; the reasons are for the engineer to read."""

    def __init__(self, blockers: list[str]) -> None:
        super().__init__("；".join(blockers))
        self.blockers = list(blockers)


def prepare_rules(check_item_version_id: str, *, actor: str) -> str | None:
    """Compile the version's rules into drafts and return their bundle, if it needs any.

    Runs before the switch and in its own transactions. Drafts change nothing
    until the bundle is approved, and the same drafts are found again on a retry.
    """

    from src.apps.rule_governance.backend.check_items.canonical_rule_context import (
        CanonicalRuleContextService,
    )

    try:
        drafts = CanonicalRuleContextService().create_drafts(check_item_version_id, actor=actor)
    except ValueError as exc:
        raise CheckItemReleaseBlocked([f"规则无法生成：{exc}"]) from exc
    return str(drafts["bundle_id"]) if drafts.get("bundle_id") else None


def _link(db: Any, check_item_version_id: str, implementation_version_id: str, *, like: Any, now: datetime) -> None:
    exists = (
        db.query(CheckItemImplementationLinkRecord.id)
        .filter(
            CheckItemImplementationLinkRecord.check_item_version_id == check_item_version_id,
            CheckItemImplementationLinkRecord.implementation_version_id == implementation_version_id,
        )
        .first()
    )
    if exists is None:
        db.add(
            CheckItemImplementationLinkRecord(
                id=str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/check-item-link/{check_item_version_id}/{implementation_version_id}",
                )),
                check_item_version_id=check_item_version_id,
                implementation_version_id=implementation_version_id,
                priority=int(like.priority),
                required=bool(like.required),
                created_at=now,
            )
        )


def _fit_projection(pinned: list[dict[str, Any]], target: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Drop what the adapter maps to fields the version no longer has; name required fields it lacks.

    A removed field needs no human: the adapter simply stops writing it. A
    required field the device output is not parsed for cannot be invented here.
    """

    from src.core.check_items.command_adapter import _field_specs, _primary_keys

    specs, keys = _field_specs(target), set(_primary_keys(target))
    required = keys | {name for name, details in specs.items() if details.get("required", True) is not False}
    dropped: list[str] = []
    missing: set[str] = set()
    for output in pinned:
        fields = dict(output.get("projection") or {}).get("fields")
        if not isinstance(fields, dict):
            continue
        for name in [name for name in fields if name not in specs and name not in keys]:
            del fields[name]
            dropped.append(name)
        missing |= required - set(fields)
    return sorted(set(dropped)), sorted(missing)


def _dimension_misfit(pinned: list[dict[str, Any]], target: dict[str, Any]) -> str:
    """Name the fields the version tells apart by other dimensions than the adapter outputs them by.

    A field's dimensions are part of what identifies each value, so an adapter is
    never moved onto different ones silently. The form does not edit them either:
    a draft that differs was not made that way on purpose.
    """

    from src.core.check_items.command_adapter import _field_specs

    specs = _field_specs(target)
    groups: dict[tuple[tuple[str, ...], tuple[str, ...]], set[str]] = {}
    for output in pinned:
        fields = dict(output.get("projection") or {}).get("fields")
        if not isinstance(fields, dict):
            continue
        for name, binding in fields.items():
            declared = tuple(sorted(str(item) for item in specs.get(name, {}).get("dimensions") or []))
            bound = tuple(sorted(dict(binding.get("dimensions") or {}))) if isinstance(binding, dict) else ()
            if declared != bound:
                groups.setdefault((declared, bound), set()).add(name)
    if not groups:
        return ""
    parts = []
    for (declared, bound), names in sorted(groups.items()):
        listed = "、".join(sorted(names)[:3]) + (" 等" if len(names) > 3 else "")
        parts.append(
            f"{len(names)} 个字段（{listed}）在新版本里"
            + (f"按 {'、'.join(declared)} 区分" if declared else "不分维度")
            + "，适配器"
            + (f"按 {'、'.join(bound)} 输出" if bound else "输出时不分维度")
        )
    return "；".join(parts) + "。表单不修改维度：撤销这份草稿后重新修改，字段会保持已发布版本的维度"


def _repin_adapters(
    db: Any,
    root: CheckItemRecord,
    version: CheckItemVersionRecord,
    previous: CheckItemVersionRecord,
    *,
    actor: str,
    now: datetime,
    replaced: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Move every active adapter of the previous version onto the new one.

    An adapter in ``replaced`` arrives in a newer version with the release itself
    (from a rule package) and is left to that version.

    A projection adapter names the check item version it writes for. It gets a
    new version of its own, pinned to the new check item version and validated
    against the new fields; an adapter that no longer fits blocks the release.
    """

    rows = (
        db.query(CollectionImplementationRecord, CollectionImplementationVersionRecord, CheckItemImplementationLinkRecord)
        .join(
            CollectionImplementationVersionRecord,
            CollectionImplementationVersionRecord.implementation_id == CollectionImplementationRecord.id,
        )
        .join(
            CheckItemImplementationLinkRecord,
            CheckItemImplementationLinkRecord.implementation_version_id == CollectionImplementationVersionRecord.id,
        )
        .filter(
            CheckItemImplementationLinkRecord.check_item_version_id == str(previous.id),
            CollectionImplementationRecord.status == "active",
            CollectionImplementationVersionRecord.status == "active",
        )
        .order_by(CollectionImplementationRecord.id)
        .with_for_update(of=CollectionImplementationRecord)
        .all()
    )
    misfits: dict[str, list[str]] = {}
    adapters: list[dict[str, Any]] = []
    for adapter, adapter_version, link in rows:
        if str(adapter.id) in replaced:
            continue
        spec = deepcopy(dict(adapter_version.adapter_spec_json or {}))
        pinned = [output for output in spec.get("outputs") or [] if output.get("check_item_id") == root.id]
        moved = {
            "implementation_id": str(adapter.id),
            "name": str(adapter.name),
            "previous_version_id": str(adapter_version.id),
            "version_id": str(adapter_version.id),
            "repinned": False,
            "dropped_fields": [],
        }
        adapters.append(moved)
        if not pinned:
            _link(db, str(version.id), str(adapter_version.id), like=link, now=now)
            continue
        # The adapter may serve other check items; each stays on its own active version.
        served = {
            str(item_version.check_item_id): item_version
            for item_version in db.query(CheckItemVersionRecord)
            .join(
                CheckItemImplementationLinkRecord,
                CheckItemImplementationLinkRecord.check_item_version_id == CheckItemVersionRecord.id,
            )
            .filter(
                CheckItemImplementationLinkRecord.implementation_version_id == str(adapter_version.id),
                CheckItemVersionRecord.status == "active",
            )
        }
        served[str(root.id)] = version
        # An output for a check item that has since been deleted has nowhere to go.
        spec["outputs"] = [output for output in spec["outputs"] if output.get("check_item_id") in served]
        for output in pinned:
            output["check_item_version_id"] = str(version.id)
        dropped, missing = _fit_projection(
            pinned,
            {"version_id": str(version.id), "canonical_contract": dict(version.canonical_contract_json or {})},
        )
        latest = max(
            int(row.version)
            for row in db.query(CollectionImplementationVersionRecord.version).filter(
                CollectionImplementationVersionRecord.implementation_id == adapter.id
            )
        )
        try:
            spec = validate_adapter_spec(
                spec,
                check_item_ids=tuple(sorted(served)),
                check_items={
                    item_id: {
                        "version_id": str(item_version.id),
                        "canonical_contract": dict(item_version.canonical_contract_json or {}),
                    }
                    for item_id, item_version in served.items()
                },
                require_pinned_check_item_versions=True,
            )
            candidate = build_collection_implementation(
                {
                    "id": str(adapter.id),
                    "version": latest + 1,
                    "name": str(adapter.name),
                    "description": str(adapter.description or ""),
                    "check_item_ids": sorted(served),
                    "match": dict(adapter_version.match_json or {}),
                    "commands": [dict(command) for command in adapter_version.commands_json or []],
                    "collector": str(adapter_version.collector),
                    "parser_requirement": str(adapter_version.parser_requirement),
                    "template_scope": adapter_version.template_scope,
                    "adapter_spec": spec,
                    "output_quality_policy": dict(adapter_version.output_quality_policy_json or {}),
                    "selection_priority": int(adapter_version.selection_priority),
                    "status": "active",
                },
                known_check_item_ids=set(served),
            )
        except ValueError as exc:
            reason = (
                f"不提供必填字段 {'、'.join(missing)}。可以先把这些字段设为选填发布，适配器补上后再改为必填"
                if missing
                else _dimension_misfit(
                    pinned,
                    {"version_id": str(version.id), "canonical_contract": dict(version.canonical_contract_json or {})},
                ) or str(exc)
            )
            misfits.setdefault(reason, []).append(str(adapter.name))
            continue
        db.add(
            CollectionImplementationVersionRecord(
                id=candidate.version_id,
                implementation_id=candidate.id,
                version=candidate.version,
                collector=candidate.collector,
                match_json=candidate.match,
                commands_json=[{"command": item.command, "priority": item.priority} for item in candidate.commands],
                parser_requirement=candidate.parser_requirement,
                template_scope=candidate.template_scope,
                adapter_spec_json=candidate.adapter_spec or None,
                output_quality_policy_json=candidate.output_quality_policy or None,
                selection_priority=candidate.selection_priority,
                status="active",
                digest=candidate.digest,
                created_by=actor,
                created_at=now,
                activated_at=now,
            )
        )
        adapter_version.status = "deprecated"
        adapter_version.deprecated_at = now
        adapter.current_version = candidate.version
        adapter.updated_at = now
        db.flush()
        for item_version in served.values():
            _link(db, str(item_version.id), candidate.version_id, like=link, now=now)
        moved.update(version_id=candidate.version_id, repinned=True, dropped_fields=dropped)
    if misfits:
        raise CheckItemReleaseBlocked([
            f"{len(names)} 个适配器与新版本的字段不匹配（{'、'.join(names)}）：{reason}"
            for reason, names in misfits.items()
        ])
    db.flush()
    return adapters


def _activate_rules(
    db: Any,
    version: CheckItemVersionRecord,
    earlier_version_ids: list[str],
    *,
    bundle_id: str | None,
    change_id: str,
    actor: str,
    reason: str,
    now: datetime,
) -> dict[str, int]:
    """Bring the new version's rules into force and retire what earlier versions left active."""

    from src.apps.rule_governance.backend.rule_control.approval import (
        RuleAssetApprovalService,
        RuleGenerationJobStateError,
    )

    activated = 0
    if bundle_id:
        bundle = (
            db.query(RuleGenerationJobRecord)
            .filter(RuleGenerationJobRecord.id == bundle_id)
            .with_for_update()
            .first()
        )
        if bundle is None or str(bundle.asset_ref) != f"check_item_version:{version.id}":
            raise CheckItemReleaseBlocked(["规则包不属于这个检查项版本，请重新生成"])
        if bundle.status == "pending_approval":
            try:
                approved = RuleAssetApprovalService()._approve_bundle(
                    db,
                    bundle,
                    actor=actor,
                    reason=reason,
                    now=now,
                    allow_same_actor=True,
                    released_with_check_item=change_id,
                )
            except RuleGenerationJobStateError as exc:
                raise CheckItemReleaseBlocked([f"规则无法生效：{exc}"]) from exc
            activated = len(approved["asset_activations"])
        elif bundle.status != "active":
            raise CheckItemReleaseBlocked([f"规则包状态为 {bundle.status}，不能随检查项发布"])
    retired = len(retire_rules(db, earlier_version_ids, now=now))
    db.flush()
    return {"activated": activated, "retired_without_successor": retired}


def _switch_bundles(
    db: Any,
    root: CheckItemRecord,
    version: CheckItemVersionRecord,
    previous: CheckItemVersionRecord | None,
    adapters: list[dict[str, Any]],
    *,
    session_scope: Any,
    actor: str,
    reason: str,
    now: datetime,
) -> list[str]:
    """Rebuild, for the new version, the runtime bundle of every adapter that was in force.

    An adapter brought in with the release gets one for every version in force it serves.
    """

    from src.apps.rule_governance.backend.check_items.runtime_bundle import (
        CheckItemRuntimeBundleService,
        RuntimeBundleValidationError,
    )

    in_force = {
        str(row.implementation_version_id)
        for row in db.query(CheckItemRuntimeBundleVersionRecord.implementation_version_id).filter(
            CheckItemRuntimeBundleVersionRecord.status == "active",
            CheckItemRuntimeBundleVersionRecord.check_item_version_id == str(previous.id),
        )
    } if previous is not None else set()
    service = CheckItemRuntimeBundleService(session_scope)
    blockers: list[str] = []
    switched: list[str] = []
    selections = []
    for adapter in adapters:
        if adapter.get("brought_in"):
            # An adapter that came with the release runs wherever it is linked to a version in force.
            adapter["in_force"] = True
            selections += [
                (adapter, str(row.check_item_version_id))
                for row in db.query(CheckItemImplementationLinkRecord)
                .join(
                    CheckItemVersionRecord,
                    CheckItemVersionRecord.id == CheckItemImplementationLinkRecord.check_item_version_id,
                )
                .filter(
                    CheckItemImplementationLinkRecord.implementation_version_id == adapter["version_id"],
                    CheckItemVersionRecord.status == "active",
                )
            ]
            continue
        adapter["in_force"] = adapter["previous_version_id"] in in_force
        if adapter["in_force"]:
            selections.append((adapter, str(version.id)))
    for adapter, check_item_version_id in selections:
        selection = {"check_item_version_id": check_item_version_id, "implementation_version_id": adapter["version_id"]}
        try:
            preview = service.preview_assembly(**selection)
            if preview.get("blockers"):
                raise RuntimeBundleValidationError(
                    [str(item.get("message") or item.get("code")) for item in preview["blockers"]]
                )
            draft = service.assemble(**selection, preview_digest=preview["preview_digest"], actor=actor)
            service.activate_in_session(
                db,
                draft["version_id"],
                actor=actor,
                reason=reason,
                now=now,
                allow_same_actor=True,
                audit_action="runtime_bundle_check_item_release",
            )
        except RuntimeBundleValidationError as exc:
            blockers.append(f"适配器“{adapter['name']}”的运行包无法切换：{'；'.join(exc.blockers)}")
            continue
        switched.append(str(draft["version_id"]))
    if blockers:
        raise CheckItemReleaseBlocked(blockers)
    return switched


def release_in_session(
    db: Any,
    *,
    session_scope: Any,
    change_id: str,
    root: CheckItemRecord,
    version: CheckItemVersionRecord,
    previous: CheckItemVersionRecord | None,
    bundle_id: str | None,
    actor: str,
    reason: str,
    now: datetime,
) -> dict[str, Any]:
    """Switch adapters, rules and runtime bundles to ``version``, which the caller has just activated.

    ``session_scope`` must yield ``db`` itself, so that every step joins the
    caller's transaction. Raises CheckItemReleaseBlocked with nothing kept.
    """

    db.flush()
    earlier = [
        str(row.id)
        for row in db.query(CheckItemVersionRecord.id).filter(
            CheckItemVersionRecord.check_item_id == root.id,
            CheckItemVersionRecord.id != version.id,
        )
    ]
    # Adapters a rule package brought with this version go into force with it.
    incoming = incoming_adapters(db, str(version.id))
    replaced = frozenset(str(item.implementation_id) for item in incoming)
    adapters = (
        _repin_adapters(db, root, version, previous, actor=actor, now=now, replaced=replaced)
        if previous is not None
        else []
    )
    brought, blockers = bring_in(db, incoming, actor=actor, now=now)
    if blockers:
        raise CheckItemReleaseBlocked(blockers)
    adapters += brought
    rules = _activate_rules(
        db, version, earlier, bundle_id=bundle_id, change_id=change_id, actor=actor, reason=reason, now=now
    )
    bundles: list[str] = []
    if (previous is not None or brought) and root.id not in system_check_item_ids():
        bundles = _switch_bundles(
            db, root, version, previous, adapters, session_scope=session_scope, actor=actor, reason=reason, now=now
        )
    # A rule the new version no longer has will never recover what it opened.
    events = close_orphaned_events(
        db,
        str(root.id),
        reason="rule_retired",
        note=f"规则随检查项 v{int(version.version)} 发布停用",
        actor=actor,
        now=now,
    )
    return {
        "kind": "check_item_release",
        "check_item_version_id": str(version.id),
        "previous_check_item_version_id": str(previous.id) if previous is not None else None,
        "rule_bundle_id": bundle_id,
        "rules_activated": rules["activated"],
        "rules_retired_without_successor": rules["retired_without_successor"],
        "adapters": adapters,
        "adapters_repinned": sum(1 for adapter in adapters if adapter["repinned"]),
        "adapters_brought_in": sum(1 for adapter in adapters if adapter.get("brought_in")),
        "runtime_bundle_version_ids": bundles,
        "events_closed": events,
    }


__all__ = ["CheckItemReleaseBlocked", "prepare_rules", "release_in_session"]
