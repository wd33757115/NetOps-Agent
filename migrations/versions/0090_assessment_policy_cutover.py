"""Add target-level assessment-policy resolver cutover ledger.

Revision ID: 0090_assessment_policy_cutover
Revises: 0089_assessment_policy_migrate
"""

import sqlalchemy as sa
from alembic import op


revision = "0090_assessment_policy_cutover"
down_revision = "0089_assessment_policy_migrate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_assessment_policy_cutovers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("check_item_id", sa.String(length=128), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("resolver_mode", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
        ),
        sa.Column("binding_ids_json", sa.JSON(), nullable=False),
        sa.Column("shadow_result_ids_json", sa.JSON(), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("supersedes_id", sa.String(length=64), nullable=True),
        sa.Column("superseded_by_id", sa.String(length=64), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "resolver_mode IN ('legacy_compat', 'governed_only')",
            name="ck_assessment_policy_cutover_mode",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded')",
            name="ck_assessment_policy_cutover_status",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_version_id"],
            ["netops_check_item_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["netops_devices.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["netops_assessment_policy_cutovers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["netops_assessment_policy_cutovers.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_assessment_policy_cutover_check_item",
        "netops_assessment_policy_cutovers",
        ["check_item_id"],
    )
    op.create_index(
        "ix_assessment_policy_cutover_check_version",
        "netops_assessment_policy_cutovers",
        ["check_item_version_id"],
    )
    op.create_index(
        "ix_assessment_policy_cutover_device",
        "netops_assessment_policy_cutovers",
        ["device_id"],
    )
    op.create_index(
        "ix_assessment_policy_cutover_mode",
        "netops_assessment_policy_cutovers",
        ["resolver_mode"],
    )
    op.create_index(
        "ix_assessment_policy_cutover_status",
        "netops_assessment_policy_cutovers",
        ["status"],
    )
    op.create_index(
        "ix_assessment_policy_cutover_evidence",
        "netops_assessment_policy_cutovers",
        ["evidence_digest"],
    )
    op.create_index(
        "ix_assessment_policy_cutover_supersedes",
        "netops_assessment_policy_cutovers",
        ["supersedes_id"],
    )
    op.create_index(
        "ix_assessment_policy_cutover_superseded_by",
        "netops_assessment_policy_cutovers",
        ["superseded_by_id"],
    )
    op.create_index(
        "ix_assessment_policy_cutover_history",
        "netops_assessment_policy_cutovers",
        ["check_item_version_id", "device_id", "created_at"],
    )
    op.create_index(
        "uq_assessment_policy_cutover_active_target",
        "netops_assessment_policy_cutovers",
        ["check_item_version_id", "device_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_table("netops_assessment_policy_cutovers")
