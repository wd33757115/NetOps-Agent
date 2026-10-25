"""Close collection ownership and derived-fact identity gaps.

Revision ID: 0031_patrol_fact_ownership
Revises: 0030_patrol_ownership_fencing
"""

from __future__ import annotations

import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "0031_patrol_fact_ownership"
down_revision = "0030_patrol_ownership_fencing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    fact_time_columns = {
        "netops_command_snapshots": ("observed_at",),
        "netops_patrol_collection_results": ("observed_at",),
        "netops_raw_evidence": ("observed_at",),
        "netops_state_observations": ("observed_at",),
        "netops_current_state": ("observed_at", "missing_since"),
        "netops_network_changes": ("observed_at", "detected_at", "processed_at"),
        "netops_network_change_decisions": ("observed_at", "expires_at"),
    }
    for table_name, columns in fact_time_columns.items():
        for column_name in columns:
            op.execute(
                f"""
                ALTER TABLE {table_name}
                ALTER COLUMN {column_name} TYPE TIMESTAMPTZ
                USING {column_name} AT TIME ZONE current_setting('TIMEZONE')
                """
            )
    op.add_column(
        "netops_patrol_runs",
        sa.Column("owner_generation", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "netops_patrol_runs", sa.Column("owner_token", sa.String(64), nullable=True)
    )
    op.create_index(
        "ix_netops_patrol_runs_owner_token", "netops_patrol_runs", ["owner_token"]
    )

    op.add_column(
        "netops_state_observations",
        sa.Column("execution_item_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "netops_state_observations",
        sa.Column("fact_order_key", sa.String(512), server_default="", nullable=False),
    )
    op.execute(
        """
        UPDATE netops_state_observations AS observation
        SET execution_item_id = snapshot.execution_item_id,
            fact_order_key = lpad(
                COALESCE(observation.metadata_json->>'command_ordinal', '0'), 20, '0'
            ) || '|' || snapshot.execution_item_id || '|' || observation.id
        FROM netops_command_snapshots AS snapshot
        WHERE snapshot.id = observation.snapshot_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM netops_state_observations
                WHERE execution_item_id IS NULL
            ) THEN
                RAISE EXCEPTION 'state observations without execution_item_id remain';
            END IF;
        END $$
        """
    )
    op.alter_column(
        "netops_state_observations", "execution_item_id", nullable=False
    )
    op.create_index(
        "ix_netops_state_observations_execution_item_id",
        "netops_state_observations",
        ["execution_item_id"],
    )

    op.add_column(
        "netops_current_state",
        sa.Column("fact_order_key", sa.String(512), server_default="", nullable=False),
    )
    op.add_column(
        "netops_current_state",
        sa.Column("source_execution_item_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "netops_current_state",
        sa.Column("source_observation_id", sa.String(64), nullable=True),
    )
    op.execute(
        """
        UPDATE netops_current_state AS current
        SET fact_order_key = observation.fact_order_key,
            source_execution_item_id = observation.execution_item_id,
            source_observation_id = observation.id
        FROM netops_state_observations AS observation
        WHERE observation.id = current.id
        """
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, device_id, entity_type, entity_key, field_name,
                   observation_type, observed_at, fact_order_key
            FROM netops_current_state
            """
        )
    ).mappings().all()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        semantic_field = row["observation_type"] or str(row["field_name"] or "").strip().lower()
        encoded = json.dumps(
            [row["device_id"], row["entity_type"], row["entity_key"], semantic_field],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        state_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        grouped.setdefault(state_key, []).append(dict(row))
    for state_key, candidates in grouped.items():
        winner = max(
            candidates,
            key=lambda item: (
                item["observed_at"],
                str(item["fact_order_key"] or ""),
                str(item["id"]),
            ),
        )
        loser_ids = [item["id"] for item in candidates if item["id"] != winner["id"]]
        if loser_ids:
            connection.execute(
                sa.text("DELETE FROM netops_current_state WHERE id = ANY(:ids)"),
                {"ids": loser_ids},
            )
        connection.execute(
            sa.text("UPDATE netops_current_state SET state_key=:key WHERE id=:id"),
            {"key": state_key, "id": winner["id"]},
        )
    op.create_index(
        "ix_netops_current_state_source_execution_item_id",
        "netops_current_state",
        ["source_execution_item_id"],
    )
    op.create_index(
        "ix_netops_current_state_source_observation_id",
        "netops_current_state",
        ["source_observation_id"],
    )

    op.add_column(
        "parser_outbox_events", sa.Column("claim_token", sa.String(64), nullable=True)
    )
    op.add_column(
        "parser_outbox_events",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "parser_outbox_events", sa.Column("last_error", sa.Text(), nullable=True)
    )
    op.create_index(
        "ix_parser_outbox_events_claim_token",
        "parser_outbox_events",
        ["claim_token"],
    )
    op.create_index(
        "ix_parser_outbox_events_lease_until",
        "parser_outbox_events",
        ["lease_until"],
    )

    op.add_column(
        "netops_network_events",
        sa.Column("last_seen_order_key", sa.String(256), server_default="", nullable=False),
    )
    op.execute(
        """
        UPDATE netops_network_events
        SET last_seen_order_key = '0|' || COALESCE(latest_change_id, source_change_id, id)
        """
    )
    event_rows = connection.execute(
        sa.text(
            """
            SELECT id, source_change_id, event_type, device_id, entity_type,
                   entity_key, run_id, correlation_key, payload_json
            FROM netops_network_events
            """
        )
    ).mappings().all()
    for row in event_rows:
        rule = dict((row["payload_json"] or {}).get("rule") or {})
        encoded = json.dumps(
            [
                row["source_change_id"],
                row["event_type"],
                row["device_id"],
                row["entity_type"],
                row["entity_key"],
                row["run_id"],
                row["correlation_key"] or "",
                rule.get("rule_id"),
                rule.get("version"),
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        idempotency_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        connection.execute(
            sa.text(
                "UPDATE netops_network_events SET idempotency_key=:key WHERE id=:id"
            ),
            {"key": idempotency_key, "id": row["id"]},
        )


def downgrade() -> None:
    op.drop_column("netops_network_events", "last_seen_order_key")
    op.drop_index("ix_parser_outbox_events_lease_until", table_name="parser_outbox_events")
    op.drop_index("ix_parser_outbox_events_claim_token", table_name="parser_outbox_events")
    op.drop_column("parser_outbox_events", "last_error")
    op.drop_column("parser_outbox_events", "lease_until")
    op.drop_column("parser_outbox_events", "claim_token")

    op.drop_index(
        "ix_netops_current_state_source_observation_id",
        table_name="netops_current_state",
    )
    op.drop_index(
        "ix_netops_current_state_source_execution_item_id",
        table_name="netops_current_state",
    )
    op.drop_column("netops_current_state", "source_observation_id")
    op.drop_column("netops_current_state", "source_execution_item_id")
    op.drop_column("netops_current_state", "fact_order_key")

    op.drop_index(
        "ix_netops_state_observations_execution_item_id",
        table_name="netops_state_observations",
    )
    op.drop_column("netops_state_observations", "fact_order_key")
    op.drop_column("netops_state_observations", "execution_item_id")

    op.drop_index("ix_netops_patrol_runs_owner_token", table_name="netops_patrol_runs")
    op.drop_column("netops_patrol_runs", "owner_token")
    op.drop_column("netops_patrol_runs", "owner_generation")

    fact_time_columns = {
        "netops_command_snapshots": ("observed_at",),
        "netops_patrol_collection_results": ("observed_at",),
        "netops_raw_evidence": ("observed_at",),
        "netops_state_observations": ("observed_at",),
        "netops_current_state": ("observed_at", "missing_since"),
        "netops_network_changes": ("observed_at", "detected_at", "processed_at"),
        "netops_network_change_decisions": ("observed_at", "expires_at"),
    }
    for table_name, columns in fact_time_columns.items():
        for column_name in columns:
            op.execute(
                f"""
                ALTER TABLE {table_name}
                ALTER COLUMN {column_name} TYPE TIMESTAMP WITHOUT TIME ZONE
                USING {column_name} AT TIME ZONE current_setting('TIMEZONE')
                """
            )
