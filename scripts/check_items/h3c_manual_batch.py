"""Locally prepare and replay H3C captures; never invoke an asset generator.

Only explicit command sections are admitted. Source files remain unchanged.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import textfsm
from src.core.patrol.command_splitter import split_cli_capture
from src.apps.rule_governance.backend.parser.textfsm_input import prepare_textfsm_input
from src.apps.rule_governance.backend.parser.record_shape import expand_list_primary_records

PACK = Path(__file__).with_name('h3c_manual_templates')
REVISION = 'h3c-manual-2026-09-23'


def sanitize(value):
    value = re.sub(r'XA-FOTIC|XA-FOTI?\.{3}', 'TEST', value, flags=re.I)
    return re.sub(r'(?<!\d)10\.34(?=\.\d{1,3}\.\d{1,3}(?!\d))', '192.168', value)


def assert_sanitized(value):
    if re.search(r'XA-FOTIC|XA-FOTI?\.{3}|(?<!\d)10\.34\.\d+\.\d+', value, re.I):
        raise ValueError('Forbidden source identifier remains in batch')


def entries():
    return json.loads((PACK/'manifest.json').read_text(encoding='utf8'))['entries']


def select(platform, model, command):
    return [e for e in entries() if e['command'] == command and
            (not e.get('models') or model in e['models'])]


def parse(entry, body):
    content = (PACK/entry['path']).read_text(encoding='utf8')
    fsm = textfsm.TextFSM(io.StringIO(content))
    rows = [dict(zip(fsm.header, row)) for row in fsm.ParseText(prepare_textfsm_input(content, body))]
    return fsm.header, expand_list_primary_records(rows, entry.get('primary_keys', []))


def decode_capture(path):
    raw = path.read_bytes()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('Unsupported source encoding: ' + path.name)


def prepare(directory, output):
    output.mkdir(parents=True, exist_ok=True)
    batch = {'revision': REVISION, 'files': [], 'samples': [], 'ignored': []}
    names = set()
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in {'.txt', '.log'}:
            continue
        name = sanitize(path.name)
        if name in names:
            raise ValueError('Sanitized filename collision')
        names.add(name)
        text = sanitize(decode_capture(path)); assert_sanitized(name + text)
        match = re.search(r'(?m)^H3C (\S+) uptime is ', text)
        if not match:
            raise ValueError('Missing explicit display version model: ' + name)
        model = match[1]; accepted = []; seen = set()
        for ordinal, block in enumerate(split_cli_capture(text, device_name=Path(name).stem)):
            command, body = block.command_canonical.lower(), block.raw_output.strip()
            item = {'file': name, 'platform': 'comware', 'model': model, 'command': command,
                    'source_line': block.start_line, 'ordinal': ordinal}
            digest = hashlib.sha256(body.encode()).hexdigest()
            if (command, digest) in seen:
                batch['ignored'].append({**item, 'reason': 'duplicate_command_output'}); continue
            seen.add((command, digest))
            if not command.startswith('display '):
                batch['ignored'].append({**item, 'reason': 'session_command'}); continue
            parsed = [(e, parse(e, body)) for e in select('comware', model, command)]
            if not any(rows for _, (_, rows) in parsed) and not (command == 'display current-configuration' and len(body) > 100):
                if command not in {'display debugging', 'display vrrp statistics', 'display logbuffer level 0', 'display logbuffer level 1'} and not select('comware', model, command):
                    raise ValueError('Uncovered command: ' + command)
                batch['ignored'].append({**item, 'reason': 'empty_header_only_or_unsupported'}); continue
            item.update(text=body, sha256=digest, record_sets=[{'name': e['record_set'], 'check_item_id': e['check_item_id'], 'rows': len(rows), 'fields': fields} for e, (fields, rows) in parsed if rows])
            batch['samples'].append(item); accepted.append((command, body))
        target = output/'samples'/name; target.parent.mkdir(exist_ok=True)
        target.write_text('\n\n'.join('命令: '+cmd+'\n输出:\n'+body+'\n'+'-'*50 for cmd, body in accepted)+'\n', encoding='utf8')
        batch['files'].append({'name': name, 'path': str(target.resolve()), 'platform': 'comware', 'model': model, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'commands': len(accepted)})
    if not batch['samples']:
        raise ValueError('No accepted H3C samples')
    result = json.dumps(batch, ensure_ascii=False, indent=2); assert_sanitized(result)
    (output/'batch.json').write_text(result, encoding='utf8')
    return batch


def verify_batch(batch):
    result = {'files': len(batch['files']), 'samples': len(batch['samples']), 'ignored': len(batch['ignored']), 'records': 0, 'replays': 0, 'assertions': 0}
    for sample in batch['samples']:
        body = sample['text']; all_rows = {}
        assert_sanitized(sample['file'] + body)
        for entry in select('comware', sample['model'], sample['command']):
            fields, rows = parse(entry, body); all_rows[entry['record_set']] = rows
            result['records'] += len(rows); result['replays'] += 1
            keys = entry.get('primary_keys', [])
            if keys and rows:
                assert all(all(r.get(k) for k in keys) for r in rows), (entry['record_set'], 'missing extraction identity')
                assert len({tuple(r[k] for k in keys) for r in rows}) == len(rows), (entry['record_set'], 'duplicate identity')
                result['assertions'] += 2
        if sample['command'] == 'display transceiver diagnosis interface':
            rows = [r for group in all_rows.values() for r in group]
            ports = set(re.findall(r'(?m)^(\S+) transceiver diagnostic information:', body))
            assert {r['interface'] for r in rows} == ports
            assert len(rows) == len(ports) + 3*len(re.findall(r'(?m)^\[channel\]', body))
            assert len({(r['interface'], r['channel']) for r in rows}) == len(rows)
            for group in re.split(r'(?m)^(?=\S+ transceiver diagnostic information:)', body):
                if 'Warning thresholds:' in group:
                    port = group.split()[0]
                    assert all(r['rx_low_warning_dbm'] and r['rx_low_alarm_dbm'] for r in rows if r['interface'] == port)
            result['assertions'] += 4
        patterns = {
            'display interface': ('interface_runtime_statistics', r'(?m)^\S+\n(?:The interface has assigned (?:a DR|an M-LAG) group\.\n)?Current state:'),
            'display version': ('device_version', r'(?m)^(?:MPU\(\S+\) )?Slot \d+:'),
            'display cpu': ('system_cpu_usage', r'(?m)^Slot \d+ CPU \d+ CPU usage:'),
            'display memory': ('system_memory_usage', r'(?m)^Mem:'),
            'display arp': ('network_arp_entries', r'(?m)^\d+(?:\.\d+){3}\s+[\da-fA-F]{4}-'),
            'display mac-address': ('network_mac_forwarding', r'(?m)^[\da-fA-F]{4}(?:-[\da-fA-F]{4}){2}\s+'),
        }
        if sample['command'] in patterns:
            name, pattern = patterns[sample['command']]
            assert len(all_rows[name]) == len(re.findall(pattern, body)), (sample['file'], name, 'source row loss')
            result['assertions'] += 1
        if sample['command'].startswith('display logbuffer'):
            assert sum(map(len, all_rows.values())) == len(re.findall(r'(?m)^%', body))
            result['assertions'] += 1
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); batch = prepare(args.directory, args.output); report = verify_batch(batch)
    (args.output/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))
