"""Add offline patrol jobs and immutable rule generation contexts.

Revision ID: 0041_offline_patrol_jobs
Revises: 0040_legacy_patrol_lookup
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_offline_patrol_jobs"
down_revision = "0040_legacy_patrol_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_offline_patrol_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("vendor", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("publish_events", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "folder_id",
            sa.String(64),
            sa.ForeignKey("netops_storage_folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("workflow_run_id", sa.String(64), nullable=True),
        sa.Column("celery_task_id", sa.String(64), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_idempotency_key", sa.String(128), nullable=True),
        sa.Column("progress_json", sa.JSON(), nullable=False),
        sa.Column("preview_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("artifacts_json", sa.JSON(), nullable=True),
        sa.Column("blockers_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, columns in (
        ("ix_offline_patrol_jobs_idempotency", ["idempotency_key"]),
        ("ix_offline_patrol_jobs_owner_created", ["owner_id", "created_at"]),
        ("ix_offline_patrol_jobs_status_created", ["status", "created_at"]),
        ("ix_offline_patrol_jobs_archived", ["archived"]),
        ("ix_offline_patrol_jobs_workflow", ["workflow_run_id"]),
    ):
        op.create_index(name, "netops_offline_patrol_jobs", columns)

    op.create_table(
        "netops_offline_patrol_periods",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("netops_offline_patrol_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dataset_id", sa.String(256), nullable=False, unique=True),
        sa.Column("patrol_run_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_offline_patrol_period_job_role",
        "netops_offline_patrol_periods",
        ["job_id", "role"],
        unique=True,
    )
    op.create_index(
        "ix_offline_patrol_period_dataset",
        "netops_offline_patrol_periods",
        ["dataset_id"],
        unique=True,
    )
    op.create_index(
        "ix_offline_patrol_period_run", "netops_offline_patrol_periods", ["patrol_run_id"]
    )

    op.create_table(
        "netops_offline_patrol_source_files",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("netops_offline_patrol_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_role", sa.String(32), nullable=False),
        sa.Column(
            "file_id",
            sa.String(64),
            sa.ForeignKey("netops_file_metadata.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="attached"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("preview_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_offline_patrol_source_job_period_file",
        "netops_offline_patrol_source_files",
        ["job_id", "period_role", "file_id"],
        unique=True,
    )
    op.create_index(
        "ix_offline_patrol_source_file", "netops_offline_patrol_source_files", ["file_id"]
    )

    op.create_table(
        "netops_offline_patrol_device_bindings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("netops_offline_patrol_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_role", sa.String(32), nullable=False),
        sa.Column("external_device_id", sa.String(512), nullable=False),
        sa.Column("device_name", sa.String(256), nullable=False),
        sa.Column("management_ip", sa.String(64), nullable=True),
        sa.Column(
            "inventory_device_id",
            sa.String(64),
            sa.ForeignKey("netops_devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("match_status", sa.String(32), nullable=False, server_default="unmatched"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("match_reason", sa.String(256), nullable=True),
        sa.Column("confirmed_by", sa.String(128), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_offline_patrol_binding_identity",
        "netops_offline_patrol_device_bindings",
        ["job_id", "period_role", "external_device_id"],
        unique=True,
    )
    op.create_index(
        "ix_offline_patrol_binding_inventory",
        "netops_offline_patrol_device_bindings",
        ["inventory_device_id"],
    )

    op.create_table(
        "netops_rule_generation_contexts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("netops_rule_generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "offline_job_id",
            sa.String(64),
            sa.ForeignKey("netops_offline_patrol_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("dataset_ids_json", sa.JSON(), nullable=False),
        sa.Column("observation_type", sa.String(256), nullable=True),
        sa.Column("field_name", sa.String(256), nullable=True),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("context_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("privacy_report_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rule_generation_context_job", "netops_rule_generation_contexts", ["job_id"])
    op.create_index(
        "ix_rule_generation_context_offline_job",
        "netops_rule_generation_contexts",
        ["offline_job_id"],
    )
    op.create_index(
        "ix_rule_generation_context_digest",
        "netops_rule_generation_contexts",
        ["context_digest"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("netops_rule_generation_contexts")
    op.drop_table("netops_offline_patrol_device_bindings")
    op.drop_table("netops_offline_patrol_source_files")
    op.drop_table("netops_offline_patrol_periods")
    op.drop_table("netops_offline_patrol_jobs")
