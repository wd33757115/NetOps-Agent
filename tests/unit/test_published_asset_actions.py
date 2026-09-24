from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from src.apps.rule_governance.backend.published_assets import PublishedAssetService
from src.infrastructure.db.models import (
    Base, AuditLogRecord, ParserTemplate,
    ChangePolicyRecord, ChangePolicyVersionRecord, EventRuleRecord, EventRuleVersionRecord,
    CheckItemRuntimeBundleRecord, CheckItemRuntimeBundleVersionRecord, RuntimeBundleAssetRecord,
    RuleGenerationJobRecord,
)


@pytest.fixture
def environment(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "assets.db"}')
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        AuditLogRecord, ParserTemplate, ChangePolicyRecord,
        ChangePolicyVersionRecord, EventRuleRecord, EventRuleVersionRecord,
        CheckItemRuntimeBundleRecord, CheckItemRuntimeBundleVersionRecord,
        RuntimeBundleAssetRecord, RuleGenerationJobRecord,
    )])
    factory = sessionmaker(bind=engine)

    @contextmanager
    def scope():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    with scope() as db:
        db.add(ParserTemplate(id='parser', vendor='H3C', profile='test', canonical_command='display test',
                              category='test', version='1', asset_path='test.textfsm', sha256='a'*64, status='active'))
        db.add(ChangePolicyRecord(id='policy-root', name='policy', observation_type='test', status='active', current_version=1))
        db.add(ChangePolicyVersionRecord(id='policy', policy_id='policy-root', version=1, observation_type='test',
                                        semantic_type='state', compare_mode='value', policy_json={}, digest='c'*64, status='active'))
        db.add(EventRuleRecord(id='event-root', name='event', status='active', current_version=1))
        db.add(EventRuleVersionRecord(id='event', rule_id='event-root', version=1, event_type='test', severity='warning',
                                      condition_json={}, digest='d'*64, status='active'))
        for kind, typ in [('parser','parser_template'),
                          ('policy','change_policy'), ('event','event_rule')]:
            db.add(CheckItemRuntimeBundleRecord(id=f'root-{kind}', implementation_id=f'impl-{kind}',
                                                check_item_id='check', name=kind, status='active', current_version=1))
            db.add(CheckItemRuntimeBundleVersionRecord(id=f'bundle-{kind}', bundle_id=f'root-{kind}', version=1,
                check_item_version_id='check-v1', implementation_version_id=f'impl-{kind}-v1', parser_bundle_json={'frozen': True},
                canonical_catalog_version=1, canonical_catalog_digest='e'*64, entity_identity_policy_version_id='identity',
                dependency_digests_json={}, compatibility_json={}, validation_report_json={}, digest=kind*16, status='active'))
            db.add(RuntimeBundleAssetRecord(id=f'asset-{kind}', bundle_version_id=f'bundle-{kind}',
                asset_type=typ, asset_id=kind, asset_version_id=kind, asset_digest='a'*64, required=True, metadata_json={}))
    return PublishedAssetService(scope), scope, factory


@pytest.mark.parametrize('kind', ['parser','policy','event'])
@pytest.mark.parametrize('action', ['deprecate','delete'])
def test_bulk_retires_only_affected_bundles_and_preserves_content(environment, kind, action):
    service, scope, _ = environment
    result = service.bulk(kind=kind, action=action, ids=[kind, 'missing', kind], actor='operator')
    assert result['succeeded'] == result['failed'] == 1
    assert len(result['results']) == 2
    with scope() as db:
        model = {'parser':ParserTemplate,
                 'policy':ChangePolicyVersionRecord,'event':EventRuleVersionRecord}[kind]
        assert db.get(model, kind).status == ('deleted' if action == 'delete' else 'deprecated')
        affected = {kind}
        for other in ('parser','policy','event'):
            bundle = db.get(CheckItemRuntimeBundleVersionRecord, f'bundle-{other}')
            assert bundle.status == ('deprecated' if other in affected else 'active')
            assert bundle.parser_bundle_json == {'frozen': True}
            assert bundle.digest == other*16
        assert db.get(ParserTemplate,'parser').sha256 == 'a'*64
        if kind in {'policy','event'}:
            root = db.get(ChangePolicyRecord if kind == 'policy' else EventRuleRecord, f'{kind}-root')
            assert root.status == 'deprecated' and root.current_version == 0
        audit = db.query(AuditLogRecord).one()
        assert audit.username == 'operator'
        assert set(audit.detail['retired_runtime_bundle_version_ids']) == {f'bundle-{x}' for x in affected}
    assert service.bulk(kind=kind, action=action, ids=[kind], actor='operator')['succeeded'] == 1
    with scope() as db:
        assert db.query(AuditLogRecord).count() == 1


def test_delete_disabled_asset_cannot_be_stopped_again(environment):
    service, scope, _ = environment
    for action in ('deprecate', 'delete'):
        assert service.bulk(kind='policy',action=action,ids=['policy'],actor='operator')['succeeded']==1
    assert service.bulk(kind='policy',action='deprecate',ids=['policy'],actor='operator')['failed']==1

def test_failed_audit_rolls_back_asset_and_bundle_together(environment):
    service, scope, factory = environment
    def fail(db, *args):
        if any(isinstance(row, AuditLogRecord) for row in db.new):
            raise SQLAlchemyError('audit failure')
    event.listen(factory, 'before_flush', fail)
    try:
        result = service.bulk(kind='policy', action='delete', ids=['policy'], actor='operator')
        assert result['failed']==1
    finally:
        event.remove(factory, 'before_flush', fail)
    with scope() as db:
        assert db.get(ChangePolicyVersionRecord,'policy').status=='active'
        assert db.get(CheckItemRuntimeBundleVersionRecord,'bundle-policy').status=='active'
        assert db.get(ChangePolicyRecord,'policy-root').current_version==1


def test_rejects_invalid_or_unbounded_batches(environment):
    service, _, _ = environment
    for ids in ([], [''] , ['x']*101):
        with pytest.raises(ValueError):
            service.bulk(kind='parser',action='delete',ids=ids,actor='operator')


def test_deleted_assets_cannot_be_restored_by_legacy_status_endpoints(environment, monkeypatch):
    from src.core.patrol import event_rules
    from src.apps.rule_governance.backend.parser import repository as parsers
    service, scope, _ = environment
    for module in (event_rules, parsers):
        monkeypatch.setattr(module, 'get_db_session', scope)
    for kind in ('parser','event'):
        assert service.bulk(kind=kind,action='delete',ids=[kind],actor='operator')['succeeded']==1
    for method in ('force_activate_rule_version','deprecate_rule_version'):
        with pytest.raises(ValueError, match='已删除'):
            getattr(event_rules.EventRuleRepository(), method)('event',actor='operator',reason='test')
    with pytest.raises(ValueError, match='已删除'):
        parsers.ParserRepository().set_template_status('parser','deprecated',actor='operator',reason='test')


def test_stopping_last_event_rule_does_not_restore_builtin_rules(environment, monkeypatch):
    from src.core.patrol import event_rules
    service, scope, _ = environment
    monkeypatch.setattr(event_rules, 'get_db_session', scope)
    service.bulk(kind='event',action='deprecate',ids=['event'],actor='operator')
    assert event_rules.EventRuleRepository().snapshot_active_rules()==[]
