"""Add durable patrol batch manifests and paginated device targets.

Revision ID: 0038_patrol_batch_manifest
Revises: 0037_batch_summary_projection
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_patrol_batch_manifest"
down_revision = "0037_batch_summary_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_patrol_batches",
        sa.Column("patrol_batch_id", sa.String(128), primary_key=True),
        sa.Column("workflow_name", sa.String(128), nullable=False),
        sa.Column("launch_mode", sa.String(32), nullable=False),
        sa.Column("patrol_mode", sa.String(32), nullable=False),
        sa.Column("ticket_id", sa.String(128), nullable=True),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_label", sa.String(256), nullable=False),
        sa.Column("selector_json", sa.JSON(), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("launched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archive_reason", sa.Text(), nullable=True),
        sa.Column("archived_by", sa.String(128), nullable=True),
        sa.Column("keep_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_patrol_batches_created_at", "netops_patrol_batches", ["created_at"])
    op.create_index("ix_patrol_batches_target_type", "netops_patrol_batches", ["target_type"])
    op.create_index("ix_patrol_batches_ticket_id", "netops_patrol_batches", ["ticket_id"])
    op.create_index("ix_patrol_batches_archived", "netops_patrol_batches", ["archived"])

    op.create_table(
        "netops_patrol_batch_devices",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "patrol_batch_id",
            sa.String(128),
            sa.ForeignKey("netops_patrol_batches.patrol_batch_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(256), nullable=False),
        sa.Column("device_name", sa.String(256), nullable=True),
        sa.Column("management_ip", sa.String(64), nullable=True),
        sa.Column("selection_status", sa.String(32), nullable=False),
        sa.Column("preflight_status", sa.String(32), nullable=False),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("workflow_run_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_patrol_batch_devices_batch", "netops_patrol_batch_devices", ["patrol_batch_id"]
    )
    op.create_index("ix_patrol_batch_devices_device", "netops_patrol_batch_devices", ["device_id"])
    op.create_index("ix_patrol_batch_devices_name", "netops_patrol_batch_devices", ["device_name"])
    op.create_index(
        "ix_patrol_batch_devices_selection", "netops_patrol_batch_devices", ["selection_status"]
    )
    op.create_index(
        "ix_patrol_batch_devices_preflight", "netops_patrol_batch_devices", ["preflight_status"]
    )
    op.create_index(
        "ix_patrol_batch_devices_failure", "netops_patrol_batch_devices", ["failure_code"]
    )
    op.create_index(
        "ix_patrol_batch_devices_run", "netops_patrol_batch_devices", ["workflow_run_id"]
    )
    op.create_index(
        "uq_netops_patrol_batch_device",
        "netops_patrol_batch_devices",
        ["patrol_batch_id", "device_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("netops_patrol_batch_devices")
    op.drop_table("netops_patrol_batches")
