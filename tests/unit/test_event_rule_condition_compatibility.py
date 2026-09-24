import pytest

from src.core.patrol.event_rules import condition_matches, current_state_condition_matches
from src.core.patrol.rule_validation import validate_event_rule_condition


@pytest.mark.parametrize("kind", ["transition", "entity_status_down"])
def test_explicit_states_validate_and_match_existing_executor(kind):
    condition = {"kind": kind, "entity_types": ["fan"], "fields": ["status"]}
    if kind == "transition":
        condition.update(from_values=["Present"], to_values=["Absent"], allow_initial_target=True)
    else:
        condition["values"] = ["Absent"]
    assert validate_event_rule_condition(condition) == []
    change = {"entity_type": "fan", "field": "status", "old": "present", "new": "absent"}
    assert condition_matches(condition, change)
    assert not condition_matches(condition, {**change, "new": "present"})
    assert current_state_condition_matches(condition, {"entity_type": "fan", "field": "status", "value": "absent"})


@pytest.mark.parametrize("values", [[], "down", [None], [1], [" "]])
@pytest.mark.parametrize("key", ["values", "from_values", "to_values"])
def test_explicit_states_reject_malformed_lists(key, values):
    condition = {"kind": "entity_status_down" if key == "values" else "transition",
                 "fields": ["status"], key: values}
    if key != "values":
        condition.update(from_group="up", to_group="down")
    assert any(f"condition.{key} must" in error for error in validate_event_rule_condition(condition))


def test_transition_group_fallback_and_unknown_kind_remain_strict():
    condition = {"kind": "transition", "fields": ["status"], "from_group": "up", "to_group": "down"}
    assert validate_event_rule_condition(condition) == []
    assert condition_matches(condition, {"field": "status", "old": "up", "new": "down"})
    del condition["to_group"]
    assert validate_event_rule_condition(condition)
    assert validate_event_rule_condition({"kind": "linear_power_baseline_loss"})
