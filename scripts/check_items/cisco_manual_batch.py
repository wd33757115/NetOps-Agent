"""Prepare and verify the hand-authored Cisco TextFSM pack, entirely locally.

Command headings are authoritative. Configuration bodies are not split again.
No generator, model API, Celery generation task or NTC runtime lookup is used.
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
from scripts.check_items.audit_cisco_samples import command_sections
from src.apps.rule_governance.backend.parser.textfsm_input import prepare_textfsm_input

PACK = Path(__file__).with_name('cisco_manual_templates')
REVISION = 'cisco-manual-2026-09-23'


def sanitize(value: str) -> str:
    value = re.sub('XA-FOTIC', 'TEST', value, flags=re.IGNORECASE)
    return re.sub(r'(?<!\d)10\.34(?=\.\d{1,3}\.\d{1,3}(?!\d))', '192.168', value)


def assert_sanitized(value: str) -> None:
    if re.search('XA-FOTIC', value, re.IGNORECASE) or re.search(r'(?<!\d)10\.34\.\d+\.\d+', value):
        raise ValueError('The batch still contains a forbidden source identifier')


def entries():
    return json.loads((PACK/'manifest.json').read_text(encoding='utf-8'))['entries']


def parse(entry, body):
    content = (PACK/entry['path']).read_text(encoding='utf-8')
    fsm = textfsm.TextFSM(io.StringIO(content))
    rows = [dict(zip(fsm.header,row)) for row in fsm.ParseText(prepare_textfsm_input(content,body))]
    return fsm.header, rows


def identity(text):
    nx = re.search(r'(?im)^\s*cisco\s+Nexus9000\s+(C[\w-]+)\s+Chassis',text)
    ios = re.search(r'(?im)^cisco\s+((?:WS-C|ISR)[\w/-]+)\s+\(',text)
    if nx:return 'nxos', 'N9K-'+nx[1].upper()
    if ios:return 'ios', ios[1].upper()
    raise ValueError('Missing supported explicit Cisco model in show version')


def select(platform,model,command):
    return [e for e in entries() if e['platform']==platform and e['command']==command
            and (not e.get('models') or model in e['models'])]


def prepare(directory: Path, output: Path):
    output.mkdir(parents=True,exist_ok=True)
    batch={'revision':REVISION,'files':[],'samples':[],'ignored':[]}
    seen=set()
    for path in sorted(directory.glob('*.txt')):
        name=sanitize(path.name)
        if name in seen:raise ValueError('Sanitization caused a filename collision')
        seen.add(name)
        text=sanitize(path.read_text(encoding='utf-8-sig'));assert_sanitized(name+'\n'+text)
        platform,model=identity(text)
        accepted=[]
        for ordinal,(command,body,line) in enumerate(command_sections(text)):
            body=re.sub(r'(?:\n|^)-{30,}\s*$','',body).strip()
            item={'file':name,'platform':platform,'model':model,'command':command,'source_line':line,'ordinal':ordinal}
            if not command.startswith('show '):
                batch['ignored'].append({**item,'reason':'session_command'});continue
            chosen=select(platform,model,command)
            parsed=[(e,parse(e,body)) for e in chosen]
            has_data=any(rows for _,(_,rows) in parsed)
            is_config=command in {'show running-config','show startup-config'} and len(body)>100
            if not has_data and not is_config:
                batch['ignored'].append({**item,'reason':'empty_header_only_or_unsupported'});continue
            item.update(text=body,sha256=hashlib.sha256(body.encode()).hexdigest(),record_sets=[{'name':e['record_set'],'check_item_id':e['check_item_id'],'rows':len(rows),'fields':fields} for e,(fields,rows) in parsed if rows])
            batch['samples'].append(item);accepted.append((command,body))
        target=output/'samples'/name;target.parent.mkdir(exist_ok=True)
        target.write_text('\n\n'.join('命令: '+cmd+'\n输出:\n'+body+'\n'+'-'*50 for cmd,body in accepted)+'\n',encoding='utf-8')
        batch['files'].append({'name':name,'path':str(target.resolve()),'platform':platform,'model':model,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'commands':len(accepted)})
    if not batch['samples']:raise ValueError('No data samples found')
    serialized=json.dumps(batch,ensure_ascii=False,indent=2);assert_sanitized(serialized)
    (output/'batch.json').write_text(serialized,encoding='utf-8')
    return batch


def verify_batch(batch):
    """Compile/replay all admitted outputs and check entity preservation invariants."""
    result={'files':len(batch['files']),'samples':len(batch['samples']),'ignored':len(batch['ignored']),'record_set_replays':0,'records':0,'assertions':0}
    for sample in batch['samples']:
        assert_sanitized(sample['file']+'\n'+sample['text'])
        for entry in select(sample['platform'],sample['model'],sample['command']):
            fields,rows=parse(entry,sample['text']);result['record_set_replays']+=1;result['records']+=len(rows)
            if entry['record_set']=='interface_optical_power':
                ports=set(re.findall(r'(?m)^(Ethernet\S+)',sample['text']))
                assert {r['interface'] for r in rows}==ports
                assert len(rows)==len(ports)+sum(max(0,len(re.findall(r'(?m)^Lane Number:',b))-1) for b in re.split(r'(?m)^Ethernet\S+',sample['text'])[1:])
                assert len({(r['interface'],r['channel']) for r in rows})==len(rows)
                result['assertions']+=3
            elif entry['record_set']=='system_memory_process':
                assert len({(r['PROCESS_ID'],r['PROCESS']) for r in rows})==len(rows)
                assert len(rows)==len(re.findall(r'(?m)^\s*\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\S',sample['text']))
                result['assertions']+=2
            elif entry['record_set']=='system_cpu_process':
                assert len(rows)==len(re.findall(r'(?m)^\s*\d+\s+\d+\s+\d+\s+\d+\s+[\d.]+%',sample['text']))
                result['assertions']+=1
            elif entry['record_set']=='device_version_stack':
                assert len(rows)==len(re.findall(r'(?m)^\s*\*?\s*\d+\s+\d+\s+WS-C\S+',sample['text']))
                assert len({r['SLOT'] for r in rows})==len(rows)
                result['assertions']+=2
            elif entry['record_set']=='network_mac_table':
                expected=len(re.findall(r'(?m)^\s*[*+GCOR~]*\s*\S+\s+[0-9a-fA-F]{4}(?:\.[0-9a-fA-F]{4}){2}\s+\S+',sample['text']))
                assert len(rows)==expected,(sample['file'],len(rows),expected)
                assert all('vPC Peer-Link' not in line or any('vPC Peer-Link' in r['interface'] for r in rows) for line in sample['text'].splitlines())
                result['assertions']+=2
            elif entry['record_set']=='redundancy_vpc_member':
                body=sample['text'].split('vPC status',1)[-1] if 'vPC status' in sample['text'] else ''
                assert len(rows)==len(re.findall(r'(?m)^\d+\s+Po\d+\s+',body))
                result['assertions']+=1
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    batch=prepare(args.directory,args.output);report=verify_batch(batch)
    (args.output/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
