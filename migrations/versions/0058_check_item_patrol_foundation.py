"""Add the check-item-centered patrol foundation.

Revision ID: 0058_check_item_foundation
Revises: 0057_contract_record_sets
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0058_check_item_foundation"
down_revision = "0057_contract_record_sets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_check_items",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_netops_check_items_category", "netops_check_items", ["category"])
    op.create_index("ix_netops_check_items_status", "netops_check_items", ["status"])

    op.create_table(
        "netops_check_item_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("check_item_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("result_mode", sa.String(length=32), nullable=False),
        sa.Column("assessment_mode", sa.String(length=32), nullable=False),
        sa.Column("canonical_contract_json", sa.JSON(), nullable=False),
        sa.Column("report_projection", sa.String(length=128), nullable=False),
        sa.Column("applicability_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "result_mode IN ('structured', 'normalized_diff', 'hash_only', 'evidence_only')",
            name="ck_check_item_version_result_mode",
        ),
        sa.CheckConstraint(
            "assessment_mode IN ('health', 'change', 'evidence')",
            name="ck_check_item_version_assessment_mode",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_check_item_version_status",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_id"],
            ["netops_check_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_netops_check_item_versions_check_item_id",
        "netops_check_item_versions",
        ["check_item_id"],
    )
    op.create_index(
        "ix_netops_check_item_versions_result_mode",
        "netops_check_item_versions",
        ["result_mode"],
    )
    op.create_index(
        "ix_netops_check_item_versions_assessment_mode",
        "netops_check_item_versions",
        ["assessment_mode"],
    )
    op.create_index(
        "ix_netops_check_item_versions_report_projection",
        "netops_check_item_versions",
        ["report_projection"],
    )
    op.create_index(
        "ix_netops_check_item_versions_status",
        "netops_check_item_versions",
        ["status"],
    )
    op.create_index(
        "ix_netops_check_item_versions_digest",
        "netops_check_item_versions",
        ["digest"],
    )
    op.create_index(
        "uq_check_item_version",
        "netops_check_item_versions",
        ["check_item_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_check_item_active_version",
        "netops_check_item_versions",
        ["check_item_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "netops_collection_implementations",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_netops_collection_implementations_status",
        "netops_collection_implementations",
        ["status"],
    )

    op.create_table(
        "netops_collection_implementation_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("implementation_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("collector", sa.String(length=32), nullable=False, server_default="cli"),
        sa.Column("match_json", sa.JSON(), nullable=False),
        sa.Column("commands_json", sa.JSON(), nullable=False),
        sa.Column("parser_requirement", sa.String(length=32), nullable=False),
        sa.Column("template_scope", sa.String(length=32), nullable=True),
        sa.Column("selection_priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "parser_requirement IN ('structured', 'normalized_diff', 'hash_only', 'evidence_only')",
            name="ck_collection_impl_parser_requirement",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_collection_impl_version_status",
        ),
        sa.ForeignKeyConstraint(
            ["implementation_id"],
            ["netops_collection_implementations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_netops_collection_impl_version_impl_id",
        "netops_collection_implementation_versions",
        ["implementation_id"],
    )
    op.create_index(
        "ix_netops_collection_impl_version_requirement",
        "netops_collection_implementation_versions",
        ["parser_requirement"],
    )
    op.create_index(
        "ix_netops_collection_impl_version_status",
        "netops_collection_implementation_versions",
        ["status"],
    )
    op.create_index(
        "ix_netops_collection_impl_version_digest",
        "netops_collection_implementation_versions",
        ["digest"],
    )
    op.create_index(
        "uq_collection_implementation_version",
        "netops_collection_implementation_versions",
        ["implementation_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_collection_implementation_active",
        "netops_collection_implementation_versions",
        ["implementation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "netops_check_item_implementation_links",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("implementation_version_id", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["check_item_version_id"],
            ["netops_check_item_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["implementation_version_id"],
            ["netops_collection_implementation_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_check_item_impl_link_check_item",
        "netops_check_item_implementation_links",
        ["check_item_version_id"],
    )
    op.create_index(
        "ix_check_item_impl_link_implementation",
        "netops_check_item_implementation_links",
        ["implementation_version_id"],
    )
    op.create_index(
        "uq_check_item_implementation_link",
        "netops_check_item_implementation_links",
        ["check_item_version_id", "implementation_version_id"],
        unique=True,
    )

    op.create_table(
        "netops_collection_implementation_parsers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("implementation_version_id", sa.String(length=64), nullable=False),
        sa.Column("canonical_command", sa.String(length=512), nullable=False),
        sa.Column("parser_template_id", sa.String(length=64), nullable=True),
        sa.Column("record_set", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("binding_role", sa.String(length=32), nullable=False, server_default="primary"),
        sa.Column("output_signature", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["implementation_version_id"],
            ["netops_collection_implementation_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parser_template_id"],
            ["parser_templates.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_impl_parser_implementation",
        "netops_collection_implementation_parsers",
        ["implementation_version_id"],
    )
    op.create_index(
        "ix_collection_impl_parser_template",
        "netops_collection_implementation_parsers",
        ["parser_template_id"],
    )
    op.create_index(
        "ix_collection_impl_parser_signature",
        "netops_collection_implementation_parsers",
        ["output_signature"],
    )
    op.create_index(
        "ix_collection_impl_parser_status",
        "netops_collection_implementation_parsers",
        ["status"],
    )
    op.create_index(
        "uq_collection_implementation_parser",
        "netops_collection_implementation_parsers",
        ["implementation_version_id", "canonical_command", "record_set", "binding_role"],
        unique=True,
    )

    op.create_table(
        "netops_comparison_contexts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("current_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_run_id", sa.String(length=128), nullable=True),
        sa.Column("baseline_snapshot_ids_json", sa.JSON(), nullable=True),
        sa.Column("max_baseline_age_seconds", sa.Integer(), nullable=True),
        sa.Column("canonical_schema_version", sa.String(length=64), nullable=True),
        sa.Column("entity_identity_policy_version", sa.String(length=64), nullable=True),
        sa.Column("selection_reason", sa.String(length=256), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('online_previous_success', 'explicit_periods', 'no_baseline')",
            name="ck_comparison_context_mode",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_version_id"],
            ["netops_check_item_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest"),
    )
    for column in (
        "digest",
        "mode",
        "device_id",
        "check_item_version_id",
        "current_observed_at",
        "baseline_observed_at",
        "baseline_run_id",
    ):
        op.create_index(
            f"ix_netops_comparison_contexts_{column}",
            "netops_comparison_contexts",
            [column],
        )

    op.create_table(
        "netops_check_executions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("implementation_version_id", sa.String(length=64), nullable=True),
        sa.Column("collection_action_ids_json", sa.JSON(), nullable=False),
        sa.Column("runtime_bundle_id", sa.String(length=64), nullable=True),
        sa.Column("runtime_bundle_digest", sa.String(length=64), nullable=True),
        sa.Column("comparison_context_id", sa.String(length=64), nullable=True),
        sa.Column("execution_status", sa.String(length=32), nullable=False, server_default="planned"),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_entity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("change_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_finding_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assessment_id", sa.String(length=64), nullable=True),
        sa.Column("target_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("asset_manifest_digest", sa.String(length=64), nullable=True),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "execution_status IN ("
            "'planned', 'collecting', 'processing', 'completed', 'not_applicable', "
            "'unsupported', 'blocked_asset_missing', 'collection_failed', 'parse_failed', "
            "'normalization_failed', 'comparison_failed', 'assessment_failed', 'cancelled')",
            name="ck_check_execution_status",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_version_id"],
            ["netops_check_item_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["implementation_version_id"],
            ["netops_collection_implementation_versions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["comparison_context_id"],
            ["netops_comparison_contexts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in (
        "patrol_batch_id",
        "device_id",
        "check_item_version_id",
        "implementation_version_id",
        "runtime_bundle_id",
        "runtime_bundle_digest",
        "comparison_context_id",
        "execution_status",
        "assessment_id",
        "target_manifest_digest",
        "asset_manifest_digest",
    ):
        op.create_index(
            f"ix_netops_check_executions_{column}",
            "netops_check_executions",
            [column],
        )
    op.create_index(
        "uq_check_execution_scope",
        "netops_check_executions",
        ["patrol_batch_id", "device_id", "check_item_version_id"],
        unique=True,
    )
    op.create_index(
        "ix_check_execution_batch_status",
        "netops_check_executions",
        ["patrol_batch_id", "execution_status"],
    )

    op.create_table(
        "netops_check_assessments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("assessment_status", sa.String(length=32), nullable=False),
        sa.Column("completeness_status", sa.String(length=32), nullable=False),
        sa.Column("comparison_status", sa.String(length=32), nullable=False),
        sa.Column("expected_entity_count", sa.Integer(), nullable=True),
        sa.Column("observed_entity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evaluated_entity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("threshold_unknown_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_finding_ids_json", sa.JSON(), nullable=False),
        sa.Column("risk_case_ids_json", sa.JSON(), nullable=False),
        sa.Column("blocking_reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("assessment_rule_bundle_digest", sa.String(length=64), nullable=True),
        sa.Column("comparison_context_digest", sa.String(length=64), nullable=True),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "assessment_status IN ("
            "'healthy', 'at_risk', 'unable_to_determine', 'not_applicable', 'informational')",
            name="ck_check_assessment_status",
        ),
        sa.CheckConstraint(
            "completeness_status IN ('complete', 'partial', 'insufficient')",
            name="ck_check_assessment_completeness",
        ),
        sa.CheckConstraint(
            "comparison_status IN ("
            "'comparable', 'first_observation', 'baseline_missing', "
            "'baseline_stale', 'schema_incompatible')",
            name="ck_check_assessment_comparison",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_version_id"],
            ["netops_check_item_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["netops_check_executions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("execution_id"),
    )
    for column in (
        "patrol_batch_id",
        "device_id",
        "check_item_version_id",
        "execution_id",
        "assessment_status",
        "completeness_status",
        "comparison_status",
        "assessment_rule_bundle_digest",
        "comparison_context_digest",
        "evidence_digest",
        "assessed_at",
    ):
        op.create_index(
            f"ix_netops_check_assessments_{column}",
            "netops_check_assessments",
            [column],
        )
    op.create_index(
        "uq_check_assessment_scope",
        "netops_check_assessments",
        ["patrol_batch_id", "device_id", "check_item_version_id"],
        unique=True,
    )
    op.create_index(
        "ix_check_assessment_batch_status",
        "netops_check_assessments",
        ["patrol_batch_id", "assessment_status"],
    )


def downgrade() -> None:
    op.drop_table("netops_check_assessments")
    op.drop_table("netops_check_executions")
    op.drop_table("netops_comparison_contexts")
    op.drop_table("netops_collection_implementation_parsers")
    op.drop_table("netops_check_item_implementation_links")
    op.drop_table("netops_collection_implementation_versions")
    op.drop_table("netops_collection_implementations")
    op.drop_table("netops_check_item_versions")
    op.drop_table("netops_check_items")
