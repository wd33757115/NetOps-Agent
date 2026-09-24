from copy import deepcopy

import pytest

from scripts.check_items.refresh_semantic_rules import verify_candidates
from src.core.check_items.canonical_rule_context import build_canonical_rule_context, generate_canonical_rule_candidates
from src.core.check_items.catalog import load_catalog


def candidates_for(check_id):
    item = next(c for c in load_catalog().check_items if c.id == check_id)
    context = build_canonical_rule_context(
        check_item_id=item.id, check_item_version_id=item.version_id,
        result_mode=item.result_mode, assessment_mode=item.assessment_mode,
        canonical_contract=item.canonical_contract,
    )
    return context, generate_canonical_rule_candidates(context)


@pytest.mark.parametrize('check_id', ['environment.fan', 'interface.runtime_statistics', 'system.cpu_usage'])
def test_refresh_replays_current_state_change_and_dynamic_threshold_rules(check_id):
    context, candidates = candidates_for(check_id)
    result = verify_candidates(context, candidates)
    assert result['success']
    assert result['assertions'] > result['rule_count']


def test_refresh_rejects_old_field_binding():
    context, candidates = candidates_for('environment.fan')
    changed = deepcopy(candidates)
    changed['event_rules'][0]['condition']['fields'] = ['old_fan_value']
    with pytest.raises(AssertionError):
        verify_candidates(context, changed)


def test_refresh_rejects_unsupported_executor_kind():
    context, candidates = candidates_for('environment.fan')
    changed = deepcopy(candidates)
    changed['event_rules'][0]['condition']['kind'] = 'unsupported_historical_condition'
    with pytest.raises(ValueError, match='No executable regression case'):
        verify_candidates(context, changed)


def test_refresh_does_not_invent_events_for_evidence_only_check():
    context, candidates = candidates_for('device.version')
    result = verify_candidates(context, candidates)
    assert result['policy_count'] == result['rule_count'] == 0
