import sqlalchemy as sa
from alembic import op

revision = "delivery_probe_0002"
down_revision = "delivery_probe_0001"


def upgrade():
    op.add_column("app_delivery_probe_runs", sa.Column("result", sa.String(500)))


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting business data")
