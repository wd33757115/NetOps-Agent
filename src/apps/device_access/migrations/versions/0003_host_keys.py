"""Domain-scoped host trust and policy; only Device Access owned tables."""

import sqlalchemy as sa
from alembic import op

revision = "device_access_0003"
down_revision = "device_access_0002"


def upgrade():
    op.create_table(
        "app_device_access_host_keys",
        sa.Column("domain", sa.String(64), primary_key=True),
        sa.Column("host", sa.String(45), primary_key=True),
        sa.Column("port", sa.Integer(), primary_key=True),
        sa.Column("key_type", sa.String(64), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("verified_by", sa.String(64)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("observed_fingerprint", sa.String(64)),
        sa.Column("changed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "app_device_access_host_key_policies",
        sa.Column("domain", sa.String(64), primary_key=True),
        sa.Column("policy", sa.String(16), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting host trust")
