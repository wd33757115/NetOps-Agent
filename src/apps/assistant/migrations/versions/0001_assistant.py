"""Immutable source revisions and runtime bookkeeping; reuse platform history."""

import sqlalchemy as sa
from alembic import op

revision = "assistant_0001"
down_revision = None


def upgrade():
    op.create_table(
        "app_assistant_documents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("current_revision", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
    )
    op.create_table(
        "app_assistant_revisions",
        sa.Column("document_id", sa.String(32), primary_key=True),
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("sections_json", sa.JSON(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "app_assistant_turns",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("active_conversation", sa.String(64), unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_message_id", sa.String(64)),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_assistant_turn_request"),
    )
    op.create_index(
        "ix_app_assistant_turns_conversation_id", "app_assistant_turns", ["conversation_id"]
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting history")
