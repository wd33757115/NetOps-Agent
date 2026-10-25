"""add event rules and collection profile control plane

Revision ID: 0011_rules_profiles
Revises: 0010_state_observation
Create Date: 2026-06-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0011_rules_profiles"
down_revision: Union[str, Sequence[str], None] = "0010_state_observation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_event_rules",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_netops_event_rules_name", "netops_event_rules", ["name"])
    op.create_index("ix_netops_event_rules_status", "netops_event_rules", ["status"])
    op.create_index("uq_netops_event_rules_name", "netops_event_rules", ["name"], unique=True)

    op.create_table(
        "netops_event_rule_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("condition_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["rule_id"], ["netops_event_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("rule_id", "event_type", "severity", "status"):
        op.create_index(
            f"ix_netops_event_rule_versions_{column}",
            "netops_event_rule_versions",
            [column],
        )
    op.create_index(
        "uq_event_rule_version",
        "netops_event_rule_versions",
        ["rule_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_event_rule_active_version",
        "netops_event_rule_versions",
        ["rule_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "netops_collection_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_netops_collection_policies_name", "netops_collection_policies", ["name"])
    op.create_index("ix_netops_collection_policies_status", "netops_collection_policies", ["status"])
    op.create_index("uq_netops_collection_policies_name", "netops_collection_policies", ["name"], unique=True)

    op.create_table(
        "netops_command_profiles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_netops_command_profiles_name", "netops_command_profiles", ["name"])
    op.create_index("ix_netops_command_profiles_status", "netops_command_profiles", ["status"])
    op.create_index("uq_netops_command_profiles_name", "netops_command_profiles", ["name"], unique=True)

    op.create_table(
        "netops_command_profile_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("items_json", sa.JSON(), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["netops_command_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("profile_id", "status"):
        op.create_index(
            f"ix_netops_command_profile_versions_{column}",
            "netops_command_profile_versions",
            [column],
        )
    op.create_index(
        "uq_command_profile_version",
        "netops_command_profile_versions",
        ["profile_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_command_profile_active_version",
        "netops_command_profile_versions",
        ["profile_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "netops_collection_profile_bindings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("profile_version_id", sa.String(length=64), nullable=False),
        sa.Column("selector_json", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["profile_version_id"],
            ["netops_command_profile_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_netops_collection_profile_bindings_profile_version_id",
        "netops_collection_profile_bindings",
        ["profile_version_id"],
    )
    op.create_index(
        "ix_netops_collection_profile_bindings_status",
        "netops_collection_profile_bindings",
        ["status"],
    )


def downgrade() -> None:
    op.drop_table("netops_collection_profile_bindings")
    op.drop_table("netops_command_profile_versions")
    op.drop_index("uq_netops_command_profiles_name", table_name="netops_command_profiles")
    op.drop_table("netops_command_profiles")
    op.drop_index("uq_netops_collection_policies_name", table_name="netops_collection_policies")
    op.drop_table("netops_collection_policies")
    op.drop_table("netops_event_rule_versions")
    op.drop_index("uq_netops_event_rules_name", table_name="netops_event_rules")
    op.drop_table("netops_event_rules")
