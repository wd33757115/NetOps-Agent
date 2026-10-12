"""A check item drawn as what follows from it.

A field leads to the judgment it gets, and a judgment leads to the events and
results the patrol reports. The same rows describe the published version, a
candidate version, and the difference between the two, so the engineer reads
one picture when looking, when changing and when deleting.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from src.apps.rule_governance.backend.check_items.canonical_rule_context import (
    build_canonical_rule_context,
    generate_canonical_rule_candidates,
)
from src.apps.rule_governance.backend.check_items.retirement import (
    event_counts,
    event_types_in_force,
    owned_event_types,
)
from src.apps.rule_governance.backend.check_items.rule_preview import (
    _CLASS_ORDER,
    _COMPARE,
    _health_lines,
    _policy_line,
    _rule_lines,
)
from src.infrastructure.db.models import (
    ChangePolicyVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    NetworkEventRecord,
)


def compile_relations(
    *,
    check_item_id: str,
    result_mode: str,
    assessment_mode: str,
    canonical_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Rows of field -> judgment -> outcomes for one contract, as it compiles; nothing is read from storage."""

    declared = [str(row.get("canonical_field")) for row in canonical_contract.get("observations") or []]
    empty = {"rows": [], "reference": [], "evidence": declared, "fields": declared, "errors": []}
    try:
        context = build_canonical_rule_context(
            check_item_id=check_item_id,
            check_item_version_id="relations",
            result_mode=result_mode,
            assessment_mode=assessment_mode,
            canonical_contract=canonical_contract,
        )
        candidates = generate_canonical_rule_candidates(context)
    except (ValueError, KeyError, TypeError) as exc:
        return {**empty, "errors": [str(exc)]}

    policies = {str(policy.get("observation_type")): policy for policy in candidates["change_policies"]}
    rules: dict[str, list[Mapping[str, Any]]] = {}
    for rule in candidates["event_rules"]:
        rules.setdefault(str(rule.get("observation_type")), []).append(rule)
    described = {
        str(row.get("canonical_field")): str(
            row.get("description") or dict(row.get("semantic_definition") or {}).get("description") or ""
        )
        for row in canonical_contract.get("observations") or []
    }
    referenced: set[str] = set()
    for target in context["targets"]:
        for reference in dict(target.get("threshold_references") or {}).values():
            referenced.add(str(reference.get("field_name")))

    rows: list[dict[str, Any]] = []
    reference: list[str] = []
    evidence: list[str] = []
    for target in context["targets"]:
        name, observation_type = str(target["canonical_field"]), str(target["observation_type"])
        health = target.get("health_policy")
        policy = policies.get(observation_type)
        field_rules = sorted(
            rules.get(observation_type, []),
            key=lambda rule: (_CLASS_ORDER.get(str(rule.get("event_class")), 3), -int(rule.get("priority") or 100)),
        )
        if not (health or policy or field_rules):
            (reference if name in referenced or "reference" in (target.get("assessment_roles") or []) else evidence).append(name)
            continue
        outcomes: list[dict[str, Any]] = []
        if health:
            lines = _health_lines(health)
            rule_node = {"kind": "health", "title": "健康判断", "detail": "按设备阈值", "lines": lines}
            outcomes.append({"key": "abnormal", "kind": "abnormal", "text": "超出阈值时计入当前异常"})
            if dict(health.get("trend") or {}).get("kind"):
                outcomes.append({"key": "trend", "kind": "trend", "text": "趋势提醒，不计入异常"})
        elif policy:
            mode = str(policy.get("compare_mode") or "")
            rule_node = {
                "kind": "change",
                "title": "变化规则",
                "detail": _COMPARE.get(mode, mode or "未指定"),
                "lines": [_policy_line(policy)],
            }
        else:
            rule_node = {"kind": "state", "title": "状态判断", "detail": "按当前值", "lines": []}
        gated = dict(target.get("event_constraints") or {}).get("allow_initial_transition_fault") is False
        for rule, sentence in zip(field_rules, _rule_lines(field_rules, gated=gated)):
            event_class = str(rule.get("event_class") or "fault")
            outcomes.append(
                {
                    "key": f"event:{rule.get('event_type')}",
                    "kind": "event",
                    "event_type": str(rule.get("event_type")),
                    "event_class": event_class,
                    "severity": str(rule.get("severity") or ""),
                    # A gated transition is only abnormal now when an expected state backs it; the sentence says so.
                    "judges_current": event_class == "fault"
                    and not gated
                    and str(rule.get("evaluation_scope") or "both") != "change",
                    "resolves": [str(item) for item in dict(rule.get("recovery") or {}).get("resolves") or []],
                    "text": sentence,
                }
            )
        if not outcomes:
            outcomes.append({"key": "counted", "kind": "counted", "text": "计入变化统计，不产生事件"})
        rows.append(
            {
                "field": {"name": name, "description": described.get(name, ""), "observation_type": observation_type},
                "rule": rule_node,
                "outcomes": outcomes,
            }
        )
    return {
        "rows": rows,
        "reference": reference,
        "evidence": evidence,
        "fields": [str(target["canonical_field"]) for target in context["targets"]],
        "errors": [str(blocker.get("message") or "") for blocker in candidates["blockers"]],
    }


# A published version never changes, so its rows are compiled once per process.
_COMPILED: dict[tuple[str, str], dict[str, Any]] = {}


def _compiled(item: Any) -> dict[str, Any]:
    key = (str(item.version_id), str(item.digest))
    if key not in _COMPILED:
        if len(_COMPILED) > 2048:
            _COMPILED.clear()
        _COMPILED[key] = compile_relations(
            check_item_id=item.id,
            result_mode=item.result_mode,
            assessment_mode=item.assessment_mode,
            canonical_contract=item.canonical_contract,
        )
    return deepcopy(_COMPILED[key])


def _count(graph: dict[str, Any], counts: Mapping[str, Mapping[str, int]]) -> None:
    for row in graph["rows"]:
        for outcome in row["outcomes"]:
            if outcome["kind"] == "event":
                outcome.update({"open": 0, "total": 0, **counts.get(outcome["event_type"], {})})


def published_relations(db: Any, item: Any, *, patrolled: bool = True) -> dict[str, Any]:
    """The published version's rows, with what is in force and how many events each rule has on record."""

    graph = _compiled(item)
    active_policies = {
        str(row.observation_type)
        for row in db.query(ChangePolicyVersionRecord.observation_type).filter(
            ChangePolicyVersionRecord.status == "active",
            ChangePolicyVersionRecord.check_item_version_id == item.version_id,
        )
    }
    active_events = {
        str(row.event_type)
        for row in db.query(EventRuleVersionRecord.event_type)
        .join(EventRuleRecord, EventRuleRecord.id == EventRuleVersionRecord.rule_id)
        .filter(
            EventRuleRecord.status == "active",
            EventRuleVersionRecord.status == "active",
            EventRuleVersionRecord.check_item_version_id == item.version_id,
        )
    }
    owned = owned_event_types(db, item.id)
    wanted = {
        outcome["event_type"]
        for row in graph["rows"]
        for outcome in row["outcomes"]
        if outcome["kind"] == "event"
    }
    counts = event_counts(db, wanted | set(owned))
    _count(graph, counts)
    for row in graph["rows"]:
        for outcome in row["outcomes"]:
            if outcome["kind"] == "event":
                outcome["state"] = "active" if outcome["event_type"] in active_events else "missing"
        events = [outcome for outcome in row["outcomes"] if outcome["kind"] == "event"]
        if row["rule"]["kind"] == "change":
            in_force = row["field"]["observation_type"] in active_policies
        else:
            in_force = all(outcome["state"] == "active" for outcome in events)
        row["rule"]["state"] = "active" if in_force else "missing"
    in_force_anywhere = event_types_in_force(db)
    graph["mode"] = "current"
    graph["patrolled"] = patrolled
    # Events on record under a rule that no longer runs: nothing will ever recover them.
    graph["retired"] = [
        {**owned[event_type], **counts[event_type]}
        for event_type in sorted(set(owned) - wanted - in_force_anywhere)
        if counts.get(event_type, {}).get("open")
    ]
    return graph


def _signature(node: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(node.get(key) if not isinstance(node.get(key), list) else tuple(node[key]) for key in keys)


def diff_relations(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> dict[str, Any]:
    """Both sets of rows as one, each field, judgment and outcome marked same, added, removed or changed.

    Without a version before, every row is new; the fields that are only shown are not listed as additions.
    """

    before = before or {"rows": [], "reference": [], "evidence": [], "fields": []}
    first = not before.get("fields")
    earlier = {row["field"]["name"]: row for row in before["rows"]}
    had, has = set(before.get("fields") or []), set(after.get("fields") or [])
    rows: list[dict[str, Any]] = []

    def mark(row: Mapping[str, Any], change: str) -> dict[str, Any]:
        marked = deepcopy(dict(row))
        marked["rule"]["change"] = change
        for outcome in marked["outcomes"]:
            outcome["change"] = change
        return marked

    for row in after["rows"]:
        name = row["field"]["name"]
        old = earlier.pop(name, None)
        if old is None:
            merged = mark(row, "added")
        else:
            merged = deepcopy(dict(row))
            same_rule = _signature(old["rule"], ("kind", "detail", "lines")) == _signature(row["rule"], ("kind", "detail", "lines"))
            merged["rule"]["change"] = "same" if same_rule else "changed"
            if not same_rule:
                merged["rule"]["before"] = {"title": old["rule"]["title"], "detail": old["rule"]["detail"]}
            old_outcomes = {outcome["key"]: outcome for outcome in old["outcomes"]}
            for outcome in merged["outcomes"]:
                previous = old_outcomes.pop(outcome["key"], None)
                if previous is None:
                    outcome["change"] = "added"
                elif _signature(previous, ("text", "severity", "event_class")) != _signature(outcome, ("text", "severity", "event_class")):
                    outcome["change"] = "changed"
                    outcome["before_text"] = previous["text"]
                else:
                    outcome["change"] = "same"
            merged["outcomes"].extend({**deepcopy(outcome), "change": "removed"} for outcome in old_outcomes.values())
        merged["field"]["change"] = "same" if name in had else "added"
        rows.append(merged)
    for name, old in earlier.items():
        merged = mark(old, "removed")
        # The field may stay on as a displayed value; only its judgment goes.
        merged["field"]["change"] = "same" if name in has else "removed"
        rows.append(merged)

    def group(kind: str) -> dict[str, list[str]]:
        now, then = list(after.get(kind) or []), list(before.get(kind) or [])
        return {
            "fields": now,
            "added": [] if first else [name for name in now if name not in had],
            "removed": [name for name in then if name not in has],
        }

    return {
        "mode": "diff",
        "rows": rows,
        "reference": group("reference"),
        "evidence": group("evidence"),
        "fields": list(after.get("fields") or []),
        "errors": list(after.get("errors") or []),
    }


def count_relations(db: Any, graph: dict[str, Any]) -> dict[str, Any]:
    """Put the number of stored events on every event of a compared graph."""

    _count(
        graph,
        event_counts(
            db,
            {
                outcome["event_type"]
                for row in graph["rows"]
                for outcome in row["outcomes"]
                if outcome["kind"] == "event"
            },
        ),
    )
    return graph


def recent_events(db: Any, check_item_id: str, event_type: str, *, limit: int = 10) -> dict[str, Any]:
    """The latest stored events of one type, for the engineer who clicks its count."""

    if event_type not in owned_event_types(db, check_item_id):
        raise KeyError(f"event type does not belong to this check item: {event_type}")
    query = db.query(NetworkEventRecord).filter(NetworkEventRecord.event_type == event_type)
    rows = query.order_by(NetworkEventRecord.last_seen_at.desc()).limit(max(1, min(int(limit), 50))).all()
    items = []
    for row in rows:
        payload = dict(row.payload_json or {})
        change = dict(payload.get("latest_change") or payload.get("change") or {})
        items.append(
            {
                "id": str(row.id),
                "device_id": str(row.device_id or ""),
                "entity_key": str(row.entity_key or ""),
                "status": str(row.status),
                "severity": str(row.severity),
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "occurrence_count": int(row.occurrence_count or 1),
                "old": change.get("old"),
                "new": change.get("new"),
                "closed_note": payload.get("closed_note"),
            }
        )
    return {"event_type": event_type, "total": int(query.count()), "items": items}


__all__ = [
    "compile_relations",
    "count_relations",
    "diff_relations",
    "published_relations",
    "recent_events",
]
