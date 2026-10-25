"""Add attested offline-current source qualifications.

Revision ID: 0076_source_qualification
Revises: 0075_patrol_scope_contract
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0076_source_qualification"
down_revision = "0075_patrol_scope_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_offline_patrol_jobs",
        sa.Column(
            "report_intent",
            sa.String(length=32),
            nullable=False,
            server_default="historical",
        ),
    )
    op.create_check_constraint(
        "ck_offline_patrol_job_report_intent",
        "netops_offline_patrol_jobs",
        "report_intent IN ('historical', 'current_attestation')",
    )
    op.create_index(
        op.f("ix_netops_offline_patrol_jobs_report_intent"),
        "netops_offline_patrol_jobs",
        ["report_intent"],
    )
    op.create_table(
        "netops_patrol_source_qualifications",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column(
            "offline_job_id",
            sa.String(length=64),
            sa.ForeignKey(
                "netops_offline_patrol_jobs.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("acquisition_mode", sa.String(length=32), nullable=False),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="approved",
        ),
        sa.Column("observed_at_min", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at_max", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chain_of_custody_digest", sa.String(length=64), nullable=False),
        sa.Column("identity_binding_digest", sa.String(length=64), nullable=False),
        sa.Column("scope_contract_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "intended_target_matrix_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "completeness_evidence_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("qualification_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("imported_by", sa.String(length=128), nullable=False),
        sa.Column("attested_by", sa.String(length=128), nullable=False),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.String(length=128), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "acquisition_mode IN ('offline_upload')",
            name="ck_patrol_source_qualification_acquisition",
        ),
        sa.CheckConstraint(
            "source_kind IN ('attested_offline_current')",
            name="ck_patrol_source_qualification_kind",
        ),
        sa.CheckConstraint(
            "status IN ('approved', 'revoked')",
            name="ck_patrol_source_qualification_status",
        ),
        sa.UniqueConstraint(
            "patrol_batch_id",
            "version",
            name="uq_patrol_source_qualification_version",
        ),
    )
    for column_name in (
        "patrol_batch_id",
        "offline_job_id",
        "acquisition_mode",
        "source_kind",
        "status",
        "chain_of_custody_digest",
        "identity_binding_digest",
        "scope_contract_digest",
        "intended_target_matrix_digest",
        "completeness_evidence_digest",
    ):
        op.create_index(
            op.f(
                "ix_netops_patrol_source_qualifications_"
                f"{column_name}"
            ),
            "netops_patrol_source_qualifications",
            [column_name],
        )
    op.create_index(
        op.f("ix_netops_patrol_source_qualifications_digest"),
        "netops_patrol_source_qualifications",
        ["digest"],
        unique=True,
    )
    op.add_column(
        "netops_patrol_batches",
        sa.Column("source_qualification_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_patrol_batches",
        sa.Column(
            "source_qualification_digest",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_patrol_batch_source_qualification",
        "netops_patrol_batches",
        "netops_patrol_source_qualifications",
        ["source_qualification_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for column_name in (
        "source_qualification_id",
        "source_qualification_digest",
    ):
        op.create_index(
            op.f(f"ix_netops_patrol_batches_{column_name}"),
            "netops_patrol_batches",
            [column_name],
        )
    op.add_column(
        "netops_report_manifests",
        sa.Column(
            "source_qualification_digest",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_netops_report_manifests_source_qualification_digest"),
        "netops_report_manifests",
        ["source_qualification_digest"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_netops_report_manifests_source_qualification_digest"),
        table_name="netops_report_manifests",
    )
    op.drop_column(
        "netops_report_manifests",
        "source_qualification_digest",
    )
    op.drop_constraint(
        "fk_patrol_batch_source_qualification",
        "netops_patrol_batches",
        type_="foreignkey",
    )
    for column_name in (
        "source_qualification_digest",
        "source_qualification_id",
    ):
        op.drop_index(
            op.f(f"ix_netops_patrol_batches_{column_name}"),
            table_name="netops_patrol_batches",
        )
        op.drop_column("netops_patrol_batches", column_name)
    op.drop_table("netops_patrol_source_qualifications")
    op.drop_index(
        op.f("ix_netops_offline_patrol_jobs_report_intent"),
        table_name="netops_offline_patrol_jobs",
    )
    op.drop_constraint(
        "ck_offline_patrol_job_report_intent",
        "netops_offline_patrol_jobs",
        type_="check",
    )
    op.drop_column("netops_offline_patrol_jobs", "report_intent")
