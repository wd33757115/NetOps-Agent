"""Add RawEvidence.source (cli|gnmi|snmp|syslog).

Revision ID: 0050_raw_evidence_source
Revises: 0049_parser_gap_ledger
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_raw_evidence_source"
down_revision = "0049_parser_gap_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_raw_evidence",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="cli"),
    )
    op.create_index("ix_netops_raw_evidence_source", "netops_raw_evidence", ["source"])
    op.execute(
        sa.text(
            "UPDATE netops_raw_evidence SET source = CASE "
            "WHEN lower(source_type) IN ('cli', 'gnmi', 'snmp', 'syslog') THEN lower(source_type) "
            "ELSE 'cli' END"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_netops_raw_evidence_source", table_name="netops_raw_evidence")
    op.drop_column("netops_raw_evidence", "source")
