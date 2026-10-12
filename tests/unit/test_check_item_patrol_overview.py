"""The catalog's patrol status is read from what a check item compiles to and what is in force."""

from types import SimpleNamespace

import pytest

from src.apps.rule_governance.backend.check_items import patrol_overview
from src.core.check_items.canonical import freeze_rule_semantics
from src.core.check_items.local_semantics import derive_assessment_mode
from tests.unit.test_check_item_value_use import STATUS, contract, cpu, field


def item(value, check_item_id='probe.item'):
    frozen = freeze_rule_semantics(value)
    return SimpleNamespace(id=check_item_id, version_id=f'{check_item_id}:v1', digest=check_item_id,
                           result_mode='structured', assessment_mode=derive_assessment_mode(frozen),
                           canonical_contract=frozen)


def adapters(*version_ids):
    return [SimpleNamespace(id=f'adapter-{value}', version_id=value) for value in version_ids]


def summary(check_item, adapter_rows, *, rules_active, ready=()):
    patrol_overview._COMPILED.clear()
    return patrol_overview._summary(check_item, adapter_rows, rules_active=rules_active, ready=set(ready),
                                    pending_change=None, rules_awaiting_approval=False)


def test_a_field_use_is_what_the_definition_compiles_to():
    uses = patrol_overview._compile(item(cpu()))['uses']
    assert uses == {'cpu': 'health', 'warn': 'reference', 'alarm': 'reference'}
    tracked = patrol_overview._compile(item(contract(
        field('status', 'state', 'change', groups=STATUS), field('serial', 'text', 'evidence'))))
    assert tracked['uses'] == {'status': 'change', 'serial': 'evidence'}
    assert tracked['policies'] == 1 and tracked['rules'] == 3


def test_state_health_counts_as_judged_now():
    compiled = patrol_overview._compile(item(contract(field('status', 'state', 'current_state', groups=STATUS))))
    assert compiled['uses'] == {'status': 'health'}


@pytest.mark.parametrize('adapter_ids,rules_active,ready,state', [
    ((), 4, (), 'no_adapter'),
    (('a', 'b'), 1, ('a', 'b'), 'rules_missing'),
    (('a', 'b'), 4, ('a',), 'adapters_pending'),
    (('a', 'b'), 4, ('a', 'b'), 'ready'),
])
def test_the_first_unmet_step_of_the_chain_names_the_state(adapter_ids, rules_active, ready, state):
    tracked = item(contract(field('status', 'state', 'change', groups=STATUS)))
    result = summary(tracked, adapters(*adapter_ids), rules_active=rules_active, ready=ready)
    assert result['state'] == state
    assert result['rules_wanted'] == 4 and result['judged_fields'] == 1


def test_a_check_item_that_only_shows_values_is_not_a_gap():
    shown = item(contract(field('serial', 'text', 'evidence')))
    result = summary(shown, adapters('a'), rules_active=0, ready=('a',))
    assert (result['state'], result['rules_wanted'], result['judged_fields']) == ('display_only', 0, 0)


def test_numeric_health_needs_no_rule_assets_to_be_ready():
    result = summary(item(cpu()), adapters('a'), rules_active=0, ready=('a',))
    assert (result['state'], result['rules_wanted'], result['judged_fields']) == ('ready', 0, 1)


def test_topology_check_items_need_neither_rules_nor_runtime_bundles():
    topology = item(contract(field('status', 'state', 'change', groups=STATUS)), 'topology.vlans')
    result = summary(topology, adapters('a', 'b'), rules_active=0)
    assert result['state'] == 'topology' and result['adapters_ready'] == 2
