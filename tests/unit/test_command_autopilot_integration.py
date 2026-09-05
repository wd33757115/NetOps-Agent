"""Real repositories and runtime assembly, isolated from the installed database."""
import copy
import inspect
import sys
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.infrastructure.db import postgres
from src.infrastructure.db.models import Base, RuleAssetCommandUnitRecord, CheckItemVersionRecord
from src.core.check_items.catalog import CheckItemCatalogRepository, load_catalog
from src.core.check_items.identity import EntityIdentityPolicyRepository, load_entity_identity_policy
from src.core.rule_control.command_autopilot import CommandAutopilotService, task_key
from src.core.rule_control import command_autopilot_steps, command_autopilot_release


@pytest.fixture
def isolated_pipeline(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline.db'}")
    Base.metadata.create_all(engine)
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
    original = postgres.get_db_session
    # Replace imported session factories and captured constructor defaults.
    # No runtime code can reach the user's configured PostgreSQL in this fixture.
    for module in list(sys.modules.values()):
        if not module or not getattr(module, '__name__', '').startswith('src.'):
            continue
        for name, value in list(vars(module).items()):
            if value is original:
                monkeypatch.setattr(module, name, scope)
            functions = [value] if inspect.isfunction(value) else [value.__init__] if inspect.isclass(value) else []
            for function in functions:
                if not inspect.isfunction(function):
                    continue
                defaults = function.__defaults__
                if defaults and any(x is original for x in defaults):
                    monkeypatch.setattr(function, '__defaults__', tuple(scope if x is original else x for x in defaults))
                defaults = function.__kwdefaults__
                if defaults and any(x is original for x in defaults.values()):
                    monkeypatch.setattr(function, '__kwdefaults__', {k: scope if v is original else v for k, v in defaults.items()})
    catalog = CheckItemCatalogRepository(session_scope=scope)
    catalog.bootstrap(load_catalog())
    EntityIdentityPolicyRepository(session_scope=scope).bootstrap(load_entity_identity_policy())
    yield scope, catalog
    engine.dispose()


def test_evidence_task_reaches_active_runtime_without_human_asset_handoffs(isolated_pipeline):
    scope, catalog = isolated_pipeline
    item = next(x for x in catalog.load_active().check_items if x.id == 'device.version')
    with scope() as db:
        version = db.get(CheckItemVersionRecord, item.version_id)
        version.result_mode = 'evidence_only'
        version.canonical_contract_json = {}
    item = next(x for x in catalog.load_active().check_items if x.id == 'device.version')
    with scope() as db:
        db.add(RuleAssetCommandUnitRecord(id='unit', offline_job_id='source', asset_key='asset', vendor='test-vendor',
            profile='test-model', canonical_command='show evidence', handling='evidence', asset_mode='observation_only',
            status='ready', current_stage='parser'))
    inputs = {'unit': {'asset_key': 'asset', 'vendor': 'test-vendor', 'models': ['test-model'], 'canonical_command': 'show evidence'},
              'check_item_id': item.id, 'check_item_version_id': item.version_id, 'check_item_digest': item.digest,
              'result_mode': item.result_mode}
    original_contract = copy.deepcopy(item.canonical_contract)
    service = CommandAutopilotService(session_scope=scope, dispatch=Mock())
    service._inputs = lambda *_: inputs
    service.control('source', 'unit', action='start', actor='admin', authorize_publish=True)
    for _ in range(6):
        result = service.advance(task_key('source', 'unit'))
        if result['status'] != 'queued':
            break
    assert result['status'] == 'completed', result.get('error')
    assert result['outputs']['runtime']['status'] == 'active'
    assert next(x for x in catalog.load_active().check_items if x.id == item.id).canonical_contract == original_contract


@pytest.mark.parametrize('parser_status', ['active', 'pending_approval'])
def test_structured_task_validates_real_cli_and_publishes_rules_and_runtime(isolated_pipeline, monkeypatch, parser_status):
    from src.infrastructure.db.models import ParserTemplate
    from src.core.parser.repository import ParserJobRepository, ParserSampleRepository
    import hashlib
    scope, catalog = isolated_pipeline
    item = next(x for x in catalog.load_active().check_items if x.id == 'interface.oper_status')
    template_text = 'Value interface (\\S+)\nValue oper_status (up|down)\nValue admin_status (up|down)\n\nStart\n  ^${interface} ${oper_status} ${admin_status}$$ -> Record\n'
    with scope() as db:
        db.add(RuleAssetCommandUnitRecord(id='unit', offline_job_id='source', asset_key='asset', vendor='test-vendor',
            profile='test-model', canonical_command='show ports', handling='structured', asset_mode='full_chain',
            status='ready', current_stage='parser'))
        db.add(ParserTemplate(id='parser', vendor='test-vendor', model='test-model', profile='test-model',
            canonical_command='show ports', status=parser_status, version='1', template_text=template_text,
            asset_path='test.textfsm', category='interface', sha256=hashlib.sha256(template_text.encode()).hexdigest(),
            validation_json={'primary_keys': ['interface'], 'compatibility': {'approved': True, 'scope': 'model', 'vendor': 'test-vendor', 'models': ['test-model']},
                             'parser_spec': {'fields': {x: {'type': 'string'} for x in ['interface', 'oper_status', 'admin_status']}}}))
    samples = [{'id': 'sample-1', 'sample_role': 'generation', 'sanitization_status': 'sanitized',
                'sanitized_hash': 'sample-hash', 'sanitized_text': 'Gi1/0/1 up up\nGi1/0/2 down up\n'}]
    monkeypatch.setattr(ParserJobRepository, 'get_job', lambda *_a, **_k: {'id': 'job', 'status': 'succeeded', 'parser_spec_status': 'confirmed', 'parser_spec_sha256': 'spec', 'samples': samples})
    monkeypatch.setattr(ParserSampleRepository, 'sanitize_pending_samples', lambda *_a, **_k: None)
    inputs = {'unit': {'asset_key': 'asset', 'vendor': 'test-vendor', 'models': ['test-model'], 'canonical_command': 'show ports',
                       'parser_job_id': 'job', 'parser_template_id': 'parser'},
              'check_item_id': item.id, 'check_item_version_id': item.version_id, 'check_item_digest': item.digest, 'result_mode': item.result_mode}
    service = CommandAutopilotService(session_scope=scope, dispatch=Mock())
    service._inputs = lambda *_: inputs
    service.control('source', 'unit', action='start', actor='admin', authorize_publish=True)
    for _ in range(7):
        result = service.advance(task_key('source', 'unit'))
        if result['status'] != 'queued':
            break
    assert result['status'] == 'completed', result.get('error')
    assert result['outputs']['runtime']['status'] == 'active'
    assert result['outputs']['projection']['sample_reports']
    assert result['outputs']['rules']['bundle_id']
