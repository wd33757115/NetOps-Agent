"""Owner/domain scoped encrypted credential sets and successful-device memory."""

import sqlalchemy as sa
from alembic import op

revision = "device_access_0002"
down_revision = "device_access_0001"


def upgrade():
    op.create_table(
        "app_device_access_credential_sets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("secret_ciphertext", sa.Text, nullable=False),
        sa.Column("enable_secret_ciphertext", sa.Text),
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column("scope_value", sa.String(256), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("disabled", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for field in ("owner_id", "domain"):
        op.create_index(
            "ix_app_device_access_credential_sets_" + field,
            "app_device_access_credential_sets",
            [field],
        )
    op.create_table(
        "app_device_access_device_credentials",
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("domain", sa.String(64), primary_key=True),
        sa.Column("device_key", sa.String(256), primary_key=True),
        sa.Column(
            "credential_set_id",
            sa.String(32),
            sa.ForeignKey("app_device_access_credential_sets.id"),
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_code", sa.String(64)),
    )


def downgrade():
    # Prevent silently destroying stored login material during a version rollback.
    raise RuntimeError("Restore a verified backup instead of deleting credential data")
