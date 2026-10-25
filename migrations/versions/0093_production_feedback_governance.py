"""Add production feedback governance records.

Revision ID: 0093_production_feedback
Revises: 0092_check_item_catalog_governance
"""

import sqlalchemy as sa
from alembic import op

revision = "0093_production_feedback"
down_revision = "0092_check_item_catalog_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netops_production_feedback",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("feedback_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="submitted", nullable=False),
        sa.Column("triage_outcome", sa.String(length=32), nullable=True),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("assessment_id", sa.String(length=64), nullable=False),
        sa.Column("event_occurrence_id", sa.String(length=64), nullable=True),
        sa.Column("risk_occurrence_id", sa.String(length=64), nullable=True),
        sa.Column("risk_episode_id", sa.String(length=64), nullable=True),
        sa.Column("patrol_batch_id", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=False),
        sa.Column("check_item_version_id", sa.String(length=64), nullable=False),
        sa.Column("runtime_bundle_id", sa.String(length=64), nullable=True),
        sa.Column("runtime_bundle_version_id", sa.String(length=64), nullable=True),
        sa.Column("runtime_bundle_digest", sa.String(length=64), nullable=True),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("assessment_basis_digest", sa.String(length=64), nullable=True),
        sa.Column("source_context_json", sa.JSON(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("reported_by", sa.String(length=128), nullable=False),
        sa.Column("triaged_by", sa.String(length=128), nullable=True),
        sa.Column("triage_reason", sa.Text(), nullable=True),
        sa.Column("triage_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("triage_request_digest", sa.String(length=64), nullable=True),
        sa.Column("work_item_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "feedback_type IN ('false_positive', 'missed_detection', 'wrong_severity', "
            "'wrong_scope', 'expected_behavior', 'evidence_quality_issue')",
            name="ck_production_feedback_type",
        ),
        sa.CheckConstraint(
            "status IN ('submitted', 'triaged', 'work_item_created', 'closed', 'rejected')",
            name="ck_production_feedback_status",
        ),
        sa.CheckConstraint(
            "triage_outcome IS NULL OR triage_outcome IN "
            "('confirmed', 'not_confirmed', 'inconclusive')",
            name="ck_production_feedback_triage_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["netops_check_executions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["netops_check_assessments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["event_occurrence_id"],
            ["netops_network_event_occurrences.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["risk_occurrence_id"], ["netops_risk_occurrences.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("triage_idempotency_key"),
    )
    for name, columns in (
        ("ix_production_feedback_request_digest", ["request_digest"]),
        ("ix_production_feedback_type", ["feedback_type"]),
        ("ix_production_feedback_status", ["status"]),
        ("ix_production_feedback_triage_outcome", ["triage_outcome"]),
        ("ix_production_feedback_execution", ["execution_id"]),
        ("ix_production_feedback_assessment", ["assessment_id"]),
        ("ix_production_feedback_event_occurrence", ["event_occurrence_id"]),
        ("ix_production_feedback_risk_occurrence", ["risk_occurrence_id"]),
        ("ix_production_feedback_risk_episode", ["risk_episode_id"]),
        ("ix_production_feedback_batch", ["patrol_batch_id"]),
        ("ix_production_feedback_device", ["device_id"]),
        ("ix_production_feedback_check_item", ["check_item_version_id"]),
        ("ix_production_feedback_bundle", ["runtime_bundle_version_id"]),
        ("ix_production_feedback_evidence", ["evidence_digest"]),
        ("ix_production_feedback_reporter", ["reported_by"]),
        ("ix_production_feedback_triager", ["triaged_by"]),
        ("ix_production_feedback_work_item", ["work_item_id"]),
        (
            "ix_production_feedback_bundle_type",
            ["runtime_bundle_version_id", "feedback_type", "triage_outcome"],
        ),
        ("ix_production_feedback_status_created", ["status", "created_at"]),
    ):
        op.create_index(name, "netops_production_feedback", columns)

    op.create_table(
        "netops_production_feedback_work_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("feedback_id", sa.String(length=64), nullable=False),
        sa.Column("work_item_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="open", nullable=False),
        sa.Column("action_payload_json", sa.JSON(), nullable=False),
        sa.Column("action_digest", sa.String(length=64), nullable=False),
        sa.Column("candidate_ref_json", sa.JSON(), nullable=True),
        sa.Column("candidate_ref_digest", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("linked_by", sa.String(length=128), nullable=True),
        sa.Column("completed_by", sa.String(length=128), nullable=True),
        sa.Column("completion_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "work_item_kind IN ('rule_sample', 'assessment_policy', 'data_quality')",
            name="ck_production_feedback_work_item_kind",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'candidate_linked', 'completed', 'cancelled')",
            name="ck_production_feedback_work_item_status",
        ),
        sa.ForeignKeyConstraint(
            ["feedback_id"], ["netops_production_feedback.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feedback_id"),
    )
    for name, columns in (
        ("ix_production_feedback_work_item_feedback", ["feedback_id"]),
        ("ix_production_feedback_work_item_kind", ["work_item_kind"]),
        ("ix_production_feedback_work_item_status", ["status"]),
        ("ix_production_feedback_work_item_action_digest", ["action_digest"]),
        ("ix_production_feedback_work_item_candidate", ["candidate_ref_digest"]),
        ("ix_production_feedback_work_item_creator", ["created_by"]),
        ("ix_production_feedback_work_item_linker", ["linked_by"]),
        ("ix_production_feedback_work_item_completer", ["completed_by"]),
        (
            "ix_production_feedback_work_item_status_created",
            ["status", "created_at"],
        ),
    ):
        op.create_index(name, "netops_production_feedback_work_items", columns)


def downgrade() -> None:
    op.drop_table("netops_production_feedback_work_items")
    op.drop_table("netops_production_feedback")
