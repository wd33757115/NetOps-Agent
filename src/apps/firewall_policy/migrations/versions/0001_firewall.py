"""Independent firewall business data; no changes to legacy artifact records."""

import sqlalchemy as sa
from alembic import op

revision = "firewall_policy_0001"
down_revision = None


def upgrade():
    op.create_table(
        "app_firewall_uploads",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_firewall_upload_request"),
    )
    op.create_index("ix_app_firewall_uploads_owner_id", "app_firewall_uploads", ["owner_id"])
    op.create_table(
        "app_firewall_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("parent_id", sa.String(32), sa.ForeignKey("app_firewall_runs.id")),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.String(2000)),
        sa.Column("result_json", sa.JSON()),
        sa.Column("artifacts_json", sa.JSON()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(32)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_firewall_run_request"),
    )
    for column in ["owner_id", "parent_id", "status"]:
        op.create_index(f"ix_app_firewall_runs_{column}", "app_firewall_runs", [column])


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting business data")
