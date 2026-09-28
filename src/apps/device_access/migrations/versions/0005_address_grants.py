"""App-owned scoped grants and transactional administrator audit."""

import sqlalchemy as sa
from alembic import op

revision = "device_access_0005"
down_revision = "device_access_0004"


def upgrade():
    op.create_table(
        "app_device_access_address_grants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(64), nullable=False),
        sa.Column("cidr", sa.String(49), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_app_device_access_address_grants_owner_id",
        "app_device_access_address_grants",
        ["owner_id"],
    )
    op.create_table(
        "app_device_access_grant_audit",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("grant_id", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_app_device_access_grant_audit_grant_id", "app_device_access_grant_audit", ["grant_id"]
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting grant audit")
