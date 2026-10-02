"""Model and reasoning effort chosen for one conversation; no row means the defaults."""

import sqlalchemy as sa
from alembic import op

revision = "assistant_0003"
down_revision = "assistant_0002"


def upgrade():
    op.create_table(
        "app_assistant_conversation_settings",
        sa.Column("conversation_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("reasoning_effort", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting history")
