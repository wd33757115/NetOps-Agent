"""Freeze assessment policy snapshots and reconstructable decision bases.

Revision ID: 0086_assessment_basis
Revises: 0085_risk_episode_association
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0086_assessment_basis"
down_revision = "0085_risk_episode_association"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_check_executions",
        sa.Column(
            "assessment_policy_snapshot_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "netops_check_executions",
        sa.Column("assessment_policy_snapshot_digest", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_netops_check_executions_assessment_policy_snapshot_digest",
        "netops_check_executions",
        ["assessment_policy_snapshot_digest"],
        unique=False,
    )

    op.add_column(
        "netops_check_assessments",
        sa.Column("assessment_policy_snapshot_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_check_assessments",
        sa.Column(
            "assessment_basis_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "netops_check_assessments",
        sa.Column("assessment_basis_digest", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_netops_check_assessments_assessment_policy_snapshot_digest",
        "netops_check_assessments",
        ["assessment_policy_snapshot_digest"],
        unique=False,
    )
    op.create_index(
        "ix_netops_check_assessments_assessment_basis_digest",
        "netops_check_assessments",
        ["assessment_basis_digest"],
        unique=False,
    )

    op.add_column(
        "netops_current_state_findings",
        sa.Column(
            "assessment_basis_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "netops_current_state_findings",
        sa.Column("assessment_basis_digest", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_netops_current_state_findings_assessment_basis_digest",
        "netops_current_state_findings",
        ["assessment_basis_digest"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_current_state_findings_assessment_basis_digest",
        table_name="netops_current_state_findings",
    )
    op.drop_column("netops_current_state_findings", "assessment_basis_digest")
    op.drop_column("netops_current_state_findings", "assessment_basis_json")

    op.drop_index(
        "ix_netops_check_assessments_assessment_basis_digest",
        table_name="netops_check_assessments",
    )
    op.drop_index(
        "ix_netops_check_assessments_assessment_policy_snapshot_digest",
        table_name="netops_check_assessments",
    )
    op.drop_column("netops_check_assessments", "assessment_basis_digest")
    op.drop_column("netops_check_assessments", "assessment_basis_json")
    op.drop_column("netops_check_assessments", "assessment_policy_snapshot_digest")

    op.drop_index(
        "ix_netops_check_executions_assessment_policy_snapshot_digest",
        table_name="netops_check_executions",
    )
    op.drop_column("netops_check_executions", "assessment_policy_snapshot_digest")
    op.drop_column("netops_check_executions", "assessment_policy_snapshot_json")
