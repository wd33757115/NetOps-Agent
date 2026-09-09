"""Patrol report templates and explicit, user-scoped AI drafting consent."""

import sqlalchemy as sa
from alembic import op

revision = "0101_patrol_report_templates"
down_revision = "0100_official_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "netops_patrol_report_templates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False, unique=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "netops_patrol_report_drafts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("template_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("provider_digest", sa.String(64), nullable=False),
        sa.Column("packet", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_netops_patrol_report_drafts_job_id", "netops_patrol_report_drafts", ["job_id"]
    )
    op.create_index(
        "ix_netops_patrol_report_drafts_owner_id", "netops_patrol_report_drafts", ["owner_id"]
    )


def downgrade():
    op.drop_table("netops_patrol_report_drafts")
    op.drop_table("netops_patrol_report_templates")
