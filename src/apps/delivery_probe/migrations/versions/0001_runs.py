import sqlalchemy as sa
from alembic import op

revision = "delivery_probe_0001"
down_revision = None


def upgrade():
    op.create_table(
        "app_delivery_probe_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(32)),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_delivery_probe_request"),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting business data")
