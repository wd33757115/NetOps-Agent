"""Add immutable report claim ledgers.

Revision ID: 0077_report_claim_ledger
Revises: 0076_source_qualification
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0077_report_claim_ledger"
down_revision = "0076_source_qualification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_report_manifests",
        sa.Column("claim_ledger_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_report_manifests",
        sa.Column(
            "claim_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        op.f("ix_netops_report_manifests_claim_ledger_digest"),
        "netops_report_manifests",
        ["claim_ledger_digest"],
    )
    op.create_table(
        "netops_report_claims",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "report_manifest_id",
            sa.String(length=64),
            sa.ForeignKey("netops_report_manifests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("claim_code", sa.String(length=256), nullable=False),
        sa.Column("claim_scope", sa.String(length=32), nullable=False),
        sa.Column("assertion_level", sa.String(length=32), nullable=False),
        sa.Column("allowed_phrase_code", sa.String(length=128), nullable=False),
        sa.Column("subject_json", sa.JSON(), nullable=False),
        sa.Column("fact_refs_json", sa.JSON(), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("limitation_refs_json", sa.JSON(), nullable=False),
        sa.Column("authority_context_digest", sa.String(length=64), nullable=False),
        sa.Column("source_facts_digest", sa.String(length=64), nullable=False),
        sa.Column("claim_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "claim_scope IN ('network', 'site', 'device_group', 'device', 'check_item', 'entity')",
            name="ck_report_claim_scope",
        ),
        sa.CheckConstraint(
            "assertion_level IN ('observed', 'assessed', 'current', 'historical', 'limited')",
            name="ck_report_claim_assertion_level",
        ),
        sa.UniqueConstraint(
            "report_manifest_id",
            "claim_code",
            name="uq_report_claim_manifest_code",
        ),
    )
    for column_name in (
        "report_manifest_id",
        "patrol_batch_id",
        "claim_scope",
        "assertion_level",
        "allowed_phrase_code",
        "authority_context_digest",
        "source_facts_digest",
    ):
        op.create_index(
            op.f(f"ix_netops_report_claims_{column_name}"),
            "netops_report_claims",
            [column_name],
        )
    op.create_index(
        op.f("ix_netops_report_claims_claim_digest"),
        "netops_report_claims",
        ["claim_digest"],
        unique=True,
    )
    op.add_column(
        "netops_report_releases",
        sa.Column("claim_ledger_digest", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_netops_report_releases_claim_ledger_digest"),
        "netops_report_releases",
        ["claim_ledger_digest"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_netops_report_releases_claim_ledger_digest"),
        table_name="netops_report_releases",
    )
    op.drop_column("netops_report_releases", "claim_ledger_digest")
    op.drop_table("netops_report_claims")
    op.drop_index(
        op.f("ix_netops_report_manifests_claim_ledger_digest"),
        table_name="netops_report_manifests",
    )
    op.drop_column("netops_report_manifests", "claim_count")
    op.drop_column("netops_report_manifests", "claim_ledger_digest")
