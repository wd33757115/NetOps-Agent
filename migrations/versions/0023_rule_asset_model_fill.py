"""fill rule asset model gaps

Revision ID: 0023_rule_asset_model_fill
Revises: 0022_rule_generation_jobs
Create Date: 2026-07-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0023_rule_asset_model_fill"
down_revision: Union[str, Sequence[str], None] = "0022_rule_generation_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "netops_rule_generation_jobs",
        sa.Column("asset_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_rule_generation_jobs",
        sa.Column("asset_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_rule_generation_jobs",
        sa.Column("asset_version_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_rule_generation_jobs",
        sa.Column("asset_digest", sa.String(length=64), nullable=True),
    )
    for column in ("asset_type", "asset_id", "asset_version_id", "asset_digest"):
        op.create_index(
            f"ix_netops_rule_generation_jobs_{column}",
            "netops_rule_generation_jobs",
            [column],
        )

    op.add_column(
        "netops_observation_contracts",
        sa.Column("source_job_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_observation_contracts",
        sa.Column("approval_json", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_netops_observation_contracts_source_job_id",
        "netops_observation_contracts",
        ["source_job_id"],
    )

    op.add_column(
        "netops_event_rule_versions",
        sa.Column("source_job_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_event_rule_versions",
        sa.Column("validation_report_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "netops_event_rule_versions",
        sa.Column("digest", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_netops_event_rule_versions_source_job_id",
        "netops_event_rule_versions",
        ["source_job_id"],
    )
    op.create_index(
        "ix_netops_event_rule_versions_digest",
        "netops_event_rule_versions",
        ["digest"],
    )

    op.create_table(
        "netops_change_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("observation_type", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    for column in ("name", "observation_type", "status"):
        op.create_index(
            f"ix_netops_change_policies_{column}",
            "netops_change_policies",
            [column],
        )

    op.create_table(
        "netops_change_policy_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("observation_type", sa.String(length=256), nullable=False),
        sa.Column("semantic_type", sa.String(length=64), nullable=False),
        sa.Column("compare_mode", sa.String(length=64), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("validation_report_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_job_id", sa.String(length=64), nullable=True),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["netops_change_policies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "policy_id",
        "observation_type",
        "semantic_type",
        "compare_mode",
        "status",
        "source_job_id",
        "digest",
    ):
        op.create_index(
            f"ix_netops_change_policy_versions_{column}",
            "netops_change_policy_versions",
            [column],
        )
    op.create_index(
        "uq_change_policy_version",
        "netops_change_policy_versions",
        ["policy_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_change_policy_active_version",
        "netops_change_policy_versions",
        ["policy_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_change_policy_version_digest",
        "netops_change_policy_versions",
        ["policy_id", "version", "digest"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_change_policy_version_digest",
        table_name="netops_change_policy_versions",
    )
    op.drop_index(
        "uq_change_policy_active_version",
        table_name="netops_change_policy_versions",
    )
    op.drop_index(
        "uq_change_policy_version",
        table_name="netops_change_policy_versions",
    )
    for column in (
        "digest",
        "source_job_id",
        "status",
        "compare_mode",
        "semantic_type",
        "observation_type",
        "policy_id",
    ):
        op.drop_index(
            f"ix_netops_change_policy_versions_{column}",
            table_name="netops_change_policy_versions",
        )
    op.drop_table("netops_change_policy_versions")

    for column in ("status", "observation_type", "name"):
        op.drop_index(
            f"ix_netops_change_policies_{column}",
            table_name="netops_change_policies",
        )
    op.drop_table("netops_change_policies")

    op.drop_index(
        "ix_netops_event_rule_versions_digest",
        table_name="netops_event_rule_versions",
    )
    op.drop_index(
        "ix_netops_event_rule_versions_source_job_id",
        table_name="netops_event_rule_versions",
    )
    op.drop_column("netops_event_rule_versions", "digest")
    op.drop_column("netops_event_rule_versions", "validation_report_json")
    op.drop_column("netops_event_rule_versions", "source_job_id")

    op.drop_index(
        "ix_netops_observation_contracts_source_job_id",
        table_name="netops_observation_contracts",
    )
    op.drop_column("netops_observation_contracts", "approval_json")
    op.drop_column("netops_observation_contracts", "source_job_id")

    for column in ("asset_digest", "asset_version_id", "asset_id", "asset_type"):
        op.drop_index(
            f"ix_netops_rule_generation_jobs_{column}",
            table_name="netops_rule_generation_jobs",
        )
        op.drop_column("netops_rule_generation_jobs", column)
