"""Tell the engineer, in plain words, what a check item judges and reports.

The draft preview runs the very functions that save and compile a check item,
and the rules in force are read from the active assets of its published
version. Both use the same sentences, so the two can be compared line by line.
Nothing is stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.apps.rule_governance.backend.check_items.canonical_rule_context import (
    build_canonical_rule_context,
    generate_canonical_rule_candidates,
)
from src.apps.rule_governance.backend.check_items.rule_guide import explain
from src.core.check_items.canonical import freeze_rule_semantics
from src.core.check_items.local_semantics import derive_assessment_mode

_SEVERITY = {"info": "提示", "warning": "警告", "major": "重要", "critical": "严重"}
_MODE = {"health": "健康与风险", "change": "变化跟踪", "evidence": "审计证据"}


def _join(values: Any) -> str:
    return "、".join(str(value) for value in values)


def _health_lines(policy: Mapping[str, Any]) -> list[str]:
    limits = dict(policy.get("device_thresholds") or {})
    lines = []
    for side, word in (("high", "高于"), ("low", "低于")):
        fields = list(limits.get(side) or [])
        if fields:
            when = "（取较严格的一个）时" if len(fields) > 1 else " 时"
            lines.append(f"{word}设备输出的 {_join(fields)}{when}判定异常。")
    lines.append("设备没有输出阈值时结果为“无法判定”，不会当作正常。")
    if policy.get("window"):
        lines.append(f"只接受 {policy['window']} 统计值。")
    trend = dict(policy.get("trend") or {})
    if trend.get("kind") == "utilization_increase":
        lines.append(
            f"趋势提醒：比上次巡检上升超过 {float(trend['threshold_ratio']):.0%}"
            f" 且至少 {float(trend['min_increase_points']):g} 个百分点；不计入异常。"
        )
    elif trend.get("kind") == "linear_power_drop":
        identity = list(trend.get("identity_fields") or [])
        line = f"趋势提醒：线性光功率比上次巡检下降超过 {float(trend['threshold_ratio']):.0%}；不计入异常。"
        if identity:
            line += f"按 {_join(identity)} 确认是同一模块，" + (
                "身份缺失时不比较。" if trend.get("require_identity", True) else "身份缺失时仍比较并注明未核实。"
            )
        lines.append(line)
    return lines


_COMPARE = {
    "exact": "精确比较",
    "hash_diff": "摘要比较",
    "state_transition": "状态转换",
    "enum_transition": "枚举转换",
    "numeric_delta": "数值变化",
    "counter_delta": "计数增量",
    "threshold_and_deadband": "阈值比较",
    "threshold_low_and_deadband": "下限阈值比较",
    "dimension_dispatch": "按维度选择",
}
_CLASS_ORDER = {"fault": 0, "recovery": 1, "change": 2}


def _policy_line(policy: Mapping[str, Any]) -> str:
    mode = str(policy.get("compare_mode") or "")
    return f"变化比较方式：{_COMPARE.get(mode, mode or '未指定')}。"


def _states(values: Any, group: Any) -> str:
    return _join(values) if values else ("正常状态" if group == "up" else "异常状态")


def _rule_lines(rules: list[Mapping[str, Any]], *, gated: bool = False) -> list[str]:
    """One sentence per rule; the draft preview and the rules in force share them."""

    lines = []
    kinds = {dict(rule.get("condition") or {}).get("kind") for rule in rules}
    directional = "transition" in kinds and "field_change" in kinds
    for rule in rules:
        condition = dict(rule.get("condition") or {})
        kind = condition.get("kind")
        severity = _SEVERITY.get(str(rule.get("severity")), str(rule.get("severity")))
        recovery = rule.get("event_class") == "recovery"
        if kind == "entity_status_down":
            lines.append(f"值为 {_join(condition.get('values') or []) or '异常状态'} 时判定异常（{severity}）。")
        elif kind == "transition":
            source = _states(condition.get("from_values"), condition.get("from_group"))
            target = _states(condition.get("to_values"), condition.get("to_group"))
            if recovery:
                lines.append(f"从 {source} 恢复为 {target} 时产生恢复事件。")
                continue
            line = f"从 {source} 变为 {target} 时产生故障事件（{severity}）。"
            if str(rule.get("evaluation_scope") or "both") != "change":
                line += f"当前值为 {target} 时" + ("需要预期状态依据才判定异常。" if gated else "也判定异常。")
            lines.append(line)
        elif kind == "field_change":
            if rule.get("severity") not in (None, "info"):
                lines.append(f"与上次巡检相比有变化时产生事件（{severity}）。")
            else:
                lines.append("其他变化产生提示事件。" if directional else "与上次巡检相比有变化时产生提示事件。")
        elif kind in {"threshold_field_gte", "threshold_field_lte"}:
            word = "不低于" if kind == "threshold_field_gte" else "不高于"
            lines.append(f"{word}设备输出的 {condition.get('reference_field')} 时产生 {rule.get('event_type')}（{severity}）。")
        elif kind in {"threshold", "threshold_lte"}:
            word, bound = ("不低于", condition.get("gte")) if kind == "threshold" else ("不高于", condition.get("lte"))
            lines.append(f"{word} {bound} 时产生 {rule.get('event_type')}（{severity}）。")
        elif recovery:
            lines.append(f"回到阈值以内时产生恢复事件 {rule.get('event_type')}。")
        else:
            lines.append(f"{rule.get('event_type')}（{severity}）：{kind}。")
    return lines


def describe_rules_in_force(
    *,
    contract: Mapping[str, Any],
    version_id: str,
    versions: Mapping[str, int],
    event_rules: list[Mapping[str, Any]],
    change_policies: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Per Value: what its published version judges now, and what older versions left behind.

    ``event_rules`` and ``change_policies`` are the active assets bound to any
    version of the check item, each carrying its ``check_item_version_id``.
    """

    fields = []
    for row in contract.get("observations") or []:
        name, observation_type = row.get("canonical_field"), row.get("observation_type")
        rules = [
            rule for rule in event_rules
            if observation_type in (dict(rule.get("condition") or {}).get("observation_types") or [])
        ]
        policies = [policy for policy in change_policies if policy.get("observation_type") == observation_type]
        current_rules = sorted(
            (rule for rule in rules if rule.get("check_item_version_id") == version_id),
            key=lambda rule: (_CLASS_ORDER.get(str(rule.get("event_class")), 3), -int(rule.get("priority") or 100)),
        )
        gated = dict(row.get("event_constraints") or {}).get("allow_initial_transition_fault") is False
        lines = _health_lines(row["health_policy"]) if row.get("health_policy") else []
        lines += [_policy_line(policy) for policy in policies if policy.get("check_item_version_id") == version_id]
        lines += _rule_lines(current_rules, gated=gated)
        older = [asset for asset in [*rules, *policies] if asset.get("check_item_version_id") != version_id]
        fields.append(
            {
                "canonical_field": name,
                "observation_type": observation_type,
                "lines": lines,
                "stale_count": len(older),
                "stale_versions": sorted({versions[asset["check_item_version_id"]] for asset in older}),
            }
        )
    return fields


def rules_in_force(check_item_id: str) -> dict[str, Any]:
    """The rules the patrol applies today to the published version of a check item."""

    from src.apps.rule_governance.backend.check_items.catalog import CheckItemCatalogRepository
    from src.infrastructure.db.models import (
        ChangePolicyVersionRecord,
        CheckItemVersionRecord,
        EventRuleRecord,
        EventRuleVersionRecord,
    )
    from src.infrastructure.db.postgres import get_db_session

    item = CheckItemCatalogRepository().load_active().check_item(check_item_id)
    if item is None:
        raise KeyError(f"active check item not found: {check_item_id}")
    with get_db_session() as db:
        versions = {
            str(row.id): int(row.version)
            for row in db.query(CheckItemVersionRecord).filter(CheckItemVersionRecord.check_item_id == check_item_id)
        }
        event_rules = [
            {
                "event_type": version.event_type,
                "severity": version.severity,
                "event_class": version.event_class or "fault",
                "evaluation_scope": version.evaluation_scope,
                "priority": version.priority,
                "condition": dict(version.condition_json or {}),
                "check_item_version_id": str(version.check_item_version_id),
            }
            for version, _rule in db.query(EventRuleVersionRecord, EventRuleRecord)
            .join(EventRuleRecord, EventRuleRecord.id == EventRuleVersionRecord.rule_id)
            .filter(EventRuleRecord.status == "active", EventRuleVersionRecord.status == "active")
            .filter(EventRuleVersionRecord.check_item_version_id.in_(list(versions)))
        ]
        change_policies = [
            {
                "observation_type": version.observation_type,
                "compare_mode": dict(version.policy_json or {}).get("compare_mode"),
                "check_item_version_id": str(version.check_item_version_id),
            }
            for version in db.query(ChangePolicyVersionRecord)
            .filter(ChangePolicyVersionRecord.status == "active")
            .filter(ChangePolicyVersionRecord.check_item_version_id.in_(list(versions)))
        ]
    current_types = {row.get("observation_type") for row in item.canonical_contract.get("observations") or []}
    leftover = [
        asset
        for asset in [*event_rules, *change_policies]
        if asset["check_item_version_id"] != item.version_id
        and not current_types
        & {asset.get("observation_type"), *(dict(asset.get("condition") or {}).get("observation_types") or [])}
    ]
    return {
        "check_item_id": item.id,
        "version": item.version,
        "check_item_version_id": item.version_id,
        # Active assets of earlier versions for Values this version no longer has.
        "leftover_count": len(leftover),
        "fields": describe_rules_in_force(
            contract=item.canonical_contract,
            version_id=item.version_id,
            versions=versions,
            event_rules=event_rules,
            change_policies=change_policies,
        ),
    }


def describe_compiled_rules(
    *,
    check_item_id: str,
    result_mode: str,
    assessment_mode: str,
    canonical_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """What a stored contract compiles to, in the sentences used for the rules in force.

    Compared field by field with ``rules_in_force``, a difference is a difference
    in what the patrol will do, never one of wording.
    """

    try:
        context = build_canonical_rule_context(
            check_item_id=check_item_id,
            check_item_version_id="candidate",
            result_mode=result_mode,
            assessment_mode=assessment_mode,
            canonical_contract=canonical_contract,
        )
        candidates = generate_canonical_rule_candidates(context)
    except (ValueError, KeyError, TypeError) as exc:
        return {"errors": [str(exc)], "fields": {}}
    fields = describe_rules_in_force(
        contract=canonical_contract,
        version_id="candidate",
        versions={"candidate": 0},
        event_rules=[{**rule, "check_item_version_id": "candidate"} for rule in candidates["event_rules"]],
        change_policies=[{**policy, "check_item_version_id": "candidate"} for policy in candidates["change_policies"]],
    )
    return {
        "errors": [str(blocker.get("message") or "") for blocker in candidates["blockers"]],
        "fields": {field["canonical_field"]: field["lines"] for field in fields},
    }


def preview_check_item_rules(
    *,
    check_item_id: str,
    result_mode: str,
    canonical_contract: Mapping[str, Any],
) -> dict[str, Any]:
    if result_mode != "structured":
        return {"valid": True, "errors": [], "assessment_mode": None, "fields": []}
    try:
        contract = freeze_rule_semantics(dict(canonical_contract or {}))
        mode = derive_assessment_mode(contract)
        context = build_canonical_rule_context(
            check_item_id=check_item_id or "draft",
            check_item_version_id="draft",
            result_mode=result_mode,
            assessment_mode=mode,
            canonical_contract=contract,
        )
        candidates = generate_canonical_rule_candidates(context)
    except (ValueError, KeyError, TypeError) as exc:
        # An unfinished form is normal while typing; say what is missing.
        return {"valid": False, "errors": [str(exc)], "assessment_mode": None, "fields": []}

    rules: dict[str, list[Mapping[str, Any]]] = {}
    for rule in candidates["event_rules"]:
        rules.setdefault(str(rule.get("observation_type")), []).append(rule)
    policies = {str(policy.get("observation_type")): policy for policy in candidates["change_policies"]}
    blockers: dict[str, list[str]] = {}
    for blocker in candidates["blockers"]:
        blockers.setdefault(str(blocker.get("observation_type")), []).append(str(blocker.get("message")))
    targets = list(context["targets"])
    referenced_by: dict[str, list[str]] = {}
    for target in targets:
        for reference in dict(target.get("threshold_references") or {}).values():
            referenced_by.setdefault(str(reference.get("field_name")), []).append(target["canonical_field"])

    fields = []
    for target in targets:
        name, observation_type = target["canonical_field"], target["observation_type"]
        roles = list(target.get("assessment_roles") or [])
        warnings = list(blockers.get(observation_type, []))
        if "current_state" in roles and target.get("health_policy"):
            outcome, lines = "health", _health_lines(target["health_policy"])
        elif "current_state" in roles:
            outcome, lines = "health", _rule_lines(rules.get(observation_type, []))
        elif "change" in roles:
            outcome = "change"
            lines = [_policy_line(policies[observation_type])] if observation_type in policies else []
            lines += _rule_lines(rules.get(observation_type, []))
        elif "reference" in roles:
            users = list(dict.fromkeys(referenced_by.get(name, [])))
            outcome = "reference"
            lines = [f"作为 {_join(users)} 的阈值。"] if users else []
            if not users:
                warnings.append("还没有字段引用这个阈值。")
        else:
            outcome = "evidence"
            lines = ["只在报告中展示，不判断是否正常。"]
        if outcome in {"health", "change"} and not lines and not warnings:
            warnings.append("这个字段不会生成任何规则。")
        groups = dict(target.get("state_groups") or {})
        policy = policies.get(observation_type)
        fields.append(
            {
                "canonical_field": name,
                "observation_type": observation_type,
                "outcome": outcome,
                "lines": lines,
                "warnings": warnings,
                # What the field compiles to and which row of the guide says so.
                **explain(
                    semantic_type=str(target.get("semantic_type") or ""),
                    use="current_state" if outcome == "health" else outcome,
                    compare_mode=str(policy.get("compare_mode")) if policy else None,
                    event_classes=[str(rule.get("event_class") or "fault") for rule in rules.get(observation_type, [])],
                    health=bool(target.get("health_policy")),
                    grouped=bool(groups.get("healthy") and groups.get("unhealthy")),
                ),
            }
        )
    return {
        "valid": True,
        "errors": [],
        "assessment_mode": mode,
        "assessment_mode_label": _MODE[mode],
        "fields": fields,
    }


__all__ = ["describe_compiled_rules", "describe_rules_in_force", "preview_check_item_rules", "rules_in_force"]
