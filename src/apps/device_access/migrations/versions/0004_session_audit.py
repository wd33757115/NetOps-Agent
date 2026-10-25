"""Append-only session evidence owned by Device Access."""

import sqlalchemy as sa
from alembic import op

revision = "device_access_0004"
down_revision = "device_access_0003"


def upgrade():
    op.create_table(
        "app_device_access_session_audit",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(32), nullable=False),
        sa.Column("target_ref", sa.String(256), nullable=False),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_app_device_access_session_audit_owner_id",
        "app_device_access_session_audit",
        ["owner_id"],
    )
    op.create_index(
        "ix_app_device_access_session_audit_session_id",
        "app_device_access_session_audit",
        ["session_id"],
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting session audit")
