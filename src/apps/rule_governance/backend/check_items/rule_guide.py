"""How a field's settings become rules, written down once.

Change rules and event rules are not authored: each field compiles to them from
its semantic type, its one use, and the normal and abnormal values or limit
fields it names. This module states that correspondence as the table an
engineer reads. Every row carries a field set the way the row describes and
what it must produce; the tests compile each of them, so the table cannot say
one thing while the compiler does another.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_KIND = {
    "state": "状态", "enum": "枚举", "boolean": "布尔", "text": "文本",
    "gauge": "数值测量", "counter": "累计计数", "threshold": "阈值参考",
}
_USE = {"evidence": "仅展示", "reference": "阈值参考", "change": "变化检测", "current_state": "健康判断"}
_STATE_LIKE = {"state", "enum", "boolean"}
_COMPARE = {"exact": "精确比较", "counter_delta": "计数增量", "numeric_delta": "数值变化", "state_transition": "状态转换"}


def _field(kind: str, use: str, *, groups: Mapping[str, list[Any]] | None = None, unit: str | None = None,
           limits: bool = False) -> list[dict[str, Any]]:
    """Observation rows as the form saves them: the field the row is about, then what it refers to."""

    numeric = kind in {"gauge", "counter", "threshold"}
    value_type = "number" if numeric else "boolean" if kind == "boolean" else "text"
    groups = dict(groups or {})

    def row(name: str, row_kind: str, row_use: str, **extra: Any) -> dict[str, Any]:
        row_type = "number" if row_kind in {"gauge", "counter", "threshold"} else value_type
        own = groups if name == "value" else {}
        return {
            "observation_type": f"guide.{name}", "canonical_field": name, "value_type": row_type, "unit": unit,
            "required": False,
            "semantic_definition": {
                "description": name, "semantic_type": row_kind, "value_types": [row_type], "canonical_unit": unit,
                "assessment_roles": [row_use], "allowed_values": [v for values in own.values() for v in values],
                "state_groups": own,
            },
            **extra,
        }

    if limits:
        return [
            row("value", kind, use, threshold_references={"high": {"field_name": "limit"}}),
            row("limit", "threshold", "reference"),
        ]
    return [row("value", kind, use)]


_GROUPS = {"healthy": ["normal"], "unhealthy": ["failed"]}

# One row per kind of setting. ``produces`` is what the compiler must make of ``example``:
# the comparison of its change rule, the classes of its event rules, and whether it is a health judgment.
GUIDE: list[dict[str, Any]] = [
    {
        "key": "evidence", "kinds": "任意", "use": "仅展示", "needs": "",
        "change_rule": "", "event_rules": "",
        "effect": "只进报告，不判断，不产生规则和事件。",
        "example": _field("text", "evidence"),
        "produces": {"compare": None, "events": [], "health": False},
    },
    {
        "key": "reference", "kinds": "阈值参考", "use": "阈值参考", "needs": "",
        "change_rule": "", "event_rules": "",
        "effect": "给数值字段当阈值，本身不判断。",
        "example": _field("threshold", "reference", unit="percent"),
        "produces": {"compare": None, "events": [], "health": False},
    },
    {
        "key": "change_text", "kinds": "文本", "use": "变化检测", "needs": "",
        "change_rule": "1 条，精确比较", "event_rules": "1 条提示",
        "effect": "和上次巡检的值不同，就产生提示事件。",
        "example": _field("text", "change"),
        "produces": {"compare": "exact", "events": ["change"], "health": False},
    },
    {
        "key": "change_counter", "kinds": "累计计数", "use": "变化检测", "needs": "",
        "change_rule": "1 条，计数增量", "event_rules": "1 条提示",
        "effect": "计数比上次巡检增加了，就产生提示事件。",
        "example": _field("counter", "change"),
        "produces": {"compare": "counter_delta", "events": ["change"], "health": False},
    },
    {
        "key": "change_gauge", "kinds": "数值测量", "use": "变化检测", "needs": "",
        "change_rule": "1 条，数值变化", "event_rules": "1 条提示",
        "effect": "数值和上次巡检不同，就产生提示事件。",
        "example": _field("gauge", "change", unit="percent"),
        "produces": {"compare": "numeric_delta", "events": ["change"], "health": False},
    },
    {
        "key": "change_state_grouped", "kinds": "状态、枚举、布尔", "use": "变化检测", "needs": "正常状态和异常状态",
        "change_rule": "1 条，状态转换", "event_rules": "3 条：故障（重要）、恢复、提示",
        "effect": "从正常变为异常产生故障事件，恢复时自动关闭它；其他变化只提示。",
        "example": _field("state", "change", groups=_GROUPS),
        "produces": {"compare": "state_transition", "events": ["fault", "recovery", "change"], "health": False},
    },
    {
        "key": "change_state_plain", "kinds": "状态、枚举、布尔", "use": "变化检测", "needs": "不填正常和异常状态",
        "change_rule": "1 条，状态转换", "event_rules": "1 条提示",
        "effect": "任何变化都只产生提示事件，不区分变坏和恢复。比较方式改成精确比较时也是这样。",
        "example": _field("state", "change"),
        "produces": {"compare": "state_transition", "events": ["change"], "health": False},
    },
    {
        "key": "health_state", "kinds": "状态、枚举、布尔", "use": "健康判断", "needs": "正常状态和异常状态",
        "change_rule": "", "event_rules": "1 条故障（重要）",
        "effect": "当前值属于异常状态就计入当前异常，不和上次巡检比较。",
        "example": _field("state", "current_state", groups=_GROUPS),
        "produces": {"compare": None, "events": ["fault"], "health": False},
    },
    {
        "key": "health_gauge", "kinds": "数值测量", "use": "健康判断", "needs": "阈值字段",
        "change_rule": "", "event_rules": "",
        "effect": "超出设备自己输出的阈值就计入当前异常，可以再加趋势提醒。不产生事件。",
        "example": _field("gauge", "current_state", unit="percent", limits=True),
        "produces": {"compare": None, "events": [], "health": True},
    },
]

FIXED = [
    "级别是固定的：故障事件一律“重要”，变化事件一律“提示”。",
    "事件名是固定的：字段的观测语义加后缀，变坏 .degraded、恢复 .recovered、变化 .changed，健康判断的故障以 _down 结尾。",
    "一个字段只有一个用途，所以最多生成 1 条变化规则和 3 条事件规则。",
]

TIMING = [
    "编辑时：每改一处就重新编译，只用于预览，不保存。",
    "打开发布预览时：把编译结果存成规则草稿，草稿不影响巡检。",
    "发布时：规则随检查项版本一起生效，上一个版本的规则同时停用。",
]

# Other state-like kinds compile exactly as the state examples above do.
VARIANTS = [
    ("change_state_grouped", _field(kind, "change", groups=groups))
    for kind, groups in (("enum", _GROUPS), ("boolean", {"healthy": [True], "unhealthy": [False]}))
] + [
    ("change_state_plain", _field(kind, "change")) for kind in ("enum", "boolean")
] + [
    ("health_state", _field(kind, "current_state", groups=groups))
    for kind, groups in (("enum", _GROUPS), ("boolean", {"healthy": [True], "unhealthy": [False]}))
]


def rule_guide() -> dict[str, Any]:
    """The table, the things an engineer cannot set, and when rules come into being."""

    return {
        "rows": [{key: value for key, value in row.items() if key not in {"example", "produces"}} for row in GUIDE],
        "fixed": list(FIXED),
        "timing": list(TIMING),
    }


def explain(*, semantic_type: str, use: str, compare_mode: str | None, event_classes: list[str],
            health: bool, grouped: bool) -> dict[str, Any]:
    """Which row of the guide a field falls in, what it generates, and why, in the engineer's words."""

    state_like = semantic_type in _STATE_LIKE
    if health:
        key = "health_gauge"
    elif use == "current_state":
        key = "health_state"
    elif compare_mode:
        key = (
            ("change_state_grouped" if "fault" in event_classes else "change_state_plain")
            if state_like
            else {"counter": "change_counter", "gauge": "change_gauge"}.get(semantic_type, "change_text")
        )
    else:
        key = "reference" if use == "reference" else "evidence"
    kind_label, use_label = _KIND.get(semantic_type, semantic_type), _USE.get(use, use)
    reason = f"因为它是{kind_label}、用于{use_label}"
    if key == "change_state_grouped":
        reason += "，并且填了正常和异常状态"
    elif key == "change_state_plain":
        reason += (
            "，比较方式选了精确比较，所以不区分变坏和恢复"
            if grouped and compare_mode == "exact"
            else "，但没有填正常和异常状态"
        )
    elif key == "health_gauge":
        reason += "，按设备自己输出的阈值判断"
    parts = []
    if compare_mode:
        parts.append(f"1 条变化规则（{_COMPARE.get(compare_mode, compare_mode)}）")
    if event_classes:
        parts.append(f"{len(event_classes)} 条事件规则")
    if health:
        summary = "不生成规则：按设备阈值直接判断当前是否异常"
    elif parts:
        summary = "会生成 " + "和 ".join(parts)
    else:
        summary = "不生成规则"
    return {
        "guide_row": key,
        "summary": summary + "。",
        "reason": reason + "。",
        "change_rules": 1 if compare_mode else 0,
        "event_rules": len(event_classes),
    }


__all__ = ["FIXED", "GUIDE", "TIMING", "VARIANTS", "explain", "rule_guide"]
