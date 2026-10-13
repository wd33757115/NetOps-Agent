"""The table that tells an engineer how rules are generated says what the compiler does."""

import pytest

from src.apps.rule_governance.backend.check_items.rule_guide import GUIDE, VARIANTS, explain, rule_guide
from src.apps.rule_governance.backend.check_items.rule_preview import preview_check_item_rules
from src.core.check_items.canonical import freeze_rule_semantics
from src.core.check_items.canonical_rule_context import build_canonical_rule_context, generate_canonical_rule_candidates
from src.core.check_items.local_semantics import derive_assessment_mode
from tests.unit.test_check_item_value_use import STATUS, contract, field

ROWS = {row["key"]: row for row in GUIDE}


def produced(rows):
    """What the first of ``rows`` compiles to: its comparison, the classes of its events, and health."""

    frozen = freeze_rule_semantics(contract(*rows))
    context = build_canonical_rule_context(
        check_item_id="guide.item", check_item_version_id="v1", result_mode="structured",
        assessment_mode=derive_assessment_mode(frozen), canonical_contract=frozen)
    result = generate_canonical_rule_candidates(context)
    assert result["blockers"] == []
    target = frozen["observations"][0]
    policies = [p for p in result["change_policies"] if p["observation_type"] == target["observation_type"]]
    rules = [r for r in result["event_rules"] if r["observation_type"] == target["observation_type"]]
    return {
        "compare": policies[0]["compare_mode"] if policies else None,
        "events": [rule["event_class"] for rule in rules],
        "health": bool(target.get("health_policy")),
    }


@pytest.mark.parametrize("row", GUIDE, ids=[row["key"] for row in GUIDE])
def test_every_row_of_the_guide_is_what_its_example_compiles_to(row):
    assert produced(row["example"]) == row["produces"]


@pytest.mark.parametrize("key,rows", VARIANTS, ids=[f"{key}:{rows[0]['semantic_definition']['semantic_type']}" for key, rows in VARIANTS])
def test_enum_and_boolean_compile_the_way_the_state_rows_say(key, rows):
    assert produced(rows) == ROWS[key]["produces"]


def test_the_wording_of_a_row_counts_the_rules_it_produces():
    for row in GUIDE:
        events = row["produces"]["events"]
        assert row["event_rules"].startswith(f"{len(events)} 条") if events else row["event_rules"] == ""
        assert bool(row["change_rule"]) == bool(row["produces"]["compare"])


def test_the_guide_an_engineer_reads_leaves_out_the_examples():
    guide = rule_guide()
    assert [row["key"] for row in guide["rows"]] == [row["key"] for row in GUIDE]
    assert all(set(row) == {"key", "kinds", "use", "needs", "change_rule", "event_rules", "effect"} for row in guide["rows"])
    assert guide["fixed"] and guide["timing"]


@pytest.mark.parametrize("settings,row,summary,reason", [
    (dict(semantic_type="state", use="change", compare_mode="state_transition",
          event_classes=["fault", "recovery", "change"], health=False, grouped=True),
     "change_state_grouped", "会生成 1 条变化规则（状态转换）和 3 条事件规则。", "因为它是状态、用于变化检测，并且填了正常和异常状态。"),
    (dict(semantic_type="boolean", use="change", compare_mode="state_transition", event_classes=["change"],
          health=False, grouped=False),
     "change_state_plain", "会生成 1 条变化规则（状态转换）和 1 条事件规则。", "因为它是布尔、用于变化检测，但没有填正常和异常状态。"),
    (dict(semantic_type="state", use="change", compare_mode="exact", event_classes=["change"], health=False, grouped=True),
     "change_state_plain", "会生成 1 条变化规则（精确比较）和 1 条事件规则。",
     "因为它是状态、用于变化检测，比较方式选了精确比较，所以不区分变坏和恢复。"),
    (dict(semantic_type="enum", use="current_state", compare_mode=None, event_classes=["fault"], health=False, grouped=True),
     "health_state", "会生成 1 条事件规则。", "因为它是枚举、用于健康判断。"),
    (dict(semantic_type="gauge", use="current_state", compare_mode=None, event_classes=[], health=True, grouped=False),
     "health_gauge", "不生成规则：按设备阈值直接判断当前是否异常。", "因为它是数值测量、用于健康判断，按设备自己输出的阈值判断。"),
    (dict(semantic_type="text", use="evidence", compare_mode=None, event_classes=[], health=False, grouped=False),
     "evidence", "不生成规则。", "因为它是文本、用于仅展示。"),
])
def test_a_field_is_told_which_row_it_falls_in_and_why(settings, row, summary, reason):
    told = explain(**settings)
    assert (told["guide_row"], told["summary"], told["reason"]) == (row, summary, reason)


def test_the_draft_preview_says_what_each_field_generates():
    preview = preview_check_item_rules(
        check_item_id="guide.item", result_mode="structured",
        canonical_contract=contract(
            field("status", "state", "change", groups=STATUS), field("mode", "enum", "current_state", groups=STATUS),
            field("serial", "text", "evidence")))
    by_field = {row["canonical_field"]: row for row in preview["fields"]}

    assert (by_field["status"]["guide_row"], by_field["status"]["change_rules"], by_field["status"]["event_rules"]) == (
        "change_state_grouped", 1, 3)
    assert (by_field["mode"]["guide_row"], by_field["mode"]["event_rules"]) == ("health_state", 1)
    assert by_field["serial"]["guide_row"] == "evidence"
    # A field that is only shown is compared with nothing, whatever its tracking flag says.
    assert by_field["serial"]["lines"] == ["只在报告中展示，不判断是否正常。"]
