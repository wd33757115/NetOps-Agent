"""Preview/create governed CheckItem revisions from the reviewed Cisco reference pack.

No NTC execution, Parser selection, Value aliasing or device changes happen here.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import yaml
from src.core.check_items.catalog import CheckItemCatalogRepository, build_check_item_definition, load_catalog_defaults, validate_check_item_definition
from src.core.check_items.canonical import freeze_rule_semantics
from src.apps.rule_governance.backend.check_items.canonical_rule_context import build_canonical_rule_context

REFERENCE = Path(__file__).with_name('cisco_reference_values.yaml')

def load_reference():
    return yaml.safe_load(REFERENCE.read_text(encoding='utf-8'))

def build_revisions(catalog, reference=None):
    reference = reference or load_reference()
    existing = {item.id: item for item in catalog.check_items}
    results = []
    for spec in reference['items']:
        old = existing.get(spec['id'])
        if old and old.canonical_contract.get('reference_revision') == reference['revision']:
            continue
        if old is None and not spec['new']:
            raise ValueError(f"Expected existing CheckItem is missing: {spec['id']}")
        if old:
            raw = deepcopy(asdict(old))
            raw['version'] += 1
        else:
            raw = {
                'id':spec['id'], 'version':1, 'category':spec['id'].split('.')[0],
                'result_mode':'structured', 'assessment_mode':'evidence',
                'report_projection':'auto@1',
                'canonical_contract':{'entity_type':spec['id'].replace('.','_'),
                    'identity_dimensions':spec['identity_dimensions'], 'observations':[]},
                'coverage_policy':{'model':'non_exhaustive','require_rule_evaluation':False},
                'empty_result_policy':{'classification':'invalid'},
            }
        raw.update(name=spec['name'], status='draft')
        raw['description'] = spec['record_notes']
        contract = raw['canonical_contract']
        contract.pop('rule_semantics_snapshot', None)
        contract['reference_revision'] = reference['revision']
        contract['value_contract'] = 'same-name/v1'
        contract['record_semantics'] = {**contract.get('record_semantics',{}),
            'reference_notes':spec['record_notes'],
            'missing_values':'缺失与不支持不得补零；仅必填缺失导致不完整；可选字段无数据保留缺失。'}
        contract['value_references'] = spec['references']
        contract.pop('parser_field_vocabulary', None) # Strict names, no alias machinery.
        rows = contract['observations']
        if spec['id']=='network.vrf_inventory':
            for row in rows:
                if row['observation_type']=='network.vrf.status':
                    row['canonical_field']='status'
        current = {r['canonical_field']:r for r in rows}
        for field in spec['fields']:
            name = field['canonical_field']
            if name in current:
                raise ValueError(f"Reference pack duplicates an existing Value: {spec['id']}.{name}")
            row = deepcopy(field)
            row['observation_type'] = f"{spec['id']}.{name.lower()}"
            row['event_on_change'] = False
            row['change_tracking'] = False
            rows.append(row)
        for key in contract['identity_dimensions']:
            if key=='device_id':continue
            contract.setdefault('identity_dimension_rules',{}).setdefault(key, {
                'kind':'identifier','value_type':'text','description':'来自原文的稳定实体标识；不使用行号或虚构值补齐。'})
        contract = freeze_rule_semantics(contract)
        raw['canonical_contract'] = contract
        candidate = build_check_item_definition(raw, defaults=load_catalog_defaults()['check_item'])
        validate_check_item_definition(candidate)
        build_canonical_rule_context(check_item_id=candidate.id,check_item_version_id=candidate.version_id,
            result_mode=candidate.result_mode,assessment_mode=candidate.assessment_mode,canonical_contract=candidate.canonical_contract)
        results.append({'payload':asdict(candidate),'new':old is None,'added_values':[r['canonical_field'] for r in spec['fields']]})
    return results

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['preview','apply'])
    parser.add_argument('--publish-ready',action='store_true',help='Publish only revisions without governance blockers; preserve blocked revisions as drafts.')
    parser.add_argument('--actor',default='admin')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    reference=load_reference();catalog=CheckItemCatalogRepository().load_active()
    revisions=build_revisions(catalog,reference)
    report={'revision':reference['revision'],'items':[]}
    if args.action=='apply':
        from src.apps.rule_governance.backend.check_items.catalog_governance import CheckItemCatalogGovernanceService
        service=CheckItemCatalogGovernanceService()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    for revision in revisions:
        payload=revision['payload'];key=reference['revision']+':'+payload['id']
        result={'id':payload['id'],'name':payload['name'],'new':revision['new'],'added_values':revision['added_values'],'payload':payload,'status':'preview'}
        if args.action=='apply':
            change=service.create_check_item_draft(payload,actor=args.actor,reason='用户要求：依据思科样本和 NTC Value 完善检查项；保留既有必填项与健康策略',idempotency_key=key+':create')
            result['change_id']=change['id']
            impact=service.preview_impact(change['id']);result['impact']=impact;result['status']=change['status']
            if args.publish_ready and not impact['blockers'] and change['status']=='draft':
                service.submit(change['id'],actor=args.actor,reason='已核对字段语义和模板依据，未发现活动运行包切换阻塞',expected_impact_digest=impact['impact_digest'],idempotency_key=key+':submit')
                result['status']=service.decide(change['id'],decision='approved',actor=args.actor,reason='按用户请求发布检查项定义；采集实现和运行包仍须验证',expected_impact_digest=impact['impact_digest'],idempotency_key=key+':approve',allow_same_actor=True)['status']
        report['items'].append(result)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in {'payload','impact','added_values'}},ensure_ascii=False))
    # An already-applied reference pack must still produce a current, empty report.
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

if __name__=='__main__':main()
