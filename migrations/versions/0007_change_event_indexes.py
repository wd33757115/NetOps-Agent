"""reconcile change and event idempotency indexes

Revision ID: 0007_change_event_indexes
Revises: 0006_change_event_data_plane
Create Date: 2026-06-15
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0007_change_event_indexes"
down_revision: Union[str, Sequence[str], None] = "0006_change_event_data_plane"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("netops_network_changes", "netops_network_events"):
        op.drop_index(f"ix_{table}_idempotency_key", table_name=table)
        op.drop_constraint(
            f"{table}_idempotency_key_key",
            table,
            type_="unique",
        )
        op.create_index(
            f"ix_{table}_idempotency_key",
            table,
            ["idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    for table in ("netops_network_changes", "netops_network_events"):
        op.drop_index(f"ix_{table}_idempotency_key", table_name=table)
        op.create_unique_constraint(
            f"{table}_idempotency_key_key",
            table,
            ["idempotency_key"],
        )
        op.create_index(
            f"ix_{table}_idempotency_key",
            table,
            ["idempotency_key"],
            unique=False,
        )
