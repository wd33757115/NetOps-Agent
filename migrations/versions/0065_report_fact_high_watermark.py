"""Freeze the report fact-ingestion high-watermark.

Revision ID: 0065_report_fact_high_watermark
Revises: 0064_contract_entity_identity
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0065_report_fact_high_watermark"
down_revision = "0064_contract_entity_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_report_manifests",
        sa.Column(
            "fact_ingestion_high_watermark",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE netops_report_manifests "
            "SET fact_ingestion_high_watermark = generated_at "
            "WHERE fact_ingestion_high_watermark IS NULL"
        )
    )
    op.alter_column(
        "netops_report_manifests",
        "fact_ingestion_high_watermark",
        nullable=False,
    )
    op.create_index(
        "ix_netops_report_manifests_fact_ingestion_high_watermark",
        "netops_report_manifests",
        ["fact_ingestion_high_watermark"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_report_manifests_fact_ingestion_high_watermark",
        table_name="netops_report_manifests",
    )
    op.drop_column(
        "netops_report_manifests",
        "fact_ingestion_high_watermark",
    )
