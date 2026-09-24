import io
from types import SimpleNamespace

import pytest
import textfsm

from scripts.check_items.cisco_manual_batch import PACK, entries, parse, prepare, sanitize
from src.core.patrol.command_splitter import split_cli_capture
from src.core.check_items.value_contract import same_name_fields
from src.core.parser.identity import DeviceIdentity
from src.core.parser.policy import ParserPolicy
from src.core.parser.runtime import ParserRuntime


def test_sanitization_is_scoped_to_requested_prefix():
    assert sanitize('xa-fotic 10.34.2.1 110.34.2.1 10.340.2.1') == (
        'TEST 192.168.2.1 110.34.2.1 10.340.2.1'
    )


def test_configuration_report_owns_embedded_prompt_lines():
    text = ('命令: show running-config\n输出:\nfeature vpc\n'
            'TEST# show vpc\nvPC domain id: 10\n' + '-' * 50 +
            '\n命令: show clock\n输出:\n12:00:00 UTC Mon Jan 1 2024\n')
    blocks = split_cli_capture(text)
    assert [b.command_canonical for b in blocks] == ['show running-config', 'show clock']
    assert 'TEST# show vpc\nvPC domain id: 10' in blocks[0].raw_output


def test_batch_ignores_empty_and_header_only_outputs(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'XA-FOTIC-10.34.2.1.txt').write_text(
        '命令: show version\n输出:\ncisco Nexus9000 C9364C Chassis\n'
        '命令: show running-config\n输出:\nfeature vpc\n' + '! test\n' * 20 +
        'TEST# show vpc\nvPC domain id: 10\n'
        '命令: show vrrp brief\n输出:\nInterface Grp Pri Time Own Pre State Master addr Group addr\n'
        '命令: show logging last 100\n输出:\n', encoding='utf-8',
    )
    batch = prepare(source, tmp_path / 'out')
    assert [s['command'] for s in batch['samples']] == ['show running-config']
    assert 'TEST# show vpc' in batch['samples'][0]['text']
    assert {s['command'] for s in batch['ignored']} >= {'show vrrp brief', 'show logging last 100'}


@pytest.mark.parametrize('entry', entries(), ids=lambda e: e['path'])
def test_manual_templates_compile(entry):
    assert textfsm.TextFSM(io.StringIO((PACK / entry['path']).read_text(encoding='utf-8'))).header


def test_system_and_poe_power_are_not_ac_input_output_states():
    entry = next(e for e in entries() if e['path'] == 'ios__show_env_all__environment_power.textfsm')
    _, rows = parse(entry, '1A  PWR-C2-640WAC  SERIAL  OK  Good  Good  640/250\n')
    assert len(rows) == 1
    assert rows[0]['power_supply_status'] == 'OK'
    assert rows[0]['input_state'] == rows[0]['output_state'] == ''
    assert rows[0]['capacity_watts'] == '640'


def test_internal_memory_processes_with_pid_zero_are_distinct():
    entry = next(e for e in entries() if e['record_set'] == 'system_memory_process')
    _, rows = parse(entry, '0 0 100 20 80 0 0 *Init*\n0 0 50 10 40 0 0 *Dead*\n')
    assert [(r['PROCESS_ID'], r['PROCESS']) for r in rows] == [('0', '*Init*'), ('0', '*Dead*')]
    assert entry['primary_keys'] == ['PROCESS_ID', 'PROCESS']


def test_same_name_dimensions_require_explicit_values():
    contract = {'identity_dimensions': ['interface'], 'observations': [
        {'canonical_field': 'ip_address', 'dimensions': ['address_family']},
        {'canonical_field': 'address_family'},
    ]}
    fields = same_name_fields(contract, ['interface', 'ip_address', 'address_family'])
    assert fields['ip_address'] == {'source': 'ip_address', 'dimensions': {'address_family': {'field': 'address_family'}}}
    with pytest.raises(ValueError, match='missing'):
        same_name_fields(contract, ['interface', 'ip_address'])


def test_approved_template_promotes_unconfigured_command_but_not_config(tmp_path):
    policy_file = tmp_path / 'policy.yaml'
    policy_file.write_text('commands:\n  show running-config:\n    enabled: false\n    handling: raw_hash_diff\n', encoding='utf-8')
    policy = ParserPolicy(policy_path=policy_file, repository=SimpleNamespace(list_command_aliases=lambda **kw: []))
    template = SimpleNamespace(category='multi_record', profile='N9K-C9364C')
    runtime = ParserRuntime(policy=policy, registry=SimpleNamespace(resolve_active=lambda **kw: template))
    identity = DeviceIdentity('Cisco', 'N9K-C9364C', 'NXOS9K', 1.0)
    decision, resolved = runtime._effective_decision(identity=identity, command='show module')
    assert decision.enabled and decision.handling == 'structured' and resolved is template
    decision, resolved = runtime._effective_decision(identity=identity, command='show running-config')
    assert not decision.enabled and decision.handling == 'raw_hash_diff' and resolved is None
