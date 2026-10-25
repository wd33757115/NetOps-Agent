"""Add governed check-item catalog change requests.

Revision ID: 0092_check_item_catalog_governance
Revises: 0091_comparison_baseline_idx
"""

import sqlalchemy as sa
from alembic import op

revision = "0092_check_item_catalog_governance"
down_revision = "0091_comparison_baseline_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic defaults version_num to VARCHAR(32), while this governed
    # revision ID is intentionally descriptive and longer than 32 chars.
    # Widen it before Alembic records this revision at transaction commit.
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
    op.create_table(
        "netops_check_item_catalog_changes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("asset_kind", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=128), nullable=False),
        sa.Column("version_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("baseline_version_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("candidate_payload_json", sa.JSON(), nullable=False),
        sa.Column("impact_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("impact_digest", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("creation_reason", sa.Text(), nullable=False),
        sa.Column("creation_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("submitted_by", sa.String(length=128), nullable=True),
        sa.Column("submission_reason", sa.Text(), nullable=True),
        sa.Column("submission_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decision_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "asset_kind IN ('check_item', 'collection_implementation')",
            name="ck_check_item_catalog_change_kind",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected', 'cancelled')",
            name="ck_check_item_catalog_change_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_check_item_catalog_change_kind",
        "netops_check_item_catalog_changes",
        ["asset_kind"],
    )
    op.create_index(
        "ix_check_item_catalog_change_asset",
        "netops_check_item_catalog_changes",
        ["asset_id"],
    )
    op.create_index(
        "ix_check_item_catalog_change_status",
        "netops_check_item_catalog_changes",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_check_item_catalog_change_baseline",
        "netops_check_item_catalog_changes",
        ["baseline_version_id"],
    )
    op.create_index(
        "ix_check_item_catalog_change_payload_digest",
        "netops_check_item_catalog_changes",
        ["payload_digest"],
    )
    op.create_index(
        "ix_check_item_catalog_change_impact_digest",
        "netops_check_item_catalog_changes",
        ["impact_digest"],
    )
    op.create_index(
        "ix_check_item_catalog_change_created_by",
        "netops_check_item_catalog_changes",
        ["created_by"],
    )
    op.create_index(
        "ix_check_item_catalog_change_submitted_by",
        "netops_check_item_catalog_changes",
        ["submitted_by"],
    )
    op.create_index(
        "ix_check_item_catalog_change_decided_by",
        "netops_check_item_catalog_changes",
        ["decided_by"],
    )
    op.create_index(
        "uq_check_item_catalog_change_version_id",
        "netops_check_item_catalog_changes",
        ["version_id"],
        unique=True,
    )
    op.create_index(
        "uq_check_item_catalog_change_creation_key",
        "netops_check_item_catalog_changes",
        ["creation_idempotency_key"],
        unique=True,
    )
    op.create_index(
        "uq_check_item_catalog_change_submission_key",
        "netops_check_item_catalog_changes",
        ["submission_idempotency_key"],
        unique=True,
    )
    op.create_index(
        "uq_check_item_catalog_change_decision_key",
        "netops_check_item_catalog_changes",
        ["decision_idempotency_key"],
        unique=True,
    )
    op.create_index(
        "uq_check_item_catalog_change_asset_version",
        "netops_check_item_catalog_changes",
        ["asset_kind", "asset_id", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("netops_check_item_catalog_changes")
