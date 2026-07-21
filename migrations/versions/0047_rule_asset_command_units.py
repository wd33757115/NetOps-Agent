"""Add command-level rule asset production projections.

Revision ID: 0047_rule_asset_command_units
Revises: 0046_rule_job_projections
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0047_rule_asset_command_units"
down_revision = "0046_rule_job_projections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_rule_asset_command_units",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "offline_job_id",
            sa.String(length=64),
            sa.ForeignKey("netops_offline_patrol_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_key", sa.String(length=64), nullable=False),
        sa.Column("vendor", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("models_json", sa.JSON(), nullable=False),
        sa.Column("family", sa.String(length=128), nullable=True),
        sa.Column("profile", sa.String(length=128), nullable=False),
        sa.Column("canonical_command", sa.String(length=512), nullable=False),
        sa.Column("handling", sa.String(length=32), nullable=False),
        sa.Column("asset_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("device_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_signature_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocker_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parse_counts_json", sa.JSON(), nullable=False),
        sa.Column("stage_states_json", sa.JSON(), nullable=False),
        sa.Column("next_action_json", sa.JSON(), nullable=False),
        sa.Column("parser_job_id", sa.String(length=64), nullable=True),
        sa.Column("parser_template_id", sa.String(length=64), nullable=True),
        sa.Column("rule_seed_job_id", sa.String(length=64), nullable=True),
        sa.Column("context_id", sa.String(length=64), nullable=True),
        sa.Column("bundle_id", sa.String(length=64), nullable=True),
        sa.Column("projection_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_rule_asset_command_unit_job_key",
        "netops_rule_asset_command_units",
        ["offline_job_id", "asset_key"],
        unique=True,
    )
    op.create_index(
        "ix_rule_asset_command_unit_job_status",
        "netops_rule_asset_command_units",
        ["offline_job_id", "status"],
    )
    op.create_index(
        "ix_rule_asset_command_unit_job_stage",
        "netops_rule_asset_command_units",
        ["offline_job_id", "current_stage"],
    )
    op.create_index(
        "ix_rule_asset_command_unit_job_mode",
        "netops_rule_asset_command_units",
        ["offline_job_id", "asset_mode"],
    )
    op.create_index(
        "ix_rule_asset_command_unit_identity",
        "netops_rule_asset_command_units",
        ["vendor", "profile", "canonical_command"],
    )


def downgrade() -> None:
    op.drop_table("netops_rule_asset_command_units")
