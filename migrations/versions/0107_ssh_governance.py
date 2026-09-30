"""Platform-wide SSH command policy; reuse the platform audit log."""

import sqlalchemy as sa
from alembic import op

revision = "0107_ssh_governance"
down_revision = "0106_platform_bootstrap"
branch_labels = depends_on = None


def upgrade():
    table = op.create_table(
        "netops_ssh_policy",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("patterns", sa.JSON, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_netops_ssh_policy_singleton"),
    )
    op.execute(
        table.insert().values(
            id=1,
            patterns=[".*"],
            version=1,
            updated_by="bootstrap",
            updated_at=sa.func.now(),
        )
    )


def downgrade():
    op.drop_table("netops_ssh_policy")
