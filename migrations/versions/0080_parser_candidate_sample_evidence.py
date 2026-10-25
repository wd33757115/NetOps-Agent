"""Bind generated Parser candidates to immutable source samples.

Revision ID: 0080_candidate_evidence
Revises: 0079_current_authority
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0080_candidate_evidence"
down_revision = "0079_current_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parser_candidate_sample_evidence",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "template_id",
            sa.String(length=64),
            sa.ForeignKey("parser_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sample_id",
            sa.String(length=64),
            sa.ForeignKey("parser_job_samples.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("manifest_item_json", sa.JSON(), nullable=False),
        sa.Column("manifest_item_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        op.f("ix_parser_candidate_sample_evidence_template_id"),
        "parser_candidate_sample_evidence",
        ["template_id"],
    )
    op.create_index(
        op.f("ix_parser_candidate_sample_evidence_sample_id"),
        "parser_candidate_sample_evidence",
        ["sample_id"],
    )
    op.create_index(
        op.f("ix_parser_candidate_sample_evidence_manifest_item_sha256"),
        "parser_candidate_sample_evidence",
        ["manifest_item_sha256"],
    )
    op.create_index(
        "uq_parser_candidate_sample_evidence",
        "parser_candidate_sample_evidence",
        ["template_id", "sample_id"],
        unique=True,
    )

    # Before this revision, generated candidates did not own an immutable link
    # to their samples. Mutable candidates must be regenerated under the new
    # protocol; active runtime assets remain untouched for continuity.
    op.execute(
        sa.text(
            """
            UPDATE parser_templates
               SET status = 'rejected',
                   validation_json = (
                       COALESCE(validation_json, '{}'::json)::jsonb
                       || jsonb_build_object(
                           'candidate_sample_evidence',
                           jsonb_build_object(
                               'status', 'invalidated',
                               'code', 'legacy_candidate_evidence_unbound',
                               'reason', 'candidate must be regenerated with immutable sample evidence',
                               'invalidated_at', CURRENT_TIMESTAMP
                           )
                       )
                   )::json,
                   updated_at = CURRENT_TIMESTAMP
             WHERE source_job_id IS NOT NULL
               AND status IN ('draft', 'validating', 'pending_approval')
               AND (
                   validation_json::jsonb ? 'simple_parser'
                   OR validation_json::jsonb ? 'parser_agent'
               )
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE netops_observation_contracts AS contract
               SET status = 'deprecated',
                   deprecated_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
              FROM parser_templates AS template
             WHERE contract.source_template_id = template.id
               AND template.status = 'rejected'
               AND contract.status IN ('draft', 'pending_approval')
            """
        )
    )


def downgrade() -> None:
    op.drop_table("parser_candidate_sample_evidence")
