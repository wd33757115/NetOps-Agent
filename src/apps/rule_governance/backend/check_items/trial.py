"""Judge the last patrol again under another version of a check item.

A patrol stores every value it collected together with the semantics of the
check item version that collected it. A trial keeps the values, labels them the
way the other version would have, and runs the patrol's own evaluation over
both labellings: what is abnormal now, and, when the patrol was compared with a
baseline, which changes it finds and which events its rules make of them.
Nothing is collected and nothing is written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from src.apps.rule_governance.backend.check_items.canonical_rule_context import (
    build_canonical_rule_context,
    generate_canonical_rule_candidates,
)
from src.apps.rule_governance.backend.check_items.relations import compile_relations
from src.core.check_items.adapter_projection import _field_context
from src.core.check_items.current_state import _attach_baselines, _normalized_observation
from src.core.patrol.event_rules import EventRuleRepository, _effective_condition
from src.infrastructure.db.models import (
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckItemVersionRecord,
    PatrolCollectionResult,
    PatrolRun,
    StateObservationRecord,
)

LIST_LIMIT = 200
_HEALTH_EVENT = "NumericHealthAbnormal"


def _types(*contracts: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("observation_type"))
        for contract in contracts
        for row in contract.get("observations") or []
        if row.get("observation_type")
    }


def _last_run(db: Any, types: set[str]) -> tuple[PatrolRun | None, PatrolRun | None]:
    """The patrol that last collected these values, and the baseline it was compared with."""

    run_ids = [
        str(row.run_id)
        for row in db.query(StateObservationRecord.run_id)
        .filter(StateObservationRecord.observation_type.in_(sorted(types)))
        .filter(StateObservationRecord.mode == "production")
        .distinct()
    ]
    if not run_ids:
        return None, None
    runs = [
        run
        for run in db.query(PatrolRun).filter(PatrolRun.id.in_(run_ids))
        # A baseline is the older half of a comparison, not a patrol of its own.
        if dict(run.metadata_json or {}).get("period_role") != "baseline"
    ]
    if not runs:
        return None, None
    last = max(runs, key=lambda run: (run.completed_at is not None, run.completed_at or run.started_at))
    job = dict(last.metadata_json or {}).get("offline_job_id")
    baseline = (
        db.query(PatrolRun)
        .filter(PatrolRun.dataset_id == f"{job}-baseline")
        .order_by(PatrolRun.completed_at.desc())
        .first()
        if job
        else None
    )
    return last, baseline


def _observations(db: Any, run: PatrolRun | None, types: set[str], check_item_id: str) -> list[dict[str, Any]]:
    if run is None:
        return []
    items = [
        _normalized_observation(row.to_dict())
        for row in db.query(StateObservationRecord).filter(
            StateObservationRecord.run_id == str(run.id),
            StateObservationRecord.observation_type.in_(sorted(types)),
        )
    ]
    return [item for item in items if str(item["metadata"].get("check_item_id") or check_item_id) == check_item_id]


def _as_version(observations: list[dict[str, Any]], version: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The same values, labelled as ``version`` would have labelled them when collecting.

    A field the version does not have is left out. Limits a field is judged
    against are read again from the other values of the same object, because
    the version may name different limit fields than the one that collected.
    """

    rows = {
        str(row.get("observation_type")): row
        for row in version["canonical_contract"].get("observations") or []
    }
    records: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in observations:
        key = (str(item.get("device_id")), str(item.get("entity_type")), str(item.get("entity_key")))
        records.setdefault(key, {})[str(item.get("field"))] = item.get("value")
    labelled = []
    for item in observations:
        row = rows.get(str(item.get("observation_type")))
        if row is None:
            continue
        key = (str(item.get("device_id")), str(item.get("entity_type")), str(item.get("entity_key")))
        metadata = {**item["metadata"], "semantics": row, "check_item_version_id": version["version_id"]}
        stored = item.get("context") if isinstance(item.get("context"), dict) else {}
        context = {**stored, **(_field_context(row, records[key]) or {})}
        labelled.append({**item, "metadata": metadata, "metadata_json": metadata, "context": context or None})
    return labelled


def _compiled(check_item_id: str, version: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The version's change policies and event rules, in the shape the patrol reads those in force."""

    context = build_canonical_rule_context(
        check_item_id=check_item_id,
        check_item_version_id=version["version_id"],
        result_mode=version["result_mode"],
        assessment_mode=version["assessment_mode"],
        canonical_contract=version["canonical_contract"],
    )
    candidates = generate_canonical_rule_candidates(context)
    policies = [
        {
            "policy_id": f"trial:{policy['observation_type']}",
            "version_id": f"trial:{policy['observation_type']}",
            "observation_type": str(policy["observation_type"]),
            "policy": dict(policy),
        }
        for policy in candidates["change_policies"]
    ]
    rules = []
    for rule in candidates["event_rules"]:
        name = f"{check_item_id}.{rule.get('name') or rule['event_type']}"
        condition = dict(rule.get("condition") or {})
        rules.append(
            {
                "rule_id": name,
                "rule_name": name,
                "version_id": f"trial:{name}",
                "version": 0,
                "digest": "trial",
                "event_type": rule["event_type"],
                "severity": rule.get("severity"),
                "condition": condition,
                "effective_condition": _effective_condition(name, condition),
                "priority": rule.get("priority", 100),
                "exclusive": bool(rule.get("exclusive")),
                "event_class": rule.get("event_class", "fault"),
                "evaluation_scope": rule.get("evaluation_scope", "both"),
                "recovery": dict(rule.get("recovery") or {}),
                "check_item_version_id": version["version_id"],
            }
        )
    return policies, rules


def _policies_in_force(db: Any, check_item_version_id: str) -> list[dict[str, Any]]:
    return [
        {
            "policy_id": str(policy.id),
            "version_id": str(version.id),
            "observation_type": str(version.observation_type),
            "policy": dict(version.policy_json or {}),
        }
        for policy, version in db.query(ChangePolicyRecord, ChangePolicyVersionRecord)
        .join(ChangePolicyVersionRecord, ChangePolicyVersionRecord.policy_id == ChangePolicyRecord.id)
        .filter(
            ChangePolicyVersionRecord.status == "active",
            ChangePolicyVersionRecord.check_item_version_id == check_item_version_id,
        )
    ]


def _compare(
    run: Mapping[str, Any],
    baseline_id: str,
    collected: list[dict[str, Any]],
    types: set[str],
    observations: list[dict[str, Any]],
    baselines: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """The patrol's own comparison of two runs, and the events its rules make of the differences.

    ``run`` and ``collected`` are the patrol run and what it recorded about each
    command it collected; handing them over keeps the comparison from looking
    them up, and from persisting anything, on its own.
    """

    from src.core.patrol.change_detector import _detect_observation_changes_against_history_impl
    from src.core.patrol.event_builder import build_events_from_changes
    from src.core.patrol.event_rules import event_rules_from_snapshot

    detected = _detect_observation_changes_against_history_impl(
        current_run_id=str(run["id"]),
        previous_run_id=baseline_id,
        persist=False,
        policy_bundle=policies,
        current_observations=observations,
        prior_observations=baselines,
        analysis_revision="check-item-trial",
        run_context=dict(run),
        collection_results=collected,
    )
    # The comparison also diffs the raw text of every command of the run that failed to parse.
    # Those differences belong to no field of this check item and are the same under any version of it.
    changes = [
        dict(change)
        for change in detected.get("changes") or []
        if str(change.get("observation_type") or "") in types
    ]
    produced: dict[str, list[dict[str, Any]]] = {}
    for event in build_events_from_changes(changes, rules=event_rules_from_snapshot(rules)):
        produced.setdefault(str(event.source_change_id), []).append(event.to_dict())
    # One outcome per change and event it produces; a change no rule picks up is still a change.
    outcomes: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for change in changes:
        for event in produced.get(str(change.get("change_id"))) or [None]:
            event_type = str(event["event_type"]) if event else ""
            key = (
                str(change.get("device_id")),
                str(change.get("entity_type")),
                str(change.get("entity_key")),
                str(change.get("field")),
                event_type,
            )
            outcomes[key] = {
                "device_id": str(change.get("device_id") or ""),
                "entity_key": str(change.get("entity_key") or ""),
                "field": str(change.get("field") or ""),
                "old": change.get("old"),
                "new": change.get("new"),
                "change_type": str(change.get("change_type") or ""),
                "event_type": event_type,
                "event_class": str(dict(dict(event["payload"]).get("rule") or {}).get("event_class") or "fault")
                if event
                else "",
                "severity": str(event["severity"]) if event else "",
            }
    classes = Counter(item["event_class"] for item in outcomes.values() if item["event_type"])
    return {
        "outcomes": outcomes,
        "changes": len(changes),
        "events": {name: classes[name] for name in ("fault", "recovery", "change")},
        "by_rule": Counter((item["field"], item["event_type"]) for item in outcomes.values() if item["event_type"]),
    }


def _judge(observations: list[dict[str, Any]], baselines: list[dict[str, Any]], rules: list[dict[str, Any]]) -> dict[str, Any]:
    # The patrol's own evaluation, reached the way the core reaches it.
    from src.core.workflows.patrol_batch_service import evaluate_current_state

    result = evaluate_current_state(_attach_baselines(observations, baselines), rules, rule_source="check_item_trial")
    abnormal: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for finding in result["findings"]:
        key = (
            str(finding.get("device_id")),
            str(finding.get("entity_type")),
            str(finding.get("entity_key")),
            str(finding.get("field")),
        )
        abnormal.setdefault(key, finding)
    return {
        "abnormal": abnormal,
        "by_rule": Counter((str(f.get("field")), str(f.get("event_type"))) for f in abnormal.values()),
        "undetermined": sum(
            int(result.get(name) or 0)
            for name in (
                "threshold_unknown_observation_count",
                "expected_state_unknown_observation_count",
                "assessment_policy_missing_observation_count",
                "assessment_policy_conflict_observation_count",
            )
        ),
    }


def _sentences(check_item_id: str, version: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    graph = compile_relations(
        check_item_id=check_item_id,
        result_mode=version["result_mode"],
        assessment_mode=version["assessment_mode"],
        canonical_contract=version["canonical_contract"],
    )
    sentences: dict[tuple[str, str], str] = {}
    for row in graph["rows"]:
        name = row["field"]["name"]
        if row["rule"]["kind"] == "health":
            sentences[(name, _HEALTH_EVENT)] = "超出设备阈值时判定异常。"
        for outcome in row["outcomes"]:
            if outcome["kind"] == "event":
                sentences[(name, outcome["event_type"])] = outcome["text"]
    return sentences


def _item(finding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "device_id": str(finding.get("device_id") or ""),
        "entity_key": str(finding.get("entity_key") or ""),
        "field": str(finding.get("field") or ""),
        "value": finding.get("value"),
        "severity": str(finding.get("severity") or ""),
        "event_type": str(finding.get("event_type") or ""),
    }


def trial_on_last_patrol(
    db: Any,
    *,
    check_item_id: str,
    current: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """What the last patrol reports as abnormal now, and what it would report under ``candidate``.

    Each version is a mapping with ``version_id``, ``result_mode``,
    ``assessment_mode`` and ``canonical_contract``.
    """

    if current is None:
        return {"available": False, "reason": "new_check_item"}
    if candidate["result_mode"] != "structured" or current["result_mode"] != "structured":
        return {"available": False, "reason": "not_structured"}
    types = _types(current["canonical_contract"], candidate["canonical_contract"])
    run, baseline_run = _last_run(db, types)
    observations = _observations(db, run, types, check_item_id)
    if run is None or not observations:
        return {"available": False, "reason": "no_run"}
    baselines = _observations(db, baseline_run, types, check_item_id)
    own_versions = {
        str(row.id)
        for row in db.query(CheckItemVersionRecord.id).filter(CheckItemVersionRecord.check_item_id == check_item_id)
    }
    in_force = EventRuleRepository().snapshot_active_rules()
    others = [rule for rule in in_force if str(rule.get("check_item_version_id")) not in own_versions]

    candidate_policies, candidate_rules = _compiled(check_item_id, candidate)
    labelled = {
        "now": (_as_version(observations, current), _as_version(baselines, current)),
        "after": (_as_version(observations, candidate), _as_version(baselines, candidate)),
    }
    now = _judge(*labelled["now"], in_force)
    after = _judge(*labelled["after"], [*others, *candidate_rules])

    before_text, after_text = _sentences(check_item_id, current), _sentences(check_item_id, candidate)
    rules = [
        {
            "field": field,
            "event_type": event_type,
            "text": after_text.get((field, event_type)) or before_text.get((field, event_type)) or event_type,
            "change": "added"
            if (field, event_type) not in before_text and (field, event_type) in after_text
            else "removed"
            if (field, event_type) in before_text and (field, event_type) not in after_text
            else "same",
            "now": now["by_rule"][(field, event_type)],
            "after": after["by_rule"][(field, event_type)],
        }
        for field, event_type in sorted(set(now["by_rule"]) | set(after["by_rule"]))
    ]
    rules.sort(key=lambda row: -abs(row["after"] - row["now"]))
    added = sorted(set(after["abnormal"]) - set(now["abnormal"]))
    removed = sorted(set(now["abnormal"]) - set(after["abnormal"]))
    seen = {str(item.get("field")) for item in observations}
    stamps = [item["observed_at"] for item in observations if item.get("observed_at")]
    latest = max(stamps) if stamps else None
    judged = {field for field, _event in after_text}
    return {
        "available": True,
        "run": {
            "run_id": str(run.id),
            "observed_at": latest.isoformat() if hasattr(latest, "isoformat") else latest,
            "devices": len({str(item.get("device_id")) for item in observations}),
            "values": len(observations),
        },
        "now": {"abnormal": len(now["abnormal"]), "undetermined": now["undetermined"]},
        "after": {"abnormal": len(after["abnormal"]), "undetermined": after["undetermined"]},
        "rules": rules,
        "added_count": len(added),
        "added": [_item(after["abnormal"][key]) for key in added[:LIST_LIMIT]],
        "removed_count": len(removed),
        "removed": [_item(now["abnormal"][key]) for key in removed[:LIST_LIMIT]],
        # A judged field the last patrol never collected cannot be tried.
        "no_data_fields": sorted(judged - seen),
        "comparison": _comparison(
            db,
            run=run,
            baseline_run=baseline_run if baselines else None,
            labelled=labelled,
            current=current,
            types=types,
            rules={"now": in_force, "after": [*others, *candidate_rules]},
            candidate_policies=candidate_policies,
            sentences=(before_text, after_text),
        ),
    }


def _comparison(
    db: Any,
    *,
    run: PatrolRun,
    baseline_run: PatrolRun | None,
    labelled: Mapping[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    current: Mapping[str, Any],
    types: set[str],
    rules: Mapping[str, list[dict[str, Any]]],
    candidate_policies: list[dict[str, Any]],
    sentences: tuple[Mapping[tuple[str, str], str], Mapping[tuple[str, str], str]],
) -> dict[str, Any]:
    """Changes against the baseline and the events they produce, now and under the candidate."""

    if baseline_run is None:
        # Without the run it was compared with, there is nothing to find changes against.
        return {"available": False, "reason": "no_baseline"}
    policies = {"now": _policies_in_force(db, current["version_id"]), "after": candidate_policies}
    collected = [
        row.to_dict()
        for row in db.query(PatrolCollectionResult).filter(PatrolCollectionResult.run_id == str(run.id))
    ]
    now, after = (
        _compare(run.to_dict(), str(baseline_run.id), collected, types, *labelled[side], policies[side], rules[side])
        for side in ("now", "after")
    )
    before_text, after_text = sentences
    moved = [
        {
            "field": field,
            "event_type": event_type,
            "text": after_text.get((field, event_type)) or before_text.get((field, event_type)) or event_type,
            "change": "added"
            if (field, event_type) not in before_text and (field, event_type) in after_text
            else "removed"
            if (field, event_type) in before_text and (field, event_type) not in after_text
            else "same",
            "now": now["by_rule"][(field, event_type)],
            "after": after["by_rule"][(field, event_type)],
        }
        for field, event_type in sorted(set(now["by_rule"]) | set(after["by_rule"]))
    ]
    moved.sort(key=lambda row: -abs(row["after"] - row["now"]))
    added = sorted(set(after["outcomes"]) - set(now["outcomes"]))
    removed = sorted(set(now["outcomes"]) - set(after["outcomes"]))
    stamps = [item["observed_at"] for item in labelled["now"][1] if item.get("observed_at")]
    latest = max(stamps) if stamps else None
    return {
        "available": True,
        "baseline": {
            "run_id": str(baseline_run.id),
            "observed_at": latest.isoformat() if hasattr(latest, "isoformat") else latest,
        },
        "now": {"changes": now["changes"], "events": now["events"]},
        "after": {"changes": after["changes"], "events": after["events"]},
        "rules": moved,
        "added_count": len(added),
        "added": [after["outcomes"][key] for key in added[:LIST_LIMIT]],
        "removed_count": len(removed),
        "removed": [now["outcomes"][key] for key in removed[:LIST_LIMIT]],
    }


__all__ = ["trial_on_last_patrol"]
