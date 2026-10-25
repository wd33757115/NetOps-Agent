"""Add governed offline-current authority request ledger.

Revision ID: 0079_current_authority
Revises: 0078_parser_spec_revision
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0079_current_authority"
down_revision = "0078_parser_spec_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_patrol_current_authority_requests",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "offline_job_id",
            sa.String(length=64),
            sa.ForeignKey("netops_offline_patrol_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_batch_id",
            sa.String(length=128),
            sa.ForeignKey(
                "netops_patrol_batches.patrol_batch_id",
                ondelete="RESTRICT",
                name="fk_current_authority_candidate_batch",
            ),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "blocker_codes_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("idempotency_scope", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("source_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("observed_at_digest", sa.String(length=64), nullable=False),
        sa.Column("identity_binding_digest", sa.String(length=64), nullable=False),
        sa.Column("scope_contract_version_id", sa.String(length=64), nullable=True),
        sa.Column("scope_contract_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "intended_target_matrix_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("scope_decision_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "completeness_evidence_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column(
            "requested_by_principal_id", sa.String(length=128), nullable=False
        ),
        sa.Column("requested_by_display", sa.String(length=256), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approved_qualification_id",
            sa.String(length=64),
            sa.ForeignKey(
                "netops_patrol_source_qualifications.id",
                ondelete="RESTRICT",
                name="fk_current_authority_approved_qualification",
            ),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('blocked', 'ready_for_attestation', 'attesting', "
            "'approved', 'rejected', 'dispatching', 'completed', 'failed', "
            "'revoked', 'expired')",
            name="ck_patrol_current_authority_request_status",
        ),
    )
    for column_name in (
        "offline_job_id",
        "candidate_batch_id",
        "status",
        "idempotency_key_hash",
        "request_digest",
        "source_manifest_digest",
        "observed_at_digest",
        "identity_binding_digest",
        "scope_contract_version_id",
        "scope_contract_digest",
        "intended_target_matrix_digest",
        "scope_decision_digest",
        "completeness_evidence_digest",
        "requested_by_principal_id",
        "expires_at",
        "approved_qualification_id",
    ):
        op.create_index(
            op.f(
                "ix_netops_patrol_current_authority_requests_"
                f"{column_name}"
            ),
            "netops_patrol_current_authority_requests",
            [column_name],
            unique=(
                column_name
                in {
                    "candidate_batch_id",
                    "idempotency_key_hash",
                    "request_digest",
                }
            ),
        )
    op.create_index(
        "ix_patrol_current_authority_job_created",
        "netops_patrol_current_authority_requests",
        ["offline_job_id", "created_at"],
    )

    qualification_columns = (
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("request_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "imported_by_principal_id", sa.String(length=128), nullable=True
        ),
        sa.Column("imported_by_display", sa.String(length=256), nullable=True),
        sa.Column(
            "attested_by_principal_id", sa.String(length=128), nullable=True
        ),
        sa.Column("attested_by_display", sa.String(length=256), nullable=True),
        sa.Column(
            "attestation_idempotency_key_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "attestation_request_digest", sa.String(length=64), nullable=True
        ),
        sa.Column("supersedes_id", sa.String(length=64), nullable=True),
        sa.Column("version_reason", sa.Text(), nullable=True),
        sa.Column(
            "revoked_by_principal_id", sa.String(length=128), nullable=True
        ),
        sa.Column("revoked_by_display", sa.String(length=256), nullable=True),
        sa.Column(
            "revocation_idempotency_key_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "revocation_request_digest", sa.String(length=64), nullable=True
        ),
    )
    for column in qualification_columns:
        op.add_column("netops_patrol_source_qualifications", column)
    op.create_foreign_key(
        "fk_source_qualification_authority_request",
        "netops_patrol_source_qualifications",
        "netops_patrol_current_authority_requests",
        ["request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_source_qualification_supersedes",
        "netops_patrol_source_qualifications",
        "netops_patrol_source_qualifications",
        ["supersedes_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_patrol_source_qualification_independent_actor",
        "netops_patrol_source_qualifications",
        "imported_by_principal_id IS NULL OR "
        "attested_by_principal_id IS NULL OR "
        "imported_by_principal_id <> attested_by_principal_id",
    )
    for column_name in (
        "request_id",
        "request_digest",
        "imported_by_principal_id",
        "attested_by_principal_id",
        "attestation_idempotency_key_hash",
        "attestation_request_digest",
        "supersedes_id",
        "revoked_by_principal_id",
        "revocation_idempotency_key_hash",
        "revocation_request_digest",
    ):
        op.create_index(
            op.f(
                "ix_netops_patrol_source_qualifications_"
                f"{column_name}"
            ),
            "netops_patrol_source_qualifications",
            [column_name],
            unique=(
                column_name
                in {
                    "attestation_idempotency_key_hash",
                    "revocation_idempotency_key_hash",
                }
            ),
        )

    op.add_column(
        "netops_patrol_batches",
        sa.Column("source_offline_job_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "netops_patrol_batches",
        sa.Column(
            "source_authority_request_id",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_patrol_batch_source_offline_job",
        "netops_patrol_batches",
        "netops_offline_patrol_jobs",
        ["source_offline_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_patrol_batch_current_authority_request",
        "netops_patrol_batches",
        "netops_patrol_current_authority_requests",
        ["source_authority_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for column_name in (
        "source_offline_job_id",
        "source_authority_request_id",
    ):
        op.create_index(
            op.f(f"ix_netops_patrol_batches_{column_name}"),
            "netops_patrol_batches",
            [column_name],
            unique=(column_name == "source_authority_request_id"),
        )


def downgrade() -> None:
    for column_name in (
        "source_authority_request_id",
        "source_offline_job_id",
    ):
        op.drop_index(
            op.f(f"ix_netops_patrol_batches_{column_name}"),
            table_name="netops_patrol_batches",
        )
    op.drop_constraint(
        "fk_patrol_batch_current_authority_request",
        "netops_patrol_batches",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_patrol_batch_source_offline_job",
        "netops_patrol_batches",
        type_="foreignkey",
    )
    op.drop_column("netops_patrol_batches", "source_authority_request_id")
    op.drop_column("netops_patrol_batches", "source_offline_job_id")

    for column_name in (
        "revocation_request_digest",
        "revocation_idempotency_key_hash",
        "revoked_by_principal_id",
        "supersedes_id",
        "attestation_request_digest",
        "attestation_idempotency_key_hash",
        "attested_by_principal_id",
        "imported_by_principal_id",
        "request_digest",
        "request_id",
    ):
        op.drop_index(
            op.f(
                "ix_netops_patrol_source_qualifications_"
                f"{column_name}"
            ),
            table_name="netops_patrol_source_qualifications",
        )
    op.drop_constraint(
        "ck_patrol_source_qualification_independent_actor",
        "netops_patrol_source_qualifications",
        type_="check",
    )
    op.drop_constraint(
        "fk_source_qualification_supersedes",
        "netops_patrol_source_qualifications",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_source_qualification_authority_request",
        "netops_patrol_source_qualifications",
        type_="foreignkey",
    )
    for column_name in (
        "revocation_request_digest",
        "revocation_idempotency_key_hash",
        "revoked_by_display",
        "revoked_by_principal_id",
        "version_reason",
        "supersedes_id",
        "attestation_request_digest",
        "attestation_idempotency_key_hash",
        "attested_by_display",
        "attested_by_principal_id",
        "imported_by_display",
        "imported_by_principal_id",
        "request_digest",
        "request_id",
    ):
        op.drop_column("netops_patrol_source_qualifications", column_name)

    op.drop_table("netops_patrol_current_authority_requests")
