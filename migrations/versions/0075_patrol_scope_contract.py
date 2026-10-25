"""Add immutable patrol scope contracts and per-target scope decisions.

Revision ID: 0075_patrol_scope_contract
Revises: 0074_risk_remediation_episode
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0075_patrol_scope_contract"
down_revision = "0074_risk_remediation_episode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_patrol_scope_contracts",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'deprecated', 'frozen')",
            name="ck_patrol_scope_contract_status",
        ),
    )
    op.create_index(
        "ix_netops_patrol_scope_contracts_status",
        "netops_patrol_scope_contracts",
        ["status"],
    )
    op.create_table(
        "netops_patrol_scope_contract_versions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "scope_contract_id",
            sa.String(length=128),
            sa.ForeignKey("netops_patrol_scope_contracts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("report_purpose", sa.String(length=32), nullable=True),
        sa.Column("inventory_selector_json", sa.JSON(), nullable=False),
        sa.Column("device_role_rules_json", sa.JSON(), nullable=False),
        sa.Column("explicit_required_by_device_json", sa.JSON(), nullable=False),
        sa.Column("allowed_exclusion_codes_json", sa.JSON(), nullable=False),
        sa.Column("claim_boundary", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=64), nullable=False),
        sa.Column("contract_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="frozen"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'deprecated', 'frozen')",
            name="ck_patrol_scope_contract_version_status",
        ),
        sa.UniqueConstraint(
            "scope_contract_id",
            "version",
            name="uq_patrol_scope_contract_version",
        ),
    )
    op.create_index(
        "ix_netops_patrol_scope_contract_versions_digest",
        "netops_patrol_scope_contract_versions",
        ["digest"],
    )
    op.create_index(
        "ix_netops_patrol_scope_contract_versions_contract",
        "netops_patrol_scope_contract_versions",
        ["scope_contract_id"],
    )
    op.create_index(
        "ix_netops_patrol_scope_contract_versions_status",
        "netops_patrol_scope_contract_versions",
        ["status"],
    )
    op.create_index(
        "ix_netops_patrol_scope_contract_versions_report_purpose",
        "netops_patrol_scope_contract_versions",
        ["report_purpose"],
    )
    op.create_table(
        "netops_intended_target_matrices",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "patrol_batch_id",
            sa.String(length=128),
            sa.ForeignKey(
                "netops_patrol_batches.patrol_batch_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "scope_contract_version_id",
            sa.String(length=64),
            sa.ForeignKey("netops_patrol_scope_contract_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("inventory_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("matrix_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("patrol_batch_id", name="uq_intended_target_matrix_batch"),
    )
    op.create_index(
        "ix_netops_intended_target_matrices_digest",
        "netops_intended_target_matrices",
        ["digest"],
    )
    op.create_index(
        "ix_netops_intended_target_matrices_scope_version",
        "netops_intended_target_matrices",
        ["scope_contract_version_id"],
    )
    op.create_index(
        "ix_netops_intended_target_matrices_inventory",
        "netops_intended_target_matrices",
        ["inventory_snapshot_digest"],
    )
    op.create_table(
        "netops_scope_decisions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "patrol_batch_id",
            sa.String(length=128),
            sa.ForeignKey(
                "netops_patrol_batches.patrol_batch_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "intended_target_matrix_id",
            sa.String(length=64),
            sa.ForeignKey("netops_intended_target_matrices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column(
            "check_item_version_id",
            sa.String(length=64),
            sa.ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("check_item_id", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("requirement", sa.String(length=32), nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('included', 'approved_exclusion', 'not_applicable', 'unresolved')",
            name="ck_patrol_scope_decision",
        ),
        sa.UniqueConstraint(
            "patrol_batch_id",
            "device_id",
            "check_item_version_id",
            name="uq_patrol_scope_decision_pair",
        ),
    )
    op.create_index(
        "ix_netops_scope_decisions_batch",
        "netops_scope_decisions",
        ["patrol_batch_id"],
    )
    op.create_index(
        "ix_netops_scope_decisions_matrix",
        "netops_scope_decisions",
        ["intended_target_matrix_id"],
    )
    op.create_index(
        "ix_netops_scope_decisions_decision",
        "netops_scope_decisions",
        ["decision"],
    )
    for table_name in (
        "netops_patrol_batches",
        "netops_check_item_runtime_manifests",
    ):
        op.add_column(table_name, sa.Column("scope_contract_version_id", sa.String(length=64), nullable=True))
        op.add_column(table_name, sa.Column("scope_contract_digest", sa.String(length=64), nullable=True))
        op.add_column(table_name, sa.Column("intended_target_matrix_digest", sa.String(length=64), nullable=True))
        op.add_column(table_name, sa.Column("scope_decision_digest", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        "fk_patrol_batch_scope_contract_version",
        "netops_patrol_batches",
        "netops_patrol_scope_contract_versions",
        ["scope_contract_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_runtime_manifest_scope_contract_version",
        "netops_check_item_runtime_manifests",
        "netops_patrol_scope_contract_versions",
        ["scope_contract_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for table_name in (
        "netops_patrol_batches",
        "netops_check_item_runtime_manifests",
    ):
        for column_name in (
            "scope_contract_version_id",
            "scope_contract_digest",
            "intended_target_matrix_digest",
            "scope_decision_digest",
        ):
            op.create_index(
                op.f(f"ix_{table_name}_{column_name}"),
                table_name,
                [column_name],
            )
    op.add_column("netops_report_manifests", sa.Column("scope_contract_digest", sa.String(length=64), nullable=True))
    op.add_column("netops_report_manifests", sa.Column("intended_target_matrix_digest", sa.String(length=64), nullable=True))
    op.add_column("netops_report_manifests", sa.Column("scope_decision_digest", sa.String(length=64), nullable=True))
    for column_name in (
        "scope_contract_digest",
        "intended_target_matrix_digest",
        "scope_decision_digest",
    ):
        op.create_index(
            op.f(f"ix_netops_report_manifests_{column_name}"),
            "netops_report_manifests",
            [column_name],
        )


def downgrade() -> None:
    for column_name in (
        "scope_decision_digest",
        "intended_target_matrix_digest",
        "scope_contract_digest",
    ):
        op.drop_index(
            op.f(f"ix_netops_report_manifests_{column_name}"),
            table_name="netops_report_manifests",
        )
        op.drop_column("netops_report_manifests", column_name)
    op.drop_constraint(
        "fk_runtime_manifest_scope_contract_version",
        "netops_check_item_runtime_manifests",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_patrol_batch_scope_contract_version",
        "netops_patrol_batches",
        type_="foreignkey",
    )
    for table_name in (
        "netops_check_item_runtime_manifests",
        "netops_patrol_batches",
    ):
        for column_name in (
            "scope_decision_digest",
            "intended_target_matrix_digest",
            "scope_contract_digest",
            "scope_contract_version_id",
        ):
            op.drop_index(
                op.f(f"ix_{table_name}_{column_name}"),
                table_name=table_name,
            )
            op.drop_column(table_name, column_name)
    op.drop_table("netops_scope_decisions")
    op.drop_table("netops_intended_target_matrices")
    op.drop_table("netops_patrol_scope_contract_versions")
    op.drop_table("netops_patrol_scope_contracts")
