"""Add governed assessment policy profiles, versions, decisions and bindings.

Revision ID: 0087_assessment_policy_assets
Revises: 0086_assessment_basis
"""

import sqlalchemy as sa
from alembic import op

revision = "0087_assessment_policy_assets"
down_revision = "0086_assessment_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_assessment_policy_profiles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("check_item_id", sa.String(length=128), nullable=False),
        sa.Column("observation_type", sa.String(length=160), nullable=False),
        sa.Column("policy_role", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("current_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "policy_role IN ('high', 'critical_high', 'low', 'critical_low', 'expected_state')",
            name="ck_assessment_policy_profile_role",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_assessment_policy_profile_status",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_id"], ["netops_check_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_assessment_policy_profile_identity",
        "netops_assessment_policy_profiles",
        ["check_item_id", "observation_type", "policy_role", "name"],
        unique=True,
    )
    for name, columns in (
        ("ix_netops_assessment_policy_profiles_check_item_id", ["check_item_id"]),
        ("ix_netops_assessment_policy_profiles_observation_type", ["observation_type"]),
        ("ix_netops_assessment_policy_profiles_policy_role", ["policy_role"]),
        ("ix_netops_assessment_policy_profiles_status", ["status"]),
    ):
        op.create_index(name, "netops_assessment_policy_profiles", columns, unique=False)

    op.create_table(
        "netops_assessment_policy_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("semantic_reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("semantic_review_reason", sa.Text(), nullable=True),
        sa.Column("semantic_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=128), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_review', 'pending_approval', 'approved', "
            "'rejected', 'superseded', 'revoked')",
            name="ck_assessment_policy_version_status",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_from < valid_until",
            name="ck_assessment_policy_version_validity",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["netops_assessment_policy_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["check_item_version_id"],
            ["netops_check_item_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "uq_assessment_policy_version",
        "netops_assessment_policy_versions",
        ["profile_id", "version"],
        unique=True,
    )
    for name, columns in (
        ("ix_netops_assessment_policy_versions_profile_id", ["profile_id"]),
        ("ix_netops_assessment_policy_versions_check_item_version_id", ["check_item_version_id"]),
        ("ix_netops_assessment_policy_versions_digest", ["digest"]),
        ("ix_netops_assessment_policy_versions_status", ["status"]),
    ):
        op.create_index(name, "netops_assessment_policy_versions", columns, unique=False)

    op.create_table(
        "netops_assessment_policy_decisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("policy_version_id", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stage IN ('submission', 'semantic_review', 'publication', 'revocation')",
            name="ck_assessment_policy_decision_stage",
        ),
        sa.CheckConstraint(
            "decision IN ('submitted', 'approved', 'rejected', 'revoked')",
            name="ck_assessment_policy_decision_value",
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["netops_assessment_policy_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for name, columns in (
        ("ix_netops_assessment_policy_decisions_policy_version_id", ["policy_version_id"]),
        ("ix_netops_assessment_policy_decisions_stage", ["stage"]),
        ("ix_netops_assessment_policy_decisions_decision", ["decision"]),
        ("ix_netops_assessment_policy_decisions_actor", ["actor"]),
    ):
        op.create_index(name, "netops_assessment_policy_decisions", columns, unique=False)

    op.create_table(
        "netops_assessment_policy_bindings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("policy_version_id", sa.String(length=64), nullable=False),
        sa.Column("check_item_id", sa.String(length=128), nullable=False),
        sa.Column("observation_type", sa.String(length=160), nullable=False),
        sa.Column("policy_role", sa.String(length=32), nullable=False),
        sa.Column("binding_type", sa.String(length=32), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("scope_digest", sa.String(length=64), nullable=False),
        sa.Column("binding_digest", sa.String(length=64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("ended_by", sa.String(length=128), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("end_request_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "policy_role IN ('high', 'critical_high', 'low', 'critical_low', 'expected_state')",
            name="ck_assessment_policy_binding_role",
        ),
        sa.CheckConstraint(
            "binding_type IN ('inventory_profile', 'scope_override', 'catalog_default')",
            name="ck_assessment_policy_binding_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'ended', 'revoked')",
            name="ck_assessment_policy_binding_status",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_from < valid_until",
            name="ck_assessment_policy_binding_validity",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["netops_assessment_policy_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["netops_assessment_policy_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["check_item_id"], ["netops_check_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_digest"),
        sa.UniqueConstraint("end_idempotency_key"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for name, columns in (
        ("ix_netops_assessment_policy_bindings_profile_id", ["profile_id"]),
        ("ix_netops_assessment_policy_bindings_policy_version_id", ["policy_version_id"]),
        ("ix_netops_assessment_policy_bindings_check_item_id", ["check_item_id"]),
        ("ix_netops_assessment_policy_bindings_observation_type", ["observation_type"]),
        ("ix_netops_assessment_policy_bindings_policy_role", ["policy_role"]),
        ("ix_netops_assessment_policy_bindings_binding_type", ["binding_type"]),
        ("ix_netops_assessment_policy_bindings_scope_digest", ["scope_digest"]),
        ("ix_netops_assessment_policy_bindings_binding_digest", ["binding_digest"]),
        ("ix_netops_assessment_policy_bindings_status", ["status"]),
    ):
        op.create_index(name, "netops_assessment_policy_bindings", columns, unique=False)
    op.create_index(
        "uq_assessment_policy_active_exact_scope",
        "netops_assessment_policy_bindings",
        [
            "check_item_id",
            "observation_type",
            "policy_role",
            "binding_type",
            "scope_digest",
        ],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_assessment_policy_active_exact_scope",
        table_name="netops_assessment_policy_bindings",
    )
    op.drop_table("netops_assessment_policy_bindings")
    op.drop_table("netops_assessment_policy_decisions")
    op.drop_table("netops_assessment_policy_versions")
    op.drop_table("netops_assessment_policy_profiles")
