"""Bind immutable risk occurrences to one risk episode.

Revision ID: 0073_risk_occurrence_episode
Revises: 0072_risk_closure_governance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0073_risk_occurrence_episode"
down_revision = "0072_risk_closure_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_risk_occurrences",
        sa.Column("risk_episode_id", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        UPDATE netops_risk_occurrences AS occurrence
        SET risk_episode_id = risk_case.episode_id
        FROM netops_risk_cases AS risk_case
        WHERE occurrence.risk_case_id = risk_case.id
          AND occurrence.risk_episode_id IS NULL
        """
    )
    op.alter_column(
        "netops_risk_occurrences",
        "risk_episode_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.create_index(
        "ix_risk_occurrence_episode_id",
        "netops_risk_occurrences",
        ["risk_episode_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_risk_occurrence_episode_id",
        table_name="netops_risk_occurrences",
    )
    op.drop_column("netops_risk_occurrences", "risk_episode_id")
