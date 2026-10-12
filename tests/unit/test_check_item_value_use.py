"""One use per Value: what the form saves is what the patrol executes."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from src.apps.rule_governance.backend.check_items.rule_preview import preview_check_item_rules
from src.core.check_items.canonical import freeze_rule_semantics
from src.core.check_items.canonical_rule_context import (
    build_canonical_rule_context,
    generate_canonical_rule_candidates,
)
from src.core.check_items.local_semantics import derive_assessment_mode
from src.core.check_items.numeric_health import evaluate_numeric_health


def field(name, kind, role, *, unit=None, groups=None, **extra):
    numeric = kind in {'gauge', 'counter', 'threshold'}
    value_type = 'number' if numeric else 'text'
    values = [value for group in (groups or {}).values() for value in group]
    return {'observation_type': f'probe.{name}', 'canonical_field': name, 'value_type': value_type,
            'unit': unit, 'required': True,
            'semantic_definition': {'description': name, 'semantic_type': kind, 'value_types': [value_type],
                                    'canonical_unit': unit, 'assessment_roles': [role],
                                    'allowed_values': values, 'state_groups': groups or {}},
            **extra}


def contract(*rows):
    return {'entity_type': 'probe', 'identity_dimensions': ['device_id', 'slot'], 'change_events': False,
            'observations': list(rows)}


def refs(**roles):
    return {role: {'field_name': name} for role, name in roles.items()}


def compiled(value):
    frozen = freeze_rule_semantics(value)
    context = build_canonical_rule_context(
        check_item_id='probe.item', check_item_version_id='v1', result_mode='structured',
        assessment_mode=derive_assessment_mode(frozen), canonical_contract=frozen)
    return frozen, generate_canonical_rule_candidates(context)


def cpu(role='current_state', **extra):
    return contract(
        field('cpu', 'gauge', role, unit='percent', threshold_references=refs(high='warn', critical_high='alarm'), **extra),
        field('warn', 'threshold', 'reference', unit='percent'),
        field('alarm', 'threshold', 'reference', unit='percent'))


def test_numeric_health_is_judged_by_device_limits_without_event_rule_assets():
    frozen, result = compiled(cpu())
    row = frozen['observations'][0]
    assert row['health_policy'] == {'baseline_mode': 'device_limits', 'device_thresholds': {'high': ['warn', 'alarm']}}
    assert {'warn', 'alarm'} <= set(row['context_fields'])
    assert result['success'] and not result['event_rules'] and not result['change_policies']
    assert freeze_rule_semantics(frozen) == frozen


def test_trend_is_the_only_part_of_health_policy_the_form_supplies():
    trend = {'kind': 'utilization_increase', 'threshold_ratio': 0.1, 'min_increase_points': 10}
    submitted = {'trend': trend, 'device_thresholds': {'high': ['hand_written']}, 'baseline_mode': 'absolute_relative'}
    value = cpu(health_policy=submitted, fixed_dimensions={'window': '5m'})
    value['observations'][0]['semantic_definition'].update(dimensions=['window'], allowed_dimension_values={'window': ['5m']})
    policy = freeze_rule_semantics(value)['observations'][0]['health_policy']
    assert policy == {'baseline_mode': 'trend_only', 'device_thresholds': {'high': ['warn', 'alarm']},
                      'window': '5m', 'trend': trend}


@pytest.mark.parametrize('mutate,match', [
    (lambda rows: rows[0].update(health_policy={'trend': {'kind': 'linear_power_drop', 'threshold_ratio': 0.1}}), 'dBm'),
    (lambda rows: rows[0].update(health_policy={'trend': {'kind': 'utilization_increase', 'threshold_ratio': 2, 'min_increase_points': 10}}), 'threshold_ratio'),
    (lambda rows: rows[0].update(threshold_references={}), '阈值'),
    (lambda rows: rows[0]['semantic_definition'].update(assessment_roles=['current_state', 'change']), '只能选择一个'),
    (lambda rows: rows[1]['semantic_definition'].update(assessment_roles=['current_state']), '只有状态和数值测量'),
])
def test_combinations_that_cannot_be_judged_are_rejected_on_save(mutate, match):
    value = cpu()
    mutate(value['observations'])
    with pytest.raises(ValueError, match=match):
        freeze_rule_semantics(value)


def test_a_value_that_is_only_shown_keeps_no_health_policy():
    stale = {'baseline_mode': 'device_limits', 'device_thresholds': {'high': ['warn']}}
    frozen, result = compiled(cpu(role='evidence', health_policy=stale))
    assert 'health_policy' not in frozen['observations'][0]
    assert not result['event_rules']


def health(value, context, policy):
    return evaluate_numeric_health({'value': value, 'unit': 'percent', 'context': context}, policy)


def test_device_limits_alone_decide_health():
    policy = {'baseline_mode': 'device_limits', 'device_thresholds': {'high': ['warn', 'alarm']}}
    assert health(50, {'warn': 80, 'alarm': 90}, policy)['status'] == 'healthy'
    exceeded = health(85, {'warn': 80, 'alarm': 90}, policy)
    assert exceeded['status'] == 'abnormal' and exceeded['thresholds']['high']['field'] == 'warn'
    assert exceeded['trend'] is None
    missing = health(85, {}, policy)
    assert missing['status'] == 'unknown' and 'device_high_threshold_missing' in missing['missing']
    assert health(85, {'alarm': 90}, policy)['status'] == 'healthy'
    assert health(None, {'warn': 80}, policy)['status'] == 'unknown'


STATUS = {'healthy': ['UP'], 'unhealthy': ['DOWN'], 'unknown': ['ADM']}


def test_state_health_flags_the_abnormal_value_itself():
    _, result = compiled(contract(field('status', 'state', 'current_state', groups=STATUS)))
    assert [(rule['condition']['kind'], rule['condition']['values'], rule['evaluation_scope'])
            for rule in result['event_rules']] == [('entity_status_down', ['DOWN'], 'current_state')]


def test_change_tracked_state_reports_the_direction_of_the_change():
    frozen, result = compiled(contract(field('status', 'state', 'change', groups=STATUS)))
    assert result['success'], result['blockers']
    rules = {rule['trigger_role']: rule for rule in result['event_rules']}
    assert set(rules) == {'down', 'up', 'change'}
    down, up, change = rules['down'], rules['up'], rules['change']
    assert (down['event_type'], down['event_class'], down['severity']) == ('probe.status.degraded', 'fault', 'major')
    assert down['condition']['from_values'] == ['UP'] and down['condition']['to_values'] == ['DOWN']
    assert (up['event_type'], up['event_class']) == ('probe.status.recovered', 'recovery')
    assert up['recovery'] == {'resolves': ['probe.status.degraded']}
    # A port that was already down is not a fault, and one change yields one event.
    assert {rule['evaluation_scope'] for rule in rules.values()} == {'change'}
    assert down['exclusive'] and up['exclusive'] and not change['exclusive']
    assert down['priority'] > change['priority']
    assert freeze_rule_semantics(frozen) == frozen


def test_direction_events_follow_the_state_groups_when_they_are_removed():
    frozen, _ = compiled(contract(field('status', 'state', 'change', groups=STATUS)))
    edited = deepcopy(frozen)
    edited['observations'][0]['semantic_definition'].update(state_groups={}, allowed_values=[])
    _, result = compiled(edited)
    assert result['success'], result['blockers']
    assert [rule['trigger_role'] for rule in result['event_rules']] == ['change']


def test_preview_says_what_each_value_will_do():
    value = contract(
        *cpu()['observations'],
        field('status', 'state', 'change', groups=STATUS),
        field('fan', 'state', 'current_state', groups={'healthy': ['Normal'], 'unhealthy': ['Fault']}),
        field('serial', 'text', 'evidence', change_tracking=False),
        field('spare', 'threshold', 'reference', unit='percent'))
    preview = preview_check_item_rules(check_item_id='probe.item', result_mode='structured', canonical_contract=value)
    assert preview['valid'] and preview['assessment_mode'] == 'health'
    by_field = {row['canonical_field']: row for row in preview['fields']}
    assert by_field['cpu']['outcome'] == 'health'
    assert '高于设备输出的 warn、alarm（取较严格的一个）时判定异常。' in by_field['cpu']['lines']
    assert by_field['warn']['lines'] == ['作为 cpu 的阈值。']
    assert by_field['spare']['warnings'] == ['还没有字段引用这个阈值。']
    assert by_field['status']['lines'] == [
        '变化比较方式：状态转换。',
        '从 UP 变为 DOWN 时产生故障事件（重要）。', '从 DOWN 恢复为 UP 时产生恢复事件。', '其他变化产生提示事件。']
    assert by_field['fan']['lines'] == ['值为 Fault 时判定异常（重要）。']
    assert by_field['serial']['lines'] == ['只在报告中展示，不判断是否正常。']


def test_preview_reports_an_unfinished_form_instead_of_failing():
    value = cpu()
    value['observations'][0]['threshold_references'] = {}
    preview = preview_check_item_rules(check_item_id='', result_mode='structured', canonical_contract=value)
    assert not preview['valid'] and '阈值' in preview['errors'][0]


def test_builtin_health_policies_are_what_the_form_fields_compile_to():
    """CPU, memory and optical power lose nothing when the form takes them over."""
    path = Path('src/apps/rule_governance/backend/check_items/assets/catalog.yaml')
    items = yaml.safe_load(path.read_text(encoding='utf-8'))['check_items']
    checked = 0
    for item in items:
        source = item['canonical_contract']
        definitions = {row['id']: row for row in source.get('rule_semantics_snapshot', {}).get('observations', [])}
        if not any(row.get('health_policy') for row in source.get('observations') or []):
            continue
        opened = deepcopy(source)
        for row in opened['observations']:
            definition = deepcopy(definitions[row['observation_type']])
            # The form shows a Value the runtime judges as "健康判断".
            role = 'current_state' if row.get('health_policy') else (row.get('assessment_roles') or definition['assessment_roles'])[0]
            row.setdefault('value_type', definition['value_types'][0])
            row['semantic_definition'] = {**definition, 'assessment_roles': [role],
                                          'value_types': [row['value_type']], 'canonical_unit': row.get('unit')}
        saved = freeze_rule_semantics(opened)
        for before, after in zip(source['observations'], saved['observations']):
            if before.get('health_policy'):
                expected = {key: value for key, value in before['health_policy'].items() if key != 'revision'}
                assert after['health_policy'] == expected, before['canonical_field']
                assert after['assessment_roles'] == ['current_state']
                checked += 1
    assert checked == 4


def optic_contract():
    return {'value_contract': 'same-name/v1', 'entity_type': 'optic', 'identity_dimensions': ['interface', 'channel'],
            'observations': [{'observation_type': 'probe.rx', 'canonical_field': 'rx', 'semantic_type': 'gauge',
                              'value_type': 'number', 'unit': 'dbm', 'required': True, 'dimensions': ['channel']}]}


def project(rows, contract=None):
    from src.core.check_items.command_adapter import execute_adapter
    from src.core.check_items.value_contract import same_name_fields

    contract = contract or optic_contract()
    spec = {'schema_version': 'command-adapter/v2', 'parser': {'template_id': 'template'},
            'outputs': [{'check_item_id': 'probe.optic', 'check_item_version_id': 'v1', 'source_record_set': 'optics',
                         'empty_status': 'unavailable',
                         'projection': {'fields': same_name_fields(contract, ['interface', 'channel', 'rx'])}}]}
    return execute_adapter(spec, record_sets={'optics': rows}, device_id='device', adapter_version_id='adapter',
                           check_items={'probe.optic': {'version_id': 'v1', 'canonical_contract': contract}})[0]


def test_an_unchannelized_optic_is_recorded_as_the_aggregate_channel():
    batch = project([{'interface': 'Te1/0/5', 'channel': '', 'rx': '-17.96'},
                     {'interface': 'Hu1/0/49', 'channel': '2', 'rx': '-3.10'}])
    assert batch.quality_status == 'complete', batch.errors
    assert [(row['interface'], row['channel'], row['rx']) for row in batch.records] == [
        ('Te1/0/5', 'aggregate', -17.96), ('Hu1/0/49', '2', -3.1)]
    assert [dims['rx'] for dims in batch.projection_dimensions] == [{'channel': 'aggregate'}, {'channel': '2'}]


def test_only_a_subdividing_dimension_may_be_blank():
    missing_interface = project([{'interface': '', 'channel': '1', 'rx': '-3.10'}])
    assert missing_interface.quality_status == 'unavailable'
    twice = project([{'interface': 'Te1/0/5', 'channel': '', 'rx': '-1'}, {'interface': 'Te1/0/5', 'channel': '', 'rx': '-2'}])
    assert twice.quality_status == 'partial' and 'duplicate primary key' in twice.errors[0]


def test_taking_over_a_value_drops_the_event_contract_of_its_earlier_use():
    legacy = {'role_event_types': {'down': 'ProbeDown', 'up': 'ProbeUp'}, 'required_event_types': ['ProbeDown', 'ProbeUp'],
              'required_event_classes': {'ProbeDown': 'fault', 'ProbeUp': 'recovery'},
              'required_recovery_pairs': [{'fault': 'ProbeDown', 'recovery': 'ProbeUp'}]}
    frozen, result = compiled(contract(field('status', 'state', 'change', groups=STATUS, event_constraints=legacy)))
    assert result['success'], result['blockers']
    assert {rule['event_type'] for rule in result['event_rules']} == {
        'probe.status.degraded', 'probe.status.recovered', 'probe.status.changed'}
    shown, _ = compiled(contract(field('status', 'state', 'evidence', groups=STATUS, event_constraints=legacy)))
    assert not generate_canonical_rule_candidates(build_canonical_rule_context(
        check_item_id='probe.item', check_item_version_id='v1', result_mode='structured',
        assessment_mode='evidence', canonical_contract=shown))['event_rules']


def test_rules_in_force_use_the_preview_sentences_and_separate_older_versions():
    from src.apps.rule_governance.backend.check_items.rule_preview import describe_rules_in_force

    def rule(event_type, event_class, scope, version, **condition):
        return {'event_type': event_type, 'severity': 'info' if event_class == 'recovery' else 'major',
                'event_class': event_class, 'evaluation_scope': scope, 'priority': 100, 'check_item_version_id': version,
                'condition': {'kind': 'transition', 'observation_types': ['optic.presence'], **condition}}

    absent = {'from_group': 'up', 'to_group': 'down', 'from_values': ['present'], 'to_values': ['absent']}
    restored = {'from_group': 'down', 'to_group': 'up', 'from_values': ['absent'], 'to_values': ['present']}
    contract = {'observations': [
        {'canonical_field': 'present', 'observation_type': 'optic.presence',
         'event_constraints': {'allow_initial_transition_fault': False}},
        {'canonical_field': 'rx', 'observation_type': 'optic.rx',
         'health_policy': {'baseline_mode': 'device_limits', 'device_thresholds': {'low': ['rx_low']}}},
        {'canonical_field': 'serial', 'observation_type': 'optic.serial'}]}
    fields = {row['canonical_field']: row for row in describe_rules_in_force(
        contract=contract, version_id='v2', versions={'v1': 1, 'v2': 2},
        event_rules=[rule('Restored', 'recovery', 'change', 'v2', **restored), rule('Absent', 'fault', 'both', 'v2', **absent),
                     rule('Absent', 'fault', 'both', 'v1', **absent)],
        change_policies=[{'observation_type': 'optic.presence', 'compare_mode': 'state_transition', 'check_item_version_id': 'v2'}])}
    assert fields['present']['lines'] == [
        '变化比较方式：状态转换。',
        '从 present 变为 absent 时产生故障事件（重要）。当前值为 absent 时需要预期状态依据才判定异常。',
        '从 absent 恢复为 present 时产生恢复事件。']
    assert (fields['present']['stale_count'], fields['present']['stale_versions']) == (1, [1])
    assert fields['rx']['lines'] == ['低于设备输出的 rx_low 时判定异常。', '设备没有输出阈值时结果为“无法判定”，不会当作正常。']
    assert fields['serial'] == {'canonical_field': 'serial', 'observation_type': 'optic.serial', 'lines': [],
                                'stale_count': 0, 'stale_versions': []}
