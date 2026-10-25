"""align patrol trust model unique indexes with SQLAlchemy metadata

Revision ID: 0025_patrol_schema_alignment
Revises: 0024_patrol_chain_trust_model
Create Date: 2026-07-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0025_patrol_schema_alignment"
down_revision: Union[str, Sequence[str], None] = "0024_patrol_chain_trust_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "netops_network_changes",
        "processed_at",
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    op.drop_constraint(
        "uq_netops_patrol_collection_result",
        "netops_patrol_collection_results",
        type_="unique",
    )
    op.create_index(
        "uq_netops_patrol_collection_result",
        "netops_patrol_collection_results",
        ["run_id", "device_id", "canonical_command"],
        unique=True,
    )

    op.drop_constraint(
        "netops_network_event_outbox_event_id_key",
        "netops_network_event_outbox",
        type_="unique",
    )
    op.drop_index(
        "ix_netops_network_event_outbox_event_id",
        table_name="netops_network_event_outbox",
    )
    op.create_index(
        "ix_netops_network_event_outbox_event_id",
        "netops_network_event_outbox",
        ["event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.alter_column(
        "netops_network_changes",
        "processed_at",
        server_default=None,
    )
    op.drop_index(
        "ix_netops_network_event_outbox_event_id",
        table_name="netops_network_event_outbox",
    )
    op.create_index(
        "ix_netops_network_event_outbox_event_id",
        "netops_network_event_outbox",
        ["event_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "netops_network_event_outbox_event_id_key",
        "netops_network_event_outbox",
        ["event_id"],
    )

    op.drop_index(
        "uq_netops_patrol_collection_result",
        table_name="netops_patrol_collection_results",
    )
    op.create_unique_constraint(
        "uq_netops_patrol_collection_result",
        "netops_patrol_collection_results",
        ["run_id", "device_id", "canonical_command"],
    )
