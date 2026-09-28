"""Publish new Value comparison defaults using the existing catalog versions.

Preview by default. Existing evidence, TextFSM and health policies are preserved.
Apply while inspection/rule-governance are drained; then reassemble affected bundles.
"""
import argparse
import json
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.check_items.catalog import (
    CheckItemCatalog, CheckItemCatalogRepository, build_check_item_definition,
    build_collection_implementation, validate_check_item_definition,
)

REVISION = 'ordinary-change-tracking-v1'


def disable_default(check_id, row):
    field = row.get('canonical_field', '')
    if row.get('semantic_type') == 'threshold' or row.get('assessment_roles') == ['reference']:
        return True
    if check_id == 'interface.runtime_statistics':
        return field in {'input_packets', 'output_packets', 'input_bytes', 'output_bytes'}
    if check_id in {'system.cpu_usage', 'system.memory_usage'}:
        return row.get('semantic_type') in {'gauge', 'counter', 'threshold'}
    if check_id == 'interface.optical_power':
        return field in {'rx_power_dbm', 'tx_power_dbm'}
    return (check_id == 'environment.power' and row.get('semantic_type') == 'gauge'
            and not row.get('health_policy') and not row.get('threshold_references'))


def revise_catalog(catalog):
    items, changed = [], {}
    for item in catalog.check_items:
        raw = deepcopy(asdict(item))
        rows = raw['canonical_contract'].get('observations', [])
        fields = [row for row in rows if disable_default(item.id, row) and row.get('change_tracking') is not False]
        if fields:
            for row in fields:
                row['change_tracking'] = False
            raw['canonical_contract']['change_tracking_revision'] = REVISION
            raw['version'] += 1
            item = build_check_item_definition(raw)
            validate_check_item_definition(item)
            changed[item.id] = item.version_id
        items.append(item)
    implementations = []
    for item in catalog.implementations:
        raw = deepcopy(asdict(item))
        raw['commands'] = list(raw['commands'])
        dirty = False
        for output in raw['adapter_spec'].get('outputs', []):
            check_id = output.get('check_item_id')
            if check_id in changed:
                output['check_item_version_id'] = changed[check_id]
                dirty = True
        if dirty:
            raw['version'] += 1
            item = build_collection_implementation(raw, known_check_item_ids={i.id for i in items})
        implementations.append(item)
    return CheckItemCatalog(catalog.catalog_version + bool(changed), tuple(items), tuple(implementations))


def apply_revision(before, after, report, path):
    """Reuse catalog, semantic-rule and runtime-bundle publication services."""
    from scripts.check_items.refresh_semantic_rules import apply_plan, build_plan, write_json
    from src.apps.rule_governance.backend.check_items.runtime_bundle import CheckItemRuntimeBundleService
    from src.infrastructure.db import models as m
    from src.infrastructure.db.postgres import get_db_session

    actor = 'admin'
    service = CheckItemRuntimeBundleService()
    state_path = path.with_suffix('.deployment.json')
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding='utf-8'))
    else:
        checks = {item['id'] for item in report['check_items']}
        adapters = {item['id'] for item in report['implementations']}
        old_checks = {item.version_id: item.id for item in before.check_items}
        old_adapters = {item.version_id: item.id for item in before.implementations}
        with get_db_session() as db:
            rows = db.query(m.CheckItemRuntimeBundleVersionRecord).filter_by(status='active').all()
            affected = [row.id for row in rows if old_checks.get(row.check_item_version_id) in checks
                        or old_adapters.get(row.implementation_version_id) in adapters]
            # Profiles are immutable and need an explicit new rollout as well.
            if affected and db.query(m.CommandProfileVersionRecord).filter_by(status='active').count():
                raise ValueError('Active command profiles require a coordinated profile rollout before applying this revision')
        state = {'check_ids': sorted(checks), 'bundles': [service.get_version(key) for key in affected],
                 'published_bundles': {}, 'catalog_applied': False}
        write_json(state_path, state)
    if not state['catalog_applied']:
        CheckItemCatalogRepository().bootstrap(after, actor=actor)
        state['catalog_applied'] = True
        write_json(state_path, state)
    rules_path = path.with_suffix('.rules.json')
    if not rules_path.exists():
        write_json(rules_path, build_plan(set(state['check_ids'])))
    apply_plan(json.loads(rules_path.read_text(encoding='utf-8')), path.with_suffix('.rules-state.json'), actor)
    catalog = CheckItemCatalogRepository().load_active()
    checks = {item.id: item for item in catalog.check_items}
    adapters = {item.id: item for item in catalog.implementations}
    for old in state['bundles']:
        if old['version_id'] in state['published_bundles']:
            continue
        params = {
            'check_item_version_id': checks[old['check_item_id']].version_id,
            'implementation_version_id': adapters[old['implementation_id']].version_id,
            'parser_template_ids': [item['template_id'] for item in old['parser_bundle'].values()],
            'comparison_profile_version_id': old.get('comparison_profile_version_id'),
        }
        preview = service.preview_assembly(**params)
        if preview['blockers']:
            raise ValueError({'check': old['check_item_id'], 'adapter': old['implementation_id'], 'blockers': preview['blockers']})
        draft = service.assemble(**params, preview_digest=preview['preview_digest'], actor=actor)
        service.activate(draft['version_id'], actor=actor, reason='Value 普通变化跟踪配置修订，保留现有 TextFSM 和健康判断', allow_same_actor=True)
        state['published_bundles'][old['version_id']] = draft['version_id']
        write_json(state_path, state)
    print({'runtime_bundles': len(state['published_bundles'])})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repository = CheckItemCatalogRepository()
    before = repository.load_active()
    after = revise_catalog(before)
    old = {item.id: item for item in before.check_items}
    old_impl = {item.id: item for item in before.implementations}
    report = {'revision': REVISION, 'check_items': [asdict(i) for i in after.check_items if i.version_id != old[i.id].version_id],
              'implementations': [asdict(i) for i in after.implementations if i.version_id != old_impl[i.id].version_id]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.with_suffix('.deployment.json').exists():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    if args.apply:
        import os
        import django
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'web/django_backend'))
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_backend.settings')
        django.setup()
        from bff.models import AppInstallation
        for app in ('inspection', 'rule-governance'):
            installed = AppInstallation.objects.filter(app_id=app).first()
            if installed and installed.state not in {'installed', 'disabled'}:
                raise ValueError(f'Drain and disable {app} before applying')
        apply_revision(before, after, report, args.output)
    print(json.dumps({'checks': len(report['check_items']), 'adapters': len(report['implementations']),
                      'mode': 'applied' if args.apply else 'preview'}))


if __name__ == '__main__':
    main()
