"""Separate technical recovery, remediation acceptance, and risk closure.

Revision ID: 0072_risk_closure_governance
Revises: 0071_report_request_ledger
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0072_risk_closure_governance"
down_revision = "0071_report_request_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_risk_cases",
        sa.Column("episode_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_risk_cases",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "netops_risk_cases",
        sa.Column("closed_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "netops_risk_cases",
        sa.Column("closure_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "netops_risk_cases",
        sa.Column("closure_disposition", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "netops_risk_cases",
        sa.Column("closure_evidence_refs_json", sa.JSON(), nullable=True),
    )
    op.execute(
        "UPDATE netops_risk_cases SET episode_id = id "
        "WHERE episode_id IS NULL"
    )
    op.alter_column(
        "netops_risk_cases",
        "episode_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.create_index(
        "ix_risk_case_episode_id",
        "netops_risk_cases",
        ["episode_id"],
    )
    op.create_index(
        "ix_risk_case_closed_at",
        "netops_risk_cases",
        ["closed_at"],
    )

    op.add_column(
        "netops_risk_remediations",
        sa.Column("risk_episode_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_risk_remediations",
        sa.Column("accepted_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "netops_risk_remediations",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "netops_risk_remediations",
        sa.Column("acceptance_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "netops_risk_remediations",
        sa.Column(
            "acceptance_evidence_refs_json",
            sa.JSON(),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE netops_risk_remediations AS remediation
        SET risk_episode_id = risk_case.episode_id
        FROM netops_risk_cases AS risk_case
        WHERE remediation.risk_case_id = risk_case.id
          AND remediation.risk_episode_id IS NULL
        """
    )
    op.create_index(
        "ix_risk_remediation_episode_id",
        "netops_risk_remediations",
        ["risk_episode_id"],
    )

    op.drop_constraint(
        "ck_risk_case_remediation_status",
        "netops_risk_cases",
        type_="check",
    )
    op.create_check_constraint(
        "ck_risk_case_remediation_status",
        "netops_risk_cases",
        "remediation_status IN ("
        "'not_started', 'planned', 'in_progress', 'completed', "
        "'accepted', 'cancelled')",
    )
    op.drop_constraint(
        "ck_risk_remediation_status",
        "netops_risk_remediations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_risk_remediation_status",
        "netops_risk_remediations",
        "status IN ("
        "'planned', 'in_progress', 'completed', 'accepted', 'cancelled')",
    )
    op.drop_constraint(
        "ck_risk_occurrence_kind",
        "netops_risk_occurrences",
        type_="check",
    )
    op.create_check_constraint(
        "ck_risk_occurrence_kind",
        "netops_risk_occurrences",
        "occurrence_kind IN ("
        "'opened', 'observed', 'reopened', 'acknowledged', 'recovering', "
        "'resolved', 'closed', 'suppressed', 'unable_to_confirm')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE netops_risk_remediations SET status = 'completed' "
        "WHERE status = 'accepted'"
    )
    op.execute(
        "UPDATE netops_risk_cases SET remediation_status = 'completed' "
        "WHERE remediation_status = 'accepted'"
    )
    op.execute(
        "DELETE FROM netops_risk_occurrences "
        "WHERE occurrence_kind = 'closed'"
    )
    op.drop_constraint(
        "ck_risk_occurrence_kind",
        "netops_risk_occurrences",
        type_="check",
    )
    op.create_check_constraint(
        "ck_risk_occurrence_kind",
        "netops_risk_occurrences",
        "occurrence_kind IN ("
        "'opened', 'observed', 'reopened', 'acknowledged', 'recovering', "
        "'resolved', 'suppressed', 'unable_to_confirm')",
    )
    op.drop_constraint(
        "ck_risk_remediation_status",
        "netops_risk_remediations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_risk_remediation_status",
        "netops_risk_remediations",
        "status IN ('planned', 'in_progress', 'completed', 'cancelled')",
    )
    op.drop_constraint(
        "ck_risk_case_remediation_status",
        "netops_risk_cases",
        type_="check",
    )
    op.create_check_constraint(
        "ck_risk_case_remediation_status",
        "netops_risk_cases",
        "remediation_status IN ("
        "'not_started', 'planned', 'in_progress', 'completed', 'cancelled')",
    )

    op.drop_index(
        "ix_risk_remediation_episode_id",
        table_name="netops_risk_remediations",
    )
    for column in (
        "acceptance_evidence_refs_json",
        "acceptance_reason",
        "accepted_at",
        "accepted_by",
        "risk_episode_id",
    ):
        op.drop_column("netops_risk_remediations", column)

    op.drop_index(
        "ix_risk_case_closed_at",
        table_name="netops_risk_cases",
    )
    op.drop_index(
        "ix_risk_case_episode_id",
        table_name="netops_risk_cases",
    )
    for column in (
        "closure_evidence_refs_json",
        "closure_disposition",
        "closure_reason",
        "closed_by",
        "closed_at",
        "episode_id",
    ):
        op.drop_column("netops_risk_cases", column)
