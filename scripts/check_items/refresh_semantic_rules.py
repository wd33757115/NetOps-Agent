"""Plan, verify and republish rules from current CheckItem semantics.

No model call or sample-derived threshold is involved. Planning is read-only;
apply requires its exact saved plan and uses existing asset/review services.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.apps.rule_governance.backend.check_items.canonical_rule_context import CanonicalRuleContextService
from src.core.check_items.catalog import CheckItemCatalogRepository
from src.core.patrol.change_policy import compare_with_policy, policy_from_dict, validate_change_policy
from src.core.patrol.event_rules import condition_matches, current_state_condition_matches
from src.infrastructure.db import models as m
from src.infrastructure.db.postgres import get_db_session


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf8')
    temporary.replace(path)


def verify_candidates(context: dict, candidates: dict) -> dict:
    """Run positive/negative cases against the real condition executor."""
    if not candidates['success']:
        raise ValueError(candidates['blockers'])
    targets = {r['observation_type']: r for r in context['targets']}
    cases = []
    for raw in candidates['change_policies']:
        policy = policy_from_dict(raw)
        assert validate_change_policy(policy)['success']
        target = targets[policy.observation_type]
        reference_fields = {v['field_name']: 50 for v in (policy.threshold_references or {}).values()}
        value = 20 if policy.semantic_type in {'gauge', 'counter', 'threshold'} else 'up'
        result = compare_with_policy(policy, old=value, new=value, field=target['canonical_field'], context=reference_fields)
        assert not result['changed'], (policy.observation_type, result)
        cases.append({'policy': policy.observation_type, 'case': 'unchanged_suppressed'})
        if policy.threshold_references:
            assert not policy.allow_static_threshold_fallback
    for rule in candidates['event_rules']:
        condition = rule['condition']
        kind = condition['kind']
        target = targets[rule['observation_type']]
        assert condition['fields'] == [target['canonical_field']]
        reference = condition.get('reference_field')
        record = {reference: 50} if reference else {}
        hysteresis = float(condition.get('hysteresis', 0))
        if kind == 'field_change':
            old, new, healthy = 'before', 'after', 'before'
        elif kind == 'entity_status_down':
            old, new, healthy = 'up', condition['values'][0], 'up'
        elif kind == 'transition':
            old, new = condition['from_values'][0], condition['to_values'][0]
            healthy = old
        elif kind == 'threshold_field_gte':
            old, new, healthy = 49, 50, 49
        elif kind == 'threshold_field_lte':
            old, new, healthy = 51, 50, 51
        elif kind == 'threshold_field_high_recovery':
            old, new, healthy = 51, 50 - hysteresis, 51
        elif kind == 'threshold_field_low_recovery':
            old, new, healthy = 49, 50 + hysteresis, 49
        else:
            raise ValueError(f'No executable regression case for {kind}')
        change = {
            'entity_type': condition['entity_types'][0], 'entity_key': 'synthetic-fixture',
            'field': target['canonical_field'], 'observation_type': rule['observation_type'],
            'change_type': 'modified', 'old': old, 'new': new, 'record_context': record,
            'dimensions': dict(condition.get('dimensions') or {}),
        }
        assert condition_matches(condition, change), (rule['name'], 'positive')
        assert not condition_matches(condition, {**change, 'new': healthy}), (rule['name'], 'negative')
        assert not condition_matches(condition, {**change, 'field': '__unrelated__'}), rule['name']
        count = 3
        if reference:
            assert not condition_matches(condition, {**change, 'record_context': {}}), (rule['name'], 'missing threshold')
            assert not condition_matches(condition, {**change, 'new': None}), (rule['name'], 'missing value')
            count += 2
        if kind == 'field_change':
            assert not condition_matches(condition, {**change, 'change_type': 'initial'})
            count += 1
        if rule['evaluation_scope'] in {'current_state', 'both'}:
            observation = {**change, 'value': new}
            assert current_state_condition_matches(condition, observation, explicit_current_scope=True)
            assert not current_state_condition_matches(condition, {**observation, 'value': healthy}, explicit_current_scope=True)
            count += 2
            if reference:
                assert not current_state_condition_matches(condition, {**observation, 'record_context': {}}, explicit_current_scope=True)
                count += 1
        cases.append({'event': rule['event_type'], 'kind': kind, 'assertions': count})
    return {'success': True, 'source': 'synthetic_boundary_cases_not_device_history',
            'policy_count': len(candidates['change_policies']), 'rule_count': len(candidates['event_rules']),
            'assertions': sum(r.get('assertions', 1) for r in cases), 'cases': cases}


def build_plan() -> dict:
    catalog = CheckItemCatalogRepository().load_active()
    service = CanonicalRuleContextService()
    checks = []
    for check in catalog.check_items:
        context = service.get(check.version_id)
        candidates = service.generate_candidates(check.version_id)
        checks.append({'id': check.id, 'name': check.name, 'version_id': check.version_id,
                       'version': check.version, 'mode': check.assessment_mode, 'result_mode': check.result_mode,
                       'context_digest': context['digest'], 'candidates': candidates,
                       'verification': verify_candidates(context, candidates)})
    ids = {c['id'] for c in checks}
    with get_db_session() as db:
        versions = db.query(m.CheckItemVersionRecord).filter(m.CheckItemVersionRecord.check_item_id.in_(ids)).all()
        version_ids = {v.id for v in versions}
        types = {r.get('observation_type') for v in versions
                 for r in (v.canonical_contract_json or {}).get('observations', [])} - {None, ''}
        old_policies = [r for r in db.query(m.ChangePolicyVersionRecord).all()
                        if r.check_item_version_id in version_ids or r.observation_type in types]
        old_events = [r for r in db.query(m.EventRuleVersionRecord).all()
                      if r.check_item_version_id in version_ids
                      or types.intersection((r.condition_json or {}).get('observation_types', []))]
        assets = {r.id for r in [*old_policies, *old_events]}
        jobs = [r for r in db.query(m.RuleGenerationJobRecord).all()
                if r.asset_version_id in assets
                or (r.job_type == 'RULE_ASSET_BUNDLE' and r.asset_type == 'CHECK_ITEM_RULES'
                    and (r.payload_json or {}).get('check_item_id') in ids)]
        bundles = [r.to_dict() for r in db.query(m.CheckItemRuntimeBundleVersionRecord).filter_by(status='active').all()]
        old_policies = [{'id': r.id, 'status': r.status} for r in old_policies]
        old_events = [{'id': r.id, 'status': r.status} for r in old_events]
        jobs = [{'id': r.id, 'status': r.status} for r in jobs]
    return {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'checks': checks,
            'old_policies': old_policies, 'old_events': old_events,
            'old_jobs': jobs, 'active_bundles_before': bundles}


def apply_plan(plan: dict, state_path: Path, actor: str) -> dict:
    from src.apps.rule_governance.backend.access import execution_lock
    from src.apps.rule_governance.backend.published_assets import PublishedAssetService
    from src.apps.rule_governance.backend.rule_control.approval import RuleAssetApprovalService
    from src.apps.rule_governance.backend.rule_control.engineering_revision import RuleAssetEngineeringRevisionService
    service = CanonicalRuleContextService()
    for check in plan['checks']:
        current = service.get(check['version_id'])
        assert current['check_item']['version_status'] == 'active'
        assert current['digest'] == check['context_digest'], f"CheckItem changed: {check['id']}"
        assert service.generate_candidates(check['version_id'])['digest'] == check['candidates']['digest']
        verify_candidates(current, check['candidates'])
    state = json.loads(state_path.read_text(encoding='utf8')) if state_path.exists() else {'published': {}, 'retired': False}
    reason = '用户要求按当前检查项语义重新生成变化策略和事件规则；已执行正反例、边界与缺失阈值验证'
    with execution_lock(exclusive=True):
        if not state['retired']:
            # Withdraw through the existing lifecycle, which also closes every
            # dependent old runtime bundle. Immutable history remains readable.
            for kind, key in [('policy', 'old_policies'), ('event', 'old_events')]:
                ids = [r['id'] for r in plan[key] if r['status'] == 'active']
                for start in range(0, len(ids), 100):
                    result = PublishedAssetService().bulk(kind=kind, action='deprecate', ids=ids[start:start+100], actor=actor, reason=reason)
                    assert result['failed'] == 0, result
            with get_db_session() as db:
                now = datetime.now(timezone.utc)
                for model, key in [(m.ChangePolicyVersionRecord, 'old_policies'), (m.EventRuleVersionRecord, 'old_events')]:
                    for row in db.query(model).filter(model.id.in_([r['id'] for r in plan[key]])).with_for_update().all():
                        if row.status in {'draft', 'pending_approval'}:
                            row.status = 'rejected'
                for row in db.query(m.RuleGenerationJobRecord).filter(m.RuleGenerationJobRecord.id.in_([r['id'] for r in plan['old_jobs']])).with_for_update().all():
                    if row.status not in {'deleted', 'deprecated', 'rejected', 'cancelled'}:
                        row.status = 'deprecated' if row.status == 'active' else 'cancelled'
                        row.updated_at = now
                db.add(m.AuditLogRecord(id=str(uuid.uuid4()), username=actor, user_id=actor,
                    action='check_item_semantic_rules_retired', resource_type='check_item_rule_batch',
                    resource_id=plan['created_at'], status='success', created_at=now,
                    detail={'reason': reason, 'policies': plan['old_policies'], 'events': plan['old_events'], 'jobs': plan['old_jobs']}))
            state['retired'] = True
            write_json(state_path, state)
        for check in plan['checks']:
            if check['id'] in state['published']:
                continue
            candidates = check['candidates']
            if not candidates['change_policies'] and not candidates['event_rules']:
                state['published'][check['id']] = {'status': 'not_required', 'mode': check['mode'], 'result_mode': check['result_mode']}
                write_json(state_path, state)
                continue
            draft = service.create_drafts(check['version_id'], actor=actor)
            assert draft['digest'] == candidates['digest']
            assert draft['bundle_id']
            if draft['bundle_status'] != 'active':
                with get_db_session() as db:
                    bundle = db.get(m.RuleGenerationJobRecord, draft['bundle_id'])
                    report = dict(bundle.validation_report_json)
                    report['deterministic_executor_verification'] = check['verification']
                    bundle.validation_report_json = report
                RuleAssetEngineeringRevisionService().review(draft['bundle_id'], decision='accepted', actor=actor,
                    reason=reason, allow_same_actor=True)
                RuleAssetApprovalService().approve(draft['bundle_id'], actor=actor, reason=reason, allow_same_actor=True)
            state['published'][check['id']] = {'status': 'active', 'version_id': check['version_id'],
                'bundle_id': draft['bundle_id'], 'change_policy_version_ids': [p['version_id'] for p in draft['change_policy_assets']],
                'event_rule_version_ids': draft['event_rule_version_ids'], 'verification_assertions': check['verification']['assertions']}
            write_json(state_path, state)
            print(check['id'], 'published', len(draft['change_policy_assets']), len(draft['event_rule_version_ids']), flush=True)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'apply'])
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--actor', default='admin')
    args = parser.parse_args()
    if args.action == 'plan':
        if args.plan.exists():
            raise ValueError('Use a new plan filename; do not overwrite an in-progress refresh')
        plan = build_plan()
        write_json(args.plan, plan)
        print({'checks': len(plan['checks']), 'policies': sum(len(c['candidates']['change_policies']) for c in plan['checks']),
               'rules': sum(len(c['candidates']['event_rules']) for c in plan['checks']),
               'assertions': sum(c['verification']['assertions'] for c in plan['checks'])})
    else:
        state = apply_plan(json.loads(args.plan.read_text(encoding='utf8')), args.plan.with_suffix('.state.json'), args.actor)
        print(dict(Counter(r['status'] for r in state['published'].values())))


if __name__ == '__main__':
    main()
