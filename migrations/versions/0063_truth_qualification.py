"""Add evidence qualification, coverage decisions and report sign-off.

Revision ID: 0063_truth_qualification
Revises: 0062_comparison_schema_ref
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0063_truth_qualification"
down_revision = "0062_comparison_schema_ref"
branch_labels = None
depends_on = None


def _index(table: str, column: str, name: str) -> None:
    op.create_index(name, table, [column])


def _add_status_column(table: str, name: str, default: str, index_name: str) -> None:
    op.add_column(
        table,
        sa.Column(
            name,
            sa.String(length=32),
            nullable=False,
            server_default=default,
        ),
    )
    _index(table, name, index_name)


def upgrade() -> None:
    for column in (
        "freshness_policy_json",
        "empty_result_policy_json",
        "coverage_policy_json",
    ):
        op.add_column(
            "netops_check_item_versions",
            sa.Column(column, sa.JSON(), nullable=True),
        )
    op.add_column(
        "netops_collection_implementation_versions",
        sa.Column("output_quality_policy_json", sa.JSON(), nullable=True),
    )

    for column, index_name in (
        ("evidence_qualification_id", "ix_check_exec_qualification"),
        ("coverage_decision_id", "ix_check_exec_coverage_decision"),
    ):
        op.add_column(
            "netops_check_executions",
            sa.Column(column, sa.String(length=64), nullable=True),
        )
        _index("netops_check_executions", column, index_name)
    for name, default, index_name in (
        ("source_identity_status", "unknown", "ix_check_exec_identity_status"),
        ("output_quality_status", "unknown", "ix_check_exec_output_quality"),
        ("freshness_status", "unknown", "ix_check_exec_freshness"),
        (
            "empty_result_classification",
            "not_evaluated",
            "ix_check_exec_empty_result",
        ),
        ("coverage_status", "unknown", "ix_check_exec_coverage_status"),
        ("coverage_basis", "unknown", "ix_check_exec_coverage_basis"),
        (
            "risk_verification_status",
            "unknown",
            "ix_check_exec_risk_verification",
        ),
    ):
        _add_status_column(
            "netops_check_executions",
            name,
            default,
            index_name,
        )
    op.add_column(
        "netops_check_executions",
        sa.Column("expected_entity_count", sa.Integer(), nullable=True),
    )
    for column, index_name in (
        ("observation_min_at", "ix_check_exec_observation_min"),
        ("observation_max_at", "ix_check_exec_observation_max"),
    ):
        op.add_column(
            "netops_check_executions",
            sa.Column(column, sa.DateTime(timezone=True), nullable=True),
        )
        _index("netops_check_executions", column, index_name)

    for column, index_name in (
        ("evidence_qualification_id", "ix_check_assess_qualification"),
        ("coverage_decision_id", "ix_check_assess_coverage_decision"),
    ):
        op.add_column(
            "netops_check_assessments",
            sa.Column(column, sa.String(length=64), nullable=True),
        )
        _index("netops_check_assessments", column, index_name)
    for name, default, index_name in (
        ("source_identity_status", "unknown", "ix_check_assess_identity"),
        ("output_quality_status", "unknown", "ix_check_assess_output"),
        ("freshness_status", "unknown", "ix_check_assess_freshness"),
        (
            "empty_result_classification",
            "not_evaluated",
            "ix_check_assess_empty_result",
        ),
        ("coverage_status", "unknown", "ix_check_assess_coverage"),
        ("coverage_basis", "unknown", "ix_check_assess_coverage_basis"),
        (
            "risk_verification_status",
            "unknown",
            "ix_check_assess_risk_verification",
        ),
    ):
        _add_status_column(
            "netops_check_assessments",
            name,
            default,
            index_name,
        )

    op.create_table(
        "netops_evidence_qualifications",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("check_execution_id", sa.String(length=64), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("implementation_version_id", sa.String(length=64), nullable=True),
        sa.Column("runtime_bundle_version_id", sa.String(length=64), nullable=True),
        sa.Column("runtime_bundle_digest", sa.String(length=64), nullable=True),
        sa.Column("source_identity_status", sa.String(length=32), nullable=False),
        sa.Column("output_quality_status", sa.String(length=32), nullable=False),
        sa.Column("freshness_status", sa.String(length=32), nullable=False),
        sa.Column("empty_result_classification", sa.String(length=32), nullable=False),
        sa.Column("snapshot_ids_json", sa.JSON(), nullable=False),
        sa.Column("observation_min_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observation_max_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qualification_reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("policy_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("profile_version_id", sa.String(length=64), nullable=True),
        sa.Column("profile_digest", sa.String(length=64), nullable=True),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_identity_status IN "
            "('confirmed', 'ambiguous', 'conflict', 'external_unbound', 'unknown')",
            name="ck_evidence_qualification_identity",
        ),
        sa.CheckConstraint(
            "output_quality_status IN "
            "('complete', 'empty', 'unsupported', 'device_error', 'truncated', "
            "'paged', 'ambiguous', 'unknown')",
            name="ck_evidence_qualification_output",
        ),
        sa.CheckConstraint(
            "freshness_status IN ('fresh', 'stale', 'unknown')",
            name="ck_evidence_qualification_freshness",
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("check_execution_id"),
        sa.UniqueConstraint("digest"),
    )
    for column, name in (
        ("digest", "ix_evidence_qualification_digest"),
        ("check_execution_id", "ix_evidence_qualification_execution"),
        ("patrol_batch_id", "ix_evidence_qualification_batch"),
        ("device_id", "ix_evidence_qualification_device"),
        ("check_item_version_id", "ix_evidence_qualification_check_item"),
        ("source_identity_status", "ix_evidence_qualification_identity"),
        ("output_quality_status", "ix_evidence_qualification_output"),
        ("freshness_status", "ix_evidence_qualification_freshness"),
        ("qualified_at", "ix_evidence_qualification_time"),
    ):
        _index("netops_evidence_qualifications", column, name)

    op.create_table(
        "netops_coverage_decisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("check_execution_id", sa.String(length=64), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("coverage_status", sa.String(length=32), nullable=False),
        sa.Column("coverage_basis", sa.String(length=32), nullable=False),
        sa.Column("expected_entity_count", sa.Integer(), nullable=True),
        sa.Column(
            "observed_entity_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "evaluated_entity_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("expected_entity_set_digest", sa.String(length=64), nullable=True),
        sa.Column("observed_entity_set_digest", sa.String(length=64), nullable=False),
        sa.Column("denominator_ref_json", sa.JSON(), nullable=False),
        sa.Column("missing_entity_sample_json", sa.JSON(), nullable=False),
        sa.Column("unexpected_entity_sample_json", sa.JSON(), nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("policy_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "coverage_status IN "
            "('complete', 'partial', 'empty', 'unbounded', 'unknown', 'not_applicable')",
            name="ck_coverage_decision_status",
        ),
        sa.CheckConstraint(
            "coverage_basis IN "
            "('fixed', 'inventory_entity_set', 'derived_entity_set', "
            "'command_reported_total', 'non_exhaustive', 'unknown')",
            name="ck_coverage_decision_basis",
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("check_execution_id"),
        sa.UniqueConstraint("digest"),
    )
    for column, name in (
        ("digest", "ix_coverage_decision_digest"),
        ("check_execution_id", "ix_coverage_decision_execution"),
        ("patrol_batch_id", "ix_coverage_decision_batch"),
        ("device_id", "ix_coverage_decision_device"),
        ("check_item_version_id", "ix_coverage_decision_check_item"),
        ("coverage_status", "ix_coverage_decision_status"),
        ("coverage_basis", "ix_coverage_decision_basis"),
        ("decided_at", "ix_coverage_decision_time"),
    ):
        _index("netops_coverage_decisions", column, name)

    _add_status_column(
        "netops_risk_cases",
        "verification_status",
        "not_reverified",
        "ix_risk_case_verification",
    )
    op.add_column(
        "netops_risk_cases",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    _index("netops_risk_cases", "last_verified_at", "ix_risk_case_last_verified")
    op.add_column(
        "netops_risk_cases",
        sa.Column("last_verification_execution_id", sa.String(length=64), nullable=True),
    )
    _index(
        "netops_risk_cases",
        "last_verification_execution_id",
        "ix_risk_case_verification_execution",
    )
    op.execute(
        sa.text(
            "UPDATE netops_risk_cases SET verification_status = 'resolved' "
            "WHERE status = 'resolved'"
        )
    )

    op.add_column(
        "netops_report_manifests",
        sa.Column("report_cutoff_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "netops_report_manifests",
        sa.Column("qualification_fact_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_report_manifests",
        sa.Column("coverage_fact_digest", sa.String(length=64), nullable=True),
    )
    legacy_digest = "0" * 64
    op.execute(
        sa.text(
            "UPDATE netops_report_manifests "
            "SET report_cutoff_at = generated_at, "
            "qualification_fact_digest = :legacy_digest, "
            "coverage_fact_digest = :legacy_digest"
        ).bindparams(legacy_digest=legacy_digest)
    )
    op.alter_column(
        "netops_report_manifests",
        "report_cutoff_at",
        nullable=False,
    )
    op.alter_column(
        "netops_report_manifests",
        "qualification_fact_digest",
        nullable=False,
    )
    op.alter_column(
        "netops_report_manifests",
        "coverage_fact_digest",
        nullable=False,
    )
    _index("netops_report_manifests", "report_cutoff_at", "ix_report_manifest_cutoff")
    _index(
        "netops_report_manifests",
        "qualification_fact_digest",
        "ix_report_manifest_qualification",
    )
    _index(
        "netops_report_manifests",
        "coverage_fact_digest",
        "ix_report_manifest_coverage",
    )

    op.create_table(
        "netops_report_releases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("report_manifest_id", sa.String(length=64), nullable=False),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="released",
        ),
        sa.Column("source_facts_digest", sa.String(length=64), nullable=False),
        sa.Column("artifact_sha256_json", sa.JSON(), nullable=False),
        sa.Column("release_digest", sa.String(length=64), nullable=False),
        sa.Column("signed_by", sa.String(length=128), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signoff_comment", sa.Text(), nullable=False),
        sa.Column("disclosure_confirmations_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status = 'released'", name="ck_report_release_status"),
        sa.ForeignKeyConstraint(
            ["report_manifest_id"],
            ["netops_report_manifests.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_manifest_id"),
        sa.UniqueConstraint("release_digest"),
    )
    for column, name in (
        ("report_manifest_id", "ix_report_release_manifest"),
        ("patrol_batch_id", "ix_report_release_batch"),
        ("status", "ix_report_release_status"),
        ("source_facts_digest", "ix_report_release_source"),
        ("release_digest", "ix_report_release_digest"),
        ("signed_by", "ix_report_release_signer"),
        ("signed_at", "ix_report_release_signed_at"),
    ):
        _index("netops_report_releases", column, name)


def downgrade() -> None:
    op.drop_table("netops_report_releases")

    for name in (
        "ix_report_manifest_coverage",
        "ix_report_manifest_qualification",
        "ix_report_manifest_cutoff",
    ):
        op.drop_index(name, table_name="netops_report_manifests")
    for column in (
        "coverage_fact_digest",
        "qualification_fact_digest",
        "report_cutoff_at",
    ):
        op.drop_column("netops_report_manifests", column)

    for name in (
        "ix_risk_case_verification_execution",
        "ix_risk_case_last_verified",
        "ix_risk_case_verification",
    ):
        op.drop_index(name, table_name="netops_risk_cases")
    for column in (
        "last_verification_execution_id",
        "last_verified_at",
        "verification_status",
    ):
        op.drop_column("netops_risk_cases", column)

    op.drop_table("netops_coverage_decisions")
    op.drop_table("netops_evidence_qualifications")

    assessment_indexes = (
        "ix_check_assess_risk_verification",
        "ix_check_assess_coverage_basis",
        "ix_check_assess_coverage",
        "ix_check_assess_empty_result",
        "ix_check_assess_freshness",
        "ix_check_assess_output",
        "ix_check_assess_identity",
        "ix_check_assess_coverage_decision",
        "ix_check_assess_qualification",
    )
    for name in assessment_indexes:
        op.drop_index(name, table_name="netops_check_assessments")
    for column in (
        "risk_verification_status",
        "coverage_basis",
        "coverage_status",
        "empty_result_classification",
        "freshness_status",
        "output_quality_status",
        "source_identity_status",
        "coverage_decision_id",
        "evidence_qualification_id",
    ):
        op.drop_column("netops_check_assessments", column)

    execution_indexes = (
        "ix_check_exec_observation_max",
        "ix_check_exec_observation_min",
        "ix_check_exec_risk_verification",
        "ix_check_exec_coverage_basis",
        "ix_check_exec_coverage_status",
        "ix_check_exec_empty_result",
        "ix_check_exec_freshness",
        "ix_check_exec_output_quality",
        "ix_check_exec_identity_status",
        "ix_check_exec_coverage_decision",
        "ix_check_exec_qualification",
    )
    for name in execution_indexes:
        op.drop_index(name, table_name="netops_check_executions")
    for column in (
        "observation_max_at",
        "observation_min_at",
        "expected_entity_count",
        "risk_verification_status",
        "coverage_basis",
        "coverage_status",
        "empty_result_classification",
        "freshness_status",
        "output_quality_status",
        "source_identity_status",
        "coverage_decision_id",
        "evidence_qualification_id",
    ):
        op.drop_column("netops_check_executions", column)

    op.drop_column(
        "netops_collection_implementation_versions",
        "output_quality_policy_json",
    )
    for column in (
        "coverage_policy_json",
        "empty_result_policy_json",
        "freshness_policy_json",
    ):
        op.drop_column("netops_check_item_versions", column)
