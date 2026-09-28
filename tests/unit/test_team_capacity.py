"""Capacity changes retain evidence and ranking; they only reduce work."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.platform.evidence.offline_jobs import (
    _rank_inventory_matches, _inventory_match_index, _indexed_inventory_candidates,
)


def test_indexed_inventory_matching_preserves_ranking_and_duplicate_names():
    inventory = [SimpleNamespace(id=str(i), device_name='switch' if i % 2 else 'other',
                                management_ip='192.0.2.1' if i % 3 else '192.0.2.2')
                 for i in range(100)]
    index = _inventory_match_index(inventory)
    for name, ip in [(' Switch ', '192.0.2.1'), ('missing', '192.0.2.2'), ('', ''), ('other', None)]:
        args = dict(device_name=name, management_ip=ip)
        assert _rank_inventory_matches(inventory, **args) == _rank_inventory_matches(
            _indexed_inventory_candidates(index, **args), **args)


def test_history_is_scoped_but_includes_devices_with_no_current_observations(tmp_path, monkeypatch):
    from src.core.state import repository
    from src.infrastructure.db.models import PatrolRun, PatrolCollectionResult, StateObservationRecord
    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    for table in [PatrolRun, PatrolCollectionResult, StateObservationRecord]:
        table.__table__.create(engine)
    factory = sessionmaker(engine)

    @contextmanager
    def session():
        with factory.begin() as db:
            yield db

    monkeypatch.setattr(repository, 'get_db_session', session)
    now = datetime.now(timezone.utc)
    with session() as db:
        for name, age, eligible in [('current', 0, False), ('old', 2, True), ('invalid', 1, False), ('future', -1, True)]:
            db.add(PatrolRun(id=name, logical_run_id=name, mode='production', source='test',
                             started_at=now - timedelta(days=age), completed_at=now - timedelta(days=age),
                             baseline_eligible=eligible, analysis_status='completed'))
        for run in ['old', 'invalid', 'future']:
            for device in ['selected', 'empty', 'unrelated']:
                key = run + device
                db.add(PatrolCollectionResult(id=key, run_id=run, device_id=device,
                       execution_item_id='cmd', canonical_command='show interface', baseline_eligible=True))
                db.add(StateObservationRecord(id=key, idempotency_key=key, run_id=run, snapshot_id=key,
                       mode='production', source='test', device_id=device, execution_item_id='cmd',
                       canonical_command='show interface', entity_type='interface', entity_key='eth1',
                       field_name='status', value_type='text', value_json='up', observed_at=now-timedelta(days=2)))
        for device in ['selected', 'empty']:
            db.add(PatrolCollectionResult(id='current'+device, run_id='current', device_id=device,
                   execution_item_id='cmd', canonical_command='show interface'))
    result = repository.StateRepository().list_prior_observations_for_run('current')
    assert {r['id'] for r in result} == {'oldselected', 'oldempty'}
    engine.dispose()


def test_domain_grouping_keeps_fault_recovery_and_partial_coverage():
    from src.apps.inspection.backend.patrol_batch_service import PatrolBatchService
    result = PatrolBatchService.domain_checks(
        observations=[dict(entity_type='interface', entity_key='eth1', field='status', value='down'),
                      dict(entity_type='cpu', entity_key='1', field='usage', value=81)],
        command_details=[], changes=[dict(entity_type='interface', field='status')],
        events=[dict(entity_type='interface', event_class='fault', status_after_occurrence='open',
                     entity_key='eth1', field='status', event_type='interface.down')],
        current_findings=[], rule_coverage={'by_domain': {'cpu': {'threshold_unknown_observation_count': 1}}},
    )
    by_domain = {r['domain']: r for r in result}
    assert by_domain['interface']['current_risk_count'] == 1
    assert by_domain['interface']['change_count'] == 1
    assert by_domain['cpu']['status'] == '数据不完整'


def test_scoped_discovery_still_detects_content_changes(monkeypatch):
    from src.platform.kernel import registry
    full = registry.discover()
    real_digest = registry.package_digest
    calls = []
    def fingerprint(path):
        calls.append(path)
        return real_digest(path)
    monkeypatch.setattr(registry, 'package_digest', fingerprint)
    scoped = registry.discover(app_id='inspection')
    assert scoped['inspection'] == full['inspection']
    assert len(calls) == len(scoped)
    assert len(scoped) < len(full)
    assert registry.discover(app_id='missing') == {}


def test_pool_budget_is_bounded_and_invalid_values_rejected():
    import pytest
    from pydantic import ValidationError
    from src.common.config import Settings
    from src.infrastructure.db.postgres import engine, async_engine
    for engine_ in [engine, async_engine]:
        assert engine_.pool.size() > 0
        assert engine_.pool._max_overflow >= 0
        assert engine_.pool.timeout() > 0
    with pytest.raises(ValidationError):
        Settings(POSTGRES_MAX_OVERFLOW=-1)


def test_history_listing_never_loads_completed_artifacts(tmp_path, monkeypatch):
    from sqlalchemy import event
    from src.infrastructure.db.models import OfflinePatrolJobRecord as Job
    from src.platform.evidence import offline_jobs
    engine = create_engine(f"sqlite:///{tmp_path / 'list.db'}")
    Job.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    @contextmanager
    def session():
        with factory.begin() as db:
            yield db
    monkeypatch.setattr(offline_jobs, 'get_db_session', session)
    with session() as db:
        for i in range(20):
            db.add(Job(id=str(i), idempotency_key=str(i), purpose='offline_patrol',
                       job_type='history_compare', name='history', owner_id='owner',
                       status='completed', workflow_run_id='old-'+str(i),
                       result_json={'large': 'x' * 10000}, artifacts_json={}))
    queries = []
    event.listen(engine, 'before_cursor_execute', lambda conn, cursor, statement, *args: queries.append(statement))
    result = offline_jobs.OfflinePatrolJobService().list_jobs(owner_id='owner', page_size=20)
    assert result['total'] == 20
    assert len(queries) == 2
    # COUNT wraps the query's columns; the actual list must exclude heavy JSON.
    assert 'result_json' not in queries[-1] and 'artifacts_json' not in queries[-1]
    engine.dispose()
