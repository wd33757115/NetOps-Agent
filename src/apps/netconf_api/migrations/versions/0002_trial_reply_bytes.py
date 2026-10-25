"""Keep how many bytes the device's replies to a trial held."""

import sqlalchemy as sa
from alembic import op

revision = "netconf_api_0002"
down_revision = "netconf_api_0001"


def upgrade():
    op.add_column("app_netconf_trials", sa.Column("reply_bytes", sa.Integer()))


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting history")
