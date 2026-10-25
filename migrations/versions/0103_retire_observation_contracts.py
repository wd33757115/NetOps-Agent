"""Retire the independent ObservationContract asset and its generation jobs."""
import sqlalchemy as sa
from alembic import op

revision = '0103_retire_data_contracts'
down_revision = '0102_remove_legacy_knowledge'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('netops_runtime_bundle_assets'):
        active = bind.execute(sa.text("""
            SELECT count(*) FROM netops_runtime_bundle_assets a
            JOIN netops_check_item_runtime_bundle_versions b ON b.id = a.bundle_version_id
            WHERE a.asset_type = 'observation_contract' AND b.status = 'active'
        """)).scalar_one()
        if active:
            raise RuntimeError('仍有活动运行包引用旧数据契约，请先迁移到检查项 V2 适配器')
    if inspector.has_table('netops_rule_generation_jobs'):
        jobs = sa.Table('netops_rule_generation_jobs', sa.MetaData(), autoload_with=bind)
        retired_ids = list(bind.execute(sa.select(jobs.c.id).where(jobs.c.job_type == 'OBSERVATION_CONTRACT')).scalars())
        if retired_ids:
            obsolete = set(retired_ids)
            # Preserve audit/history, but withdraw old candidates from all queues.
            for ident, payload in bind.execute(sa.select(jobs.c.id, jobs.c.payload_json).where(jobs.c.job_type == 'RULE_ASSET_BUNDLE')):
                if isinstance(payload, dict) and obsolete.intersection(payload.get('child_job_ids') or []):
                    retired_ids.append(ident)
            bind.execute(jobs.update().where(jobs.c.id.in_(retired_ids)).values(status='deprecated'))
    # No CASCADE: an unexpected foreign key must stop the migration.
    op.execute(sa.text('DROP TABLE IF EXISTS netops_observation_contracts'))


def downgrade():
    raise RuntimeError('旧数据契约已删除；回退请恢复升级前的备份与代码')
