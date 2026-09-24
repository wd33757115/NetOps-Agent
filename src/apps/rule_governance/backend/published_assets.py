"""Withdraw published assets without erasing immutable inspection evidence."""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, or_
from sqlalchemy.exc import SQLAlchemyError

from src.infrastructure.db.postgres import get_db_session
from src.infrastructure.db.models import (
    AuditLogRecord, ParserTemplate,
    ChangePolicyRecord, ChangePolicyVersionRecord, EventRuleRecord, EventRuleVersionRecord,
    CheckItemRuntimeBundleRecord, CheckItemRuntimeBundleVersionRecord, RuntimeBundleAssetRecord,
)

logger = logging.getLogger(__name__)
ASSETS = {
    'parser': (ParserTemplate, ('parser_template',)),
    'policy': (ChangePolicyVersionRecord, ('change_policy',)),
    'event': (EventRuleVersionRecord, ('event_rule', 'current_state_rule')),
}


class PublishedAssetService:
    def __init__(self, session_scope=get_db_session):
        self.session_scope = session_scope

    def bulk(self, *, kind, action, ids, actor, reason=''):
        if kind not in ASSETS or action not in {'deprecate', 'delete'}:
            raise ValueError('不支持的资产类型或操作')
        if not str(actor).strip():
            raise ValueError('缺少操作用户')
        if not ids or len(ids) > 100 or any(not isinstance(i, str) or not i.strip() for i in ids):
            raise ValueError('每次请选择 1 至 100 个有效资产')
        results = []
        for asset_id in dict.fromkeys(ids):
            try:
                results.append(self._withdraw(kind, action, asset_id, actor, reason.strip()))
            except ValueError as exc:
                results.append({'id': asset_id, 'success': False, 'error': str(exc)})
            except SQLAlchemyError:
                logger.exception('Published asset lifecycle failed kind=%s id=%s', kind, asset_id)
                results.append({'id': asset_id, 'success': False, 'error': '资产正在变更或保存失败，请重试'})
        succeeded = sum(item['success'] for item in results)
        return {'results': results, 'succeeded': succeeded, 'failed': len(results) - succeeded}

    def _withdraw(self, kind, action, asset_id, actor, reason):
        model, types = ASSETS[kind]
        target = 'deleted' if action == 'delete' else 'deprecated'
        now = datetime.now(timezone.utc)
        with self.session_scope() as db:
            row = db.query(model).filter(model.id == asset_id).with_for_update().one_or_none()
            if row is None:
                raise ValueError('资产不存在，请刷新列表')
            if row.status == 'deleted':
                if action != 'delete':
                    raise ValueError('资产已删除，不能再停用')
                return {'id': asset_id, 'success': True, 'status': target, 'unchanged': True}
            if row.status not in {'active', 'deprecated'}:
                raise ValueError('仅可操作已发布或已停用的资产')
            previous = row.status
            predicates = [and_(RuntimeBundleAssetRecord.asset_type.in_(types),
                               RuntimeBundleAssetRecord.asset_version_id == asset_id)]
            bundle_ids = db.query(RuntimeBundleAssetRecord.bundle_version_id).filter(or_(*predicates))
            bundles = (db.query(CheckItemRuntimeBundleVersionRecord)
                       .filter(CheckItemRuntimeBundleVersionRecord.id.in_(bundle_ids),
                               CheckItemRuntimeBundleVersionRecord.status == 'active')
                       .order_by(CheckItemRuntimeBundleVersionRecord.id).with_for_update().all())
            for bundle in bundles:
                bundle.status, bundle.deprecated_at = 'deprecated', now
            db.flush()
            for bundle_id in sorted({b.bundle_id for b in bundles}):
                root = db.query(CheckItemRuntimeBundleRecord).filter_by(id=bundle_id).with_for_update().one()
                if not db.query(CheckItemRuntimeBundleVersionRecord).filter_by(bundle_id=bundle_id, status='active').first():
                    root.status, root.updated_at = 'deprecated', now
            row.status, row.deprecated_at = target, now
            if hasattr(row, 'updated_at'):
                row.updated_at = now
            db.flush()
            if kind in {'policy', 'event'}:
                root_model, field = (ChangePolicyRecord, 'policy_id') if kind == 'policy' else (EventRuleRecord, 'rule_id')
                root = db.query(root_model).filter_by(id=getattr(row, field)).with_for_update().one()
                active = db.query(model).filter(getattr(model, field) == root.id, model.status == 'active').first()
                root.current_version = active.version if active else 0
                root.status = 'active' if active else 'deprecated'
                root.updated_at = now
            # Retrying a successful stop does not duplicate the audit trail, but
            # still repairs any stale active bundle reference encountered above.
            if previous != target or bundles:
                db.add(AuditLogRecord(
                    id=str(uuid.uuid4()), username=actor, user_id=actor,
                    action=f'published_asset_{action}', resource_type=types[0], resource_id=asset_id,
                    detail={'kind': kind, 'reason': reason, 'from_status': previous, 'to_status': target,
                            'retired_runtime_bundle_version_ids': [b.id for b in bundles]},
                    status='success', created_at=now,
                ))
            db.flush()
            return {'id': asset_id, 'success': True, 'status': target,
                    'retired_bundle_count': len(bundles), 'unchanged': previous == target}
