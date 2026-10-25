"""Where a check item stands in the patrol.

A check item is what the engineer maintains. Change policies, event rules and
runtime bundles follow from it; this module reads them back as one status so the
catalog can show, per check item, what it judges, whether the rules are in force
and which adapters actually run it. Read-only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from src.apps.rule_governance.backend.check_items.canonical_rule_context import (
    build_canonical_rule_context,
    generate_canonical_rule_candidates,
)
from src.apps.rule_governance.backend.check_items.catalog import CheckItemCatalogRepository
from src.apps.rule_governance.backend.check_items.relations import published_relations
from src.apps.rule_governance.backend.check_items.rule_preview import rules_in_force
from src.apps.rule_governance.backend.check_items.system_items import system_check_item_ids
from src.core.check_items.command_adapter import direct_projection_manifest
from src.infrastructure.db.models import (
    ChangePolicyVersionRecord,
    CheckItemCatalogChangeRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _topology_only(check_item_id: str) -> bool:
    """System check items feed topology discovery; they never enter a patrol."""

    return check_item_id in system_check_item_ids()


_USES = {"health": "健康判断", "change": "变化检测", "reference": "阈值参考", "evidence": "仅展示"}


# A published version never changes, so what it asks for is compiled once per process.
_COMPILED: dict[tuple[str, str], dict[str, Any]] = {}


def _compile(item: Any) -> dict[str, Any]:
    try:
        context = build_canonical_rule_context(
            check_item_id=item.id,
            check_item_version_id=item.version_id,
            result_mode=item.result_mode,
            assessment_mode=item.assessment_mode,
            canonical_contract=item.canonical_contract,
        )
        candidates = generate_canonical_rule_candidates(context)
    except (ValueError, KeyError, TypeError) as exc:
        return {"error": str(exc), "policies": 0, "rules": 0, "uses": {}, "blockers": [str(exc)]}
    # A field's use is read from what it compiles to, not from its declared
    # roles: older definitions declare roles that produce no rule at all.
    tracked = {str(policy.get("observation_type")) for policy in candidates["change_policies"]}
    judged_now: set[str] = set()
    for rule in candidates["event_rules"]:
        types = {str(rule.get("observation_type") or ""), *map(str, dict(rule.get("condition") or {}).get("observation_types") or [])}
        tracked |= types
        if rule.get("event_class", "fault") == "fault" and str(rule.get("evaluation_scope") or "both") != "change":
            judged_now |= types
    referenced = {
        str(reference.get("field_name"))
        for target in context["targets"]
        for reference in dict(target.get("threshold_references") or {}).values()
    }
    uses = {}
    for target in context["targets"]:
        name, observation_type = target["canonical_field"], str(target["observation_type"])
        if target.get("health_policy") or observation_type in judged_now:
            uses[name] = "health"
        elif observation_type in tracked:
            uses[name] = "change"
        elif name in referenced or "reference" in (target.get("assessment_roles") or []):
            uses[name] = "reference"
        else:
            uses[name] = "evidence"
    return {
        "policies": len(candidates["change_policies"]),
        "rules": len(candidates["event_rules"]),
        "uses": uses,
        "blockers": [str(blocker.get("message") or "") for blocker in candidates["blockers"]],
    }


def _wanted(item: Any) -> dict[str, Any]:
    key = (str(item.version_id), str(item.digest))
    if key not in _COMPILED:
        if len(_COMPILED) > 2048:
            _COMPILED.clear()
        _COMPILED[key] = _compile(item)
    return _COMPILED[key]


def _summary(item: Any, adapters: list[Any], *, rules_active: int, ready: set[str],
             pending_change: Mapping[str, Any] | None, rules_awaiting_approval: bool) -> dict[str, Any]:
    wanted = _wanted(item)
    rules_wanted = wanted["policies"] + wanted["rules"]
    judged = sum(1 for use in wanted["uses"].values() if use in {"health", "change"})
    topology = _topology_only(item.id)
    adapters_ready = sum(1 for adapter in adapters if adapter.version_id in ready)
    if topology:
        state = "topology"
    elif not adapters:
        state = "no_adapter"
    elif rules_active < rules_wanted:
        state = "rules_missing"
    elif adapters_ready < len(adapters):
        state = "adapters_pending"
    elif not judged:
        state = "display_only"
    else:
        state = "ready"
    return {
        "state": state,
        "judged_fields": judged,
        "rules_wanted": rules_wanted,
        "rules_active": min(rules_active, rules_wanted),
        "rules_awaiting_approval": rules_awaiting_approval,
        "adapters": len(adapters),
        "adapters_ready": len(adapters) if topology else adapters_ready,
        "pending_change": dict(pending_change) if pending_change else None,
        "blockers": wanted["blockers"],
    }


def _load(db: Any, items: list[Any]) -> dict[str, Any]:
    version_ids = [item.version_id for item in items]
    policies = Counter(
        str(row.check_item_version_id)
        for row in db.query(ChangePolicyVersionRecord.check_item_version_id).filter(
            ChangePolicyVersionRecord.status == "active",
            ChangePolicyVersionRecord.check_item_version_id.in_(version_ids),
        )
    )
    rules = Counter(
        str(row.check_item_version_id)
        for row in db.query(EventRuleVersionRecord.check_item_version_id)
        .join(EventRuleRecord, EventRuleRecord.id == EventRuleVersionRecord.rule_id)
        .filter(
            EventRuleRecord.status == "active",
            EventRuleVersionRecord.status == "active",
            EventRuleVersionRecord.check_item_version_id.in_(version_ids),
        )
    )
    ready: dict[str, set[str]] = {}
    for row in db.query(
        CheckItemRuntimeBundleVersionRecord.check_item_version_id,
        CheckItemRuntimeBundleVersionRecord.implementation_version_id,
    ).filter(
        CheckItemRuntimeBundleVersionRecord.status == "active",
        CheckItemRuntimeBundleVersionRecord.check_item_version_id.in_(version_ids),
    ):
        ready.setdefault(str(row.check_item_version_id), set()).add(str(row.implementation_version_id))
    changes = {}
    for row in db.query(CheckItemCatalogChangeRecord).filter(
        CheckItemCatalogChangeRecord.asset_kind == "check_item",
        CheckItemCatalogChangeRecord.asset_id.in_([item.id for item in items]),
        CheckItemCatalogChangeRecord.status.in_(["draft", "pending_approval"]),
    ).order_by(CheckItemCatalogChangeRecord.updated_at):
        changes[str(row.asset_id)] = {"id": str(row.id), "status": str(row.status)}
    awaiting = {
        str(row.asset_ref).removeprefix("check_item_version:")
        for row in db.query(RuleGenerationJobRecord.asset_ref).filter(
            RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE",
            RuleGenerationJobRecord.status == "pending_approval",
            RuleGenerationJobRecord.asset_ref.in_([f"check_item_version:{value}" for value in version_ids]),
        )
    }
    return {"policies": policies, "rules": rules, "ready": ready, "changes": changes, "awaiting": awaiting}


def patrol_status(check_item_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """One status per check item, for the rows of the catalog."""

    catalog = CheckItemCatalogRepository().load_active()
    wanted_ids = set(check_item_ids)
    items = [item for item in catalog.check_items if item.id in wanted_ids]
    if not items:
        return {}
    with get_db_session() as db:
        loaded = _load(db, items)
    return {
        item.id: _summary(
            item,
            [adapter for adapter in catalog.implementations if item.id in adapter.check_item_ids],
            rules_active=loaded["policies"][item.version_id] + loaded["rules"][item.version_id],
            ready=loaded["ready"].get(item.version_id, set()),
            pending_change=loaded["changes"].get(item.id),
            rules_awaiting_approval=item.version_id in loaded["awaiting"],
        )
        for item in items
    }


def _scope(match: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("vendors", "vendor", "families", "platforms", "model_patterns"):
        value = match.get(key)
        for entry in value if isinstance(value, list) else [value] if value else []:
            if str(entry) not in parts:
                parts.append(str(entry))
    return " / ".join(parts)


def check_item_overview(check_item_id: str) -> dict[str, Any]:
    """Fields, the rules they produce and the adapters that provide them, in one read."""

    catalog = CheckItemCatalogRepository().load_active()
    item = catalog.check_item(check_item_id)
    if item is None:
        raise KeyError(f"active check item not found: {check_item_id}")
    adapters = [adapter for adapter in catalog.implementations if item.id in adapter.check_item_ids]
    topology = _topology_only(item.id)
    with get_db_session() as db:
        loaded = _load(db, [item])
        versions = {
            str(row.id): int(row.version)
            for row in db.query(CheckItemVersionRecord).filter(CheckItemVersionRecord.check_item_id == item.id)
        }
        # Adapters still served by a runtime bundle of an earlier check item version.
        earlier: dict[str, int] = {}
        for bundle, implementation in db.query(
            CheckItemRuntimeBundleVersionRecord, CollectionImplementationVersionRecord
        ).join(
            CollectionImplementationVersionRecord,
            CollectionImplementationVersionRecord.id == CheckItemRuntimeBundleVersionRecord.implementation_version_id,
        ).filter(
            CheckItemRuntimeBundleVersionRecord.status == "active",
            CheckItemRuntimeBundleVersionRecord.check_item_version_id.in_(
                [version_id for version_id in versions if version_id != item.version_id]
            ),
        ):
            number = versions[str(bundle.check_item_version_id)]
            key = str(implementation.implementation_id)
            earlier[key] = max(earlier.get(key, 0), number)
        graph = published_relations(db, item, patrolled=not topology)
        # The version that was in force before this one; a rollback publishes its content again.
        released_before = [
            int(row.version)
            for row in db.query(CheckItemVersionRecord).filter(
                CheckItemVersionRecord.check_item_id == item.id,
                CheckItemVersionRecord.version < item.version,
                CheckItemVersionRecord.activated_at.isnot(None),
            )
        ]
    ready = loaded["ready"].get(item.version_id, set())
    wanted = _wanted(item)
    in_force = rules_in_force(item.id)
    contract = {"version_id": item.version_id, "canonical_contract": item.canonical_contract}
    rows = {row.get("canonical_field"): row for row in item.canonical_contract.get("observations") or []}

    from src.apps.rule_governance.backend.check_items.adapter_field_service import recorded

    # What trials found: a field the device does not print is not a gap someone still has to close.
    trials = recorded(item.id) if not topology else {}
    adapter_rows = []
    provided_by: Counter[str] = Counter()
    absent_by: Counter[str] = Counter()
    structured = 0
    for adapter in adapters:
        produced: list[str] | None = None
        if (adapter.adapter_spec or {}).get("outputs"):
            try:
                manifest = direct_projection_manifest(
                    adapter.adapter_spec,
                    check_item_id=item.id,
                    check_item=contract,
                    require_pinned_check_item_version=False,
                )
                produced = sorted({field for output in manifest["outputs"] for field in output["target_fields"]})
            except (ValueError, KeyError, TypeError):
                produced = None
        missing = None if produced is None else [field for field in rows if field not in produced]
        trial = trials.get(adapter.id, {})
        absent = [field for field in trial.get("absent_fields", []) if field in (missing or [])]
        if produced is not None:
            structured += 1
            provided_by.update(field for field in produced if field in rows)
            absent_by.update(absent)
        if topology:
            state = "not_required"
        elif adapter.version_id in ready:
            state = "ready"
        elif adapter.id in earlier:
            state = "previous_version"
        else:
            state = "pending"
        adapter_rows.append(
            {
                "implementation_id": adapter.id,
                "implementation_version_id": adapter.version_id,
                "name": adapter.name,
                "scope": _scope(adapter.match or {}),
                "command": (adapter.commands[0].command if adapter.commands else ""),
                "state": state,
                "running_check_item_version": earlier.get(adapter.id) if state == "previous_version" else None,
                "fields_provided": None if produced is None else len([field for field in produced if field in rows]),
                "missing_fields": missing,
                # Of the missing ones: those a trial on this adapter's own outputs did not find.
                "absent_fields": absent,
                "field_trial": str(trial.get("trial_status") or "idle"),
            }
        )

    fields = []
    for entry in in_force["fields"]:
        name = entry["canonical_field"]
        use = wanted["uses"].get(name, "evidence")
        fields.append(
            {
                "canonical_field": name,
                "description": str(
                    rows.get(name, {}).get("description")
                    or dict(rows.get(name, {}).get("semantic_definition") or {}).get("description")
                    or ""
                ),
                "use": use,
                "use_label": _USES[use],
                "lines": entry["lines"],
                # A judged Value with nothing in force is a gap, not a quiet field.
                "rules_missing": use in {"health", "change"} and not entry["lines"],
                "stale_count": entry["stale_count"],
                "provided_by": provided_by[name] if structured else None,
                "absent_by": absent_by[name] if structured else None,
            }
        )
    return {
        "check_item_id": item.id,
        "name": item.name,
        "version": item.version,
        "check_item_version_id": item.version_id,
        "result_mode": item.result_mode,
        "status": _summary(
            item,
            adapters,
            rules_active=loaded["policies"][item.version_id] + loaded["rules"][item.version_id],
            ready=ready,
            pending_change=loaded["changes"].get(item.id),
            rules_awaiting_approval=item.version_id in loaded["awaiting"],
        ),
        "fields": fields,
        "structured_adapters": structured,
        "adapters": adapter_rows,
        "leftover_count": in_force["leftover_count"],
        "graph": graph,
        "rollback_version": max(released_before) if released_before and not topology else None,
    }


__all__ = ["check_item_overview", "patrol_status"]
