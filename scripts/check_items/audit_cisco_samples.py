"""Local-only CLI/NTC evidence audit. Reports counts, never sends source text to a model."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path
from textfsm import clitable

# Explicit selection avoids permissive NTC index matches (memory -> CPU,
# startup-config -> standby, interface stats -> interfaces status).
COMMON = {'show version':'show_version','show clock':'show_clock','show inventory':'show_inventory',
          'show ip interface brief':'show_ip_interface_brief','show ip arp':'show_ip_arp'}
IOS = {**COMMON,'show processes cpu':'show_processes_cpu','show processes memory':'show_processes_memory_sorted',
       'show interface status':'show_interfaces_status','show mac address-table':'show_mac-address-table',
       'show vrrp brief':'show_vrrp_brief','show interface transceiver detail':'show_interface_transceiver',
       'show switch detail':'show_switch_detail:show_switch_detail_stack_ports'}
NXOS = {**COMMON,'show module':'show_module','show environment power':'show_environment',
        'show environment fan':'show_environment','show environment temperature':'show_environment_temperature',
        'show interface transceiver detail':'show_interface_transceiver_details',
        'show interface status':'show_interface_status','show vpc':'show_vpc','show mac address-table':'show_mac_address-table'}

def command_sections(text):
    marks=list(re.finditer(r'^命令:\s*(.+)$',text,re.M))
    for i,match in enumerate(marks):
        command=match.group(1).strip().lower()
        body=text[match.end():marks[i+1].start() if i+1<len(marks) else len(text)]
        body=re.sub(r'^\s*输出:\s*\n','',body)
        body=re.sub(r'\n-{10,}\s*$','',body).strip()
        yield command,body,text[:match.start()].count('\n')+1

def audit(directory, templates):
    records=[]
    for path in sorted(Path(directory).glob('*.txt')):
        text=path.read_text(encoding='utf-8-sig')
        platform='cisco_nxos' if 'Nexus Operating System' in text else 'cisco_ios'
        for command,body,line in command_sections(text):
            if not command.startswith('show '):continue
            record={'file':path.name,'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'platform':platform,'command':command,'line':line,'characters':len(body)}
            exact=command.split('|')[0].strip()
            selected=(NXOS if platform=='cisco_nxos' else IOS).get(exact)
            embedded=re.findall(r'^\S+[#>]\s*(show\s+[^\r\n]+)',body,re.M|re.I)
            mismatches=[v for v in embedded if v.lower().strip()!=command]
            if mismatches:record['capture_warning']='mixed_command_outputs'
            if not body.strip():record['capture_warning']='empty_output'
            if re.search(r'(?im)^\s*%\s*(?:invalid|unrecognized|incomplete)',body):record['capture_warning']='command_error'
            if selected:
                names=[platform+'_'+s+'.textfsm' for s in selected.split(':')]
                record['templates']=names
                table=clitable.CliTable('index',str(templates))
                try:
                    table.ParseCmd(body,templates=':'.join(names))
                    rows=[dict(zip(table.header,row)) for row in table]
                    record.update(rows=len(rows),fields=list(table.header),populated={h:sum(bool(row[h]) for row in rows) for h in table.header})
                    record['result']='parsed' if rows else 'empty_not_validated'
                except Exception as error:
                    # Error messages may include CLI content; keep only the type.
                    record.update(result='template_incompatible',error_type=type(error).__name__)
            else:
                record.update(templates=[],result='no_exact_template')
            records.append(record)
    return records

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--templates',type=Path,default=Path(__file__).resolve().parents[2]/'venv/Lib/site-packages/ntc_templates/templates')
    args=parser.parse_args();result=audit(args.directory,args.templates)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    from collections import Counter
    print(json.dumps({'files':len({r['file'] for r in result}),'command_outputs':len(result),'results':dict(Counter(r['result'] for r in result)),'capture_warnings':dict(Counter(r['capture_warning'] for r in result if r.get('capture_warning')))},ensure_ascii=False))
