"""Require every risk remediation to belong to one risk episode.

Revision ID: 0074_risk_remediation_episode
Revises: 0073_risk_occurrence_episode
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0074_risk_remediation_episode"
down_revision = "0073_risk_occurrence_episode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE netops_risk_remediations AS remediation
        SET risk_episode_id = risk_case.episode_id
        FROM netops_risk_cases AS risk_case
        WHERE remediation.risk_case_id = risk_case.id
          AND remediation.risk_episode_id IS NULL
        """
    )
    op.alter_column(
        "netops_risk_remediations",
        "risk_episode_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "netops_risk_remediations",
        "risk_episode_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )
