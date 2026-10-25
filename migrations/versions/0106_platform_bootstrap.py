"""Platform initial-data ledger; business table ownership remains in App manifests."""
import sqlalchemy as sa
from alembic import op

revision = "0106_platform_bootstrap"
down_revision = "0105_patrol_capacity_indexes"
branch_labels = depends_on = None


def upgrade():
    op.create_table("netops_bootstrap_steps",
                    sa.Column("owner", sa.String(128), primary_key=True),
                    sa.Column("version", sa.String(64), primary_key=True),
                    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("netops_bootstrap_steps")
