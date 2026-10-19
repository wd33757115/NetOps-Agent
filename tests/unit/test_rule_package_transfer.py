import base64
import copy
import hashlib
import io
import zipfile
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.infrastructure.db import models as m
from src.apps.rule_governance.backend.transfer_assets import SPECS, Graph
from src.apps.rule_governance.backend.transfer_format import pack, unpack, canonical
from src.apps.rule_governance.backend.transfer_service import TransferService, TransferConflict, validate_content, parse_sample

TEMPLATE = 'Value VERSION (\\S+)\n\nStart\n  ^Version ${VERSION}$$ -> Record\n'


@pytest.fixture
def environment(tmp_path):
    def create(name):
        engine = create_engine(f'sqlite:///{tmp_path / (name + ".db")}')
        event.listen(engine, 'connect', lambda con, _: con.execute('PRAGMA foreign_keys=ON'))
        tables = {s.model.__table__ for s in SPECS.values()} | {m.AuditLogRecord.__table__, m.CheckItemCatalogChangeRecord.__table__,
            m.CheckItemRuntimeBundleRecord.__table__, m.CheckItemRuntimeBundleVersionRecord.__table__, m.RuntimeBundleAssetRecord.__table__,
            # Where exported samples come from: parser job samples, else patrol output.
            m.CommandSnapshot.__table__, m.ParserJobSample.__table__, m.ParserCandidateSampleEvidence.__table__}
        while missing := {key.column.table for table in tables for key in table.foreign_keys} - tables:
            tables |= missing
        # Mirror production partial indexes: several legacy models only declare PostgreSQL predicates.
        for table in tables:
            for idx in table.indexes:
                predicate = idx.dialect_options['postgresql'].get('where')
                if predicate is not None:
                    idx.dialect_options['sqlite']['where'] = predicate
        m.Base.metadata.create_all(engine, tables=list(tables))
        factory = sessionmaker(engine)
        @contextmanager
        def scope():
            with factory.begin() as db:
                yield db
        return TransferService(scope, validator=validate_content), scope
    return create


def parser_package(template=TEMPLATE, version='1'):
    asset = {'kind': 'parser', 'ref': 'parser:foreign-id', 'source_status': 'active', 'data': {
        'vendor': 'test', 'model': None, 'family': None, 'profile': 'test', 'canonical_command': 'show version',
        'category': 'system', 'version': version, 'template_text': template, 'validation_json': {'primary_keys': ['VERSION'], 'required_fields': ['VERSION']}}}
    return {'assets': [asset], 'roots': [asset['ref']], 'recipes': [], 'samples': []}


def encode(package):
    return pack(**package)


def test_cross_environment_parser_samples_and_no_inherited_approval(environment):
    service, scope = environment('target')
    p = parser_package()
    p['samples'] = [{'parser': {'$ref': p['roots'][0]}, 'text': 'Version 8.2\n', 'expected': {'default': [{'VERSION': '8.2'}]}}]
    raw = encode(p)
    preview = service.preview(raw)
    with scope() as db:
        assert db.query(m.ParserTemplate).count() == 0  # preview is read only
    result = service.execute(raw, preview['revision'], actor='tester')
    with scope() as db:
        row = db.get(m.ParserTemplate, result['items'][0]['local_id'])
        assert row.id != 'foreign-id'
        assert row.status == 'draft'
        assert row.activated_at is None
        assert row.validation_json['status'] == 'pending'
        assert row.validation_json['primary_keys'] == ['VERSION']
        assert service.parser_samples(row)[0]['sample_role'] == 'package_replay'
        assert parse_sample(row.template_text, 'Version 8.2\n') == p['samples'][0]['expected']
    assert service.execute(raw, preview['revision'], actor='tester')['replayed']
    assert service.preview(raw)['counts'] == {'reuse': 1}


def test_identical_repacked_package_reuses_assets(environment):
    service, scope = environment('same')
    p = parser_package()
    raw = encode(p)
    service.execute(raw, service.preview(raw)['revision'], actor='tester')
    p['assets'][0]['ref'] = 'parser:other-source-id'
    p['roots'] = ['parser:other-source-id']
    raw = encode(p)
    assert service.preview(raw)['counts'] == {'reuse': 1}
    service.execute(raw, service.preview(raw)['revision'], actor='tester')
    with scope() as db:
        assert db.query(m.ParserTemplate).count() == 1


def test_a_withdrawn_copy_is_not_reused_when_the_same_package_comes_again(environment):
    service, scope = environment('withdrawn')
    p = parser_package()
    raw = encode(p)
    first = service.execute(raw, service.preview(raw)['revision'], actor='tester')
    with scope() as db:
        # Its import was cancelled: the draft stood down.
        db.get(m.ParserTemplate, first['items'][0]['local_id']).status = 'deprecated'
    p['assets'][0]['ref'] = p['roots'][0] = 'parser:exported-again'
    raw = encode(p)
    again = service.preview(raw)
    assert again['items'][0]['action'] == 'new_version' and again['requires_confirmation']
    second = service.execute(raw, again['revision'], actor='tester', confirm_conflicts=True)
    with scope() as db:
        row = db.get(m.ParserTemplate, second['items'][0]['local_id'])
        assert row.id != first['items'][0]['local_id'] and row.status == 'draft'


def test_a_template_no_parser_job_made_exports_its_latest_patrol_output_sanitized(environment):
    from datetime import datetime, timezone

    service, scope = environment('evidence')
    data = parser_package()['assets'][0]['data']
    with scope() as db:
        db.add(m.ParserTemplate(id='manual', **data, asset_path='unused', sha256=hashlib.sha256(TEMPLATE.encode()).hexdigest(), status='active'))
        db.add(m.PatrolRun(id='run-1', mode='production', source='test', logical_run_id='run-1'))
        db.flush()
        for observed, text in ((1, 'Version 8.1\n'), (2, 'Version 8.2 from 10.20.30.40\n')):
            db.add(m.CommandSnapshot(
                id=f'snapshot-{observed}', run_id='run-1', device_id='d', execution_item_id='e', device_name='d',
                command='show version', canonical_command='show version', parser_template_id='manual',
                observed_at=datetime(2026, 10, observed, tzinfo=timezone.utc), raw_text=text, raw_text_hash=str(observed),
                parse_status='parsed'))

    exported = service.export(['parser:manual'], include_samples=True, actor='tester')

    samples = unpack(base64.b64decode(exported['content_base64']))['samples']
    assert exported['sample_count'] == len(samples) == 1
    # The newest output, with its address replaced by one of the same shape, parsed as the template parses it.
    assert '10.20.30.40' not in samples[0]['text'] and samples[0]['text'].startswith('Version 8.2 from ')
    assert samples[0]['expected'] == parse_sample(TEMPLATE, samples[0]['text'])
    assert service.export(['parser:manual'], actor='tester')['sample_count'] == 0


def test_topology_system_items_stay_out_of_exports_and_an_old_package_with_them_still_imports(environment):
    service, scope = environment('system')
    contract = {'entity_type': 'security_zone_member', 'identity_dimensions': ['ZONE', 'INTERFACE'], 'observations': []}
    with scope() as db:
        db.add(m.CheckItemRecord(id='topology.security_zones', name='安全域成员', category='topology', status='active', current_version=1))
        db.flush()
        # Every field is a key: the membership is the fact, so there is nothing to observe. Seeded, never validated.
        db.add(m.CheckItemVersionRecord(id='zones-v1', check_item_id='topology.security_zones', version=1, result_mode='structured',
                                        assessment_mode='evidence', canonical_contract_json=contract, report_projection='auto@1',
                                        status='active', digest='z' * 64))
        db.add(m.ParserTemplate(id='user-parser', **parser_package()['assets'][0]['data'], asset_path='unused',
                                sha256=hashlib.sha256(TEMPLATE.encode()).hexdigest(), status='active'))

    exported = unpack(base64.b64decode(service.export([], all_active=True, actor='tester')['content_base64']))
    assert {a['kind'] for a in exported['assets']} == {'parser'}
    assert service.catalog('check_version')['items'] == [] and len(service.catalog('parser')['items']) == 1
    with pytest.raises(ValueError, match='平台自带'):
        service.export(['check_version:zones-v1'], actor='tester')
    # A package exported before this, carrying the system item as this environment has it.
    with scope() as db:
        g = Graph(db); root = g.add('check_version', 'zones-v1')['$ref']; old = pack(g.close(), [root])
    assert set(service.preview(old)['counts']) == {'reuse'}


def test_same_version_conflict_requires_confirmation_and_preserves_active(environment):
    service, scope = environment('conflict')
    raw = encode(parser_package())
    r = service.execute(raw, service.preview(raw)['revision'], actor='tester')
    with scope() as db:
        db.get(m.ParserTemplate, r['items'][0]['local_id']).status = 'active'
    changed = encode(parser_package(TEMPLATE.replace('Version', 'Software')))
    p = service.preview(changed)
    assert p['requires_confirmation'] and p['items'][0]['same_version_conflict']
    with pytest.raises(TransferConflict, match='确认'):
        service.execute(changed, p['revision'], actor='tester')
    service.execute(changed, p['revision'], actor='tester', confirm_conflicts=True)
    with scope() as db:
        assert db.query(m.ParserTemplate).filter_by(status='active').count() == 1
        assert db.query(m.ParserTemplate).filter_by(status='draft').count() == 1


def test_stale_preview_rejected(environment):
    service, scope = environment('stale')
    raw = encode(parser_package())
    p = service.preview(raw)
    with scope() as db:
        db.add(m.ParserTemplate(id='other', **parser_package()['assets'][0]['data'], asset_path='unused', sha256=hashlib.sha256(TEMPLATE.encode()).hexdigest(), status='active'))
    with pytest.raises(TransferConflict, match='重新预检'):
        service.execute(raw, p['revision'], actor='tester')


def test_failed_transaction_rolls_back_all_assets(environment, monkeypatch):
    service, scope = environment('rollback')
    p = parser_package()
    second = copy.deepcopy(p['assets'][0]); second['ref'] = 'parser:second'; second['data']['canonical_command'] = 'show other'
    p['assets'].append(second)
    raw = encode(p); preview = service.preview(raw)
    original = service.insert
    def fail(db, item, *args):
        if item['ref'] == 'parser:second':
            raise ValueError('injected failure')
        original(db, item, *args)
    monkeypatch.setattr(service, 'insert', fail)
    with pytest.raises(ValueError, match='injected'):
        service.execute(raw, preview['revision'], actor='tester')
    with scope() as db:
        assert db.query(m.ParserTemplate).count() == 0
        assert db.query(m.AuditLogRecord).count() == 0


@pytest.mark.parametrize('mutation', ['unknown_field', 'missing_dependency', 'bad_template', 'bad_sample', 'bad_type'])
def test_invalid_package_prevents_writes(environment, mutation):
    service, scope = environment(mutation)
    p = parser_package()
    if mutation == 'unknown_field': p['assets'][0]['data']['status'] = 'active'
    if mutation == 'missing_dependency': p['assets'][0]['data']['family'] = {'$ref': 'missing'}
    if mutation == 'bad_template': p['assets'][0]['data']['template_text'] = 'invalid'
    if mutation == 'bad_type': p['assets'][0]['data']['vendor'] = 123
    if mutation == 'bad_sample': p['samples'] = [{'parser': {'$ref': p['roots'][0]}, 'text': 'Version 8.2', 'expected': []}]
    with pytest.raises(ValueError):
        service.preview(encode(p))
    with scope() as db:
        assert db.query(m.ParserTemplate).count() == 0


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'assets\\escape', 'C:evil', 'manifest.json'])
def test_reject_unsafe_or_duplicate_zip(name):
    raw = encode(parser_package()); out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, 'w') as target:
        for path in source.namelist(): target.writestr(path, source.read(path))
        target.writestr(name, b'bad')
    with pytest.raises(ValueError): unpack(out.getvalue())


def test_checksum_mismatch_rejected():
    raw = encode(parser_package()); out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, 'w') as target:
        for path in source.namelist(): target.writestr(path, b'corrupt' if path.endswith('.textfsm') else source.read(path))
    with pytest.raises(ValueError, match='校验失败'): unpack(out.getvalue())


def test_source_approval_cannot_be_smuggled_in_parser_contract(environment):
    service, _ = environment('approval')
    package = parser_package()
    package['assets'][0]['data']['validation_json']['human_semantic_review'] = {'status': 'passed'}
    with pytest.raises(ValueError, match='审批'):
        service.preview(encode(package))


def test_bounded_validator_runs_real_compiler():
    from src.apps.rule_governance.backend.transfer_service import bounded_validation
    assert bounded_validation(parser_package())[0]['status'] == 'passed'


def test_full_check_dependency_roundtrip(environment):
    from src.apps.rule_governance.backend.check_items.catalog import load_catalog
    source, source_scope = environment('source')
    target, target_scope = environment('target')
    check = load_catalog().check_items[0]
    impl = next(i for i in load_catalog().implementations if check.id in i.check_item_ids)
    with source_scope() as db:
        db.add(m.CheckItemRecord(id=check.id, name=check.name, category=check.category, status='active', current_version=check.version)); db.flush()
        db.add(m.CheckItemVersionRecord(id=check.version_id, check_item_id=check.id, version=check.version, result_mode=check.result_mode, assessment_mode=check.assessment_mode,
            canonical_contract_json=check.canonical_contract, report_projection=check.report_projection, applicability_json=check.applicability, freshness_policy_json=check.freshness_policy,
            empty_result_policy_json=check.empty_result_policy, coverage_policy_json=check.coverage_policy, status='active', digest=check.digest))
        db.add(m.CollectionImplementationRecord(id=impl.id, name=impl.name, status='active')); db.flush()
        db.add(m.ParserTemplate(id='source-parser', **parser_package()['assets'][0]['data'], asset_path='unused', sha256=hashlib.sha256(TEMPLATE.encode()).hexdigest(), status='active')); db.flush()
        db.add(m.CollectionImplementationVersionRecord(id=impl.version_id, implementation_id=impl.id, version=impl.version, collector='cli', match_json=impl.match,
            commands_json=[{'command': c.command, 'priority': c.priority} for c in impl.commands], parser_requirement=impl.parser_requirement,
            adapter_spec_json={'schema_version': 'command-adapter/v2', 'parser': {'template_id': 'source-parser'},
                'outputs': [{'check_item_id': check.id, 'check_item_version_id': check.version_id, 'source_record_set': 'default',
                             'projection': {'fields': {'os_version': {'source': 'VERSION'}}}}]}, selection_priority=100, status='active', digest=impl.digest)); db.flush()
        db.add(m.CheckItemImplementationLinkRecord(id='source-link', check_item_version_id=check.version_id, implementation_version_id=impl.version_id))
        db.add(m.ChangePolicyRecord(id='policy-root', name='test.state', observation_type='device.software.version', status='active')); db.flush()
        db.add(m.ChangePolicyVersionRecord(id='policy-version', policy_id='policy-root', check_item_version_id=check.version_id, version=1,
            observation_type='device.software.version', semantic_type='state', compare_mode='exact', policy_json={'observation_type': 'device.software.version', 'semantic_type': 'state', 'compare_mode': 'exact'}, status='active', digest='x'))
        db.add(m.EventRuleRecord(id='event-root', name='version.changed', status='active')); db.flush()
        db.add(m.EventRuleVersionRecord(id='event-version', rule_id='event-root', check_item_version_id=check.version_id, version=1,
            event_type='VersionChanged', severity='info', condition_json={'kind': 'field_change', 'fields': ['os_version']}, status='active'))
    # Use source graph directly: this is a read-only export, with all refs explicit.
    with source_scope() as db:
        g = Graph(db); root = g.add('check_version', check.version_id)['$ref']; assets = g.close()
        raw = pack(assets, [root])
    assert {'check_version', 'implementation_version', 'parser', 'policy_version', 'event_version', 'link'} <= {a['kind'] for a in unpack(raw)['assets']}
    p = target.preview(raw)
    r = target.execute(raw, p['revision'], actor='tester')
    mapping = {i['ref']: i['local_id'] for i in r['items']}
    actions = {i['ref']: i['action'] for i in r['items']}
    with target_scope() as db:
        imported_impl = db.get(m.CollectionImplementationVersionRecord, mapping['implementation_version:' + impl.version_id])
        assert imported_impl.adapter_spec_json['parser']['template_id'] == mapping['parser:source-parser']
        # Rules are compiled from the check item when it is released; the package's copies are not imported.
        assert actions['event_version:event-version'] == actions['policy_version:policy-version'] == 'derived'
        assert db.query(m.EventRuleVersionRecord).count() == 0 and db.query(m.ChangePolicyVersionRecord).count() == 0
        # The adapter goes into force with the imported check item, so it has no draft of its own.
        changes = db.query(m.CheckItemCatalogChangeRecord).all()
        assert [(c.asset_kind, c.asset_id) for c in changes] == [('check_item', check.id)]
    assert {i['action'] for i in target.preview(raw)['items']} == {'reuse', 'derived'}
    assert 'recipes' in r and all(a['role'] not in {'change_policy', 'event_rule', 'current_state_rule'}
                                  for recipe in r['recipes'] for a in recipe['assets'])
    # Bringing the same file again only replays the import while what it created is still a draft or in force.
    assert target.execute(raw, p['revision'], actor='tester')['replayed']
    with target_scope() as db:
        # Released, then the check item was deleted: the release stays on record, its versions stood down.
        db.query(m.CheckItemCatalogChangeRecord).one().status = 'approved'
        for ref, model in (('check_version:' + check.version_id, m.CheckItemVersionRecord),
                           ('implementation_version:' + impl.version_id, m.CollectionImplementationVersionRecord),
                           ('parser:source-parser', m.ParserTemplate)):
            db.get(model, mapping[ref]).status = 'deprecated'
    again = target.preview(raw)
    assert {i['ref']: i['action'] for i in again['items']}['check_version:' + check.version_id] == 'new_version'
    second = target.execute(raw, again['revision'], actor='tester', confirm_conflicts=True)
    assert not second.get('replayed') and second['record_id'] != r['record_id']
    with target_scope() as db:
        assert db.query(m.CheckItemCatalogChangeRecord).filter_by(status='draft').count() == 1
        assert db.query(m.CheckItemVersionRecord).filter_by(status='draft').count() == 1


def test_package_api_requires_app_permission():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from src.apps.rule_governance.backend.transfer_api import router, app_user
    app = FastAPI(); app.include_router(router)
    def denied(): raise HTTPException(403, 'not granted')
    app.dependency_overrides[app_user] = denied
    client = TestClient(app)
    assert client.get('/rule-packages/assets').status_code == 403
    assert client.post('/rule-packages/preview', json={'content_base64': 'AA=='}).status_code == 403
