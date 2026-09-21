"""Keep bounded execution summaries with the existing turn."""

import sqlalchemy as sa
from alembic import op

revision = "assistant_0002"
down_revision = "assistant_0001"


def upgrade():
    op.add_column("app_assistant_turns", sa.Column("progress_json", sa.JSON()))


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting history")
