"""Add governed cross-risk episode associations.

Revision ID: 0085_risk_episode_association
Revises: 0084_report_content_acceptance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0085_risk_episode_association"
down_revision = "0084_report_content_acceptance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_risk_episode_associations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("primary_risk_case_id", sa.String(length=64), nullable=False),
        sa.Column("primary_risk_episode_id", sa.String(length=64), nullable=False),
        sa.Column("related_risk_case_id", sa.String(length=64), nullable=False),
        sa.Column("related_risk_episode_id", sa.String(length=64), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending_review",
            nullable=False,
        ),
        sa.Column(
            "source_type",
            sa.String(length=32),
            server_default="manual",
            nullable=False,
        ),
        sa.Column("policy_ref_json", sa.JSON(), nullable=True),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.String(length=128), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=128), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "primary_risk_case_id <> related_risk_case_id",
            name="ck_risk_episode_association_distinct_cases",
        ),
        sa.CheckConstraint(
            "primary_risk_episode_id <> related_risk_episode_id",
            name="ck_risk_episode_association_distinct_episodes",
        ),
        sa.CheckConstraint(
            "relation_type IN ('same_root_cause', 'causal_symptom', 'shared_incident')",
            name="ck_risk_episode_association_relation_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'revoked')",
            name="ck_risk_episode_association_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('manual', 'approved_policy')",
            name="ck_risk_episode_association_source_type",
        ),
        sa.ForeignKeyConstraint(
            ["primary_risk_case_id"],
            ["netops_risk_cases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["related_risk_case_id"],
            ["netops_risk_cases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for name, columns in (
        ("ix_netops_risk_episode_associations_primary_risk_case_id", ["primary_risk_case_id"]),
        ("ix_netops_risk_episode_associations_primary_risk_episode_id", ["primary_risk_episode_id"]),
        ("ix_netops_risk_episode_associations_related_risk_case_id", ["related_risk_case_id"]),
        ("ix_netops_risk_episode_associations_related_risk_episode_id", ["related_risk_episode_id"]),
        ("ix_netops_risk_episode_associations_relation_type", ["relation_type"]),
        ("ix_netops_risk_episode_associations_status", ["status"]),
        ("ix_netops_risk_episode_associations_source_type", ["source_type"]),
        ("ix_netops_risk_episode_associations_proposed_by", ["proposed_by"]),
        ("ix_netops_risk_episode_associations_reviewed_by", ["reviewed_by"]),
        ("ix_netops_risk_episode_associations_reviewed_at", ["reviewed_at"]),
        ("ix_netops_risk_episode_associations_revoked_by", ["revoked_by"]),
        ("ix_netops_risk_episode_associations_revoked_at", ["revoked_at"]),
        (
            "ix_risk_episode_association_primary_status",
            ["primary_risk_episode_id", "status"],
        ),
    ):
        op.create_index(
            name,
            "netops_risk_episode_associations",
            columns,
            unique=False,
        )
    op.create_index(
        "uq_risk_episode_association_live_pair",
        "netops_risk_episode_associations",
        ["primary_risk_episode_id", "related_risk_episode_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending_review', 'approved')"),
    )
    op.create_index(
        "uq_risk_episode_association_approved_child",
        "netops_risk_episode_associations",
        ["related_risk_episode_id"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_risk_episode_association_approved_child",
        table_name="netops_risk_episode_associations",
    )
    op.drop_index(
        "uq_risk_episode_association_live_pair",
        table_name="netops_risk_episode_associations",
    )
    op.drop_table("netops_risk_episode_associations")
