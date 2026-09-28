"""Batch writes preserve fact idempotency, NULLs and current-state ordering."""
from dataclasses import replace

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from src.core.state.repository import StateRepository
from src.infrastructure.db.models import CurrentStateRecord, StateObservationRecord
from tests.unit.test_state_normalizer import _state_observation


def test_summary_normalization_does_not_serialize_discarded_observations(monkeypatch):
    from types import SimpleNamespace
    from src.core.state import normalizer
    from src.core.state.domain import StateObservation

    snapshot = dict(id='s', run_id='run', device_id='device', vendor='H3C', model='test',
                    canonical_command='display current-configuration',
                    observed_at='2026-09-27T00:00:00+00:00', parse_status='raw_hashed',
                    raw_text_hash='a' * 64, metadata_json={'handling': 'raw_hash_diff'})
    store = SimpleNamespace(get_snapshot_context=lambda _: dict(snapshot=snapshot,
                            mode='production', source='test', dataset_id=None))
    expected, facts, update = normalizer._build_snapshot_normalization(
        store, 's', update_current_state=False)

    def forbidden(_):
        raise AssertionError('Summary-only execution must not copy every observation')

    monkeypatch.setattr(StateObservation, 'to_dict', forbidden)
    actual, summary_facts, summary_update = normalizer._build_snapshot_normalization(
        store, 's', update_current_state=False, include_observations=False)
    assert actual == {**expected, 'observations': []}
    assert summary_facts == facts
    assert summary_update == update


def test_batch_insert_preserves_facts_and_rejects_stale_current_state():
    engine = create_engine('sqlite://')
    StateObservationRecord.__table__.create(engine)
    CurrentStateRecord.__table__.create(engine)
    repo = StateRepository()
    first = _state_observation('2026-09-27T10:00:00+00:00')
    null = replace(first, observation_id='null', idempotency_key='null',
                   state_key='other', value=None, value_text=None, value_number=None)
    newer = replace(first, observation_id='new', idempotency_key='new',
                    observed_at='2026-09-27T11:00:00+00:00', value='down', value_text='down')
    stale = replace(first, observation_id='stale', idempotency_key='stale',
                    observed_at='2026-09-27T09:00:00+00:00')
    with Session(engine) as db:
        assert repo.save_observations_in_session(db, [first, null], update_current_state=True) == (2, 2)
        assert repo.save_observations_in_session(db, [first, null], update_current_state=True) == (0, 0)
        assert repo.save_observations_in_session(db, [newer, stale], update_current_state=True, chunk_size=1) == (2, 1)
        assert db.scalar(select(func.count()).select_from(StateObservationRecord)) == 4
        states = {r.state_key: r for r in db.scalars(select(CurrentStateRecord))}
        assert states[first.state_key].value_json == 'down'
        assert states[first.state_key].version == 2
        assert states['other'].value_json is None
        assert states['other'].value_number is None
        evidence = replace(first, observation_id='evidence', idempotency_key='evidence', value='evidence-only')
        assert repo.save_observations_in_session(db, [evidence], update_current_state=False) == (1, 0)
        db.expire_all()
        assert db.scalar(select(CurrentStateRecord).where(CurrentStateRecord.state_key == first.state_key)).value_json == 'down'
    engine.dispose()
