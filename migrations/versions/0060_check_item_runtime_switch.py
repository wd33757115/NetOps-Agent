"""Add check-item runtime planning, asset gaps and report manifests.

Revision ID: 0060_check_item_runtime_switch
Revises: 0059_check_item_runtime
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0060_check_item_runtime_switch"
down_revision = "0059_check_item_runtime"
branch_labels = None
depends_on = None


def _add_index(table: str, column: str, name: str | None = None) -> None:
    op.create_index(name or f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.add_column(
        "netops_patrol_batch_summary_projections",
        sa.Column("report_manifest_id", sa.String(length=64), nullable=True),
    )
    _add_index(
        "netops_patrol_batch_summary_projections",
        "report_manifest_id",
        "ix_patrol_summary_report_manifest",
    )

    for column in (
        sa.Column("target_manifest_digest", sa.String(length=64), nullable=True),
        sa.Column("inventory_snapshot_digest", sa.String(length=64), nullable=True),
        sa.Column("runtime_manifest_id", sa.String(length=64), nullable=True),
        sa.Column("runtime_manifest_digest", sa.String(length=64), nullable=True),
        sa.Column("report_manifest_id", sa.String(length=64), nullable=True),
        sa.Column("report_manifest_digest", sa.String(length=64), nullable=True),
    ):
        op.add_column("netops_patrol_batches", column)
        _add_index(
            "netops_patrol_batches",
            column.name,
            f"ix_patrol_batch_{column.name}",
        )

    for column in (
        sa.Column("collection_action_id", sa.String(length=64), nullable=True),
        sa.Column("implementation_version_id", sa.String(length=64), nullable=True),
        sa.Column("check_item_ids_json", sa.JSON(), nullable=True),
    ):
        op.add_column("netops_command_snapshots", column)
    _add_index(
        "netops_command_snapshots",
        "collection_action_id",
        "ix_command_snapshot_collection_action",
    )
    _add_index(
        "netops_command_snapshots",
        "implementation_version_id",
        "ix_command_snapshot_implementation",
    )

    op.add_column(
        "netops_patrol_collection_results",
        sa.Column("collection_action_id", sa.String(length=64), nullable=True),
    )
    _add_index(
        "netops_patrol_collection_results",
        "collection_action_id",
        "ix_patrol_collection_result_action",
    )

    op.add_column(
        "netops_check_executions",
        sa.Column("runtime_bundle_version_id", sa.String(length=64), nullable=True),
    )
    _add_index(
        "netops_check_executions",
        "runtime_bundle_version_id",
        "ix_check_execution_runtime_bundle_version",
    )

    for table in ("netops_network_changes", "netops_network_events"):
        for column in (
            sa.Column("check_item_id", sa.String(length=128), nullable=True),
            sa.Column("check_item_version_id", sa.String(length=64), nullable=True),
            sa.Column("check_execution_id", sa.String(length=64), nullable=True),
        ):
            op.add_column(table, column)
            _add_index(
                table,
                column.name,
                f"ix_{table.removeprefix('netops_')}_{column.name}",
            )

    op.add_column(
        "netops_rule_asset_command_units",
        sa.Column("check_item_id", sa.String(length=128), nullable=True),
    )
    _add_index(
        "netops_rule_asset_command_units",
        "check_item_id",
        "ix_rule_asset_command_unit_check_item",
    )

    op.create_table(
        "netops_patrol_profile_check_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("profile_version_id", sa.String(length=64), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_patrol_profile_check_item_status",
        ),
        sa.ForeignKeyConstraint(
            ["profile_version_id"],
            ["netops_command_profile_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_version_id"],
            ["netops_check_item_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _add_index(
        "netops_patrol_profile_check_items",
        "profile_version_id",
        "ix_patrol_profile_check_item_profile",
    )
    _add_index(
        "netops_patrol_profile_check_items",
        "check_item_version_id",
        "ix_patrol_profile_check_item_check",
    )
    _add_index(
        "netops_patrol_profile_check_items",
        "status",
        "ix_patrol_profile_check_item_status",
    )
    op.create_index(
        "uq_patrol_profile_check_item",
        "netops_patrol_profile_check_items",
        ["profile_version_id", "check_item_version_id"],
        unique=True,
    )

    op.create_table(
        "netops_check_item_collection_actions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("collector", sa.String(length=32), nullable=False, server_default="cli"),
        sa.Column("command", sa.String(length=512), nullable=False),
        sa.Column("canonical_command", sa.String(length=512), nullable=False),
        sa.Column("fallback_rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("check_item_version_ids_json", sa.JSON(), nullable=False),
        sa.Column("implementation_version_ids_json", sa.JSON(), nullable=False),
        sa.Column("runtime_bundle_version_ids_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planned"),
        sa.Column("execution_plan_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned', 'collecting', 'completed', 'failed', 'cancelled', 'skipped')",
            name="ck_check_item_collection_action_status",
        ),
        sa.ForeignKeyConstraint(
            ["execution_plan_id"],
            ["netops_collection_execution_plans.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column, name in (
        ("patrol_batch_id", "ix_check_action_batch"),
        ("device_id", "ix_check_action_device"),
        ("canonical_command", "ix_check_action_command"),
        ("digest", "ix_check_action_digest"),
        ("status", "ix_check_action_status"),
        ("execution_plan_id", "ix_check_action_execution_plan"),
        ("failure_code", "ix_check_action_failure"),
    ):
        _add_index("netops_check_item_collection_actions", column, name)
    op.create_index(
        "uq_check_item_collection_action",
        "netops_check_item_collection_actions",
        ["patrol_batch_id", "device_id", "digest"],
        unique=True,
    )

    op.create_table(
        "netops_check_item_runtime_manifests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("target_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("inventory_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("check_item_versions_json", sa.JSON(), nullable=False),
        sa.Column("collection_actions_json", sa.JSON(), nullable=False),
        sa.Column("runtime_bundle_refs_json", sa.JSON(), nullable=False),
        sa.Column("comparison_context_ids_json", sa.JSON(), nullable=False),
        sa.Column("entity_identity_policy_versions_json", sa.JSON(), nullable=False),
        sa.Column("assessment_schema_version", sa.String(length=64), nullable=False),
        sa.Column("report_schema_version", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="frozen"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('frozen', 'superseded')",
            name="ck_check_item_runtime_manifest_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("patrol_batch_id"),
        sa.UniqueConstraint("digest"),
    )
    for column, name in (
        ("patrol_batch_id", "ix_check_manifest_batch"),
        ("target_manifest_digest", "ix_check_manifest_target"),
        ("inventory_snapshot_digest", "ix_check_manifest_inventory"),
        ("digest", "ix_check_manifest_digest"),
        ("status", "ix_check_manifest_status"),
    ):
        _add_index("netops_check_item_runtime_manifests", column, name)

    op.create_table(
        "netops_text_comparison_profiles",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _add_index(
        "netops_text_comparison_profiles",
        "status",
        "ix_text_comparison_profile_status",
    )
    op.create_table(
        "netops_text_comparison_profile_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "mode IN ('ordered_lines', 'unordered_lines', 'hash_only')",
            name="ck_text_comparison_profile_mode",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_text_comparison_profile_status",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["netops_text_comparison_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column, name in (
        ("profile_id", "ix_text_comparison_version_profile"),
        ("digest", "ix_text_comparison_version_digest"),
        ("status", "ix_text_comparison_version_status"),
    ):
        _add_index("netops_text_comparison_profile_versions", column, name)
    op.create_index(
        "uq_text_comparison_profile_version",
        "netops_text_comparison_profile_versions",
        ["profile_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_text_comparison_profile_active",
        "netops_text_comparison_profile_versions",
        ["profile_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "netops_asset_gaps",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=True),
        sa.Column("check_execution_id", sa.String(length=64), nullable=True),
        sa.Column("check_item_id", sa.String(length=128), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=True),
        sa.Column("implementation_version_id", sa.String(length=64), nullable=True),
        sa.Column("canonical_command", sa.String(length=512), nullable=True),
        sa.Column("output_signature", sa.String(length=64), nullable=True),
        sa.Column("gap_type", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("sanitized_sample_refs_json", sa.JSON(), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=128), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('production_patrol', 'offline_import', 'historical_backfill')",
            name="ck_asset_gap_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'ignored')",
            name="ck_asset_gap_status",
        ),
        sa.ForeignKeyConstraint(
            ["check_execution_id"],
            ["netops_check_executions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column, name in (
        ("patrol_batch_id", "ix_asset_gap_batch"),
        ("check_execution_id", "ix_asset_gap_execution"),
        ("check_item_id", "ix_asset_gap_check_item"),
        ("check_item_version_id", "ix_asset_gap_check_version"),
        ("implementation_version_id", "ix_asset_gap_implementation"),
        ("canonical_command", "ix_asset_gap_command"),
        ("output_signature", "ix_asset_gap_signature"),
        ("gap_type", "ix_asset_gap_type"),
        ("purpose", "ix_asset_gap_purpose"),
        ("status", "ix_asset_gap_status"),
        ("reason_code", "ix_asset_gap_reason"),
        ("first_seen_at", "ix_asset_gap_first_seen"),
        ("last_seen_at", "ix_asset_gap_last_seen"),
    ):
        _add_index("netops_asset_gaps", column, name)
    op.create_index(
        "ix_asset_gap_work_queue",
        "netops_asset_gaps",
        ["status", "gap_type", "last_seen_at"],
    )

    op.create_table(
        "netops_report_manifests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("target_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("runtime_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("comparison_context_digests_json", sa.JSON(), nullable=False),
        sa.Column("check_execution_fact_digest", sa.String(length=64), nullable=False),
        sa.Column("check_assessment_fact_digest", sa.String(length=64), nullable=False),
        sa.Column("risk_case_revision", sa.String(length=64), nullable=False),
        sa.Column("evidence_index_digest", sa.String(length=64), nullable=False),
        sa.Column("generator_version", sa.String(length=64), nullable=False),
        sa.Column("source_facts_json", sa.JSON(), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("artifact_object_keys_json", sa.JSON(), nullable=False),
        sa.Column("artifact_sha256_json", sa.JSON(), nullable=False),
        sa.Column("generated_by", sa.String(length=128), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'partial', 'final', 'superseded')",
            name="ck_report_manifest_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column, name in (
        ("patrol_batch_id", "ix_report_manifest_batch"),
        ("status", "ix_report_manifest_status"),
        ("target_manifest_digest", "ix_report_manifest_target"),
        ("runtime_manifest_digest", "ix_report_manifest_runtime"),
        ("check_execution_fact_digest", "ix_report_manifest_execution"),
        ("check_assessment_fact_digest", "ix_report_manifest_assessment"),
        ("risk_case_revision", "ix_report_manifest_risk"),
        ("evidence_index_digest", "ix_report_manifest_evidence"),
        ("source_digest", "ix_report_manifest_source"),
    ):
        _add_index("netops_report_manifests", column, name)
    op.create_index(
        "uq_report_manifest_batch_version",
        "netops_report_manifests",
        ["patrol_batch_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_report_manifest_batch_source",
        "netops_report_manifests",
        ["patrol_batch_id", "source_digest"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("netops_report_manifests")
    op.drop_table("netops_asset_gaps")
    op.drop_table("netops_text_comparison_profile_versions")
    op.drop_table("netops_text_comparison_profiles")
    op.drop_table("netops_check_item_runtime_manifests")
    op.drop_table("netops_check_item_collection_actions")
    op.drop_table("netops_patrol_profile_check_items")

    op.drop_index(
        "ix_rule_asset_command_unit_check_item",
        table_name="netops_rule_asset_command_units",
    )
    op.drop_column("netops_rule_asset_command_units", "check_item_id")

    for table in ("netops_network_events", "netops_network_changes"):
        for column in ("check_execution_id", "check_item_version_id", "check_item_id"):
            op.drop_index(
                f"ix_{table.removeprefix('netops_')}_{column}",
                table_name=table,
            )
            op.drop_column(table, column)

    op.drop_index(
        "ix_patrol_collection_result_action",
        table_name="netops_patrol_collection_results",
    )
    op.drop_column("netops_patrol_collection_results", "collection_action_id")

    op.drop_index(
        "ix_check_execution_runtime_bundle_version",
        table_name="netops_check_executions",
    )
    op.drop_column("netops_check_executions", "runtime_bundle_version_id")

    op.drop_index(
        "ix_command_snapshot_implementation",
        table_name="netops_command_snapshots",
    )
    op.drop_index(
        "ix_command_snapshot_collection_action",
        table_name="netops_command_snapshots",
    )
    op.drop_column("netops_command_snapshots", "check_item_ids_json")
    op.drop_column("netops_command_snapshots", "implementation_version_id")
    op.drop_column("netops_command_snapshots", "collection_action_id")

    for column in (
        "report_manifest_digest",
        "report_manifest_id",
        "runtime_manifest_digest",
        "runtime_manifest_id",
        "inventory_snapshot_digest",
        "target_manifest_digest",
    ):
        op.drop_index(f"ix_patrol_batch_{column}", table_name="netops_patrol_batches")
        op.drop_column("netops_patrol_batches", column)

    op.drop_index(
        "ix_patrol_summary_report_manifest",
        table_name="netops_patrol_batch_summary_projections",
    )
    op.drop_column("netops_patrol_batch_summary_projections", "report_manifest_id")
