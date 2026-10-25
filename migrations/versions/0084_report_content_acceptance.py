"""Add scalable report content review and acceptance ledger.

Revision ID: 0084_report_content_acceptance
Revises: 0083_record_set_relations
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0084_report_content_acceptance"
down_revision = "0083_record_set_relations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_report_content_reviews",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("report_manifest_id", sa.String(length=64), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="in_review",
            nullable=False,
        ),
        sa.Column("source_facts_digest", sa.String(length=64), nullable=False),
        sa.Column("claim_ledger_digest", sa.String(length=64), nullable=False),
        sa.Column("artifact_sha256_json", sa.JSON(), nullable=False),
        sa.Column("review_plan_digest", sa.String(length=64), nullable=False),
        sa.Column("machine_validation_json", sa.JSON(), nullable=False),
        sa.Column("machine_validation_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "required_item_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "accepted_item_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "rejected_item_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("required_confirmations_json", sa.JSON(), nullable=False),
        sa.Column("accepted_confirmations_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("accepted_by", sa.String(length=128), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acceptance_comment", sa.Text(), nullable=True),
        sa.Column("acceptance_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('in_review', 'revision_required', 'accepted')",
            name="ck_report_content_review_status",
        ),
        sa.CheckConstraint(
            "required_item_count >= 0 AND accepted_item_count >= 0 "
            "AND rejected_item_count >= 0",
            name="ck_report_content_review_counts_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["report_manifest_id"],
            ["netops_report_manifests.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_manifest_id"),
        sa.UniqueConstraint("review_plan_digest"),
        sa.UniqueConstraint("acceptance_digest"),
    )
    for name, columns in (
        ("ix_netops_report_content_reviews_report_manifest_id", ["report_manifest_id"]),
        ("ix_netops_report_content_reviews_patrol_batch_id", ["patrol_batch_id"]),
        ("ix_netops_report_content_reviews_status", ["status"]),
        ("ix_netops_report_content_reviews_source_facts_digest", ["source_facts_digest"]),
        ("ix_netops_report_content_reviews_claim_ledger_digest", ["claim_ledger_digest"]),
        ("ix_netops_report_content_reviews_review_plan_digest", ["review_plan_digest"]),
        ("ix_netops_report_content_reviews_machine_validation_digest", ["machine_validation_digest"]),
        ("ix_netops_report_content_reviews_created_by", ["created_by"]),
        ("ix_netops_report_content_reviews_accepted_by", ["accepted_by"]),
        ("ix_netops_report_content_reviews_accepted_at", ["accepted_at"]),
        ("ix_netops_report_content_reviews_acceptance_digest", ["acceptance_digest"]),
    ):
        op.create_index(name, "netops_report_content_reviews", columns, unique=False)

    op.create_table(
        "netops_report_content_review_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("review_id", sa.String(length=64), nullable=False),
        sa.Column("review_item_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("review_reasons_json", sa.JSON(), nullable=False),
        sa.Column("stratum_key", sa.String(length=512), nullable=True),
        sa.Column("fact_type", sa.String(length=64), nullable=False),
        sa.Column("fact_id", sa.String(length=256), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=True),
        sa.Column("check_item_id", sa.String(length=256), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=True),
        sa.Column("conclusion_status", sa.String(length=64), nullable=True),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column(
            "review_status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "review_status IN ('pending', 'accepted', 'rejected')",
            name="ck_report_content_review_item_status",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["netops_report_content_reviews.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_netops_report_content_review_items_review_id", ["review_id"]),
        ("ix_netops_report_content_review_items_review_item_id", ["review_item_id"]),
        ("ix_netops_report_content_review_items_category", ["category"]),
        ("ix_netops_report_content_review_items_stratum_key", ["stratum_key"]),
        ("ix_netops_report_content_review_items_fact_type", ["fact_type"]),
        ("ix_netops_report_content_review_items_fact_id", ["fact_id"]),
        ("ix_netops_report_content_review_items_device_id", ["device_id"]),
        ("ix_netops_report_content_review_items_check_item_id", ["check_item_id"]),
        ("ix_netops_report_content_review_items_severity", ["severity"]),
        ("ix_netops_report_content_review_items_conclusion_status", ["conclusion_status"]),
        ("ix_netops_report_content_review_items_review_status", ["review_status"]),
        ("ix_netops_report_content_review_items_reviewed_by", ["reviewed_by"]),
        ("ix_netops_report_content_review_items_reviewed_at", ["reviewed_at"]),
    ):
        op.create_index(name, "netops_report_content_review_items", columns, unique=False)
    op.create_index(
        "uq_report_content_review_item",
        "netops_report_content_review_items",
        ["review_id", "review_item_id"],
        unique=True,
    )
    op.create_index(
        "ix_report_content_review_queue",
        "netops_report_content_review_items",
        ["review_id", "review_status", "category", "id"],
        unique=False,
    )

    op.add_column(
        "netops_report_releases",
        sa.Column("content_review_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_report_releases",
        sa.Column("content_acceptance_digest", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_report_release_content_review",
        "netops_report_releases",
        "netops_report_content_reviews",
        ["content_review_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_netops_report_releases_content_review_id",
        "netops_report_releases",
        ["content_review_id"],
        unique=False,
    )
    op.create_index(
        "ix_netops_report_releases_content_acceptance_digest",
        "netops_report_releases",
        ["content_acceptance_digest"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_report_releases_content_acceptance_digest",
        table_name="netops_report_releases",
    )
    op.drop_index(
        "ix_netops_report_releases_content_review_id",
        table_name="netops_report_releases",
    )
    op.drop_constraint(
        "fk_report_release_content_review",
        "netops_report_releases",
        type_="foreignkey",
    )
    op.drop_column("netops_report_releases", "content_acceptance_digest")
    op.drop_column("netops_report_releases", "content_review_id")
    op.drop_index(
        "ix_report_content_review_queue",
        table_name="netops_report_content_review_items",
    )
    op.drop_index(
        "uq_report_content_review_item",
        table_name="netops_report_content_review_items",
    )
    op.drop_table("netops_report_content_review_items")
    op.drop_table("netops_report_content_reviews")
