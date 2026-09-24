from dataclasses import replace
from pathlib import Path
import importlib.util
import yaml
import pytest
from src.core.check_items.catalog import load_catalog,build_check_item_definition,load_catalog_defaults
from src.core.check_items.interface_values import definition
from src.core.check_items.canonical import rule_semantics_catalog

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('cisco_values',ROOT/'scripts/check_items/enrich_cisco_values.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
audit_spec=importlib.util.spec_from_file_location('cisco_audit',ROOT/'scripts/check_items/audit_cisco_samples.py')
audit_module=importlib.util.module_from_spec(audit_spec);audit_spec.loader.exec_module(audit_module)

@pytest.fixture(scope='module')
def revisions():
    catalog=load_catalog()
    items={x.id:x for x in catalog.check_items}
    items['interface.runtime_statistics']=definition()
    source=ROOT/'src/apps/rule_governance/backend/check_items/assets/device_version_values.yaml'
    items['device.version']=build_check_item_definition(yaml.safe_load(source.read_text(encoding='utf-8')),defaults=load_catalog_defaults()['check_item'])
    original=replace(catalog,check_items=tuple(items.values()))
    return original,module.build_revisions(original)

def test_existing_values_required_constraints_and_health_rules_are_preserved(revisions):
    original,changes=revisions
    by_id={c['payload']['id']:c['payload'] for c in changes}
    assert len(changes)==21
    assert 'interface.runtime_statistics' not in by_id
    assert sum(len(c['added_values']) for c in changes)==133
    for old in original.check_items:
        if old.id not in by_id:continue
        after=by_id[old.id]['canonical_contract']
        rows={r['observation_type']:r for r in after['observations']}
        for before in old.canonical_contract['observations']:
            row=rows[before['observation_type']]
            for key in ['required','policy_constraints','health_policy','threshold_references','event_constraints']:
                assert row.get(key)==before.get(key),(old.id,key)
        assert after['identity_dimensions']==old.canonical_contract['identity_dimensions']
    version=by_id['device.version']['canonical_contract']
    required={r['canonical_field'] for r in version['observations'] if r.get('required',True)}|set(version['identity_dimensions'])
    assert required=={'VENDOR','SOFTWARE_VERSION','BOOT_IMAGE','SYSTEM_IMAGE','SLOT','UPTIME','BOARD_TYPE'}

def test_new_fields_have_frozen_semantics_and_do_not_invent_fault_rules(revisions):
    _,changes=revisions
    for change in changes:
        contract=change['payload']['canonical_contract'];catalog=rule_semantics_catalog(contract)
        rows={r['canonical_field']:r for r in contract['observations']}
        for name in change['added_values']:
            row=rows[name]
            assert row['description'] and row['source_kind'] in {'ntc','sample_cli'}
            assert catalog.get(row['observation_type'])
            assert not row.get('event_on_change')
            assert not row.get('event_constraints')
    by_id={c['payload']['id']:c['payload']['canonical_contract'] for c in changes}
    assert by_id['system.memory_process']['identity_dimensions']==['PROCESS_ID','PROCESS']
    assert next(r for r in by_id['network.mac_table']['observations'] if r['canonical_field']=='ports')['collection']=='list'
    assert next(r for r in by_id['system.memory_usage']['observations'] if r['canonical_field']=='memory_total_bytes')['unit']=='bytes'

def test_reference_headers_really_contain_every_referenced_ntc_value():
    import hashlib,re
    for item in module.load_reference()['items']:
        known=set()
        for reference in item['references']:
            path=ROOT/'venv/Lib/site-packages/ntc_templates/templates'/reference['template']
            if not path.exists():pytest.skip('Local reference NTC package unavailable')
            content=path.read_bytes()
            assert hashlib.sha256(content).hexdigest()==reference['sha256']
            known.update(re.findall(r'^Value(?:\s+\w+(?:,\w+)*)?\s+(\w+)\s+\(',content.decode(),re.M))
        for field in item['fields']:assert set(field['ntc_values'])<=known

def test_audit_never_misroutes_memory_startup_or_interface_stats():
    assert audit_module.IOS['show processes memory']=='show_processes_memory_sorted'
    assert 'show startup-config' not in audit_module.IOS
    assert 'show interface stats' not in audit_module.IOS
    sections=list(audit_module.command_sections('命令: show clock\n输出:\n12:00 UTC\n----------\n命令: show version\n输出:\nIOS\n'))
    assert [s[:2] for s in sections]==[('show clock','12:00 UTC'),('show version','IOS')]


def test_fully_applied_preview_replaces_stale_report(monkeypatch,tmp_path):
    import json
    from types import SimpleNamespace
    output=tmp_path/'preview.json'
    output.write_text('{"items":[{"id":"stale"}]}',encoding='utf-8')
    monkeypatch.setattr(module,'CheckItemCatalogRepository',lambda:SimpleNamespace(load_active=lambda:None))
    monkeypatch.setattr(module,'build_revisions',lambda *args:[])
    monkeypatch.setattr(module.sys,'argv',['enrich_cisco_values.py','preview','--output',str(output)])
    module.main()
    assert json.loads(output.read_text(encoding='utf-8'))['items']==[]
