"""What a check item no longer asks for leaves the patrol with it: rules, adapters and the events left open."""

from datetime import datetime, timezone

from src.apps.rule_governance.backend.check_items.retirement import (
    close_orphaned_events,
    orphaned_events,
    retire_check_item,
)
from src.core.patrol.change_event_repository import close_events_in_session
from src.infrastructure.db.models import (
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    NetworkEventRecord,
)
from tests.unit.test_check_item_catalog_governance import _environment, _session_scope

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


def _rule(db, version_id, event_type, *, status='active', event_class='fault'):
    db.add(EventRuleRecord(id=f'rule-{event_type}', name=f'rule-{event_type}', status=status, current_version=1))
    db.add(EventRuleVersionRecord(
        id=f'rule-{event_type}-v1', rule_id=f'rule-{event_type}', check_item_version_id=version_id, version=1,
        event_type=event_type, severity='major', condition_json={'kind': 'field_change', 'fields': ['fan_status']},
        event_class=event_class, evaluation_scope='both', status=status))


def _policy(db, version_id, observation_type):
    db.add(ChangePolicyRecord(id=f'policy-{observation_type}', name=observation_type,
                              observation_type=observation_type, status='active', current_version=1))
    db.add(ChangePolicyVersionRecord(
        id=f'policy-{observation_type}-v1', policy_id=f'policy-{observation_type}', check_item_version_id=version_id,
        version=1, observation_type=observation_type, semantic_type='state', compare_mode='state_transition',
        policy_json={}, status='active', digest='0' * 64))


def _event(db, event_id, event_type, *, status='open'):
    db.add(NetworkEventRecord(
        id=event_id, idempotency_key=event_id, event_type=event_type, severity='major', device_id='device-1',
        entity_type='fan', entity_key='["Fan1"]', correlation_key=event_id, occurred_at=NOW, first_seen_at=NOW,
        last_seen_at=NOW, status=status, payload_json={'event_type': event_type}))


def _statuses(db):
    return {str(row.id): str(row.status) for row in db.query(NetworkEventRecord)}


def test_closing_events_touches_only_the_open_ones_of_the_named_types_and_says_why(tmp_path):
    _repository, _service, session_factory = _environment(tmp_path)
    with _session_scope(session_factory) as db:
        _event(db, 'open-a', 'FanDown')
        _event(db, 'acknowledged-a', 'FanDown', status='acknowledged')
        _event(db, 'resolved-a', 'FanDown', status='resolved')
        _event(db, 'open-b', 'TemperatureHigh')
        db.flush()

        closed = close_events_in_session(
            db, event_types=['FanDown'], reason='rule_retired', note='规则已停用', actor='admin', now=NOW)

        assert closed == {'FanDown': 2}
        assert _statuses(db) == {
            'open-a': 'resolved', 'acknowledged-a': 'resolved', 'resolved-a': 'resolved', 'open-b': 'open'}
        payload = db.get(NetworkEventRecord, 'open-a').payload_json
        assert (payload['closed_reason'], payload['closed_note'], payload['closed_by']) == ('rule_retired', '规则已停用', 'admin')
        assert payload['resolved_at'] == NOW.isoformat()
        assert 'closed_reason' not in db.get(NetworkEventRecord, 'resolved-a').payload_json


def test_events_are_orphaned_only_when_no_rule_anywhere_still_produces_their_type(tmp_path):
    repository, _service, session_factory = _environment(tmp_path)
    catalog = repository.load_active()
    fan, power = catalog.check_item('environment.fan'), catalog.check_item('environment.power')
    with _session_scope(session_factory) as db:
        _rule(db, fan.version_id, 'FanDown')                          # still judged
        _rule(db, fan.version_id, 'FanSpeedChanged', status='deprecated', event_class='change')
        _rule(db, power.version_id, 'PowerDown', status='deprecated')  # another check item's leftover
        for index, event_type in enumerate(['FanDown', 'FanSpeedChanged', 'FanSpeedChanged', 'PowerDown']):
            _event(db, f'event-{index}', event_type)
        db.flush()

        assert orphaned_events(db, fan.id) == [{
            'event_type': 'FanSpeedChanged', 'event_class': 'change', 'severity': 'major', 'field': 'fan_status',
            'open': 2, 'total': 2}]
        closed = close_orphaned_events(db, fan.id, reason='rule_retired', note='规则已停用', actor='admin', now=NOW)

        assert [(event['event_type'], event['count']) for event in closed] == [('FanSpeedChanged', 2)]
        assert _statuses(db) == {'event-0': 'open', 'event-1': 'resolved', 'event-2': 'resolved', 'event-3': 'open'}
        assert orphaned_events(db, fan.id) == []


def test_a_deleted_check_item_takes_its_rules_and_its_own_adapters_and_leaves_shared_ones(tmp_path):
    repository, _service, session_factory = _environment(tmp_path)
    catalog = repository.load_active()
    serving = {}
    for adapter in catalog.implementations:
        if len(adapter.check_item_ids) == 1 and not adapter.check_item_ids[0].startswith('topology.'):
            serving.setdefault(adapter.check_item_ids[0], []).append(adapter)
    target_id, (shared, *own) = next((key, value) for key, value in serving.items() if len(value) > 1)
    target = catalog.check_item(target_id)
    other = next(item for item in catalog.check_items if item.id != target.id and not item.id.startswith('topology.'))
    with _session_scope(session_factory) as db:
        # One of its adapters also serves another check item.
        db.add(CheckItemImplementationLinkRecord(
            id='shared-link', check_item_version_id=other.version_id, implementation_version_id=shared.version_id,
            priority=100, required=True, created_at=NOW))
        _rule(db, target.version_id, 'TargetDown')
        _policy(db, target.version_id, 'target.state')
        _event(db, 'left-open', 'TargetDown')
        db.flush()

        result = retire_check_item(db, db.get(CheckItemRecord, target.id), actor='admin', reason='不再巡检', now=NOW)

        assert sorted(rule['name'] for rule in result['rules']) == ['TargetDown', 'target.state']
        assert {adapter['implementation_id'] for adapter in result['adapters_withdrawn']} == {adapter.id for adapter in own}
        assert result['adapters_kept'] == [
            {'implementation_id': shared.id, 'name': shared.name, 'also_serves': [other.name]}]
        assert [(event['event_type'], event['count']) for event in result['events_closed']] == [('TargetDown', 1)]
        assert db.get(CheckItemRecord, target.id).status == 'deprecated'
        assert {row.status for row in db.query(CheckItemVersionRecord).filter(
            CheckItemVersionRecord.check_item_id == target.id)} == {'deprecated'}
        assert db.get(EventRuleVersionRecord, 'rule-TargetDown-v1').status == 'deprecated'
        assert db.get(ChangePolicyVersionRecord, 'policy-target.state-v1').status == 'deprecated'
        assert db.get(CollectionImplementationRecord, shared.id).status == 'active'
        payload = db.get(NetworkEventRecord, 'left-open').payload_json
        assert (payload['closed_reason'], payload['closed_note']) == ('check_item_deleted', '检查项已删除')

    after = repository.load_active()
    assert after.check_item(target.id) is None
    survivor = next(adapter for adapter in after.implementations if adapter.id == shared.id)
    assert survivor.check_item_ids == (other.id,)
    assert not {adapter.id for adapter in own} & {adapter.id for adapter in after.implementations}


def test_the_delete_preview_reports_the_same_and_changes_nothing(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    target = next(
        item for item in repository.load_active().check_items
        if not item.id.startswith('topology.')
        and any(adapter.check_item_ids == (item.id,) for adapter in repository.load_active().implementations)
    )
    with _session_scope(session_factory) as db:
        _rule(db, target.version_id, 'TargetDown')
        _event(db, 'left-open', 'TargetDown')

    preview = service.preview_delete(target.id, actor='admin')

    assert preview['deletable'] and preview['adapters_withdrawn']
    assert [rule['name'] for rule in preview['rules']] == ['TargetDown']
    assert [(event['event_type'], event['count']) for event in preview['events_closed']] == [('TargetDown', 1)]
    assert repository.load_active().check_item(target.id) is not None
    with _session_scope(session_factory) as db:
        assert db.get(EventRuleVersionRecord, 'rule-TargetDown-v1').status == 'active'
        assert _statuses(db) == {'left-open': 'open'}

    deleted = service.delete_check_item(target.id, actor='admin', idempotency_key='delete-target-check-item')

    assert deleted['status'] == 'deprecated'
    assert deleted['adapters_withdrawn'] == preview['adapters_withdrawn']
    assert deleted['events_closed'] == preview['events_closed']
    assert repository.load_active().check_item(target.id) is None
    with _session_scope(session_factory) as db:
        assert _statuses(db) == {'left-open': 'resolved'}


def test_a_system_check_item_is_reported_as_not_deletable(tmp_path):
    _repository, service, _session_factory = _environment(tmp_path)
    preview = service.preview_delete('topology.routes', actor='admin')
    assert preview['deletable'] is False and preview['blockers']
