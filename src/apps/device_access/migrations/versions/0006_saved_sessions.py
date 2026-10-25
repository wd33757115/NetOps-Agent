"""SSH terminal: saved sessions and one default login per account (D-5).

Drops management-domain grants, credential sets, one-hour connections and
per-domain host-key policies. Back up the database before upgrading.
Recorded host keys are kept; each device keeps one key, no longer per domain.
"""

import sqlalchemy as sa
from alembic import op

revision = "device_access_0006"
down_revision = "device_access_0005"

HOST_KEYS = "app_device_access_host_keys"


def _keep_one_key_per_device():
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(f"SELECT domain, host, port, status FROM {HOST_KEYS}")  # noqa: S608
    ).all()
    kept = set()
    # A key already under the fixed domain wins; otherwise the first one seen.
    for domain, host, port, status in sorted(rows, key=lambda row: row[0] != "default"):
        if status == "revoked" or (host, port) in kept:
            bind.execute(
                sa.text(f"DELETE FROM {HOST_KEYS} WHERE domain=:d AND host=:h AND port=:p"),  # noqa: S608
                {"d": domain, "h": host, "p": port},
            )
        else:
            kept.add((host, port))
    bind.execute(
        sa.text(
            f"UPDATE {HOST_KEYS} SET domain='default', status='trusted', "  # noqa: S608
            "observed_fingerprint=NULL, changed_at=NULL"
        )
    )


def upgrade():
    for table in (
        "app_device_access_grant_audit",
        "app_device_access_address_grants",
        "app_device_access_host_key_policies",
        "app_device_access_device_credentials",
        "app_device_access_credential_sets",
        "app_device_access_operations",
        "app_device_access_connections",
    ):
        op.drop_table(table)
    op.create_table(
        "app_device_access_saved_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("host", sa.String(45), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(128)),
        sa.Column("secret_ciphertext", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_app_device_access_saved_sessions_owner_id",
        "app_device_access_saved_sessions",
        ["owner_id"],
    )
    op.create_table(
        "app_device_access_default_logins",
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "app_device_access_operations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(32)),
        sa.Column("host", sa.String(45), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner_id", "request_id"),
    )
    op.create_index(
        "ix_app_device_access_operations_owner_id", "app_device_access_operations", ["owner_id"]
    )
    with op.batch_alter_table(HOST_KEYS) as batch:
        batch.add_column(sa.Column("observed_key_type", sa.String(64)))
        batch.add_column(sa.Column("observed_public_key", sa.Text()))
    _keep_one_key_per_device()


def downgrade():
    raise RuntimeError("Restore a verified backup instead of recreating removed access data")
