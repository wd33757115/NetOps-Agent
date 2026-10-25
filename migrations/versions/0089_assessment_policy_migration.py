"""Add assessment-policy migration inventory and shadow evidence.

Revision ID: 0089_assessment_policy_migrate
Revises: 0088_assessment_applicability
"""

import sqlalchemy as sa
from alembic import op


revision = "0089_assessment_policy_migrate"
down_revision = "0088_assessment_applicability"
branch_labels = None
depends_on = None


def _indexes(table_name: str, definitions: tuple[tuple[str, list[str]], ...]) -> None:
    for name, columns in definitions:
        op.create_index(name, table_name, columns, unique=False)


def upgrade() -> None:
    op.create_table(
        "netops_assessment_policy_migration_sources",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_locator", sa.String(length=512), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("check_item_id", sa.String(length=128), nullable=True),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=True),
        sa.Column("device_id", sa.String(length=256), nullable=True),
        sa.Column("observation_type", sa.String(length=160), nullable=True),
        sa.Column("policy_role", sa.String(length=32), nullable=True),
        sa.Column("binding_type", sa.String(length=32), nullable=True),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("source_payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "conversion_status",
            sa.String(length=32),
            server_default="inventoried",
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("inventoried_by", sa.String(length=128), nullable=False),
        sa.Column("draft_policy_version_id", sa.String(length=64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('inventory_attribute', 'scope_configuration', "
            "'catalog_default', 'check_item_applicability', "
            "'check_item_empty_policy', 'execution_snapshot', 'rule_constant')",
            name="ck_assessment_policy_migration_source_type",
        ),
        sa.CheckConstraint(
            "conversion_status IN ('inventoried', 'draft_created', "
            "'review_required', 'ignored', 'cutover', 'retired')",
            name="ck_assessment_policy_migration_conversion_status",
        ),
        sa.ForeignKeyConstraint(
            ["draft_policy_version_id"],
            ["netops_assessment_policy_versions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_digest"),
    )
    _indexes(
        "netops_assessment_policy_migration_sources",
        (
            ("ix_policy_migration_source_type", ["source_type"]),
            ("ix_policy_migration_source_digest", ["source_digest"]),
            ("ix_policy_migration_check_item", ["check_item_id"]),
            ("ix_policy_migration_check_version", ["check_item_version_id"]),
            ("ix_policy_migration_device", ["device_id"]),
            ("ix_policy_migration_observation", ["observation_type"]),
            ("ix_policy_migration_role", ["policy_role"]),
            ("ix_policy_migration_binding_type", ["binding_type"]),
            ("ix_policy_migration_conversion", ["conversion_status"]),
            ("ix_policy_migration_draft", ["draft_policy_version_id"]),
        ),
    )

    op.create_table(
        "netops_assessment_policy_shadow_results",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("check_item_id", sa.String(length=128), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("observation_type", sa.String(length=160), nullable=False),
        sa.Column("rule_version_id", sa.String(length=128), nullable=True),
        sa.Column("legacy_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("governed_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("legacy_result_json", sa.JSON(), nullable=False),
        sa.Column("governed_result_json", sa.JSON(), nullable=False),
        sa.Column("difference_json", sa.JSON(), nullable=False),
        sa.Column("comparison_status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "comparison_status IN ('matched', 'different', "
            "'governed_missing', 'legacy_missing', 'error')",
            name="ck_assessment_policy_shadow_result_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("input_digest"),
    )
    _indexes(
        "netops_assessment_policy_shadow_results",
        (
            ("ix_policy_shadow_input", ["input_digest"]),
            ("ix_policy_shadow_check_item", ["check_item_id"]),
            ("ix_policy_shadow_check_version", ["check_item_version_id"]),
            ("ix_policy_shadow_device", ["device_id"]),
            ("ix_policy_shadow_observation", ["observation_type"]),
            ("ix_policy_shadow_rule", ["rule_version_id"]),
            ("ix_policy_shadow_legacy_digest", ["legacy_snapshot_digest"]),
            ("ix_policy_shadow_governed_digest", ["governed_snapshot_digest"]),
            ("ix_policy_shadow_status", ["comparison_status"]),
            (
                "ix_assessment_policy_shadow_scope",
                ["check_item_version_id", "device_id", "created_at"],
            ),
        ),
    )


def downgrade() -> None:
    op.drop_table("netops_assessment_policy_shadow_results")
    op.drop_table("netops_assessment_policy_migration_sources")
