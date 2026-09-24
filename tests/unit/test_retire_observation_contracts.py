import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
import sqlalchemy as sa

from src.infrastructure.db.models import Base
from src.apps.rule_governance.backend.generators.runner import generator_metadata, GeneratorExecutionError
from src.apps.rule_governance.backend.rule_control.repository import RuleGenerationJobRepository


def test_removed_contract_has_no_model_endpoint_generator_or_job_entry():
    from src.apps.rule_governance.backend import rule_control
    assert not hasattr(rule_control, 'RuleAssetTemplateActivationService')
    assert not hasattr(rule_control, 'RuleAssetSubmissionService')
    for name in rule_control.__all__:
        assert getattr(rule_control, name) is not None
    from src.apps.rule_governance.backend.router import router
    assert 'netops_observation_contracts' not in Base.metadata.tables
    paths = {route.path for route in router.routes}
    for path in paths:
        assert not any(part in path for part in ['observation-contracts', '/start-generation', '/generation-context', '/generation-runs', '/runtime-assets/resolve', '/runtime-assets/health', '/engineering-revision', '/execute-revision', '/rule-generation-jobs/{job_id}/submit', '/rule-generation-jobs/{job_id}/revise'])
    assert any(path.endswith('/rule-context') for path in paths)
    assert any(path.endswith('/check-item-runtime-bundles/assemble') for path in paths)
    with pytest.raises(GeneratorExecutionError, match='unknown'):
        generator_metadata('observation-contract-generator')
    with pytest.raises(ValueError, match='unsupported'):
        RuleGenerationJobRepository().create_job(job_type='OBSERVATION_CONTRACT')


@pytest.mark.parametrize('active_reference', [False, True])
def test_retirement_migration_preserves_facts_and_blocks_active_dependency(active_reference):
    engine = sa.create_engine('sqlite://')
    metadata = sa.MetaData()
    contracts = sa.Table('netops_observation_contracts', metadata, sa.Column('id', sa.String, primary_key=True))
    versions = sa.Table('netops_check_item_runtime_bundle_versions', metadata, sa.Column('id', sa.String, primary_key=True), sa.Column('status', sa.String))
    assets = sa.Table('netops_runtime_bundle_assets', metadata, sa.Column('bundle_version_id', sa.String), sa.Column('asset_type', sa.String))
    facts = sa.Table('netops_state_observations', metadata, sa.Column('id', sa.String), sa.Column('value', sa.String))
    jobs = sa.Table('netops_rule_generation_jobs', metadata, sa.Column('id', sa.String, primary_key=True), sa.Column('job_type', sa.String), sa.Column('status', sa.String), sa.Column('payload_json', sa.JSON))
    metadata.create_all(engine)
    path = Path(__file__).resolve().parents[2] / 'migrations/versions/0103_retire_observation_contracts.py'
    spec = importlib.util.spec_from_file_location('retirement_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as db:
        db.execute(contracts.insert(), {'id':'legacy'})
        db.execute(versions.insert(), {'id':'bundle', 'status':'active' if active_reference else 'deprecated'})
        db.execute(assets.insert(), {'bundle_version_id':'bundle','asset_type':'observation_contract'})
        db.execute(facts.insert(), {'id':'evidence', 'value':'unchanged'})
        db.execute(jobs.insert(), [
            {'id':'old','job_type':'OBSERVATION_CONTRACT','status':'draft','payload_json':{}},
            {'id':'parent','job_type':'RULE_ASSET_BUNDLE','status':'pending_approval','payload_json':{'child_job_ids':['old']}},
            {'id':'current','job_type':'RULE_ASSET_BUNDLE','status':'active','payload_json':{'flow':'check_item_semantics'}},
        ])
        module.op = SimpleNamespace(get_bind=lambda:db, execute=db.execute)
        if active_reference:
            with pytest.raises(RuntimeError, match='V2'):
                module.upgrade()
            assert sa.inspect(db).has_table('netops_observation_contracts')
        else:
            module.upgrade()
            assert not sa.inspect(db).has_table('netops_observation_contracts')
            assert dict(db.execute(sa.select(jobs.c.id,jobs.c.status)).all()) == {'old':'deprecated','parent':'deprecated','current':'active'}
        assert db.execute(sa.select(facts.c.value)).scalar_one() == 'unchanged'
