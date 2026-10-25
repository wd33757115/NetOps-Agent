"""Freeze report purpose and the raw evidence identity set.

Revision ID: 0067_report_purpose_raw_evidence
Revises: 0066_coverage_not_evaluable
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0067_report_purpose_raw_evidence"
down_revision = "0066_coverage_not_evaluable"
branch_labels = None
depends_on = None

_PURPOSE_CHECK = (
    "report_purpose IN ("
    "'production_current', 'offline_historical', "
    "'shadow_validation', 'parser_validation', "
    "'rule_asset_replay'"
    ")"
)


def upgrade() -> None:
    op.add_column(
        "netops_patrol_batches",
        sa.Column("report_purpose", sa.String(length=32), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE netops_patrol_batches "
            "SET report_purpose = CASE "
            "WHEN patrol_mode = 'parser_validation' "
            "THEN 'parser_validation' "
            "ELSE 'shadow_validation' END "
            "WHERE report_purpose IS NULL"
        )
    )
    op.alter_column(
        "netops_patrol_batches",
        "report_purpose",
        nullable=False,
        server_default=sa.text("'production_current'"),
    )
    op.create_check_constraint(
        "ck_patrol_batch_report_purpose",
        "netops_patrol_batches",
        _PURPOSE_CHECK,
    )
    op.create_index(
        "ix_netops_patrol_batches_report_purpose",
        "netops_patrol_batches",
        ["report_purpose"],
    )

    op.add_column(
        "netops_report_manifests",
        sa.Column("report_purpose", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "netops_report_manifests",
        sa.Column(
            "raw_evidence_set_digest",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE netops_report_manifests AS manifest "
            "SET report_purpose = COALESCE(("
            "SELECT batch.report_purpose "
            "FROM netops_patrol_batches AS batch "
            "WHERE batch.patrol_batch_id = manifest.patrol_batch_id"
            "), 'shadow_validation'), "
            "raw_evidence_set_digest = manifest.evidence_index_digest "
            "WHERE manifest.report_purpose IS NULL "
            "OR manifest.raw_evidence_set_digest IS NULL"
        )
    )
    op.alter_column(
        "netops_report_manifests",
        "report_purpose",
        nullable=False,
        server_default=sa.text("'shadow_validation'"),
    )
    op.alter_column(
        "netops_report_manifests",
        "raw_evidence_set_digest",
        nullable=False,
    )
    op.create_check_constraint(
        "ck_report_manifest_purpose",
        "netops_report_manifests",
        _PURPOSE_CHECK,
    )
    op.create_index(
        "ix_netops_report_manifests_report_purpose",
        "netops_report_manifests",
        ["report_purpose"],
    )
    op.create_index(
        "ix_netops_report_manifests_raw_evidence_set_digest",
        "netops_report_manifests",
        ["raw_evidence_set_digest"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_report_manifests_raw_evidence_set_digest",
        table_name="netops_report_manifests",
    )
    op.drop_index(
        "ix_netops_report_manifests_report_purpose",
        table_name="netops_report_manifests",
    )
    op.drop_constraint(
        "ck_report_manifest_purpose",
        "netops_report_manifests",
        type_="check",
    )
    op.drop_column(
        "netops_report_manifests",
        "raw_evidence_set_digest",
    )
    op.drop_column("netops_report_manifests", "report_purpose")

    op.drop_index(
        "ix_netops_patrol_batches_report_purpose",
        table_name="netops_patrol_batches",
    )
    op.drop_constraint(
        "ck_patrol_batch_report_purpose",
        "netops_patrol_batches",
        type_="check",
    )
    op.drop_column("netops_patrol_batches", "report_purpose")
