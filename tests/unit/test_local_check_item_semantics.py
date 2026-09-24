from copy import deepcopy

import pytest

from src.core.check_items.canonical import freeze_rule_semantics, load_canonical_catalog, rule_semantics_catalog
from src.core.check_items.canonical_rule_context import build_canonical_rule_context, generate_canonical_rule_candidates


def contract():
    return {'entity_type': 'power', 'identity_dimensions': ['SLOT'], 'change_events': False,
            'observations': [{'observation_type': 'custom.power.state', 'canonical_field': 'STATUS',
                              'value_type': 'text', 'required': True,
                              'semantic_definition': {
                                  'description': 'Power state', 'semantic_type': 'state',
                                  'value_types': ['text'], 'assessment_roles': ['current_state'],
                                  'allowed_values': ['ok', 'broken', 'unknown'],
                                  'state_groups': {'healthy': ['ok'], 'unhealthy': ['broken'], 'unknown': ['unknown']},
                              }}]}


def candidates(value):
    context = build_canonical_rule_context(check_item_id='custom.power', check_item_version_id='v1',
                                          result_mode='structured', assessment_mode='health', canonical_contract=value)
    return generate_canonical_rule_candidates(context)


def test_custom_state_is_frozen_and_compiles_actual_unhealthy_values():
    source = contract()
    frozen = freeze_rule_semantics(source)
    assert 'rule_semantics_snapshot' not in source
    result = candidates(frozen)
    assert result['success'], result['blockers']
    assert result['event_rules'][0]['condition']['values'] == ['broken']
    assert load_canonical_catalog().get('custom.power.state') is None
    assert freeze_rule_semantics(frozen) == frozen


def test_edit_changes_digest_but_not_other_check_item_or_global_catalog():
    original = freeze_rule_semantics(contract())
    edited = deepcopy(original)
    definition = edited['observations'][0]['semantic_definition']
    definition['allowed_values'].append('offline')
    definition['state_groups']['unhealthy'].append('offline')
    edited['rule_semantics_snapshot']['digest'] = 'untrusted-client-value'
    updated = freeze_rule_semantics(edited)
    assert updated['rule_semantics_snapshot']['digest'] != original['rule_semantics_snapshot']['digest']
    assert set(candidates(updated)['event_rules'][0]['condition']['values']) == {'broken', 'offline'}
    assert rule_semantics_catalog(original).get('custom.power.state').state_groups['unhealthy'] == ['broken']


@pytest.mark.parametrize('mutation,match', [
    (lambda d: d['state_groups']['healthy'].append('broken'), '交叉'),
    (lambda d: d['state_groups'].pop('unhealthy'), '正常和异常'),
    (lambda d: d.update(assessment_roles=['unsupported']), '用途'),
    (lambda d: d.update(semantic_type='gauge'), '数值类型'),
    (lambda d: d.update(description=' '), '说明'),
])
def test_invalid_semantics_rejected(mutation, match):
    value = contract()
    mutation(value['observations'][0]['semantic_definition'])
    with pytest.raises(ValueError, match=match):
        freeze_rule_semantics(value)


def numeric_contract():
    value = contract()
    value['observations'] = [
        {'observation_type': 'custom.power.watts', 'canonical_field': 'WATTS', 'value_type': 'number', 'unit': 'watts',
         'threshold_references': {'high': {'field_name': 'LIMIT', 'context_key': 'threshold.high', 'source': 'structured_reference'}},
         'semantic_definition': {'description': 'Current power', 'semantic_type': 'gauge', 'canonical_unit': 'watts', 'value_types': ['number'], 'assessment_roles': ['current_state']}},
        {'observation_type': 'custom.power.limit', 'canonical_field': 'LIMIT', 'value_type': 'number', 'unit': 'watts',
         'semantic_definition': {'description': 'Device power limit', 'semantic_type': 'threshold', 'canonical_unit': 'watts', 'value_types': ['number'], 'assessment_roles': ['reference']}},
    ]
    return value


def test_numeric_threshold_is_compiled_from_local_reference():
    result = candidates(freeze_rule_semantics(numeric_contract()))
    assert result['success'], result['blockers']
    assert any(rule['condition'].get('reference_field') == 'LIMIT' for rule in result['event_rules'])


@pytest.mark.parametrize('target,unit', [('MISSING', 'watts'), ('WATTS', 'watts'), ('LIMIT', 'volts')])
def test_invalid_threshold_references_rejected(target, unit):
    value = numeric_contract()
    value['observations'][0]['threshold_references']['high']['field_name'] = target
    value['observations'][1]['semantic_definition']['canonical_unit'] = unit
    with pytest.raises(ValueError, match='阈值'):
        freeze_rule_semantics(value)


@pytest.mark.parametrize('legacy', [False, True])
def test_local_change_opt_in_generates_event_without_bulk_switch(legacy):
    value = contract()
    if legacy:
        value.pop('change_events')
    value['observations'][0]['semantic_definition']['assessment_roles'] = ['change']
    result = candidates(freeze_rule_semantics(value))
    assert result['success'], result['blockers']
    assert result['event_rules'][0]['condition']['kind'] == 'field_change'
    assert result['event_rules'][0]['severity'] == 'info'


def test_numeric_health_cannot_fall_back_to_implicit_thresholds():
    value = numeric_contract()
    value['observations'][0]['threshold_references'] = {}
    with pytest.raises(ValueError, match='隐含固定阈值'):
        freeze_rule_semantics(value)


def test_evidence_only_field_does_not_generate_rules():
    value = contract()
    value['observations'][0]['semantic_definition']['assessment_roles'] = ['evidence']
    result = candidates(freeze_rule_semantics(value))
    assert result['success'] and not result['event_rules'] and not result['change_policies']
