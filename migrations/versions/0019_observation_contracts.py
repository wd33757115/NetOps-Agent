"""add observation contracts

Revision ID: 0019_observation_contracts
Revises: 0018_raw_evidence
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019_observation_contracts"
down_revision: Union[str, Sequence[str], None] = "0018_raw_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "netops_observation_contracts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("schema_id", sa.String(length=128), nullable=False),
        sa.Column("schema_ref", sa.String(length=512), nullable=True),
        sa.Column("source_template_id", sa.String(length=64), nullable=True),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("primary_keys_json", sa.JSON(), nullable=False),
        sa.Column("required_fields_json", sa.JSON(), nullable=False),
        sa.Column("optional_fields_json", sa.JSON(), nullable=True),
        sa.Column("validators_json", sa.JSON(), nullable=True),
        sa.Column("ignore_fields_json", sa.JSON(), nullable=True),
        sa.Column("allow_empty", sa.Boolean(), nullable=False),
        sa.Column("empty_patterns_json", sa.JSON(), nullable=True),
        sa.Column("state_semantics_json", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("contract_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_template_id"],
            ["parser_templates.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "source_type",
        "schema_id",
        "source_template_id",
        "entity_type",
        "contract_sha256",
        "status",
    ):
        op.create_index(
            f"ix_netops_observation_contracts_{column}",
            "netops_observation_contracts",
            [column],
        )
    op.create_index(
        "uq_observation_contract_version",
        "netops_observation_contracts",
        ["source_type", "schema_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_observation_contract_active",
        "netops_observation_contracts",
        ["source_type", "schema_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_observation_contract_active",
        table_name="netops_observation_contracts",
    )
    op.drop_index(
        "uq_observation_contract_version",
        table_name="netops_observation_contracts",
    )
    for column in (
        "status",
        "contract_sha256",
        "entity_type",
        "source_template_id",
        "schema_id",
        "source_type",
    ):
        op.drop_index(
            f"ix_netops_observation_contracts_{column}",
            table_name="netops_observation_contracts",
        )
    op.drop_table("netops_observation_contracts")
