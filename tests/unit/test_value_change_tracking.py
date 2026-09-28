"""Regression across projection, stored Changes, and independent numeric health."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items.adapter_projection import project_adapter_batches_to_observations
from src.core.check_items.command_adapter import ObservationBatch
from src.core.check_items.value_contract import normalize_value_contract
from src.core.patrol import change_detector as detector
from src.core.patrol import change_event_repository
from src.apps.inspection.backend.patrol_batch_service import evaluate_current_state
from src.infrastructure.db.models import NetworkChangeDecisionRecord, NetworkChangeRecord


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'changes.db'}")
    for model in (NetworkChangeRecord, NetworkChangeDecisionRecord):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    @contextmanager
    def session():
        with factory.begin() as db:
            yield db
    monkeypatch.setattr(change_event_repository, 'get_db_session', session)
    monkeypatch.setattr(detector.PatrolRepository, 'get_run', lambda *_: {})
    monkeypatch.setattr(detector.PatrolRepository, 'list_collection_results', lambda *_: [])
    return session


def projected(field, value, period, tracking, policy=None):
    row = {'canonical_field': field, 'observation_type': 'sample.' + field,
           'value_type': 'text' if isinstance(value, str) else 'number',
           'change_tracking': tracking, 'unit': 'dbm' if field == 'rx_power_dbm' else 'percent',
           'semantic_type': 'state' if isinstance(value, str) else 'gauge'}
    if policy:
        row['health_policy'] = policy
    item = SimpleNamespace(id='sample', version_id='v2', version=2,
                           canonical_contract={'entity_type': 'device', 'identity_dimensions': ['device_id'], 'observations': [row]})
    timestamp = '2026-09-01T00:00:00+00:00' if period == 'prior' else '2026-09-02T00:00:00+00:00'
    batch = ObservationBatch('device', 'sample', timestamp, 'complete', 'adapter', 'v2', ({field: value},), {})
    obs = project_adapter_batches_to_observations([batch], check_items={'sample': item},
        snapshot={'id': period, 'run_id': period, 'device_id': 'device', 'canonical_command': 'show sample', 'observed_at': timestamp},
        mode='offline', source='test', dataset_id='sample', command_adapter_id='adapter', command_adapter_version_id='adapter-v2')[0]
    return {**obs.to_dict(), 'value_json': obs.value, 'metadata_json': obs.metadata, 'dimensions_json': obs.dimensions}


LOAD = {'baseline_mode': 'trend_only', 'device_thresholds': {'high': ['high']},
        'trend': {'kind': 'utilization_increase', 'threshold_ratio': .1, 'min_increase_points': 10}}
OPTIC = {'baseline_mode': 'trend_only', 'device_thresholds': {'high': ['high'], 'low': ['low']},
         'trend': {'kind': 'linear_power_drop', 'threshold_ratio': .1}}


@pytest.mark.parametrize('field,old,new,tracking,policy,health,notice', [
    ('input_packets', 100, 200, False, None, None, False),
    ('voltage_volts', 11.9, 12.1, False, None, None, False),
    ('line_status', 'UP', 'DOWN', True, None, None, False),
    ('cpu_usage_percent', 20, 21, False, LOAD, 'healthy', False),
    ('cpu_usage_percent', 20, 30, False, LOAD, 'healthy', True),
    ('cpu_usage_percent', 70, 81, False, LOAD, 'abnormal', False),
    ('rx_power_dbm', -10, -11, False, OPTIC, 'healthy', True),
    ('rx_power_dbm', -10, -21, False, OPTIC, 'abnormal', False),
])
def test_project_compare_persist_and_health(store, field, old, new, tracking, policy, health, notice):
    previous = projected(field, old, 'prior', True, policy)
    current = projected(field, new, 'current', tracking, policy)
    saved = deepcopy(current)
    policies = [{'observation_type': 'sample.' + field, 'policy_id': 'p', 'version_id': 'pv',
                 'policy': {'observation_type': 'sample.' + field, 'semantic_type': 'state', 'compare_mode': 'exact'}}]
    result = detector._detect_observation_changes_against_history_impl(current_run_id='current',
        current_observations=[current], prior_observations=[previous], policy_bundle=policies, persist=True)
    assert current == saved  # comparison never removes or rewrites the StateObservation
    assert result['change_count'] == int(tracking)
    with store() as db:
        assert db.query(NetworkChangeRecord).count() == int(tracking)
    if not tracking:
        assert all(result[key] == 0 for key in ('added_observations', 'deleted_observations', 'missing_observations', 'reappeared_observations'))
    if policy:
        context = {'high': 0 if policy == OPTIC else 80, 'low': -20}
        current['field'] = field
        current['command'] = current['canonical_command']
        current['context'] = {**context, 'baseline_value': old, 'baseline_observation_id': previous['id'],
                              'baseline_unit': current['unit'], 'baseline_context': context}
        evaluation = evaluate_current_state([current], [], rule_source='test')
        assert evaluation['finding_count'] == int(health == 'abnormal')
        assessments = evaluation['assessment_basis']['entries']
        assert assessments[0]['health_assessment']['status'] == health
        assert (assessments[0]['health_assessment']['trend']['status'] == 'notice') == notice


def test_switch_on_uses_current_definition_not_baseline_flag(store):
    previous = projected('status', 'UP', 'prior', False)
    current = projected('status', 'DOWN', 'current', True)
    result = detector._detect_observation_changes_against_history_impl(current_run_id='current', persist=False,
        current_observations=[current], prior_observations=[previous],
        policy_bundle=[{'observation_type': 'sample.status', 'version_id': 'pv', 'policy': {
            'observation_type': 'sample.status', 'semantic_type': 'state', 'compare_mode': 'exact'}}])
    assert result['change_count'] == 1
    assert result['changes'][0]['change_type'] == 'modified'


@pytest.mark.parametrize('confirmations', [1, 2])
def test_absent_disabled_field_does_not_create_missing_or_deleted_change(store, confirmations):
    previous = projected('input_packets', 100, 'prior', True)
    result = detector._detect_observation_changes_against_history_impl(current_run_id='current', persist=False,
        current_observations=[], prior_observations=[previous], policy_bundle=[], include_internal=True,
        change_tracking={'sample:sample.input_packets': False}, missing_confirmations=confirmations,
        collection_results=[{'device_id': 'device', 'canonical_command': 'show sample',
                             'authoritative_for_deletion': True, 'metadata_json': {'snapshot_id': 'current'}}])
    assert result['change_count'] == result['deleted_observations'] == result['missing_observations'] == 0
    # Presence evidence survives for independent health/data completeness assessment.
    assert len(result['_derived_observations']) == 1
    assert result['change_tracking_disabled_count'] == 1


def test_default_revision_preserves_error_counters_and_health():
    from scripts.check_items.revise_change_tracking import disable_default
    assert disable_default('interface.runtime_statistics', {'canonical_field': 'input_packets'})
    assert not disable_default('interface.runtime_statistics', {'canonical_field': 'input_errors', 'semantic_type': 'counter'})
    assert not disable_default('environment.power', {'canonical_field': 'power_status', 'semantic_type': 'state'})
    assert not disable_default('environment.power', {'canonical_field': 'voltage', 'semantic_type': 'gauge', 'health_policy': LOAD})


@pytest.mark.parametrize('previous', [[], [projected('status', 'UP', 'prior', True)]])
def test_missing_policy_never_falls_back_to_exact_or_presence(store, previous):
    current = projected('status', 'DOWN', 'current', True)
    result = detector._detect_observation_changes_against_history_impl(current_run_id='current', persist=True,
        current_observations=[current], prior_observations=previous, policy_bundle=[])
    assert result['change_count'] == 0
    assert result['change_policy_missing_count'] == 1


@pytest.mark.parametrize('value', ['false', 0, None])
def test_flag_requires_boolean_even_without_change_events(value):
    with pytest.raises(ValueError, match='change_tracking must be boolean'):
        normalize_value_contract({'observations': [{'canonical_field': 'field', 'change_tracking': value}]})


def test_default_flag_does_not_rewrite_immutable_legacy_contract():
    contract = {'observations': [{'canonical_field': 'status', 'observation_type': 'device.state'}]}
    assert normalize_value_contract(contract) == contract


def test_disable_preserves_health_recovery_and_thresholds():
    row = {'canonical_field': 'state', 'observation_type': 'power.state', 'change_tracking': False,
           'assessment_roles': ['change', 'current_state'], 'health_policy': LOAD,
           'threshold_references': {'high': {'field_name': 'high'}},
           'event_constraints': {'required_event_types': ['changed', 'fault', 'recovered'],
             'role_event_types': {'change': 'changed', 'fault': 'fault'},
             'required_event_classes': {'changed': 'change', 'fault': 'fault', 'recovered': 'recovery'},
             'required_recovery_pairs': [{'fault': 'fault', 'recovery': 'recovered'}]}}
    normalized = normalize_value_contract({'change_events': True, 'observations': [row]})['observations'][0]
    assert normalized['assessment_roles'] == ['current_state']
    assert normalized['health_policy'] == LOAD
    assert normalized['threshold_references'] == row['threshold_references']
    assert normalized['event_constraints']['required_event_types'] == ['fault', 'recovered']
    assert normalized['event_constraints']['required_recovery_pairs'] == row['event_constraints']['required_recovery_pairs']

