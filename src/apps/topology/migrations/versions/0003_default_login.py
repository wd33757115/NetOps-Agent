"""One saved discovery username/password per platform account (D-5)."""

import sqlalchemy as sa
from alembic import op

revision = "topology_0003"
down_revision = "topology_0002"


def upgrade():
    op.create_table(
        "app_topology_default_logins",
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting saved logins")
