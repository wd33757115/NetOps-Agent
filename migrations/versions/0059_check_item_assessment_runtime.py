"""Add check-item assessment facts, runtime bundles and risk lifecycle.

Revision ID: 0059_check_item_runtime
Revises: 0058_check_item_foundation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059_check_item_runtime"
down_revision = "0058_check_item_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_event_rule_versions",
        sa.Column(
            "evaluation_scope",
            sa.String(length=32),
            nullable=False,
            server_default="both",
        ),
    )
    op.execute(
        "UPDATE netops_event_rule_versions "
        "SET evaluation_scope = 'change' WHERE event_class = 'recovery'"
    )
    op.create_check_constraint(
        "ck_event_rule_version_evaluation_scope",
        "netops_event_rule_versions",
        "evaluation_scope IN ('current_state', 'change', 'both')",
    )
    op.create_index(
        "ix_netops_event_rule_versions_evaluation_scope",
        "netops_event_rule_versions",
        ["evaluation_scope"],
    )

    op.create_table(
        "netops_entity_identity_policies",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_netops_entity_identity_policies_status",
        "netops_entity_identity_policies",
        ["status"],
    )
    op.create_table(
        "netops_entity_identity_policy_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_entity_identity_policy_version_status",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["netops_entity_identity_policies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_netops_entity_identity_policy_versions_policy_id",
        "netops_entity_identity_policy_versions",
        ["policy_id"],
    )
    op.create_index(
        "ix_netops_entity_identity_policy_versions_digest",
        "netops_entity_identity_policy_versions",
        ["digest"],
    )
    op.create_index(
        "ix_netops_entity_identity_policy_versions_status",
        "netops_entity_identity_policy_versions",
        ["status"],
    )
    op.create_index(
        "uq_entity_identity_policy_version",
        "netops_entity_identity_policy_versions",
        ["policy_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_entity_identity_policy_active",
        "netops_entity_identity_policy_versions",
        ["policy_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "netops_check_item_runtime_bundles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("check_item_id", sa.String(length=128), nullable=False),
        sa.Column("implementation_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["check_item_id"],
            ["netops_check_items.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["implementation_id"],
            ["netops_collection_implementations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_netops_check_item_runtime_bundles_check_item_id",
        "netops_check_item_runtime_bundles",
        ["check_item_id"],
    )
    op.create_index(
        "ix_netops_check_item_runtime_bundles_implementation_id",
        "netops_check_item_runtime_bundles",
        ["implementation_id"],
    )
    op.create_index(
        "ix_netops_check_item_runtime_bundles_status",
        "netops_check_item_runtime_bundles",
        ["status"],
    )
    op.create_index(
        "uq_check_item_runtime_bundle_identity",
        "netops_check_item_runtime_bundles",
        ["check_item_id", "implementation_id"],
        unique=True,
    )

    op.create_table(
        "netops_check_item_runtime_bundle_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("bundle_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("implementation_version_id", sa.String(length=64), nullable=False),
        sa.Column("parser_bundle_json", sa.JSON(), nullable=False),
        sa.Column("canonical_catalog_version", sa.Integer(), nullable=False),
        sa.Column("canonical_catalog_digest", sa.String(length=64), nullable=False),
        sa.Column("entity_identity_policy_version_id", sa.String(length=64), nullable=False),
        sa.Column("comparison_profile_version_id", sa.String(length=64), nullable=True),
        sa.Column("dependency_digests_json", sa.JSON(), nullable=False),
        sa.Column("compatibility_json", sa.JSON(), nullable=False),
        sa.Column("validation_report_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_check_item_runtime_bundle_version_status",
        ),
        sa.ForeignKeyConstraint(
            ["bundle_id"],
            ["netops_check_item_runtime_bundles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_version_id"],
            ["netops_check_item_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["implementation_version_id"],
            ["netops_collection_implementation_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entity_identity_policy_version_id"],
            ["netops_entity_identity_policy_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest"),
    )
    for column, index_name in {
        "bundle_id": "ix_runtime_bundle_version_bundle",
        "check_item_version_id": "ix_runtime_bundle_version_check_item",
        "implementation_version_id": "ix_runtime_bundle_version_implementation",
        "canonical_catalog_digest": "ix_runtime_bundle_version_catalog_digest",
        "entity_identity_policy_version_id": "ix_runtime_bundle_version_identity_policy",
        "comparison_profile_version_id": "ix_runtime_bundle_version_comparison_profile",
        "digest": "ix_runtime_bundle_version_digest",
        "status": "ix_runtime_bundle_version_status",
    }.items():
        op.create_index(
            index_name,
            "netops_check_item_runtime_bundle_versions",
            [column],
        )
    op.create_index(
        "uq_check_item_runtime_bundle_version",
        "netops_check_item_runtime_bundle_versions",
        ["bundle_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_check_item_runtime_bundle_active",
        "netops_check_item_runtime_bundle_versions",
        ["bundle_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "netops_runtime_bundle_assets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("bundle_version_id", sa.String(length=64), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=128), nullable=False),
        sa.Column("asset_version_id", sa.String(length=128), nullable=False),
        sa.Column("asset_digest", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="primary"),
        sa.Column("canonical_command", sa.String(length=512), nullable=True),
        sa.Column("observation_type", sa.String(length=256), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "asset_type IN ("
            "'parser_template', 'observation_contract', 'current_state_rule', "
            "'change_policy', 'event_rule', 'comparison_profile', 'identity_policy')",
            name="ck_runtime_bundle_asset_type",
        ),
        sa.ForeignKeyConstraint(
            ["bundle_version_id"],
            ["netops_check_item_runtime_bundle_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "bundle_version_id",
        "asset_type",
        "asset_id",
        "asset_version_id",
        "asset_digest",
        "canonical_command",
        "observation_type",
    ):
        op.create_index(
            f"ix_netops_runtime_bundle_assets_{column}",
            "netops_runtime_bundle_assets",
            [column],
        )
    op.create_index(
        "uq_runtime_bundle_asset_ref",
        "netops_runtime_bundle_assets",
        ["bundle_version_id", "asset_type", "asset_version_id", "role"],
        unique=True,
    )

    op.create_table(
        "netops_current_state_findings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("check_execution_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("implementation_version_id", sa.String(length=64), nullable=True),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_key", sa.String(length=1024), nullable=False),
        sa.Column("dimensions_json", sa.JSON(), nullable=True),
        sa.Column("observation_id", sa.String(length=64), nullable=False),
        sa.Column("observation_type", sa.String(length=256), nullable=True),
        sa.Column("actual_value_json", sa.JSON(), nullable=True),
        sa.Column("actual_value_text", sa.Text(), nullable=True),
        sa.Column("actual_value_number", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("reference_value_json", sa.JSON(), nullable=True),
        sa.Column("reference_value_text", sa.Text(), nullable=True),
        sa.Column("reference_value_number", sa.Float(), nullable=True),
        sa.Column("reference_source", sa.String(length=128), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_rule_version_id", sa.String(length=64), nullable=False),
        sa.Column("rule_digest", sa.String(length=64), nullable=False),
        sa.Column("runtime_bundle_version_id", sa.String(length=64), nullable=False),
        sa.Column("runtime_bundle_digest", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["check_execution_id"],
            ["netops_check_executions.id"],
            ondelete="CASCADE",
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
            ["observation_id"],
            ["netops_state_observations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["event_rule_version_id"],
            ["netops_event_rule_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_bundle_version_id"],
            ["netops_check_item_runtime_bundle_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column, index_name in {
        "patrol_batch_id": "ix_current_finding_batch",
        "check_execution_id": "ix_current_finding_execution",
        "device_id": "ix_current_finding_device",
        "check_item_version_id": "ix_current_finding_check_item",
        "implementation_version_id": "ix_current_finding_implementation",
        "entity_type": "ix_current_finding_entity_type",
        "observation_id": "ix_current_finding_observation",
        "observation_type": "ix_current_finding_observation_type",
        "severity": "ix_current_finding_severity",
        "event_type": "ix_current_finding_event_type",
        "event_rule_version_id": "ix_current_finding_event_rule",
        "rule_digest": "ix_current_finding_rule_digest",
        "runtime_bundle_version_id": "ix_current_finding_runtime_bundle",
        "runtime_bundle_digest": "ix_current_finding_bundle_digest",
        "observed_at": "ix_current_finding_observed_at",
        "assessed_at": "ix_current_finding_assessed_at",
        "evidence_digest": "ix_current_finding_evidence_digest",
    }.items():
        op.create_index(
            index_name,
            "netops_current_state_findings",
            [column],
        )
    op.create_index(
        "ix_current_state_finding_scope",
        "netops_current_state_findings",
        ["patrol_batch_id", "device_id", "check_item_version_id"],
    )

    op.create_table(
        "netops_risk_cases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("correlation_key", sa.String(length=1024), nullable=False),
        sa.Column("check_item_id", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_key", sa.String(length=1024), nullable=False),
        sa.Column("correlation_dimensions_json", sa.JSON(), nullable=True),
        sa.Column("risk_type", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_successful_evaluation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_finding_ids_json", sa.JSON(), nullable=False),
        sa.Column("source_event_ids_json", sa.JSON(), nullable=False),
        sa.Column("owner", sa.String(length=128), nullable=True),
        sa.Column(
            "remediation_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_started",
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_evidence_refs_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'recovering', 'resolved', 'suppressed')",
            name="ck_risk_case_status",
        ),
        sa.CheckConstraint(
            "remediation_status IN ('not_started', 'planned', 'in_progress', 'completed', 'cancelled')",
            name="ck_risk_case_remediation_status",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_id"],
            ["netops_check_items.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("correlation_key"),
    )
    for column in (
        "check_item_id",
        "device_id",
        "entity_type",
        "risk_type",
        "severity",
        "status",
        "first_seen_at",
        "last_seen_at",
        "last_successful_evaluation_at",
        "resolved_at",
        "owner",
        "remediation_status",
        "due_at",
    ):
        op.create_index(
            f"ix_netops_risk_cases_{column}",
            "netops_risk_cases",
            [column],
        )
    op.create_index(
        "ix_risk_case_active_scope",
        "netops_risk_cases",
        ["check_item_id", "device_id", "status"],
    )

    op.create_table(
        "netops_risk_occurrences",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("risk_case_id", sa.String(length=64), nullable=False),
        sa.Column("occurrence_kind", sa.String(length=32), nullable=False),
        sa.Column("source_finding_id", sa.String(length=64), nullable=True),
        sa.Column("source_event_id", sa.String(length=64), nullable=True),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_after", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "occurrence_kind IN ("
            "'opened', 'observed', 'reopened', 'acknowledged', 'recovering', "
            "'resolved', 'suppressed', 'unable_to_confirm')",
            name="ck_risk_occurrence_kind",
        ),
        sa.ForeignKeyConstraint(
            ["risk_case_id"],
            ["netops_risk_cases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_finding_id"],
            ["netops_current_state_findings.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_event_id"],
            ["netops_network_events.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in (
        "risk_case_id",
        "occurrence_kind",
        "source_finding_id",
        "source_event_id",
        "patrol_batch_id",
        "observed_at",
    ):
        op.create_index(
            f"ix_netops_risk_occurrences_{column}",
            "netops_risk_occurrences",
            [column],
        )

    op.create_table(
        "netops_risk_remediations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("risk_case_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planned"),
        sa.Column("owner", sa.String(length=128), nullable=True),
        sa.Column("action_text", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("completed_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned', 'in_progress', 'completed', 'cancelled')",
            name="ck_risk_remediation_status",
        ),
        sa.ForeignKeyConstraint(
            ["risk_case_id"],
            ["netops_risk_cases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("risk_case_id", "status", "owner", "due_at"):
        op.create_index(
            f"ix_netops_risk_remediations_{column}",
            "netops_risk_remediations",
            [column],
        )


def downgrade() -> None:
    op.drop_table("netops_risk_remediations")
    op.drop_table("netops_risk_occurrences")
    op.drop_table("netops_risk_cases")
    op.drop_table("netops_current_state_findings")
    op.drop_table("netops_runtime_bundle_assets")
    op.drop_table("netops_check_item_runtime_bundle_versions")
    op.drop_table("netops_check_item_runtime_bundles")
    op.drop_table("netops_entity_identity_policy_versions")
    op.drop_table("netops_entity_identity_policies")
    op.drop_index(
        "ix_netops_event_rule_versions_evaluation_scope",
        table_name="netops_event_rule_versions",
    )
    op.drop_constraint(
        "ck_event_rule_version_evaluation_scope",
        "netops_event_rule_versions",
        type_="check",
    )
    op.drop_column("netops_event_rule_versions", "evaluation_scope")
