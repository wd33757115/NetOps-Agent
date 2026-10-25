"""add patrol chain trust, presence, provenance, and outbox models

Revision ID: 0024_patrol_chain_trust_model
Revises: 0023_rule_asset_model_fill
Create Date: 2026-07-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0024_patrol_chain_trust_model"
down_revision: Union[str, Sequence[str], None] = "0023_rule_asset_model_fill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("netops_patrol_runs", sa.Column("collection_status", sa.String(32), nullable=False, server_default="running"))
    op.add_column("netops_patrol_runs", sa.Column("analysis_status", sa.String(32), nullable=False, server_default="pending"))
    op.add_column("netops_patrol_runs", sa.Column("baseline_eligible", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("netops_patrol_runs", sa.Column("run_purpose", sa.String(32), nullable=False, server_default="scheduled_patrol"))
    for column in ("collection_status", "analysis_status", "baseline_eligible", "run_purpose"):
        op.create_index(f"ix_netops_patrol_runs_{column}", "netops_patrol_runs", [column])
    op.execute("UPDATE netops_patrol_runs SET collection_status = CASE WHEN status = 'completed' THEN 'completed' ELSE status END")
    op.execute("UPDATE netops_patrol_runs SET analysis_status = 'completed', baseline_eligible = true WHERE status = 'completed'")

    op.create_table(
        "netops_patrol_collection_results",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(128), sa.ForeignKey("netops_patrol_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("inventory_device_id", sa.String(64), sa.ForeignKey("netops_devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("device_id", sa.String(256), nullable=False),
        sa.Column("canonical_command", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("authoritative_for_deletion", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("baseline_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("observed_entity_count", sa.Integer(), nullable=True),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("run_id", "device_id", "canonical_command", name="uq_netops_patrol_collection_result"),
    )
    for column in ("run_id", "inventory_device_id", "device_id", "canonical_command", "status", "baseline_eligible", "failure_code", "observed_at"):
        op.create_index(f"ix_netops_patrol_collection_results_{column}", "netops_patrol_collection_results", [column])

    for table in ("netops_state_observations", "netops_current_state"):
        op.add_column(table, sa.Column("observation_type", sa.String(256), nullable=True))
        op.add_column(table, sa.Column("semantic_type", sa.String(64), nullable=True))
        op.add_column(table, sa.Column("unit", sa.String(64), nullable=True))
        op.create_index(f"ix_{table}_observation_type", table, ["observation_type"])
        op.create_index(f"ix_{table}_semantic_type", table, ["semantic_type"])

    op.add_column("netops_current_state", sa.Column("lifecycle_status", sa.String(32), nullable=False, server_default="active"))
    op.add_column("netops_current_state", sa.Column("missing_since", sa.DateTime(), nullable=True))
    op.add_column("netops_current_state", sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_netops_current_state_lifecycle_status", "netops_current_state", ["lifecycle_status"])
    op.create_index("ix_netops_current_state_missing_since", "netops_current_state", ["missing_since"])

    change_columns = (
        sa.Column("observation_type", sa.String(256), nullable=True),
        sa.Column("change_policy_id", sa.String(64), nullable=True),
        sa.Column("change_policy_version_id", sa.String(64), nullable=True),
        sa.Column("comparison_type", sa.String(64), nullable=True),
        sa.Column("decision_reason", sa.String(256), nullable=True),
        sa.Column("policy_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
    )
    for column in change_columns:
        op.add_column("netops_network_changes", column)
    op.execute("UPDATE netops_network_changes SET processed_at = COALESCE(created_at, detected_at)")
    op.alter_column("netops_network_changes", "processed_at", nullable=False)
    for column in ("observation_type", "change_policy_id", "change_policy_version_id", "observed_at", "processed_at"):
        op.create_index(f"ix_netops_network_changes_{column}", "netops_network_changes", [column])

    op.create_table(
        "netops_network_event_outbox",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), sa.ForeignKey("netops_network_events.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("message_id", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    for column in ("event_id", "event_type", "status", "available_at"):
        op.create_index(f"ix_netops_network_event_outbox_{column}", "netops_network_event_outbox", [column])

    event_rule_columns = (
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("exclusive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("event_class", sa.String(32), nullable=False, server_default="fault"),
        sa.Column("recovery_json", sa.JSON(), nullable=True),
        sa.Column("correlation_key_json", sa.JSON(), nullable=True),
    )
    for column in event_rule_columns:
        op.add_column("netops_event_rule_versions", column)
    op.create_index("ix_netops_event_rule_versions_priority", "netops_event_rule_versions", ["priority"])
    op.create_index("ix_netops_event_rule_versions_event_class", "netops_event_rule_versions", ["event_class"])
    recovery_pairs = {
        "CPURecovered": '["CPUHigh"]',
        "MemoryRecovered": '["MemoryHigh"]',
        "InterfaceUp": '["InterfaceDown"]',
        "BGPNeighborEstablished": '["BGPNeighborLost"]',
        "OpticalRecovered": '["OpticalRxPowerLow", "OpticalTxPowerLow"]',
    }
    for event_type, resolves in recovery_pairs.items():
        op.execute(
            sa.text(
                "UPDATE netops_event_rule_versions "
                "SET event_class = 'recovery', "
                "recovery_json = CAST(:recovery AS JSON) "
                "WHERE event_type = :event_type"
            ).bindparams(
                recovery='{"resolves": ' + resolves + "}",
                event_type=event_type,
            )
        )
    op.add_column("netops_observation_contracts", sa.Column("compatibility_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("netops_observation_contracts", "compatibility_json")
    for column in ("correlation_key_json", "recovery_json", "event_class", "exclusive", "priority"):
        op.drop_column("netops_event_rule_versions", column)
    op.drop_table("netops_network_event_outbox")
    for column in ("processed_at", "observed_at", "policy_snapshot_json", "decision_reason", "comparison_type", "change_policy_version_id", "change_policy_id", "observation_type"):
        op.drop_column("netops_network_changes", column)
    for column in ("missing_count", "missing_since", "lifecycle_status", "unit", "semantic_type", "observation_type"):
        op.drop_column("netops_current_state", column)
    for column in ("unit", "semantic_type", "observation_type"):
        op.drop_column("netops_state_observations", column)
    op.drop_table("netops_patrol_collection_results")
    for column in ("run_purpose", "baseline_eligible", "analysis_status", "collection_status"):
        op.drop_column("netops_patrol_runs", column)
