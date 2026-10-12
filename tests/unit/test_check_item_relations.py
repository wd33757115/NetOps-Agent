"""A check item reads as field -> judgment -> outcomes, for one version and for the difference between two."""

from src.apps.rule_governance.backend.check_items.relations import compile_relations, diff_relations
from src.core.check_items.canonical import freeze_rule_semantics
from src.core.check_items.local_semantics import derive_assessment_mode
from tests.unit.test_check_item_value_use import STATUS, contract, cpu, field


def relations(value):
    frozen = freeze_rule_semantics(value)
    return compile_relations(
        check_item_id='probe.item', result_mode='structured',
        assessment_mode=derive_assessment_mode(frozen), canonical_contract=frozen)


def by_field(graph):
    return {row['field']['name']: row for row in graph['rows']}


TRACKED = contract(field('status', 'state', 'change', groups=STATUS), field('serial', 'text', 'evidence'))


def test_a_tracked_state_leads_to_its_change_rule_and_one_outcome_per_event_rule():
    graph = relations(TRACKED)
    row = by_field(graph)['status']
    assert (row['rule']['kind'], row['rule']['title'], row['rule']['detail']) == ('change', '变化规则', '状态转换')
    assert [outcome['kind'] for outcome in row['outcomes']] == ['event'] * 3
    assert {outcome['event_class'] for outcome in row['outcomes']} == {'fault', 'recovery', 'change'}
    recovery = next(outcome for outcome in row['outcomes'] if outcome['event_class'] == 'recovery')
    fault = next(outcome for outcome in row['outcomes'] if outcome['event_class'] == 'fault')
    assert recovery['resolves'] == [fault['event_type']]
    assert all(outcome['text'].endswith('。') for outcome in row['outcomes'])
    assert graph['evidence'] == ['serial'] and graph['reference'] == []


def test_numeric_health_leads_to_the_current_abnormal_result_not_to_an_event():
    graph = relations(cpu())
    row = by_field(graph)['cpu']
    assert (row['rule']['kind'], row['rule']['title']) == ('health', '健康判断')
    assert [outcome['key'] for outcome in row['outcomes']] == ['abnormal']
    assert graph['reference'] == ['warn', 'alarm'] and graph['evidence'] == []


def test_a_field_that_is_only_shown_has_no_row():
    graph = relations(contract(field('serial', 'text', 'evidence'), field('model', 'text', 'evidence')))
    assert graph['rows'] == [] and graph['evidence'] == ['serial', 'model']


def test_a_contract_that_cannot_compile_reports_why_and_still_lists_its_fields():
    graph = compile_relations(
        check_item_id='probe.item', result_mode='structured', assessment_mode='health',
        canonical_contract={'observations': [{'canonical_field': 'broken'}]})
    assert graph['rows'] == [] and graph['errors'] and graph['fields'] == ['broken']


def test_the_difference_marks_what_is_new_what_stops_and_what_stays():
    before = relations(TRACKED)
    after = relations(contract(
        field('status', 'state', 'evidence', groups=STATUS),          # judgment taken away, field kept
        field('mode', 'state', 'change', groups=STATUS),              # new field with a rule
        field('model', 'text', 'evidence')))                          # serial gone, model new, both only shown
    diff = diff_relations(before, after)
    rows = by_field(diff)

    assert diff['mode'] == 'diff'
    assert rows['mode']['field']['change'] == 'added' and rows['mode']['rule']['change'] == 'added'
    assert {outcome['change'] for outcome in rows['mode']['outcomes']} == {'added'}
    assert rows['status']['field']['change'] == 'same' and rows['status']['rule']['change'] == 'removed'
    assert {outcome['change'] for outcome in rows['status']['outcomes']} == {'removed'}
    assert diff['evidence'] == {'fields': ['status', 'model'], 'added': ['model'], 'removed': ['serial']}


def test_an_unchanged_version_differs_nowhere_and_a_changed_rule_says_what_it_was():
    same = diff_relations(relations(TRACKED), relations(TRACKED))
    assert {row['rule']['change'] for row in same['rows']} == {'same'}
    assert {outcome['change'] for row in same['rows'] for outcome in row['outcomes']} == {'same'}

    changed = diff_relations(relations(TRACKED), relations(contract(
        field('status', 'state', 'current_state', groups=STATUS), field('serial', 'text', 'evidence'))))
    row = by_field(changed)['status']
    assert row['field']['change'] == 'same'
    assert row['rule']['change'] == 'changed' and row['rule']['title'] == '状态判断'
    assert row['rule']['before'] == {'title': '变化规则', 'detail': '状态转换'}
    assert [outcome['change'] for outcome in row['outcomes']] == ['added', 'removed', 'removed', 'removed']


def test_a_first_version_is_new_throughout_without_listing_every_shown_field_as_an_addition():
    diff = diff_relations(None, relations(TRACKED))
    assert by_field(diff)['status']['rule']['change'] == 'added'
    assert by_field(diff)['status']['field']['change'] == 'added'
    assert diff['evidence'] == {'fields': ['serial'], 'added': [], 'removed': []}
