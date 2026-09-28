"""Retire obsolete platform module switches; App lifecycle owns availability."""
import sqlalchemy as sa
from alembic import op

revision = '0104_retire_module_controls'
down_revision = '0103_retire_data_contracts'
branch_labels = None
depends_on = None


def upgrade():
    # No CASCADE: unexpected dependencies must stop the migration.
    op.execute(sa.text('DROP TABLE IF EXISTS netops_agent_module_controls'))


def downgrade():
    raise RuntimeError('旧平台模块开关已删除；回退请恢复升级前的备份与代码')
