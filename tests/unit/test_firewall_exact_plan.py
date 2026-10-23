"""Independent exhaustive small-space checks plus large-interval and CLI regressions."""
import importlib
import io
import json
from itertools import product
from pathlib import Path
from random import Random
import sys
from time import perf_counter

import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'src/skills/firewall-policy-generator/scripts'


@pytest.fixture(scope='module')
def api():
    names = ['core', 'policy_sets', 'policy_window', 'policy_optimizer', 'policy_input', 'policy_plan', 'vendor_config', 'vendor_validation', 'plan_preview']
    previous = {name: sys.modules.pop(name, None) for name in names}
    sys.path.insert(0, str(SCRIPTS))
    modules = {name: importlib.import_module(name) for name in names}
    yield modules
    sys.path.remove(str(SCRIPTS))
    for name, module in previous.items():
        sys.modules.pop(name, None)
        if module is not None:
            sys.modules[name] = module


def members(rules):
    return {(rule.context, *point) for rule in rules for point in product(*[
        [v for a, b in axis for v in range(a, b)] for axis in rule.axes])}


def test_random_factoring_and_independent_verifier_against_exhaustive_sets(api):
    opt = api['policy_optimizer']
    rng = Random(409)
    for case in range(1000):
        rules = [opt.Rule(('FW', 'default', 'S', 'D', rng.choice(('tcp', 'udp'))), *[
            api['policy_sets'].union((n, n + 1) for n in rng.sample(range(6), rng.randint(1, 3)))
            for _ in range(3)], (str(i),)) for i in range(rng.randint(1, 12))]
        merged, result = opt.optimize(rules)
        assert result['status'] == 'verified', case
        assert members(merged) == members(rules), case
        assert opt.verify(rules, merged)['equivalent']
        changed = [opt.Rule(rules[0].context, ((0, 7),), ((0, 7),), ((0, 7),))]
        assert opt.verify(rules, changed)['equivalent'] == (members(rules) == members(changed))


def test_rectangle_subtraction_is_exact_and_disjoint(api):
    opt = api['policy_optimizer']
    rng = Random(57)
    for _ in range(500):
        a, b = [tuple((n, n + rng.randint(1, 4)) for n in [rng.randrange(5) for _ in range(3)]) for _ in range(2)]
        pieces = opt.subtract_box(a, b)
        points = lambda box: set(product(*(range(lo, hi) for lo, hi in box)))
        sets = [points(piece) for piece in pieces]
        assert set().union(*sets) == points(a) - points(b)
        assert sum(map(len, sets)) == len(set().union(*sets))


def test_cross_relationship_holes_protocol_and_context_are_preserved(api):
    opt = api['policy_optimizer']
    context = ('FW', 'default', 'S', 'D', 'tcp')
    rules = [opt.Rule(context, ((s, s + 1),), ((d, d + 1),), ((443, 444),)) for s, d in [(1, 10), (1, 20), (3, 10)]]
    merged, _ = opt.optimize(rules)
    points = members(merged)
    assert (context, 3, 20, 443) not in points
    assert (context, 2, 10, 443) not in points
    assert points == members(rules)


def test_budget_and_bad_candidate_preserve_original_rules(api, monkeypatch):
    opt = api['policy_optimizer']
    rule = opt.Rule(('FW', 'default', 'S', 'D', 'tcp'), ((1, 2),), ((3, 4),), ((80, 81),))
    result, status = opt.optimize([rule], budget=opt.Budget(limit=0))
    assert result == [rule] and status['status'] == 'unmerged'
    bad = opt.Rule(rule.context, ((1, 9),), rule.destinations, rule.ports)
    monkeypatch.setattr(opt, 'factor', lambda *args: ([bad], []))
    result, status = opt.optimize([rule])
    assert result == [rule] and status['status'] == 'unmerged'
    assert status['rejected_candidate']['added']


def topology(api, tmp_path, domains, **extra):
    path = tmp_path / 'topology.json'
    path.write_text(json.dumps({'firewalls': [{'name': 'FW', 'type': 'H3C', 'security_domains': domains}], **extra}), encoding='utf-8')
    return api['core'].NetworkTopology(path)


def test_business_request_keeps_separate_paths_for_each_address_owner(api, tmp_path):
    t = topology(api, tmp_path, [{'name': 'S', 'ip_ranges': ['10.0.0.0/24']},
                                {'name': 'D1', 'ip_ranges': ['10.1.0.0/25']},
                                {'name': 'D2', 'ip_ranges': ['10.1.0.128/25']}])
    df = pd.DataFrame([{'src_ip': '10.0.0.1', 'dst_ip': '10.1.0.0/24', 'port': '443', 'proto': 'tcp',
                        'action': 'permit', '_row': 10,
                        '_original': {'source_system': '订单', 'destination_system': '支付'}}])
    plan, _ = api['policy_plan'].build_plan(df, t, 'TEST')
    assert len(plan['evidence']) == 2
    assert {e['path'][-1] for e in plan['evidence']} == {('FW', 'D1'), ('FW', 'D2')}
    assert {e['row'] for e in plan['evidence']} == {10}
    assert plan['requests'][0]['destination_system'] == '支付'
    for evidence in plan['evidence']:
        rules = [r for r in plan['rules'] if evidence['id'] in r['evidence_ids']]
        assert {r['destination_zone'] for r in rules} == {evidence['path'][-1][1]}


def test_slash8_split_without_host_iteration(api, tmp_path, monkeypatch):
    from ipaddress import IPv4Network
    monkeypatch.setattr(IPv4Network, 'hosts', lambda *a: pytest.fail('host enumeration'))
    t = topology(api, tmp_path, [{'name': 'A', 'ip_ranges': ['10.0.0.0/8']}, {'name': 'B', 'ip_ranges': ['10.20.0.0/16']}])
    parts = t.split_ownership(api['policy_sets'].addresses('10.0.0.0/8'))
    assert [p['owner'][1] for p in parts] == ['A', 'B', 'A']
    assert sum(b-a for a,b in [p['interval'] for p in parts]) == 2**24
    assert t.find_ip_owner('10.0.0.0/8') is None


def test_conflicting_owner_is_not_chosen_by_name(api, tmp_path):
    t = topology(api, tmp_path, [{'name': name, 'ip_ranges': ['10.0.0.0/8']} for name in ('A', 'Z')])
    with pytest.raises(ValueError, match='归属冲突'):
        t.split_ownership(api['policy_sets'].addresses('10.0.0.1'))


def test_explicit_empty_connections_and_invalid_path(api, tmp_path):
    domains = [{'name': z, 'ip_ranges': [f'10.{i}.0.0/16'], 'intra_connections': []} for i, z in enumerate(('A', 'B'))]
    t = topology(api, tmp_path, domains)
    assert t.find_shortest_path(('FW', 'A'), ('FW', 'B')) == []
    t = topology(api, tmp_path, domains, explicit_paths=[{'start': {'firewall': 'FW', 'domain': 'A'},
        'end': {'firewall': 'FW', 'domain': 'B'}, 'path': [['FW', 'A'], ['FW', 'B']]}])
    with pytest.raises(ValueError, match='指定路径'):
        t.find_shortest_path(('FW', 'A'), ('FW', 'B'))


@pytest.mark.parametrize('vendor,cls,suffix', [('华为','HuaweiConfigGenerator','.txt'),('H3C','H3CConfigGenerator','.txt'),
    ('H3CF1000','H3CF1000ConfigGenerator','.txt'),('天融信','TopSecConfigGenerator','.cfg'),('山石','HillstoneConfigGenerator','.cfg')])
@pytest.mark.parametrize('proto', ['tcp', 'udp', 'icmp'])
def test_vendor_cli_round_trip_and_missing_condition_rejected(api, tmp_path, vendor, cls, suffix, proto):
    sets, opt = api['policy_sets'], api['policy_optimizer']
    rule = opt.Rule(('FW', 'default', 'S', 'D', proto), sets.addresses('10.0.0.254-10.0.1.2'),
                    sets.addresses('10.1.0.0/16'), sets.ports('1-65535') if proto != 'icmp' else ((0,1),))
    rows = api['policy_plan'].vendor_rules([rule], 'TEST')['FW']
    getattr(api['vendor_config'], cls).generate(str(tmp_path), 'FW', rows)
    text = (tmp_path / ('FW' + suffix)).read_text(encoding='utf-8')
    api['vendor_validation'].validate_output(vendor, text, [rule])
    assert len(text) < 12000
    assert 'xxx' not in text
    with pytest.raises((ValueError, KeyError)):
        api['vendor_validation'].validate_output(vendor, text.replace('10.0.1.2', '10.0.1.9').replace('action permit','action deny').replace('action pass','action deny'), [rule])


def test_cli_removal_of_destination_is_detected(api, tmp_path):
    sets, opt = api['policy_sets'], api['policy_optimizer']
    rule = opt.Rule(('FW','default','S','D','tcp'), sets.addresses('10.0.0.1'),sets.addresses('10.1.0.1'),sets.ports('443'))
    row = api['policy_plan'].vendor_rules([rule],'TEST')['FW'][0]
    text = '\n'.join(api['vendor_config'].HuaweiConfigGenerator._generate_rule(row, 1))
    with pytest.raises(ValueError):
        api['vendor_validation'].validate_output('华为', '\n'.join(l for l in text.splitlines() if 'destination-address' not in l), [rule])


def test_plan_traces_per_device_and_no_added_pair(api, tmp_path):
    t = topology(api, tmp_path, [{'name': 'S', 'ip_ranges': ['10.0.0.0/24']}, {'name': 'D', 'ip_ranges': ['10.1.0.0/24']}])
    df = pd.DataFrame([{'src_ip': s, 'dst_ip': d, 'port': '443', 'proto': 'tcp/udp', 'action': 'permit', '_row': i+2}
        for i,(s,d) in enumerate([('10.0.0.1','10.1.0.1'),('10.0.0.1','10.1.0.3'),('10.0.0.3','10.1.0.1')])])
    plan, rules = api['policy_plan'].build_plan(df,t,'TEST')
    assert {r.context[-1] for r in rules} == {'tcp','udp'}
    assert {e['row'] for e in plan['evidence']} == {2,3,4}
    assert all(r.refs for r in rules)
    assert plan['optimization']['equivalent']


def test_sparse_large_batch_does_not_enumerate_subsets(api):
    opt = api['policy_optimizer']
    rules = [opt.Rule(('FW','default','S','D','tcp'), ((i,i+1),), ((i,i+1),), ((443,444),)) for i in range(300)]
    start = perf_counter()
    merged, _ = opt.optimize(rules)
    assert merged == rules
    assert perf_counter() - start < 12


WINDOW = ('2030-01-02 08:00', '2030-02-01 00:00')


@pytest.mark.parametrize('vendor,cls', [('华为', 'HuaweiConfigGenerator'), ('H3C', 'H3CConfigGenerator')])
@pytest.mark.parametrize('window,hours', [(WINDOW, 168), (WINDOW, None), (None, 0)])
def test_validity_and_long_link_round_trip(api, tmp_path, vendor, cls, window, hours):
    sets, opt, extend = api['policy_sets'], api['policy_optimizer'], api['policy_window'].extend
    rule = opt.Rule(extend(('FW', 'default', 'S', 'D', 'tcp'), window, hours),
                    sets.addresses('10.0.0.1'), sets.addresses('10.1.0.1'), sets.ports('443'))
    plain = opt.Rule(('FW', 'default', 'S', 'D', 'udp'), sets.addresses('10.0.0.1'),
                     sets.addresses('10.1.0.1'), sets.ports('53'))
    rows = api['policy_plan'].vendor_rules([rule, plain], 'REQ-1')['FW']
    getattr(api['vendor_config'], cls).generate(str(tmp_path), 'FW', rows)
    text = (tmp_path / 'FW.txt').read_text(encoding='utf-8')
    api['vendor_validation'].validate_output(vendor, text, [rule, plain])
    # Without the settings the CLI no longer matches the plan.
    for marker in ('time-range REQ-1', 'aging-time'):
        if marker in text:
            stripped = '\n'.join(l for l in text.splitlines() if marker not in l)
            with pytest.raises((ValueError, KeyError)):
                api['vendor_validation'].validate_output(vendor, stripped, [rule, plain])


@pytest.mark.parametrize('vendor,cls,old,new', [
    ('华为', 'HuaweiConfigGenerator', 'to 23:59:59 2030/01/31', 'to 23:59:59 2030/02/28'),
    ('华为', 'HuaweiConfigGenerator', ' long-link enable\n', ''),
    ('H3C', 'H3CConfigGenerator', 'to 24:00 2030/01/31', 'to 18:00 2030/01/31'),
    ('H3C', 'H3CConfigGenerator', 'aging-time 168', 'aging-time 24'),
])
def test_tampered_validity_or_long_link_is_detected(api, tmp_path, vendor, cls, old, new):
    sets, opt, extend = api['policy_sets'], api['policy_optimizer'], api['policy_window'].extend
    rule = opt.Rule(extend(('FW', 'default', 'S', 'D', 'tcp'), WINDOW, 168),
                    sets.addresses('10.0.0.1'), sets.addresses('10.1.0.1'), sets.ports('443'))
    rows = api['policy_plan'].vendor_rules([rule], 'REQ-1')['FW']
    getattr(api['vendor_config'], cls).generate(str(tmp_path), 'FW', rows)
    text = (tmp_path / 'FW.txt').read_text(encoding='utf-8')
    assert old in text
    with pytest.raises(ValueError):
        api['vendor_validation'].validate_output(vendor, text.replace(old, new), [rule])
