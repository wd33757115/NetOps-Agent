import io
from types import SimpleNamespace

import pytest
import textfsm

from scripts.check_items.h3c_manual_batch import PACK, entries, parse, prepare, sanitize
from src.core.parser.output_classifier import OutputClassifier


def entry(name):
    return next(e for e in entries() if e['record_set'] == name)


@pytest.mark.parametrize('item', entries(), ids=lambda e: e['path'])
def test_templates_compile(item):
    assert textfsm.TextFSM(io.StringIO((PACK/item['path']).read_text(encoding='utf8'))).header


def test_sanitize_full_and_truncated_names_and_only_target_subnet():
    assert sanitize('XA-FOTIC XA-FOTI... XA-FOT... 10.34.5.9 110.34.5.9 10.340.5.9') == 'TEST TEST TEST 192.168.5.9 110.34.5.9 10.340.5.9'


def test_utf16_capture_abbreviations_duplicates_and_empty_outputs(tmp_path):
    source = tmp_path/'input'; source.mkdir()
    (source/'XA-FOTIC-10.34.1.2.log').write_text(
        '命令: display version\n输出:\nH3C S6850-56HF uptime is 1 day\n'
        '命令: dis startup\n输出:\nMainBoard:\n Current startup saved-configuration file: flash:/a.cfg\n Next main startup saved-configuration file: flash:/a.cfg\n Next backup startup saved-configuration file: NULL\n'
        '命令: dis startup\n输出:\nMainBoard:\n Current startup saved-configuration file: flash:/a.cfg\n Next main startup saved-configuration file: flash:/a.cfg\n Next backup startup saved-configuration file: NULL\n'
        '命令: display mac-address\n输出:\nMAC Address VLAN ID State Port Aging\n', encoding='utf-16')
    batch = prepare(source, tmp_path/'output')
    assert [s['command'] for s in batch['samples']] == ['display startup']
    assert batch['files'][0]['name'] == 'TEST-192.168.1.2.log'
    assert any(s['reason'] == 'duplicate_command_output' for s in batch['ignored'])


def test_stacked_members_keep_separate_uptime_and_last_member():
    body = 'H3C Comware Software, Version 7.1, Release TEST\nBoot image: flash:/boot.bin\nSystem image: flash:/system.bin\nSlot 1:\nUptime is 10 days\nBOARD TYPE: TEST1\nSlot 2:\nUptime is 9 days\nBOARD TYPE: TEST2\n'
    _, rows = parse(entry('device_version'), body)
    assert [(r['SLOT'], r['UPTIME'], r['BOARD_TYPE']) for r in rows] == [('1', '10 days', 'TEST1'), ('2', '9 days', 'TEST2')]
    assert all(r['BOOT_IMAGE'] == 'flash:/boot.bin' for r in rows)


def test_multiple_subslot_zero_components_not_overwritten():
    _, rows = parse(entry('device_subslot_inventory'), 'Slot 0: MPU uptime is 1 day\n[SUBSLOT 0]CON (Hardware)1.0\n[SUBSLOT 0]MGE0 (Hardware)2.0\n')
    assert [r['COMPONENT'] for r in rows] == ['CON', 'MGE0']


def test_mac_multihoming_retains_each_port():
    _, rows = parse(entry('network_mac_forwarding'), '1234-5678-9abc 95 Learned XGE1/0/1 Y\n1234-5678-9abc 95 Learned XGE2/0/1 Y\n')
    assert [r['INTERFACE'] for r in rows] == ['XGE1/0/1', 'XGE2/0/1']
    assert entry('network_mac_forwarding')['primary_keys'] == ['VLAN_ID', 'MAC_ADDRESS', 'INTERFACE']


def test_optical_lane_expansion_shares_thresholds_without_loss():
    body = 'HundredGigE1/0/1 transceiver diagnostic information:\n Current diagnostic parameters:\n[module] Temp.(C) Voltage(V)\n 47 3.26\n[channel] Bias(mA) RX power(dBm) TX power(dBm)\n 1 6.66 -1.21 -1.36\n 2 6.46 -1.75 -1.29\n Alarm thresholds:\n High 80 3.60 12.00 3.40 0.50\n Low -10 3.00 0.00 -10.50 -8.60\n'
    _, rows = parse(entry('interface_optical_lanes'), body)
    assert [r['channel'] for r in rows] == ['1', '2']
    assert [r['rx_power_dbm'] for r in rows] == ['-1.21', '-1.75']
    assert all(r['rx_low_alarm_dbm'] == '-10.50' for r in rows)
    assert parse(entry('interface_optical_single'), body)[1] == []


def test_single_optic_warning_thresholds_and_absent_module_do_not_leak():
    body = 'XGE1/0/1 transceiver diagnostic information:\n Current diagnostic parameters:\n Temp.(C) Voltage(V) Bias(mA) RX power(dBm) TX power(dBm)\n 30 3.3 6.3 -20 -3\n Alarm thresholds:\n High 80 3.6 15 2 2\n Low -10 2.9 2 -14 -9\n Warning thresholds:\n High 70 3.4 12 0 0\n Low 0 3.1 3 -12 -7\nXGE1/0/2 transceiver diagnostic information:\nThe transceiver is absent.\n'
    _, rows = parse(entry('interface_optical_single'), body)
    assert len(rows) == 1 and rows[0]['rx_low_alarm_dbm'] == '-14'
    assert rows[0]['rx_low_warning_dbm'] == '-12'
    _, absent = parse(entry('interface_optical_absent'), body)
    assert len(absent) == 1 and absent[0]['interface'] == 'XGE1/0/2'
    assert absent[0]['rx_power_dbm'] == absent[0]['rx_low_alarm_dbm'] == ''


def test_syslog_empty_first_line_reads_continuation():
    body = '%Jan 23 08:29:49:061 2026 TEST DEV/4/DROP: \n Chip=0, Drop=12\n%Jan 23 08:30:49:061 2026 TEST DEV/4/RECOVER: recovered\n'
    _, rows = parse(entry('operations_syslog_records_4'), body)
    assert [r['MESSAGE'] for r in rows] == ['Chip=0, Drop=12', 'recovered']


def test_syslog_mentions_are_not_current_command_errors():
    classifier = OutputClassifier(); decision = SimpleNamespace(canonical_command='display logbuffer level 4')
    body = '%Jan 23 08:29:49:061 2026 TEST SHELL/4/CMD: Unrecognized command found\n'
    assert classifier.classify(body, decision=decision).status == 'data'
    assert classifier.classify(body + "% Unrecognized command found at '^' position.\n", decision=decision).status == 'unsupported_command'
    assert classifier.classify(body + '--More--\n', decision=decision).status == 'paged'


def test_router_fan_level_not_reported_as_rpm_or_fabricated_slot():
    body = 'Index Status Speedlevel\n1 Normal 3\n'
    _, rows = parse(entry('environment_fan'), body)
    assert rows[0]['slot'] == '' and 'speed_rpm' not in rows[0]
    assert parse(entry('environment_fan_speed_level'), body)[1][0]['SPEED_LEVEL'] == '3'
